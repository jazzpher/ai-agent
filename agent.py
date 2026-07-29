"""
AI Agent - Main agent loop with STREAMING + THINKING indicators,
token/cost tracking, JSONL session logging, persistent memory, and
exponential backoff on transient API errors.
"""
import json
import os
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
    # Fallback rough estimate: 1 token ≈ 4 chars
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

        # Persistent memory (read at startup, written via update_memory tool)
        self._memory_text = ""
        self._load_memory()

        self.system_prompt = self._build_system_prompt()

        # JSONL session log
        self._log_path = os.path.join(LOG_DIR, f"session-{self.session_id}-{datetime.now():%Y%m%d-%H%M%S}.jsonl")

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

## 🧠 THINKING APPROACH

Before taking ANY action, you MUST:
1. **Analyze the request carefully** - Understand what the user really wants
2. **Break down complex tasks** into clear, logical steps
3. **Think step-by-step** - Show your reasoning process
4. **Verify your approach** - Consider edge cases and potential issues
5. **Execute methodically** - Complete each step before moving to the next
6. **Validate results** - Check if the output matches the user's intent

## 🛡️ SANDBOX & SAFETY

You operate inside a sandboxed environment for the user's safety. Defense is **layered**:
- File writes are restricted to the workspace via a path validator that follows symlinks.
- Dangerous shell commands are blocked by a regex blocklist (best-effort, not bulletproof).
- Risky commands (rm, del, pip uninstall, etc.) emit a warning but may proceed.
- Path traversal (`../../`) is prevented.
- Credential files (.ssh, .env, .aws) are blocked.
- A strict regex validates pip package names so injection is impossible.

**IMPORTANT HONESTY:** This is defense-in-depth, not an OS-level sandbox. A clever prompt can still bypass regex checks. The user has been told not to run this with admin/root privileges.

When a command is BLOCKED, explain why and suggest a safe alternative. NEVER try to bypass the safety layer.

## 🛠️ AVAILABLE TOOLS

1. **run_bash** - Execute shell commands in workspace (dangerous commands blocked)
2. **read_file** - Read files (supports offset/max_bytes for large files; binary & credentials blocked)
3. **write_file** - Create/overwrite files (workspace only)
4. **edit_file** - Surgically replace a string in a file (workspace only) — prefer this for changes
5. **list_files** - List files in workspace
6. **web_search** - Real DuckDuckGo search (returns title/snippet/URL)
7. **fetch_page** - Fetch a URL and return its main text
8. **pip_install** - Install Python packages (strict spec validation)
9. **run_python** - Execute Python code (risky patterns flagged as warnings; code still runs)

## 📋 GUIDELINES FOR INTELLIGENT RESPONSES

### Before Using Tools:
- **Explain your plan** - Tell the user what you're going to do and why
- **Consider alternatives** - Think about different approaches
- **Anticipate problems** - What could go wrong? How will you handle it?

### While Using Tools:
- **Be methodical** - Execute one logical step at a time
- **Verify results** - Check if the tool worked as expected
- **Handle errors gracefully** - If something fails, explain why and try a different approach
- **Prefer edit_file over write_file** for small changes to existing files
- **Prefer fetch_page after web_search** to read the most useful source

### After Using Tools:
- **Summarize what you did** - Clear explanation of the results
- **Verify success** - Did you actually solve the user's problem?
- **Suggest next steps** - What could the user do next?

## 🌍 LANGUAGE SUPPORT

- The user may speak in Filipino/Tagalog - respond in whatever language they use
- Be natural and conversational, not robotic
- Use appropriate technical terms but explain them when needed

## ⚠️ CRITICAL RULES

