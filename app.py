from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from sqlalchemy import func
import pytz
import os
import csv
from io import BytesIO, StringIO
from fpdf import FPDF
from flask import jsonify

app = Flask(__name__)
app.secret_key = 'secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///printing_press.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
db = SQLAlchemy(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ---------------- Models ---------------- #
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    bill_filename = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.String(100), nullable=False)

    user = db.relationship('User', backref='expenses')

class MonthlySummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(7))
    data = db.Column(db.Text)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    purpose = db.Column(db.String(200), nullable=False)
    proof_filename = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.String(100), nullable=False)

    user = db.relationship('User', backref='payments')

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(300), nullable=False)
    media_filename = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.String(100), nullable=False)

# === New Attendance Model ===
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD format
    status = db.Column(db.String(20), nullable=False)  # Present, Absent, Leave, Holiday, Overtime
    time_in = db.Column(db.String(20), nullable=True)  # Optional: HH:MM:SS AM/PM
    time_out = db.Column(db.String(20), nullable=True) # Optional: HH:MM:SS AM/PM
    overtime = db.Column(db.String(20), nullable=True)  # New field for overtime duration
    user = db.relationship('User', backref='attendances')

# ---------------- Monthly Summary Generation ---------------- #
@app.before_request
def generate_monthly_summary():
    today = datetime.now(pytz.timezone('Asia/Dhaka'))
    last_month_date = today.replace(day=1) - timedelta(days=1)
    last_month = last_month_date.strftime('%Y-%m')

    if today.day != 1:
        return

    existing = MonthlySummary.query.filter_by(month=last_month).first()
    if existing:
        return

    expenses = db.session.query(
        User.username,
        Expense.category,
        func.sum(Expense.amount)
    ).join(Expense, Expense.user_id == User.id).filter(
        Expense.timestamp.like(f"{last_month}-%")
    ).group_by(User.username, Expense.category).all()

    summary = {}
    for username, category, total_amount in expenses:
        if username not in summary:
            summary[username] = {}
        summary[username][category] = float(total_amount)

    new_summary = MonthlySummary(month=last_month, data=str(summary))
    db.session.add(new_summary)
    db.session.commit()

# ---------------- Routes ---------------- #

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            return "Username already exists"
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect('/login')
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == "admin" and password == "1234":
            session['admin'] = True
            session['user_id'] = None
            return redirect('/admin')
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            session['user_id'] = user.id
            session['admin'] = False
            return redirect('/submit')
        return "Invalid credentials"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('admin', None)
    return redirect('/login')

@app.route('/submit', methods=['GET', 'POST'])
def submit():
    if not session.get('user_id'):
        return redirect('/login')
    if request.method == 'POST':
        description = request.form['description']
        amount = float(request.form['amount'])
        category = request.form['category']
        file = request.files['bill']
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        bd_time = datetime.now(pytz.timezone('Asia/Dhaka')).strftime("%Y-%m-%d %I:%M:%S %p")
        expense = Expense(
            user_id=session['user_id'],
            description=description,
            amount=amount,
            category=category,
            bill_filename=filename,
            timestamp=bd_time
        )
        db.session.add(expense)
        db.session.commit()
        return redirect('/records')
    return render_template('submit.html')

@app.route('/records', methods=['GET', 'POST'])
def records():
    if not session.get('user_id'):
        return redirect('/login')
    category_filter = request.form.get('category') if request.method == 'POST' else None
    start_date = request.form.get('start_date') if request.method == 'POST' else None
    end_date = request.form.get('end_date') if request.method == 'POST' else None
    search = request.form.get('search') if request.method == 'POST' else None
    export = request.form.get('export')
    query = Expense.query.join(User).filter(Expense.user_id == session['user_id'])
    if category_filter and category_filter != "All":
        query = query.filter(Expense.category == category_filter)
    if start_date:
        query = query.filter(Expense.timestamp >= start_date)
    if end_date:
        query = query.filter(Expense.timestamp <= end_date + " 23:59:59")
    if search:
        query = query.filter(Expense.description.ilike(f"%{search}%"))
    expenses = query.order_by(Expense.id.desc()).all()
    total = sum(e.amount for e in expenses)
    if export == "CSV":
        return export_csv(expenses)
    elif export == "PDF":
        return export_pdf(expenses)
    return render_template(
        'records.html',
        expenses=expenses,
        total=round(total, 2),
        selected_category=category_filter or "All",
        start_date=start_date or "",
        end_date=end_date or "",
        search=search or ""
    )

