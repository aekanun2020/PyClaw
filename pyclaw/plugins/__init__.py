"""Layer 5 — Plugins (package for reuse, principle #5).

A plugin bundles skills + hooks + agents + MCP config so a team can install a
capability as one versioned unit. EliteClaw had nothing here (🔴 -> 🟢).

A plugin directory contains:
  plugin.yaml       : manifest (name, version, provides: skills/hooks/agents)
  permissions.yaml  : allowed_tools / blocked_tools for this plugin
  skills/  hooks/  agents/ : the bundled assets

On load, PyClaw registers the plugin's hooks into the HookEngine and its skills
into the SkillRegistry, and applies its permissions to the tool layer.
"""

from pyclaw.plugins.loader import PluginLoader, PluginManifest  # noqa: F401
from pyclaw.plugins.permissions import PermissionPolicy  # noqa: F401
