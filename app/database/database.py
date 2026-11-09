"""SQLite initialization and versioned migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


Migration = Callable[[sqlite3.Connection], None]


def _migration_1_create_tasks(connection: sqlite3.Connection) -> None:
    # IF NOT EXISTS：老版本程序建过表但没有 schema_migrations 记录时，
    # 这一版要能把旧库升级到 v1，而不是 OperationalError 直接起不来。
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
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
        "CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks(created_at DESC)"
    )


def _migration_2_add_task_metadata(connection: sqlite3.Connection) -> None:
    # 逐列检查再 ALTER：升级路径必须幂等（旧库可能已有其中一列），
    # 否则 ALTER TABLE 抛 duplicate column name，整个程序起不来。
    existing = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
    if "retry_of" not in existing:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN retry_of TEXT REFERENCES tasks(task_id)"
        )
    if "result_json" not in existing:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS tasks_queue_idx ON tasks(status, created_at)"
    )


MIGRATIONS: dict[int, Migration] = {
    1: _migration_1_create_tasks,
    2: _migration_2_add_task_metadata,
}


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    # 自己管事务：默认隔离级别会在 DML 前隐式 BEGIN，跟下面的
    # BEGIN IMMEDIATE 冲突（cannot start a transaction within a transaction）。
    connection.isolation_level = None
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
            # 跨进程互斥迁移。桌面端和 Web 控制台共用同一个任务库，两边都会在
            # 启动时调用这里：原来的「读版本 → 跑迁移 → 记版本」不是原子的，
            # 两个进程同时首启时都读到版本 0，都执行 CREATE TABLE，后提交的一方
            # 直接 OperationalError「table tasks already exists」，程序起不来。
            # BEGIN IMMEDIATE 马上拿写锁：后到的进程阻塞到前一方提交（最多
            # timeout=10s），然后在锁内重读版本，发现已应用就跳过。
            connection.execute("BEGIN IMMEDIATE")
            try:
                already_applied = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
                if version <= already_applied:
                    connection.execute("COMMIT")
                    continue
                migration(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    finally:
        connection.close()