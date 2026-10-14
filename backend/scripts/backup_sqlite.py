"""Create a consistent SQLite backup using SQLite's backup API."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise SystemExit("DATABASE_URL must point to SQLite for this backup script")

    source_path = Path(database_url[len(prefix) :])
    backup_dir = Path(os.environ.get("BACKUP_DIR", "/backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination_path = backup_dir / f"coach-{timestamp}.db"

    with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
        source.backup(destination)

    print(destination_path)


if __name__ == "__main__":
    main()
