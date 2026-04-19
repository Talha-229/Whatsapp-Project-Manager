"""User preference columns (reminder lead, etc.)."""

from app.db.supabase_client import get_supabase
from app.services.agent_context import ensure_user_row_for_wa
from app.services.users_resolve import normalize_wa_id


def get_meeting_reminder_lead_minutes(wa_id: str) -> int:
    wid = normalize_wa_id(wa_id)
    sb = get_supabase()
    r = sb.table("users").select("meeting_reminder_lead_minutes").eq("whatsapp_number", wid).limit(1).execute()
    if not r.data:
        return 15
    v = r.data[0].get("meeting_reminder_lead_minutes")
    return int(v) if v is not None else 15


def set_meeting_reminder_lead_minutes(wa_id: str, minutes: int) -> None:
    ensure_user_row_for_wa(wa_id)
    wid = normalize_wa_id(wa_id)
    sb = get_supabase()
    sb.table("users").update({"meeting_reminder_lead_minutes": minutes}).eq("whatsapp_number", wid).execute()
