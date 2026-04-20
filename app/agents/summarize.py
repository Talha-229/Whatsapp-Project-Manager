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


def _parent_ai_index_for_tool(msgs: list[BaseMessage], tool_index: int) -> int | None:
    """Index of the AIMessage whose tool_calls include this ToolMessage, or None if invalid."""
    j = tool_index - 1
    while j >= 0 and isinstance(msgs[j], ToolMessage):
        j -= 1
    if j < 0 or not isinstance(msgs[j], AIMessage) or not msgs[j].tool_calls:
        return None
    tid = msgs[tool_index].tool_call_id
    if not tid:
        return None
    if any(tc.get("id") == tid for tc in (msgs[j].tool_calls or [])):
        return j
    return None


def _orphan_tool_remove_ids(msgs: list[BaseMessage]) -> list[str]:
    """
    OpenAI rejects requests where a ToolMessage is not immediately after the AIMessage
    that issued tool_calls (with matching tool_call_id). Summarization splits can strand
    ToolMessages; checkpoints can too. Collect ids for RemoveMessage.
    """
    out: list[str] = []
    for i, m in enumerate(msgs):
        if not isinstance(m, ToolMessage):
            continue
        parent_i = _parent_ai_index_for_tool(msgs, i)
        if parent_i is None:
            mid = getattr(m, "id", None)
            if mid:
                out.append(mid)
            else:
                logger.warning(
                    "Orphan ToolMessage at index %s has no id; state may stay invalid until thread reset",
                    i,
                )
    return out


def _incomplete_tool_round_remove_ids(msgs: list[BaseMessage]) -> list[str]:
    """
    Remove an AIMessage with tool_calls if not every tool_call_id has a ToolMessage
    before the next non-tool message; also remove any partial ToolMessages in that block.
    """
    out: list[str] = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if isinstance(m, AIMessage) and m.tool_calls:
            needed = {str(tc["id"]) for tc in (m.tool_calls or []) if tc.get("id")}
            j = i + 1
            tool_block: list[BaseMessage] = []
            while j < len(msgs) and isinstance(msgs[j], ToolMessage):
                tool_block.append(msgs[j])
                j += 1
            seen = {str(getattr(t, "tool_call_id", None)) for t in tool_block}
            seen.discard("None")
            if needed and needed != needed & seen:
                mid = getattr(m, "id", None)
                if mid:
                    out.append(mid)
                for t in tool_block:
                    tid = getattr(t, "id", None)
                    if tid:
                        out.append(tid)
                logger.warning(
                    "Removing AIMessage + partial tools (incomplete tool round; checkpoint/summary corruption)"
                )
                i = j
                continue
        i += 1
    return out


def _safe_split_for_summary(msgs: list[BaseMessage], tail_size: int) -> int:
    """
    Return split index `s` such that old = msgs[:s], protected = msgs[s:], and we never
    cut between an AIMessage with tool_calls and its ToolMessages.
    """
    if len(msgs) <= tail_size:
        return 0
    s = len(msgs) - tail_size
    while 0 < s < len(msgs) and isinstance(msgs[s], ToolMessage):
        s -= 1
    if s > 0 and isinstance(msgs[s - 1], AIMessage) and msgs[s - 1].tool_calls:
        if s < len(msgs) and isinstance(msgs[s], ToolMessage):
            s -= 1
            while 0 < s < len(msgs) and isinstance(msgs[s], ToolMessage):
                s -= 1
    return s


def _remove_ids_updates(ids: list[str]) -> list[RemoveMessage]:
    return [RemoveMessage(id=i) for i in ids]


def pre_model_summarize(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph pre_model_hook:
    1) Always strip invalid tool message sequences (fixes OpenAI 400 on bad checkpoints / splits).
    2) If over token budget, summarize messages older than the protected tail.
    """
    msgs = list(state.get("messages") or [])

    cleanup_ids = list(
        dict.fromkeys(_orphan_tool_remove_ids(msgs) + _incomplete_tool_round_remove_ids(msgs))
    )
    removed = set(cleanup_ids)
    linear = [m for m in msgs if getattr(m, "id", None) not in removed]

    split = _safe_split_for_summary(linear, PROTECTED_MESSAGE_COUNT)
    old = linear[:split]
    prior_summary = (state.get("conversation_summary") or "").strip()

    enc = tiktoken.get_encoding("cl100k_base")
    total = _count_tokens(linear, enc)

    updates: list[Any] = _remove_ids_updates(cleanup_ids)

    if len(linear) <= PROTECTED_MESSAGE_COUNT or total <= TOKEN_BUDGET:
        if updates:
            return {"messages": updates}
        return {}

    s = get_settings()
    if not s.openai_api_key:
        new_summary = prior_summary + "\n[Older messages omitted due to length.]"
        for m in old:
            mid = getattr(m, "id", None)
            if mid:
                updates.append(RemoveMessage(id=mid))
            else:
                logger.warning("Skipping RemoveMessage for old message without id (cannot trim thread)")
        sm = SystemMessage(content=f"Earlier summary: {new_summary}")
        return {"messages": updates + [sm], "conversation_summary": new_summary}

    blob = "\n".join(f"{m.__class__.__name__}: {_msg_text(m)}" for m in old[:200])
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

    for m in old:
        mid = getattr(m, "id", None)
        if mid:
            updates.append(RemoveMessage(id=mid))
        else:
            logger.warning("Skipping RemoveMessage for summarized message without id")
    sm = SystemMessage(content=body)
    return {"messages": updates + [sm], "conversation_summary": merged_summary}


def _msg_text(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, str):
        return c[:2000]
    return str(c)[:2000]
