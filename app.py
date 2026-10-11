"""Skyline Student Association: one system for members, events, merch and money."""
import csv
import io
import os
import smtplib
import sqlite3
import sys
import uuid
import requests
import hmac
from pathlib import Path
from dotenv import load_dotenv
from flask import url_for
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from flask_wtf.csrf import CSRFProtect

from io import BytesIO
from openpyxl import Workbook
from flask import send_file, abort


from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from flask import (Flask, Response, abort, flash, has_request_context, jsonify, redirect,
                   render_template, request, send_from_directory, session, url_for)

import db
from db import execute, query

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)


load_dotenv()

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def send_order_confirmation_email(name, email, token):
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name = os.getenv("BREVO_SENDER_NAME", "skylineclub")
    base_url = os.getenv(
        "BASE_URL", "http://127.0.0.1:5000"
    ).rstrip("/")

    if not api_key or not sender_email:
        app.logger.error("Brevo configuration is missing.")
        return False

    tracking_path = url_for("order_page", token=token)
    tracking_url = f"{base_url}{tracking_path}"

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email
        },
        "to": [{"email": email, "name": name}],
        "subject": "Your Skyline Club order confirmation",
        "htmlContent": f"""
            <h2>Thank you for your order, {name}!</h2>
            <p>Your order has been placed successfully.</p>
            <p>
                <a href="{tracking_url}">Track Your Order</a>
            </p>
        """,
        "textContent": (
            f"Your Skyline Club order has been placed. "
            f"Track it here: {tracking_url}"
        )
    }

    try:
        response = requests.post(
            BREVO_API_URL,
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json"
            },
            json=payload,
            timeout=15
        )

        if response.status_code in (200, 201, 202):
            return True

        app.logger.error(
            "Brevo returned status %s", response.status_code
        )
        return False

    except requests.RequestException:
        app.logger.exception("Failed to send order email.")
        return False


PRODUCT_UPLOAD_FOLDER = os.path.join(
    app.root_path, "static", "uploads", "products"
)
os.makedirs(PRODUCT_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_PRODUCT_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


EVENT_UPLOAD_FOLDER = os.path.join(
    app.root_path, "static", "uploads", "events"
)
os.makedirs(EVENT_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EVENT_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def save_event_thumbnail(file):
    """Save an optional event thumbnail and return its filename."""
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)

    if "." not in filename:
        raise ValueError("Please upload a JPG, PNG, or WEBP image.")

    extension = filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_EVENT_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, and WEBP images are allowed.")

    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(os.path.join(EVENT_UPLOAD_FOLDER, unique_filename))

    return unique_filename


app.secret_key = os.environ.get('SECRET_KEY')

if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is missing. Add a strong SECRET_KEY to your .env file."
    )

app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0') == '1',
)

csrf = CSRFProtect(app)
@app.context_processor
def inject_csrf_token():
    from flask_wtf.csrf import generate_csrf
    return {"csrf_token": generate_csrf}

