"""Context compaction for long tasks.

When the conversation nears the model's context window, the older middle of
the conversation is folded into one summary message, so a long task keeps
going instead of hitting the limit or silently losing how it started.

Kept word for word:
- the system prompt
- the original request of the task
- the most recent steps, with every tool call kept next to its results

Summarised: everything in between, into progress so far, a requirement
checklist with evidence for each item marked done, files touched and what is
left. An earlier summary is folded into the next one, so the checklist
survives every compaction.

Light by design (it runs on an 8 GB laptop): counting uses tiktoken, already a
dependency, or len/4; the summary is one short non-streaming call; and when
that call fails a plain summary is built locally, with no model at all.

Settings (environment):
  AGENT_CONTEXT_TOKENS  the context window to stay within (default 32000)
  AGENT_COMPACT_AT      compact at this share of it (default 0.75)
  AGENT_COMPACT_TARGET  shrink to this share of it when compacting (default 0.5)
  AGENT_COMPACT_KEEP    recent messages kept as they are (default 8)
"""
import json
import os
from dataclasses import dataclass

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken is in requirements
    _ENC = None

SUMMARY_TAG = "[Context compacted]"
SUMMARY_RESERVE = 900  # room kept for the summary itself (its max_tokens)
MIN_FOLD_TOKENS = 600  # fold only when the older part is worth a summary call

SUMMARY_PREFACE = (
    SUMMARY_TAG + " The older part of this conversation was summarized to stay within "
    "the model's context window. The original request is kept word for word above; the "
    "most recent steps follow below. Full tool outputs are still saved: call recall_step "
    "with a step number to read one again. Continue the task from here and do not redo "
    "work the checklist marks done.\n\n"
)

SUMMARIZER_SYSTEM = (
    "You compress the working history of an AI coding agent so it can keep going on a "
    "long task. Write only these sections, in plain text:\n"
    "REQUEST: the user's request in one or two lines (the full original stays in the "
    "conversation).\n"
    "CHECKLIST: every requirement of the request and of any earlier checklist, one per "
    "line, as '[x] item - evidence' (the tool result, file or output that shows it is "
    "done) or '[ ] item - what is left'. Never drop an item from an earlier checklist. "
    "Never mark an item done without evidence in the history.\n"
    "DONE SO FAR: the key actions and their results, with file paths, commands and exact "
    "values that matter later.\n"
    "FILES: files created or changed, one per line.\n"
    "OPEN: errors not fixed yet, decisions made, the next step.\n"
    "At most 350 words. No preamble."
)


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def context_tokens() -> int:
    return _int_env("AGENT_CONTEXT_TOKENS", 32000)


def threshold_ratio() -> float:
    try:
        ratio = float(os.environ.get("AGENT_COMPACT_AT", "0.75"))
    except ValueError:
        return 0.75
    return ratio if 0.2 <= ratio <= 0.95 else 0.75


def target_ratio() -> float:
    """Share of the window to shrink to, well under the trigger so a long task
    doesn't compact again every step (each compaction is a model call)."""
    trigger = threshold_ratio()
    try:
        ratio = float(os.environ.get("AGENT_COMPACT_TARGET", "0.5"))
    except ValueError:
        ratio = 0.5
    if not 0.1 <= ratio < trigger:
        ratio = min(0.5, trigger * 0.66)
    return ratio


def keep_recent() -> int:
    return max(2, _int_env("AGENT_COMPACT_KEEP", 8))


