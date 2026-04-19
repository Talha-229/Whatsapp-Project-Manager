from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    return URLSafeTimedSerializer(s.secret_key, salt="wa-google-oauth")


def sign_state(wa_id: str, max_age_seconds: int = 3600) -> str:
    """Pack whatsapp sender id into a short-lived token for OAuth start URL."""
    return _serializer().dumps({"wa": wa_id})


def verify_state(state: str, max_age_seconds: int = 3600) -> str | None:
    try:
        data = _serializer().loads(state, max_age=max_age_seconds)
        return data.get("wa")
    except (BadSignature, SignatureExpired):
        return None
