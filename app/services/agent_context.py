"""Persist short-lived scheduling draft per WhatsApp user (Supabase)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.db.supabase_client import get_supabase
from app.services.users_resolve import normalize_wa_id

logger = logging.getLogger(__name__)


def ensure_user_row_for_wa(wa_id: str) -> None:
    """Insert a minimal users row so we can store agent_context before Google OAuth."""
    wid = normalize_wa_id(wa_id)
    if not wid:
        return
    sb = get_supabase()
    try:
        r = sb.table("users").select("id").eq("whatsapp_number", wid).limit(1).execute()
        if r.data:
            return
        sb.table("users").insert({"name": "WhatsApp User", "whatsapp_number": wid}).execute()
    except Exception as e:
        logger.warning("ensure_user_row_for_wa: %s", e)


def load_agent_context(wa_id: str) -> dict[str, Any]:
    wid = normalize_wa_id(wa_id)
    sb = get_supabase()
    try:
        r = sb.table("users").select("agent_context").eq("whatsapp_number", wid).limit(1).execute()
        if not r.data:
            return {}
        raw = r.data[0].get("agent_context")
        if isinstance(raw, dict):
            return raw
        return {}
    except Exception as e:
        logger.exception("load_agent_context failed: %s", e)
        return {}


def save_agent_context(wa_id: str, context: dict[str, Any]) -> None:
    wid = normalize_wa_id(wa_id)
    sb = get_supabase()
    try:
        sb.table("users").update({"agent_context": context}).eq("whatsapp_number", wid).execute()
    except Exception as e:
        logger.exception("save_agent_context failed: %s", e)


def clear_agent_context(wa_id: str) -> None:
    save_agent_context(wa_id, {})


def save_schedule_draft(
    wa_id: str,
    *,
    meeting_title: str | None,
    start_iso: str | None,
    end_iso: str | None,
    attendee_names: list[str],
    attendee_emails: list[str],
) -> None:
    ensure_user_row_for_wa(wa_id)
    draft: dict[str, Any] = {
        "meeting_title": meeting_title,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "attendee_names": attendee_names,
        "attendee_emails": attendee_emails,
    }
    save_agent_context(wa_id, {"draft": draft})


def disconnect_google_and_clear_context(wa_id: str) -> str:
    """Remove Google refresh token and agent draft for this WhatsApp user."""
    wid = normalize_wa_id(wa_id)
    sb = get_supabase()
    try:
        sb.table("users").update(
            {
                "google_refresh_token_encrypted": None,
                "google_connected_at": None,
                "agent_context": {},
            }
        ).eq("whatsapp_number", wid).execute()
    except Exception as e:
        logger.exception("disconnect_google failed: %s", e)
        return "Could not remove Google access right now. Try again later."
    return (
        "Google access for this WhatsApp number was removed (token cleared). "
        "Scheduling drafts were cleared. Send a new message when you want to connect Google again."
    )


# Disconnect / revoke Google Calendar & OAuth (broad wording; avoid policy-query false positives)
_DISCONNECT_PHRASES = (
    "remove my token",
    "revoke my token",
    "delete my token",
    "disconnect google",
    "remove google",
    "disconnect my google",
    "remove my google",
    "forget my google",
    "log out google",
    "sign out google",
    "unlink google",
    "remove calendar",
    "remove my calendar",
    "disconnect calendar",
    "unlink calendar",
    "revoke calendar",
    "remove calendar access",
    "remove my calendar access",
    "revoke my calendar access",
    "disconnect calendar access",
)
_DISCONNECT_RE = re.compile(
    r"\b(remove|revoke|disconnect|delete|unlink|disable|turn off|clear)\b"
    r"[\s\S]{0,72}?"
    r"\b(google|calendar|calender|gmail|meet|scheduling)\b",
    re.IGNORECASE,
)
_DISCONNECT_RE2 = re.compile(
    r"\b(google|calendar|calender|gmail|meet)\b"
    r"[\s\S]{0,48}?"
    r"\b(access|connection|link|integration|permission)\b"
    r"[\s\S]{0,40}?"
    r"\b(remove|revoke|disconnect|delete|unlink|disable)\b",
    re.IGNORECASE,
)


def is_disconnect_request(text: str) -> bool:
    t = (text or "").lower().strip()
    if not t:
        return False
    if any(p in t for p in _DISCONNECT_PHRASES):
        return True
    # Typo: "calender"
    tn = t.replace("calender", "calendar")
    if any(p in tn for p in _DISCONNECT_PHRASES):
        return True
    if _DISCONNECT_RE.search(t) or _DISCONNECT_RE.search(tn):
        return True
    if _DISCONNECT_RE2.search(t) or _DISCONNECT_RE2.search(tn):
        return True
    return False