@app.route('/edit-expense/<int:expense_id>', methods=['GET', 'POST'])
def edit_expense(expense_id):
    if not session.get('user_id'):
        return redirect('/login')

    expense = Expense.query.get_or_404(expense_id)
    
    # Prevent others from editing someone else's record
    if expense.user_id != session['user_id']:
        return "Unauthorized", 403

    if request.method == 'POST':
        expense.description = request.form['description']
        expense.amount = float(request.form['amount'])
        expense.category = request.form['category']

        file = request.files.get('bill')
        if file and file.filename != "":
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            expense.bill_filename = filename

        db.session.commit()
        flash("Expense updated successfully.")
        return redirect('/records')

    return render_template('edit_expense.html', expense=expense)


@app.route('/admin')
def admin():
    if not session.get('admin'):
        return redirect('/login')

    # Filters
    name = request.args.get('name', '').strip()
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    users = User.query.all()
    summary = []
    for user in users:
        user_total = db.session.query(func.sum(Expense.amount)).filter_by(user_id=user.id).scalar() or 0
        count = Expense.query.filter_by(user_id=user.id).count()
        summary.append({
            'username': user.username,
            'total': round(user_total, 2),
            'count': count
        })

    # Expense filters
    query = Expense.query.join(User)
    if name:
        query = query.filter(User.username.ilike(f"%{name}%"))
    if date_from:
        query = query.filter(Expense.date >= date_from)
    if date_to:
        query = query.filter(Expense.date <= date_to)

    query = query.order_by(Expense.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    expenses = pagination.items

    total_expense = db.session.query(func.sum(Expense.amount))
    if name or date_from or date_to:
        total_expense = total_expense.select_from(Expense).join(User)
        if name:
            total_expense = total_expense.filter(User.username.ilike(f"%{name}%"))
        if date_from:
            total_expense = total_expense.filter(Expense.date >= date_from)
        if date_to:
            total_expense = total_expense.filter(Expense.date <= date_to)
    total_amount = total_expense.scalar() or 0

    # Attendance summary
    attendance_summary = []
    for user in users:
        attendance_count = db.session.query(
            Attendance.status,
            func.count(Attendance.id)
        ).filter(Attendance.user_id == user.id).group_by(Attendance.status).all()
        
        attendance_summary.append({
            'username': user.username,
            'attendance': {status: count for status, count in attendance_count}
        })

    return render_template('admin.html',
                           summary=summary,
                           expenses=expenses,
                           attendance_summary=attendance_summary,
                           pagination=pagination,
                           total_amount=total_amount,
                           name=name,
                           date_from=date_from,
                           date_to=date_to)

@app.route('/delete-expense/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    if not session.get('admin'):
        return redirect('/login')
    expense = Expense.query.get_or_404(expense_id)
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted.")
    return redirect('/admin')

@app.route('/bulk-delete-expenses', methods=['POST'])
def bulk_delete_expenses():
    if not session.get('admin'):
        return redirect('/login')
    ids = request.form.getlist('selected_expenses')
    for i in ids:
        e = Expense.query.get(int(i))
        if e:
            db.session.delete(e)
    db.session.commit()
    flash(f"{len(ids)} expense(s) deleted.")
    return redirect('/admin')

@app.route('/monthly-summary')
def monthly_summary():
    if not session.get('admin'):
        return redirect('/login')
    all_summaries = MonthlySummary.query.order_by(MonthlySummary.id.desc()).all()
    return render_template('monthly_summary.html', summaries=all_summaries)


@app.route('/payment', methods=['GET', 'POST'])
def payment():
    if not session.get('user_id'):
        return redirect('/login')
    if request.method == 'POST':
        customer_name = request.form['customer_name']
        amount = float(request.form['amount'])
        purpose = request.form['purpose']
        file = request.files['proof']
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        bd_time = datetime.now(pytz.timezone('Asia/Dhaka')).strftime("%Y-%m-%d %I:%M:%S %p")
        payment = Payment(
            user_id=session['user_id'],
            customer_name=customer_name,
            amount=amount,
            purpose=purpose,
            proof_filename=filename,
            timestamp=bd_time
        )
        db.session.add(payment)
        db.session.commit()
        flash("Payment recorded.")
        return redirect('/payment')
    return render_template('payment.html')

@app.route('/admin-payments')
def admin_payments():
    if not session.get('admin'):
        return redirect('/login')

    name = request.args.get('name', '').strip()
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = Payment.query.join(User)

    if name:
        query = query.filter(Payment.customer_name.ilike(f"%{name}%"))
    if date_from:
        query = query.filter(Payment.timestamp >= date_from)
    if date_to:
        query = query.filter(Payment.timestamp <= date_to)

    pagination = query.order_by(Payment.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    payments = pagination.items

    # ✅ Safe and correct way to get total sum of the filtered query
    total_amount = query.with_entities(func.sum(Payment.amount)).scalar() or 0

    return render_template(
        'admin_payments.html',
        payments=payments,
        pagination=pagination,
        name=name,
        date_from=date_from,
        date_to=date_to,
        total_amount=total_amount
    )

@app.route('/delete-payment/<int:id>', methods=['POST'])
def delete_payment(id):
    if not session.get('admin'):
        return redirect('/login')
    p = Payment.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash("Payment deleted.")
    return redirect('/admin-payments')


@app.route('/bulk-delete-payments', methods=['POST'])
def bulk_delete_payments():
    if not session.get('admin'):
        return redirect('/login')
    ids = request.form.getlist('selected_payments')
    for i in ids:
        p = Payment.query.get(int(i))
        if p:
            db.session.delete(p)
    db.session.commit()
    flash(f"{len(ids)} payment(s) deleted.")
    return redirect('/admin-payments')

@app.route('/add-task', methods=['GET', 'POST'])
def add_task():
    if not session.get('admin'):
        return redirect('/login')
    if request.method == 'POST':
        description = request.form['description']
        file = request.files.get('media')
        filename = None
        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        bd_time = datetime.now(pytz.timezone('Asia/Dhaka')).strftime("%Y-%m-%d %I:%M:%S %p")
        task = Task(description=description, media_filename=filename, timestamp=bd_time)
        db.session.add(task)
        db.session.commit()
        flash("Task assigned successfully.")
        return redirect('/add-task')
    return render_template('add_task.html')

@app.route('/tasks')
def tasks():
    if not (session.get('admin') or session.get('user_id')):
        return redirect('/login')

    # Filters
    search = request.args.get('search', '').strip()
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    query = Task.query

    if search:
        query = query.filter(Task.description.ilike(f"%{search}%"))
    if date_from:
        query = query.filter(Task.timestamp >= date_from)
    if date_to:
        query = query.filter(Task.timestamp <= date_to)

    query = query.order_by(Task.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    tasks = pagination.items

    return render_template('tasks.html', tasks=tasks, pagination=pagination,
                           search=search, date_from=date_from, date_to=date_to)

@app.route('/delete-task/<int:id>', methods=['POST'])
def delete_task(id):
    if not session.get('admin'):
        return redirect('/login')
    task = Task.query.get_or_404(id)
    if task.media_filename:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], task.media_filename))
        except:
            pass
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.")
    return redirect('/tasks')


@app.route('/bulk-delete-tasks', methods=['POST'])
def bulk_delete_tasks():
    if not session.get('admin'):
        return redirect('/login')
    ids = request.form.getlist('selected_tasks')
    for task_id in ids:
        task = Task.query.get(int(task_id))
        if task:
            if task.media_filename:
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], task.media_filename))
                except:
                    pass
            db.session.delete(task)
    db.session.commit()
    flash(f"{len(ids)} task(s) deleted.")
    return redirect('/tasks')


