"""Public entry: WhatsApp agent (orchestrator + optional fast paths)."""

import logging

from app.agents.orchestrator_graph import invoke_orchestrator
from app.services.agent_context import disconnect_google_and_clear_context, is_disconnect_request

logger = logging.getLogger(__name__)


def run_agent(user_wa_id: str, raw_text: str) -> str:
    """One user message in -> assistant reply out (Postgres-backed thread per WhatsApp id)."""
    if is_disconnect_request(raw_text):
        return disconnect_google_and_clear_context(user_wa_id)
    try:
        return invoke_orchestrator(user_wa_id, raw_text)
    except Exception as e:
        logger.exception("Orchestrator failed: %s", e)
        return f"Something went wrong: {e!s}"[:1000]
