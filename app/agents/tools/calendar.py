"""Google Calendar–related tools."""

import json

from langchain_core.tools import tool

from app.agents.context import get_wa_id
from app.config import get_settings
from app.oauth.state_token import sign_state
from app.services.calendar_events import list_upcoming_events
from app.services.meeting_scheduler import preview_schedule_from_agent, schedule_from_agent
from app.services.user_prefs import set_meeting_reminder_lead_minutes as persist_reminder_lead_minutes
from app.services.users_resolve import (
    get_user_row_by_wa,
    lookup_contact_candidates_for_wa,
    normalize_wa_id,
)
from app.utils.email_extract import extract_emails


@tool
def check_google_calendar_connected() -> str:
    """Check whether this WhatsApp user has connected Google Calendar. Use before scheduling if unsure."""
    wa = get_wa_id()
    row = get_user_row_by_wa(wa)
    if row and row.get("google_refresh_token_encrypted"):
        return "Google Calendar is connected for this account."
    return "NOT_CONNECTED"


@tool
def get_google_oauth_link() -> str:
    """Get the URL the user must open once to connect Google Calendar. Use when scheduling is requested but Google is not connected."""
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    s = get_settings()
    from urllib.parse import quote

    base = s.public_base_url.rstrip("/")
    st = sign_state(wid)
    link = f"{base}/oauth/google/start?state={quote(st, safe='')}"
    return json.dumps({"needs_oauth": True, "url": link, "message": "Open this link to connect Google Calendar, then message me again."})


@tool
def lookup_google_contacts_for_attendees(search_terms: str) -> str:
    """
    Search this user's Google Contacts for people to invite before booking a meeting.
    Pass comma-separated names or email fragments (e.g. 'Talha', 'sarah@').
    Returns candidate display names and emails so you can confirm with the user before create_calendar_meeting.
    Requires Google Calendar/Contacts connected.
    """
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    terms = [t.strip() for t in (search_terms or "").split(",") if t.strip()]
    if not terms:
        return json.dumps(
            {
                "ok": False,
                "hint": "Pass at least one name or email fragment, comma-separated.",
            },
            ensure_ascii=False,
        )

    row = get_user_row_by_wa(wa)
    if not row or not row.get("google_refresh_token_encrypted"):
        return json.dumps(
            {
                "ok": False,
                "needs_oauth": True,
                "message": "Connect Google first (get_google_oauth_link).",
            },
            ensure_ascii=False,
        )

    result = lookup_contact_candidates_for_wa(wid, terms)
    return json.dumps(result, ensure_ascii=False)


def _parse_meeting_tool_args(
    title: str,
    start_iso: str,
    end_iso: str,
    attendee_names: str,
    attendee_emails: str,
) -> tuple[str, str, str | None, list[str], list[str]]:
    names = [n.strip() for n in attendee_names.split(",") if n.strip()]
    emails = [e.strip() for e in attendee_emails.split(",") if e.strip()]
    if not emails and attendee_emails:
        emails = extract_emails(attendee_emails)
    end = end_iso.strip() or None
    return (
        title.strip() or "Meeting",
        start_iso.strip(),
        end,
        names,
        emails,
    )


@tool
def preview_calendar_meeting(
    title: str,
    start_iso: str,
    end_iso: str = "",
    attendee_names: str = "",
    attendee_emails: str = "",
) -> str:
    """
    Show a summary of the meeting (title, start/end, invitees, reminder) for the user to confirm.
    Does not create any calendar event. Call this before create_calendar_meeting.
    Same parameters as create_calendar_meeting: ISO 8601 datetimes; comma-separated names/emails.
    Times without a timezone are wall-clock in the user's DEFAULT_TZ. A trailing Z means UTC (not local).
    """
    wa = get_wa_id()
    t, start_i, end_i, names, emails = _parse_meeting_tool_args(
        title, start_iso, end_iso, attendee_names, attendee_emails
    )
    return preview_schedule_from_agent(wa, t, start_i, end_i, names, attendee_emails=emails)


@tool
def create_calendar_meeting(
    title: str,
    start_iso: str,
    end_iso: str = "",
    attendee_names: str = "",
    attendee_emails: str = "",
    user_confirmed: bool = False,
) -> str:
    """
    Create a Google Calendar meeting with optional Google Meet after the user confirmed a preview.
    start_iso/end_iso are ISO 8601 datetimes. attendee_names: comma-separated; attendee_emails: comma-separated.
    Set user_confirmed=True only after the user said yes to the same details shown by preview_calendar_meeting.
    Times without a timezone are wall-clock in DEFAULT_TZ; use an explicit offset (e.g. +05:00 for Pakistan) or Z only for true UTC.
    """
    if not user_confirmed:
        return (
            "No calendar event was created. First use preview_calendar_meeting with the same "
            "title, start_iso, end_iso, and attendees, send the user that summary, and only after "
            "they confirm call create_calendar_meeting again with user_confirmed=True."
        )
    wa = get_wa_id()
    t, start_i, end_i, names, emails = _parse_meeting_tool_args(
        title, start_iso, end_iso, attendee_names, attendee_emails
    )
    return schedule_from_agent(wa, t, start_i, end_i, names, attendee_emails=emails)


@tool
def list_my_calendar_events(days: int = 7) -> str:
    """List this user's Google Calendar events from today through the next `days` days (default 7). Requires Google connected."""
    wa = get_wa_id()
    evs = list_upcoming_events(wa, days=min(max(days, 1), 30))
    if not evs:
        row = get_user_row_by_wa(wa)
        if not row or not row.get("google_refresh_token_encrypted"):
            return "Connect Google Calendar first (use get_google_oauth_link if needed)."
        return "No events found in that window."
    lines = [f"- {e.get('summary')} | start: {e.get('start')} | {e.get('html_link') or ''}" for e in evs[:40]]
    return "Upcoming events:\n" + "\n".join(lines)


@tool
def set_meeting_reminder_lead_minutes(minutes: int) -> str:
    """Set how many minutes before each meeting the bot should send a WhatsApp reminder (e.g. 30 or 40). Persists for this user."""
    wa = get_wa_id()
    m = max(1, min(int(minutes), 24 * 60))
    persist_reminder_lead_minutes(wa, m)
    return f"Reminder time saved: I will message you {m} minutes before each meeting."
