import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.calendar_events import event_start_utc, list_primary_calendar_events_window
from app.services.user_prefs import get_meeting_reminder_lead_minutes
from app.whatsapp.meta_client import send_text_message

logger = logging.getLogger(__name__)


def _log_query_failure(msg: str, exc: Exception) -> None:
    err = str(exc).lower()
    transient = (
        "getaddrinfo" in err
        or "connecterror" in err
        or "10054" in err
        or "10060" in err
        or "remoteprotocol" in err
        or "connectionterminated" in err
        or "timeout" in err
        or "timed out" in err
        or "remote end closed" in err
        or "connection aborted" in err
    )
    if transient:
        logger.warning("%s: %s", msg, exc)
    else:
        logger.exception("%s", msg)


def _send_meeting_reminders() -> None:
    """Remind for rows in `meetings` (usually bot-scheduled)."""
    sb = get_supabase()
    now = datetime.now(timezone.utc)

    try:
        r = (
            sb.table("meetings")
            .select("*")
            .is_("reminder_sent_at", None)
            .execute()
        )
    except Exception as e:
        _log_query_failure("Meeting reminder query failed", e)
        return

    # Always use current user preference — per-meeting snapshot could be stale after user changes lead time.
    users_cache: dict[str, int] = {}

    for m in r.data or []:
        mid = m.get("id")
        title = m.get("title", "Meeting")
        when_s = m.get("scheduled_at", "")
        wa = m.get("created_by_wa_id")
        if not wa:
            continue
        if wa not in users_cache:
            try:
                users_cache[wa] = get_meeting_reminder_lead_minutes(wa)
            except Exception as e:
                _log_query_failure("Meeting reminder user lead failed", e)
                users_cache[wa] = 15
        lead = users_cache[wa]

        try:
            scheduled_at = datetime.fromisoformat(when_s.replace("Z", "+00:00"))
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if scheduled_at <= now:
            continue
        # Fire once we're inside the window: at or after (start - lead), before start.
        trigger_at = scheduled_at - timedelta(minutes=lead)
        if now < trigger_at:
            continue

        mins_left = max(1, int((scheduled_at - now).total_seconds() // 60))
        body = f'Reminder: "{title}" starts in about {mins_left} minute(s).'
        try:
            sent = send_text_message(wa, body)
            if not sent.get("ok"):
                logger.error("Meeting reminder send failed: %s", sent)
                continue
            sb.table("meetings").update({"reminder_sent_at": now.isoformat()}).eq("id", mid).execute()
        except Exception as e:
            logger.exception("Failed meeting reminder %s: %s", mid, e)


def _send_google_calendar_reminders() -> None:
    """
    Remind for primary-calendar events (including those created outside the bot).
    Skips events linked to `meetings.google_calendar_event_id` (bot path uses `meetings` row).
    Dedupes via `calendar_reminder_sent`.
    """
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    s = get_settings()
    tz_name = s.default_tz

    try:
        ur = (
            sb.table("users")
            .select("whatsapp_number, meeting_reminder_lead_minutes, google_refresh_token_encrypted")
            .execute()
        )
    except Exception as e:
        _log_query_failure("Calendar reminder user query failed", e)
        return

    for row in ur.data or []:
        wa = row.get("whatsapp_number")
        if not wa or not row.get("google_refresh_token_encrypted"):
            continue
        lead = int(row.get("meeting_reminder_lead_minutes") or 15)

        try:
            mr = (
                sb.table("meetings")
                .select("google_calendar_event_id")
                .eq("created_by_wa_id", wa)
                .execute()
            )
            bot_gcal_ids = {
                m["google_calendar_event_id"]
                for m in (mr.data or [])
                if m.get("google_calendar_event_id")
            }
        except Exception as e:
            _log_query_failure(f"Meetings gcal id lookup failed wa={wa}", e)
            bot_gcal_ids = set()

        try:
            events = list_primary_calendar_events_window(wa, hours_ahead=72)
        except Exception as e:
            logger.warning("Calendar list failed for wa=%s: %s", wa, e)
            continue

        for ev in events:
            if (ev.get("status") or "").lower() == "cancelled":
                continue
            eid = ev.get("id")
            if not eid:
                continue
            if eid in bot_gcal_ids:
                continue

            start_utc = event_start_utc(ev, tz_name)
            if not start_utc:
                continue
            if start_utc <= now:
                continue

            trigger_at = start_utc - timedelta(minutes=lead)
            if now < trigger_at:
                continue

            mins_left = max(1, int((start_utc - now).total_seconds() // 60))

            start_key = start_utc.isoformat().replace("+00:00", "Z")
            try:
                dup = (
                    sb.table("calendar_reminder_sent")
                    .select("id")
                    .eq("whatsapp_number", wa)
                    .eq("google_event_id", eid)
                    .eq("event_start_utc", start_key)
                    .limit(1)
                    .execute()
                )
                if dup.data:
                    continue
            except Exception as e:
                _log_query_failure("calendar_reminder_sent dup check failed", e)
                continue

            title = ev.get("summary") or "Event"
            link = ev.get("html_link") or ""
            body = f'Reminder: "{title}" starts in about {mins_left} minute(s).'
            if link:
                body += f" {link}"

            try:
                sent = send_text_message(wa, body)
                if not sent.get("ok"):
                    logger.error("Calendar reminder send failed: %s", sent)
                    continue
                sb.table("calendar_reminder_sent").insert(
                    {
                        "whatsapp_number": wa,
                        "google_event_id": eid,
                        "event_start_utc": start_key,
                    }
                ).execute()
            except Exception as e:
                logger.exception("Calendar reminder insert/send failed: %s", e)


def _send_overdue_tasks() -> None:
    sb = get_supabase()
    today = date.today().isoformat()
    try:
        r = (
            sb.table("tasks")
            .select("*")
            .lt("due_date", today)
            .neq("status", "done")
            .execute()
        )
    except Exception as e:
        _log_query_failure("Overdue task query failed", e)
        return

    users = sb.table("users").select("name,whatsapp_number").execute()
    by_name = {(row.get("name") or "").lower(): row.get("whatsapp_number") for row in (users.data or [])}

    for t in r.data or []:
        wa_direct = t.get("created_by_wa_id")
        if wa_direct:
            wid = str(wa_direct).strip()
        else:
            assignee = t.get("assignee") or ""
            wid = by_name.get(assignee.lower())
        if not wid:
            continue
        title = t.get("title", "Task")
        due = t.get("due_date", "")
        body = f"Overdue task: \"{title}\" (due {due}). Please update status."
        try:
            sent = send_text_message(wid, body)
            if not sent.get("ok"):
                logger.error("Overdue task notify failed: %s", sent)
        except Exception as e:
            logger.exception("Overdue notify failed: %s", e)


def setup_scheduler(sched: BackgroundScheduler) -> None:
    # coalesce: if a run was missed while another was active, run once when free.
    # Google Calendar polling can exceed 1 minute (timeouts); 2-minute gcal interval avoids
    # "maximum number of running instances reached" when the previous tick is still in flight.
    job_kw = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}
    sched.add_job(
        _send_meeting_reminders,
        "interval",
        minutes=1,
        id="meeting_reminders",
        replace_existing=True,
        **job_kw,
    )
    sched.add_job(
        _send_google_calendar_reminders,
        "interval",
        minutes=2,
        id="gcal_reminders",
        replace_existing=True,
        **job_kw,
    )
    sched.add_job(_send_overdue_tasks, "interval", hours=6, id="overdue_tasks", replace_existing=True)
