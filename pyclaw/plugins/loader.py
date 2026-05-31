"""Layer 5 — Plugin loader: parse plugin.yaml, register assets, apply permissions.

A plugin is a directory under `plugins_root` containing a `plugin.yaml` manifest
and, optionally:
  - permissions.yaml          -> PermissionPolicy (Layer 5)
  - hooks declared in manifest -> registered into a HookEngine (Layer 3)
  - skills/**/SKILL.md         -> scanned into a SkillRegistry (Layer 2)

Everything a plugin contributes therefore flows through the same deterministic
chokepoints as everything else (principle #1): its tools obey the permission
policy, and its hooks fire from the core loop.

plugin.yaml shape (all but name/version optional)::

    name: pdpa-guard
    version: 1.2.0
    requires:
      core: ">=0.1.0"
    skills_dir: skills            # relative; defaults to "skills"
    hooks:
      - name: block_secrets
        event: PreToolUse
        runner: python            # bash | python | http | llm
        target: pdpa_guard.hooks:block_secrets
        priority: 10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyclaw.hooks import HookEngine
from pyclaw.hooks.engine import HookSpec
from pyclaw.hooks.events import HookEvent
from pyclaw.hooks.runners import RunnerType
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.skills.registry import SkillRegistry


@dataclass
class PluginManifest:
    """Parsed plugin.yaml."""

    name: str
    version: str
    path: Path
    provides_skills: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    provides_agents: list[str] = field(default_factory=list)
    requires: dict[str, str] = field(default_factory=dict)  # name -> semver constraint
    # Raw hook declarations (turned into HookSpecs at load() time).
    hook_specs: list[dict[str, Any]] = field(default_factory=list)
    skills_dir: str = "skills"


def _parse_version(v: str) -> tuple[int, ...]:
    """Lenient numeric-tuple parse of a dotted version (e.g. '1.2.0' -> (1,2,0))."""
    parts: list[int] = []
    for chunk in str(v).split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _satisfies(installed: str, constraint: str) -> bool:
    """Tiny semver check supporting '>=', '>', '<=', '<', '==' and a bare version (==)."""
    constraint = constraint.strip()
    for op in (">=", "<=", "==", ">", "<"):
        if constraint.startswith(op):
            want = _parse_version(constraint[len(op):].strip())
            have = _parse_version(installed)
            if op == ">=":
                return have >= want
            if op == "<=":
                return have <= want
            if op == ">":
                return have > want
            if op == "<":
                return have < want
            return have == want
    # bare version means exact match
    return _parse_version(installed) == _parse_version(constraint)


@dataclass
class PluginLoader:
    plugins_root: Path
    # Versions of things a plugin may declare in `requires`. The runtime can
    # pass {"core": "0.1.0", <plugin>: <version>, ...} so cross-plugin
    # dependencies are checked deterministically.
    installed_versions: dict[str, str] = field(default_factory=dict)

    def discover(self) -> list[PluginManifest]:
        """Find `*/plugin.yaml` files under `plugins_root` and parse manifests.

        Returns manifests sorted by name for deterministic load order. A bad
        manifest (missing name/version, or unparseable YAML) fails loudly
        (principle #6) — we never silently skip a broken plugin.
        """
        import yaml

        if not self.plugins_root.is_dir():
            return []

        manifests: list[PluginManifest] = []
        for manifest_path in sorted(self.plugins_root.glob("*/plugin.yaml")):
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ValueError(f"{manifest_path}: plugin.yaml must be a mapping")

            name = raw.get("name")
            version = raw.get("version")
            if not name or not version:
                raise ValueError(
                    f"{manifest_path}: plugin.yaml requires both 'name' and 'version' "
                    "(fail loudly, principle #6)"
                )

            hooks = raw.get("hooks", []) or []
            if not isinstance(hooks, list):
                raise ValueError(f"{manifest_path}: 'hooks' must be a list")

            manifests.append(
                PluginManifest(
                    name=str(name),
                    version=str(version),
                    path=manifest_path.parent,
                    provides_skills=list(raw.get("provides_skills", []) or []),
                    provides_hooks=[h.get("name", "") for h in hooks if isinstance(h, dict)],
                    provides_agents=list(raw.get("provides_agents", []) or []),
                    requires=dict(raw.get("requires", {}) or {}),
                    hook_specs=[h for h in hooks if isinstance(h, dict)],
                    skills_dir=str(raw.get("skills_dir", "skills")),
                )
            )
        return manifests

    def load(
        self,
        manifest: PluginManifest,
        *,
        hooks: HookEngine | None = None,
        skills: SkillRegistry | None = None,
    ) -> PermissionPolicy:
        """Register a plugin's hooks/skills and return its PermissionPolicy.

        Steps (all deterministic):
          1. check `requires` against `installed_versions` (semver); fail loudly
          2. read permissions.yaml -> PermissionPolicy
          3. register each declared hook into `hooks` (if provided)
          4. scan the plugin's skills dir into `skills` (if provided)
          5. return the PermissionPolicy for the core loop to merge
        """
        # 1. Dependency check (fail loudly if a requirement is unmet).
        for dep, constraint in manifest.requires.items():
            have = self.installed_versions.get(dep)
            if have is None:
                raise RuntimeError(
                    f"plugin {manifest.name!r} requires {dep!r} ({constraint}) "
                    "which is not installed (fail loudly, principle #6)"
                )
            if not _satisfies(have, constraint):
                raise RuntimeError(
                    f"plugin {manifest.name!r} requires {dep} {constraint}, "
                    f"but {have} is installed"
                )

        # 2. Permissions (Layer 5).
        policy = PermissionPolicy.from_yaml(manifest.path / "permissions.yaml")

        # 3. Hooks (Layer 3) — each becomes a deterministic HookSpec.
        if hooks is not None:
            for h in manifest.hook_specs:
                hooks.register(
                    HookSpec(
                        name=h.get("name") or f"{manifest.name}:hook",
                        event=HookEvent(h["event"]),
                        runner=RunnerType(h.get("runner", "python")),
                        target=h["target"],
                        priority=int(h.get("priority", 100)),
                        enabled=bool(h.get("enabled", True)),
                    )
                )

        # 4. Skills (Layer 2) — lazy frontmatter scan only.
        if skills is not None:
            skills_dir = manifest.path / manifest.skills_dir
            if skills_dir.is_dir():
                skills.scan(skills_dir)

        # 5. Hand the policy back so the loop can merge it (blocked wins).
        return policy

    def load_all(
        self,
        *,
        hooks: HookEngine | None = None,
        skills: SkillRegistry | None = None,
    ) -> PermissionPolicy:
        """Discover and load every plugin, returning one merged PermissionPolicy."""
        merged = PermissionPolicy()
        for manifest in self.discover():
            merged = merged.merge(self.load(manifest, hooks=hooks, skills=skills))
        return merged
