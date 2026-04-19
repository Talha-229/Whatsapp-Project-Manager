import hashlib
import hmac
import json
import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.agents.graph import run_agent
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

# Meta often delivers the same inbound message more than once (retries / duplicate notifications).
# Dedupe on WhatsApp message id so we only run the agent once per user message.
_dedupe_lock = threading.Lock()
_processed_wa_message_ids: dict[str, float] = {}
_DEDUPE_TTL_SEC = 86_400  # 24h — ids are unique per message
_MAX_DEDUPE_ENTRIES = 20_000


def _dedupe_should_skip(wa_message_id: str | None) -> bool:
    """Return True if this message id was already handled (skip processing)."""
    if not wa_message_id or not isinstance(wa_message_id, str):
        return False
    now = time.time()
    with _dedupe_lock:
        # prune occasionally to bound memory
        if len(_processed_wa_message_ids) > _MAX_DEDUPE_ENTRIES:
            cutoff = now - _DEDUPE_TTL_SEC
            stale = [k for k, t in _processed_wa_message_ids.items() if t < cutoff]
            for k in stale[: _MAX_DEDUPE_ENTRIES // 2]:
                _processed_wa_message_ids.pop(k, None)
        if wa_message_id in _processed_wa_message_ids:
            logger.info(
                "Skipping duplicate WhatsApp inbound (already processed): %s…",
                wa_message_id[:32],
            )
            return True
        _processed_wa_message_ids[wa_message_id] = now
    return False


def _verify_signature(body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, "sha256=" + expected)


def _extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []) or []:
                out.append(msg)
    return out


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    s = get_settings()
    expected = (s.meta_wa_verify_token or "").strip()
    got = (hub_verify_token or "").strip()
    if hub_mode == "subscribe" and got == expected and expected and hub_challenge:
        return PlainTextResponse(content=str(hub_challenge))
    logger.warning(
        "Webhook verify failed: mode=%r expected_token_configured=%s token_match=%s has_challenge=%s",
        hub_mode,
        bool(expected),
        got == expected if expected else False,
        bool(hub_challenge),
    )
    raise HTTPException(403, "Verification failed")


@router.post("/whatsapp")
async def receive_webhook(request: Request):
    s = get_settings()
    body = await request.body()

    if s.meta_wa_app_secret:
        sig = request.headers.get("X-Hub-Signature-256")
        if not _verify_signature(body, sig, s.meta_wa_app_secret):
            raise HTTPException(403, "Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # Handle statuses (delivered/read) without processing
    if payload.get("entry"):
        for entry in payload["entry"]:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("statuses"):
                    return {"status": "ok"}

    messages = _extract_messages(payload)
    for msg in messages:
        wa_mid = msg.get("id")
        if _dedupe_should_skip(wa_mid if isinstance(wa_mid, str) else None):
            continue
        from_id = msg.get("from")
        if not from_id:
            continue
        msg_type = msg.get("type")
        text_body = ""
        if msg_type == "text":
            text_body = (msg.get("text") or {}).get("body") or ""
        elif msg_type == "audio":
            media_id = (msg.get("audio") or {}).get("id")
            if media_id:
                try:
                    from app.whatsapp.media import transcribe_whatsapp_audio

                    text_body = transcribe_whatsapp_audio(media_id)
                except Exception as e:
                    logger.exception("Transcription failed")
                    from app.whatsapp.meta_client import send_text_message

                    _ = send_text_message(
                        from_id,
                        "I could not transcribe that voice note. Please send a text message or try again.",
                    )
                    continue
        else:
            from app.whatsapp.meta_client import send_text_message

            _ = send_text_message(from_id, "Please send text or a voice note.")
            continue

        if not text_body.strip():
            continue

        try:
            reply = run_agent(from_id, text_body.strip())
            from app.whatsapp.meta_client import send_text_message

            sent = send_text_message(from_id, reply)
            if not sent.get("ok"):
                logger.error("WhatsApp outbound failed (check META_WA_ACCESS_TOKEN): %s", sent)
        except Exception as e:
            logger.exception("Agent failed")
            from app.whatsapp.meta_client import send_text_message

            sent = send_text_message(
                from_id,
                f"Something went wrong processing your message: {e!s}"[:1000],
            )
            if not sent.get("ok"):
                logger.error("Could not send error reply to user: %s", sent)

    return {"status": "ok"}
