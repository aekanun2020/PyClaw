"""Tests for Layer 1 MemoryLoader: directory walk, scope order, @import, caps."""
from __future__ import annotations

import pytest

from pyclaw.memory.loader import (
    AUTO_MEMORY_MAX_LINES,
    MAX_IMPORT_HOPS,
    MemoryLoader,
)


def test_directory_walk_global_then_local(tmp_path):
    # root/AGENT_MEMORY.md (global) and root/sub/AGENT_MEMORY.md (local)
    (tmp_path / "AGENT_MEMORY.md").write_text("GLOBAL_RULE", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENT_MEMORY.md").write_text("LOCAL_RULE", encoding="utf-8")

    bundle = MemoryLoader(root=tmp_path).load(start=sub)
    # both present, global first (farther) then local (nearer)
    assert "GLOBAL_RULE" in bundle.text and "LOCAL_RULE" in bundle.text
    assert bundle.text.index("GLOBAL_RULE") < bundle.text.index("LOCAL_RULE")
    assert len(bundle.sources) == 2


def test_claude_md_alias_is_recognised(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("FROM_CLAUDE_MD", encoding="utf-8")
    bundle = MemoryLoader(root=tmp_path).load(start=tmp_path)
    assert "FROM_CLAUDE_MD" in bundle.text


def test_import_expansion(tmp_path):
    (tmp_path / "part.md").write_text("IMPORTED_CONTENT", encoding="utf-8")
    (tmp_path / "AGENT_MEMORY.md").write_text("HEAD\n@./part.md\nTAIL", encoding="utf-8")
    bundle = MemoryLoader(root=tmp_path).load(start=tmp_path)
    assert "IMPORTED_CONTENT" in bundle.text
    assert "@./part.md" not in bundle.text  # expanded, not left raw


def test_import_cycle_guarded(tmp_path):
    # a.md imports b.md imports a.md -> must not loop forever
    (tmp_path / "a.md").write_text("A\n@./b.md", encoding="utf-8")
    (tmp_path / "b.md").write_text("B\n@./a.md", encoding="utf-8")
    (tmp_path / "AGENT_MEMORY.md").write_text("@./a.md", encoding="utf-8")
    bundle = MemoryLoader(root=tmp_path).load(start=tmp_path)
    assert "A" in bundle.text and "B" in bundle.text
    assert "cycle skipped" in bundle.text


def test_import_depth_limit_fails_loudly(tmp_path):
    # chain longer than MAX_IMPORT_HOPS must raise
    for i in range(MAX_IMPORT_HOPS + 3):
        nxt = f"@./f{i+1}.md" if i < MAX_IMPORT_HOPS + 2 else "END"
        (tmp_path / f"f{i}.md").write_text(f"L{i}\n{nxt}", encoding="utf-8")
    (tmp_path / "AGENT_MEMORY.md").write_text("@./f0.md", encoding="utf-8")
    with pytest.raises(RuntimeError):
        MemoryLoader(root=tmp_path).load(start=tmp_path)


def test_auto_memory_line_cap(tmp_path):
    (tmp_path / "AGENT_MEMORY.md").write_text("HEAD", encoding="utf-8")
    big = "\n".join(f"line{i}" for i in range(AUTO_MEMORY_MAX_LINES + 50))
    (tmp_path / "AUTO_MEMORY.md").write_text(big, encoding="utf-8")
    bundle = MemoryLoader(root=tmp_path).load(start=tmp_path)
    # only first 200 auto lines kept -> line249 must be gone
    assert "line0" in bundle.text
    assert f"line{AUTO_MEMORY_MAX_LINES + 49}" not in bundle.text


def test_append_auto_memory_fifo_trim(tmp_path):
    auto = tmp_path / "AUTO_MEMORY.md"
    loader = MemoryLoader(root=tmp_path)
    for i in range(AUTO_MEMORY_MAX_LINES + 20):
        loader.append_auto_memory(auto, f"note{i}")
    lines = auto.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= AUTO_MEMORY_MAX_LINES
    # oldest trimmed, newest kept
    assert "note0" not in lines
    assert f"note{AUTO_MEMORY_MAX_LINES + 19}" in lines
