import re

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)


def extract_emails(text: str) -> list[str]:
    """Return unique emails in order of appearance."""
    found = _EMAIL_RE.findall(text or "")
    return list(dict.fromkeys(found))


def filter_names_vs_emails(names: list[str], emails: list[str]) -> list[str]:
    """Drop name tokens that are already represented by an explicit email (e.g. Javed vs javed@...)."""
    if not names:
        return []
    email_l = [e.lower() for e in emails if e]
    local_parts = {e.split("@", 1)[0].lower() for e in email_l if "@" in e}
    out: list[str] = []
    for n in names:
        nl = (n or "").strip()
        if not nl:
            continue
        low = nl.lower()
        if low in local_parts:
            continue
        if any(low in e for e in email_l):
            continue
        out.append(nl)
    return out[:15]
