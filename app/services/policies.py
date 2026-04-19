import logging
import re

from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def search_policies(query: str, limit: int = 3) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    sb = get_supabase()
    r = sb.table("policies").select("*").limit(50).execute()
    rows = r.data or []
    qlow = q.lower()
    scored: list[tuple[int, dict]] = []
    for row in rows:
        blob = f"{row.get('title','')} {row.get('content','')} {row.get('category','')}".lower()
        score = 0
        for tok in re.split(r"\W+", qlow):
            if len(tok) < 2:
                continue
            if tok in blob:
                score += 2
        if qlow in blob:
            score += 5
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return [row for _, row in scored[:limit]]
