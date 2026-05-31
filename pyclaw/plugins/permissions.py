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
        """Parse a permissions.yaml file into a PermissionPolicy.

        Expected shape (both keys optional)::

            allowed_tools: [read_file, write_file]
            blocked_tools: [deploy_to_production]

        A missing file yields an empty (permit-anything-not-blocked) policy so a
        plugin without explicit permissions is still loadable. A malformed file
        (non-mapping, or a list where a list isn't expected) fails loudly
        (principle #6) rather than silently widening access.
        """
        import yaml

        if not path.is_file():
            return cls()

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"{path}: permissions.yaml must be a mapping, got {type(raw).__name__} "
                "(fail loudly, principle #6)"
            )

        def _as_set(key: str) -> frozenset[str]:
            value = raw.get(key, []) or []
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{path}: {key} must be a list, got {type(value).__name__}")
            return frozenset(str(v) for v in value)

        return cls(
            allowed_tools=_as_set("allowed_tools"),
            blocked_tools=_as_set("blocked_tools"),
        )

    def merge(self, other: "PermissionPolicy") -> "PermissionPolicy":
        """Combine two policies (e.g. global + plugin). blocked is unioned."""
        return PermissionPolicy(
            allowed_tools=self.allowed_tools | other.allowed_tools,
            blocked_tools=self.blocked_tools | other.blocked_tools,
        )
