import csv
import secrets
import string

from app import app
import db
from werkzeug.security import generate_password_hash


def setup_passwords():
    db.init_db()

    members = db.query("""
        SELECT id, code, name, email, password_hash
        FROM members
        ORDER BY id
    """)

    if not members:
        print("No members found in the database.")
        print("Add your existing members first.")
        return

    alphabet = string.ascii_letters + string.digits + "!@#$%"
    created = []

    for member in members:
        if member["password_hash"]:
            continue

        password = "".join(
            secrets.choice(alphabet) for _ in range(14)
        )

        db.execute(
            "UPDATE members SET password_hash = ? WHERE id = ?",
            (
                generate_password_hash(password),
                member["id"]
            )
        )

        created.append({
            "member_id": member["code"] or f"SKY-{member['id']:04d}",
            "name": member["name"],
            "email": member["email"],
            "temporary_password": password,
        })

    if not created:
        print("All existing members already have passwords.")
        return

    filename = "member_initial_credentials.csv"

    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "member_id",
                "name",
                "email",
                "temporary_password",
            ],
        )
        writer.writeheader()
        writer.writerows(created)

    print(f"Created passwords for {len(created)} members.")
    print(f"Credentials saved to {filename}")
    print("Keep this file private and delete it after distributing credentials.")


if __name__ == "__main__":
    with app.app_context():
        setup_passwords()