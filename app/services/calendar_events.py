"""List Google Calendar events in a time range (primary calendar)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from app.config import get_settings
from app.services.google_credentials import ensure_fresh_credentials, get_google_credentials_for_wa
from app.services.users_resolve import normalize_wa_id

logger = logging.getLogger(__name__)


def event_start_utc(ev: dict, default_tz: str) -> datetime | None:
    """Parse Calendar API event start to UTC. Handles dateTime and all-day date."""
    start = ev.get("start") or {}
    dt_s = start.get("dateTime")
    date_s = start.get("date")
    try:
        if dt_s:
            t = datetime.fromisoformat(dt_s.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc)
        if date_s:
            local = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=ZoneInfo(default_tz))
            return local.astimezone(timezone.utc)
    except Exception:
        logger.debug("Could not parse event start: %s", ev.get("id"))
    return None


def list_primary_calendar_events_window(
    wa_id: str,
    *,
    hours_ahead: int = 72,
) -> list[dict]:
    """
    Raw-ish event dicts from primary calendar: id, summary, htmlLink, start, end, status.
    Window: now UTC through now + hours_ahead.
    """
    wid = normalize_wa_id(wa_id)
    creds = get_google_credentials_for_wa(wid)
    if not creds:
        return []
    creds = ensure_fresh_credentials(creds)
    now_utc = datetime.now(timezone.utc)
    time_min = now_utc.isoformat().replace("+00:00", "Z")
    time_max = (now_utc + timedelta(hours=max(1, hours_ahead))).isoformat().replace("+00:00", "Z")

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    out: list[dict] = []
    page_token = None
    while True:
        kwargs: dict = {
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.events().list(**kwargs).execute()
        for ev in resp.get("items") or []:
            out.append(
                {
                    "id": ev.get("id"),
                    "summary": ev.get("summary") or "(no title)",
                    "html_link": ev.get("htmlLink"),
                    "start": ev.get("start") or {},
                    "end": ev.get("end") or {},
                    "status": ev.get("status") or "confirmed",
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def list_upcoming_events(
    wa_id: str,
    *,
    days: int = 7,
) -> list[dict]:
    """
    Return events from now through end of window in default_tz.
    Each dict: summary, start, end, html_link, id.
    """
    wid = normalize_wa_id(wa_id)
    creds = get_google_credentials_for_wa(wid)
    if not creds:
        return []
    creds = ensure_fresh_credentials(creds)
    s = get_settings()
    tz = ZoneInfo(s.default_tz)
    now = datetime.now(timezone.utc).astimezone(tz)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    out: list[dict] = []
    page_token = None
    while True:
        kwargs: dict = {
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 250,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.events().list(**kwargs).execute()
        for ev in resp.get("items") or []:
            start = ev.get("start") or {}
            end = ev.get("end") or {}
            start_s = start.get("dateTime") or start.get("date") or ""
            end_s = end.get("dateTime") or end.get("date") or ""
            out.append(
                {
                    "id": ev.get("id"),
                    "summary": ev.get("summary") or "(no title)",
                    "start": start_s,
                    "end": end_s,
                    "html_link": ev.get("htmlLink"),
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out
