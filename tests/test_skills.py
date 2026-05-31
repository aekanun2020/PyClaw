"""Tests for Layer 2 — skill registry scan, auto-detect, lazy load, chaining."""

from __future__ import annotations

from pathlib import Path

from pyclaw.skills.loader import SkillLoader
from pyclaw.skills.registry import Invocation, SkillRegistry, parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_SKILLS = REPO_ROOT / ".agent" / "skills"


def _write_skill(root: Path, name: str, *, desc: str, invocation: str = "auto",
                 chains_to: str = "") -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ninvocation: {invocation}\n"
        f"chains_to: {chains_to}\n---\n\n## Body\ninstructions for {name}\n",
        encoding="utf-8",
    )


def test_parse_frontmatter_basic() -> None:
    fields, body = parse_frontmatter("---\nname: x\ndescription: hi # c\n---\n\nBODY\n")
    assert fields == {"name": "x", "description": "hi"}
    assert body.strip() == "BODY"


def test_parse_frontmatter_none() -> None:
    fields, body = parse_frontmatter("no frontmatter here")
    assert fields == {}
    assert body == "no frontmatter here"


def test_scan_and_catalog(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", desc="do alpha things")
    _write_skill(tmp_path, "beta", desc="handle beta")
    reg = SkillRegistry()
    reg.scan(tmp_path)
    assert {m.name for m in reg.all()} == {"alpha", "beta"}
    catalog = reg.build_prompt_catalog()
    assert "alpha: do alpha things" in catalog


def test_scan_missing_dir_is_noop(tmp_path: Path) -> None:
    reg = SkillRegistry()
    reg.scan(tmp_path / "does-not-exist")  # must not raise
    assert reg.all() == []


def test_detect_ranks_by_overlap(tmp_path: Path) -> None:
    _write_skill(tmp_path, "code-review", desc="review a pull request diff for bugs")
    _write_skill(tmp_path, "release-notes", desc="write release notes")
    loader = SkillLoader(SkillRegistry())
    loader.registry.scan(tmp_path)
    hits = loader.detect("please review this pull request diff")
    assert hits[0].name == "code-review"


def test_detect_skips_manual(tmp_path: Path) -> None:
    _write_skill(tmp_path, "danger", desc="manual only deploy", invocation="manual")
    loader = SkillLoader(SkillRegistry())
    loader.registry.scan(tmp_path)
    assert loader.detect("deploy") == []          # manual is never auto-detected
    assert loader.resolve_manual("/danger").name == "danger"  # but reachable manually


def test_load_full_strips_frontmatter(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", desc="x")
    loader = SkillLoader(SkillRegistry())
    loader.registry.scan(tmp_path)
    body = loader.load_full(loader.registry.get("alpha"))
    assert "instructions for alpha" in body
    assert "---" not in body                      # frontmatter removed


def test_expand_chain_cycle_guarded(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a", desc="a", chains_to="b")
    _write_skill(tmp_path, "b", desc="b", chains_to="a")  # cycle a<->b
    loader = SkillLoader(SkillRegistry())
    loader.registry.scan(tmp_path)
    chain = loader.expand_chain(loader.registry.get("a"))
    assert [m.name for m in chain] == ["b"]        # no infinite loop


# --- the shipped code-review skill is well-formed ----------------------------
def test_shipped_code_review_skill() -> None:
    reg = SkillRegistry()
    reg.scan(SHIPPED_SKILLS)
    meta = reg.get("code-review")
    assert meta is not None
    assert meta.invocation is Invocation.AUTO
    assert meta.subagent == "review"
