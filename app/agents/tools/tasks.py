"""User tasks stored in Supabase (WhatsApp-scoped)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.agents.context import get_wa_id
from app.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.agent_context import ensure_user_row_for_wa
from app.services.users_resolve import get_user_row_by_wa, normalize_wa_id


def _today_in_default_tz() -> date:
    s = get_settings()
    try:
        tz = ZoneInfo(s.default_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _parse_due(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    t = str(s).strip()[:10]
    try:
        return date.fromisoformat(t)
    except Exception:
        return None


def _find_open_task(wid: str, needle: str) -> dict | None:
    """Best-effort match among open tasks for this user."""
    if not needle.strip():
        return None
    sb = get_supabase()
    r = (
        sb.table("tasks")
        .select("*")
        .eq("created_by_wa_id", wid)
        .neq("status", "done")
        .execute()
    )
    candidates = r.data or []
    n = needle.strip().lower()
    for t in candidates:
        title = (t.get("title") or "").lower()
        if n in title or title in n:
            return t
    for t in candidates:
        if any(w and w in (t.get("title") or "").lower() for w in n.split() if len(w) > 2):
            return t
    return None


@tool
def create_my_task(title: str, due_date: str | None = None, notes: str | None = None) -> str:
    """
    Create a **new** task only. Do NOT use this to change due date or edit an existing task — use update_my_task instead.
    Use when the user asks to add/create a new task, todo, or reminder.
    due_date: optional ISO date YYYY-MM-DD (e.g. tomorrow = ask user or infer from context).
    """
    wa = get_wa_id()
    ensure_user_row_for_wa(wa)
    wid = normalize_wa_id(wa)
    t = (title or "").strip()
    if not t:
        return json.dumps({"ok": False, "error": "missing_title"}, ensure_ascii=False)

    row = get_user_row_by_wa(wa) or {}
    assignee = (row.get("name") or "Me").strip() or "Me"
    due = _parse_due(due_date)
    sb = get_supabase()
    try:
        payload: dict = {
            "title": t[:500],
            "assignee": assignee[:200],
            "due_date": due.isoformat() if due else None,
            "status": "open",
            "project_id": None,
            "created_by_wa_id": wid,
        }
        n = (notes or "").strip()
        if n:
            payload["notes"] = n[:2000]
        ins = sb.table("tasks").insert(payload).execute()
        tid = ins.data[0].get("id") if ins.data else None
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": True,
            "task_id": tid,
            "title": t,
            "due_date": due.isoformat() if due else None,
            "message": "Task saved.",
        },
        ensure_ascii=False,
    )


@tool
def list_my_tasks(scope: str = "open") -> str:
    """
    List this user's tasks. scope: 'open' (default), 'today' (due today or no due date), 'week' (due within 7 days or undated),
    'overdue', or 'all'. Use for what are my tasks, what is due today, show my todos.
    """
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    if not wid:
        return json.dumps({"ok": False, "error": "no_user"}, ensure_ascii=False)

    sb = get_supabase()
    today = _today_in_default_tz()
    week_end = today + timedelta(days=7)
    scope_l = (scope or "open").strip().lower()

    try:
        base = sb.table("tasks").select("*").eq("created_by_wa_id", wid)
        if scope_l == "all":
            r = base.execute()
            rows = r.data or []
        elif scope_l == "open":
            r = base.neq("status", "done").execute()
            rows = r.data or []
        elif scope_l == "overdue":
            r = base.neq("status", "done").execute()
            rows = []
            for x in r.data or []:
                d = _parse_due(str(x["due_date"])) if x.get("due_date") else None
                if d and d < today:
                    rows.append(x)
        elif scope_l == "today":
            r = base.neq("status", "done").execute()
            rows = []
            for x in r.data or []:
                d = _parse_due(str(x["due_date"])) if x.get("due_date") else None
                if d is None or d == today:
                    rows.append(x)
        elif scope_l == "week":
            r = base.neq("status", "done").execute()
            rows = []
            for x in r.data or []:
                d = _parse_due(str(x["due_date"])) if x.get("due_date") else None
                if d is None or (today <= d <= week_end):
                    rows.append(x)
        else:
            r = base.neq("status", "done").execute()
            rows = r.data or []
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    return json.dumps(
        {"ok": True, "scope": scope_l, "tasks": _format_tasks(rows), "today": today.isoformat()},
        ensure_ascii=False,
    )


def _format_tasks(rows: list) -> list[dict]:
    out = []
    for x in rows:
        out.append(
            {
                "id": str(x.get("id", "")),
                "title": x.get("title"),
                "due_date": x.get("due_date"),
                "status": x.get("status"),
                "notes": ((x.get("notes") or "")[:200] or None),
            }
        )
    return out


@tool
def update_my_task(
    task_title: str,
    new_due_date: str | None = None,
    new_title: str | None = None,
    new_notes: str | None = None,
) -> str:
    """
    Update an **existing** open task (change due date, rename, or notes). Use when the user asks to move, reschedule,
    change deadline, or edit a task — NOT create_my_task.
    task_title: words from the existing task title to match (same matching as complete_my_task).
    new_due_date: ISO date YYYY-MM-DD, or the word "clear" to remove due date; omit if not changing date.
    new_title: omit if not renaming.
    new_notes: omit if not changing notes; use "clear" to clear notes.
    """
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    needle = (task_title or "").strip()
    if not needle:
        return json.dumps({"ok": False, "error": "missing_task_title"}, ensure_ascii=False)

    try:
        match = _find_open_task(wid, needle)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    if not match:
        return json.dumps({"ok": False, "error": "no_match", "hint": "list_my_tasks to see open tasks"}, ensure_ascii=False)

    patch: dict = {}
    if new_title is not None and str(new_title).strip():
        patch["title"] = str(new_title).strip()[:500]

    if new_due_date is not None:
        raw = str(new_due_date).strip().lower()
        if raw in ("", "clear", "none", "remove"):
            patch["due_date"] = None
        else:
            d = _parse_due(new_due_date)
            if not d:
                return json.dumps({"ok": False, "error": "bad_date", "expected": "YYYY-MM-DD"}, ensure_ascii=False)
            patch["due_date"] = d.isoformat()

    if new_notes is not None:
        raw = str(new_notes).strip().lower()
        if raw in ("clear", "none", "remove"):
            patch["notes"] = None
        elif str(new_notes).strip():
            patch["notes"] = str(new_notes).strip()[:2000]

    if not patch:
        return json.dumps({"ok": False, "error": "nothing_to_update", "matched": match.get("title")}, ensure_ascii=False)

    sb = get_supabase()
    try:
        sb.table("tasks").update(patch).eq("id", match["id"]).execute()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": True,
            "updated": match.get("title"),
            "task_id": str(match.get("id")),
            "changes": patch,
        },
        ensure_ascii=False,
    )


@tool
def complete_my_task(task_title: str) -> str:
    """
    Mark a task done by matching title (best match among open tasks). Use when user says they finished a task.
    """
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    needle = (task_title or "").strip().lower()
    if not needle:
        return json.dumps({"ok": False, "error": "missing_title"}, ensure_ascii=False)

    try:
        match = _find_open_task(wid, needle)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    if not match:
        sb = get_supabase()
        try:
            r = (
                sb.table("tasks")
                .select("id")
                .eq("created_by_wa_id", wid)
                .neq("status", "done")
                .execute()
            )
            n = len(r.data or [])
        except Exception:
            n = 0
        return json.dumps({"ok": False, "error": "no_match", "open_count": n}, ensure_ascii=False)

    sb = get_supabase()
    try:
        sb.table("tasks").update({"status": "done"}).eq("id", match["id"]).execute()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    return json.dumps(
        {"ok": True, "completed": match.get("title"), "task_id": str(match.get("id"))},
        ensure_ascii=False,
    )