app.teardown_appcontext(db.close_db)
ADMIN_USERNAME = os.environ.get('CLUB_ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('CLUB_ADMIN_PASSWORD')
db.init_db()

INCOME_CATEGORIES = ['dues', 'tickets', 'merch', 'fundraiser', 'donation', 'other']
EXPENSE_CATEGORIES = ['reimbursement', 'venue', 'supplies', 'marketing', 'other']
TASK_FLOW = ['todo', 'doing', 'done']
RECEIPT_TYPES = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def today_iso():
    return date.today().isoformat()


def base_url():
    if os.environ.get('BASE_URL'):
        return os.environ['BASE_URL'].rstrip('/')
    if has_request_context():
        return request.host_url.rstrip('/')
    return 'http://localhost:5000'


@app.template_filter('money')
def money_filter(value):
    return f"₹{float(value or 0):,.2f}"


@app.template_filter('pretty')
def pretty_filter(value):
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if 'T' in value or ' ' in value:
        return parsed.strftime('%d %b %Y, %I:%M %p')
    return parsed.strftime('%d %b %Y')


def member_status(m):
    """pending = dues unpaid, active = paid and not expired, expired = paid but past end date."""
    if not m['paid']:
        return 'pending'
    if m['expires_on'] and m['expires_on'] < today_iso():
        return 'expired'
    return 'active'


@app.context_processor
def inject_globals():
    return {'is_admin': bool(session.get('admin')), 'status_of': member_status}


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('login', next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapper


def staff_required(*allowed_roles):
    """Require an authenticated, active staff account with an allowed role."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if session.get('admin'):
                return view(*args, **kwargs)

            staff_id = session.get('staff_id')
            if not staff_id:
                if request.path == '/admin/checkin' and request.method == 'POST':
                    return jsonify(
                        status='unauthorized',
                        message='Your staff session has expired. Please log in again.'
                    ), 401

                return redirect(url_for('login_choice'))

            staff = db.query(
                'SELECT id, role, active FROM committee_users WHERE id = ?',
                (staff_id,),
                one=True
            )

            if not staff or not staff['active']:
                session.clear()
                flash('Your staff account is inactive or unavailable.', 'error')
                return redirect(url_for('login_choice'))

            if staff['role'] not in allowed_roles:
                abort(403)

            return view(*args, **kwargs)
        return wrapper
    return decorator



def create_excel_file(sheet_name, headers, rows):
    """Create an Excel workbook in memory."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name

    sheet.append(headers)

    for row in rows:
        sheet.append(list(row))

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def download_excel(sheet_name, headers, rows, filename):
    output = create_excel_file(sheet_name, headers, rows)

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


# ==========================================
# EXCEL REPORT DOWNLOAD ROUTES
# ==========================================

@app.get('/admin/reports/events.xlsx')
@staff_required('event_manager')
def report_events_excel():
    rows = query("""
        SELECT
            e.id,
            e.name,
            e.description,
            e.venue,
            e.location_address,
            e.maps_url,
            e.starts_at,
            e.capacity,
            e.member_price,
            e.public_price,
            e.created_at
        FROM events e
        ORDER BY e.starts_at DESC
    """)

    headers = [
        'Event ID', 'Event Name', 'Description', 'Venue',
        'Address', 'Maps URL', 'Start Date/Time',
        'Capacity', 'Member Price', 'Public Price', 'Created At'
    ]

    data = [
        [r[h] for h in [
            'id', 'name', 'description', 'venue',
            'location_address', 'maps_url', 'starts_at',
            'capacity', 'member_price', 'public_price', 'created_at'
        ]]
        for r in rows
    ]

    return download_excel(
        'Events', headers, data, 'Events.xlsx'
    )


@app.get('/admin/reports/ticket-sales.xlsx')
@staff_required('event_manager')
def report_ticket_sales_excel():
    rows = query("""
        SELECT
            t.id AS ticket_id,
            e.name AS event_name,
            t.name AS attendee_name,
            t.email,
            t.code AS ticket_code,
            t.price_paid,
            t.paid,
            t.created_at,
            t.checked_in_at
        FROM tickets t
        JOIN events e ON e.id = t.event_id
        WHERE t.paid = 1
        ORDER BY t.created_at DESC
    """)

    headers = [
        'Ticket ID', 'Event', 'Attendee', 'Email',
        'Ticket Code', 'Amount Paid', 'Paid',
        'Purchase Date', 'Check-in Time'
    ]

    data = [
        [
            r['ticket_id'],
            r['event_name'],
            r['attendee_name'],
            r['email'],
            r['ticket_code'],
            r['price_paid'],
            'Yes' if r['paid'] else 'No',
            r['created_at'],
            r['checked_in_at'] or 'Not checked in'
        ]
        for r in rows
    ]

    return download_excel(
        'Ticket Sales', headers, data, 'Ticket_Sales.xlsx'
    )


@app.get('/admin/reports/attendance.xlsx')
@staff_required('event_manager')
def report_attendance_excel():
    rows = query("""
        SELECT
            t.id AS ticket_id,
            e.name AS event_name,
            t.name AS attendee_name,
            t.email,
            t.code AS ticket_code,
            t.checked_in_at
        FROM tickets t
        JOIN events e ON e.id = t.event_id
        WHERE t.paid = 1
        ORDER BY e.starts_at DESC, t.name COLLATE NOCASE
    """)

    headers = [
        'Ticket ID', 'Event', 'Attendee', 'Email',
        'Ticket Code', 'Attendance Status', 'Check-in Time'
    ]

    data = [
        [
            r['ticket_id'],
            r['event_name'],
            r['attendee_name'],
            r['email'],
            r['ticket_code'],
            'Present' if r['checked_in_at'] else 'Not checked in',
            r['checked_in_at'] or ''
        ]
        for r in rows
    ]

    return download_excel(
        'Attendance', headers, data, 'Attendance.xlsx'
    )


@app.get('/admin/reports/merchandise-sales.xlsx')
@admin_required
def report_merchandise_sales_excel():
    rows = query("""
        SELECT
            o.id AS order_id,
            o.created_at,
            o.name AS customer_name,
            o.email,
            o.phone,
            o.address,
            o.city,
            o.pincode,
            o.delivery_status,
            p.name AS product_name,
            v.size,
            oi.qty,
            oi.unit_price,
            o.total AS order_total,
            o.status
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN variants v ON v.id = oi.variant_id
        JOIN products p ON p.id = v.product_id
        WHERE LOWER(o.status) = 'paid'
        ORDER BY o.created_at DESC
    """)

    headers = [
        'Order ID', 'Order Date', 'Customer', 'Email',
        'Phone', 'Address', 'City', 'Pincode',
        'Delivery Status', 'Product', 'Size',
        'Quantity', 'Unit Price', 'Order Total', 'Status'
    ]

    data = [
        [
            r['order_id'],
            r['created_at'],
            r['customer_name'],
            r['email'],
            r['phone'],
            r['address'],
            r['city'],
            r['pincode'],
            r['delivery_status'],
            r['product_name'],
            r['size'],
            r['qty'],
            r['unit_price'],
            r['order_total'],
            r['status']
        ]
        for r in rows
    ]

    return download_excel(
        'Merchandise Sales',
        headers,
        data,
        'Merchandise_Sales.xlsx'
    )


@app.get('/admin/reports')
@staff_required('event_manager')
def admin_reports():
    return render_template('admin_reports.html')



def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Email (always logged to the outbox; really sent if SMTP_HOST is configured)
# ---------------------------------------------------------------------------
def send_email(to, subject, body, kind='general'):
    status = 'logged'
    host = os.environ.get('SMTP_HOST')
    if host:
        try:
            msg = EmailMessage()
            msg['From'] = os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', 'club@example.com'))
            msg['To'] = to
            msg['Subject'] = subject
            msg.set_content(body)
            with smtplib.SMTP(host, to_int(os.environ.get('SMTP_PORT'), 587), timeout=10) as smtp:
                smtp.starttls()
                if os.environ.get('SMTP_USER'):
                    smtp.login(os.environ['SMTP_USER'], os.environ.get('SMTP_PASSWORD', ''))
                smtp.send_message(msg)
            status = 'sent'
        except Exception as exc:  # keep the app working if mail fails
            status = f'failed: {exc}'[:200]
    execute('INSERT INTO outbox(to_email, subject, body, kind, created_at, status) VALUES (?,?,?,?,?,?)',
            (to, subject, body, kind, now_iso(), status))


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def add_tx(kind, category, amount, description, ref=None, on=None):
    execute('INSERT INTO transactions(date, kind, category, amount, description, ref) VALUES (?,?,?,?,?,?)',
            (on or today_iso(), kind, category, round(amount, 2), description, ref))


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
MEMBER_SQL = """SELECT m.*, t.name AS tier_name, t.price AS tier_price, t.discount_percent
                FROM members m JOIN tiers t ON t.id = m.tier_id"""


def find_member(q):
    q = (q or '').strip()
    if not q:
        return None
    return query(MEMBER_SQL + ' WHERE lower(m.email) = lower(?) OR upper(m.code) = upper(?)', (q, q), one=True)


def active_member(email):
    m = find_member(email)
    return m if m and member_status(m) == 'active' else None


def year_end(start):
    """Memberships run to 31 July; joining in the last 30 days of the year rolls into the next."""
    end = date(start.year, 7, 31) if start.month <= 7 else date(start.year + 1, 7, 31)
    if (end - start).days < 30:
        end = date(end.year + 1, 7, 31)
    return end


def next_expiry(m):
    start = date.today()
    if m['paid'] and m['expires_on'] and m['expires_on'] >= today_iso():
        start = date.fromisoformat(m['expires_on']) + timedelta(days=1)
    return year_end(start)


def can_renew(m):
    if member_status(m) in ('pending', 'expired'):
        return True
    return (date.fromisoformat(m['expires_on']) - date.today()).days <= 60


def create_member(name, email, phone, tier_id):
    member_id = execute('INSERT INTO members(name, email, phone, tier_id, paid, joined_on) VALUES (?,?,?,?,0,?)',
                        (name, email.strip().lower(), phone, tier_id, today_iso()))
    execute('UPDATE members SET code = ? WHERE id = ?', (f'SKY-{member_id:04d}', member_id))
    return member_id


def pay_membership(member_id, on=None):
    m = query(MEMBER_SQL + ' WHERE m.id = ?', (member_id,), one=True)
    if not m:
        return None
    expiry = next_expiry(m)
    execute('UPDATE members SET paid = 1, expires_on = ?, reminded_for = NULL WHERE id = ?',
            (expiry.isoformat(), member_id))
    add_tx('income', 'dues', m['tier_price'], f"{m['tier_name']} membership: {m['name']} ({m['code']})",
           ref=f'member:{member_id}', on=on)
    send_email(m['email'], 'Your Skyline membership is active',
               f"Hi {m['name']},\n\nYour {m['tier_name']} membership ({m['code']}) is active until "
               f"{expiry.strftime('%d %b %Y')}. You get {m['discount_percent']}% off merchandise and "
               f"member prices on event tickets.\n\nSkyline Student Association", 'membership')
    return expiry


def send_due_reminders(days=30):
    """Queue one renewal reminder per member whose membership ends within `days` days."""
    limit = (date.today() + timedelta(days=days)).isoformat()
    due = query("""SELECT * FROM members WHERE paid = 1 AND expires_on BETWEEN ? AND ?
                   AND (reminded_for IS NULL OR reminded_for != expires_on)""", (today_iso(), limit))
    for m in due:
        send_email(m['email'], 'Your Skyline membership ends soon',
                   f"Hi {m['name']},\n\nYour membership ends on {date.fromisoformat(m['expires_on']).strftime('%d %b %Y')}. "
                   f"Renew here to keep your member prices: {base_url()}{url_for('membership', q=m['email'])}\n\n"
                   f"Skyline Student Association", 'renewal')
        execute('UPDATE members SET reminded_for = expires_on WHERE id = ?', (m['id'],))
    return len(due)


# ---------------------------------------------------------------------------
# Events and tickets
# ---------------------------------------------------------------------------
EVENT_SQL = """SELECT e.*, e.capacity - (SELECT COUNT(*) FROM tickets t WHERE t.event_id = e.id) AS seats_left
               FROM events e"""


def create_ticket_order(event_id, name, email, qty):
    event = query(EVENT_SQL + ' WHERE e.id = ?', (event_id,), one=True)
    if not event:
        return None, 'Event not found.'
    if qty < 1 or qty > 5:
        return None, 'You can buy 1 to 5 tickets at a time.'
    if qty > event['seats_left']:
        return None, f"Only {event['seats_left']} seat(s) left."
    member = active_member(email)
    price = event['member_price'] if member else event['public_price']
    batch = uuid.uuid4().hex
    conn = db.get_db()
    for _ in range(qty):
        code = uuid.uuid4().hex[:8].upper()
        conn.execute("""INSERT INTO tickets(event_id, name, email, code, batch, price_paid, is_member, paid, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (event_id, name, email.strip().lower(), code, batch, price,
                      1 if member else 0, 1 if price == 0 else 0, now_iso()))
    conn.commit()
    return batch, None


def pay_tickets(batch):
    rows = query("""SELECT t.*, e.name AS event_name FROM tickets t JOIN events e ON e.id = t.event_id
                    WHERE t.batch = ? AND t.paid = 0""", (batch,))
    if not rows:
        return False
    total = sum(r['price_paid'] for r in rows)
    execute('UPDATE tickets SET paid = 1 WHERE batch = ?', (batch,))
    add_tx('income', 'tickets', total, f"{len(rows)} ticket(s) for {rows[0]['event_name']}: {rows[0]['name']}",
           ref=f"event:{rows[0]['event_id']}")
    return True


def event_stats(event_id):
    row = query("""SELECT COUNT(*) AS sold,
                          COALESCE(SUM(price_paid), 0) AS revenue,
                          SUM(CASE WHEN checked_in_at IS NOT NULL THEN 1 ELSE 0 END) AS attended,
                          SUM(is_member) AS members
                   FROM tickets WHERE event_id = ? AND paid = 1""", (event_id,), one=True)
    sold = row['sold'] or 0
    attended = row['attended'] or 0
    return {'sold': sold, 'revenue': row['revenue'], 'attended': attended,
            'members': row['members'] or 0, 'no_shows': sold - attended,
            'rate': round(attended * 100 / sold) if sold else 0}


# ---------------------------------------------------------------------------
# Merchandise
# ---------------------------------------------------------------------------
def create_product(name, price, sizes_spec, description=''):
    product_id = execute('INSERT INTO products(name, description, price) VALUES (?,?,?)', (name, description, price))
    for part in sizes_spec.split(','):
        if ':' not in part:
            continue
        size, stock = part.split(':', 1)
        if size.strip():
            execute('INSERT INTO variants(product_id, size, stock) VALUES (?,?,?)',
                    (product_id, size.strip().upper(), max(0, to_int(stock))))
    return product_id


def create_shop_order(name, email, phone, address, city, pincode, wanted):
    wanted = {vid: qty for vid, qty in wanted.items() if qty > 0}

    if not wanted:
        return None, 'Choose at least one item.'

    member = active_member(email)
    percent = member['discount_percent'] if member else 0

    items, subtotal = [], 0.0

    for variant_id, qty in wanted.items():
        v = query(
            """SELECT v.*, p.name AS product_name, p.price
               FROM variants v
               JOIN products p ON p.id = v.product_id
               WHERE v.id = ? AND p.active = 1""",
            (variant_id,),
            one=True
        )

        if not v:
            return None, 'One of the items is no longer available.'

        if qty > v['stock']:
            return None, f"Only {v['stock']} left of {v['product_name']} ({v['size']})."

        items.append((v, qty))
        subtotal += v['price'] * qty

    discount = round(subtotal * percent / 100, 2)
    token = uuid.uuid4().hex

    conn = db.get_db()

    cur = conn.execute(
        """INSERT INTO orders
           (token, name, email, phone, address, city, pincode,
            subtotal, discount, total, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            token, name, email.strip().lower(), phone,
            address, city, pincode, subtotal, discount,
            subtotal - discount, now_iso()
        )
    )

    for v, qty in items:
        conn.execute(
            """INSERT INTO order_items
               (order_id, variant_id, qty, unit_price)
               VALUES (?, ?, ?, ?)""",
            (cur.lastrowid, v['id'], qty, v['price'])
        )

        conn.execute(
            'UPDATE variants SET stock = stock - ? WHERE id = ?',
            (qty, v['id'])
        )

    conn.commit()
    return token, None



def pay_order(order):
    if order['status'] != 'pending':
        return False
    execute("UPDATE orders SET status = 'paid', paid_at = ? WHERE id = ?", (now_iso(), order['id']))
    add_tx('income', 'merch', order['total'], f"Merch order #{order['id']}: {order['name']}",
           ref=f"order:{order['id']}")
    return True


def cancel_order(order):
    if order['status'] != 'pending':
        return False
    for item in query('SELECT * FROM order_items WHERE order_id = ?', (order['id'],)):
        execute('UPDATE variants SET stock = stock + ? WHERE id = ?', (item['qty'], item['variant_id']))
    execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order['id'],))
    return True


