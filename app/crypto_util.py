import base64
import hashlib
import logging

from cryptography.fernet import Fernet

from app.config import get_settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet | None:
    s = get_settings()
    key = (s.google_token_encryption_key or "").strip()
    if not key:
        logger.warning("GOOGLE_TOKEN_ENCRYPTION_KEY not set; storing Google tokens in plaintext (demo only)")
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        # allow raw 32-byte key passed as hex
        raw = bytes.fromhex(key) if len(key) == 64 else None
        if raw and len(raw) == 32:
            k = base64.urlsafe_b64encode(raw)
            return Fernet(k)
        raise


def encrypt_secret(plain: str) -> str:
    f = _fernet()
    if f is None:
        return plain
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    f = _fernet()
    if f is None:
        return token
    return f.decrypt(token.encode()).decode()
