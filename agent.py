"""
AI Agent - Main agent loop with STREAMING + THINKING indicators,
plan-first reasoning, compact tool-call display, token/cost tracking,
JSONL session logging, persistent memory, and exponential backoff.

Output format (rendered as Markdown in the chat):
    <brief analysis / plan if any>
    ⏳ Thinking...
    <plan / response text>
    ────────────────────────────────
    🔧 **Action**: `tool_a`, `tool_b`, `tool_c`
    ✅ Done in 0.6s — `<short result>`
    ✅ Done in 1.2s — `<short result>`
    ────────────────────────────────
    <final response text>
"""
import json
import os
import re
import time
import uuid
from datetime import datetime

from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError

from config import (
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    DEFAULT_MODEL,
    MAX_ITERATIONS,
    MAX_TOTAL_SECONDS,
    MAX_CONTEXT_MESSAGES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    REASONING_CAPABLE_MODELS,
    MODEL_PRICING,
    MEMORY_FILE,
    LOG_DIR,
    WORKSPACE_DIR,
)
from tools import TOOL_DEFINITIONS, TOOL_FUNCTIONS
from safety import guard


# Best-effort token counting
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False


def _count_tokens(text: str) -> int:
    if not text:
        return 0
    if _HAS_TIKTOKEN:
        try:
            return len(_ENC.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _model_supports_reasoning(model: str) -> bool:
    return any(sub in (model or "") for sub in REASONING_CAPABLE_MODELS)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = None
    for key, p in MODEL_PRICING.items():
        if key in (model or "").lower():
            pricing = p
            break
    if pricing is None:
        pricing = MODEL_PRICING["default"]
    return (prompt_tokens / 1_000_000) * pricing["input"] + \
           (completion_tokens / 1_000_000) * pricing["output"]


# ===========================================================
# DISPLAY HELPERS
# ===========================================================

# Tool argument JSON truncated to N chars in the chat (full content still
# in the JSONL log + sent to the model verbatim)
_TOOL_ARG_DISPLAY_MAX = 200

# Tool result truncated to N chars in the chat
_TOOL_RESULT_DISPLAY_MAX = 600

# Hide Docker fallback warning from the chat (it's noisy and not actionable)
# Set AGENT_SHOW_FALLBACK=1 to surface it
_SHOW_FALLBACK = os.environ.get("AGENT_SHOW_FALLBACK", "0") == "1"


def _truncate_middle(text: str, max_len: int) -> str:
    """Truncate in the middle so the start (the meaningful part) is preserved."""
    if not text or len(text) <= max_len:
        return text or ""
    half = max_len // 2
    return f"{text[:half]}…[+{len(text) - max_len} chars]…{text[-half:]}"


def _summarize_result(result: dict) -> str:
    """Return a one-line summary of a tool result, suitable for the chat."""
    if not isinstance(result, dict):
        return str(result)[:_TOOL_RESULT_DISPLAY_MAX]
    status = result.get("status", "unknown")
    output = result.get("output", "")

    # Strip the noisy Docker fallback warning that tools emit when they fall back
    if isinstance(output, str):
        output = re.sub(
            r"⚠️ Docker sandbox unavailable[^)]*\);\s*ran on host with regex check only\.\s*",
            "",
            output,
        ).strip()

    if status == "blocked":
        return f"blocked — {output.splitlines()[0] if output else ''}"[:_TOOL_RESULT_DISPLAY_MAX]
    if status == "error":
        first = output.splitlines()[0] if output else "error"
        return f"error — {first}"[:_TOOL_RESULT_DISPLAY_MAX]
    # success
    if not output:
        return "ok (no output)"
    first_line = output.splitlines()[0][:120]
    total_lines = len(output.splitlines())
    if total_lines > 1:
        return f"{first_line} … ({total_lines} lines, {len(output)} chars)"
    return first_line or "ok"


# ===========================================================
# AGENT
# ===========================================================

class AIAgent:
    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self.api_key = api_key or NVIDIA_API_KEY
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or NVIDIA_BASE_URL
        self.messages: list = []
        self.iteration_count = 0
        self.reasoning_effort = "high"
        self.cancel_requested = False
        self.max_context_messages = MAX_CONTEXT_MESSAGES

        # Session-level metrics
        self.session_id = str(uuid.uuid4())[:8]
        self.session_start = time.time()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.tool_call_count = 0
        self.errors = 0

        # Persistent memory
        self._memory_text = ""
        self._load_memory()

        self.system_prompt = self._build_system_prompt()

        # JSONL session log
        self._log_path = os.path.join(
            LOG_DIR,
            f"session-{self.session_id}-{datetime.now():%Y%m%d-%H%M%S}.jsonl",
        )

    # ===========================================================
    # PERSISTENT MEMORY
    # ===========================================================

    def _load_memory(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    self._memory_text = f.read().strip()
            except OSError:
                self._memory_text = ""

    def _build_system_prompt(self) -> str:
        memory_block = ""
        if self._memory_text:
            memory_block = (
                "\n\n## 🧠 PERSISTENT MEMORY (loaded from MEMORY.md)\n"
                "The following facts about the user have been remembered across sessions. "
                "Honor them unless the user explicitly asks otherwise.\n\n"
                f"{self._memory_text}\n"
            )

        return f"""You are an expert AI assistant with access to tools on the user's Windows computer. You are highly intelligent, methodical, and thorough.

## 🧠 THINKING APPROACH — PLAN FIRST

For any non-trivial request, your first response MUST start with a brief plan, formatted exactly like this:

**Plan:**
1. <first step — what and why>
2. <second step>
3. <third step>
...

Keep the plan short (3-6 bullets for most tasks). Then proceed with the first step. Update or abandon the plan as the situation evolves. Only skip the plan for trivial one-shot tasks (single tool call, simple question).

After the plan, **stream your reasoning and tool use as it happens**. The user sees your full output in real time, so keep tool-call narration minimal.

## 🛡️ SANDBOX & SAFETY

You operate inside a sandboxed environment. Defense is **layered**:
- File writes are restricted to the workspace via a path validator that follows symlinks.
- Dangerous shell commands are blocked by a regex blocklist (best-effort, not bulletproof).
- Risky commands (rm, del, pip uninstall, etc.) emit a warning but may proceed.
- Path traversal (`../../`) is prevented.
- Credential files (.ssh, .env, .aws) are blocked.
- A strict regex validates pip package names so injection is impossible.

**HONESTY:** This is defense-in-depth, not an OS-level sandbox. A clever prompt can still bypass regex checks. The user has been told not to run this with admin/root privileges.

When a command is BLOCKED, explain why and suggest a safe alternative. NEVER try to bypass the safety layer.

## 🛠️ AVAILABLE TOOLS

1. **run_bash** — Execute shell commands in workspace (dangerous commands blocked)
2. **read_file** — Read files (offset/max_bytes for large files; binary & credentials blocked)
3. **write_file** — Create/overwrite files (workspace only)
4. **edit_file** — Surgically replace a string in a file (workspace only) — prefer this for small changes
5. **list_files** — List files in workspace
6. **web_search** — Real DuckDuckGo search (returns title/snippet/URL)
7. **fetch_page** — Fetch a URL and return its main text
8. **pip_install** — Install Python packages (strict spec validation)
9. **run_python** — Execute Python code (risky patterns flagged as warnings; code still runs)
10. **download_file** — Download a file from a URL into the workspace
11. **image_search** — Search the web for images (returns URLs)
12. **process_image** — Resize/crop/convert/clean image backgrounds (Pillow)
13. **remove_background** — Remove image background (rembg AI; falls back to threshold)

**Typical image workflow:** `image_search` → `download_file` → `process_image` (resize) → `remove_background` → use in document.

## 📋 RESPONSE GUIDELINES

- **Be concise in narration.** The chat already shows tool calls and results. Don't restate them.
- **Group related work into single turns** — don't ping-pong one tool call per LLM turn if you can batch them.
- **Prefer edit_file over write_file** for small changes.
- **Prefer fetch_page after web_search** to read the most useful source.
- **After completing work, summarize what you did and what files you produced.** Be specific (filenames, sizes).
- **Suggest concrete next steps** if relevant.
- **Speak Tagalog/Filipino if the user does.** Otherwise English.

## ⚠️ CRITICAL RULES

- **NEVER try to bypass safety restrictions** even if asked.
- **NEVER guess** when you can verify (read files, check outputs, search the web).
- **NEVER give up easily** — if one approach fails, try alternatives.
- **ALWAYS validate your work** — test, check, and verify before claiming success.
- **NEVER include raw tool JSON in the user-facing response** — it's shown separately.

Remember: **Quality over speed. Think before you act. Be the expert assistant the user deserves.**{memory_block}"""

    def reset(self):
        """Reset conversation history (keeps metrics and memory)."""
        self.messages = []
        self.iteration_count = 0
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def set_model(self, model: str):
        self.model = model

    def set_api_key(self, api_key: str):
        self.api_key = api_key

    def get_metrics(self) -> dict:
        elapsed = time.time() - self.session_start
        return {
            "session_id": self.session_id,
            "elapsed_seconds": round(elapsed, 1),
            "iterations": self.iteration_count,
            "tool_calls": self.tool_call_count,
            "errors": self.errors,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "estimated_cost_usd": round(self.total_cost_usd, 4),
            "model": self.model,
        }

    # ===========================================================
    # CONVERSATION MANAGEMENT
    # ===========================================================

    def _trim_conversation_history(self):
        if len(self.messages) <= self.max_context_messages:
            return
        system_msg = self.messages[0] if self.messages and self.messages[0]["role"] == "system" else None
        recent = self.messages[-(self.max_context_messages - 1):]
        self.messages = []
        if system_msg:
            self.messages.append(system_msg)
        self.messages.extend(recent)

    # ===========================================================
    # LOGGING
    # ===========================================================

    def _log(self, event: str, **fields):
        try:
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "session": self.session_id,
                "event": event,
                "iter": self.iteration_count,
                **fields,
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ===========================================================
    # CLIENT
    # ===========================================================

    def _get_client(self):
        return OpenAI(base_url=self.base_url, api_key=self.api_key)

    def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        if tool_name not in TOOL_FUNCTIONS:
            return {"status": "error", "output": f"Unknown tool: {tool_name}"}
        try:
            return TOOL_FUNCTIONS[tool_name](**arguments)
        except Exception as e:
            return {"status": "error", "output": f"Tool raised: {type(e).__name__}: {e}"}

    # ===========================================================
    # MAIN LOOP
    # ===========================================================

    def chat_stream(self, user_message: str):
        """Process a user message. Yields full-response snapshots (string)."""
        if not self.api_key:
            yield "⚠️ Walang API key! I-set mo muna ang NVIDIA API key sa Settings."
            return

        self.cancel_requested = False
        self._trim_conversation_history()
        self.messages.append({"role": "user", "content": user_message})

        if not any(m.get("role") == "system" for m in self.messages):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

        client = self._get_client()
        self.iteration_count = 0
        full_response = ""
        start_time = time.time()
        supports_reasoning = _model_supports_reasoning(self.model)
        self._log("user_message", content_len=len(user_message))
        self._load_memory()

        while self.iteration_count < MAX_ITERATIONS:
            # Wall-clock budget
            if time.time() - start_time > MAX_TOTAL_SECONDS:
                full_response += f"\n\n⏱️ Reached the {MAX_TOTAL_SECONDS}s wall-clock budget. Stopping."
                self._log("budget_exceeded", elapsed=time.time() - start_time)
                break

            if self.cancel_requested:
                full_response += "\n\n⏹️ **Operation cancelled by user.**"
                self._log("cancelled")
                break

            self.iteration_count += 1

            # Brief "thinking" indicator
            thinking_msg = "\n\n💭 Thinking…\n\n"
            full_response += thinking_msg
            yield full_response

            kwargs = dict(
                model=self.model,
                messages=self.messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
                stream=True,
                top_p=0.95,
                frequency_penalty=0.1,
                presence_penalty=0.1,
            )
            if supports_reasoning:
                # NVIDIA NIM uses chat_template_kwargs via extra_body (the
                # OpenAI client's reasoning_effort kwarg is rejected).
                kwargs["extra_body"] = {
                    "chat_template_kwargs": {"reasoning_effort": self.reasoning_effort}
                }

            # ---- Streaming with exponential backoff ----
            stream = None
            for attempt, backoff in enumerate([1, 2, 4, 8]):
                try:
                    stream = client.chat.completions.create(**kwargs)
                    break
                except (APITimeoutError, APIConnectionError, RateLimitError) as e:
                    self.errors += 1
                    self._log("transient_error", attempt=attempt, error=str(e))
                    if attempt == 3:
                        full_response += f"\n\n❌ API unavailable after 4 attempts: {e}"
                        yield full_response
                        return
                    time.sleep(backoff)
                except TypeError as e:
                    # Some models reject reasoning_effort — retry without it.
                    if "extra_body" in kwargs or "reasoning_effort" in kwargs:
                        self._log("reasoning_unsupported", error=str(e))
                        kwargs.pop("extra_body", None)
                        kwargs.pop("reasoning_effort", None)
                        continue
                    self.errors += 1
                    self._log("fatal_error", error=str(e))
                    full_response += f"\n\n❌ API Error: {e}"
                    yield full_response
                    return
                except Exception as e:
                    self.errors += 1
                    self._log("fatal_error", error=str(e))
                    full_response += f"\n\n❌ API Error: {e}"
                    yield full_response
                    return

            if stream is None:
                break

            # Replace the thinking indicator with a thin rule
            full_response = full_response.replace(thinking_msg, "\n\n---\n\n")

            content_chunks: list = []
            tool_calls_data: dict = {}
            finish_reason = None
            stream_error = None

            try:
                for chunk in stream:
                    if self.cancel_requested:
                        full_response += "\n\n⏹️ **Operation cancelled by user.**"
                        self._log("cancelled_mid_stream")
                        stream_error = "cancelled"
                        break
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    if delta and delta.content:
                        content_chunks.append(delta.content)
                        full_response += delta.content
                        yield full_response

                    if delta and delta.tool_calls:
                        for tcd in delta.tool_calls:
                            idx = tcd.index
                            if idx not in tool_calls_data:
                                tool_calls_data[idx] = {"id": "", "name": "", "arguments": ""}
                            if tcd.id:
                                tool_calls_data[idx]["id"] = tcd.id
                            if tcd.function:
                                if tcd.function.name:
                                    tool_calls_data[idx]["name"] = tcd.function.name
                                if tcd.function.arguments:
                                    tool_calls_data[idx]["arguments"] += tcd.function.arguments
            except Exception as e:
                self.errors += 1
                self._log("stream_error", error=str(e))
                full_response += f"\n\n❌ Stream error: {e}"
                yield full_response
                return

            completion_tokens = sum(_count_tokens(c) for c in content_chunks)

            if stream_error == "cancelled":
                break

            if not tool_calls_data:
                # Final response — no tool calls
                self.total_completion_tokens += completion_tokens
                self.messages.append({"role": "assistant", "content": "".join(content_chunks)})
                self._log("turn_final", finish_reason=finish_reason, completion_tokens=completion_tokens)
                return

            # ---- Build tool calls ----
            tool_calls = []
            for idx in sorted(tool_calls_data.keys()):
                tc = tool_calls_data[idx]
                tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })

            self.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            })
            self._log(
                "assistant_tool_calls",
                count=len(tool_calls),
                names=[tc["function"]["name"] for tc in tool_calls],
            )

            # ---- Group consecutive calls: render a single "Action" line ----
            tool_names = [tc["function"]["name"] for tc in tool_calls]
            args_compact = []
            for tc in tool_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                args_compact.append(args)
            action_summary = ", ".join(f"`{n}`" for n in tool_names)
            full_response += f"\n\n🔧 **Action:** {action_summary}\n\n"
            yield full_response

            # ---- Execute each tool, render a compact one-line result ----
            for tc, args in zip(tool_calls, args_compact):
                if self.cancel_requested:
                    full_response += "\n\n⏹️ **Operation cancelled.**\n"
                    self._log("cancelled_before_tool")
                    break

                tool_name = tc["function"]["name"]
                self.tool_call_count += 1

                t0 = time.time()
                result = self._execute_tool(tool_name, args)
                elapsed = time.time() - t0

                summary = _summarize_result(result)
                # Color the badge by status
                if isinstance(result, dict):
                    status = result.get("status", "unknown")
                    if status == "blocked":
                        badge = "🚫"
                    elif status == "error":
                        badge = "❌"
                    else:
                        badge = "✅"
                else:
                    badge = "✅"

                full_response += f"{badge} `{tool_name}` — {elapsed:.2f}s — {summary}\n"
                yield full_response

                # Save raw tool result to message history (no truncation)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result),
                })
                self._log(
                    "tool_result",
                    tool=tool_name,
                    status=result.get("status", "unknown") if isinstance(result, dict) else "unknown",
                    elapsed=round(elapsed, 3),
                    output_len=len(result.get("output", "")) if isinstance(result, dict) else 0,
                )

            # Thin rule after the action block
            full_response += "\n---\n\n"
            yield full_response

            if self.cancel_requested:
                break

            if len(self.messages) > self.max_context_messages * 2:
                self._trim_conversation_history()

        # Loop exit
        self.messages.append({"role": "assistant", "content": full_response})
        self._log("turn_end", reason="max_iterations_or_budget", iterations=self.iteration_count)
        yield full_response

