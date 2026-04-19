"""User projects in Supabase (WhatsApp-scoped)."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.agents.context import get_wa_id
from app.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.agent_context import ensure_user_row_for_wa
from app.services.users_resolve import get_user_row_by_wa, normalize_wa_id


def _today_iso() -> str:
    s = get_settings()
    try:
        tz = ZoneInfo(s.default_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date().isoformat()


def _parse_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    t = str(s).strip()[:10]
    try:
        return date.fromisoformat(t)
    except Exception:
        return None


@tool
def create_my_project(
    name: str,
    deadline: str | None = None,
    status: str = "active",
) -> str:
    """
    Create a **new** project for this WhatsApp user. Use when they ask to add/create a project or initiative.
    name: project title. deadline: optional YYYY-MM-DD. status: optional, usually 'active' (also 'on_hold', etc.).
    """
    wa = get_wa_id()
    ensure_user_row_for_wa(wa)
    wid = normalize_wa_id(wa)
    n = (name or "").strip()
    if not n:
        return json.dumps({"ok": False, "error": "missing_name"}, ensure_ascii=False)

    row = get_user_row_by_wa(wa) or {}
    owner = (row.get("name") or "Me").strip() or "Me"
    dl = _parse_date(deadline)
    st = (status or "active").strip().lower() or "active"
    if st not in ("active", "on_hold", "done", "completed", "cancelled"):
        st = "active"

    sb = get_supabase()
    try:
        ins = (
            sb.table("projects")
            .insert(
                {
                    "name": n[:500],
                    "owner": owner[:200],
                    "status": st,
                    "deadline": dl.isoformat() if dl else None,
                    "created_by_wa_id": wid,
                }
            )
            .execute()
        )
        pid = ins.data[0].get("id") if ins.data else None
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": True,
            "project_id": str(pid) if pid else None,
            "name": n,
            "deadline": dl.isoformat() if dl else None,
            "status": st,
            "message": "Project saved.",
        },
        ensure_ascii=False,
    )


@tool
def list_my_projects(scope: str = "active") -> str:
    """
    List this user's projects. scope: 'active' (default, excludes done/completed/cancelled), 'all', or 'on_hold' only.
    Use when the user asks what projects they have, list my projects, show initiatives.
    """
    wa = get_wa_id()
    wid = normalize_wa_id(wa)
    if not wid:
        return json.dumps({"ok": False, "error": "no_user"}, ensure_ascii=False)

    scope_l = (scope or "active").strip().lower()
    sb = get_supabase()
    try:
        q = sb.table("projects").select("*").eq("created_by_wa_id", wid)
        if scope_l == "all":
            r = q.execute()
            rows = r.data or []
        elif scope_l == "on_hold":
            r = q.eq("status", "on_hold").execute()
            rows = r.data or []
        else:
            # active: hide terminal states
            r = q.execute()
            rows = [
                x
                for x in (r.data or [])
                if (x.get("status") or "active").lower() not in ("done", "completed", "cancelled")
            ]
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)

    today = _today_iso()

    out = []
    for x in rows:
        out.append(
            {
                "id": str(x.get("id", "")),
                "name": x.get("name"),
                "owner": x.get("owner"),
                "status": x.get("status"),
                "deadline": x.get("deadline"),
            }
        )

    return json.dumps(
        {"ok": True, "scope": scope_l, "projects": out, "today": today},
        ensure_ascii=False,
    )
