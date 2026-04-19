from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "WhatsApp Agent API"
    debug: bool = False

    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    # Postgres connection string for LangGraph PostgresSaver (e.g. Supabase: Settings -> Database -> URI)
    database_url: str = ""

    meta_wa_verify_token: str = ""
    meta_wa_access_token: str = ""
    meta_wa_phone_number_id: str = ""
    meta_wa_app_secret: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    whisper_model: str = "whisper-1"

    default_tz: str = "UTC"

    public_base_url: str = "http://localhost:8000"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/oauth/google/callback"
    google_token_encryption_key: str = ""  # Fernet key base64; generate if empty at runtime not allowed — require in prod

    langgraph_max_clarify_turns: int = 2

    # Recall.ai (meeting notetaker) + Svix webhook verification — optional
    recall_api_key: str = ""
    recall_region: str = "us-east-1"
    recall_bot_name: str = "WhatsApp Notetaker"
    recall_webhook_secret: str = ""  # whsec_... from Recall dashboard (Svix)

    @field_validator(
        "meta_wa_verify_token",
        "meta_wa_access_token",
        "meta_wa_phone_number_id",
        "meta_wa_app_secret",
        mode="before",
    )
    @classmethod
    def strip_meta_strings(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1].strip()
            return s
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
