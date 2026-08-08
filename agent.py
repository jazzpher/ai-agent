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
from sandbox_session import session_manager


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
2. **View it** before doing anything: `view_file` for docx/pdf/pptx/images/xlsx, `read_file` for text files.
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
2. **read_file** — Read text files (offset/max_bytes for large files; binary & credentials blocked)
3. **view_file** — View/preview ANY file: docx, pdf, pptx, xlsx, images, csv, text. USE THIS for binary files!
4. **write_file** — Create/overwrite files (workspace only)
5. **edit_file** — Surgically replace a string in a file (workspace only) — prefer for small changes
6. **list_files** — List files in workspace
7. **web_search** — Real DuckDuckGo search (returns title/snippet/URL)
8. **fetch_page** — Fetch a URL and return its main text
9. **pip_install** — Install Python packages in TEMPORARY sandbox (session-only, host untouched)
10. **run_python** — Execute Python code (risky patterns flagged; code still runs)
11. **download_file** — Download a file from a URL into the workspace
12. **image_search** — Search the web for images (returns URLs)
13. **process_image** — Resize/crop/convert/clean image backgrounds (Pillow)
14. **remove_background** — Remove image background (rembg AI; falls back to threshold)

# 📦 TEMPORARY SANDBOX & PACKAGE INSTALLATION

All code execution (run_bash, run_python, pip_install) happens in a **temporary per-session sandbox**:
- Packages installed via `pip_install` are available for the rest of the session
- They are **NOT** installed on the user's host machine
- Everything is cleaned up when the session ends
- Core packages are pre-installed: Pillow, python-docx, python-pptx, openpyxl, PyPDF2, pdfplumber, requests, beautifulsoup4, pandas