# ==== New routes for attendance ====

@app.route('/attendance', methods=['GET', 'POST'])
def attendance():
    if not session.get('user_id'):
        return redirect('/login')

    user_id = session['user_id']
    today_str = datetime.now(pytz.timezone('Asia/Dhaka')).strftime('%Y-%m-%d')
    attendance_record = Attendance.query.filter_by(user_id=user_id, date=today_str).first()

    if request.method == 'POST':
        status = request.form.get('status')
        current_time_bd = datetime.now(pytz.timezone('Asia/Dhaka'))

        time_in = request.form.get('time_in')
        time_out = request.form.get('time_out')

        # Define time formats
        fmt_24 = "%H:%M:%S"
        fmt_12 = "%I:%M:%S %p"

        # Handle time_in
        if not time_in and status in ['Present', 'Overtime']:
            time_in = current_time_bd.strftime(fmt_12)
        elif time_in:
            time_in_dt = datetime.strptime(time_in, fmt_24)
            time_in = time_in_dt.strftime(fmt_12)

        # Handle time_out — only convert if user provided it
        if time_out:
            time_out_dt = datetime.strptime(time_out, fmt_24)
            time_out = time_out_dt.strftime(fmt_12)
        else:
            time_out = None  # Keep it blank if not provided

        # Handle overtime only if status is Overtime and time_out is provided
        overtime_str = None
        if status == 'Overtime' and time_out:
            official_end_time = datetime.strptime("06:00:00 PM", fmt_12)
            actual_time_out = datetime.strptime(time_out, fmt_12)
            overtime_delta = actual_time_out - official_end_time

            if overtime_delta.total_seconds() > 0:
                hours, remainder = divmod(overtime_delta.seconds, 3600)
                minutes = remainder // 60
                overtime_str = f"{hours:02d}:{minutes:02d}"
            else:
                overtime_str = "00:00"

        # Save to DB
        if attendance_record:
            attendance_record.status = status
            attendance_record.time_in = time_in
            attendance_record.time_out = time_out
            attendance_record.overtime = overtime_str
        else:
            attendance_record = Attendance(
                user_id=user_id, date=today_str, status=status,
                time_in=time_in, time_out=time_out, overtime=overtime_str
            )
            db.session.add(attendance_record)

        db.session.commit()
        flash("Attendance updated successfully.")
        return redirect('/attendance')

    return render_template('attendance.html', attendance=attendance_record, today=today_str)


