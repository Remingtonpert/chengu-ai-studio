"""Local persistence for the desktop creation-center sessions.

The web console keeps sessions in browser ``localStorage``; the desktop shell
keeps the very same structure (session -> batches -> taskIds) in a small JSON
file next to the task database, so the creative timeline survives restarts
without touching the shared SQLite task store or the service layer.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

SESSIONS_FILENAME = "desktop_sessions.json"
MAX_SESSIONS = 120


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class SessionStore:
    """CRUD over the desktop sessions JSON file (atomic writes)."""

    def __init__(self, data_dir: Path, logger=None) -> None:
        self.path = Path(data_dir) / SESSIONS_FILENAME
        self.logger = logger
        self.sessions: list[dict[str, Any]] = []
        self.active_id: str | None = None
        self.settings: dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        sessions = raw.get("sessions")
        if isinstance(sessions, list):
            self.sessions = [
                session
                for session in sessions
                if isinstance(session, dict) and session.get("id")
            ][:MAX_SESSIONS]
        active_id = raw.get("activeId")
        if isinstance(active_id, str) and self.get(active_id) is not None:
            self.active_id = active_id
        elif self.sessions:
            self.active_id = self.sessions[0]["id"]
        settings = raw.get("settings")
        if isinstance(settings, dict):
            self.settings = settings

    def persist(self) -> None:
        payload = {
            "version": 1,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "activeId": self.active_id,
            "settings": self.settings,
            "sessions": self.sessions,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=".sessions-",
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(handle.name, self.path)
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning("会话数据保存失败：%s", exc)

    # --------------------------------------------------------------- CRUD
    def get(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        for session in self.sessions:
            if session.get("id") == session_id:
                return session
        return None

    def active(self) -> dict[str, Any] | None:
        return self.get(self.active_id)

    def create(self, title: str) -> dict[str, Any]:
        session = {
            "id": new_id(),
            "title": (title or "新会话")[:48],
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "thumb": None,
            "batches": [],
        }
        self.sessions.insert(0, session)
        self.active_id = session["id"]
        self.persist()
        return session

    def ensure_active(self, title: str) -> dict[str, Any]:
        session = self.active()
        if session is not None:
            return session
        return self.create(title)

    def activate(self, session_id: str) -> None:
        if self.get(session_id) is not None:
            self.active_id = session_id
            self.persist()

    def rename(self, session_id: str, title: str) -> None:
        session = self.get(session_id)
        if session is not None and title.strip():
            session["title"] = title.strip()[:48]
            self.persist()

    def delete(self, session_id: str) -> None:
        self.sessions = [s for s in self.sessions if s.get("id") != session_id]
        if self.active_id == session_id:
            self.active_id = self.sessions[0]["id"] if self.sessions else None
        self.persist()

    def add_batch(self, session_id: str, batch: dict[str, Any]) -> dict[str, Any]:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"会话不存在：{session_id}")
        batch.setdefault("id", new_id())
        batch.setdefault("taskIds", [])
        session["batches"].append(batch)
        self.persist()
        return batch

    def set_thumb(self, session_id: str, thumb_path: str | None) -> None:
        session = self.get(session_id)
        if session is not None and thumb_path and not session.get("thumb"):
            session["thumb"] = thumb_path
            self.persist()
