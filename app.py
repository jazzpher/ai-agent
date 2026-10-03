"""
AI Agent - Web UI (Gradio 5.x compatible)

New in this version:
- Per-session agent instances (no more shared singleton)
- Temporary sandbox for all tool execution
- Sandbox status display (venv/docker mode)
- view_file tool for docx/pdf/pptx/images
- Metrics panel with sandbox info
"""
import os
import shutil
import time
import uuid
from pathlib import Path

import gradio as gr

from server_settings import launch_settings
from agent import AIAgent
from config import NVIDIA_API_KEY, DEFAULT_MODEL, WORKSPACE_DIR
from tools import get_sandbox_status, TOOL_FUNCTIONS
from sandbox_session import session_manager
from approvals import gate as approval_gate, approval_mode


# ============================================================
# FILE UPLOAD HELPERS
# ============================================================

PREVIEWABLE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".txt", ".md", ".py", ".js", ".html", ".css", ".json", ".xml", ".yaml", ".yml",
    ".csv", ".log", ".sh", ".bat", ".ps1",
}


def copy_uploads_to_workspace(file_paths) -> tuple[list[str], str]:
    """Copy uploaded files into the workspace and return (paths, status_text)."""
    if not file_paths:
        return [], "No files uploaded"

    workspace_paths = []
    info_lines = []

    for file_path in file_paths:
        if not file_path:
            continue
        try:
            filename = os.path.basename(file_path)
            dest = os.path.join(WORKSPACE_DIR, filename)
            shutil.copy2(file_path, dest)
            size = os.path.getsize(dest)
            workspace_paths.append(dest)
            info_lines.append(f"📎 {filename} ({size:,} bytes)")
        except Exception as e:
            info_lines.append(f"❌ {os.path.basename(file_path)}: {e}")

    return workspace_paths, "\n".join(info_lines) if info_lines else "No files uploaded"


# ============================================================
# METRICS PANEL
# ============================================================

def format_metrics(agent) -> str:
    m = agent.get_metrics()
    sandbox_mode = m.get("sandbox_mode", "unknown")
    sandbox_emoji = "🐳" if sandbox_mode == "docker" else "🛡️"
    sandbox_pkg_count = m.get("sandbox_packages", 0)

    return (
        f"**📊 Session metrics**\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Session | `{m['session_id']}` |\n"
        f"| Model | `{m['model']}` |\n"
        f"| Elapsed | {m['elapsed_seconds']}s |\n"
        f"| Iterations | {m['iterations']} / {20} |\n"
        f"| Tool calls | {m['tool_calls']} |\n"
        f"| Errors | {m['errors']} |\n"
        f"| Prompt tokens | {m['prompt_tokens']:,} |\n"
        f"| Completion tokens | {m['completion_tokens']:,} |\n"
        f"| **Total tokens** | **{m['total_tokens']:,}** |\n"
        f"| **Est. cost** | **${m['estimated_cost_usd']:.4f}** |\n"
        f"| {sandbox_emoji} Sandbox | {sandbox_mode} ({sandbox_pkg_count} pkgs) |\n"
    )


# ============================================================
# SANDBOX STATUS
# ============================================================

def approval_warning() -> str:
    mode = approval_mode()
    if mode == "off":
        return "🚨 Walang Docker at **naka-off ang approval** (AGENT_APPROVAL=off). Risky commands tatakbo agad."
    return ("🔐 Walang Docker: risky commands (delete, overwrite, pip install, atbp.) "
            "ay hihingi muna ng approval mo.")


