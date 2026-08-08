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

from agent import AIAgent
from config import NVIDIA_API_KEY, DEFAULT_MODEL, WORKSPACE_DIR
from tools import get_sandbox_status, TOOL_FUNCTIONS
from sandbox_session import session_manager


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

def format_sandbox_status(agent) -> str:
    status = get_sandbox_status(agent.session_id)
    mode = status.get("mode", "unknown")

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
            f"⚠️ Host machine is NOT modified. Install Docker for stronger isolation."
        )
    else:
        return (
            f"⏳ **Sandbox: Initializing...**\n\n"
            f"The sandbox will be created on first use."
        )


# ============================================================
# CHAT STREAMING
# ============================================================

def chat_stream(message: str, history: list, api_key: str, model: str, file_paths, agent: AIAgent):
    """Stream a chat response. Uses Gradio 5 'messages' format."""
    if not message.strip() and not file_paths:
        yield history, ""
        return

    if api_key:
        agent.set_api_key(api_key)
    if model:
        agent.set_model(model)

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


def start_chat(message, history, api_key, model, file_paths, agent: AIAgent):
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
        for h, metrics in chat_stream(message, history, api_key, model, file_paths, agent):
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
            **Sandboxed local AI assistant** with streaming responses, file tools,
            temporary sandbox, and a real Docker sandbox option.
            """
        )

        # Per-session agent (created fresh for each browser session)
        agent_state = gr.State(lambda: AIAgent())

        with gr.Row():
            # ---- LEFT: chat + input ----
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    label="Chat",
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
                api_key_input = gr.Textbox(
                    label="NVIDIA API Key",
                    placeholder="nvapi-...",
                    type="password",
                    value=NVIDIA_API_KEY,
                )
                model_input = gr.Textbox(
                    label="Model",
                    value=DEFAULT_MODEL,
                    info="e.g. openai/gpt-oss-120b",
                )

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

        # Upload handler
        upload_btn.upload(
            handle_upload,
            inputs=[upload_btn],
            outputs=[file_status, uploaded_files],
        )

        # Main chat event
        chat_inputs = [msg, chatbot, api_key_input, model_input, uploaded_files, agent_state]
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

        # Stop
        stop_btn.click(
            stop_chat,
            inputs=[agent_state],
            outputs=[send_btn, stop_btn],
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
    print(f"  🌐 URL: http://127.0.0.1:7860")
    print("=" * 50)

    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
    )
