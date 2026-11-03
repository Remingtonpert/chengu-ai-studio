"""SQLite initialization and versioned migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


Migration = Callable[[sqlite3.Connection], None]


def _migration_1_create_tasks(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            engine_id TEXT NOT NULL,
            model_id TEXT,
            prompt TEXT NOT NULL,
            input_files_json TEXT NOT NULL DEFAULT '[]',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'preparing', 'running', 'completed', 'failed', 'cancelled')
            ),
            progress REAL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            output_files_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX tasks_created_at_idx ON tasks(created_at DESC)"
    )


def _migration_2_add_task_metadata(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE tasks ADD COLUMN retry_of TEXT REFERENCES tasks(task_id)"
    )
    connection.execute(
        "ALTER TABLE tasks ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'"
    )
    connection.execute(
        "CREATE INDEX tasks_queue_idx ON tasks(status, created_at)"
    )


MIGRATIONS: dict[int, Migration] = {
    1: _migration_1_create_tasks,
    2: _migration_2_add_task_metadata,
}


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        current_version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        if current_version > max(MIGRATIONS, default=0):
            raise RuntimeError(
                f"数据库版本 {current_version} 高于本程序支持版本"
            )
        for version, migration in sorted(MIGRATIONS.items()):
            if version <= current_version:
                continue
            with connection:
                migration(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                )
    finally:
        connection.close()