def format_sandbox_status(agent) -> str:
    status = get_sandbox_status(agent.session_id)
    mode = status.get("mode", "unknown")

    if mode == "not started":
        return "🛡️ Sandbox: not started. It starts only when a tool needs it."

    if mode == "docker":
        return (
            f"🐳 **Docker sandbox: ACTIVE**\n"
            f"- Session: `{status.get('session_id', '?')}`\n"
            f"- Uptime: {status.get('uptime_seconds', 0)}s\n"
            f"- Packages: {len(status.get('packages_installed', []))}\n\n"
            f"All commands run in an isolated container."
        )
    elif mode == "venv":
        pkgs = status.get("packages_installed", [])
        pkg_preview = ", ".join(pkgs[:5])
        if len(pkgs) > 5:
            pkg_preview += f" (+{len(pkgs) - 5} more)"
        return (
            f"🛡️ **Sandbox: ACTIVE (venv mode)**\n"
            f"- Session: `{status.get('session_id', '?')}`\n"
            f"- Uptime: {status.get('uptime_seconds', 0)}s\n"
            f"- Packages: {pkg_preview or 'installing...'}\n\n"
            f"⚠️ A venv isolates packages, not files or secrets. Commands can modify this server.\n\n"
            + approval_warning()
        )
    else:
        return (
            f"⏳ **Sandbox: Initializing...**\n\n"
            f"The sandbox will be created on first use."
        )


# ============================================================
# CHAT STREAMING
# ============================================================

def chat_stream(message: str, history: list, file_paths, agent: AIAgent):
    """Stream a chat response. Uses Gradio 5 'messages' format."""
    if not message.strip() and not file_paths:
        yield history, ""
        return


    # Copy uploads into workspace
    processed_paths, upload_info = copy_uploads_to_workspace(file_paths)

    # Append file info to user message
    display_message = message
    if processed_paths:
        display_message += "\n\n📎 Uploaded files:\n"
        for fp in processed_paths:
            display_message += f"- `{os.path.basename(fp)}`\n"

    # Gradio 5 'messages' format: list of {"role": ..., "content": ...}
    history = history or []
    history = history + [
        {"role": "user", "content": display_message},
        {"role": "assistant", "content": ""},
    ]

    history[-1]["content"] = "⏳ Preparing your request…"
    yield history, format_metrics(agent)
    agent.refresh_providers()  # picks up Settings changes without restart

    # Stream agent response (with two-pass analyze-then-act)
    for partial in agent.chat_stream(display_message, uploaded_files_info=upload_info):
        history[-1]["content"] = partial
        yield history, format_metrics(agent)


# ============================================================
# EVENT HANDLERS
# ============================================================

def clear_chat(agent: AIAgent):
    """Clear chat and destroy the old sandbox."""
    agent.cleanup_sandbox()
    agent.reset()
    # Create a new agent (new session)
    new_agent = AIAgent()
    return new_agent, [], "", "No files uploaded", [], format_metrics(new_agent)


def handle_upload(files):
    """Handle UploadButton files. Returns status text and list of paths."""
    if not files:
        return "No files uploaded", []

    paths = []
    info = []
    for f in files:
        path = getattr(f, "name", None) or (f if isinstance(f, str) else None)
        if path:
            paths.append(path)
            info.append(f"📎 {os.path.basename(path)}")

    return (" | ".join(info) if info else "No files uploaded"), paths


def stop_chat(agent: AIAgent):
    """User clicked the stop button. Cancel the running agent and revert UI."""
    agent.cancel()
    return gr.update(visible=True, interactive=True), gr.update(visible=False, interactive=False, value="⏹️")


