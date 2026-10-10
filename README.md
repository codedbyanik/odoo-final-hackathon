# Skyline Student Association system

One Flask + SQLite app for memberships, events and tickets, announcements, merch,
fundraiser planning and the treasurer's books. No Odoo, no build step.

## Run it
    pip install -r requirements.txt
    python seed.py        # optional demo data
    python app.py         # http://localhost:5000

Committee login password: `skyline` (change with the CLUB_ADMIN_PASSWORD environment variable).

## Where each scene lives
- New member and renewals: /join, /membership (public), Committee > Members
- Spring gala tickets and door check-in: /events (public), Committee > Events, Door check-in
- Announcements: /announcements (public), Committee > Announcements (also emails members and keeps a log)
- Hoodies: /shop (public), Committee > Merch
- Bake sale: Committee > Fundraisers (task board, progress, money raised)
- Treasurer: Committee > Finance (ledger, totals, CSV), Expense claims, /expenses/new for volunteers

## How the parts connect
- Paying dues, tickets, merch orders and fundraiser takings all write to one ledger automatically.
- Active members get member ticket prices and their tier's merch discount just by using their email.
- Renewal reminders go out automatically 30 days before expiry (checked whenever the committee dashboard opens,
  or run `python app.py reminders` from a daily cron job).
- Reimbursed expense claims become ledger expenses.

## Settings (environment variables)
- CLUB_ADMIN_PASSWORD, SECRET_KEY: set both before any real use.
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM: send real emails. Without them, emails are only logged.
- BASE_URL: the public address used in email links.

## Known limits (good "next steps" slide)
- Payments are a demo button; swap in Razorpay or UPI at the pay step.
- One shared committee password, no separate roles, and no CSRF tokens yet.
- QR scanning uses a CDN script, so the door tablet needs internet; typing the code always works.

## Test
    python tests/smoke_test.py
