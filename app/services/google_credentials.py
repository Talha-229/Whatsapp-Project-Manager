import logging
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import get_settings
from app.crypto_util import decrypt_secret
from app.db.supabase_client import get_supabase
from app.services.google_scopes import GOOGLE_OAUTH_SCOPES

logger = logging.getLogger(__name__)


def _normalize_wa_id(wa_id: str) -> str:
    return re.sub(r"\D", "", wa_id or "")


def get_google_credentials_for_wa(wa_id: str) -> Credentials | None:
    """Load OAuth credentials for a WhatsApp user, or None if Google is not linked."""
    s = get_settings()
    wid = _normalize_wa_id(wa_id)
    sb = get_supabase()
    r = sb.table("users").select("google_refresh_token_encrypted").eq("whatsapp_number", wid).limit(1).execute()
    row = r.data[0] if r.data else None
    if not row or not row.get("google_refresh_token_encrypted"):
        return None
    refresh = decrypt_secret(row["google_refresh_token_encrypted"])
    return Credentials(
        token=None,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=GOOGLE_OAUTH_SCOPES,
    )


def ensure_fresh_credentials(creds: Credentials) -> Credentials:
    if creds.expired or not creds.token:
        creds.refresh(Request())
    return creds