def start_chat(message, history, file_paths, agent: AIAgent):
    """
    Streaming entry point. Yields 8 values per yield:
    (history, metrics, send_btn, stop_btn, file_status, uploaded_files, msg, agent)

    Send <-> Stop button toggle:
    - On entry: swap Send -> Stop (hide Send, show Stop with red color)
    - During streaming: keep Stop visible
    - On exit: restore Send (show Send, hide Stop) AND clear uploaded files AND clear msg

    File upload behavior:
    - Files uploaded before this turn are attached and used
    - After this turn, the uploaded files state and status are cleared
    - The actual files in the workspace remain (so the agent can still reference them)
    - To re-attach the same file, the user must re-upload it
    """
    no_change = gr.update()

    # Preserved state: keep everything as-is during the "nothing to do" path
    if not message.strip() and not file_paths:
        yield (history or [], format_metrics(agent),
               no_change, no_change, no_change, no_change, no_change, no_change)
        return

    # Reset cancel flag
    agent.cancel_requested = False

    # Snapshot of "current" file status so we can re-emit it during streaming
    cur_status = (
        "📎 " + ", ".join(os.path.basename(p) for p in file_paths)
        if file_paths
        else "No files uploaded"
    )

    # ---- Phase 1: Enter "running" mode (swap Send -> Stop) ----
    send_state = gr.update(visible=False, interactive=False)
    stop_state = gr.update(visible=True, interactive=True, variant="stop", value="⏹️ Stop")
    yield (
        history or [],
        format_metrics(agent),
        send_state,
        stop_state,
        cur_status,
        list(file_paths or []),
        no_change,        # keep msg as-is during running
        no_change,        # keep agent as-is
    )

    # ---- Phase 2: Run the stream (keep Stop visible) ----
    try:
        for h, metrics in chat_stream(message, history, file_paths, agent):
            history = h  # preserve the streamed answer when final cleanup runs
            yield (h, metrics, send_state, stop_state,
                   cur_status, list(file_paths or []), no_change, no_change)
    except Exception as e:
        # Surface the error in the metrics panel so the user can see it
        err = f"❌ {type(e).__name__}: {e}"
        if history:
            history.append({"role": "assistant", "content": err})
        else:
            history = [{"role": "assistant", "content": err}]
        yield (history, err, send_state, stop_state,
               cur_status, list(file_paths or []), no_change, no_change)
    finally:
        # ---- Phase 3: Exit "running" mode ----
        # Restore buttons, clear uploaded files, clear msg textbox
        yield (
            history if history else [],
            gr.update(),       # keep metrics as-is
            gr.update(visible=True, interactive=True),     # show Send
            gr.update(visible=False, interactive=False, value="⏹️"),  # hide Stop
            gr.update(value="No files uploaded"),  # clear file_status text
            [],                                     # clear uploaded_files state
            gr.update(value=""),                     # clear msg textbox
            no_change,                               # keep agent
        )


# ============================================================
# UI BUILDER
# ============================================================

