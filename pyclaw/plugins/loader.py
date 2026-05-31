"""Layer 5 — Plugin loader: parse plugin.yaml, register assets, apply permissions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pyclaw.plugins.permissions import PermissionPolicy


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


@dataclass
class PluginLoader:
    plugins_root: Path

    def discover(self) -> list[PluginManifest]:
        """Find plugin.yaml files and parse manifests.

        TODO:
          - glob '*/plugin.yaml' under plugins_root
          - yaml.safe_load each; build PluginManifest
          - version/semver validation; fail loudly on bad manifest (principle #6)
        """
        raise NotImplementedError("PluginLoader.discover (scaffold)")

    def load(self, manifest: PluginManifest) -> PermissionPolicy:
        """Register a plugin's hooks/skills/agents and return its PermissionPolicy.

        TODO:
          - read manifest.path/permissions.yaml -> PermissionPolicy.from_yaml
          - register hooks into HookEngine, skills into SkillRegistry,
            agents into the agent registry
          - check `requires` against installed plugin versions (semver)
          - return the PermissionPolicy for the core loop to merge
        """
        raise NotImplementedError("PluginLoader.load (scaffold)")
