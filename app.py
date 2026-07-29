"""
AI Agent - Web UI (Gradio 5.x compatible)

New in this version:
- Gradio 5.x API: theme passed to gr.Blocks(), Chatbot uses type='messages'
- Metrics panel: live tokens, cost, iterations, tool calls
- Sandbox status badge: 🐳 Docker / 🛡️ regex-only
- File preview: shows inline previews of common file types
- Cleaned up event handlers
"""
import os
import shutil
import time
from pathlib import Path

import gradio as gr

from agent import AIAgent
from config import NVIDIA_API_KEY, DEFAULT_MODEL, WORKSPACE_DIR
from tools import get_sandbox_status, TOOL_FUNCTIONS


# ============================================================
# AGENT SINGLETON
# ============================================================

agent = AIAgent()


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

def format_metrics() -> str:
    m = agent.get_metrics()
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
    )


# ============================================================
# SANDBAD STATUS
# ============================================================

def format_sandbox_status() -> str:
    status = get_sandbox_status()
    mode = status.get("mode", "unknown")
    if mode == "docker" and status.get("available"):
        return (
            f"🐳 **Docker sandbox: ACTIVE**\n"
            f"- Version: {status.get('version', '?')}\n"
            f"- Image: `{status.get('image', '?')}`\n"
            f"- Limits: {status.get('memory', '?')} RAM, {status.get('cpus', '?')} CPU\n\n"
            f"All bash/python/pip commands run in an isolated container "
            f"with no network and a read-only filesystem."
        )
    else:
        reason = status.get("reason", "unknown")
        return (
            f"🛡️ **Regex-only sandbox**\n"
            f"- Reason: {reason}\n"
            f"- Install Docker Desktop for real OS-level isolation\n\n"
            f"Dangerous commands are blocked by a regex blocklist. "
            f"This is defense-in-depth, not a true sandbox."
        )


# ============================================================
# CHAT STREAMING
# ============================================================

def chat_stream(message: str, history: list, api_key: str, model: str, file_paths):
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
        yield history, format_metrics()


# ============================================================
# EVENT HANDLERS
# ============================================================

def clear_chat():
    agent.reset()
    return [], "", "No files uploaded", [], format_metrics()


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


def cancel_chat():
    agent.cancel()
    return gr.update(value="⏹️ Cancelling…", interactive=False)


def start_chat(message, history, api_key, model, file_paths):
    """Streaming entry point. Yields (history, metrics) tuples."""
    # Reset cancel flag
    agent.cancel_requested = False

    for h, metrics in chat_stream(message, history, api_key, model, file_paths):
        yield h, metrics, gr.update(value="", interactive=True)


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
            and a real Docker sandbox.
            """
        )

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
                    cancel_btn = gr.Button("⏹️", variant="stop", scale=0, visible=False)

                with gr.Row():
                    clear_btn = gr.Button("🗑️ Clear", scale=0)
                    example_dd = gr.Dropdown(
                        choices=[
                            "Gawa ka ng hello.py tapos i-edit mo yung function name",
                            "I-search mo kung paano gumawa ng FastAPI app, tapos basahin mo yung top result",
                            "I-install mo ang rich at gawa ka ng colored output",
                            "Gumawa ka ng simple Flask web server",
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

                # Sandbox status (auto-loaded)
                sandbox_md = gr.Markdown(format_sandbox_status)

                gr.Markdown("---")

                # Metrics (auto-updated per turn)
                metrics_md = gr.Markdown(format_metrics)

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
        chat_inputs = [msg, chatbot, api_key_input, model_input, uploaded_files]
        chat_outputs = [chatbot, metrics_md, msg]

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

        # Cancel
        cancel_btn.click(cancel_chat, outputs=[send_btn])

        # Clear
        clear_btn.click(
            clear_chat,
            outputs=[chatbot, msg, file_status, uploaded_files, metrics_md],
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
    print("=" * 50)
    print(f"  📂 Workspace: {WORKSPACE_DIR}")
    print(f"  🛡️  Safety: ACTIVE")
    sb = get_sandbox_status()
    if sb.get("available") and sb.get("mode") == "docker":
        print(f"  🐳 Sandbox: Docker ({sb.get('version', '?')})")
    else:
        print(f"  🛡️  Sandbox: regex-only ({sb.get('reason', '?')})")
    print(f"  🌐 URL: http://127.0.0.1:7860")
    print("=" * 50)

    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
    )

