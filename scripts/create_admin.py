"""Create Aurora's first local administrator.

Usage: python -m scripts.create_admin --username admin
"""
import argparse
import getpass
import sqlite3

from services.auth import hash_password
from services.database import AppDatabase
from utils import settings
from utils.path_tool import get_abs_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an Aurora administrator")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")

    db = AppDatabase(get_abs_path(settings.APP_DB_PATH))
    try:
        user = db.create_user(
            username=args.username.strip(),
            password_hash=hash_password(password),
            role="admin",
            created_by="bootstrap",
        )
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise SystemExit(f"Unable to create administrator: {exc}") from exc
    print(f"Administrator created: {user.username}")


if __name__ == "__main__":
    main()