When you need a package that isn't pre-installed, just `pip_install` it — the user won't be affected.

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

    def cleanup_sandbox(self):
        """Destroy the session's sandbox (called on clear/reset)."""
        session_manager.destroy(self.session_id)

    def set_model(self, model: str):
        self.model = model

    def set_api_key(self, api_key: str):
        self.api_key = api_key

    def get_metrics(self) -> dict:
        elapsed = time.time() - self.session_start
        sandbox_info = {}
        try:
            sb = session_manager.get_or_create(self.session_id)
            sandbox_info = sb.get_status()
        except Exception:
            pass
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
            "sandbox_mode": sandbox_info.get("mode", "unknown"),
            "sandbox_packages": len(sandbox_info.get("packages_installed", [])),
        }

    # ===========================================================
    # CONVERSATION MANAGEMENT
    # ===========================================================

    def _trim_conversation_history(self):
        """
        Keep `self.messages` bounded so the LLM context doesn't grow unbounded.

        Strategy:
        1. Always keep the system message (slot 0).
        2. Keep the most recent N messages.
        3. For each surviving tool result, if it's over _LARGE_TOOL_RESULT_CHARS,
           replace it with a placeholder so it doesn't blow up the context.
        4. For each surviving assistant message, if it's over _LARGE_ASSISTANT_CHARS,
           replace it with a brief summary.
        """
        # Bound 1: Truncate oversized tool result payloads
        _LARGE_TOOL_RESULT_CHARS = 4000
        for m in self.messages:
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                if len(m["content"]) > _LARGE_TOOL_RESULT_CHARS:
                    m["content"] = (
                        m["content"][:_LARGE_TOOL_RESULT_CHARS]
                        + f"\n\n[... truncated {len(m['content']) - _LARGE_TOOL_RESULT_CHARS} chars ...]"
                    )

        # Bound 2: Truncate oversized assistant messages
        _LARGE_ASSISTANT_CHARS = 2000
        for m in self.messages:
            if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                if len(m["content"]) > _LARGE_ASSISTANT_CHARS:
                    m["content"] = (
                        m["content"][:_LARGE_ASSISTANT_CHARS]
                        + f"\n\n[... truncated {len(m['content']) - _LARGE_ASSISTANT_CHARS} chars ...]"
                    )

        # Bound 3: Drop oldest messages if still over the threshold
        if len(self.messages) <= self.max_context_messages:
            return
        system_msg = None
        if self.messages and self.messages[0].get("role") == "system":
            system_msg = self.messages[0]
        recent = self.messages[-(self.max_context_messages - 1):] if system_msg else self.messages[-self.max_context_messages:]
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
            # Inject session_id so tools use the session's sandbox
            arguments["session_id"] = self.session_id
            return TOOL_FUNCTIONS[tool_name](**arguments)
        except Exception as e:
            return {"status": "error", "output": f"Tool raised: {type(e).__name__}: {e}"}

    # ===========================================================
    # AGENTIC LOOP — self-correction after major actions
    # ===========================================================

    _EVALUATE_SYSTEM = """You are a senior reviewer. The agent just took an action toward the user's goal. Your job is to evaluate whether the action was successful and worth keeping, OR whether it needs improvement.

# OUTPUT FORMAT (use EXACTLY one of these three)

If the action is **good enough** to proceed:
```
KEEP
```

If the action **needs improvement** (or a follow-up fix):
```
FIX
Issue: <what's wrong or missing>
Fix: <concrete correction to make next>
```

If the action reveals the agent is **off track entirely**:
```
REPLAN
Reason: <why the current approach is wrong>
New direction: <what to do instead>
```

# RULES
- Be practical. Don't nitpick minor formatting; only flag real issues.
- "Issue" should be specific and actionable, not vague.
- "Fix" should be a single concrete next step, not a full re-plan.
- "REPLAN" is rare — only when the fundamental approach is wrong.
- Don't propose improvements the user didn't ask for. Stay focused on the goal.
- Match the user's language."""

    def _evaluate_action(self, client, goal: str, action_summary: str, result_text: str) -> str:
        """
        After a major action, ask the LLM to evaluate if the action was good.
        Returns one of: "KEEP", "FIX\n...", or "REPLAN\n..."
        """
        eval_messages = [
            {"role": "system", "content": self._EVALUATE_SYSTEM},
            {"role": "user", "content": (
                f"**Goal:** {goal}\n\n"
                f"**Action just taken:** {action_summary}\n\n"
                f"**Result / output:**\n```\n{result_text[:2000]}\n```\n\n"
                f"Evaluate the action. Is it good enough to proceed, or does it need a fix?"
            )},
        ]
        supports_reasoning = _model_supports_reasoning(self.model)
        kwargs = dict(
            model=self.model,
            messages=eval_messages,
            temperature=0.1,  # very low for deterministic review
            max_tokens=400,
            stream=False,
        )
        if supports_reasoning:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"reasoning_effort": "medium"}
            }
        try:
            resp = client.chat.completions.create(**kwargs)
            evaluation = (resp.choices[0].message.content or "").strip()
            if resp.usage:
                self.total_prompt_tokens += resp.usage.prompt_tokens or 0
                self.total_completion_tokens += resp.usage.completion_tokens or 0
                self.total_cost_usd += _estimate_cost(
                    self.model,
                    resp.usage.prompt_tokens or 0,
                    resp.usage.completion_tokens or 0,
                )
            self._log("evaluate", verdict=evaluation.splitlines()[0] if evaluation else "empty")
            return evaluation
        except Exception as e:
            self._log("evaluate_failed", error=str(e))
            return "KEEP"  # don't block on evaluation failures

    def _should_analyze(self, user_message: str, uploaded_files_info: str) -> bool:
        """
        Decide whether the analyze pass is worth the cost.

        Run when:
        - Message contains imperative action verbs (make, build, fix, etc.)
        - Multi-sentence or has "and"/"then" suggests multi-step
        - Files were uploaded
        - Message is long

        Skip when:
        - Very short message + no uploads + no action verbs (likely a quick question)
        """
        msg = user_message.strip()
        msg_lower = msg.lower()

        # Always analyze when files are uploaded (need to look at them first)
        if uploaded_files_info:
            return True

        # Action keywords that warrant upfront planning
        action_keywords = (
            "build", "create", "make ", "make.", "fix", "improve", "rewrite",
            "design", "implement", "convert", "transform", "edit",
            "search", "find ", "look up", "download", "install",
            "analyze", "extract", "summarize", "translate", "format",
            "update", "add ", "remove", "delete", "modify", "change",
            "refactor", "gawing", "gawa",
            "ayusin", "hanapin", "i-download", "i-install", "i-fix",
        )
        if any(kw in msg_lower for kw in action_keywords):
            return True

        # Multi-sentence or has "and"/"then" suggests multi-step
        if len(msg) > 120 or " and " in msg_lower or " then " in msg_lower:
            return True

        # If it ends with "?" and is short, likely a question — skip
        if msg.endswith("?") and len(msg) < 80:
            return False

        # Default: skip for very short messages without action verbs
        return len(msg) >= 60

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
            return (
                f"**Restate:** {user_message[:200]}\n\n"
                f"**Goal:** Complete the user's request\n\n"
                f"**Plan:**\n1. Examine the request and any provided files\n"
                f"2. Proceed step by step using available tools\n"
                f"3. Verify and report"
            )

    def _format_analysis_for_user(self, analysis: str) -> str:
        """Format the Pass-1 analysis as a 'task plan' block for the user."""
        if not analysis:
            return ""
        return f"📋 **Task analysis:**\n\n{analysis}\n\n---\n"

    # ===========================================================
    # PASS 2: ACT (with iterative self-evaluation)
    # ===========================================================

    def chat_stream(self, user_message: str, uploaded_files_info: str = ""):
        """Process a user message. Yields full-response snapshots (string)."""
        if not self.api_key:
            yield "⚠️ Walang API key! I-set mo muna ang NVIDIA API key sa Settings."
            return

        self.cancel_requested = False

        # ALWAYS trim at the start of every turn so we don't accumulate
        # infinite tool-call messages from previous turns.
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

        # ---- CONDITIONAL PASS 1: ANALYZE (no tools) ----
        # Only run for non-trivial tasks. The analysis is short but adds latency,
        # so we skip it for short questions.
        if self._should_analyze(user_message, uploaded_files_info):
            analysis_thinking_msg = "\n\n💭 Analyzing your request…\n\n"
            full_response += analysis_thinking_msg
            yield full_response

            analysis = self._analyze_task(client, user_message, uploaded_files_info)
            full_response = full_response.replace(
                analysis_thinking_msg, self._format_analysis_for_user(analysis)
            )
            yield full_response

            # Inject the analysis into the main message stream as a hint
            augmented_user_message = (
                user_message
                + "\n\n---\n\n# PRE-ANALYSIS (already done — do not re-analyze, just execute):\n\n"
                + analysis
            )
            self.messages[-1] = {"role": "user", "content": augmented_user_message}

        # Track the current goal for self-evaluation
        self._current_goal = user_message[:200]
        _eval_count = 0
        _MAX_EVALS_PER_TURN = 3

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

            # ---- SELF-EVALUATION (agentic loop) ----
            # After each batch of tool calls, ask: "did this work? need a fix?"
            # This is what Arena Agent Mode does — self-correcting loop.
            # Only run for write/edit/run_python actions (not reads/searches).
            action_tool_names = [tc["function"]["name"] for tc in tool_calls]
            producing_actions = {"write_file", "edit_file", "run_python", "run_bash", "pip_install", "process_image", "remove_background"}
            should_evaluate = any(n in producing_actions for n in action_tool_names) and _eval_count < _MAX_EVALS_PER_TURN
            if should_evaluate and not self.cancel_requested:
                # Collect what was just done
                last_results = []
                for tc, args in zip(tool_calls, args_compact):
                    # find the corresponding tool message we just appended
                    pass
                # Reconstruct: use the last N tool messages
                recent_tool_msgs = [
                    m for m in self.messages
                    if m.get("role") == "tool"
                ][-len(tool_calls):]

                action_summary = ", ".join(
                    f"`{n}({', '.join(f'{k}={str(v)[:50]}' for k, v in zip(a.keys(), a.values()))})`"
                    for n, a in zip(action_tool_names, args_compact)
                )
                result_text = "\n\n".join(
                    m.get("content", "")[:1500] for m in recent_tool_msgs
                )

                eval_msg = "\n\n🔍 **Self-check…**\n"
                full_response += eval_msg
                yield full_response

                _eval_count += 1
                evaluation = self._evaluate_action(
                    client, self._current_goal, action_summary, result_text
                )
                first_line = evaluation.splitlines()[0].strip() if evaluation else "KEEP"

                if first_line == "KEEP":
                    full_response += "✅ Looks good. Moving on.\n"
                    self._log("eval_keep")
                elif first_line == "REPLAN":
                    # Major change: inject a new user message with the replan
                    replan_body = "\n".join(evaluation.splitlines()[1:]).strip()
                    full_response += f"🔄 Re-planning: {replan_body[:200]}\n"
                    self._log("eval_replan", body=replan_body[:200])
                    self.messages.append({
                        "role": "user",
                        "content": (
                            f"[Self-evaluation feedback — major correction needed]\n\n"
                            f"{replan_body}\n\n"
                            f"Adjust your approach accordingly on the next turn. "
                            f"Don't repeat the failed approach."
                        ),
                    })
                elif first_line == "FIX":
                    fix_body = "\n".join(evaluation.splitlines()[1:]).strip()
                    # Parse out the Issue and Fix lines
                    issue = ""
                    fix = ""
                    for line in fix_body.splitlines():
                        if line.lower().startswith("issue:"):
                            issue = line.split(":", 1)[1].strip()
                        elif line.lower().startswith("fix:"):
                            fix = line.split(":", 1)[1].strip()
                    full_response += f"🛠️ Needs improvement: {issue[:200]}\n"
                    if fix:
                        full_response += f"   → {fix[:200]}\n"
                    self._log("eval_fix", issue=issue[:200], fix=fix[:200])
                    if fix:
                        # Inject a focused correction
                        self.messages.append({
                            "role": "user",
                            "content": (
                                f"[Self-evaluation feedback — small fix needed]\n\n"
                                f"Issue: {issue}\n\n"
                                f"Suggested fix: {fix}\n\n"
                                f"Apply this correction and continue. Don't re-do the whole task — just the specific fix."
                            ),
                        })
                    # else: just a heads-up, continue normally
                else:
                    # Unrecognized format, treat as KEEP
                    full_response += "✅ (could not parse evaluation)\n"
                    self._log("eval_unknown", first=first_line[:50])

                yield full_response

            # Thin rule after the action block
            full_response += "\n---\n\n"
            yield full_response

            if self.cancel_requested:
                break

            if len(self.messages) > self.max_context_messages * 2:
                self._trim_conversation_history()

        # Loop exit — store a brief summary, NOT the full response (which can be
        # many KB of tool-call dumps and would balloon the history).
        summary = (
            f"[Turn ended after {self.iteration_count} iterations. "
            f"Elapsed: {time.time() - start_time:.1f}s. "
            f"Tool calls: {self.tool_call_count}. "
            f"Final response excerpt: {full_response[:300]!s}]"
        )
        self.messages.append({"role": "assistant", "content": summary})
        self._log("turn_end", reason="max_iterations_or_budget", iterations=self.iteration_count)
        yield full_response

