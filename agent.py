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
import threading
import time
import uuid
from datetime import datetime

from response_status import with_status

HEARTBEAT_SECONDS = 8.0  # keep the stream alive during slow sandbox/tool work

from api_retry import make_client, completion_with_retry, is_transient, CompletionCancelled

from config import (
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    DEFAULT_MODEL,
    MAX_ITERATIONS,
    MAX_TOTAL_SECONDS,
    REPEAT_CALL_LIMIT,
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
from context_manager import ContextManager
from vision import image_data_uri, strip_old_images
from approvals import gate as approval_gate, needs_approval

MAX_VERIFY_ROUNDS = 1


def verify_enabled() -> bool:
    return os.environ.get("AGENT_VERIFY", "on").strip().lower() not in ("off", "0", "false", "no")


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
        lines = [l.strip() for l in output.splitlines() if l.strip()] if output else []
        # run_python output starts with a bare "Errors:" header; the useful line is
        # the last one of the traceback (the exception message).
        useful = [l for l in lines if l.rstrip(":") not in ("Errors", "Output")]
        first = (useful[-1] if useful else lines[0] if lines else "error")
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

def _workspace_snapshot() -> dict:
    snap = {}
    try:
        for root, dirs, names in os.walk(WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for n in names:
                if n.startswith("."):
                    continue
                path = os.path.join(root, n)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                snap[os.path.relpath(path, WORKSPACE_DIR)] = (st.st_mtime_ns, st.st_size)
                if len(snap) > 500:
                    return snap
    except OSError:
        pass
    return snap


def _changed_files(before) -> list:
    if before is None:
        return []
    now = _workspace_snapshot()
    return sorted(p for p, meta in now.items() if before.get(p) != meta)[:20]


class AIAgent:
    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self.api_key = api_key or NVIDIA_API_KEY
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or NVIDIA_BASE_URL
        self._providers: list = []
        self._provider_idx = 0
        self.vision = False  # set per provider; text-only models never get images
        self.messages: list = []
        self.iteration_count = 0
        self.reasoning_effort = "high"
        self.enable_thinking = False
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

        # Context manager (TencentDB-inspired offloading)
        self.context = ContextManager(self.session_id)

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

        return f"""You are an expert AI assistant with sandboxed tools on the user's computer. You are deliberate, careful, and you verify your work.

For letters, memos and official-style documents (.docx), call the create_letter_docx tool. Do not hand-write python-docx code for them.

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

# 🧠 CONTEXT MANAGEMENT (Progressive Disclosure)

Your tool outputs are **offloaded to files** to save context space. You see compact summaries in context, but full details are saved.

**When you need full details of a past step:**
- Use `recall_step(step_id)` to read the full output of any step
- Step IDs are shown in the summaries: "Step 3: write_file — OK [→ step_003.md]"

**Key facts are automatically extracted** from your conversations and shown to you as "Known facts." Honor these facts unless the user explicitly changes them.

**Task state is tracked** — you can see your current goal and progress as "📋 Task: ... | ✅ Step 1 | ⏳ Step 2 | ⬜ Step 3"

When you complete a step, the system automatically advances the progress tracker.

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
        """Destroy the session's sandbox and context (called on clear/reset)."""
        session_manager.destroy(self.session_id)
        self.context.cleanup()

    def set_model(self, model: str):
        self.model = model

    def set_api_key(self, api_key: str):
        self.api_key = api_key

    def refresh_providers(self):
        """Reload provider list (providers.json / env) and select the first one."""
        from providers import active_providers
        self._providers = active_providers()
        self._provider_idx = 0
        if self._providers:
            self._apply_provider(self._providers[0])

    def _apply_provider(self, p: dict):
        self.base_url, self.api_key, self.model = p["base_url"], p["api_key"], p["model"]
        self.vision = bool(p.get("vision", False))

    def _switch_provider(self) -> bool:
        """Move to the next provider in fallback order. False if none left."""
        provs = getattr(self, "_providers", [])
        if self._provider_idx + 1 >= len(provs):
            return False
        self._provider_idx += 1
        self._apply_provider(provs[self._provider_idx])
        self._log("provider_fallback", provider_index=self._provider_idx, model=self.model)
        return True

    def get_metrics(self) -> dict:
        elapsed = time.time() - self.session_start
        sandbox_info = {}
        try:
            sandbox_info = session_manager.peek_status(self.session_id)
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
            "context_steps": self.context.step_count,
            "context_facts": len(self.context.facts),
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
        if event in {"transient_error", "api_failed", "stream_error", "reasoning_started",
                     "empty_response", "turn_final", "budget_exceeded", "cancelled",
                     "cancelled_mid_stream", "provider_fallback"}:
            safe = {key: value for key, value in fields.items()
                    if key in {"attempt", "delay", "reasoning_seen", "completion_tokens", "elapsed"}
                    and isinstance(value, (int, float, bool))}
            print(json.dumps({"event": event, "session": self.session_id,
                              "iter": self.iteration_count, **safe}), flush=True)
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

    def _thinking_body(self, effort=None):
        if "nemotron" in (self.model or "").lower():
            controls = {"enable_thinking": self.enable_thinking}
            if self.enable_thinking:
                # NVIDIA's modelcard requires this for reasoning + tool parsing.
                controls["force_nonempty_content"] = True
            return {"chat_template_kwargs": controls}
        if _model_supports_reasoning(self.model):
            return {"chat_template_kwargs": {"reasoning_effort": effort or self.reasoning_effort}}
        return None

    def _get_client(self):
        return make_client(self.base_url, self.api_key)

    def _approval_prompt(self, tool_name: str, args: dict):
        """Text to show the user when this call needs approval, else None."""
        try:
            if tool_name == "run_bash":
                check = guard.validate_command(args.get("command", ""))
                shown = args.get("command", "")
            elif tool_name == "run_python":
                check = guard.validate_python_code(args.get("code", ""))
                shown = args.get("code", "")
            elif tool_name == "pip_install":
                check = {"risk_level": "risky"}
                shown = args.get("package", "")
            else:
                return None
            if not check.get("safe", True):
                return None  # blocked stays blocked; the tool reports it
            mode = session_manager.get_or_create(self.session_id).mode
            if needs_approval(tool_name, check.get("risk_level", "safe"), mode):
                return f"{tool_name}: {str(shown)[:500]}", mode
        except Exception as e:
            self._log("approval_check_failed", error=type(e).__name__)
        return None

    def _in_background(self, fn, label: str, interval: float = None):
        """Run fn in a worker thread. Yield ("beat", text) every `interval` seconds
        so the stream never goes silent (proxies drop idle connections), then
        yield ("done", result). Exceptions in fn are re-raised here."""
        interval = HEARTBEAT_SECONDS if interval is None else interval
        box = {}

        def work():
            try:
                box["value"] = fn()
            except BaseException as e:  # noqa: BLE001 - re-raised below
                box["error"] = e

        t = threading.Thread(target=work, daemon=True)
        t0 = time.time()
        t.start()
        while True:
            t.join(interval)
            if not t.is_alive():
                break
            yield ("beat", f"\n\n⏳ {label}... {int(time.time() - t0)}s\n\n")
        if "error" in box:
            raise box["error"]
        yield ("done", box.get("value"))

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

    _VERIFY_SYSTEM = """You check an AI agent's final answer before the user sees it as finished. Compare the answer with the user's goal and the tool evidence.

Reply with EXACTLY one of:

PASS

or

FAIL
Issue: <the specific problem: claim not backed by tool output, ignored error, missing part of the request, file not created, etc.>
Fix: <one concrete next step>

RULES
- PASS unless there is a real, specific problem. Do not nitpick style or wording.
- A claim like "created X" or "tests pass" needs matching evidence in the tool results.
- If a tool result shows an error or a denied/blocked command that the answer ignores, FAIL.
- Never claim the code has a syntax or import error unless a tool output shows that error. You only see the start of the code, so judge from tool output and files.
- Tool evidence includes the start of the code that ran and the files created this turn. If a tool succeeded and the file the user asked for is listed as created, do NOT FAIL because you cannot see all of the code.
- Match the user's language."""

    def _verify_final(self, client, goal: str, answer: str) -> str:
        """Ask the model to check its own final answer. Never blocks on failure."""
        evidence = "\n".join(
            f"- {name} [{status}]: {out}" for name, status, out in self._turn_evidence[-8:]
        ) or "(no tool calls)"
        files = _changed_files(getattr(self, "_files_before", None))
        if files:
            evidence += "\n- Files created or changed this turn: " + ", ".join(files)
        messages = [
            {"role": "system", "content": self._VERIFY_SYSTEM},
            {"role": "user", "content": (
                f"**User goal:** {goal}\n\n**Tool evidence (latest):**\n{evidence[:4500]}\n\n"
                f"**Final answer:**\n{answer[:2000]}"
            )},
        ]
        try:
            resp = completion_with_retry(
                client, cancelled=lambda: self.cancel_requested,
                deadline=getattr(self, "_completion_deadline", None),
                model=self.model, messages=messages, temperature=0.1,
                max_tokens=300, stream=False,
                **({"extra_body": self._thinking_body()} if self._thinking_body() else {}))
            verdict = (resp.choices[0].message.content or "").strip()
            self._log("verify", verdict=verdict.splitlines()[0] if verdict else "empty")
            return verdict or "PASS"
        except Exception as e:
            self._log("verify_failed", error=type(e).__name__)
            return "PASS"

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
        if self._thinking_body("medium"):
            kwargs["extra_body"] = self._thinking_body("medium")
        try:
            resp = completion_with_retry(
                client, cancelled=lambda: self.cancel_requested,
                deadline=getattr(self, "_completion_deadline", None), **kwargs)
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
        if self._thinking_body("high"):
            kwargs["extra_body"] = self._thinking_body("high")

        try:
            resp = completion_with_retry(
                client, cancelled=lambda: self.cancel_requested,
                deadline=getattr(self, "_completion_deadline", None), **kwargs)
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
            yield "⚠️ Walang API key! Buksan ang 🔑 Providers sa Settings at i-save ang key mo."
            return

        self.cancel_requested = False

        # ALWAYS trim at the start of every turn so we don't accumulate
        # infinite tool-call messages from previous turns.
        self._trim_conversation_history()

        self.messages.append({"role": "user", "content": user_message})

        if not any(m.get("role") == "system" for m in self.messages):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

        # Inject dynamic context block into system message
        context_block = self.context.build_context_block()
        if context_block:
            # Update system message with context
            base_prompt = self.system_prompt
            self.messages[0]["content"] = base_prompt + "\n\n# 🧠 DYNAMIC CONTEXT\n\n" + context_block

        client = self._get_client()
        self.iteration_count = 0
        full_response = ""
        start_time = time.time()
        self._completion_deadline = (time.monotonic() + MAX_TOTAL_SECONDS
                                     if MAX_TOTAL_SECONDS else float("inf"))
        self._loop_halted = False
        self._last_call_sig = None
        self._same_call_count = 0
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

        # Track the current goal for self-evaluation and task state
        self._current_goal = user_message[:200]
        self.context.set_goal(user_message[:200])
        _eval_count = 0
        _MAX_EVALS_PER_TURN = 3
        self._turn_evidence = []
        self._files_before = _workspace_snapshot()
        _verify_rounds = 0
        _stream_retries = 0

        while MAX_ITERATIONS is None or self.iteration_count < MAX_ITERATIONS:
            if self._loop_halted:
                full_response += ("\n\n🔁 Stopped: the same tool call was repeated "
                                  f"{REPEAT_CALL_LIMIT} times in a row, so this looks like a loop. "
                                  "Send a new message to continue.")
                self._log("repeat_halt", calls=self._same_call_count)
                break
            # Optional wall-clock budget (off by default)
            if MAX_TOTAL_SECONDS and time.time() - start_time > MAX_TOTAL_SECONDS:
                full_response += f"\n\n⏱️ Reached the {MAX_TOTAL_SECONDS}s wall-clock budget. Stopping."
                self._log("budget_exceeded", elapsed=time.time() - start_time)
                break

            if self.cancel_requested:
                full_response += "\n\n⏹️ **Operation cancelled by user.**"
                self._log("cancelled")
                break

            self.iteration_count += 1

            # Keep this visible until answer text or tool calls actually arrive.
            thinking_msg = "\n\n⏳ Waiting for the model…\n\n"
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
                frequency_penalty=0.0,
                presence_penalty=0.0,
            )
            if self._thinking_body():
                kwargs["extra_body"] = self._thinking_body()

            # Retry only before streaming starts. Partial streams are not replayed.
            stream = None
            while stream is None:
                try:
                    retry_status = [None]
                    def on_retry(attempt, delay, error):
                        retry_status[0] = f"Retrying (attempt {attempt + 1}/3, {delay}s backoff)…"
                        self._log("transient_error", attempt=attempt, delay=delay, error=error)
                    def create_stream():
                        result = completion_with_retry(
                            client, cancelled=lambda: self.cancel_requested,
                            deadline=self._completion_deadline, on_retry=on_retry, **kwargs)
                        if self.cancel_requested or time.monotonic() >= self._completion_deadline:
                            result.close()
                            raise CompletionCancelled()
                        yield result
                    for kind, value in with_status(create_stream,
                            deadline=self._completion_deadline,
                            cancelled=lambda: self.cancel_requested):
                        if kind == "item":
                            stream = value
                        else:
                            status = retry_status[0] or "Waiting for the model…"
                            yield full_response.replace(thinking_msg, f"\n\n⏳ {status}\n\n")
                except CompletionCancelled:
                    full_response += "\n\nOperation cancelled by user."
                    yield full_response
                    return
                except TypeError as e:
                    if "extra_body" in kwargs or "reasoning_effort" in kwargs:
                        self._log("reasoning_unsupported", error=str(e))
                        kwargs.pop("extra_body", None)
                        kwargs.pop("reasoning_effort", None)
                        continue
                    self.errors += 1
                    full_response += f"\n\n❌ API Error: {e}"
                    yield full_response
                    return
                except Exception as e:
                    self.errors += 1
                    if (is_transient(e) and time.monotonic() < self._completion_deadline
                            and self._switch_provider()):
                        full_response += f"\n\n🔁 Switching to fallback model `{self.model}`…"
                        yield full_response
                        client = self._get_client()
                        kwargs["model"] = self.model
                        supports_reasoning = _model_supports_reasoning(self.model)
                        kwargs.pop("extra_body", None)
                        if self._thinking_body():
                            kwargs["extra_body"] = self._thinking_body()
                        continue
                    self._log("api_failed", error=type(e).__name__)
                    full_response += f"\n\n❌ API unavailable: {e}"
                    yield full_response
                    return

            if stream is None:
                break

            content_chunks: list = []
            tool_calls_data: dict = {}
            finish_reason = None
            stream_error = None

            _last_ui = 0.0
            try:
                reasoning_seen = False
                for kind, chunk in with_status(lambda: stream,
                        deadline=self._completion_deadline,
                        cancelled=lambda: self.cancel_requested):
                    if kind == "wait":
                        # The 0.15 CPU free instance chokes on a UI update every second.
                        if time.monotonic() - _last_ui >= 4.0:
                            _last_ui = time.monotonic()
                            status = "Model is thinking; waiting for answer…" if reasoning_seen else "Waiting for the model…"
                            yield full_response.replace(thinking_msg, f"\n\n⏳ {status}\n\n")
                        continue
                    if self.cancel_requested:
                        full_response += "\n\n⏹️ **Operation cancelled by user.**"
                        self._log("cancelled_mid_stream")
                        stream_error = "cancelled"
                        break
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    if delta and getattr(delta, "reasoning_content", None) and not reasoning_seen:
                        reasoning_seen = True
                        self._log("reasoning_started")
                        _last_ui = time.monotonic()
                        yield full_response.replace(thinking_msg, "\n\n⏳ Model is thinking; waiting for answer…\n\n")
                    if delta and delta.content:
                        full_response = full_response.replace(thinking_msg, "\n\n---\n\n")
                        content_chunks.append(delta.content)
                        full_response += delta.content
                        # Throttle UI pushes: one per token floods Gradio on the free instance.
                        if time.monotonic() - _last_ui >= 0.3:
                            _last_ui = time.monotonic()
                            yield full_response

                    if delta and delta.tool_calls:
                        full_response = full_response.replace(thinking_msg, "\n\n---\n\n")
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
            except CompletionCancelled:
                full_response += "\n\n⏹️ Operation cancelled by user."
                yield full_response
                return
            except Exception as e:
                self.errors += 1
                self._log("stream_error", error=type(e).__name__)
                _silent = not content_chunks and not tool_calls_data and not reasoning_seen
                if (_silent and _stream_retries < 2 and not self.cancel_requested
                        and (is_transient(e) or "timeout" in type(e).__name__.lower()
                             or "timed out" in str(e).lower())
                        and time.monotonic() < self._completion_deadline):
                    # Nothing arrived yet, so replaying this step cannot duplicate output.
                    _stream_retries += 1
                    self._log("stream_retry", attempt=_stream_retries, error=type(e).__name__)
                    full_response = full_response.replace(thinking_msg, "")
                    full_response += f"\n\n🔁 Model stream stalled, retrying ({_stream_retries}/2)…\n\n"
                    yield full_response
                    self.iteration_count -= 1
                    continue
                full_response += f"\n\n❌ Stream error: {e}"
                yield full_response
                return

            if content_chunks:
                yield full_response  # flush text withheld by the throttle
            completion_tokens = sum(_count_tokens(c) for c in content_chunks)

            if stream_error == "cancelled":
                break

            if not tool_calls_data:
                # Final response — no tool calls
                self.total_completion_tokens += completion_tokens
                final_text = "".join(content_chunks)
                if not final_text.strip():
                    self.errors += 1
                    self._log("empty_response", reasoning_seen=reasoning_seen)
                    full_response = full_response.replace(thinking_msg, "")
                    full_response += "\n\n⚠️ The model finished without an answer. Try again or choose another model in Settings."
                    yield full_response
                    return
                self.messages.append({"role": "assistant", "content": final_text})
                if (verify_enabled() and self._turn_evidence and final_text.strip()
                        and _verify_rounds < MAX_VERIFY_ROUNDS
                        and self._completion_deadline - time.monotonic() > 60
                        and not self.cancel_requested):
                    _verify_rounds += 1
                    full_response += "\n\n🔎 **Checking my answer…**\n"
                    yield full_response
                    verdict = ""
                    for kind, val in self._in_background(
                            lambda: self._verify_final(client, self._current_goal, final_text),
                            "Checking my answer"):
                        if kind == "beat":
                            yield full_response + val
                        else:
                            verdict = val
                    if verdict.splitlines()[0].strip().upper() == "FAIL":
                        body = "\n".join(verdict.splitlines()[1:]).strip()
                        full_response += f"🛠️ May kulang: {body[:300]}\n\n"
                        self._log("verify_fail", body=body[:300])
                        self.messages.append({"role": "user", "content": (
                            "[Verification feedback — your answer was not fully backed up]\n\n"
                            f"{body}\n\nFix this with tools if needed, then give a corrected final answer. "
                            "Do not repeat work that already succeeded."
                        )})
                        yield full_response
                        continue
                    full_response += "✅ Na-check, ok.\n"
                    yield full_response
                _made = _changed_files(getattr(self, "_files_before", None))
                if _made:
                    full_response += ("\n\n📎 **Files ready:** " + ", ".join(f"`{n}`" for n in _made)
                                      + " - download them from **Files in workspace** below the chat.\n")
                    yield full_response
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

            pending_images = []
            # ---- Execute each tool, render a compact one-line result ----
            for tc, args in zip(tool_calls, args_compact):
                if self.cancel_requested:
                    full_response += "\n\n⏹️ **Operation cancelled.**\n"
                    self._log("cancelled_before_tool")
                    break

                tool_name = tc["function"]["name"]
                sig = (tool_name, tc["function"]["arguments"])
                self._same_call_count = self._same_call_count + 1 if sig == self._last_call_sig else 1
                self._last_call_sig = sig
                if self._loop_halted or self._same_call_count >= REPEAT_CALL_LIMIT:
                    # Every tool_call needs a reply, so answer it, run nothing, end the turn.
                    self._loop_halted = True
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": "Not run: repeated identical call; turn stopped."})
                    continue
                self.tool_call_count += 1

                t0 = time.time()
                prompt = None
                for kind, val in self._in_background(
                        lambda: self._approval_prompt(tool_name, args), "Inihahanda ang sandbox"):
                    if kind == "beat":
                        yield full_response + val
                    else:
                        prompt = val
                if prompt:
                    text, sbx_mode = prompt
                    full_response += (
                        f"⏸️ **Kailangan ng approval** (walang Docker, mode: `{sbx_mode}`)\n"
                        f"```\n{text}\n```\n"
                        "Pindutin ang ✅ Approve o ❌ Deny sa itaas ng chat.\n"
                    )
                    yield full_response
                    approved = approval_gate.request(
                        self.session_id, text, cancelled=lambda: self.cancel_requested)
                    self._log("approval", tool=tool_name, approved=approved)
                    if approved:
                        result = None
                        for kind, val in self._in_background(
                                lambda: self._execute_tool(tool_name, args), f"Tumatakbo ang {tool_name}"):
                            if kind == "beat":
                                yield full_response + val
                            else:
                                result = val
                    else:
                        result = {"status": "blocked", "risk_level": "risky",
                                  "output": "The user denied (or did not answer) the approval request. "
                                            "Do not retry this command. Choose a safer approach or ask the user."}
                else:
                    result = None
                    for kind, val in self._in_background(
                            lambda: self._execute_tool(tool_name, args), f"Tumatakbo ang {tool_name}"):
                        if kind == "beat":
                            yield full_response + val
                        else:
                            result = val
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

                _arg_head = ""
                if isinstance(args, dict):
                    _code = args.get("code") or args.get("command") or ""
                    if _code:
                        _arg_head = f" | input starts: {str(_code)[:500]!r}"
                self._turn_evidence.append((
                    tool_name,
                    result.get("status", "unknown") if isinstance(result, dict) else "unknown",
                    str(result.get("output", "") if isinstance(result, dict) else result)[:800] + _arg_head,
                ))
                full_response += f"{badge} `{tool_name}` — {elapsed:.2f}s — {summary}\n"
                yield full_response

                # Offload full result to file, inject summary into context
                if (tool_name == "view_file" and isinstance(result, dict)
                        and result.get("file_type") == "image" and result.get("image_path")):
                    if self.vision:
                        pending_images.append(result["image_path"])
                        result["output"] = result.get("output", "") + "\n\n(The image is attached in the next message.)"
                    else:
                        result["output"] = result.get("output", "") + (
                            "\n\nNote: the current model has vision turned off, so you cannot see this image. "
                            "Use only the details above. Do not guess what the picture shows; "
                            "tell the user, or ask them to describe it.")

                raw_output = result.get("output", "") if isinstance(result, dict) else str(result)
                offloaded_summary = self.context.offload(
                    tool_name=tool_name,
                    arguments=args,
                    full_output=raw_output,
                )

                # Save offloaded summary to message history (compact)
                # But keep full result for the LLM to reason about
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

            # ---- Show images to vision-capable models (after all tool messages) ----
            if pending_images:
                parts = [{"type": "text", "text": "Image(s) from view_file:"}]
                for img_path in pending_images:
                    uri = image_data_uri(img_path)
                    if uri:
                        parts.append({"type": "image_url", "image_url": {"url": uri}})
                    else:
                        parts[0]["text"] += f" (could not load {os.path.basename(img_path)})"
                strip_old_images(self.messages)
                self.messages.append({"role": "user", "content": parts})
                self._log("image_attached", count=len(pending_images))

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
                evaluation = ""
                for kind, val in self._in_background(
                        lambda: self._evaluate_action(
                            client, self._current_goal, action_summary, result_text),
                        "Self-check"):
                    if kind == "beat":
                        yield full_response + val
                    else:
                        evaluation = val
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
