"""Settings must honour EliteClaw/OpenClaw .env conventions.

SETTINGS resolves env vars at import, so each case runs in a fresh subprocess
with a controlled environment.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def _resolve(env_extra: dict[str, str], dotenv_body: str | None, tmp_path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin"}
    if dotenv_body is not None:
        p = tmp_path / ".env"
        p.write_text(textwrap.dedent(dotenv_body), encoding="utf-8")
        env["PYCLAW_DOTENV"] = str(p)
    env.update(env_extra)
    code = (
        "from pyclaw.config import SETTINGS;"
        "print(SETTINGS.openrouter_api_key);"
        "print(SETTINGS.openrouter_base_url);"
        "print(SETTINGS.default_model)"
    )
    out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True, cwd=str(tmp_path))
    # keep empty lines (api_key may be ""); drop only the trailing newline
    key, base, model = out.rstrip("\n").split("\n")
    return {"api_key": key, "base_url": base, "model": model}


def test_openrouter_model_env_is_used(tmp_path):
    # EliteClaw/OpenClaw .env uses OPENROUTER_MODEL — must be respected.
    r = _resolve({"OPENROUTER_MODEL": "qwen3.5:35b"}, None, tmp_path)
    assert r["model"] == "qwen3.5:35b"


def test_pyclaw_default_model_overrides_openrouter_model(tmp_path):
    r = _resolve(
        {"OPENROUTER_MODEL": "qwen3.5:35b", "PYCLAW_DEFAULT_MODEL": "openai/gpt-oss-120b"},
        None,
        tmp_path,
    )
    assert r["model"] == "openai/gpt-oss-120b"


def test_pyclaw_dotenv_loads_llm_settings(tmp_path):
    # Pointing PYCLAW_DOTENV at an OpenClaw .env brings over OPENROUTER_* too.
    body = """
        OPENROUTER_API_KEY=ollama
        OPENROUTER_BASE_URL=http://10.211.55.2:11434/v1
        OPENROUTER_MODEL=qwen3.5:35b-a3b-coding-nvfp4
    """
    r = _resolve({}, body, tmp_path)
    assert r["api_key"] == "ollama"
    assert r["base_url"] == "http://10.211.55.2:11434/v1"
    assert r["model"] == "qwen3.5:35b-a3b-coding-nvfp4"


def test_hosted_default_when_nothing_set(tmp_path):
    r = _resolve({}, None, tmp_path)
    assert r["model"] == "anthropic/claude-3.7-sonnet"
    assert r["base_url"] == "https://openrouter.ai/api/v1"