def build_providers_panel():
    """Provider/API-key settings (up to MAX_SLOTS, top = tried first)."""
    from providers import (PRESETS, PRESET_VISION, MAX_SLOTS, load_providers, save_providers,
                           mask_key, test_connection, environment_providers,
                           list_models, benchmark_model, format_benchmark)
    saved = load_providers()
    saved += [{"preset": "Custom", "base_url": "", "model": "", "api_key": "", "enabled": False}] * (MAX_SLOTS - len(saved))

    with gr.Accordion("🔑 Providers & API keys", open=not any(p["api_key"] for p in saved)):
        gr.Markdown("Top = tried first. If it is rate-limited or down, the next one is used. "
                    "UI saves go to `providers.json`. On Render free these are temporary. "
                    "Set AGENT_PROVIDERS_JSON in Render Environment for settings that survive restarts.")
        if environment_providers():
            gr.Markdown("Environment provider settings are active. Local Save overrides them only until restart.")
        slots = []
        for i in range(MAX_SLOTS):
            s = saved[i]
            with gr.Group():
                gr.Markdown(f"**Provider {i + 1}**")
                preset = gr.Dropdown(list(PRESETS), value=s["preset"], label="Preset")
                base = gr.Textbox(value=s["base_url"], label="Base URL")
                model = gr.Textbox(value=s["model"], label="Model")
                key = gr.Textbox(label="API key", type="password",
                                 placeholder="leave empty to keep saved key")
                hint = gr.Markdown(f"Key: {mask_key(s['api_key'])}")
                on = gr.Checkbox(value=s["enabled"], label="Enabled")
                vision = gr.Checkbox(value=s.get("vision", False), label="Vision (model can see images)")
                test_btn = gr.Button("Test connection", size="sm")
                with gr.Row():
                    fetch_btn = gr.Button("Fetch models", size="sm")
                    bench_btn = gr.Button("Benchmark (5 calls)", size="sm")
                models_dd = gr.Dropdown([], label="Models from provider", allow_custom_value=True,
                                        interactive=True)
                result = gr.Markdown()
            preset.change(lambda n: (*PRESETS[n], PRESET_VISION.get(n, False)) if n != "Custom"
                          else (gr.update(), gr.update(), gr.update()),
                          preset, [base, model, vision])
            models_dd.input(lambda v: v or gr.update(), models_dd, model)
            slots.append((preset, base, model, key, hint, on, vision, test_btn, result,
                          fetch_btn, bench_btn, models_dd))

        def _saved_key(typed_key, idx):
            return typed_key or (load_providers() + [{}] * MAX_SLOTS)[idx].get("api_key", "")

        def _fetch_models(base_url, typed_key, idx):
            k = _saved_key(typed_key, idx)
            try:
                ids = list_models(base_url.strip(), k)
            except Exception as e:
                return gr.update(), f"❌ Hindi makuha ang models: {type(e).__name__}: {str(e).replace(k, '***')[:200]}"
            return gr.update(choices=ids), f"✅ {len(ids)} models. Pumili sa dropdown para ilagay sa Model."

        def _benchmark(base_url, model_name, typed_key, idx):
            k = _saved_key(typed_key, idx)
            try:
                return format_benchmark(model_name.strip(),
                                        benchmark_model(base_url.strip(), model_name.strip(), k))
            except Exception as e:
                return f"❌ {type(e).__name__}: {str(e).replace(k, '***')[:200]}"

        def _test(base_url, model_name, typed_key, idx):
            k = typed_key or (load_providers() + [{}] * MAX_SLOTS)[idx].get("api_key", "")
            return test_connection(base_url.strip(), model_name.strip(), k)

        for i, (preset, base, model, key, hint, on, vision, test_btn, result,
                fetch_btn, bench_btn, models_dd) in enumerate(slots):
            test_btn.click(lambda b, m, k, i=i: _test(b, m, k, i), [base, model, key], result,
                           show_progress="full")
            fetch_btn.click(lambda b, k, i=i: _fetch_models(b, k, i), [base, key], [models_dd, result],
                            show_progress="full")
            bench_btn.click(lambda b, m, k, i=i: _benchmark(b, m, k, i), [base, model, key], result,
                            show_progress="full")

        def _save(*vals):
            old = load_providers() + [{}] * MAX_SLOTS
            items, hints = [], []
            for i in range(MAX_SLOTS):
                preset, base_url, model_name, typed, enabled, has_vision = vals[i * 6:(i + 1) * 6]
                k = typed.strip() or old[i].get("api_key", "")
                items.append({"preset": preset, "base_url": base_url.strip(),
                              "model": model_name.strip(), "api_key": k, "enabled": bool(enabled),
                              "vision": bool(has_vision)})
                hints += [gr.update(value=""), f"Key: {mask_key(k)}"]
            save_providers(items)
            return hints + ["✅ Saved."]

        save_btn = gr.Button("Save providers", variant="primary")
        status = gr.Markdown()
        ins, outs = [], []
        for preset, base, model, key, hint, on, vision, *_ in slots:
            ins += [preset, base, model, key, on, vision]
            outs += [key, hint]
        save_btn.click(_save, ins, outs + [status], show_progress="full")