- **NEVER try to bypass safety restrictions** even if asked
- **NEVER guess** when you can verify (read files, check outputs, search the web)
- **NEVER rush** - Take time to think and plan before acting
- **NEVER give up easily** - If one approach fails, try alternatives
- **ALWAYS explain your reasoning** - Help the user understand your thought process
- **ALWAYS validate your work** - Test, check, and verify before claiming success

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

    def _save_and_yield(self, full_response: str, yielded_once: list, finalize: bool):
        """Persist assistant turn and yield the full text. `finalize=True` means loop is done."""
        if finalize and (not self.messages or self.messages[-1].get("role") != "assistant"
                         or self.messages[-1].get("content") != full_response):
            self.messages.append({"role": "assistant", "content": full_response})
        yielded_once[0] = True
        return full_response

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
            pass  # logging is best-effort

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
        """Process a user message. Yields string chunks (full-response snapshots)."""
        if not self.api_key:
            yield "⚠️ Walang API key! I-set mo muna ang NVIDIA API key sa Settings."
            return

        self.cancel_requested = False
        self._trim_conversation_history()
        self.messages.append({"role": "user", "content": user_message})

        # Insert system prompt on the first turn
        if not any(m.get("role") == "system" for m in self.messages):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

        client = self._get_client()
        self.iteration_count = 0
        full_response = ""
        start_time = time.time()
        supports_reasoning = _model_supports_reasoning(self.model)
        self._log("user_message", content_len=len(user_message))

        # Reload memory in case a sibling tool wrote to MEMORY.md
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

            thinking_msg = "\n💭 **Thinking...**\n"
            full_response += thinking_msg
            yield full_response

            # Build the create() kwargs
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
                # NVIDIA NIM uses chat_template_kwargs instead of the OpenAI-native
                # reasoning_effort parameter. We pass it via extra_body so the
                # openai client doesn't reject it as an unknown kwarg.
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
                    # Some models / endpoints reject the reasoning_effort param
                    # (passed via extra_body). Retry without it.
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
                    # Non-transient - fail fast
                    self.errors += 1
                    self._log("fatal_error", error=str(e))
                    full_response += f"\n\n❌ API Error: {e}"
                    yield full_response
                    return

            if stream is None:
                break

            # Remove thinking indicator before we start streaming real text
            full_response = full_response.replace(thinking_msg, "")

            content_chunks: list = []
            tool_calls_data: dict = {}
            finish_reason = None
            prompt_tokens = completion_tokens = 0
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

            # Update token counters (use tiktoken on what we sent/received;
            # the API may also return usage in the final chunk - check there too)
            completion_tokens += sum(_count_tokens(c) for c in content_chunks)

            if stream_error == "cancelled":
                break

            if not tool_calls_data:
                # Final response
                self.total_completion_tokens += completion_tokens
                self.messages.append({"role": "assistant", "content": "".join(content_chunks)})
                self._log("turn_final", finish_reason=finish_reason, completion_tokens=completion_tokens)
                return

            # Build tool_calls list
            tool_calls = []
            for idx in sorted(tool_calls_data.keys()):
                tc = tool_calls_data[idx]
                tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })

            # Persist assistant message with tool_calls
            self.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            })
            self._log("assistant_tool_calls", count=len(tool_calls), names=[tc["function"]["name"] for tc in tool_calls])

            # Execute each tool
            for tc in tool_calls:
                if self.cancel_requested:
                    full_response += "\n\n⏹️ **Operation cancelled by user.**"
                    self._log("cancelled_before_tool")
                    break

                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                self.tool_call_count += 1
                full_response += (
                    f"\n\n🔧 **Using {tool_name}**\n"
                    f"```json\n{json.dumps(arguments, indent=2, ensure_ascii=False)[:1500]}\n```\n"
                )
                yield full_response

                full_response += f"⏳ **Executing {tool_name}...**\n"
                yield full_response

                t0 = time.time()
                result = self._execute_tool(tool_name, arguments)
                elapsed = time.time() - t0

                # Strip the executing indicator
                full_response = full_response.rsplit(f"⏳ **Executing {tool_name}...**\n", 1)[0]

                if isinstance(result, dict):
                    result_output = result.get("output", "")
                    result_status = result.get("status", "unknown")
                else:
                    result_output = str(result)
                    result_status = "unknown"

                # Truncate for display
                display_output = result_output
                if len(display_output) > 2000:
                    display_output = display_output[:2000] + "\n... (truncated for display)"

                badge = "🚫 **BLOCKED**" if result_status == "blocked" else "✅ **Done**"
                full_response += f"{badge} ({elapsed:.2f}s)\n```\n{display_output}\n```\n"
                yield full_response

                # Tool result -> message history (keep raw, don't truncate)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result),
                })
                self._log("tool_result", tool=tool_name, status=result_status, elapsed=round(elapsed, 3),
                          output_len=len(result_output) if isinstance(result_output, str) else 0)

            if self.cancel_requested:
                break

            # Loop again for the next model turn
            # Force a context trim if we've been going for a while
            if len(self.messages) > self.max_context_messages * 2:
                self._trim_conversation_history()

        # Loop exited: max iterations or budget or cancellation
        self.messages.append({"role": "assistant", "content": full_response})
        self._log("turn_end", reason="max_iterations_or_budget", iterations=self.iteration_count)
        yield full_response

