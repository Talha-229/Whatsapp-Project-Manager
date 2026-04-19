"""Policy search tools."""

from langchain_core.tools import tool

from app.agents.context import get_wa_id
from app.services.policies import search_policies


@tool
def search_company_policies(query: str) -> str:
    """Search the company policy database for HR, workplace, leave, expense, and conduct topics. Use when the user asks about policies, rules, remote work, vacation, etc."""
    _ = get_wa_id()
    rows = search_policies(query, limit=5)
    if not rows:
        return "No matching policies found. Try different keywords or ask HR."
    chunks = []
    for r in rows:
        title = r.get("title", "")
        content = (r.get("content") or "")[:900]
        chunks.append(f"*{title}*: {content}")
    return "Policy database results:\n\n" + "\n\n".join(chunks)
