"""Layer 0 — Observability: append-only audit log.

Every tool invocation is recorded to `.agent/logs/audit.jsonl` (one JSON
object per line). Inputs/outputs are hashed, not stored verbatim, so the log
is safe to keep even when payloads contain PII (PDPA-friendly).

Per the ADK Spec, each record carries:
    timestamp, event, tool, input_hash, output_hash, user

This is deterministic by construction (it always fires from the core loop,
regardless of what the LLM "decides") — an instance of principle #1.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyclaw.config import SETTINGS


def _hash(value: Any) -> str:
    """Stable SHA-256 of a JSON-serialisable value (sorted keys)."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    timestamp: str
    event: str          # e.g. "tool_call", "hook_block", "hitl_approved"
    tool: str
    input_hash: str
    output_hash: str
    user: str

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


class AuditLog:
    """Append-only JSONL writer. Thread-unsafe stub; TODO add a lock."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SETTINGS.audit_log_path

    def record(
        self,
        *,
        event: str,
        tool: str,
        input_payload: Any,
        output_payload: Any,
        user: str = "system",
    ) -> AuditRecord:
        """Append one record to the JSONL log and return it.

        Inputs/outputs are hashed, never stored verbatim (PDPA-friendly). The
        write is append-only and flushed+fsynced so a crash can't lose a record.
        """
        rec = AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event=event,
            tool=tool,
            input_hash=_hash(input_payload),
            output_hash=_hash(output_payload),
            user=user,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(rec.to_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec
