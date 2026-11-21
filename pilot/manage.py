import argparse
import getpass
import os
from . import db
from .auth import password_hash

def main():
    parser = argparse.ArgumentParser(description="Provision a ReleasePilot account")
    parser.add_argument("email")
    parser.add_argument("role", choices=["viewer", "developer", "approver", "admin"])
    parser.add_argument("--environment", action="append", help="Developer release grant; repeat for additional environments")
    args = parser.parse_args()
    password = os.getenv("PILOT_NEW_PASSWORD") or getpass.getpass("Password (12+ characters): ")
    if len(password) < 12:
        parser.error("Use at least 12 characters")
    with db.transaction(True) as conn:
        uid = conn.execute(db.users.insert().values(email=args.email.lower(), role=args.role,
                            environments=args.environment or ["development"],
                            password_hash=password_hash(password))).inserted_primary_key[0]
        db.audit(conn, "account.created", account=uid, role=args.role)

if __name__ == "__main__":
    main()
