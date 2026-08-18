"""Create or update a dashboard account.

Usage:
    python scripts/create_user.py --username admin
    python scripts/create_user.py --username admin --reset-password
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.security import hash_password  # noqa: E402


def prompt_password() -> str:
    while True:
        first = getpass.getpass("Passwort: ")
        if len(first) < 10:
            print("Bitte mindestens 10 Zeichen verwenden.")
            continue
        if first != getpass.getpass("Passwort wiederholen: "):
            print("Die Eingaben stimmen nicht überein.")
            continue
        return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument(
        "--reset-password", action="store_true", help="set a new password for an existing account"
    )
    args = parser.parse_args()

    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        user = session.scalars(select(User).where(User.username == args.username)).first()

        if user is not None and not args.reset_password:
            print(
                f'Benutzer "{args.username}" existiert bereits — '
                "--reset-password verwenden, um das Passwort zu ändern."
            )
            return 1

        password = prompt_password()
        if user is None:
            session.add(
                User(
                    username=args.username,
                    password_hash=hash_password(password),
                    display_name=args.display_name or args.username,
                )
            )
            print(f'Benutzer "{args.username}" angelegt.')
        else:
            user.password_hash = hash_password(password)
            if args.display_name:
                user.display_name = args.display_name
            print(f'Passwort für "{args.username}" geändert.')
        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
