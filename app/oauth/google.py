import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_settings
from app.crypto_util import encrypt_secret
from app.db.supabase_client import get_supabase
from app.oauth.state_token import verify_state
from app.services.google_scopes import GOOGLE_OAUTH_SCOPES

logger = logging.getLogger(__name__)

USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

router = APIRouter(prefix="/oauth/google", tags=["oauth"])

SCOPES = GOOGLE_OAUTH_SCOPES


def _flow() -> Flow:
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret:
        raise HTTPException(503, "Google OAuth not configured")
    client_config = {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [s.google_redirect_uri],
        }
    }
    # PKCE is off: /start and /callback use separate Flow instances, so an
    # auto-generated code_verifier from authorization_url() would be lost.
    # Web client + client_secret does not require PKCE.
    return Flow.from_client_config(
        client_config,
        SCOPES,
        redirect_uri=s.google_redirect_uri,
        autogenerate_code_verifier=False,
    )


async def _fetch_google_profile(access_token: str) -> dict[str, Any]:
    """Return userinfo (email, name, …) or {} if the call fails."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code != 200:
            logger.warning("Google userinfo failed: %s %s", r.status_code, r.text[:300])
            return {}
        return r.json()
    except Exception as e:
        logger.warning("Google userinfo request error: %s", e)
        return {}


@router.get("/start")
async def oauth_start(state: str):
    wa_id = verify_state(state)
    if not wa_id:
        raise HTTPException(400, "Invalid or expired link. Request a new link from WhatsApp.")
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret:
        return HTMLResponse(
            "<h3>Google OAuth is not configured on the server.</h3>"
            "<p>Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> "
            "from Google Cloud → APIs &amp; Services → Credentials (OAuth client) to your <code>.env</code>, "
            "then <strong>restart</strong> the API (env is cached on startup).</p>",
            status_code=503,
        )
    flow = _flow()
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(authorization_url, status_code=302)


@router.get("/callback")
async def oauth_callback(request: Request):
    if request.query_params.get("error"):
        err = request.query_params.get("error")
        return HTMLResponse(f"<h3>Google error: {err}</h3>", status_code=400)

    state = request.query_params.get("state")
    if not state:
        raise HTTPException(400, "Missing state")

    wa_id = verify_state(state)
    if not wa_id:
        return HTMLResponse("<h3>Invalid or expired session. Open a new link from WhatsApp.</h3>", status_code=400)

    try:
        flow = _flow()
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as e:
        logger.exception("OAuth token exchange failed")
        return HTMLResponse(f"<h3>OAuth failed: {e}</h3>", status_code=400)

    creds: Credentials = flow.credentials
    refresh = creds.refresh_token
    if not refresh:
        return HTMLResponse(
            "<h3>No refresh token returned. Remove app access in Google Account settings and try again.</h3>",
            status_code=400,
        )

    enc = encrypt_secret(refresh)
    now = datetime.now(timezone.utc).isoformat()
    profile = await _fetch_google_profile(creds.token)
    email = (profile.get("email") or "").strip() or None
    gname = (profile.get("name") or "").strip() or None

    sb = get_supabase()
    existing = sb.table("users").select("id,name").eq("whatsapp_number", wa_id).limit(1).execute()
    if existing.data:
        row = existing.data[0]
        update_payload: dict[str, Any] = {
            "google_refresh_token_encrypted": enc,
            "google_connected_at": now,
        }
        if email:
            update_payload["email"] = email
        if gname and (row.get("name") or "").strip() in ("", "WhatsApp User"):
            update_payload["name"] = gname
        sb.table("users").update(update_payload).eq("whatsapp_number", wa_id).execute()
    else:
        insert_payload: dict[str, Any] = {
            "name": gname or "WhatsApp User",
            "whatsapp_number": wa_id,
            "google_refresh_token_encrypted": enc,
            "google_connected_at": now,
        }
        if email:
            insert_payload["email"] = email
        sb.table("users").insert(insert_payload).execute()

    return HTMLResponse(
        "<html><body style='font-family:sans-serif;padding:2rem'>"
        "<h2>Google connected</h2><p>You can return to WhatsApp and schedule meetings.</p>"
        "</body></html>"
    )
