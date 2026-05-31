"""Tests for session persistence (the 'persistence' stage of the agent loop)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.runtime.context import ContextManager, Message, Role
from pyclaw.runtime.session import SessionStore


def _ctx(*pairs: tuple[Role, str]) -> ContextManager:
    c = ContextManager()
    for role, content in pairs:
        c.append(Message(role=role, content=content))
    return c


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    sid = store.create()
    ctx = _ctx((Role.SYSTEM, "sys"), (Role.USER, "hi"), (Role.ASSISTANT, "hello"))
    path = store.save(sid, ctx)
    assert path.is_file()

    # Fresh context, load the saved session back in.
    restored = ContextManager()
    meta = store.load_into(sid, restored)
    assert meta["id"] == sid
    roles = [(m.role, m.content) for m in restored.messages]
    assert roles == [(Role.SYSTEM, "sys"), (Role.USER, "hi"), (Role.ASSISTANT, "hello")]


def test_meta_survives_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    sid = store.create()
    ctx = ContextManager()
    ctx.append(Message(role=Role.TOOL, content="result",
                       meta={"tool": "db_x", "tool_call_id": "c1"}))
    store.save(sid, ctx)

    restored = ContextManager()
    store.load_into(sid, restored)
    m = restored.messages[0]
    assert m.role is Role.TOOL
    assert m.meta["tool"] == "db_x"
    assert m.meta["tool_call_id"] == "c1"


def test_created_at_preserved_across_saves(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    sid = store.create()
    ctx = _ctx((Role.USER, "one"))
    store.save(sid, ctx)
    first = store.load_into(sid, ContextManager())["created_at"]

    ctx.append(Message(role=Role.USER, content="two"))
    store.save(sid, ctx)
    second = store.load_into(sid, ContextManager())
    assert second["created_at"] == first          # created_at is stable
    assert second["updated_at"] >= first           # updated_at moves forward


def test_load_missing_session_fails_loudly(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    with pytest.raises(FileNotFoundError):
        store.load_into("does-not-exist", ContextManager())


def test_corrupt_session_fails_loudly(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    store.root.mkdir(parents=True)
    (store.root / "bad.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        store.load_into("bad", ContextManager())


def test_path_traversal_rejected(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    for bad in ["../escape", "a/b", ".."]:
        with pytest.raises(ValueError):
            store.exists(bad)


def test_list_ids_newest_first(tmp_path: Path) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    for sid in ["20260101-000000-aaa", "20260201-000000-bbb"]:
        store.save(sid, _ctx((Role.USER, "x")))
    assert store.list_ids() == ["20260201-000000-bbb", "20260101-000000-aaa"]