# ---------------------------------------------------------------------------
# Expense claims
# ---------------------------------------------------------------------------
def submit_claim(member_id, amount, description, receipt_file=None):
    return execute('INSERT INTO claims(member_id, amount, description, receipt_file, created_at) VALUES (?,?,?,?,?)',
                   (member_id, amount, description, receipt_file, now_iso()))


def claim_action(claim_id, action):
    claim = query("""SELECT c.*, m.name AS member_name, m.email FROM claims c
                     JOIN members m ON m.id = c.member_id WHERE c.id = ?""", (claim_id,), one=True)
    if not claim:
        return False
    if action == 'approve' and claim['status'] == 'pending':
        execute("UPDATE claims SET status = 'approved', decided_at = ? WHERE id = ?", (now_iso(), claim_id))
        message = 'approved. The treasurer will reimburse you soon.'
    elif action == 'reject' and claim['status'] == 'pending':
        execute("UPDATE claims SET status = 'rejected', decided_at = ? WHERE id = ?", (now_iso(), claim_id))
        message = 'not approved. Please speak to the treasurer.'
    elif action == 'reimburse' and claim['status'] == 'approved':
        execute("UPDATE claims SET status = 'reimbursed', reimbursed_at = ? WHERE id = ?", (now_iso(), claim_id))
        add_tx('expense', 'reimbursement', claim['amount'],
               f"Reimbursed {claim['member_name']}: {claim['description']}", ref=f'claim:{claim_id}')
        message = 'reimbursed.'
    else:
        return False
    send_email(claim['email'], 'Your expense claim', f"Hi {claim['member_name']},\n\nYour claim for "
               f"{money_filter(claim['amount'])} ({claim['description']}) was {message}", 'claim')
    return True


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.get('/')
def home():
    events = query(EVENT_SQL + ' WHERE e.starts_at >= ? ORDER BY e.starts_at LIMIT 3', (today_iso(),))
    posts = query('SELECT * FROM announcements ORDER BY id DESC LIMIT 3')
    return render_template('home.html', events=events, posts=posts)


@app.route('/join', methods=['GET', 'POST'])
def join():
    tiers = query('SELECT * FROM tiers ORDER BY price')
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        tier_id = to_int(request.form.get('tier_id'))
        if not name or '@' not in email or not query('SELECT 1 FROM tiers WHERE id = ?', (tier_id,)):
            flash('Please fill in your name, a valid email and choose a tier.', 'error')
            return render_template('join.html', tiers=tiers), 400
        try:
            member_id = create_member(name, email, request.form.get('phone', '').strip(), tier_id)
        except sqlite3.IntegrityError:
            flash('That email is already registered. Look up your membership below.', 'error')
            return redirect(url_for('membership', q=email))
        return redirect(url_for('pay_membership_route', member_id=member_id))
    return render_template('join.html', tiers=tiers)


@app.get('/membership')
def membership():
    q = request.args.get('q', '').strip()
    m = find_member(q) if q else None
    return render_template('membership.html', q=q, m=m, renewable=bool(m and can_renew(m)))


@app.route('/pay/membership/<int:member_id>', methods=['GET', 'POST'])
def pay_membership_route(member_id):
    m = query(MEMBER_SQL + ' WHERE m.id = ?', (member_id,), one=True)
    if not m:
        abort(404)
    back = url_for('membership', q=m['email'])
    if not can_renew(m):
        flash('Your membership is already active.')
        return redirect(back)
    if request.method == 'POST':
        expiry = pay_membership(member_id)
        flash(f"Payment received. Membership active until {expiry.strftime('%d %b %Y')}.")
        return redirect(back)
    return render_template('pay.html', title='Membership dues', back=back, amount=m['tier_price'],
                           action=request.path,
                           lines=[f"{m['name']} ({m['code']})", f"{m['tier_name']} membership",
                                  f"Valid until {next_expiry(m).strftime('%d %b %Y')}"])


@app.get('/events')
def events():
    return render_template('events.html', events=query(EVENT_SQL + ' ORDER BY e.starts_at'))


@app.get('/events/<int:event_id>')
def event_detail(event_id):
    event = query(EVENT_SQL + ' WHERE e.id = ?', (event_id,), one=True)
    if not event:
        abort(404)
    return render_template('event_detail.html', e=event)


@app.get('/api/event-price/<int:event_id>')
def api_event_price(event_id):
    event = query('SELECT * FROM events WHERE id = ?', (event_id,), one=True)
    if not event:
        abort(404)
    member = active_member(request.args.get('email', ''))
    return jsonify(price=event['member_price'] if member else event['public_price'], member=bool(member))


@app.post('/events/<int:event_id>/buy')
def buy_tickets(event_id):
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    if not name or '@' not in email:
        flash('Please enter your name and a valid email.', 'error')
        return redirect(url_for('event_detail', event_id=event_id))
    batch, error = create_ticket_order(event_id, name, email, to_int(request.form.get('qty'), 1))
    if error:
        flash(error, 'error')
        return redirect(url_for('event_detail', event_id=event_id))
    return redirect(url_for('tickets_page', batch=batch))



