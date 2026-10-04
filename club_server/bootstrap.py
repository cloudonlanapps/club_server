"""Bootstrap utility for creating or updating the sudo super admin user.

Usage:
    club_bootstrap <password>

This creates the 'sudo' super admin user if it doesn't exist,
or updates the password if it does.
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from .db.engine import async_session_maker
from .db.models.user import User, UserStatus
from .services.auth import AuthService
from .utils import now_utc_ms


async def bootstrap_sudo_user(password: str) -> bool:
    """Create or update the sudo super admin user.

    Args:
        password: The password to set for the sudo user.

    Returns:
        True if user was created, False if updated.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.username == "sudo", User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()

        if user:
            user.password = AuthService.hash_password(password)
            user.password_changed_at = now_utc_ms()
            await session.commit()
            return False
        else:
            user = User(
                username="sudo",
                password=AuthService.hash_password(password),
                first_name="Super Admin",
                status=UserStatus.active.value,
                is_super_admin=1,
                roles=json.dumps({"roles": ["admin"]}),
                created_at=now_utc_ms(),
            )
            session.add(user)
            await session.commit()
            return True


def main() -> None:
    """CLI entry point for bootstrap utility."""
    parser = argparse.ArgumentParser(
        description="Create or update the sudo super admin user"
    )
    _ = parser.add_argument(
        "password",
        help="Password for the sudo user",
    )
    args = parser.parse_args()

    if len(args.password) < 6:
        print("Error: Password must be at least 6 characters", file=sys.stderr)
        sys.exit(1)

    created = asyncio.run(bootstrap_sudo_user(args.password))

    if created:
        print("Created sudo super admin user")
    else:
        print("Updated password for sudo super admin user")


if __name__ == "__main__":
    main()
