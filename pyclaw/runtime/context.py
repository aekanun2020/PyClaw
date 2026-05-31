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
        """TODO: real tokenizer; placeholder ~4 chars/token heuristic."""
        raise NotImplementedError("ContextManager.estimate_tokens (scaffold)")

    def maybe_compact(self) -> bool:
        """Compact if over budget. Returns True if compaction happened.

        TODO:
          - if estimate_tokens() <= token_budget: return False
          - new = self.strategy.compact(self._messages, self.token_budget)
          - swap in `new`, fire PostCompaction hook, return True
        """
        raise NotImplementedError("ContextManager.maybe_compact (scaffold)")
