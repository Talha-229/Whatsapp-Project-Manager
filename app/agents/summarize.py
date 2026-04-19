"""Trim + summarize long thread histories (tiktoken policy)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import tiktoken
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.modifier import RemoveMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings

if TYPE_CHECKING:
    from app.agents.state import OrchestratorState

logger = logging.getLogger(__name__)

# Keep last 10 turns ~= 20 messages if alternating; cap protected window
PROTECTED_MESSAGE_COUNT = 20
TOKEN_BUDGET = 3000


def _count_tokens(messages: list[BaseMessage], encoding) -> int:
    n = 0
    for m in messages:
        content = m.content
        if isinstance(content, str):
            n += len(encoding.encode(content))
        elif isinstance(content, list):
            n += len(encoding.encode(str(content)))
        else:
            n += len(encoding.encode(str(content)))
    return n


def pre_model_summarize(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph pre_model_hook: if total tokens > TOKEN_BUDGET, summarize messages
    older than the protected tail and store `conversation_summary`.
    """
    msgs = list(state.get("messages") or [])
    if len(msgs) <= PROTECTED_MESSAGE_COUNT:
        return {}

    enc = tiktoken.get_encoding("cl100k_base")
    total = _count_tokens(msgs, enc)
    if total <= TOKEN_BUDGET:
        return {}

    protected = msgs[-PROTECTED_MESSAGE_COUNT:]
    old = msgs[:-PROTECTED_MESSAGE_COUNT]
    prior_summary = (state.get("conversation_summary") or "").strip()

    s = get_settings()
    if not s.openai_api_key:
        # Hard truncate: keep summary stub + protected only
        new_summary = prior_summary + "\n[Older messages omitted due to length.]"
        updates: list[BaseMessage] = []
        for m in old:
            if getattr(m, "id", None):
                updates.append(RemoveMessage(id=m.id))
        sm = SystemMessage(content=f"Earlier summary: {new_summary}")
        return {"messages": updates + [sm], "conversation_summary": new_summary}

    # LLM summary of older segment
    blob = "\n".join(
        f"{m.__class__.__name__}: {_msg_text(m)}" for m in old[:200]
    )
    llm = ChatOpenAI(api_key=s.openai_api_key, model=s.openai_model, temperature=0.2)
    sum_prompt = (
        "Summarize the following WhatsApp assistant conversation segment for memory. "
        "Be concise (bullet points ok). Focus on user goals, scheduled times, and decisions.\n\n" + blob[:12000]
    )
    try:
        out = llm.invoke([HumanMessage(content=sum_prompt)])
        piece = (out.content or "").strip()
    except Exception as e:
        logger.exception("Summarization LLM failed: %s", e)
        piece = "[Summary unavailable.]"

    merged_summary = (prior_summary + "\n" + piece).strip() if prior_summary else piece
    body = f"Earlier summary:\n{merged_summary}"

    updates: list[BaseMessage] = []
    for m in old:
        mid = getattr(m, "id", None)
        if mid:
            updates.append(RemoveMessage(id=mid))
    sm = SystemMessage(content=body)
    return {"messages": updates + [sm], "conversation_summary": merged_summary}


def _msg_text(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, str):
        return c[:2000]
    return str(c)[:2000]
