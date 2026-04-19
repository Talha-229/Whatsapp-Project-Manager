"""Run 10 canned user messages through run_agent (one thread per case)."""

from __future__ import annotations

import os
import sys

# Project root = parent of scripts/
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.orchestrator_graph import compile_orchestrator
from app.config import get_settings
from app.db.checkpoint import init_checkpoint_pool
from app.agents.graph import run_agent


CASES: list[tuple[str, str]] = [
    ("01-hi", "Hi - what can you help me with?"),
    ("02-policy-remote", "What does our company say about remote work?"),
    ("03-calendar-connected", "Is my Google Calendar connected?"),
    ("04-list-events", "What meetings do I have coming up in the next week?"),
    ("05-reminder-lead", "Remind me 25 minutes before each meeting from now on."),
    ("06-thanks", "Thanks, that helps."),
    ("07-vacation", "How do vacation days and carryover work here?"),
    ("08-schedule", "Schedule a 45-minute meeting titled Sprint Review next Tuesday at 2pm UTC."),
    ("09-oauth", "I need to connect Google Calendar - send me the link."),
    ("10-disconnect", "disconnect google"),
]


def main() -> None:
    s = get_settings()
    if not (s.openai_api_key or "").strip():
        print("ERROR: OPENAI_API_KEY is not set in environment / .env")
        sys.exit(1)

    if (s.database_url or "").strip():
        cp = init_checkpoint_pool(s.database_url.strip())
        print("Checkpointer: Postgres")
    else:
        cp = InMemorySaver()
        print("Checkpointer: InMemorySaver (no DATABASE_URL)")

    compile_orchestrator(cp)

    for wa_suffix, text in CASES:
        wa_id = f"smoke-{wa_suffix}"
        print("\n" + "=" * 72)
        print(f"thread_id={wa_id}")
        print(f"USER: {text}")
        print("-" * 72)
        try:
            reply = run_agent(wa_id, text)
            print(f"ASSISTANT: {reply}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
