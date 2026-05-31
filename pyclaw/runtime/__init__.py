"""Layer 0 — Runtime.

Responsibilities the agent loop depends on but that are *not* the LLM's job:
  - context.py : context window management (summarization / retrieval / compaction)
  - audit.py   : observability — append-only audit log (.agent/logs/audit.jsonl)
  - hitl.py    : Human-In-The-Loop approval gate for dangerous tools

EliteClaw had only `maxToolRounds`; PyClaw adds the rest (🟡 -> 🟢).
"""
