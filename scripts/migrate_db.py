#!/usr/bin/env python
"""
Run Alembic migrations against the configured database.

Usage:
    python scripts/migrate_db.py          # Upgrade to latest
    python scripts/migrate_db.py --check  # Check current revision only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _add_src_to_path() -> None:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


def get_alembic_config():
    from alembic.config import Config

    alembic_ini = PROJECT_ROOT / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def get_current_revision(database_url: str) -> str | None:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def get_head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    config = get_alembic_config()
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    return heads[0] if heads else None


def run_upgrade() -> bool:
    from alembic import command

    _add_src_to_path()
    from jobintel.core.config import settings

    print(f"Running migrations against: {settings.DATABASE_URL[:50]}...")
    config = get_alembic_config()

    old_cwd = os.getcwd()
    try:
        os.chdir(PROJECT_ROOT)
        command.upgrade(config, "head")
        return True
    except Exception as e:
        print(f"Migration error: {e}")
        return False
    finally:
        os.chdir(old_cwd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Exit nonzero if DB is not at Alembic head"
    )
    args = parser.parse_args()

    _add_src_to_path()
    from jobintel.core.config import settings

    print(f"Environment: {settings.ENV}")
    print(f"Database: {settings.DATABASE_URL[:50]}...")
    print()

    current = get_current_revision(settings.DATABASE_URL)
    head = get_head_revision()

    print(f"Current revision: {current or '(none)'}")
    print(f"Head revision: {head or '(unknown)'}")
    print()

    if args.check:
        if current == head:
            print("Database is up to date.")
            return 0
        print("Database needs migration.")
        return 1

    if run_upgrade():
        print("\nMigrations completed successfully.")
        return 0
    print("\nMigration failed!")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
