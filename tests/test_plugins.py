"""Tests for Layer 5: PermissionPolicy.from_yaml + PluginLoader (discover/load)."""
from __future__ import annotations

import pytest

from pyclaw.hooks import HookEngine
from pyclaw.hooks.events import HookEvent
from pyclaw.plugins.loader import PluginLoader
from pyclaw.plugins.permissions import PermissionPolicy
from pyclaw.skills.registry import SkillRegistry


# -- PermissionPolicy.from_yaml ----------------------------------------------
def test_permissions_from_yaml(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text("allowed_tools: [read_file, write_file]\nblocked_tools: [deploy_to_production]\n", encoding="utf-8")
    policy = PermissionPolicy.from_yaml(p)
    assert policy.is_allowed("read_file") is True
    assert policy.is_allowed("delete_file") is False       # allowlist mode
    assert policy.is_allowed("deploy_to_production") is False  # blocked wins


def test_permissions_missing_file_is_permissive(tmp_path):
    policy = PermissionPolicy.from_yaml(tmp_path / "nope.yaml")
    assert policy.is_allowed("anything") is True  # no allowlist, nothing blocked


def test_permissions_blocked_only(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text("blocked_tools: [delete_file]\n", encoding="utf-8")
    policy = PermissionPolicy.from_yaml(p)
    assert policy.is_allowed("read_file") is True
    assert policy.is_allowed("delete_file") is False


def test_permissions_malformed_fails_loud(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        PermissionPolicy.from_yaml(p)


# -- PluginLoader -------------------------------------------------------------
def _make_plugin(root, name, *, version="1.0.0", requires=None, hooks=None, perms=None, skill=None):
    pdir = root / name
    pdir.mkdir(parents=True)
    manifest = f"name: {name}\nversion: {version}\n"
    if requires:
        manifest += "requires:\n" + "".join(f"  {k}: \"{v}\"\n" for k, v in requires.items())
    if hooks:
        manifest += "hooks:\n"
        for h in hooks:
            manifest += (
                f"  - name: {h['name']}\n"
                f"    event: {h['event']}\n"
                f"    runner: {h.get('runner','python')}\n"
                f"    target: {h['target']}\n"
                f"    priority: {h.get('priority',100)}\n"
            )
    (pdir / "plugin.yaml").write_text(manifest, encoding="utf-8")
    if perms is not None:
        (pdir / "permissions.yaml").write_text(perms, encoding="utf-8")
    if skill is not None:
        sdir = pdir / "skills" / "demo"
        sdir.mkdir(parents=True)
        (sdir / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: a demo\n---\nbody", encoding="utf-8"
        )
    return pdir


def test_discover_finds_and_sorts(tmp_path):
    _make_plugin(tmp_path, "bbb")
    _make_plugin(tmp_path, "aaa")
    loader = PluginLoader(plugins_root=tmp_path)
    manifests = loader.discover()
    assert [m.name for m in manifests] == ["aaa", "bbb"]  # sorted


def test_discover_missing_root_returns_empty(tmp_path):
    loader = PluginLoader(plugins_root=tmp_path / "nope")
    assert loader.discover() == []


def test_discover_bad_manifest_fails_loud(tmp_path):
    pdir = tmp_path / "broken"
    pdir.mkdir()
    (pdir / "plugin.yaml").write_text("version: 1.0.0\n", encoding="utf-8")  # no name
    with pytest.raises(ValueError):
        PluginLoader(plugins_root=tmp_path).discover()


def test_load_registers_hooks_skills_and_returns_policy(tmp_path):
    _make_plugin(
        tmp_path, "guard",
        hooks=[{"name": "block_x", "event": "PreToolUse", "runner": "python",
                "target": "pyclaw.subagents._test_hooks:deny", "priority": 5}],
        perms="blocked_tools: [delete_file]\n",
        skill=True,
    )
    hooks = HookEngine()
    skills = SkillRegistry()
    loader = PluginLoader(plugins_root=tmp_path, installed_versions={"core": "0.1.0"})
    [manifest] = loader.discover()
    policy = loader.load(manifest, hooks=hooks, skills=skills)

    assert policy.is_allowed("delete_file") is False
    assert len(hooks.hooks_for(HookEvent.PRE_TOOL_USE)) == 1
    assert skills.get("demo-skill") is not None


def test_load_requires_unmet_fails_loud(tmp_path):
    _make_plugin(tmp_path, "needs", requires={"core": ">=9.9.9"})
    loader = PluginLoader(plugins_root=tmp_path, installed_versions={"core": "0.1.0"})
    [manifest] = loader.discover()
    with pytest.raises(RuntimeError):
        loader.load(manifest)


def test_load_all_merges_policies(tmp_path):
    _make_plugin(tmp_path, "p1", perms="blocked_tools: [a]\n")
    _make_plugin(tmp_path, "p2", perms="blocked_tools: [b]\n")
    loader = PluginLoader(plugins_root=tmp_path, installed_versions={"core": "0.1.0"})
    merged = loader.load_all()
    assert merged.is_allowed("a") is False and merged.is_allowed("b") is False
