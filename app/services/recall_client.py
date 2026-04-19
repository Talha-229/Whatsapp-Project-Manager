"""
Recall.ai REST API — create notetaker bots and fetch transcripts.

Docs: https://docs.recall.ai/ — use the same region as your dashboard URL
(e.g. ap-northeast-1.recall.ai).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def recall_api_v1_base(region: str) -> str:
    r = (region or "us-east-1").strip()
    return f"https://{r}.recall.ai/api/v1"


def _headers() -> dict[str, str]:
    s = get_settings()
    key = (s.recall_api_key or "").strip()
    if not key:
        raise RuntimeError("RECALL_API_KEY is not set")
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}


def create_notetaker_bot(
    meeting_url: str,
    join_at: datetime | None,
    metadata: dict[str, str],
    bot_name: str | None = None,
) -> dict[str, Any]:
    """
    POST /bot/ — pass join_at to target the calendar start time in UTC.
    If join_at is None, omits join_at (ad-hoc / join ASAP; may return 507 under load).
    """
    s = get_settings()
    base = recall_api_v1_base(s.recall_region)
    name = (bot_name or s.recall_bot_name or "WhatsApp Notetaker").strip()[:100]
    body: dict[str, Any] = {
        "meeting_url": meeting_url.strip(),
        "bot_name": name,
        "metadata": {k: str(v) for k, v in metadata.items()},
    }
    if join_at is not None:
        ju = join_at if join_at.tzinfo else join_at.replace(tzinfo=timezone.utc)
        body["join_at"] = ju.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{base}/bot/", json=body, headers=_headers())
    if r.status_code >= 400:
        logger.error("Recall create bot failed: %s %s", r.status_code, r.text[:2000])
        r.raise_for_status()
    return r.json()


def get_bot(bot_id: str) -> dict[str, Any]:
    s = get_settings()
    base = recall_api_v1_base(s.recall_region)
    with httpx.Client(timeout=60.0) as client:
        r = client.get(f"{base}/bot/{bot_id}/", headers=_headers())
    if r.status_code >= 400:
        logger.error("Recall get bot failed: %s %s", r.status_code, r.text[:2000])
        r.raise_for_status()
    return r.json()


def _video_mixed_download_url_from_bot(bot: dict[str, Any]) -> str | None:
    """Pre-signed MP4 URL from latest recording with done video_mixed (Recall docs: media_shortcuts.video_mixed.data.download_url)."""
    recs = bot.get("recordings")
    if not isinstance(recs, list):
        return None
    for rec in reversed(recs):
        if not isinstance(rec, dict):
            continue
        ms = rec.get("media_shortcuts")
        if not isinstance(ms, dict):
            continue
        vm = ms.get("video_mixed")
        if not isinstance(vm, dict):
            continue
        st = vm.get("status")
        if isinstance(st, dict) and st.get("code") != "done":
            continue
        data = vm.get("data")
        if isinstance(data, dict):
            url = data.get("download_url")
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def transcribe_meeting_from_recall_video(bot_id: str) -> str | None:
    """When Recall has no transcript artifact, download mixed MP4 and run Whisper on the audio track."""
    bot = get_bot(bot_id)
    url = _video_mixed_download_url_from_bot(bot)
    if not url:
        logger.info("No video_mixed download_url for bot %s", bot_id)
        return None
    # Whisper API limit ~25 MB; typical short Meet recordings are fine.
    max_bytes = 25 * 1024 * 1024
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        r = client.get(url)
    if r.status_code >= 400:
        logger.error("Recall video download failed: %s %s", r.status_code, r.text[:500])
        return None
    data = r.content
    if len(data) > max_bytes:
        logger.warning("Recall video too large for Whisper (%s bytes); skip", len(data))
        return None
    from app.whatsapp.media import transcribe_whisper

    try:
        text = transcribe_whisper(data, filename="meeting.mp4")
    except Exception as e:
        logger.exception("Whisper transcribe of Recall video failed for %s: %s", bot_id, e)
        return None
    return text.strip() or None


def _transcript_artifact_id_from_bot(bot: dict[str, Any]) -> str | None:
    """Resolve transcript artifact UUID from GET /bot/ embed (recordings → media_shortcuts.transcript)."""
    recs = bot.get("recordings")
    if not isinstance(recs, list):
        return None
    for rec in reversed(recs):
        if not isinstance(rec, dict):
            continue
        ms = rec.get("media_shortcuts")
        if not isinstance(ms, dict):
            continue
        tr = ms.get("transcript")
        if isinstance(tr, dict):
            tid = tr.get("id")
            if isinstance(tid, str) and len(tid) > 8:
                return tid
    return None


def _transcript_artifact_done(artifact: dict[str, Any]) -> bool:
    st = artifact.get("status")
    if not isinstance(st, dict):
        return False
    return st.get("code") == "done"


def _download_transcript_json(download_url: str) -> Any:
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(download_url)
    if r.status_code >= 400:
        logger.error("Recall transcript download failed: %s %s", r.status_code, r.text[:500])
        r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return r.text


def get_bot_transcript(bot_id: str) -> Any:
    """
    Fetch meeting transcript via GET /transcript/{artifact_id}/ (legacy /bot/.../transcript/ removed).

    Loads bot, finds transcript artifact id on the latest recording, retrieves artifact JSON,
    then downloads the JSON payload from data.download_url.
    """
    bot = get_bot(bot_id)
    tid = _transcript_artifact_id_from_bot(bot)
    if not tid:
        return None

    s = get_settings()
    base = recall_api_v1_base(s.recall_region)
    with httpx.Client(timeout=120.0) as client:
        r = client.get(f"{base}/transcript/{tid}/", headers=_headers())
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        logger.error("Recall get transcript artifact failed: %s %s", r.status_code, r.text[:2000])
        r.raise_for_status()
    artifact = r.json()
    if not isinstance(artifact, dict):
        return artifact
    if not _transcript_artifact_done(artifact):
        st = artifact.get("status")
        code = st.get("code") if isinstance(st, dict) else st
        logger.info("Transcript artifact %s not ready (status=%s)", tid, code)
        return None
    data = artifact.get("data")
    url = data.get("download_url") if isinstance(data, dict) else None
    if not url:
        logger.warning("Transcript artifact %s has no download_url", tid)
        return None
    return _download_transcript_json(url)


def transcript_payload_to_text(data: Any) -> str:
    """Normalize Recall transcript JSON to plain text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list):
        lines: list[str] = []
        for item in data:
            lines.append(transcript_payload_to_text(item))
        return "\n".join(x for x in lines if x).strip()
    if isinstance(data, dict):
        if "text" in data and isinstance(data["text"], str):
            return data["text"].strip()
        if "transcript" in data:
            return transcript_payload_to_text(data["transcript"])
        if "paragraphs" in data:
            return transcript_payload_to_text(data["paragraphs"])
        if "words" in data and isinstance(data["words"], list):
            parts = []
            for w in data["words"]:
                if isinstance(w, dict) and w.get("text"):
                    parts.append(str(w["text"]))
                elif isinstance(w, str):
                    parts.append(w)
            return " ".join(parts).strip()
        # fallback: concatenate string values
        return "\n".join(str(v) for v in data.values() if isinstance(v, str)).strip()
    return json.dumps(data, ensure_ascii=False)[:50000]


def choose_join_at_for_meeting(start: datetime) -> datetime | None:
    """Return calendar start in UTC for Recall join_at, or None only if that time is already past.

    If the meeting is still in the future, we always pass this instant — even if it is only
    minutes away. (Previously we used ad-hoc when <10 min away, which made the bot join
    immediately while the Google invite still showed the real start time.)

    Naive datetimes use DEFAULT_TZ (same as create_calendar_event).
    """
    now = datetime.now(timezone.utc)
    if start.tzinfo:
        st = start.astimezone(timezone.utc)
    else:
        tz = ZoneInfo(get_settings().default_tz)
        st = start.replace(tzinfo=tz).astimezone(timezone.utc)
    if st > now:
        return st
    return None
