"""Layer 0 — Session persistence.

The OpenClaw agentic-loop definition ends in **persistence**:

    intake -> context assembly -> model inference -> tool execution
    -> streaming replies -> persistence

`pyclaw chat` is a long-lived multi-turn loop. To honour that definition (and
the spec's Episodic Memory — "จดจำเหตุการณ์และการตัดสินใจที่ผ่านมา") this module
saves the conversation to disk so a chat can be **resumed** in a later process,
not just remembered for the lifetime of one run.

Storage layout (under the project's `.agent/`, where the audit log already
lives):

    .agent/sessions/<session_id>.json

The file is a small JSON envelope:

    {
      "id": "20260531-185530-ab12cd",
      "created_at": "2026-05-31T18:55:30+00:00",
      "updated_at": "2026-05-31T18:57:01+00:00",
      "messages": [ {role, content, meta}, ... ]   # ContextManager.to_list()
    }

Design choices (consistent with PyClaw principles):
  - fail loudly on a corrupt/unreadable session file (#6) — never silently
    start a blank chat when the user asked to resume a specific id.
  - atomic-ish save (write temp + replace) so a crash mid-write can't truncate
    an existing session.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pyclaw.config import SETTINGS
from pyclaw.runtime.context import ContextManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """Sortable, collision-resistant id: <UTC timestamp>-<random>."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


@dataclass
class SessionStore:
    """Loads/saves chat sessions as JSON under `<agent_dir>/sessions/`."""

    root: Path = field(default_factory=lambda: SETTINGS.agent_dir / "sessions")

    # -- paths ----------------------------------------------------------------
    def _path(self, session_id: str) -> Path:
        # Guard against path traversal in a user-supplied --resume id.
        if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
            raise ValueError(f"invalid session id: {session_id!r}")
        return self.root / f"{session_id}.json"

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).is_file()

    def list_ids(self) -> list[str]:
        """All known session ids, newest first (ids are timestamp-prefixed)."""
        if not self.root.is_dir():
            return []
        ids = [p.stem for p in self.root.glob("*.json")]
        return sorted(ids, reverse=True)

    # -- create / load / save -------------------------------------------------
    def create(self) -> str:
        """Allocate a fresh session id (file is written on first save)."""
        return _new_id()

    def load_into(self, session_id: str, context: ContextManager) -> dict:
        """Load a saved session's messages into `context`. Fails loudly.

        Returns the envelope metadata (id, created_at, updated_at).
        """
        path = self._path(session_id)
        if not path.is_file():
            raise FileNotFoundError(f"session {session_id!r} not found at {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:  # fail loudly (#6)
            raise RuntimeError(f"cannot read session {session_id!r}: {exc}") from exc
        context.load_messages(list(data.get("messages", [])))
        return {
            "id": data.get("id", session_id),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    def save(self, session_id: str, context: ContextManager) -> Path:
        """Persist `context` for `session_id` (atomic write). Returns the path."""
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        created_at = _now_iso()
        if path.is_file():
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
                created_at = prev.get("created_at", created_at)
            except (json.JSONDecodeError, OSError):
                pass  # keep the fresh created_at if the old file is unreadable

        envelope = {
            "id": session_id,
            "created_at": created_at,
            "updated_at": _now_iso(),
            "messages": context.to_list(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same filesystem
        return path
