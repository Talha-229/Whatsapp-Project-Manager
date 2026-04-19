import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

META_GRAPH = "https://graph.facebook.com/v21.0"


def send_text_message(to_wa_id: str, body: str) -> dict[str, Any]:
    """Send a WhatsApp text message via Cloud API."""
    s = get_settings()
    if not s.meta_wa_access_token or not s.meta_wa_phone_number_id:
        logger.error("Meta WhatsApp not configured; cannot send message")
        return {"ok": False, "error": "meta_not_configured"}

    url = f"{META_GRAPH}/{s.meta_wa_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {s.meta_wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_id,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        if r.status_code == 401:
            logger.error(
                "Meta WhatsApp send failed: 401 Unauthorized (access token expired or invalid). "
                "Open Meta for Developers → WhatsApp → API setup and generate a new permanent token, "
                "then update META_WA_ACCESS_TOKEN. Body: %s",
                r.text[:800],
            )
        else:
            logger.error("Meta send failed: %s %s", r.status_code, r.text)
        return {
            "ok": False,
            "status_code": r.status_code,
            "body": r.text,
        }
    try:
        data = r.json()
    except Exception:
        data = {}
    return {"ok": True, "data": data}