def build_app():
    theme = gr.themes.Soft(primary_hue="blue", secondary_hue="green")

    css = """
    .sandbox-info {
        background: #e8f5e9;
        border: 1px solid #4caf50;
        border-radius: 8px;
        padding: 10px;
        margin: 10px 0;
        font-size: 13px;
    }
    .danger-zone {
        background: #ffebee;
        border: 1px solid #f44336;
        border-radius: 8px;
        padding: 10px;
        margin: 10px 0;
        font-size: 13px;
    }
    .metrics-panel {
        background: #f5f5f5;
        border: 1px solid #ddd;
        border-radius: 8px;
        padding: 10px;
        margin: 10px 0;
        font-size: 12px;
        font-family: monospace;
    }
    @media (max-width: 640px) {
        .gradio-container { padding: 8px !important; }
        #agent-chat { height: 50svh !important; min-height: 260px; }
        input, textarea { font-size: 16px !important; }
    }
    footer { display: none !important; }
    """

    with gr.Blocks(
        title="🤖 AI Agent - Sandboxed Local Assistant",
        theme=theme,
        css=css,
    ) as app:

        # ---- Header ----
        gr.Markdown(
            """
            # 🤖 AI Agent
            **Private AI assistant** with streaming responses, file tools,
            temporary sandbox, and a real Docker sandbox option.
            """
        )

        if os.environ.get("RENDER", "").lower() == "true":
            gr.Markdown("⚠️ Render free: local keys, memory, files and installed tools can disappear "
                        "on restart or idle sleep. This service uses a venv, not Docker isolation. "
                        "Use only your own login and do not add unrelated secrets.")

        # Per-session agent (created fresh for each browser session)
        agent_state = gr.State(lambda: AIAgent())

        with gr.Row():
            # ---- LEFT: chat + input ----
            with gr.Column(scale=4):
                approval_md = gr.Markdown(visible=False, elem_id="approval-banner")
                with gr.Row(visible=False) as approval_row:
                    approve_btn = gr.Button("✅ Approve", variant="primary")
                    deny_btn = gr.Button("❌ Deny", variant="stop")
                chatbot = gr.Chatbot(
                    label="Chat",
                    elem_id="agent-chat",
                    height=550,
                    type="messages",   # Gradio 5.x required
                    allow_tags=False,  # Gradio 5.50+ default change
                    show_label=False,
                )

                with gr.Row():
                    upload_btn = gr.UploadButton(
                        "📎 Upload",
                        file_count="multiple",
                        file_types=["file"],
                        variant="secondary",
                        scale=0,
                    )
                    file_status = gr.Textbox(
                        value="No files uploaded",
                        interactive=False,
                        show_label=False,
                        scale=4,
                    )

                with gr.Row():
                    msg = gr.Textbox(
                        placeholder="Ano ang gagawin natin ngayon? (Press Enter to send)",
                        show_label=False,
                        scale=5,
                        container=False,
                    )
                    send_btn = gr.Button("Send 🚀", variant="primary", scale=1)
                    stop_btn = gr.Button("⏹️ Stop", variant="stop", scale=1, visible=False)

                with gr.Row():
                    clear_btn = gr.Button("🗑️ Clear", scale=0)
                    example_dd = gr.Dropdown(
                        choices=[
                            "Gawa ka ng hello.py tapos i-edit mo yung function name",
                            "I-search mo kung paano gumawa ng FastAPI app, tapos basahin mo yung top result",
                            "I-install mo ang rich at gawa ka ng colored output",
                            "Gumawa ka ng simple Flask web server",
                            "Basahin mo yung uploaded na docx file at i-summarize",
                            "Test: subukan mong i-delete ang C:\\Windows (blocked dapat)",
                            "Test: format C: (blocked dapat)",
                        ],
                        label="💡 Examples",
                        interactive=True,
                        scale=4,
                    )

            # ---- RIGHT: settings + status ----
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ Settings")
                build_providers_panel()
                thinking_checkbox = gr.Checkbox(
                    value=False, label="Enable Nemotron thinking (slower)",
                    info="Off by default for faster answers. Reasoning text stays private.")

                gr.Markdown("---")

                # Sandbox status (updated via chat events, not auto-refresh)
                sandbox_md = gr.Markdown(
                    lambda: format_sandbox_status(AIAgent()),
                )

                gr.Markdown("---")

                # Metrics (updated via chat events)
                metrics_md = gr.Markdown(lambda: format_metrics(AIAgent()))

                gr.Markdown("---")

                # Tools list
                gr.Markdown(
                    f"### 🛠️ Tools ({len(TOOL_FUNCTIONS)})\n"
                    + "\n".join(f"- `{name}`" for name in sorted(TOOL_FUNCTIONS.keys()))
                )

                gr.Markdown("---")

                gr.Markdown(
                    """
                    ### 🚫 Blocked by default
                    - `format`, `shutdown`, `diskpart`
                    - Deleting system directories
                    - Registry modifications
                    - Pip injection vectors
                    - Path traversal attacks
                    - Credential file reads

                    ### 📦 Sandbox info
                    - All code runs in a **temporary sandbox**
                    - Installed packages are session-only
                    - Host machine is **never modified**
                    - Install Docker for stronger isolation
                    """
                )

        # ============================================================
        # EVENT WIRING
        # ============================================================

        uploaded_files = gr.State([])

        def set_thinking(enabled, agent):
            agent.enable_thinking = bool(enabled)
            return agent
        thinking_checkbox.change(set_thinking, inputs=[thinking_checkbox, agent_state],
                                 outputs=[agent_state], queue=False)

        # Upload handler
        upload_btn.upload(
            handle_upload,
            inputs=[upload_btn],
            outputs=[file_status, uploaded_files],
        )

        # Main chat event
        chat_inputs = [msg, chatbot, uploaded_files, agent_state]
        # Outputs: chatbot (history), metrics, send_btn, stop_btn,
        #          file_status, uploaded_files (clear on exit),
        #          msg (clear textbox on exit), agent_state
        chat_outputs = [
            chatbot, metrics_md, send_btn, stop_btn,
            file_status, uploaded_files, msg, agent_state,
        ]

        send_btn.click(
            start_chat,
            inputs=chat_inputs,
            outputs=chat_outputs,
        )
        msg.submit(
            start_chat,
            inputs=chat_inputs,
            outputs=chat_outputs,
        )

        # Approval banner: poll for a pending request from the running agent
        def poll_approval(agent: AIAgent):
            text = approval_gate.pending(agent.session_id)
            if not text:
                return gr.update(visible=False), gr.update(visible=False)
            return (gr.update(value=f"### ⏸️ Approval needed\n```\n{text}\n```", visible=True),
                    gr.update(visible=True))

        def answer_approval(agent: AIAgent, approved: bool):
            approval_gate.answer(agent.session_id, approved)
            return gr.update(visible=False), gr.update(visible=False)

        approval_timer = gr.Timer(1.0)
        approval_timer.tick(poll_approval, inputs=[agent_state], outputs=[approval_md, approval_row],
                            show_progress="hidden")
        approve_btn.click(lambda a: answer_approval(a, True), inputs=[agent_state],
                          outputs=[approval_md, approval_row])
        deny_btn.click(lambda a: answer_approval(a, False), inputs=[agent_state],
                       outputs=[approval_md, approval_row])

        # Stop
        stop_btn.click(
            stop_chat,
            inputs=[agent_state],
            outputs=[send_btn, stop_btn], queue=False,
        )

        # Clear
        clear_btn.click(
            clear_chat,
            inputs=[agent_state],
            outputs=[agent_state, chatbot, msg, file_status, uploaded_files, metrics_md],
        )

        # Example dropdown -> message
        example_dd.change(
            lambda choice: choice or "",
            inputs=[example_dd],
            outputs=[msg],
        )

    return app


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    # Silence noisy Gradio deprecation warnings (we're pinned to 5.50 for now)
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="gradio")
    import logging
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)

    print("=" * 50)
    print("  🤖 AI Agent - Sandboxed Local Assistant")
    print("  ⚡ STREAMING ENABLED")
    print("  📦 TEMPORARY SANDBOX")
    print("=" * 50)
    print(f"  📂 Workspace: {WORKSPACE_DIR}")
    print(f"  🛡️  Safety: ACTIVE")
    print("  🌐 Listener and authentication configured by environment")
    print("=" * 50)

    settings = launch_settings()  # validate before constructing any app/session
    app = build_app()
    app.launch(**settings, blocked_paths=[
        str(Path(__file__).parent / name)
        for name in (".env", "providers.json", ".agent_logs", ".sandboxes", ".context")
    ])
