"""Layer 5 — Permission policy (allowed_tools / blocked_tools).

Loaded from a plugin's `permissions.yaml`. The policy is enforced
deterministically: the core loop checks `is_allowed(tool)` before every tool
call (and a PreToolUse hook can also consult it). blocked_tools always wins
(fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PermissionPolicy:
    """allowed_tools acts as an allowlist when non-empty; blocked_tools always denies."""

    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    blocked_tools: frozenset[str] = field(default_factory=frozenset)

    def is_allowed(self, tool: str) -> bool:
        if tool in self.blocked_tools:
            return False
        if self.allowed_tools:  # allowlist mode
            return tool in self.allowed_tools
        return True  # no allowlist -> permit anything not explicitly blocked

    @classmethod
    def from_yaml(cls, path: Path) -> "PermissionPolicy":
        """Parse permissions.yaml.

        TODO:
          - yaml.safe_load(path)
          - return cls(allowed_tools=frozenset(...), blocked_tools=frozenset(...))
        """
        raise NotImplementedError("PermissionPolicy.from_yaml (scaffold)")

    def merge(self, other: "PermissionPolicy") -> "PermissionPolicy":
        """Combine two policies (e.g. global + plugin). blocked is unioned."""
        return PermissionPolicy(
            allowed_tools=self.allowed_tools | other.allowed_tools,
            blocked_tools=self.blocked_tools | other.blocked_tools,
        )
