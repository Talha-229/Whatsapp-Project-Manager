import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from app.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.google_credentials import ensure_fresh_credentials, get_google_credentials_for_wa
from app.services.recall_client import choose_join_at_for_meeting, create_notetaker_bot
from app.services.user_prefs import get_meeting_reminder_lead_minutes
from app.services.users_resolve import find_emails_for_names, get_user_row_by_wa, normalize_wa_id

logger = logging.getLogger(__name__)


@dataclass
class ResolvedMeetingPlan:
    wid: str
    title: str
    start: datetime
    end: datetime
    emails: list[str]
    reminder_lead_minutes: int
    timezone_name: str


def resolve_meeting_plan(
    wa_id: str,
    title: str,
    start_iso: str | None,
    end_iso: str | None,
    attendee_names: list[str] | None,
    attendee_emails: list[str] | None = None,
) -> tuple[ResolvedMeetingPlan | None, str]:
    """
    Validate OAuth, parse times, resolve contacts -> plan or user-facing error string.
    """
    s = get_settings()
    wid = normalize_wa_id(wa_id)
    row = get_user_row_by_wa(wid)
    if not row or not row.get("google_refresh_token_encrypted"):
        from urllib.parse import quote

        from app.oauth.state_token import sign_state

        base = s.public_base_url.rstrip("/")
        st = sign_state(wid)
        link = f"{base}/oauth/google/start?state={quote(st, safe='')}"
        return (
            None,
            "To schedule meetings I need Google Calendar access.\n"
            f"Open this link once to connect: {link}",
        )

    if not start_iso:
        return (None, "When should the meeting start? (e.g. tomorrow 3pm)")

    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except Exception:
        return (None, "I could not understand the date/time. Please try e.g. 2026-04-20T15:00:00+03:00")

    if end_iso:
        try:
            end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        except Exception:
            end = start + timedelta(hours=1)
    else:
        end = start + timedelta(hours=1)

    explicit = list(
        dict.fromkeys([e.strip().lower() for e in (attendee_emails or []) if e and "@" in e])
    )
    emails_map = find_emails_for_names(wid, attendee_names or [])
    resolved = [e for e in emails_map.values() if e]
    missing = [n for n, e in emails_map.items() if not e]
    emails = list(dict.fromkeys(explicit + resolved))
    if missing:
        return (
            None,
            "I could not match these names in your Google Contacts (with email): "
            + ", ".join(missing)
            + ". Try a different spelling, add them in Google Contacts, or paste their full email address.",
        )

    lead = get_meeting_reminder_lead_minutes(wid)
    plan = ResolvedMeetingPlan(
        wid=wid,
        title=title or "Meeting",
        start=start,
        end=end,
        emails=emails,
        reminder_lead_minutes=lead,
        timezone_name=s.default_tz,
    )
    return (plan, "")


def format_meeting_preview(plan: ResolvedMeetingPlan) -> str:
    """Human-readable summary for WhatsApp before creation."""
    tz = ZoneInfo(plan.timezone_name)
    start_l = plan.start.astimezone(tz) if plan.start.tzinfo else plan.start.replace(tzinfo=tz)
    end_l = plan.end.astimezone(tz) if plan.end.tzinfo else plan.end.replace(tzinfo=tz)
    inv = ", ".join(plan.emails) if plan.emails else "(no invitees — calendar entry only)"
    lines = [
        "Please confirm this meeting before I add it to your calendar:",
        f"• Title: {plan.title}",
        f"• Start: {start_l.strftime('%a %d %b %Y, %H:%M')} ({plan.timezone_name})",
        f"• End: {end_l.strftime('%a %d %b %Y, %H:%M')} ({plan.timezone_name})",
        f"• Invitees: {inv}",
        f"• Reminder ping: {plan.reminder_lead_minutes} minutes before",
        "",
        "Reply yes or confirm if this is correct. If anything should change, say what to fix.",
    ]
    return "\n".join(lines)


def preview_schedule_from_agent(
    wa_id: str,
    title: str,
    start_iso: str | None,
    end_iso: str | None,
    attendee_names: list[str] | None,
    attendee_emails: list[str] | None = None,
) -> str:
    """Validate and return a confirmation summary; does not create a calendar event."""
    plan, err = resolve_meeting_plan(
        wa_id, title, start_iso, end_iso, attendee_names, attendee_emails=attendee_emails
    )
    if not plan:
        return err
    return format_meeting_preview(plan)


