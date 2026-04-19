"""ReAct orchestrator: summarize (pre-hook) + LLM + ToolNode loop."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.agents.context import reset_wa_id, set_wa_id
from app.agents.state import OrchestratorState
from app.agents.summarize import pre_model_summarize
from app.agents.tools import ALL_TOOLS
from app.config import get_settings
from app.services.users_resolve import normalize_wa_id
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = """You are a helpful WhatsApp assistant for workplace scheduling and HR policies.

Use tools when appropriate:
- Company policies / HR questions -> search_company_policies
- Calendar: check connection first if needed (check_google_calendar_connected). If not connected, use get_google_oauth_link and tell the user to open the link.
- Booking with named attendees: use lookup_google_contacts_for_attendees with the names (comma-separated) to pull emails from their Google Contacts, confirm the right person with the user, then preview_calendar_meeting with full details, then only after the user agrees create_calendar_meeting with user_confirmed=True and the same fields.
- Creating meetings: always call preview_calendar_meeting first; never call create_calendar_meeting until the user has confirmed the preview. create_calendar_meeting requires user_confirmed=True.
- Listing upcoming meetings -> list_my_calendar_events
- Changing reminder timing -> set_meeting_reminder_lead_minutes
- User wants to disconnect Google -> disconnect_google_account
- Tasks: create_my_task only for **new** tasks. To **change due date, rename, or edit** an existing task -> update_my_task (task_title match, optional new_due_date YYYY-MM-DD or "clear", optional new_title, optional new_notes). list_my_tasks (scope: open, today, week, overdue, all). complete_my_task (task_title).
- Projects: create_my_project (name, optional deadline YYYY-MM-DD, optional status). list_my_projects (scope: active, all, on_hold).

If no tool is needed, reply concisely in plain text.

For the current calendar date, weekday, or time of day, use only the "Current date and time" line below — do not rely on model training or memory for "today"."""


def _authoritative_time_block() -> str:
    """Wall-clock context for the model (avoids wrong 'today' from cutoff knowledge)."""
    s = get_settings()
    try:
        tz = ZoneInfo(s.default_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    return (
        "Current date and time (authoritative): "
        f"{now.strftime('%A, %B %d, %Y')} — {now.strftime('%H:%M')} "
        f"({s.default_tz}); ISO {now.isoformat(timespec='seconds')}."
    )


def _orchestrator_prompt(state: OrchestratorState | dict[str, Any]) -> list[Any]:
    """System prompt + fresh clock each model call (LangGraph prompt callable)."""
    if isinstance(state, dict):
        msgs = list(state.get("messages") or [])
    else:
        msgs = list(getattr(state, "messages", None) or [])
    body = ORCHESTRATOR_SYSTEM_PROMPT + "\n\n" + _authoritative_time_block()
    return [SystemMessage(content=body)] + msgs

_compiled = None


def build_orchestrator(checkpointer: BaseCheckpointSaver):
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for the orchestrator")

    model = ChatOpenAI(
        api_key=s.openai_api_key,
        model=s.openai_model,
        temperature=0.2,
    )
    return create_react_agent(
        model,
        ALL_TOOLS,
        prompt=_orchestrator_prompt,
        state_schema=OrchestratorState,
        pre_model_hook=pre_model_summarize,
        checkpointer=checkpointer,
        version="v1",
    )


def get_compiled_orchestrator() -> Any:
    global _compiled
    if _compiled is None:
        raise RuntimeError("Orchestrator not compiled; call compile_orchestrator at startup")
    return _compiled


def compile_orchestrator(checkpointer: BaseCheckpointSaver) -> Any:
    global _compiled
    _compiled = build_orchestrator(checkpointer)
    return _compiled


def invoke_orchestrator(user_wa_id: str, raw_text: str) -> str:
    """Run one user turn (appends HumanMessage, returns last AI text)."""
    wid = normalize_wa_id(user_wa_id)
    graph = get_compiled_orchestrator()
    cfg: dict[str, Any] = {"configurable": {"thread_id": wid}}
    tok = set_wa_id(user_wa_id)
    try:
        out = graph.invoke(
            {"messages": [HumanMessage(content=raw_text.strip())]},
            cfg,
        )
    finally:
        reset_wa_id(tok)

    msgs = out.get("messages") or []
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and not m.tool_calls:
            c = m.content
            if isinstance(c, str) and c.strip():
                return c.strip()
            if c:
                return str(c).strip()
    return "OK."
