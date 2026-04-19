"""
After a meeting transcript is available, extract decisions and action items (owners, deadlines)
and format a WhatsApp-friendly recap.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 120_000


class DispatchActionItem(BaseModel):
    description: str
    owner: str = "Unassigned"
    due_date: str | None = None  # YYYY-MM-DD or null


class MeetingDispatch(BaseModel):
    decisions: list[str] = Field(default_factory=list)
    action_items: list[DispatchActionItem] = Field(default_factory=list)
    brief_summary: str = ""


def generate_meeting_dispatch(transcript: str, meeting_title: str) -> MeetingDispatch | None:
    """
    Use OpenAI to produce structured decisions, action items, and a short summary.
    Returns None if API key missing or the model call fails.
    """
    s = get_settings()
    if not (s.openai_api_key or "").strip():
        logger.warning("OPENAI_API_KEY not set; skipping meeting dispatch extraction")
        return None

    body = (transcript or "").strip()
    if not body:
        return None
    if len(body) > MAX_TRANSCRIPT_CHARS:
        body = body[:MAX_TRANSCRIPT_CHARS] + "\n\n[Transcript truncated for analysis.]"

    schema_hint = json.dumps(
        {
            "decisions": ["string — concrete agreements or choices made"],
            "action_items": [
                {
                    "description": "string — specific task",
                    "owner": "person name or role, or Unassigned",
                    "due_date": "YYYY-MM-DD or null if unknown",
                }
            ],
            "brief_summary": "2–4 sentences: overall outcome and next steps",
        },
        indent=2,
    )

    user_prompt = (
        f"Meeting title: {meeting_title or 'Meeting'}\n\n"
        "Analyze the transcript below. Extract:\n"
        "1) decisions — clear outcomes or agreements (not general discussion).\n"
        "2) action_items — tasks with best-effort owner and deadline if mentioned; "
        "use null for due_date when not stated.\n"
        "3) brief_summary — concise overview.\n"
        "Respond with JSON only matching this shape:\n"
        f"{schema_hint}\n\n---\nTRANSCRIPT:\n{body}"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=s.openai_api_key)
        resp = client.chat.completions.create(
            model=s.openai_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured meeting notes from transcripts. "
                        "Return only valid JSON. Use empty arrays when nothing applies. "
                        "Do not invent dates or owners; prefer null/Unassigned when unclear."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        data: Any = json.loads(raw)
        return MeetingDispatch.model_validate(data)
    except Exception as e:
        logger.exception("Meeting dispatch LLM failed: %s", e)
        return None


def format_dispatch_whatsapp(title: str, dispatch: MeetingDispatch) -> str:
    """Single message body; caller may chunk for 4096 limit."""
    lines: list[str] = []
    t = (title or "Meeting").strip() or "Meeting"
    lines.append(f"📋 *Meeting recap: {t}*")
    lines.append("")

    if dispatch.brief_summary.strip():
        lines.append(dispatch.brief_summary.strip())
        lines.append("")

    if dispatch.decisions:
        lines.append("*Decisions*")
        for d in dispatch.decisions:
            d = (d or "").strip()
            if d:
                lines.append(f"• {d}")
        lines.append("")
    else:
        lines.append("*Decisions*")
        lines.append("• (none captured)")
        lines.append("")

    lines.append("*Action items*")
    if not dispatch.action_items:
        lines.append("• (none captured)")
    else:
        for it in dispatch.action_items:
            desc = (it.description or "").strip()
            if not desc:
                continue
            owner = (it.owner or "Unassigned").strip() or "Unassigned"
            due = (it.due_date or "").strip()
            due_part = f" — Due: {due}" if due else ""
            lines.append(f"• {desc} — Owner: {owner}{due_part}")

    return "\n".join(lines).strip()


def chunk_whatsapp_bodies(text: str, limit: int = 3800) -> list[str]:
    """Split long text on paragraph boundaries for Meta 4096 cap (use margin)."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    n = 1
    while rest:
        if len(rest) <= limit:
            chunks.append(rest if n == 1 else f"(continued {n})\n{rest}")
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        piece = rest[:cut].strip()
        rest = rest[cut:].strip()
        prefix = "" if n == 1 else f"(continued {n})\n"
        chunks.append(prefix + piece)
        n += 1
    return chunks


def sanitize_action_due_date(s: str | None) -> str | None:
    """Keep YYYY-MM-DD only; else None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None
