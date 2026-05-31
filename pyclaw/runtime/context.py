"""Layer 0 — Context window management.

Keeps the conversation within the model's context budget using three
strategies from the ADK Spec:
  - Summarization : replace old turns with a compact summary
  - Retrieval     : pull only the relevant slices back in on demand
  - Compaction    : drop/merge low-value turns (e.g. resolved tool noise)

EliteClaw simply appended to `conversationHistory[]` and relied on
`maxToolRounds` to bound length. PyClaw manages context explicitly.

The `PostCompaction` hook (Layer 3) fires after compaction so plugins can
re-inject anything they need preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: Role
    content: str
    # opaque metadata: tool_call_id, token estimate, pinned flag, etc.
    meta: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable form (for session persistence)."""
        return {"role": self.role.value, "content": self.content, "meta": self.meta}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Message":
        return cls(
            role=Role(data["role"]),
            content=str(data.get("content", "")),
            meta=dict(data.get("meta") or {}),
        )


class CompactionStrategy(Protocol):
    """Pluggable strategy. Returns the new (possibly shorter) message list."""

    def compact(self, messages: list[Message], token_budget: int) -> list[Message]: ...


@dataclass
class ContextManager:
    """Owns the conversation history and enforces a token budget."""

    token_budget: int = 128_000
    strategy: CompactionStrategy | None = None
    _messages: list[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        self._messages.append(message)

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def estimate_tokens(self) -> int:
        """Cheap heuristic: ~4 characters per token across all message content.

        Good enough to decide *when* to compact; swap in a real tokenizer later
        without changing callers.
        """
        chars = sum(len(m.content) for m in self._messages)
        return (chars + 3) // 4

    def maybe_compact(self) -> bool:
        """Compact if over budget. Returns True if compaction happened.

        With no strategy configured this is a safe no-op (returns False), so the
        agent loop can call it every round unconditionally. The loop fires the
        PostCompaction hook when this returns True.
        """
        if self.estimate_tokens() <= self.token_budget:
            return False
        if self.strategy is None:
            return False
        new = self.strategy.compact(self._messages, self.token_budget)
        self._messages = list(new)
        return True

    # -- persistence ----------------------------------------------------------
    def to_list(self) -> list[dict[str, object]]:
        """Serialise the whole history to a list of JSON-able dicts."""
        return [m.to_dict() for m in self._messages]

    def load_messages(self, items: list[dict[str, object]]) -> None:
        """Replace the history from a previously-serialised list (resume)."""
        self._messages = [Message.from_dict(d) for d in items]