@app.get('/tickets/<batch>')
def tickets_page(batch):
    rows = query("""
        SELECT t.*, e.name AS event_name, e.venue, e.starts_at
        FROM tickets t
        JOIN events e ON e.id = t.event_id
        WHERE t.batch = ?
        ORDER BY t.id
    """, (batch,))

    if not rows:
        abort(404)

    # Require a customer login before displaying tickets and QR codes.
    customer_id = session.get('customer_id')

    if not customer_id:
        session['pending_ticket_batch'] = batch
        flash(
            'Please log in or create an account using the same email '
            'you used to purchase the tickets.',
            'info'
        )
        return redirect(url_for('customer_login'))

    customer = query(
        'SELECT email FROM customer_accounts WHERE id = ?',
        (customer_id,),
        one=True
    )
    print("DEBUG customer_id:", customer_id)
    print("DEBUG customer email:", customer['email'] if customer else None)
    print("DEBUG ticket emails:", [ticket['email'] for ticket in rows])

    if not customer:
        session.pop('customer_id', None)
        abort(403)

    # Only the account matching the ticket email can view the QR codes.
    if any(
        ticket['email'].strip().lower() != customer['email'].strip().lower()
        for ticket in rows
    ):
        abort(403)

    unpaid_total = sum(
        ticket['price_paid'] for ticket in rows if not ticket['paid']
    )

    return render_template(
        'tickets.html',
        tickets=rows,
        batch=batch,
        unpaid_total=unpaid_total
    )



@app.route('/pay/tickets/<batch>', methods=['GET', 'POST'])
def pay_tickets_route(batch):
    rows = query('SELECT * FROM tickets WHERE batch = ? AND paid = 0', (batch,))
    back = url_for('tickets_page', batch=batch)
    if not rows:
        return redirect(back)
    if request.method == 'POST':
        pay_tickets(batch)
        flash('Payment received. Your tickets are ready.')
        return redirect(back)
    event = query('SELECT name FROM events WHERE id = ?', (rows[0]['event_id'],), one=True)
    return render_template('pay.html', title='Ticket payment', back=back,
                           amount=sum(r['price_paid'] for r in rows), action=request.path,
                           lines=[event['name'], f"{len(rows)} ticket(s) for {rows[0]['name']}"])


@app.get('/announcements')
def announcements():
    return render_template('announcements.html', posts=query('SELECT * FROM announcements ORDER BY id DESC'))



@app.get('/shop')
def shop():
    if not session.get('customer_id'):
        flash(
            'Please log in first to access the merchandise shop.',
            'info'
        )
        return redirect(url_for('customer_login', next=url_for('shop')))

    products = query(
        'SELECT * FROM products WHERE active = 1 ORDER BY id'
    )

    variants = {}
    for v in query('SELECT * FROM variants ORDER BY id'):
        variants.setdefault(v['product_id'], []).append(v)

    return render_template(
        'shop.html',
        products=products,
        variants=variants
    )



@app.post('/shop/order')
def shop_order():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    phone = request.form.get('phone', '').strip()
    address = request.form.get('address', '').strip()
    city = request.form.get('city', '').strip()
    pincode = request.form.get('pincode', '').strip()

    if (
        not name
        or '@' not in email
        or not phone
        or not address
        or not city
        or not pincode
    ):
        flash('Please complete all delivery details and enter a valid email.', 'error')
        return redirect(url_for('shop'))

    if not phone.isdigit() or len(phone) < 10 or len(phone) > 15:
        flash('Please enter a valid phone number (10–15 digits).', 'error')
        return redirect(url_for('shop'))

    if not pincode.isdigit() or len(pincode) != 6:
        flash('Please enter a valid 6-digit PIN code.', 'error')
        return redirect(url_for('shop'))

    wanted = {}

    for key, value in request.form.items():
        if key.startswith('qty_'):
            wanted[to_int(key[4:])] = max(0, min(10, to_int(value)))

    token, error = create_shop_order(
        name, email, phone, address, city, pincode, wanted
    )
    if error:
        flash(error, "error")
        return redirect(url_for("shop"))

    send_order_confirmation_email(name, email, token)

    return redirect(url_for("order_page", token=token))


def load_order(token):
    order = query('SELECT * FROM orders WHERE token = ?', (token,), one=True)
    if not order:
        abort(404)
    items = query("""SELECT oi.*, v.size, p.name AS product_name FROM order_items oi
                     JOIN variants v ON v.id = oi.variant_id JOIN products p ON p.id = v.product_id
                     WHERE oi.order_id = ?""", (order['id'],))
    return order, items


@app.get('/shop/order/<token>')
def order_page(token):
    order, items = load_order(token)
    return render_template('order.html', order=order, items=items)


@app.route('/pay/order/<token>', methods=['GET', 'POST'])
def pay_order_route(token):
    order, items = load_order(token)
    back = url_for('order_page', token=token)
    if order['status'] != 'pending':
        return redirect(back)
    if request.method == 'POST':
        pay_order(order)
        flash('Payment received. Thank you!')
        return redirect(back)
    return render_template('pay.html', title='Merchandise payment', back=back, amount=order['total'],
                           action=request.path, lines=[f"{i['product_name']} ({i['size']}) x {i['qty']}" for i in items])


def save_receipt(file):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in RECEIPT_TYPES:
        raise ValueError('Receipts must be an image or a PDF.')
    name = f'{uuid.uuid4().hex}.{ext}'
    file.save(os.path.join(UPLOAD_DIR, name))
    return name


@app.route('/expenses/new', methods=['GET', 'POST'])
def expense_new():
    if request.method == 'POST':
        member = active_member(request.form.get('email', ''))
        amount = to_float(request.form.get('amount'))
        description = request.form.get('description', '').strip()
        if not member:
            flash('Only active members can submit claims. Check the email you joined with.', 'error')
        elif amount <= 0 or not description:
            flash('Enter an amount and a description.', 'error')
        else:
            try:
                receipt = save_receipt(request.files.get('receipt'))
            except ValueError as exc:
                flash(str(exc), 'error')
                return render_template('expense_new.html'), 400
            submit_claim(member['id'], amount, description, receipt)
            flash('Claim submitted. The treasurer will review it.')
            return redirect(url_for('expense_new'))
        return render_template('expense_new.html'), 400
    return render_template('expense_new.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if (
            ADMIN_PASSWORD
            and hmac.compare_digest(username, ADMIN_USERNAME)
            and hmac.compare_digest(password, ADMIN_PASSWORD)         
        ):
            session.clear()
            session['admin'] = True

            target = request.args.get('next', '')
            if target.startswith('/') and not target.startswith('//'):
                return redirect(target)

            return redirect(url_for('admin_dashboard'))

        flash('Invalid admin username or password.', 'error')

    return render_template('login.html')


@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Committee pages
# ---------------------------------------------------------------------------
@app.get('/admin')
@admin_required
def admin_dashboard():
    reminded = send_due_reminders()
    if reminded:
        flash(f'{reminded} renewal reminder(s) were sent automatically.')
    members = query(MEMBER_SQL)
    counts = {'active': 0, 'pending': 0, 'expired': 0}
    for m in members:
        counts[member_status(m)] += 1
    limit = (date.today() + timedelta(days=30)).isoformat()
    expiring = query(MEMBER_SQL + ' WHERE m.paid = 1 AND m.expires_on BETWEEN ? AND ? ORDER BY m.expires_on',
                     (today_iso(), limit))
    totals = query("""SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount END), 0) AS income,
                             COALESCE(SUM(CASE WHEN kind='expense' THEN amount END), 0) AS expense
                      FROM transactions""", one=True)
    upcoming = query(EVENT_SQL + ' WHERE e.starts_at >= ? ORDER BY e.starts_at LIMIT 5', (today_iso(),))
    overdue = query("SELECT COUNT(*) AS n FROM tasks WHERE status != 'done' AND due_date < ?", (today_iso(),), one=True)
    return render_template(
        'admin_dashboard.html', counts=counts, expiring=expiring, upcoming=upcoming,
        income=totals['income'], expense=totals['expense'],
        pending_orders=query("SELECT COUNT(*) AS n FROM orders WHERE status = 'pending'", one=True)['n'],
        pending_claims=query("SELECT COUNT(*) AS n FROM claims WHERE status = 'pending'", one=True)['n'],
        overdue_tasks=overdue['n'])