def count_text(text) -> int:
    if not text:
        return 0
    text = str(text)
    if _ENC is not None:
        try:
            return len(_ENC.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _text_of(content) -> str:
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
    return content if isinstance(content, str) else ("" if content is None else str(content))


def message_tokens(message: dict) -> int:
    content = message.get("content")
    n = count_text(_text_of(content))
    if isinstance(content, list):
        n += 1000 * sum(1 for p in content if isinstance(p, dict) and p.get("type") != "text")
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        n += count_text(fn.get("name", "")) + count_text(fn.get("arguments", ""))
    return n + 4


def estimate(messages, extra_tokens: int = 0) -> int:
    """Tokens the next request will carry: the messages plus `extra_tokens`
    (the tool schemas, sent with every call)."""
    return extra_tokens + sum(message_tokens(m) for m in messages)


def needs_compaction(messages, extra_tokens: int = 0, max_messages: int = None) -> bool:
    if max_messages and len(messages) > max_messages:
        return True
    return estimate(messages, extra_tokens) >= context_tokens() * threshold_ratio()


def is_summary(message: dict) -> bool:
    return message.get("role") == "user" and _text_of(message.get("content")).startswith(SUMMARY_TAG)


def guess_request(messages):
    """The latest real user message: not an injected note in [brackets], not a summary."""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = m["content"].lstrip()
            if text and not text.startswith("["):
                return m
    return None


def _recent_start(messages, base: int, keep: int) -> int:
    start = max(base, len(messages) - keep)
    # Never open the kept window on tool results: pull back to the call they answer.
    while start > base and messages[start].get("role") == "tool":
        start -= 1
    return start


def _render(message: dict) -> str:
    role = message.get("role")
    text = _text_of(message.get("content"))
    if is_summary(message):
        return "EARLIER SUMMARY (carry its checklist forward):\n" + text[len(SUMMARY_TAG):].strip()
    if role == "assistant" and message.get("tool_calls"):
        calls = []
        for call in message["tool_calls"]:
            fn = call.get("function") or {}
            calls.append(f"{fn.get('name', '?')}({str(fn.get('arguments', ''))[:400]})")
        head = ("ASSISTANT: " + text[:600] + "\n") if text.strip() else ""
        return head + "ASSISTANT called: " + "; ".join(calls)
    if role == "tool":
        return "RESULT: " + text[:1200]
    if role == "assistant":
        return "ASSISTANT: " + text[:1500]
    return "USER: " + text[:2000]


def build_prompt(request, folded, notes: str = "", limit: int = None):
    """Messages for the summary call, the transcript bounded to about half the window."""
    limit = limit or context_tokens()
    budget = max(4000, limit * 2)  # characters, about half the window in tokens
    entries = [_render(m) for m in folded]
    keep = [is_summary(m) for m in folded]
    total = sum(len(e) for e in entries)
    dropped = 0
    i = 0
    while total > budget and i < len(entries):
        if not keep[i] and entries[i]:
            total -= len(entries[i])
            entries[i] = ""
            dropped += 1
        i += 1
    lines = [e for e in entries if e]
    if dropped:
        lines.insert(0, f"(... {dropped} earliest steps left out for length ...)")
    req = _text_of(request.get("content")) if request else "(not available)"
    user = ("Original request:\n" + req[:4000] + "\n\nHistory to compress:\n" + "\n\n".join(lines))
    if notes:
        user += "\n\nTracked by the agent:\n" + notes
    return [{"role": "system", "content": SUMMARIZER_SYSTEM},
            {"role": "user", "content": user}]


def fallback_summary(request, folded, notes: str = "") -> str:
    """A plain summary built without the model, when the summary call fails."""
    results = {}
    for m in folded:
        if m.get("role") == "tool":
            status = "done"
            try:
                data = json.loads(m.get("content") or "")
                if isinstance(data, dict) and data.get("status"):
                    status = str(data["status"])
            except (ValueError, TypeError):
                pass
            results[m.get("tool_call_id")] = status
    actions, files = [], []
    for m in folded:
        if is_summary(m):
            actions.append("earlier summary: " + _text_of(m["content"])[len(SUMMARY_TAG):].strip()[:1500])
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = str(fn.get("arguments", ""))
            try:
                path = json.loads(args).get("path")
            except (ValueError, AttributeError):
                path = None
            if path and fn.get("name") in ("write_file", "edit_file", "create_file", "append_file") \
                    and path not in files:
                files.append(path)
            actions.append(f"{fn.get('name', '?')}({args[:160]}) -> {results.get(call.get('id'), 'no result')}")
    req = _text_of(request.get("content")) if request else ""
    out = ["REQUEST: " + req[:300].replace("\n", " "),
           "DONE SO FAR (automatic list, the model summary was unavailable):"]
    out += ["- " + a for a in actions[-25:]] or ["- (no tool calls)"]
    if files:
        out.append("FILES: " + ", ".join(files))
    if notes:
        out.append(notes)
    return "\n".join(out)


@dataclass
class Result:
    messages: list
    before: int
    after: int
    folded: int
    fallback: bool


def _shrink_recent(window, last_group_start: int):
    """Shorten big tool results in the kept window, all but the latest step's."""
    out = []
    for i, m in enumerate(window):
        content = m.get("content")
        if (i < last_group_start and m.get("role") == "tool" and isinstance(content, str)
                and len(content) > 1500):
            m = dict(m, content=content[:1500] + f"\n[... {len(content) - 1500} chars cut at compaction; recall_step has the full output ...]")
        out.append(m)
    return out


def compact(messages, summarize, request=None, notes: str = "", extra_tokens: int = 0,
            keep: int = None, limit: int = None):
    """Fold the older middle of `messages` into one summary.

    `summarize(prompt_messages) -> str` makes the summary (one model call); if it
    fails or returns nothing, a plain summary is built locally. Returns a Result,
    or None when there is too little to fold.
    """
    keep = keep or keep_recent()
    limit = limit or context_tokens()
    if not messages:
        return None
    base = 1 if messages[0].get("role") == "system" else 0
    head = messages[:base]
    start = _recent_start(messages, base, keep)
    # Fold more than the default when the kept window alone would leave the
    # context near the trigger: aim for target_ratio(), never past the latest step.
    last_group = len(messages) - 1
    while last_group > base and messages[last_group].get("role") == "tool":
        last_group -= 1
    budget = limit * target_ratio()
    while (start < last_group
           and estimate(head + messages[start:], extra_tokens) + SUMMARY_RESERVE > budget):
        nxt = start + 1
        while nxt < last_group and messages[nxt].get("role") == "tool":
            nxt += 1
        start = nxt
    older = messages[base:start]
    request = request if request is not None else guess_request(messages)
    request_in_older = request is not None and any(m is request for m in older)
    folded = [m for m in older if m is not request]
    if len(folded) < 2 or estimate(folded) < MIN_FOLD_TOKENS:
        return None  # too little to fold: a summary would not save anything
    before = estimate(messages, extra_tokens)
    text, fallback = "", False
    try:
        text = (summarize(build_prompt(request, folded, notes, limit)) or "").strip()
    except Exception:
        text = ""
    if len(text) < 20:
        text, fallback = fallback_summary(request, folded, notes), True
    elif notes:
        text += "\n\n" + notes
    summary = {"role": "user", "content": SUMMARY_PREFACE + text}
    window = messages[start:]
    new = head + ([request] if request_in_older else []) + [summary] + window
    after = estimate(new, extra_tokens)
    if after >= limit * threshold_ratio():
        last = len(window)
        while last > 0 and window[last - 1].get("role") == "tool":
            last -= 1
        last = max(0, last - 1)
        new = head + ([request] if request_in_older else []) + [summary] + _shrink_recent(window, last)
        after = estimate(new, extra_tokens)
    if after >= before:
        return None  # the summary is no smaller than what it replaces
    return Result(new, before, after, len(folded), fallback)
