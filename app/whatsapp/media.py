import logging
from io import BytesIO

import httpx
from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

META_GRAPH = "https://graph.facebook.com/v21.0"


def _meta_headers() -> dict[str, str]:
    s = get_settings()
    return {"Authorization": f"Bearer {s.meta_wa_access_token}"}


def fetch_media_url(media_id: str) -> str | None:
    """Resolve Graph media id to a download URL."""
    url = f"{META_GRAPH}/{media_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_meta_headers())
        r.raise_for_status()
        data = r.json()
        return data.get("url")


def download_media_bytes(media_url: str) -> bytes:
    with httpx.Client(timeout=120.0) as client:
        r = client.get(media_url, headers=_meta_headers())
        r.raise_for_status()
        return r.content


def transcribe_whisper(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=s.openai_api_key)
    bio = BytesIO(audio_bytes)
    bio.name = filename
    tr = client.audio.transcriptions.create(model=s.whisper_model, file=bio)
    return (tr.text or "").strip()


def transcribe_whatsapp_audio(media_id: str) -> str:
    url = fetch_media_url(media_id)
    if not url:
        raise RuntimeError("Could not resolve media URL")
    data = download_media_bytes(url)
    return transcribe_whisper(data)
