"""PyClaw CLI entrypoint.

Usage:
    pyclaw run "your task here"

Wires up every layer with defaults discovered from `.agent/`, then runs the
AgentLoop.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyclaw", description="PyClaw agent runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a task through the agent loop")
    run_p.add_argument("task", help="the task / user request")

    sub.add_parser("doctor", help="check config, .agent layout, and layer wiring")

    args = parser.parse_args(argv)

    if args.command == "run":
        # TODO: assemble AgentLoop (llm, hooks, context, audit, hitl,
        # permissions, memory, skills) from config and run args.task
        raise NotImplementedError("cli run: assemble AgentLoop and execute (scaffold)")

    if args.command == "doctor":
        # TODO: validate .agent dir, OPENROUTER_API_KEY, hooks/plugins load
        raise NotImplementedError("cli doctor (scaffold)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
