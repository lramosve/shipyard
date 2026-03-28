"""Context compaction — auto-summarize old messages when approaching token limits.

Includes emergency truncation as a fallback when LLM-based summarization fails.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from shipyard.config import settings

logger = logging.getLogger(__name__)

# Rough heuristic: ~4 chars per token
CHARS_PER_TOKEN = 4
# Keep the last N messages intact during compaction
KEEP_RECENT = 10
# Maximum characters for any single tool result message
MAX_TOOL_RESULT_CHARS = 12000
# Hard ceiling: if estimated tokens exceed this, force-truncate before LLM call
HARD_TOKEN_CEILING_RATIO = 0.85  # 85% of context_window_size


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
    Falls back to emergency truncation if LLM summarization fails.
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
        logger.warning(f"LLM summarization failed: {e}. Falling back to emergency truncation.")
        return _emergency_truncate(messages, system_msg, recent_messages)

    # Sanitize recent messages: ensure they don't start with orphaned ToolMessages
    sanitized = _sanitize_recent_messages(recent_messages)

    # Build new message list
    result = []
    if system_msg:
        result.append(system_msg)
    result.append(SystemMessage(content=f"[Conversation Summary]\n{summary_text}"))
    result.extend(sanitized)

    return result


def enforce_hard_ceiling(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Force-truncate messages if they exceed the hard token ceiling.

    This is the last line of defense before an LLM call. It:
    1. Truncates oversized ToolMessage content in-place
    2. Drops oldest non-system messages if still over budget
    3. Guarantees the result fits within context_window_size

    Called from call_llm() AFTER compaction, BEFORE the actual API call.
    """
    ceiling = int(settings.context_window_size * HARD_TOKEN_CEILING_RATIO)
    estimated = estimate_tokens(messages)
    if estimated <= ceiling:
        return messages

    logger.warning(f"Hard ceiling exceeded: ~{estimated} tokens > {ceiling} ceiling. Truncating.")

    # Step 1: Truncate oversized tool results
    messages = _truncate_large_messages(messages)
    estimated = estimate_tokens(messages)
    if estimated <= ceiling:
        logger.info(f"After tool-result truncation: ~{estimated} tokens (under ceiling)")
        return messages

    # Step 2: Drop oldest non-system messages until under ceiling
    system_msg = messages[0] if isinstance(messages[0], SystemMessage) else None
    start = 1 if system_msg else 0

    # Keep dropping from the front (oldest) until we're under budget
    while estimated > ceiling and len(messages) > start + 3:
        dropped = messages.pop(start)
        dropped_content = dropped.content if isinstance(dropped.content, str) else str(dropped.content)
        logger.info(f"Dropped {dropped.type} message ({len(dropped_content)} chars) to fit ceiling")
        # Sanitize: if we dropped an AIMessage with tool_calls, drop its orphaned ToolMessages
        if isinstance(dropped, AIMessage) and dropped.tool_calls:
            tool_call_ids = {tc["id"] for tc in dropped.tool_calls}
            messages = [
                m for i, m in enumerate(messages)
                if not (isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) in tool_call_ids)
                or i < start
            ]
        estimated = estimate_tokens(messages)

    logger.info(f"After ceiling enforcement: ~{estimated} tokens, {len(messages)} messages")
    return messages


def _truncate_large_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Truncate any message content exceeding MAX_TOOL_RESULT_CHARS.

    ToolMessages are the main offender (large file reads), but also
    truncates oversized AI or Human messages.
    """
    result = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > MAX_TOOL_RESULT_CHARS:
            truncated = content[:MAX_TOOL_RESULT_CHARS] + f"\n... (truncated from {len(content)} to {MAX_TOOL_RESULT_CHARS} chars)"
            if isinstance(msg, ToolMessage):
                new_msg = ToolMessage(content=truncated, tool_call_id=msg.tool_call_id, name=getattr(msg, "name", ""))
            elif isinstance(msg, AIMessage):
                new_msg = AIMessage(content=truncated)
            elif isinstance(msg, HumanMessage):
                new_msg = HumanMessage(content=truncated)
            elif isinstance(msg, SystemMessage):
                # Never truncate system messages (they contain the plan)
                new_msg = msg
            else:
                new_msg = msg
            result.append(new_msg)
        else:
            result.append(msg)
    return result


def _emergency_truncate(
    messages: list[BaseMessage],
    system_msg: SystemMessage | None,
    recent_messages: list[BaseMessage],
) -> list[BaseMessage]:
    """Fallback when LLM-based compaction fails.

    Instead of keeping ALL messages (which caused the 1.5M token overflow),
    drop old messages and create a minimal synthetic summary.
    """
    logger.warning("Emergency truncation: dropping old messages to prevent context overflow")

    # Count what we're dropping
    start_idx = 1 if system_msg else 0
    old_messages = messages[start_idx:-KEEP_RECENT] if len(messages) > KEEP_RECENT + start_idx else []
    dropped_count = len(old_messages)

    # Build a minimal summary from old messages (just file paths and key actions)
    file_paths_seen = set()
    actions = []
    for msg in old_messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        # Extract file paths (common patterns)
        import re
        paths = re.findall(r'[A-Za-z]:[/\\][\w/\\.\-]+|/[\w/.\-]{5,}', content[:500])
        file_paths_seen.update(paths[:5])
        # Extract brief action summaries
        if msg.type == "human" and content.startswith("[") and "]" in content:
            tag_end = content.index("]")
            actions.append(content[:min(tag_end + 50, 200)])

    summary_parts = [f"[Emergency Summary] Dropped {dropped_count} old messages due to compaction failure."]
    if file_paths_seen:
        summary_parts.append(f"Files referenced: {', '.join(list(file_paths_seen)[:20])}")
    if actions:
        summary_parts.append(f"Recent actions: {'; '.join(actions[:10])}")
    summary_text = "\n".join(summary_parts)

    # Sanitize and truncate recent messages
    sanitized = _sanitize_recent_messages(recent_messages)
    sanitized = _truncate_large_messages(sanitized)

    result = []
    if system_msg:
        result.append(system_msg)
    result.append(SystemMessage(content=summary_text))
    result.extend(sanitized)

    logger.info(f"Emergency truncation complete: {len(result)} messages, ~{estimate_tokens(result)} tokens")
    return result


def _sanitize_recent_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Remove orphaned ToolMessages from the start of the recent window.

    ToolMessages must follow an AIMessage with matching tool_calls.
    After compaction, the AIMessage might have been summarized away.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # Find the first safe starting point
    start = 0
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            # Check if there's a preceding AIMessage with tool_calls
            has_parent = False
            for j in range(i - 1, -1, -1):
                if isinstance(messages[j], AIMessage) and messages[j].tool_calls:
                    has_parent = True
                    break
                if isinstance(messages[j], (HumanMessage, SystemMessage)):
                    break
            if not has_parent:
                start = i + 1
        else:
            break

    return messages[start:]