@app.get('/admin/members')
@admin_required
def admin_members():
    q = request.args.get('q', '').strip()
    sql, args = MEMBER_SQL, ()
    if q:
        sql += ' WHERE m.name LIKE ? OR m.email LIKE ? OR m.code LIKE ?'
        args = (f'%{q}%',) * 3
    return render_template('admin_members.html', members=query(sql + ' ORDER BY m.id DESC', args), q=q,
                           tiers=query('SELECT * FROM tiers ORDER BY price'))


@app.post('/admin/members/add')
@admin_required
def admin_member_add():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    tier_id = to_int(request.form.get('tier_id'))
    if not name or '@' not in email or not query('SELECT 1 FROM tiers WHERE id = ?', (tier_id,)):
        flash('Name, valid email and tier are required.', 'error')
        return redirect(url_for('admin_members'))
    try:
        member_id = create_member(name, email, request.form.get('phone', '').strip(), tier_id)
    except sqlite3.IntegrityError:
        flash('That email is already registered.', 'error')
        return redirect(url_for('admin_members'))
    if request.form.get('paid_cash'):
        pay_membership(member_id)
        flash(f'{name} added and marked as paid.')
    else:
        flash(f'{name} added. Dues are still unpaid.')
    return redirect(url_for('admin_members'))


@app.post('/admin/members/<int:member_id>/delete')
@admin_required
def admin_member_delete(member_id):
    member = query(
        'SELECT id, name FROM members WHERE id = ?',
        (member_id,),
        one=True
    )

    if not member:
        flash('Member not found.', 'error')
        return redirect(url_for('admin_members'))

    # Preserve historical records that reference the member.
    referenced = query(
        'SELECT 1 FROM claims WHERE member_id = ? LIMIT 1',
        (member_id,),
        one=True
    )

    if referenced:
        flash(
            'This member has claim history. Deactivate or retain the record instead.',
            'error'
        )
        return redirect(url_for('admin_members'))

    execute('DELETE FROM members WHERE id = ?', (member_id,))
    flash(f"Member {member['name']} deleted.", 'success')
    return redirect(url_for('admin_members'))


@app.post('/admin/members/<int:member_id>/pay')
@admin_required
def admin_member_pay(member_id):
    m = query(MEMBER_SQL + ' WHERE m.id = ?', (member_id,), one=True)
    if not m or not can_renew(m):
        flash('Nothing to pay for this member.', 'error')
    else:
        pay_membership(member_id)
        flash(f"Recorded cash payment for {m['name']}.")
    return redirect(request.referrer or url_for('admin_members'))


@app.post('/admin/members/<int:member_id>/remind')
@admin_required
def admin_member_remind(member_id):
    m = query('SELECT * FROM members WHERE id = ?', (member_id,), one=True)
    if m:
        send_email(m['email'], 'Reminder: Skyline membership',
                   f"Hi {m['name']},\n\nPlease renew or complete your membership: "
                   f"{base_url()}{url_for('membership', q=m['email'])}", 'renewal')
        flash(f"Reminder sent to {m['name']}.")
    return redirect(request.referrer or url_for('admin_members'))


