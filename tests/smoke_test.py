"""End-to-end smoke test: python tests/smoke_test.py"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['CLUB_DB'] = os.path.join(tmp, 'test.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as club  # noqa: E402
import seed  # noqa: E402
from db import query  # noqa: E402

seed.seed()
client = club.app.test_client()


def ok(response, code=200):
    assert response.status_code == code, f'{response.request.path} -> {response.status_code}'
    return response.get_data(as_text=True)


def check(label, condition):
    print(('PASS ' if condition else 'FAIL ') + label)
    if not condition:
        sys.exit(1)


# public pages
for path in ['/', '/events', '/events/1', '/announcements', '/shop', '/join', '/membership', '/expenses/new', '/login']:
    ok(client.get(path))
check('public pages load', True)
ok(client.get('/admin'), 302)
check('admin pages are locked', True)

# join + pay
ok(client.post('/join', data={'name': 'Test Student', 'email': 'test@example.com', 'phone': '1', 'tier_id': '1'}), 302)
with club.app.app_context():
    member = query('SELECT * FROM members WHERE email = ?', ('test@example.com',), one=True)
    mid = member['id']
    check('member created unpaid', member['paid'] == 0 and member['code'].startswith('SKY-'))
ok(client.get(f'/pay/membership/{mid}'))
ok(client.post(f'/pay/membership/{mid}'), 302)
with club.app.app_context():
    check('member active after payment', club.member_status(query('SELECT * FROM members WHERE id=?', (mid,), one=True)) == 'active')
check('membership page shows active', 'Active' in ok(client.get('/membership?q=test@example.com')))

# member vs non-member ticket price
ok(client.post('/events/3/buy', data={'name': 'Test Student', 'email': 'test@example.com', 'qty': '2'}), 302)
ok(client.post('/events/3/buy', data={'name': 'Walker', 'email': 'walker@example.com', 'qty': '1'}), 302)
with club.app.app_context():
    prices = [r['price_paid'] for r in query("SELECT price_paid FROM tickets WHERE email IN ('test@example.com','walker@example.com') ORDER BY id")]
    check('member 400 vs public 500', prices == [400, 400, 500])
    batch = query("SELECT batch FROM tickets WHERE email='test@example.com'", one=True)['batch']
ok(client.post(f'/pay/tickets/{batch}'), 302)
check('ticket page has code', 'class="code"' in ok(client.get(f'/tickets/{batch}')))

# seat limit
ok(client.post('/events/2/buy', data={'name': 'X', 'email': 'x@example.com', 'qty': '9'}), 302)
with club.app.app_context():
    check('max 5 tickets enforced', query("SELECT COUNT(*) AS n FROM tickets WHERE email='x@example.com'", one=True)['n'] == 0)

# admin
ok(client.post('/login', data={'password': 'wrong'}))
ok(client.post('/login', data={'password': 'skyline'}), 302)
for path in ['/admin', '/admin/members', '/admin/events', '/admin/events/1', '/admin/checkin', '/admin/announcements',
             '/admin/shop', '/admin/fundraisers', '/admin/fundraisers/1', '/admin/claims', '/admin/finance', '/admin/finance.csv']:
    ok(client.get(path))
check('all committee pages load', True)

# door check-in
with club.app.app_context():
    code = query("SELECT code FROM tickets WHERE email='test@example.com' ORDER BY id", one=True)['code']
    unpaid = query('SELECT code FROM tickets WHERE paid = 0', one=True)['code']
r = client.post('/admin/checkin', data={'code': code.lower(), 'event_id': '3'}).get_json()
check('check-in works and sees member', r['status'] == 'ok' and r['member'] is True)
check('second scan flagged', client.post('/admin/checkin', data={'code': code, 'event_id': '3'}).get_json()['status'] == 'used')
check('invalid code', client.post('/admin/checkin', data={'code': 'NOPE1234', 'event_id': '3'}).get_json()['status'] == 'invalid')
check('wrong event', client.post('/admin/checkin', data={'code': code, 'event_id': '1'}).get_json()['status'] == 'wrong_event')
check('unpaid ticket refused', client.post('/admin/checkin', data={'code': unpaid, 'event_id': '3'}).get_json()['status'] == 'unpaid')

# merch: discount, stock, payment
with club.app.app_context():
    v = query("SELECT v.id, v.stock FROM variants v JOIN products p ON p.id=v.product_id WHERE p.name='Skyline T-Shirt' AND v.size='M'", one=True)
r = client.post('/shop/order', data={'name': 'Test Student', 'email': 'test@example.com', f'qty_{v["id"]}': '2'})
ok(r, 302)
token = r.headers['Location'].rsplit('/', 1)[-1]
with club.app.app_context():
    order = query('SELECT * FROM orders WHERE token=?', (token,), one=True)
    check('tier discount applied (10% of 998)', abs(order['total'] - 898.2) < 0.01)
    check('stock reduced', query('SELECT stock FROM variants WHERE id=?', (v['id'],), one=True)['stock'] == v['stock'] - 2)
ok(client.post(f'/pay/order/{token}'), 302)
ok(client.post('/shop/order', data={'name': 'Greedy', 'email': 'g@example.com', f'qty_{v["id"]}': '10'}), 302)
r = client.post('/shop/order', data={'name': 'Greedy', 'email': 'g@example.com', f'qty_{v["id"]}': '10'})
ok(r, 302)
check('overselling blocked', r.headers['Location'].endswith('/shop'))
with club.app.app_context():
    check('stock never negative', query('SELECT MIN(stock) AS m FROM variants', one=True)['m'] >= 0)

# expense claim flow
with club.app.app_context():
    before = query("SELECT COALESCE(SUM(amount),0) AS s FROM transactions WHERE category='reimbursement'", one=True)['s']
ok(client.post('/expenses/new', data={'email': 'test@example.com', 'amount': '250', 'description': 'Snacks for meeting'}), 302)
with club.app.app_context():
    cid = query('SELECT id FROM claims ORDER BY id DESC', one=True)['id']
for action in ('approve', 'reimburse'):
    ok(client.post(f'/admin/claims/{cid}/{action}'), 302)
with club.app.app_context():
    after = query("SELECT COALESCE(SUM(amount),0) AS s FROM transactions WHERE category='reimbursement'", one=True)['s']
    check('reimbursement lands in ledger', after - before == 250)
r = client.post('/expenses/new', data={'email': 'walker@example.com', 'amount': '50', 'description': 'x'})
check('non-members cannot claim', r.status_code == 400)

# fundraiser board
ok(client.post('/admin/fundraisers/1/tasks', data={'title': 'New task', 'assignee': 'Test', 'due_date': ''}), 302)
with club.app.app_context():
    tid = query('SELECT id FROM tasks ORDER BY id DESC', one=True)['id']
ok(client.post(f'/admin/tasks/{tid}/next'), 302)
with club.app.app_context():
    check('task moved forward', query('SELECT status FROM tasks WHERE id=?', (tid,), one=True)['status'] == 'doing')

# finance totals match the ledger
with club.app.app_context():
    t = query("""SELECT SUM(CASE WHEN kind='income' THEN amount END) AS i, SUM(CASE WHEN kind='expense' THEN amount END) AS e FROM transactions""", one=True)
csv_text = ok(client.get('/admin/finance.csv'))
with club.app.app_context():
    rows = query('SELECT COUNT(*) AS n FROM transactions', one=True)['n']
check('csv has every ledger row', len(csv_text.strip().splitlines()) - 1 == rows)
check('income exceeds expenses in demo', t['i'] > t['e'])

# renewal reminders are idempotent
ok(client.get('/admin'))
with club.app.app_context():
    n1 = query("SELECT COUNT(*) AS n FROM outbox WHERE kind='renewal'", one=True)['n']
ok(client.get('/admin'))
with club.app.app_context():
    n2 = query("SELECT COUNT(*) AS n FROM outbox WHERE kind='renewal'", one=True)['n']
check('renewal reminder sent once only', n1 == 1 and n2 == 1)
print('ALL GOOD')
