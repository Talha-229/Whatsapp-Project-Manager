"""Single source of truth for Google OAuth scopes (Calendar + Contacts)."""

GOOGLE_OAUTH_SCOPES: list[str] = [
    # Required so token response matches request: Google adds `openid` when using
    # userinfo scopes; oauthlib rejects fetch_token if requested scopes differ.
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/contacts.readonly",
]
