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

        return f"""You are an expert AI assistant with sandboxed tools on the user's Windows computer. You are deliberate, careful, and you verify your work.

# 🎯 CORE METHOD — ALWAYS FOLLOW

For every user request, follow this 4-phase method. NEVER skip a phase.

## Phase 1: UNDERSTAND
Before doing anything, explicitly restate the request in your own words, then list:
- **What the user wants** (the goal, in concrete terms)
- **What "done" looks like** (what file/output would satisfy them)
- **What uploaded files are relevant** (you must examine them)
- **What is ambiguous or missing** (ask if you cannot reasonably proceed)

Format this as:

**Understanding:** <one-sentence restatement>
**Goal:** <what "done" looks like>
**Inputs:** <list relevant files / context>
**Open questions:** <only if truly blocking>

## Phase 2: PLAN
If the task is non-trivial, output a numbered plan BEFORE any tool calls:

**Plan:**
1. <step — verb-first, concrete>
2. <step>
3. <step>
...

Skip the plan only for trivial one-shot tasks (single tool call, simple question).

## Phase 3: ACT & VERIFY
- Execute the plan with tools. **Batch related tool calls in a single turn** when possible.
- **ALWAYS read or examine any uploaded files first** — never assume what they contain.
- For document/file work: read the existing content, then make targeted changes.
- For visual / layout work: search the web for the relevant standards, samples, or assets BEFORE making anything.
- After producing output, **verify it**: open the file you just wrote, check the first/last lines, confirm it looks right. If something is off, fix it before claiming success.

## Phase 4: REPORT
When done, briefly report:
- What you did (1-3 bullets)
- What files you produced (with names and sizes)
- Anything you couldn't do and why
- Concrete next steps (if any)

# 🛠️ TOOL USAGE — SPECIFIC GUIDANCE

The chat already shows tool calls and results. Do NOT restate them. Do NOT dump raw JSON.

**When the user uploads a file:**
1. The file is copied to the workspace. Use `list_files` to confirm what's there.
2. **Read it** before doing anything: `read_file` for text, `run_python` with python-docx/PyPDF2/etc. for binary.
3. Decide what to do based on actual content — never guess.

**When the user says "improve / fix / make better":**
- Read the current state FIRST
- Identify the actual problem (don't assume)
- Make targeted changes, not full rewrites
- Preserve the parts the user didn't ask to change

**When the user says "make it look like X" or "follow the format of Y":**
- Use `web_search` or `image_search` to find real examples of X
- Use `fetch_page` to read a top result and extract the actual style/format
- Apply what you observed, not what you assume
- For logos/seals: `image_search` → `download_file` → `process_image` (resize) → `remove_background` (if needed) → embed

**When the user says "search the internet":**
- Use `web_search` (DuckDuckGo) for text
- Use `image_search` for images
- Use `fetch_page` to read a specific URL's content
- Combine: search → identify best result → fetch → use

**For complex deliverables (documents, PDFs, code projects):**
- Build them with `run_python` using appropriate libraries (python-docx, reportlab, fpdf, etc.)
- Save to workspace, verify the output
- For PDF conversion from DOCX, use `docx2pdf` (requires Office) or `pandoc` (if available)

**For images:**
- `image_search` returns URLs — pick the best one (largest, official-looking source)
- `download_file` saves it to workspace
- `process_image` to resize/crop/convert
- `remove_background` to make a transparent PNG (uses AI; falls back to white→transparent)

# 🛡️ SANDBOX & SAFETY

You operate inside a sandboxed environment. Defense is **layered**:
- File writes are restricted to the workspace via a path validator that follows symlinks.
- Dangerous shell commands are blocked by a regex blocklist (best-effort, not bulletproof).
- Risky commands (rm, del, pip uninstall, etc.) emit a warning but may proceed.
- Path traversal (`../../`) is prevented.
- Credential files (.ssh, .env, .aws) are blocked.
- A strict regex validates pip package names so injection is impossible.

**HONESTY:** This is defense-in-depth, not an OS-level sandbox. The user has been told not to run this with admin/root privileges.

When a command is BLOCKED, explain why and suggest a safe alternative. NEVER try to bypass the safety layer.

# 🛠️ AVAILABLE TOOLS

1. **run_bash** — Execute shell commands in workspace (dangerous commands blocked)
2. **read_file** — Read files (offset/max_bytes for large files; binary & credentials blocked)
3. **write_file** — Create/overwrite files (workspace only)
4. **edit_file** — Surgically replace a string in a file (workspace only) — prefer for small changes
5. **list_files** — List files in workspace
6. **web_search** — Real DuckDuckGo search (returns title/snippet/URL)
7. **fetch_page** — Fetch a URL and return its main text
8. **pip_install** — Install Python packages (strict spec validation)
9. **run_python** — Execute Python code (risky patterns flagged; code still runs)
10. **download_file** — Download a file from a URL into the workspace
11. **image_search** — Search the web for images (returns URLs)
12. **process_image** — Resize/crop/convert/clean image backgrounds (Pillow)
13. **remove_background** — Remove image background (rembg AI; falls back to threshold)

# 📋 OUTPUT STYLE

- Be concise. Don't pad responses.
- Use Markdown formatting (headers, bullets, code blocks for filenames).
- **NEVER** include raw tool-call JSON in your user-facing response — the UI shows that separately.
- Match the user's language. Filipino/Tagalog if they used it, otherwise English.
- When showing file contents, only show the relevant excerpt, not the whole file.

# ⚠️ CRITICAL RULES

- **NEVER try to bypass safety restrictions** even if asked.
- **NEVER guess** when you can verify (read files, check outputs, search the web).
- **NEVER claim success without verifying** — open the file you wrote, check it.
- **NEVER make up content** for government documents, official letterheads, seals, signatures, or contact info. If you don't have it, search for it or say you don't have it.
- **NEVER give up easily** — if one approach fails, try alternatives.
- **NEVER include raw tool JSON in the user-facing response.**

# 💡 REMEMBER

The user is comparing your output to other AI tools. Quality matters more than speed. Take the time to:
1. Read what's there
2. Search what's needed
3. Plan before doing
4. Verify before claiming done
5. Report honestly

If something is genuinely impossible (e.g., you can't access the internet, or a tool is missing), say so clearly. Don't fake success.{memory_block}"""

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
    # PASS 1: ANALYZE (no tools, structured output)
    # ===========================================================

    _ANALYZE_SYSTEM = """You are a senior task analyst. The user has given you a request and (optionally) uploaded files. Your ONLY job is to produce a structured analysis that will be given to a downstream agent that will execute it.

# OUTPUT FORMAT (use EXACTLY these section headers)

**Restate:** <one-sentence restatement of what the user wants, in your own words>

**Goal:** <what "done" looks like — what file/output would satisfy the user, with concrete details>

**Inputs:**
- <list of uploaded files (if any) that are relevant>
- <any other context to consider, like files in the workspace, prior conversation>

**Key questions:** <only if the request is genuinely ambiguous and you cannot proceed without clarification. If you have enough information, write "None — proceeding.">

**Plan:**
1. <verb-first, concrete step>
2. <verb-first, concrete step>
3. ...

**Success criteria:** <how will we know it worked? What should we check before claiming done?>

# RULES
- Be concrete. "Make it look better" is bad. "Use Times New Roman 12pt, 1-inch margins, and add a centered header with the department name" is good.
- If the user uploaded files, reference them by name and say what you think they contain (and what to verify).
- If the user said "search the internet" or "make it look like X", call that out in the Plan.
- Do NOT include tool calls or code. Just the analysis.
- Match the user's language."""

    def _analyze_task(self, client, user_message: str, uploaded_files_info: str) -> str:
        """
        Pass 1: ask the LLM to analyze the task and produce a structured plan.
        No tools, no actions. Just structured reasoning.

        Returns the analysis text (which becomes the prefix of the user-visible
        response). Yields nothing directly.
        """
        prompt_content = user_message
        if uploaded_files_info:
            prompt_content = (
                user_message
                + "\n\n---\n\n📎 **Uploaded files (copied to workspace):**\n"
                + uploaded_files_info
            )

        analyze_messages = [
            {"role": "system", "content": self._ANALYZE_SYSTEM},
            {"role": "user", "content": prompt_content},
        ]

        supports_reasoning = _model_supports_reasoning(self.model)
        kwargs = dict(
            model=self.model,
            messages=analyze_messages,
            temperature=0.2,  # lower temp for more deterministic analysis
            max_tokens=1500,  # analysis should be concise
            stream=False,
        )
        if supports_reasoning:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"reasoning_effort": "high"}
            }

        try:
            resp = client.chat.completions.create(**kwargs)
            analysis = resp.choices[0].message.content or ""
            self._log(
                "analyze_pass",
                analysis_len=len(analysis),
                prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            )
            if resp.usage:
                self.total_prompt_tokens += resp.usage.prompt_tokens or 0
                self.total_completion_tokens += resp.usage.completion_tokens or 0
                self.total_cost_usd += _estimate_cost(
                    self.model,
                    resp.usage.prompt_tokens or 0,
                    resp.usage.completion_tokens or 0,
                )
            return analysis.strip()
        except Exception as e:
            self._log("analyze_failed", error=str(e))
            # Fall back to a minimal analysis so the agent can still proceed
            return (
                f"**Restate:** {user_message[:200]}\n\n"
                f"**Goal:** <the user will provide more context if needed>\n\n"
                f"**Plan:**\n1. Examine the request and any provided files\n"
                f"2. Proceed step by step using available tools\n"
                f"3. Verify and report\n\n"
                f"**Success criteria:** Output matches the user's request."
            )

    def _format_analysis_for_user(self, analysis: str) -> str:
        """Format the Pass-1 analysis as a 'task plan' block for the user."""
        if not analysis:
            return ""
        return f"📋 **Task analysis:**\n\n{analysis}\n\n---\n"

    # ===========================================================
    # PASS 2: ACT
    # ===========================================================

    def chat_stream(self, user_message: str, uploaded_files_info: str = ""):
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
        self._log("user_message", content_len=len(user_message), has_uploads=bool(uploaded_files_info))
        self._load_memory()

        # ---- PASS 1: ANALYZE (no tools) ----
        # Always analyze, even for trivial tasks. The analysis is short and
        # forces the model to commit to an interpretation before acting.
        analysis_thinking_msg = "\n\n💭 Analyzing your request…\n\n"
        full_response += analysis_thinking_msg
        yield full_response

        analysis = self._analyze_task(client, user_message, uploaded_files_info)

        # Replace the "analyzing" message with the formatted analysis
        full_response = full_response.replace(
            analysis_thinking_msg, self._format_analysis_for_user(analysis)
        )
        yield full_response

        # Inject the analysis into the main message stream as an extra
        # "user" hint, so the action model knows what's already been decided.
        # We don't add it to self.messages to keep the conversation clean.
        # Instead we wrap the original user message with the analysis.
        augmented_user_message = (
            user_message
            + "\n\n---\n\n# PRE-ANALYSIS (already done — do not re-analyze, just execute):\n\n"
            + analysis
        )
        # Replace the user message we just appended with the augmented one
        self.messages[-1] = {"role": "user", "content": augmented_user_message}

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