@app.route('/admin-attendance', methods=['GET', 'POST'])
def admin_attendance():
    if not session.get('admin'):
        return redirect('/login')

    username = request.args.get('username', '').strip()
    date_from = request.args.get('from')
    date_to = request.args.get('to')
    action = request.args.get('action')

    # Pagination params
    page = request.args.get('page', 1, type=int)
    per_page = 10  # or make dynamic

    records_query = Attendance.query.join(User)

    if username:
        records_query = records_query.filter(User.username.ilike(f"%{username}%"))
    if date_from:
        records_query = records_query.filter(Attendance.date >= date_from)
    if date_to:
        records_query = records_query.filter(Attendance.date <= date_to)

    records_query = records_query.order_by(Attendance.date.desc(), User.username)
    pagination = records_query.paginate(page=page, per_page=per_page, error_out=False)
    records = pagination.items

    # Prepare date_obj and late entries flag
    for r in records:
        # Convert string date to date object
        if isinstance(r.date, str):
            try:
                r.date_obj = datetime.strptime(r.date, "%Y-%m-%d").date()
            except:
                r.date_obj = None
        else:
            r.date_obj = r.date
        
        # Late if time_in > 9:00 AM (adjust as needed)
        if r.time_in:
            try:
                t = datetime.strptime(r.time_in, "%H:%M").time()
                r.is_late = t > time(9, 0)
            except:
                r.is_late = False
        else:
            r.is_late = False

    # Export PDF
    if action == 'export_pdf':
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=10)
        pdf.cell(200, 10, txt="Attendance Report", ln=True, align='C')

        for r in records_query.all():
            line = f"User: {r.user.username} | Date: {r.date} | Status: {r.status} | In: {r.time_in or '—'} | Out: {r.time_out or '—'} | OT: {r.overtime or '—'}"
            pdf.multi_cell(0, 10, txt=line)

        mem = BytesIO()
        pdf.output(mem)
        mem.seek(0)
        return send_file(mem, mimetype='application/pdf', as_attachment=True, download_name='attendance_report.pdf')

    # Pass now and timedelta to template for month dropdown
    now = datetime.now()
    return render_template('admin_attendance.html', records=records, pagination=pagination, now=now, timedelta=timedelta)


    # ✅ Bulk delete
    if request.method == 'POST' and request.form.get('bulk_delete') == '1':
        for r in records:
            db.session.delete(r)
        db.session.commit()
        flash("Filtered attendance records deleted.")
        return redirect('/admin-attendance')

    return render_template(
        'admin_attendance.html',
        records=records,
        now=datetime.now(),
        timedelta=timedelta
    )

