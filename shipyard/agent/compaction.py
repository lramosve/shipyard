"""Context compaction — auto-summarize old messages when approaching token limits."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from shipyard.config import settings

logger = logging.getLogger(__name__)

# Rough heuristic: ~4 chars per token
CHARS_PER_TOKEN = 4
# Keep the last N messages intact during compaction
KEEP_RECENT = 10


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """Estimate token count from message content length."""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total += len(content) // CHARS_PER_TOKEN
    return total


def needs_compaction(messages: list[BaseMessage]) -> bool:
    """Check if messages exceed the compaction threshold."""
    estimated = estimate_tokens(messages)
    threshold = int(settings.context_window_size * settings.compaction_threshold)
    if estimated > threshold:
        logger.info(f"Compaction needed: ~{estimated} tokens > {threshold} threshold")
        return True
    return False


def compact_messages(messages: list[BaseMessage], model: Any) -> list[BaseMessage]:
    """Summarize older messages, keeping the system prompt and recent messages.

    Returns a new message list: [system_msg, summary_msg, ...recent_messages].
    """
    if len(messages) <= KEEP_RECENT + 1:
        return messages  # Nothing to compact

    system_msg = messages[0] if isinstance(messages[0], SystemMessage) else None
    start_idx = 1 if system_msg else 0

    # Split into old (to summarize) and recent (to keep)
    old_messages = messages[start_idx:-KEEP_RECENT]
    recent_messages = messages[-KEEP_RECENT:]

    if not old_messages:
        return messages

    # Build summary prompt
    old_text_parts = []
    for msg in old_messages:
        role = msg.type
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        # Truncate very long tool results in the summary input
        if len(content) > 2000:
            content = content[:2000] + "... (truncated)"
        old_text_parts.append(f"[{role}]: {content}")

    old_text = "\n\n".join(old_text_parts)

    summary_prompt = [
        SystemMessage(content="You are a conversation summarizer. Summarize the following conversation history concisely. Preserve: all file paths mentioned, code changes made, key decisions, errors encountered, and their resolutions. Do NOT include any tool call syntax or JSON."),
        HumanMessage(content=f"Summarize this conversation history:\n\n{old_text}"),
    ]

    try:
        response = model.invoke(summary_prompt)
        summary_text = response.content if isinstance(response.content, str) else str(response.content)
        logger.info(f"Compacted {len(old_messages)} messages into summary ({len(summary_text)} chars)")
    except Exception as e:
        logger.warning(f"Compaction failed, keeping original messages: {e}")
        return messages

    # Build new message list
    result = []
    if system_msg:
        result.append(system_msg)
    result.append(SystemMessage(content=f"[Conversation Summary]\n{summary_text}"))
    result.extend(recent_messages)

    return result