def create_calendar_event(
    wa_id: str,
    title: str,
    start: datetime,
    end: datetime,
    attendee_emails: list[str],
    add_meet: bool = True,
) -> dict:
    """Create Google Calendar event via API; returns id, links."""
    creds = get_google_credentials_for_wa(wa_id)
    if not creds:
        raise RuntimeError("not_connected")

    creds = ensure_fresh_credentials(creds)
    s = get_settings()
    tz = ZoneInfo(s.default_tz)
    start_l = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
    end_l = end.astimezone(tz) if end.tzinfo else end.replace(tzinfo=tz)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body: dict = {
        "summary": title,
        "start": {"dateTime": start_l.isoformat(), "timeZone": s.default_tz},
        "end": {"dateTime": end_l.isoformat(), "timeZone": s.default_tz},
        "attendees": [{"email": e} for e in attendee_emails if e],
    }
    if add_meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    kwargs: dict = {"calendarId": "primary", "body": body, "sendUpdates": "all"}
    if add_meet:
        kwargs["conferenceDataVersion"] = 1

    event = service.events().insert(**kwargs).execute()
    meet_link = None
    if event.get("conferenceData") and event["conferenceData"].get("entryPoints"):
        for ep in event["conferenceData"]["entryPoints"]:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break
    return {
        "id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "meet_link": meet_link or event.get("hangoutLink"),
        "raw": event,
    }


def schedule_from_agent(
    wa_id: str,
    title: str,
    start_iso: str | None,
    end_iso: str | None,
    attendee_names: list[str] | None,
    attendee_emails: list[str] | None = None,
) -> str:
    """Create event after validation. Caller must have obtained user confirmation separately."""
    plan, err = resolve_meeting_plan(
        wa_id, title, start_iso, end_iso, attendee_names, attendee_emails=attendee_emails
    )
    if not plan:
        return err

    try:
        ev = create_calendar_event(plan.wid, plan.title, plan.start, plan.end, plan.emails)
    except Exception as e:
        logger.exception("Calendar create failed")
        return f"Could not create calendar event: {e!s}"

    sb = get_supabase()
    meet_url = ev.get("meet_link") or ev.get("html_link") or ""
    meeting_id: str | None = None
    try:
        ins = (
            sb.table("meetings")
            .insert(
                {
                    "title": plan.title,
                    "attendees": plan.emails,
                    "scheduled_at": plan.start.isoformat(),
                    "meeting_url": meet_url or None,
                    "created_by_wa_id": plan.wid,
                    # null so reminder job always uses current users.meeting_reminder_lead_minutes
                    "reminder_lead_minutes": None,
                    "google_calendar_event_id": ev.get("id"),
                }
            )
            .execute()
        )
        if ins.data:
            meeting_id = ins.data[0].get("id")
    except Exception as e:
        logger.exception("Supabase insert meeting failed")
        return (
            f"Calendar event was created, but saving to the database failed: {e!s}. "
            f"Link: {ev.get('meet_link') or ev.get('html_link') or ''}"
        )

    s = get_settings()
    if (s.recall_api_key or "").strip() and meet_url and meeting_id:
        try:
            ja = choose_join_at_for_meeting(plan.start)
            bot = create_notetaker_bot(
                meet_url,
                ja,
                {"meeting_id": str(meeting_id), "wa_id": plan.wid},
            )
            bid = bot.get("id")
            if bid:
                sb.table("meetings").update({"recall_bot_id": bid}).eq("id", meeting_id).execute()
        except Exception as e:
            logger.warning("Recall notetaker scheduling failed (meeting still created): %s", e)

    link = meet_url
    parts = [
        "Done! Meeting scheduled.",
        f"Invite sent to: {', '.join(plan.emails)}." if plan.emails else "Invite created.",
    ]
    if link:
        parts.append(f"Join: {link}")
    parts.append(f"Reminder: I will ping you {plan.reminder_lead_minutes} minutes before.")
    return " ".join(parts)
