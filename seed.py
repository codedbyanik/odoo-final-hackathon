"""Fill the database with realistic demo data: python seed.py"""
from datetime import date, datetime, timedelta

import app as club
from db import execute, query


def at(days, hour=18):
    return (datetime.combine(date.today(), datetime.min.time()) + timedelta(days=days, hours=hour)).isoformat(timespec='minutes')


def seed():
    with club.app.test_request_context():
        if query('SELECT 1 FROM tiers LIMIT 1'):
            print('Database already has data. Delete club.db to start fresh.')
            return

        standard = execute("INSERT INTO tiers(name, price, discount_percent) VALUES ('Standard', 300, 10)")
        premium = execute("INSERT INTO tiers(name, price, discount_percent) VALUES ('Premium', 600, 20)")

        people = [
            ('Aarav Menon', standard, True), ('Diya Nair', premium, True), ('Rohan Iyer', standard, True),
            ('Ananya Rao', premium, True), ('Karthik Subramanian', standard, True), ('Meera Krishnan', standard, True),
            ('Vikram Shetty', premium, True), ('Sneha Pillai', standard, False), ('Arjun Reddy', standard, False),
            ('Nisha Kapoor', standard, True),
        ]
        ids = {}
        for name, tier, paid in people:
            email = name.lower().replace(' ', '.') + '@example.com'
            ids[name] = club.create_member(name, email, '', tier)
            if paid:
                club.pay_membership(ids[name], on=(date.today() - timedelta(days=40)).isoformat())
        # one membership about to lapse, one already lapsed
        execute('UPDATE members SET expires_on = ? WHERE id = ?', ((date.today() + timedelta(days=12)).isoformat(), ids['Meera Krishnan']))
        execute('UPDATE members SET expires_on = ? WHERE id = ?', ((date.today() - timedelta(days=3)).isoformat(), ids['Nisha Kapoor']))

        past = execute("""INSERT INTO events(name, description, venue, starts_at, capacity, member_price, public_price, created_at)
                          VALUES ('Welcome Mixer', 'Meet the committee and other members.', 'Student Centre', ?, 60, 100, 150, ?)""",
                       (at(-30), club.now_iso()))
        execute("""INSERT INTO events(name, description, venue, starts_at, capacity, member_price, public_price, created_at)
                   VALUES ('Tech Talk Night', 'Lightning talks from final-year students.', 'Seminar Hall 2', ?, 80, 0, 50, ?)""",
                (at(14), club.now_iso()))
        gala = execute("""INSERT INTO events(name, description, venue, starts_at, capacity, member_price, public_price, created_at)
                          VALUES ('Spring Gala', 'The biggest night of the club year: dinner, music and awards.', 'Grand Ballroom', ?, 150, 400, 500, ?)""",
                       (at(60, 19), club.now_iso()))

        for name in ('Aarav Menon', 'Diya Nair', 'Rohan Iyer', 'Ananya Rao'):
            batch, _ = club.create_ticket_order(past, name, name.lower().replace(' ', '.') + '@example.com', 1)
            club.pay_tickets(batch)
        batch, _ = club.create_ticket_order(past, 'Guest One', 'guest1@example.com', 2)
        club.pay_tickets(batch)
        for t in query('SELECT id FROM tickets WHERE event_id = ? ORDER BY id LIMIT 4', (past,)):
            execute('UPDATE tickets SET checked_in_at = ? WHERE id = ?', (at(-30, 19), t['id']))
        batch, _ = club.create_ticket_order(gala, 'Karthik Subramanian', 'karthik.subramanian@example.com', 2)
        club.pay_tickets(batch)
        club.create_ticket_order(gala, 'Priya Walk-in', 'priya@example.com', 1)  # left unpaid on purpose

        hoodie = club.create_product('Skyline Hoodie', 899, 'S:8,M:12,L:10,XL:5', 'Heavy fleece, embroidered logo.')
        club.create_product('Skyline T-Shirt', 499, 'S:15,M:20,L:15,XL:10', 'Soft cotton, printed logo.')
        sizes = {v['size']: v['id'] for v in query('SELECT * FROM variants WHERE product_id = ?', (hoodie,))}
        token, _ = club.create_shop_order('Diya Nair', 'diya.nair@example.com', {sizes['M']: 1})
        club.pay_order(query('SELECT * FROM orders WHERE token = ?', (token,), one=True))
        club.create_shop_order('Guest Two', 'guest2@example.com', {sizes['L']: 2})  # pending payment

        bake = execute("INSERT INTO fundraisers(name, description, goal, deadline, created_at) VALUES (?,?,?,?,?)",
                       ('Bake Sale', 'Raise money for the spring gala decorations.', 5000,
                        (date.today() + timedelta(days=20)).isoformat(), club.now_iso()))
        for title, who, status, due in [
            ('Bake cookies and brownies', 'Ananya Rao', 'doing', 10), ('Buy flour, sugar and packaging', 'Rohan Iyer', 'done', 5),
            ('Print price list and prepare cash box', 'Aarav Menon', 'todo', 15), ('Manage the table on the day', 'Vikram Shetty', 'todo', 20),
            ('Post flyers around campus', 'Diya Nair', 'doing', -2)]:
            execute('INSERT INTO tasks(fundraiser_id, title, assignee, status, due_date) VALUES (?,?,?,?,?)',
                    (bake, title, who, status, (date.today() + timedelta(days=due)).isoformat()))
        club.add_tx('income', 'fundraiser', 1200, 'Bake Sale: pre-orders from the hostel', ref=f'fundraiser:{bake}')

        for title, body in [('Welcome to the new club website', 'Memberships, tickets and merch are now all online. Tell your friends!'),
                            ('Spring Gala tickets are on sale', 'Members pay 400, everyone else 500. Seats are limited to 150.')]:
            execute('INSERT INTO announcements(title, body, created_at) VALUES (?,?,?)', (title, body, club.now_iso()))

        c1 = club.submit_claim(ids['Rohan Iyer'], 640, 'Supplies for the bake sale')
        c2 = club.submit_claim(ids['Aarav Menon'], 1800, 'Printing posters for the mixer')
        club.submit_claim(ids['Ananya Rao'], 350, 'Packaging boxes')
        club.claim_action(c1, 'approve')
        club.claim_action(c2, 'approve')
        club.claim_action(c2, 'reimburse')

        club.add_tx('expense', 'venue', 2500, 'Student Centre hire for the Welcome Mixer', on=(date.today() - timedelta(days=30)).isoformat())
        club.add_tx('income', 'donation', 1000, 'Alumni donation', on=(date.today() - timedelta(days=20)).isoformat())
        print('Demo data created. Log in at /login with password "skyline".')


if __name__ == '__main__':
    seed()
