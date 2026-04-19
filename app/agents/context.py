"""Per-request WhatsApp user id for tools (graph compiled once; wa_id varies)."""

import contextvars

wa_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("wa_id", default=None)


def set_wa_id(wa_id: str) -> contextvars.Token[str | None]:
    return wa_id_ctx.set(wa_id)


def reset_wa_id(token: contextvars.Token[str | None]) -> None:
    wa_id_ctx.reset(token)


def get_wa_id() -> str:
    v = wa_id_ctx.get()
    if not v:
        raise RuntimeError("wa_id not set in context")
    return v
