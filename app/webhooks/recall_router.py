"""
Inbound webhooks from Recall.ai (delivered via Svix).

Register in Recall dashboard: POST URL = {PUBLIC_BASE_URL}/webhooks/recall
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.meeting_summary_dispatch import (
    chunk_whatsapp_bodies,
    format_dispatch_whatsapp,
    generate_meeting_dispatch,
    sanitize_action_due_date,
)
from app.services.recall_client import (
    get_bot_transcript,
    transcript_payload_to_text,
    transcribe_meeting_from_recall_video,
)
from app.whatsapp.meta_client import send_text_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["recall"])


def _svix_verify(body: bytes, headers: dict[str, str], secret: str) -> dict[str, Any]:
    from svix.webhooks import Webhook

    wh = Webhook(secret)
    hdrs = {
        "svix-id": headers.get("svix-id") or headers.get("webhook-id") or "",
        "svix-timestamp": headers.get("svix-timestamp") or headers.get("webhook-timestamp") or "",
        "svix-signature": headers.get("svix-signature") or headers.get("webhook-signature") or "",
    }
    if not all(hdrs.values()):
        raise ValueError("Missing Svix webhook headers")
    raw = wh.verify(body, hdrs)
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return json.loads(json.dumps(raw))


def _extract_bot_id(payload: dict[str, Any]) -> str | None:
    paths: list[tuple[str, ...]] = [
        ("data", "bot", "id"),
        ("data", "id"),
        ("bot", "id"),
        ("bot_id",),
        ("id",),
    ]
    for path in paths:
        cur: Any = payload
        try:
            for p in path:
                cur = cur[p]
            if isinstance(cur, str) and len(cur) > 8:
                return cur
        except (KeyError, TypeError):
            continue

    def walk(o: Any) -> str | None:
        if isinstance(o, dict):
            bid = o.get("bot_id")
            if isinstance(bid, str) and len(bid) > 8:
                return bid
            b = o.get("bot")
            if isinstance(b, dict) and isinstance(b.get("id"), str):
                return b["id"]
            for v in o.values():
                x = walk(v)
                if x:
                    return x
        if isinstance(o, list):
            for v in o:
                x = walk(v)
                if x:
                    return x
        return None

    return walk(payload)


def _process_bot_event(bot_id: str) -> None:
    sb = get_supabase()
    text = ""
    try:
        raw_tr = get_bot_transcript(bot_id)
        text = transcript_payload_to_text(raw_tr)
    except Exception as e:
        logger.warning("Recall transcript not ready for %s: %s", bot_id, e)

    if not text.strip():
        try:
            fallback = transcribe_meeting_from_recall_video(bot_id)
            if fallback:
                text = fallback
                logger.info("Used Whisper on Recall video_mixed for %s", bot_id)
        except Exception as e:
            logger.warning("Recall video Whisper fallback failed for %s: %s", bot_id, e)

    try:
        mr = (
            sb.table("meetings")
            .select("id, created_by_wa_id, transcript, summary, title")
            .eq("recall_bot_id", bot_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.exception("Supabase meeting lookup for recall_bot_id: %s", e)
        return

    rows = mr.data or []
    if not rows:
        logger.warning("No meeting row for recall_bot_id=%s", bot_id)
        return

    row = rows[0]
    if row.get("summary"):
        logger.info("Meeting %s already has post-meeting summary; skipping", row.get("id"))
        return

    transcript_body = (row.get("transcript") or text or "").strip()
    if not transcript_body:
        logger.info("Recall empty transcript (no video fallback) for %s", bot_id)
        return

    mid = row.get("id")
    wa = row.get("created_by_wa_id")
    title = (row.get("title") or "Meeting").strip() or "Meeting"

    saved_transcript_this_run = False
    if not row.get("transcript") and text.strip():
        try:
            sb.table("meetings").update({"transcript": text.strip()}).eq("id", mid).execute()
            saved_transcript_this_run = True
        except Exception as e:
            logger.exception("Failed to save transcript for meeting %s: %s", mid, e)
            return

    dispatch = generate_meeting_dispatch(transcript_body, title)
    if not dispatch:
        if wa and saved_transcript_this_run:
            send_text_message(
                wa,
                "Your meeting transcript was saved, but I could not generate a recap "
                "(summary unavailable).",
            )
        return

    summary_to_store = dispatch.brief_summary.strip() or "—"
    try:
        sb.table("meetings").update({"summary": summary_to_store}).eq("id", mid).execute()
    except Exception as e:
        logger.exception("Failed to save summary for meeting %s: %s", mid, e)

    try:
        sb.table("action_items").delete().eq("meeting_id", mid).execute()
        for it in dispatch.action_items:
            desc = (it.description or "").strip()
            if not desc:
                continue
            owner = (it.owner or "").strip() or None
            due = sanitize_action_due_date(it.due_date)
            sb.table("action_items").insert(
                {
                    "meeting_id": mid,
                    "description": desc[:4000],
                    "owner": (owner or "Unassigned")[:500],
                    "due_date": due,
                }
            ).execute()
    except Exception as e:
        logger.exception("Failed to save action items for meeting %s: %s", mid, e)

    if not wa:
        return

    body = format_dispatch_whatsapp(title, dispatch)
    for part in chunk_whatsapp_bodies(body):
        send_text_message(wa, part)


@router.post("/recall")
async def recall_webhook(request: Request):
    body = await request.body()
    s = get_settings()
    secret = (s.recall_webhook_secret or "").strip()

    hdrs = {k.lower(): v for k, v in request.headers.items()}

    if secret:
        try:
            payload = _svix_verify(body, hdrs, secret)
        except Exception as e:
            logger.warning("Recall webhook verify failed: %s", e)
            raise HTTPException(400, "Invalid webhook signature") from e
    else:
        logger.warning("RECALL_WEBHOOK_SECRET not set; accepting webhook without verification")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception as e:
            raise HTTPException(400, "Invalid JSON") from e

    bot_id = _extract_bot_id(payload) if isinstance(payload, dict) else None
    if not bot_id:
        logger.info("Recall webhook: could not extract bot id; keys=%s", list(payload.keys())[:20] if isinstance(payload, dict) else type(payload))
        return JSONResponse({"status": "ok", "note": "no bot id"})

    try:
        _process_bot_event(bot_id)
    except Exception:
        logger.exception("Recall process bot %s", bot_id)

    return JSONResponse({"status": "ok"})
