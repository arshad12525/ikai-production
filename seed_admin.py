"""
Run this ONCE (locally or via `railway run python seed_admin.py`) to create
your first admin and warehouse logins. Edit the values below before running.
"""
import os
import pymysql
from werkzeug.security import generate_password_hash

conn = pymysql.connect(
    host=os.environ.get('MYSQLHOST', 'localhost'),
    user=os.environ.get('MYSQLUSER', 'root'),
    password=os.environ.get('MYSQLPASSWORD', 'root'),
    database=os.environ.get('MYSQLDATABASE', 'ikai_production'),
    port=int(os.environ.get('MYSQLPORT', 3306)),
)

users_to_create = [
    # (username, password, full_name, role)
    ('admin', '12525', 'Admin', 'admin'),
    ('warehouse1', '12525', 'Warehouse Staff', 'store'),
]

with conn.cursor() as cur:
    for username, password, full_name, role in users_to_create:
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            print(f"User '{username}' already exists, skipping.")
            continue
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
            (username, generate_password_hash(password), full_name, role),
        )
        print(f"Created user '{username}' with role '{role}'.")

conn.commit()
conn.close()
print("Done. Remember to change these passwords after first login (or delete this script's plaintext values).")
