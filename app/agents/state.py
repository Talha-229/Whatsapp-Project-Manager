"""Orchestrator graph state: messages + rolling summary + scheduling draft."""

from typing import Any

from langgraph.graph import MessagesState
from langgraph.managed import RemainingSteps
from typing_extensions import NotRequired


class OrchestratorState(MessagesState, total=False):
    """LangGraph state persisted via Postgres checkpointer (thread_id = WhatsApp id)."""

    remaining_steps: RemainingSteps
    conversation_summary: NotRequired[str]
    pending_draft: NotRequired[dict[str, Any]]