@app.route('/admin/events', methods=['GET', 'POST'])
@staff_required('event_manager')
def admin_events():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        starts_at = request.form.get('starts_at', '').strip()
        capacity = to_int(request.form.get('capacity'))

        description = request.form.get('description', '').strip()
        venue = request.form.get('venue', '').strip()
        location_address = request.form.get('location_address', '').strip()
        maps_url = request.form.get('maps_url', '').strip()

        member_price = to_float(request.form.get('member_price'))
        public_price = to_float(request.form.get('public_price'))

        if not name or not starts_at or capacity < 1:
            flash('Name, date and a capacity of at least 1 are required.', 'error')
            return redirect(url_for('admin_events'))

        thumbnail = request.files.get('thumbnail')

        try:
            thumbnail_filename = save_event_thumbnail(thumbnail)
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('admin_events'))

        execute("""
            INSERT INTO events (
                name, description, venue, starts_at, capacity,
                member_price, public_price, created_at,
                thumbnail_filename, location_address, maps_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            description,
            venue,
            starts_at,
            capacity,
            member_price,
            public_price,
            now_iso(),
            thumbnail_filename,
            location_address,
            maps_url
        ))

        flash('Event created successfully.')
        return redirect(url_for('admin_events'))

    rows = query(EVENT_SQL + ' ORDER BY e.starts_at DESC')

    return render_template(
        'admin_events.html',
        events=rows,
        stats={r['id']: event_stats(r['id']) for r in rows}
    )


@app.route('/admin/events/<int:event_id>', methods=['GET', 'POST'])
@staff_required('event_manager')
def admin_event(event_id):
    event = query(
        'SELECT * FROM events WHERE id = ?',
        (event_id,),
        one=True
    )

    if not event:
        abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        starts_at = request.form.get('starts_at', '').strip()
        capacity = to_int(request.form.get('capacity'))

        if not name or not starts_at or capacity < 1:
            flash('Name, date and a capacity of at least 1 are required.', 'error')
            return redirect(url_for('admin_event', event_id=event_id))

        issued_tickets = query(
            'SELECT COUNT(*) AS total FROM tickets WHERE event_id = ?',
            (event_id,),
            one=True
        )['total']

        if capacity < issued_tickets:
            flash(
                f'Capacity cannot be less than the {issued_tickets} tickets already issued.',
                'error'
            )
            return redirect(url_for('admin_event', event_id=event_id))

        thumbnail_filename = event['thumbnail_filename']
        thumbnail = request.files.get('thumbnail')

        try:
            new_thumbnail = save_event_thumbnail(thumbnail)
            if new_thumbnail:
                thumbnail_filename = new_thumbnail
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('admin_event', event_id=event_id))

        execute("""
            UPDATE events
            SET name = ?,
                description = ?,
                venue = ?,
                starts_at = ?,
                capacity = ?,
                member_price = ?,
                public_price = ?,
                thumbnail_filename = ?,
                location_address = ?,
                maps_url = ?
            WHERE id = ?
        """, (
            name,
            request.form.get('description', '').strip(),
            request.form.get('venue', '').strip(),
            starts_at,
            capacity,
            to_float(request.form.get('member_price')),
            to_float(request.form.get('public_price')),
            thumbnail_filename,
            request.form.get('location_address', '').strip(),
            request.form.get('maps_url', '').strip(),
            event_id
        ))

        flash('Event updated successfully.')
        return redirect(url_for('admin_event', event_id=event_id))

    event = query(
        EVENT_SQL + ' WHERE e.id = ?',
        (event_id,),
        one=True
    )

    attendees = query(
        'SELECT * FROM tickets WHERE event_id = ? ORDER BY name',
        (event_id,)
    )

    return render_template(
        'admin_event.html',
        e=event,
        s=event_stats(event_id),
        attendees=attendees
    )


@app.get('/admin/checkin')
@staff_required('checkin_staff')
def admin_checkin_page():
    events = query(EVENT_SQL + ' ORDER BY e.starts_at DESC')
    upcoming = query('SELECT id FROM events WHERE starts_at >= ? ORDER BY starts_at LIMIT 1', (today_iso(),), one=True)
    selected = to_int(request.args.get('event_id'), upcoming['id'] if upcoming else (events[0]['id'] if events else 0))
    return render_template('admin_checkin.html', events=events, selected=selected,
                           stats=event_stats(selected) if selected else None)


@app.get('/admin/api/event-stats/<int:event_id>')
@admin_required
def api_event_stats(event_id):
    return jsonify(event_stats(event_id))


@app.post('/admin/checkin')
@staff_required('checkin_staff')
def checkin():
    code = request.form.get('code', '').strip().upper()
    event_id = to_int(request.form.get('event_id'))
    t = query("""SELECT t.*, e.name AS event_name FROM tickets t JOIN events e ON e.id = t.event_id
                 WHERE t.code = ?""", (code,), one=True)
    if not t:
        return jsonify(status='invalid', message='Ticket not found.')
    if t['event_id'] != event_id:
        return jsonify(status='wrong_event', message=f"This ticket is for {t['event_name']}.", name=t['name'])
    if not t['paid']:
        return jsonify(status='unpaid', message='This ticket has not been paid for.', name=t['name'])
    if t['checked_in_at']:
        return jsonify(status='used', name=t['name'],
                       message=f"Already checked in at {datetime.fromisoformat(t['checked_in_at']).strftime('%I:%M %p')}.")
    execute('UPDATE tickets SET checked_in_at = ? WHERE id = ?', (now_iso(), t['id']))
    return jsonify(status='ok', name=t['name'], member=bool(active_member(t['email'])),
                   message='Welcome in!', stats=event_stats(event_id))


@app.get('/admin/api/member')
@admin_required
def api_member_search():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    rows = query(MEMBER_SQL + ' WHERE m.name LIKE ? OR m.email LIKE ? OR m.code LIKE ? LIMIT 8', (f'%{q}%',) * 3)
    return jsonify([{'name': m['name'], 'code': m['code'], 'tier': m['tier_name'],
                     'status': member_status(m), 'expires_on': m['expires_on']} for m in rows])


@app.route('/admin/announcements', methods=['GET', 'POST'])
@admin_required
def admin_announcements():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        if not title or not body:
            flash('Title and message are required.', 'error')
            return redirect(url_for('admin_announcements'))
        emailed = 0
        if request.form.get('email_members'):
            for m in query('SELECT * FROM members WHERE paid = 1 AND (expires_on IS NULL OR expires_on >= ?)',
                           (today_iso(),)):
                send_email(m['email'], title, body, 'announcement')
                emailed += 1
        execute('INSERT INTO announcements(title, body, created_at, emailed) VALUES (?,?,?,?)',
                (title, body, now_iso(), emailed))
        flash(f'Announcement posted' + (f' and emailed to {emailed} member(s).' if emailed else '.'))
        return redirect(url_for('admin_announcements'))
    return render_template('admin_announcements.html', posts=query('SELECT * FROM announcements ORDER BY id DESC'),
                           outbox=query('SELECT * FROM outbox ORDER BY id DESC LIMIT 40'))


@app.get('/admin/shop')
@admin_required
def admin_shop():
    variants = {}
    for v in query('SELECT * FROM variants ORDER BY id'):
        variants.setdefault(v['product_id'], []).append(v)
    orders = query('SELECT * FROM orders ORDER BY id DESC LIMIT 50')
    order_items = {}
    for item in query("""SELECT oi.*, v.size, p.name AS product_name FROM order_items oi
                         JOIN variants v ON v.id = oi.variant_id JOIN products p ON p.id = v.product_id"""):
        order_items.setdefault(item['order_id'], []).append(item)
    return render_template('admin_shop.html', products=query('SELECT * FROM products ORDER BY id'),
                           variants=variants, orders=orders, order_items=order_items)



@app.post('/admin/shop/order/<int:order_id>/delivery-status')
@admin_required
def update_delivery_status(order_id):
    allowed_statuses = [
        'Order Placed',
        'Processing',
        'Shipped',
        'Delivered',
        'Cancelled'
    ]

    new_status = request.form.get('delivery_status', '').strip()

    if new_status not in allowed_statuses:
        flash('Invalid delivery status.', 'error')
        return redirect(url_for('admin_shop'))

    order = query(
        'SELECT id FROM orders WHERE id = ?',
        (order_id,),
        one=True
    )

    if not order:
        flash('Order not found.', 'error')
        return redirect(url_for('admin_shop'))

    execute(
        'UPDATE orders SET delivery_status = ? WHERE id = ?',
        (new_status, order_id)
    )

    flash(f'Order #{order_id} delivery status updated to {new_status}.')
    return redirect(url_for('admin_shop'))


@app.post('/admin/shop/product')
@admin_required
def admin_product_add():
    name = request.form.get('name', '').strip()
    price = to_float(request.form.get('price'))
    sizes = request.form.get('sizes', 'S:0,M:0,L:0,XL:0')
    description = request.form.get('description', '').strip()
    image = request.files.get('image')

    if not name or price <= 0:
        flash('Name and a price above zero are required.', 'error')
        return redirect(url_for('admin_shop'))

    image_filename = None

    if image and image.filename:
        original_name = secure_filename(image.filename)

        extension = (
            original_name.rsplit('.', 1)[-1].lower()
            if '.' in original_name else ''
        )

        if extension not in ALLOWED_PRODUCT_IMAGE_EXTENSIONS:
            flash('Please upload a JPG, PNG, or WEBP image.', 'error')
            return redirect(url_for('admin_shop'))

        image_filename = f"{uuid.uuid4().hex}.{extension}"
        image_path = os.path.join(
            PRODUCT_UPLOAD_FOLDER, image_filename
        )
        image.save(image_path)

    product_id = create_product(
        name, price, sizes, description
    )

    if image_filename:
        execute(
            "UPDATE products SET image_filename = ? WHERE id = ?",
            (image_filename, product_id)
        )

    flash('Product added successfully.')
    return redirect(url_for('admin_shop'))



@app.post('/admin/shop/variant/<int:variant_id>')
@admin_required
def admin_variant_stock(variant_id):
    execute('UPDATE variants SET stock = ? WHERE id = ?', (max(0, to_int(request.form.get('stock'))), variant_id))
    flash('Stock updated.')
    return redirect(url_for('admin_shop'))


@app.post('/admin/shop/product/<int:product_id>/toggle')
@admin_required
def admin_product_toggle(product_id):
    execute('UPDATE products SET active = 1 - active WHERE id = ?', (product_id,))
    return redirect(url_for('admin_shop'))


@app.post('/admin/shop/order/<int:order_id>/<action>')
@admin_required
def admin_order_action(order_id, action):
    order = query('SELECT * FROM orders WHERE id = ?', (order_id,), one=True)
    if not order:
        abort(404)
    if action == 'paid' and pay_order(order):
        flash(f'Order #{order_id} marked as paid.')
    elif action == 'cancel' and cancel_order(order):
        flash(f'Order #{order_id} cancelled and stock returned.')
    else:
        flash('Nothing changed.', 'error')
    return redirect(url_for('admin_shop'))


@app.route('/admin/fundraisers', methods=['GET', 'POST'])
@admin_required
def admin_fundraisers():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Give the fundraiser a name.', 'error')
        else:
            fid = execute('INSERT INTO fundraisers(name, description, goal, deadline, created_at) VALUES (?,?,?,?,?)',
                          (name, request.form.get('description', '').strip(), to_float(request.form.get('goal')),
                           request.form.get('deadline') or None, now_iso()))
            return redirect(url_for('admin_fundraiser', fundraiser_id=fid))
    rows = query('SELECT * FROM fundraisers ORDER BY id DESC')
    summary = {r['id']: fundraiser_summary(r) for r in rows}
    return render_template('admin_fundraisers.html', fundraisers=rows, summary=summary)


def fundraiser_summary(f):
    tasks = query('SELECT * FROM tasks WHERE fundraiser_id = ?', (f['id'],))
    done = sum(1 for t in tasks if t['status'] == 'done')
    overdue = sum(1 for t in tasks if t['status'] != 'done' and t['due_date'] and t['due_date'] < today_iso())
    raised = query("SELECT COALESCE(SUM(amount), 0) AS s FROM transactions WHERE ref = ?",
                   (f"fundraiser:{f['id']}",), one=True)['s']
    days_left = (date.fromisoformat(f['deadline']) - date.today()).days if f['deadline'] else None
    task_pct = round(done * 100 / len(tasks)) if tasks else 0
    goal_pct = round(raised * 100 / f['goal']) if f['goal'] else 0
    if overdue:
        health = 'Behind: overdue tasks'
    elif days_left is not None and days_left < 0 and done < len(tasks):
        health = 'Behind: deadline passed'
    elif tasks and done == len(tasks):
        health = 'All tasks done'
    else:
        health = 'On track'
    return {'total': len(tasks), 'done': done, 'overdue': overdue, 'raised': raised, 'days_left': days_left,
            'task_pct': task_pct, 'goal_pct': min(goal_pct, 100), 'goal_pct_raw': goal_pct, 'health': health}


@app.get('/admin/fundraisers/<int:fundraiser_id>')
@admin_required
def admin_fundraiser(fundraiser_id):
    f = query('SELECT * FROM fundraisers WHERE id = ?', (fundraiser_id,), one=True)
    if not f:
        abort(404)
    columns = {s: [] for s in TASK_FLOW}
    for t in query('SELECT * FROM tasks WHERE fundraiser_id = ? ORDER BY due_date, id', (fundraiser_id,)):
        columns[t['status']].append(t)
    return render_template('admin_fundraiser.html', f=f, columns=columns, s=fundraiser_summary(f),
                           today=today_iso(), members=query('SELECT name FROM members ORDER BY name'))


@app.post('/admin/fundraisers/<int:fundraiser_id>/tasks')
@admin_required
def admin_task_add(fundraiser_id):
    title = request.form.get('title', '').strip()
    if title:
        execute('INSERT INTO tasks(fundraiser_id, title, assignee, due_date) VALUES (?,?,?,?)',
                (fundraiser_id, title, request.form.get('assignee', '').strip(), request.form.get('due_date') or None))
    else:
        flash('Task title is required.', 'error')
    return redirect(url_for('admin_fundraiser', fundraiser_id=fundraiser_id))


@app.post('/admin/tasks/<int:task_id>/<direction>')
@admin_required
def admin_task_move(task_id, direction):
    t = query('SELECT * FROM tasks WHERE id = ?', (task_id,), one=True)
    if not t:
        abort(404)
    if direction == 'delete':
        execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    else:
        i = TASK_FLOW.index(t['status']) + (1 if direction == 'next' else -1)
        execute('UPDATE tasks SET status = ? WHERE id = ?', (TASK_FLOW[max(0, min(2, i))], task_id))
    return redirect(url_for('admin_fundraiser', fundraiser_id=t['fundraiser_id']))


@app.post('/admin/fundraisers/<int:fundraiser_id>/raised')
@admin_required
def admin_fundraiser_raised(fundraiser_id):
    f = query('SELECT * FROM fundraisers WHERE id = ?', (fundraiser_id,), one=True)
    amount = to_float(request.form.get('amount'))
    if not f or amount <= 0:
        flash('Enter an amount above zero.', 'error')
    else:
        add_tx('income', 'fundraiser', amount, f"{f['name']}: {request.form.get('note', '').strip() or 'money raised'}",
               ref=f'fundraiser:{fundraiser_id}')
        flash('Recorded in the club ledger.')
    return redirect(url_for('admin_fundraiser', fundraiser_id=fundraiser_id))


@app.get('/admin/claims')
@admin_required
def admin_claims():
    rows = query("""SELECT c.*, m.name AS member_name, m.code FROM claims c JOIN members m ON m.id = c.member_id
                    ORDER BY CASE c.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, c.id DESC""")
    return render_template('admin_claims.html', claims=rows)


@app.post('/admin/claims/<int:claim_id>/<action>')
@admin_required
def admin_claim_action(claim_id, action):
    flash('Claim updated.' if claim_action(claim_id, action) else 'Nothing changed.')
    return redirect(url_for('admin_claims'))


@app.get('/admin/receipt/<int:claim_id>')
@admin_required
def admin_receipt(claim_id):
    claim = query('SELECT receipt_file FROM claims WHERE id = ?', (claim_id,), one=True)
    if not claim or not claim['receipt_file']:
        abort(404)
    return send_from_directory(UPLOAD_DIR, claim['receipt_file'])


def finance_rows():
    start, end = request.args.get('start', ''), request.args.get('end', '')
    category = request.args.get('category', '')
    sql, args = 'SELECT * FROM transactions WHERE 1 = 1', []
    if start:
        sql += ' AND date >= ?'
        args.append(start)
    if end:
        sql += ' AND date <= ?'
        args.append(end)
    if category:
        sql += ' AND category = ?'
        args.append(category)
    return query(sql + ' ORDER BY date DESC, id DESC', args), start, end, category


@app.get('/admin/finance')
@staff_required('treasurer')
def admin_finance():
    rows, start, end, category = finance_rows()
    income = sum(r['amount'] for r in rows if r['kind'] == 'income')
    expense = sum(r['amount'] for r in rows if r['kind'] == 'expense')
    breakdown = {'income': {}, 'expense': {}}
    for r in rows:
        breakdown[r['kind']][r['category']] = breakdown[r['kind']].get(r['category'], 0) + r['amount']
    return render_template('admin_finance.html', rows=rows, income=income, expense=expense, breakdown=breakdown,
                           start=start, end=end, category=category,
                           categories=sorted(set(INCOME_CATEGORIES + EXPENSE_CATEGORIES)),
                           income_cats=INCOME_CATEGORIES, expense_cats=EXPENSE_CATEGORIES, today=today_iso())


@app.post('/admin/finance/add')
@staff_required('treasurer')
def admin_finance_add():
    kind = request.form.get('kind')
    category = request.form.get('category', 'other')
    amount = to_float(request.form.get('amount'))
    description = request.form.get('description', '').strip()
    if kind not in ('income', 'expense') or amount <= 0 or not description:
        flash('Choose income or expense, an amount above zero and a description.', 'error')
    else:
        add_tx(kind, category, amount, description, on=request.form.get('date') or today_iso())
        flash('Entry added to the ledger.')
    return redirect(url_for('admin_finance'))


@app.get('/admin/finance.csv')
@staff_required('treasurer')
def admin_finance_csv():
    rows, *_ = finance_rows()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['date', 'kind', 'category', 'amount', 'description', 'ref'])
    for r in rows:
        writer.writerow([r['date'], r['kind'], r['category'], r['amount'], r['description'], r['ref'] or ''])
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=skyline-ledger.csv'})


@app.cli.command('create-committee-user')
def create_committee_user():
    import getpass

    email = input('Staff email: ').strip().lower()

    roles = {
        'admin',
        'treasurer',
        'event_manager',
        'checkin_staff',
        'delivery_staff'
    }

    print('Available roles:')
    print('admin')
    print('treasurer')
    print('event_manager')
    print('checkin_staff')
    print('delivery_staff')

    role = input('Enter role: ').strip().lower()

    if role not in roles:
        print('Invalid role.')
        return

    if not email or '@' not in email:
        print('Enter a valid email address.')
        return

    password = getpass.getpass('Password (minimum 8 characters): ')

    if len(password) < 8:
        print('Password must be at least 8 characters.')
        return

    existing = db.query(
        'SELECT id FROM committee_users WHERE LOWER(email) = LOWER(?)',
        (email,),
        one=True
    )

    if existing:
        print('That email already has an account.')
        return

    db.execute(
        """
        INSERT INTO committee_users (email, password_hash, role, active)
        VALUES (?, ?, ?, 1)
        """,
        (email, generate_password_hash(password), role)
    )

    print(f'Staff account created successfully with role: {role}')


@app.cli.command('import-staff-csv')
def import_staff_csv():
    csv_path = Path(__file__).resolve().parent / 'staff_accounts.csv'

    if not csv_path.exists():
        print('Error: staff_accounts.csv not found.')
        return

    allowed_roles = {
        'treasurer',
        'event_manager',
        'checkin_staff',
        'delivery_staff'
    }

    created = 0

    with open(csv_path, newline='', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)

        required = {'email', 'password', 'role'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            print('CSV must contain email,password,role columns.')
            return

        for row in reader:
            email = (row.get('email') or '').strip().lower()
            password = row.get('password') or ''
            role = (row.get('role') or '').strip().lower()

            if not email or '@' not in email:
                print(f'Skipped invalid email: {email}')
                continue

            if len(password) < 8:
                print(f'Skipped {email}: password must be at least 8 characters.')
                continue

            if role not in allowed_roles:
                print(f'Skipped {email}: invalid role.')
                continue

            existing = db.query(
                'SELECT id FROM committee_users WHERE LOWER(email) = LOWER(?)',
                (email,),
                one=True
            )

            if existing:
                print(f'Skipped {email}: account already exists.')
                continue

            db.execute(
                '''
                INSERT INTO committee_users
                    (email, password_hash, role, active)
                VALUES (?, ?, ?, 1)
                ''',
                (email, generate_password_hash(password), role)
            )

            created += 1
            print(f'Created {email} ({role})')

    print(f'Import complete. Created {created} staff account(s).')


@app.route('/admin/staff', methods=['GET', 'POST'])
@admin_required
def admin_staff():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role = request.form.get('role', 'committee').strip().lower()

        if not email or '@' not in email or len(password) < 8:
            flash('Enter a valid email and a password of at least 8 characters.', 'error')
            return redirect(url_for('admin_staff'))

        if role not in ('committee', 'admin'):
            flash('Invalid staff role.', 'error')
            return redirect(url_for('admin_staff'))

        if query('SELECT id FROM committee_users WHERE LOWER(email) = ?', (email,), one=True):
            flash('An account with this email already exists.', 'error')
            return redirect(url_for('admin_staff'))

        execute(
            '''INSERT INTO committee_users (email, password_hash, role, active)
               VALUES (?, ?, ?, 1)''',
            (email, generate_password_hash(password), role)
        )
        flash('Staff account created.', 'success')
        return redirect(url_for('admin_staff'))

    staff = query(
        '''SELECT id, email, role, active, created_at
           FROM committee_users ORDER BY id DESC'''
    )
    return render_template('admin_staff.html', staff=staff)


@app.post('/admin/staff/<int:staff_id>/toggle')
@admin_required
def admin_staff_toggle(staff_id):
    person = query(
        'SELECT id, email, active FROM committee_users WHERE id = ?',
        (staff_id,), one=True
    )

    if not person:
        flash('Staff account not found.', 'error')
        return redirect(url_for('admin_staff'))

    new_status = 0 if person['active'] else 1
    execute(
        'UPDATE committee_users SET active = ? WHERE id = ?',
        (new_status, staff_id)
    )
    flash(
        'Staff account deactivated.' if not new_status else 'Staff account activated.',
        'success'
    )
    return redirect(url_for('admin_staff'))


@app.route('/member-login', methods=['GET', 'POST'])
def member_login():
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')

        member = db.query(
            """
            SELECT id, code, name, email, password_hash
            FROM members
            WHERE LOWER(email) = LOWER(?)
               OR LOWER(code) = LOWER(?)
            """,
            (identifier, identifier),
            one=True
        )

        if (
            member
            and member['password_hash']
            and check_password_hash(member['password_hash'], password)
        ):
            session.clear()
            session['member_id'] = member['id']
            return redirect(url_for('member_dashboard'))

        flash('Invalid Member ID/email or password.', 'error')

    return render_template('member_login.html')

@app.route('/member-register', methods=['GET', 'POST'])
def member_register():
    tiers = query('SELECT * FROM tiers ORDER BY price')

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        tier_id = to_int(request.form.get('tier_id'))

        if not name or '@' not in email:
            flash('Enter your name and a valid email.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif password != confirm_password:
            flash('Passwords do not match.', 'error')
        elif not any(t['id'] == tier_id for t in tiers):
            flash('Please choose a valid membership tier.', 'error')
        elif query(
            'SELECT id FROM members WHERE LOWER(email) = LOWER(?)',
            (email,),
            one=True
        ):
            flash('This email is already registered. Please log in.', 'error')
        else:
            try:
                member_id = create_member(name, email, phone, tier_id)

                db.execute(
                    'UPDATE members SET password_hash = ? WHERE id = ?',
                    (generate_password_hash(password), member_id)
                )

                session.clear()
                session['member_id'] = member_id
                flash('Your account has been created.', 'success')
                return redirect(url_for('member_dashboard'))

            except sqlite3.IntegrityError:
                flash('This email is already registered.', 'error')

    return render_template('member_register.html', tiers=tiers)


@app.route('/committee-login')
def login_choice():
    return render_template('login_choice.html')

@app.route('/staff-login', methods=['GET', 'POST'])
def staff_login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        staff = db.query(
            '''
            SELECT id, email, password_hash, role, active
            FROM committee_users
            WHERE LOWER(email) = LOWER(?)
            ''',
            (email,),
            one=True
        )

        if (
            not staff
            or not staff['active']
            or not check_password_hash(staff['password_hash'], password)
        ):
            flash('Invalid email or password, or account inactive.', 'error')
            return render_template('staff_login.html')

        session.clear()
        session['staff_id'] = staff['id']
        session['staff_role'] = staff['role']

        flash('Staff login successful.', 'success')

        return redirect(url_for('staff_dashboard'))

    return render_template('staff_login.html')

@app.route('/staff-dashboard')
def staff_dashboard():
    if not session.get('staff_id'):
        return redirect(url_for('staff_login'))

    return render_template(
        'staff_dashboard.html',
        role=session.get('staff_role')
    )


@app.route('/member-dashboard')
def member_dashboard():
    member_id = session.get('member_id')

    if not member_id:
        return redirect(url_for('member_login'))

    member = db.query(
        """
        SELECT members.*, tiers.name AS tier_name
        FROM members
        LEFT JOIN tiers ON tiers.id = members.tier_id
        WHERE members.id = ?
        """,
        (member_id,),
        one=True
    )

    if not member:
        session.clear()
        flash('Member account not found.', 'error')
        return redirect(url_for('member_login'))

    return render_template('member_dashboard.html', member=member)


@app.post('/member-logout')
def member_logout():
    session.pop('member_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))


@app.get('/staff/deliveries')
@staff_required('delivery_staff')
def staff_deliveries():
    orders = query("""
        SELECT id, name, phone, address, city, pincode,
               total, status, delivery_status, created_at
        FROM orders
        WHERE status = 'paid'
        ORDER BY id DESC
    """)
    return render_template('staff_deliveries.html', orders=orders)


@app.post('/staff/deliveries/<int:order_id>/status')
@staff_required('delivery_staff')
def staff_update_delivery_status(order_id):
    allowed_statuses = [
        'Order Placed',
        'Processing',
        'Shipped',
        'Delivered',
        'Cancelled'
    ]

    new_status = request.form.get('delivery_status', '').strip()

    if new_status not in allowed_statuses:
        flash('Invalid delivery status.', 'error')
        return redirect(url_for('staff_deliveries'))

    order = query(
        'SELECT id FROM orders WHERE id = ? AND status = ?',
        (order_id, 'paid'),
        one=True
    )

    if not order:
        flash('Paid order not found.', 'error')
        return redirect(url_for('staff_deliveries'))

    execute(
        'UPDATE orders SET delivery_status = ? WHERE id = ?',
        (new_status, order_id)
    )

    flash(f'Order #{order_id} delivery status updated successfully.')
    return redirect(url_for('staff_deliveries'))


@app.route('/customer-register', methods=['GET', 'POST'])
def customer_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or '@' not in email:
            flash('Enter your name and a valid email.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif password != confirm_password:
            flash('Passwords do not match.', 'error')
        elif query(
            'SELECT id FROM customer_accounts WHERE email = ?',
            (email,),
            one=True
        ):
            flash('An account already exists. Please log in.', 'error')
        else:
            try:
                customer_id = execute(
                    '''
                    INSERT INTO customer_accounts
                        (name, email, password_hash)
                    VALUES (?, ?, ?)
                    ''',
                    (name, email, generate_password_hash(password))
                )

                session.clear()
                session['customer_id'] = customer_id
                flash('Account created successfully.', 'success')
                return redirect(url_for('customer_dashboard'))

            except sqlite3.IntegrityError:
                flash('An account already exists for this email.', 'error')

    return render_template('customer_register.html')


@app.route('/customer-login', methods=['GET', 'POST'])
def customer_login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        customer = query(
            '''
            SELECT id, email, password_hash
            FROM customer_accounts
            WHERE email = ?
            ''',
            (email,),
            one=True
        )

        if not customer or not check_password_hash(
            customer['password_hash'], password
        ):
            flash('Invalid email or password.', 'error')
            return render_template('customer_login.html')

        session.clear()
        session['customer_id'] = customer['id']
        flash('Logged in successfully.', 'success')
        return redirect(url_for('customer_dashboard'))

    return render_template('customer_login.html')



@app.get('/customer-dashboard')
def customer_dashboard():
    customer_id = session.get('customer_id')

    if not customer_id:
        flash('Please log in to view your account.', 'info')
        return redirect(url_for('customer_login'))

    customer = query(
        'SELECT id, name, email FROM customer_accounts WHERE id = ?',
        (customer_id,),
        one=True
    )

    if not customer:
        session.clear()
        flash('Account not found. Please log in again.', 'error')
        return redirect(url_for('customer_login'))

    tickets = query(
        '''
        SELECT t.*, e.name AS event_name, e.venue, e.starts_at
        FROM tickets t
        JOIN events e ON e.id = t.event_id
        WHERE LOWER(t.email) = LOWER(?)
        ORDER BY t.id DESC
        ''',
        (customer['email'],)
    )

    orders = query(
        '''
        SELECT id, email, total, status, delivery_status,
               address, city, pincode, created_at, paid_at
        FROM orders
        WHERE LOWER(email) = LOWER(?)
        ORDER BY id DESC
        ''',
        (customer['email'],)
    )

    return render_template(
        'customer_dashboard.html',
        customer=customer,
        tickets=tickets,
        orders=orders
    )



@app.post('/customer-logout')
def customer_logout():
    session.pop('customer_id', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'reminders':
        with app.test_request_context():
            print(f'{send_due_reminders()} renewal reminder(s) queued.')
    else:
        app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=to_int(os.environ.get('PORT'), 5000))