@app.route('/delete-attendance/<int:record_id>', methods=['POST'])
def delete_attendance(record_id):
    if not session.get('admin'):
        return redirect('/login')
    
    record = Attendance.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    flash("Attendance record deleted successfully.")
    return redirect('/admin-attendance')

@app.route('/bulk-delete-attendance', methods=['POST'])
def bulk_delete_attendance():
    if not session.get('admin'):
        return redirect('/login')

    ids = request.form.getlist('selected_records')
    if ids:
        for record_id in ids:
            record = Attendance.query.get(int(record_id))
            if record:
                db.session.delete(record)
        db.session.commit()
        flash("Selected records deleted successfully.")

    return redirect('/admin-attendance')

@app.route('/export-attendance-pdf', methods=['POST'])
def export_attendance_pdf():
    if not session.get('admin'):
        return redirect('/login')

    ids = request.form.getlist('selected_records')
    if not ids:
        flash("No records selected for export.")
        return redirect('/admin-attendance')

    selected = Attendance.query.filter(Attendance.id.in_(ids)).join(User).all()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    pdf.cell(200, 10, txt="Attendance Report", ln=True, align='C')

    for rec in selected:
        line = f"{rec.user.username} | {rec.date} | {rec.status} | In: {rec.time_in or '-'} | Out: {rec.time_out or '-'} | OT: {rec.overtime or '-'}"
        pdf.multi_cell(0, 10, txt=line)

    mem = BytesIO()
    pdf.output(mem)
    mem.seek(0)
    return send_file(mem, mimetype='application/pdf', as_attachment=True, download_name='attendance_report.pdf')


# ----------- Export Helpers ------------- #
def export_csv(expenses):
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Username', 'Description', 'Amount', 'Category', 'Timestamp'])
    for e in expenses:
        writer.writerow([e.user.username, e.description, e.amount, e.category, e.timestamp])
    mem = BytesIO()
    mem.write(si.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='expenses.csv')

def export_pdf(expenses):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    pdf.cell(200, 10, txt="Expense Report", ln=True, align='C')
    for e in expenses:
        line = f"User {e.user.username} | {e.description} | ৳{e.amount} | {e.category} | {e.timestamp}"
        pdf.multi_cell(0, 10, txt=line)
    mem = BytesIO()
    pdf.output(mem)
    mem.seek(0)
    return send_file(mem, mimetype='application/pdf', as_attachment=True, download_name='expenses.pdf')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
