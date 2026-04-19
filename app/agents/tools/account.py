"""Account / OAuth disconnect tool."""

from langchain_core.tools import tool

from app.agents.context import get_wa_id
from app.services.agent_context import disconnect_google_and_clear_context


@tool
def disconnect_google_account() -> str:
    """Remove Google Calendar access and clear the saved OAuth token for this WhatsApp number. Use when the user asks to remove, revoke, or disconnect Google."""
    wa = get_wa_id()
    return disconnect_google_and_clear_context(wa)
