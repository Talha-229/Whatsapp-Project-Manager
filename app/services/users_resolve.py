import logging
import re
from typing import Iterable

from app.db.supabase_client import get_supabase
from app.services.google_contacts import resolve_names_to_emails, search_contact_candidates
from app.services.google_credentials import ensure_fresh_credentials, get_google_credentials_for_wa

logger = logging.getLogger(__name__)


def normalize_wa_id(wa_id: str) -> str:
    return re.sub(r"\D", "", wa_id or "")


def find_emails_for_names(wa_id: str, names: Iterable[str]) -> dict[str, str | None]:
    """
    Map attendee first names / display names to emails using Google People API
    (connections). Requires the WhatsApp user to have completed Google OAuth.

    Does not use seeded Supabase rows; resolution is live against Google Contacts.
    """
    names_list = [n.strip() for n in names if n and n.strip()]
    out: dict[str, str | None] = {n: None for n in names_list}
    if not names_list:
        return out

    creds = get_google_credentials_for_wa(wa_id)
    if not creds:
        return out

    try:
        creds = ensure_fresh_credentials(creds)
        resolved = resolve_names_to_emails(creds, names_list)
        for k, v in resolved.items():
            if k in out:
                out[k] = v
    except Exception as e:
        logger.exception("Google Contacts resolution failed: %s", e)

    return out


def lookup_contact_candidates_for_wa(wa_id: str, queries: list[str]) -> dict:
    """
    Search Google Contacts for each query string; returns structured candidates
    for user confirmation before scheduling.
    """
    queries = [q.strip() for q in queries if q and q.strip()]
    if not queries:
        return {"ok": False, "error": "no_search_terms", "message": "No names or search terms were provided."}

    creds = get_google_credentials_for_wa(wa_id)
    if not creds:
        return {"ok": False, "error": "not_connected", "message": "Google account not connected."}

    try:
        creds = ensure_fresh_credentials(creds)
        matches = search_contact_candidates(creds, queries)
    except Exception as e:
        logger.exception("lookup_contact_candidates_for_wa failed: %s", e)
        return {"ok": False, "error": "api_error", "message": str(e)[:500]}

    return {"ok": True, "matches": matches}


def get_user_row_by_wa(wa_id: str) -> dict | None:
    sb = get_supabase()
    wid = normalize_wa_id(wa_id)
    r = sb.table("users").select("*").eq("whatsapp_number", wid).limit(1).execute()
    return r.data[0] if r.data else None
