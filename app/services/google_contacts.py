"""
Resolve attendee names to emails using Google People API (live contacts).

Uses connections.list with pagination; matches requested names to display names
and returns primary email addresses. No reliance on seeded Supabase users.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.services.google_credentials import ensure_fresh_credentials

logger = logging.getLogger(__name__)

_PEOPLE = "people"
_VERSION = "v1"


def _person_display_strings(person: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for n in person.get("names") or []:
        for key in ("displayName", "givenName", "familyName", "middleName"):
            v = (n.get(key) or "").strip()
            if v:
                out.add(v.lower())
        u = (n.get("unstructuredName") or "").strip()
        if u:
            out.add(u.lower())
    return out


def _primary_email(person: dict[str, Any]) -> str | None:
    emails = person.get("emailAddresses") or []
    if not emails:
        return None
    primary = [e for e in emails if e.get("metadata", {}).get("primary")]
    pick = primary[0] if primary else emails[0]
    return (pick.get("value") or "").strip() or None


def fetch_all_connections(creds: Credentials) -> list[dict[str, Any]]:
    creds = ensure_fresh_credentials(creds)
    service = build(_PEOPLE, _VERSION, credentials=creds, cache_discovery=False)
    connections: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "resourceName": "people/me",
            "pageSize": 500,
            "personFields": "names,emailAddresses",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.people().connections().list(**kwargs).execute()
        connections.extend(resp.get("connections", []) or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return connections


def _display_name(person: dict[str, Any]) -> str:
    for n in person.get("names") or []:
        d = (n.get("displayName") or "").strip()
        if d:
            return d
    parts = []
    for n in person.get("names") or []:
        for key in ("givenName", "familyName"):
            p = (n.get(key) or "").strip()
            if p:
                parts.append(p)
    return " ".join(parts).strip() or "Unknown"


def _score_person_for_query(person: dict[str, Any], query: str) -> int:
    """Higher = better match; 0 = no match."""
    q = query.lower().strip()
    if len(q) < 1:
        return 0
    email = (_primary_email(person) or "").lower()
    score = 0
    if "@" in q:
        if email and (q in email or email == q):
            score = 100
        elif email and q.split("@", 1)[0] in email:
            score = 85
    q_tokens = [t for t in re.split(r"\s+", q) if len(t) >= 2]
    blobs = _person_display_strings(person)
    for blob in blobs:
        if q == blob:
            return max(score, 100)
        if q in blob or blob in q:
            score = max(score, 80)
        for tok in q_tokens:
            if tok in blob:
                score = max(score, 40 + min(len(tok), 20))
    return score


def search_contact_candidates(
    creds: Credentials,
    queries: list[str],
    *,
    limit_per_query: int = 6,
    min_score: int = 35,
) -> dict[str, list[dict[str, Any]]]:
    """
    For each free-text query, return ranked contact candidates (name + primary email).
    Used to confirm attendee details before booking.
    """
    queries = [q.strip() for q in queries if q and q.strip()]
    out: dict[str, list[dict[str, Any]]] = {q: [] for q in queries}
    if not queries:
        return out

    try:
        people = fetch_all_connections(creds)
    except Exception as e:
        logger.exception("Google People fetch for search failed: %s", e)
        return out

    for q in queries:
        ranked: list[tuple[int, dict[str, Any]]] = []
        for person in people:
            sc = _score_person_for_query(person, q)
            if sc < min_score:
                continue
            em = _primary_email(person)
            if not em:
                continue
            ranked.append(
                (
                    sc,
                    {
                        "display_name": _display_name(person),
                        "email": em,
                        "match_score": sc,
                    },
                )
            )
        ranked.sort(key=lambda x: -x[0])
        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for _, row in ranked:
            key = row["email"].lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append({k: v for k, v in row.items() if k != "match_score"})
            if len(uniq) >= limit_per_query:
                break
        out[q] = uniq

    return out


def resolve_names_to_emails(creds: Credentials, names: list[str]) -> dict[str, str | None]:
    """
    Map each requested name (e.g. 'Rania') to an email from Google Contacts.
    Best-effort case-insensitive substring / token match on display names.
    """
    names = [n.strip() for n in names if n and n.strip()]
    out: dict[str, str | None] = {n: None for n in names}
    if not names:
        return out

    try:
        people = fetch_all_connections(creds)
    except Exception as e:
        logger.exception("Google People connections.list failed: %s", e)
        return out

    # Build lookup: normalized search key -> list of (email, display blobs)
    entries: list[tuple[str | None, set[str]]] = []
    for person in people:
        email = _primary_email(person)
        blobs = _person_display_strings(person)
        if email and blobs:
            entries.append((email, blobs))

    for q in names:
        qn = q.lower().strip()
        q_tokens = [t for t in re.split(r"\s+", qn) if len(t) >= 2]
        best_email: str | None = None
        best_score = 0
        for email, blobs in entries:
            score = 0
            for blob in blobs:
                if qn == blob:
                    score = 100
                    break
                if qn in blob or blob in qn:
                    score = max(score, 80)
                for tok in q_tokens:
                    if tok in blob:
                        score = max(score, 40 + len(tok))
            if score > best_score:
                best_score = score
                best_email = email
        if best_score >= 40:
            out[q] = best_email
        elif best_score > 0 and best_email:
            out[q] = best_email

    return out
