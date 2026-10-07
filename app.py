"""
AI Agent - Web UI (Gradio 5.x compatible)

New in this version:
- Per-session agent instances (no more shared singleton)
- Temporary sandbox for all tool execution
- Sandbox status display (venv/docker mode)
- view_file tool for docx/pdf/pptx/images
- Metrics panel with sandbox info
"""
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import gradio as gr

from ui_design import CSS, HEADER, EMPTY_CHAT, workspace_theme, PAGE_JS, COPY_JS
from server_settings import launch_settings
from agent import AIAgent
from config import NVIDIA_API_KEY, DEFAULT_MODEL, WORKSPACE_DIR
import thinking
import worklog
import files_tree
from file_preview import render_preview, safe_workspace_path
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
            info_lines.append(f"{filename} ({size:,} bytes)")
        except Exception as e:
            info_lines.append(f"Error: {os.path.basename(file_path)}: {e}")

    return workspace_paths, "\n".join(info_lines) if info_lines else "No files uploaded"


# ============================================================
# METRICS PANEL
# ============================================================

def format_metrics(agent) -> str:
    m = agent.get_metrics()
    sandbox_mode = m.get("sandbox_mode", "unknown")
    sandbox_pkg_count = m.get("sandbox_packages", 0)

    return (
        f"**Session metrics**\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Session | `{m['session_id']}` |\n"
        f"| Model | `{m['model']}` |\n"
        f"| Elapsed | {m['elapsed_seconds']}s |\n"
        f"| Iterations | {m['iterations']} |\n"
        f"| Tool calls | {m['tool_calls']} |\n"
        f"| Errors | {m['errors']} |\n"
        f"| Prompt tokens | {m['prompt_tokens']:,} |\n"
        f"| Completion tokens | {m['completion_tokens']:,} |\n"
        f"| **Total tokens** | **{m['total_tokens']:,}** |\n"
        f"| **Est. cost** | **${m['estimated_cost_usd']:.4f}** |\n"
        f"| Sandbox | {sandbox_mode} ({sandbox_pkg_count} pkgs) |\n"
    )


# ============================================================
# SANDBOX STATUS
# ============================================================

def refresh_sandbox_status(agent):
    text = format_sandbox_status(agent)
    if getattr(agent, "_last_sandbox_display", None) == text:
        return gr.skip()
    agent._last_sandbox_display = text
    return text


def approval_warning() -> str:
    mode = approval_mode()
    if mode == "off":
        return "Walang Docker at **naka-off ang approval** (AGENT_APPROVAL=off). Risky commands tatakbo agad."
    return ("Kernel-isolated Bash/Python: no approval prompt in auto mode. "
            "Network package installs still require approval. Venv-only risky commands require approval.")


def format_sandbox_status(agent) -> str:
    status = get_sandbox_status(agent.session_id)
    mode = status.get("mode", "unknown")

    if mode == "not started":
        return "Sandbox: not started. It starts only when a tool needs it."

    if mode in {"bubblewrap", "landlock"}:
        return (f"**Kernel sandbox: ACTIVE ({mode})**\n"
                "- Bash/Python: restricted file access, no network, clean environment, resource limits.\n"
                "- Package installs require approval and get network access inside the sandbox.\n"
                "- Only the tool runner is isolated; the app and built-in fetch/file tools remain trusted.")
    if mode == "docker":
        return (
            f"**Docker sandbox: ACTIVE**\n"
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
            f"**Sandbox: ACTIVE (venv mode)**\n"
            f"- Session: `{status.get('session_id', '?')}`\n"
            f"- Uptime: {status.get('uptime_seconds', 0)}s\n"
            f"- Packages: {pkg_preview or 'installing...'}\n\n"
            f"A venv isolates packages, not files or secrets. Commands can modify this server.\n\n"
            + approval_warning()
        )
    else:
        return (
            f"**Sandbox: Initializing...**\n\n"
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
        display_message += "\n\nUploaded files:\n"
        for fp in processed_paths:
            display_message += f"- `{os.path.basename(fp)}`\n"

    # Gradio 5 'messages' format: list of {"role": ..., "content": ...}
    history = history or []
    history = history + [
        {"role": "user", "content": display_message},
        {"role": "assistant", "content": ""},
    ]

    history[-1]["content"] = "Preparing your request..."
    yield history, format_metrics(agent)
    agent.refresh_providers()  # picks up Settings changes without restart
    apply_model_choice(agent)

    # Stream agent response (with two-pass analyze-then-act)
    for partial in agent.chat_stream(display_message, uploaded_files_info=upload_info):
        history[-1]["content"] = thinking.render(agent) + worklog.render(partial, WORKSPACE_DIR)
        yield history, format_metrics(agent)


# ============================================================
# EVENT HANDLERS
# ============================================================

def clear_chat(agent: AIAgent):
    """Clear chat and destroy the old sandbox."""
    if getattr(agent, "_ui_running", False):
        # Never destroy an active worker's sandbox or start a duplicate run.
        return agent, getattr(agent, "_ui_history", []), gr.update(), gr.update(), gr.update(), gr.update()
    agent.cleanup_sandbox()
    agent.reset()
    # Create a new agent (new session)
    new_agent = AIAgent()
    set_model_choice(getattr(agent, "_picked_model", None), new_agent)  # keep the chat picker
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
            info.append(f"{os.path.basename(path)}")

    return (" | ".join(info) if info else "No files uploaded"), paths


def stop_chat(agent: AIAgent):
    """User clicked the stop button. Cancel the running agent and revert UI."""
    agent.cancel()
    return gr.update(visible=True, interactive=True), gr.update(visible=False, interactive=False, value="Stop")


KEEPALIVE_SECONDS = 8.0


def keepalive(gen, interval=None, cancel=None):
    """Iterate `gen` in a worker thread; yield ("beat", None) whenever it stays
    silent for `interval` seconds (model call, self-check, approval wait...), so
    the browser stream never idles out. Yields ("item", value) otherwise.
    If the consumer goes away (GeneratorExit) the worker is asked to stop."""
    import queue
    import threading
    interval = KEEPALIVE_SECONDS if interval is None else interval
    q = queue.Queue()
    stop = threading.Event()
    DONE = object()

    def pump():
        try:
            for item in gen:
                q.put(("item", item))
                if stop.is_set():
                    break
            q.put(("done", DONE))
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            q.put(("error", e))

    threading.Thread(target=pump, daemon=True).start()
    try:
        while True:
            try:
                kind, val = q.get(timeout=interval)
            except queue.Empty:
                yield ("beat", None)
                continue
            if kind == "done":
                return
            if kind == "error":
                raise val
            yield ("item", val)
    except GeneratorExit:
        stop.set()
        if cancel:
            cancel()
        raise


def run_with_beats(fn, label, make_update=lambda text: text, interval=None):
    """Run a slow blocking call (Test, Benchmark, model list) in a worker thread and
    yield a status line while it works, so the browser connection never sits silent
    long enough for the host proxy to drop it. Yields make_update(text) values; the
    last one is the result. Errors become a readable line, never a dropped stream."""
    start = time.time()

    def work():
        yield fn()

    try:
        yield make_update(f"⏳ {label}…")
        for kind, val in keepalive(work(), interval):
            if kind == "beat":
                yield make_update(f"⏳ {label}… {int(time.time() - start)}s")
            else:
                yield make_update(val)
    except Exception as e:  # noqa: BLE001
        yield make_update(f"❌ {type(e).__name__}: {str(e)[:200]}")


def list_workspace_files(agent=None):
    """Files in the workspace, newest first, for the download panel."""
    found = []
    try:
        for root, dirs, names in os.walk(WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for n in names:
                if n.startswith("."):
                    continue
                path = os.path.join(root, n)
                try:
                    found.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    except OSError:
        pass
    found.sort(reverse=True)
    return [p for _, p in found[:30]] or None


def refresh_panels(agent):
    """Refresh the download list and the Session sandbox status after a turn."""
    try:
        files = list_workspace_files(agent)
    except Exception:
        files = None
    try:
        status = format_sandbox_status(agent)
    except Exception:
        status = gr.update()
    return files, status


EXTRA_MODELS = ["deepseek-ai/deepseek-v4.1-flash"]  # free NVIDIA endpoint, tested in chat


def model_choices():
    """(label, id) pairs: the models of the enabled providers plus known free NVIDIA extras."""
    try:
        from providers import active_providers
        import router
        ids = [p["model"] for p in router.rank_providers(active_providers())]  # biggest first
    except Exception:
        ids = []
    seen, out = set(), []
    for mid in ids + EXTRA_MODELS:
        if mid and mid not in seen:
            seen.add(mid)
            out.append((mid.split("/")[-1], mid))
    return out


def apply_model_choice(agent):
    """Re-apply the chat picker after refresh_providers() resets the model."""
    choice = getattr(agent, "_picked_model", None)
    if choice:
        agent.use_model(choice)


def set_model_choice(choice, agent: AIAgent):
    agent._picked_model = (choice or "").strip() or None
    if agent._picked_model:
        agent.use_model(agent._picked_model)
    return agent


def _rel(path):
    return os.path.relpath(path, WORKSPACE_DIR) if path else None


def _sheet(selected_abs=None):
    """(usage text, tree html) for the Files sheet."""
    rel = _rel(selected_abs) if selected_abs else None
    return files_tree.usage_text(WORKSPACE_DIR), files_tree.tree_html(WORKSPACE_DIR, rel)


def _newest_file():
    files = list_workspace_files() or []
    for ext in (".pdf", ".docx"):
        for p in files:
            if p.lower().endswith(ext):
                return p
    return files[0] if files else None


def open_artifact(rel):
    real = safe_workspace_path(os.path.join(WORKSPACE_DIR, rel or ""), WORKSPACE_DIR)
    if not real:
        return (gr.update(visible=False), "", render_preview(None), gr.update(visible=False))
    return (gr.update(visible=True), os.path.relpath(real, WORKSPACE_DIR), render_preview(real),
            gr.update(value=real, visible=True))


def live_workspace(agent, selected):
    if not getattr(agent, "_ui_running", False):
        return (gr.update(), gr.update())
    signature = tuple(files_tree.scan(WORKSPACE_DIR))
    if signature == getattr(agent, "_ui_tree_signature", None):
        return (gr.update(), gr.update())
    agent._ui_tree_signature = signature
    real = safe_workspace_path(os.path.join(WORKSPACE_DIR, selected or ""), WORKSPACE_DIR)
    return _sheet(real)


def open_files_panel(current=None, pick=None):
    """Show the Files sheet, refresh tree + usage, preview the newest (or picked) file.
    Returns: panel, usage, tree, preview, download, pick, zip button."""
    cur = safe_workspace_path(os.path.join(WORKSPACE_DIR, pick), WORKSPACE_DIR) if pick else None
    target = cur or _newest_file()
    usage, tree = _sheet(target)
    zpath = files_tree.make_zip(WORKSPACE_DIR)
    return (gr.update(visible=True), usage, tree, *preview_selected(target),
            _rel(target) or "", gr.update(value=zpath, visible=bool(zpath)))


def pick_file(rel):
    """User tapped a row in the tree."""
    real = safe_workspace_path(os.path.join(WORKSPACE_DIR, rel or ""), WORKSPACE_DIR)
    usage, tree = _sheet(real)
    return (usage, tree, *preview_selected(real))


def preview_selected(path):
    real = safe_workspace_path(path, WORKSPACE_DIR)
    if not real:
        return render_preview(None), gr.update(value=None, visible=False)
    return render_preview(real), gr.update(value=real, visible=True)


def close_files_panel():
    return gr.update(visible=False)


def auto_open_files(agent):
    """Timer tick: after a run that made a PDF/docx, open the sheet on it once."""
    path = getattr(agent, "_ui_autoopen", None)
    if not path:
        return tuple(gr.update() for _ in range(7))
    agent._ui_autoopen = None
    usage, tree = _sheet(path)
    zpath = files_tree.make_zip(WORKSPACE_DIR)
    return (gr.update(visible=True), usage, tree, *preview_selected(path),
            _rel(path), gr.update(value=zpath, visible=bool(zpath)))


def load_files_only(request: gr.Request = None):
    """Page-load refresh: only needs the workspace files, never the agent state."""
    try:
        return list_workspace_files()
    except Exception:
        return None


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
        ", ".join(os.path.basename(p) for p in file_paths)
        if file_paths
        else "No files uploaded"
    )

    # ---- Phase 1: Enter "running" mode (swap Send -> Stop) ----
    send_state = gr.update(visible=False, interactive=False)
    stop_state = gr.update(visible=True, interactive=True, variant="stop", value="Stop")
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
    closed = False
    waited = 0.0
    try:
        # No cancel on disconnect: a dropped browser stream must not kill the work
        # (files still get written). The Stop button is the only thing that cancels.
        for kind, item in keepalive(chat_stream(message, history, file_paths, agent)):
            if kind == "beat":
                waited += KEEPALIVE_SECONDS
                if history:  # tiny visible change so the update is really sent
                    shown = [dict(m) for m in history]
                    shown[-1]["content"] = (str(shown[-1].get("content", ""))
                                            + f"\n\n⏳ Gumagana pa... {int(waited)}s\n")
                    yield (shown, no_change, send_state, stop_state,
                           cur_status, list(file_paths or []), no_change, no_change)
                continue
            waited = 0.0
            h, metrics = item
            history = h  # preserve the streamed answer when final cleanup runs
            yield (h, metrics, send_state, stop_state,
                   cur_status, list(file_paths or []), no_change, no_change)
    except GeneratorExit:
        closed = True  # client disconnected: a generator must not yield now
        print(json.dumps({"event": "client_disconnected", "waited_s": waited}), flush=True)
        raise
    except Exception as e:
        print(json.dumps({"event": "chat_exception", "error": type(e).__name__,
                          "detail": str(e)[:300]}), flush=True)
        # Surface the error in the metrics panel so the user can see it
        err = f"Error: {type(e).__name__}: {e}"
        if history:
            history.append({"role": "assistant", "content": err})
        else:
            history = [{"role": "assistant", "content": err}]
        yield (history, err, send_state, stop_state,
               cur_status, list(file_paths or []), no_change, no_change)
    finally:
        # ---- Phase 3: Exit "running" mode ----
        # Restore buttons, clear uploaded files, clear msg textbox
        if not closed:
            yield (
                history if history else [],
                gr.update(),       # keep metrics as-is
                gr.update(visible=True, interactive=True),     # show Send
                gr.update(visible=False, interactive=False, value="Stop"),  # hide Stop
                gr.update(value="No files uploaded"),  # clear file_status text
                [],                                     # clear uploaded_files state
                gr.update(value=""),                     # clear msg textbox
                no_change,                               # keep agent
            )


# Short request / durable-in-process run state. A dropped SSE connection
# no longer owns the generator or loses the last reply. Restart still loses it.
def begin_background_chat(message, history, file_paths, agent):
    import threading
    if getattr(agent, "_ui_running", False):
        return poll_background_chat(agent)
    if not message.strip() and not file_paths:
        return poll_background_chat(agent)
    agent._ui_running = True
    _before = files_tree.scan(WORKSPACE_DIR)
    _before_map = {r: (sz, mt) for r, sz, mt in _before}
    agent._ui_history = list(history or [])
    agent._ui_started = time.monotonic()
    agent.cancel_requested = False

    def work():
        try:
            for h, metrics in chat_stream(message, history, file_paths, agent):
                agent._ui_history = [dict(m) for m in h]
                agent._ui_metrics = metrics
        except Exception as error:
            agent._ui_history = list(getattr(agent, "_ui_history", [])) + [
                {"role": "assistant", "content": "Run failed: " + type(error).__name__ + ": " + str(error)[:300]}]
        finally:
            agent._ui_running = False
            try:
                fresh = [(mt, r) for r, sz, mt in files_tree.scan(WORKSPACE_DIR)
                         if _before_map.get(r) != (sz, mt) and r.lower().endswith((".pdf", ".docx", ".html", ".htm"))]
                if fresh:
                    pdfs = [x for x in fresh if x[1].lower().endswith(".pdf")]
                    agent._ui_autoopen = os.path.join(WORKSPACE_DIR, max(pdfs or fresh)[1])
            except Exception:
                pass
    threading.Thread(target=work, daemon=True).start()
    out = list(poll_background_chat(agent))
    # Clear the textbox / attachments once, at submit time (not from the poll).
    out[4] = gr.update(value="No files uploaded")
    out[5] = []
    out[6] = gr.update(value="")
    return tuple(out)


def poll_background_chat(agent):
    history = getattr(agent, "_ui_history", None)
    running = getattr(agent, "_ui_running", False)
    if history is None:
        return (gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(), gr.update())
    shown = [dict(m) for m in history]
    if running and shown and shown[-1].get("role") == "assistant":
        elapsed = int(time.monotonic() - agent._ui_started)
        shown[-1]["content"] += f"\n\n⏳ Running... {elapsed}s"
    # Do not replace the whole chat DOM on quiet polls: preserve open log rows/iframe scroll.
    signature = (running, history, getattr(agent, "_ui_metrics", None))
    changed = signature != getattr(agent, "_ui_poll_signature", None)
    agent._ui_poll_signature = signature
    return (shown if changed else gr.update(), getattr(agent, "_ui_metrics", gr.update()) if changed else gr.update(),
            gr.update(visible=not running, interactive=not running),
            gr.update(visible=running, interactive=running, value="Stop"),
            # Never touch file_status / uploaded_files / msg from the 3 s poll: resetting
            # them while idle wiped what the user was typing or attaching.
            gr.update(), gr.update(), gr.update(), gr.update())


def activity_phase(text):
    """Short phase label from the tail of the live reply (cheap, no model call)."""
    tail = (text or "")[-400:]
    if "Retrying" in tail:
        return "retrying the model"
    if "Waiting for the model" in tail:
        return "waiting for the model"
    if "Model is thinking" in tail or "Analyzing your request" in tail:
        return "thinking"
    if "Running" in tail and "\u23f3" in tail:
        return "running a tool"
    if "**Action:**" in tail:
        return "running a tool"
    return "writing"


def activity_html(agent):
    """Always-visible busy bar. Pure CSS animation; each poll re-renders it, which
    restarts the watchdog animation. If polls stop (server asleep / connection lost)
    the bar flips itself to 'no signal' after a few seconds with no JavaScript."""
    import html as _html
    if not getattr(agent, "_ui_running", False):
        return gr.update(value="", visible=False)
    now = time.monotonic()
    elapsed = int(now - getattr(agent, "_ui_started", now))
    history = getattr(agent, "_ui_history", None) or []
    text = history[-1].get("content", "") if history and isinstance(history[-1].get("content", ""), str) else ""
    mark = (len(text), text[-80:])
    if mark != getattr(agent, "_ui_activity_mark", None):
        agent._ui_activity_mark = mark
        agent._ui_activity_changed = now
    quiet = int(now - getattr(agent, "_ui_activity_changed", now))
    phase = activity_phase(text)
    mm, ss = divmod(elapsed, 60)
    clock = f"{mm}:{ss:02d}"
    if quiet >= 45:
        cls, note = "slow", f"no new output for {quiet}s, still connected"
    else:
        cls, note = "ok", f"last update {quiet}s ago"
    return gr.update(visible=True, value=(
        f'<div class="activity-bar {cls}" role="status" aria-live="off">'
        '<span class="act-dots" aria-hidden="true"><i></i><i></i><i></i></span>'
        f'<span class="act-phase">{_html.escape(phase)}</span>'
        f'<span class="act-time">{clock}</span>'
        f'<span class="act-note">{_html.escape(note)}</span>'
        '<span class="act-lost" aria-hidden="true">no signal from server. It may be restarting; keep this page open.</span>'
        '</div>'))


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

    with gr.Accordion("Providers & API keys", open=False, elem_id="provider-settings"):
        gr.Markdown("Top = tried first. If it is rate-limited or down, the next one is used. "
                    "UI saves go to `providers.json`. On Render free these are temporary. "
                    "Set AGENT_PROVIDERS_JSON in Render Environment for settings that survive restarts.")
        if environment_providers():
            gr.Markdown("Environment provider settings are active. Local Save overrides them only until restart.")
        slots = []
        for i in range(MAX_SLOTS):
            s = saved[i]
            with gr.Accordion(f"Provider {i + 1} - {s['preset']}", open=False, elem_classes=["provider-slot"]):
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

            def go():
                try:
                    ids = list_models(base_url.strip(), k)
                except Exception as e:
                    return gr.update(), f" Hindi makuha ang models: {type(e).__name__}: {str(e).replace(k, '***')[:200]}"
                return gr.update(choices=ids), f" {len(ids)} models. Pumili sa dropdown para ilagay sa Model."

            for kind, val in keepalive((go() for _ in [0])):
                if kind == "beat":
                    yield gr.update(), "⏳ Kinukuha ang models…"
                else:
                    yield val

        def _benchmark(base_url, model_name, typed_key, idx):
            k = _saved_key(typed_key, idx)
            def go():
                try:
                    return format_benchmark(model_name.strip(),
                                            benchmark_model(base_url.strip(), model_name.strip(), k))
                except Exception as e:
                    return f" {type(e).__name__}: {str(e).replace(k, '***')[:200]}"

            yield from run_with_beats(go, "Benchmark running (5 calls, may take a few minutes)")

        def _test(base_url, model_name, typed_key, idx):
            k = typed_key or (load_providers() + [{}] * MAX_SLOTS)[idx].get("api_key", "")
            yield from run_with_beats(lambda: test_connection(base_url.strip(), model_name.strip(), k),
                                      "Testing connection")

        for i, (preset, base, model, key, hint, on, vision, test_btn, result,
                fetch_btn, bench_btn, models_dd) in enumerate(slots):
            def _bind(i=i):
                # Real generator functions (not lambdas that return generators), so Gradio streams them.
                def test_h(b, m, k):
                    yield from _test(b, m, k, i)

                def fetch_h(b, k):
                    yield from _fetch_models(b, k, i)

                def bench_h(b, m, k):
                    yield from _benchmark(b, m, k, i)
                return test_h, fetch_h, bench_h
            test_h, fetch_h, bench_h = _bind()
            test_btn.click(test_h, [base, model, key], result, show_progress="full")
            fetch_btn.click(fetch_h, [base, key], [models_dd, result], show_progress="full")
            bench_btn.click(bench_h, [base, model, key], result, show_progress="full")

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
            return hints + [" Saved."]

        save_btn = gr.Button("Save providers", variant="primary")
        status = gr.Markdown()
        ins, outs = [], []
        for preset, base, model, key, hint, on, vision, *_ in slots:
            ins += [preset, base, model, key, on, vision]
            outs += [key, hint]
        save_btn.click(_save, ins, outs + [status], show_progress="full")


def build_app():
    theme = workspace_theme()
    with gr.Blocks(title="AI Agent | Your workspace", theme=theme, css=CSS, js=PAGE_JS) as app:
        gr.HTML(HEADER)
        folder_btn = gr.Button("\U0001F4C1", elem_id="folder-fab", scale=0, size="sm")
        if os.environ.get("RENDER", "").lower() == "true":
            gr.Markdown("Render free: local keys, memory and files may disappear on restart. "
                        "Tool isolation status is shown in Session and each command work log. Use only your own login.",
                        elem_id="render-notice")
        agent_state = gr.State(lambda: AIAgent())
        with gr.Tabs(elem_id="workspace-tabs"):
            with gr.Tab("Chat", id="chat"):
                with gr.Column(elem_id="chat-workspace"):
                    gr.Markdown("**Conversation** &nbsp; A fresh place to start", elem_id="conversation-heading")
                    approval_md = gr.Markdown(visible=False, elem_id="approval-banner")
                    with gr.Row(visible=False, elem_id="approval-actions") as approval_row:
                        approve_btn = gr.Button("Approve", variant="primary")
                        deny_btn = gr.Button("Deny", variant="stop")
                    chatbot = gr.Chatbot(
                        label="Conversation", elem_id="agent-chat", height=550,
                        type="messages", allow_tags=False, show_label=False,
                        placeholder=EMPTY_CHAT,
                    )
                    activity_bar = gr.HTML(value="", visible=False, elem_id="activity-wrap")
                    with gr.Column(elem_id="composer"):
                        model_dd = gr.Dropdown(choices=model_choices(), value=(model_choices() or [(None, None)])[0][1],
                                               label="Model", allow_custom_value=True, filterable=False,
                                               elem_id="model-picker", container=False,
                                               info=None)
                        with gr.Row(elem_id="compose-row"):
                            msg = gr.Textbox(placeholder="Ask or create...", show_label=False,
                                             lines=1, max_lines=5, scale=5, container=False,
                                             elem_id="message-input")
                            send_btn = gr.Button("Send", variant="primary", scale=0, elem_id="send-button")
                            files_btn = gr.Button("Files", variant="secondary", scale=0, elem_id="files-button")
                            stop_btn = gr.Button("Stop", variant="stop", scale=0, visible=False, elem_id="stop-button")
                        with gr.Row(elem_id="attachment-row"):
                            upload_btn = gr.UploadButton("Attach files", file_count="multiple",
                                file_types=["file"], variant="secondary", scale=0, elem_id="upload-button")
                            file_status = gr.Textbox(value="No files uploaded", interactive=False,
                                show_label=False, scale=4, elem_id="file-status", container=False)
                            clear_btn = gr.Button("New chat", scale=0, elem_id="clear-button")
                        with gr.Accordion("Files & starter prompts", open=False, elem_id="extras"):
                            files_box = gr.File(label="Files in workspace (download)", file_count="multiple",
                                                interactive=False, elem_id="workspace-files")
                            refresh_files_btn = gr.Button("Refresh files", scale=0, elem_id="refresh-files")
                            example_dd = gr.Dropdown(choices=[
                                "Choose a starter prompt...",
                                "Help me outline a project plan",
                                "Gawa ka ng hello.py tapos i-edit mo yung function name",
                                "I-search mo kung paano gumawa ng FastAPI app, tapos basahin mo yung top result",
                                "Basahin mo yung uploaded na docx file at i-summarize",
                            ], show_label=False, label="Try a prompt", value="Choose a starter prompt...",
                               interactive=True, elem_id="examples", filterable=False)
            with gr.Tab("Settings", id="settings"):
                with gr.Column(elem_id="settings-panel"):
                    gr.Markdown("## Make it yours\nChoose your providers and how your assistant thinks. "
                                "Changes apply to your next message.")
                    thinking_checkbox = gr.Checkbox(value=False, label="Enable Nemotron thinking",
                        info="Slower, more considered answers. Off by default. Reasoning text stays private.")
                    build_providers_panel()
            with gr.Tab("Session", id="session"):
                with gr.Column(elem_id="session-panel"):
                    gr.Markdown("## Behind this conversation\nUsage, tools and sandbox details for this session.")
                    sandbox_md = gr.Markdown("Sandbox: not started. It starts only when a tool needs it.")
                    with gr.Accordion("Usage & metrics", open=True):
                        metrics_md = gr.Markdown(lambda: format_metrics(AIAgent()))
                    with gr.Accordion(f"Available tools ({len(TOOL_FUNCTIONS)})", open=False):
                        gr.Markdown("\n".join(f"- `{name}`" for name in sorted(TOOL_FUNCTIONS)))
                    with gr.Accordion("Safety & isolation", open=False):
                        gr.Markdown("**Blocked by default:** destructive system commands, system-directory "
                                    "deletion, registry changes, pip injection, path traversal and credential reads.\n\n"
                                    "**Kernel isolation:** Landlock/seccomp restrict file access, network and host-process actions. "
                                    "Bubblewrap additionally provides namespace isolation when supported. "
                                    "Only the workspace and a private temporary directory are writable. "
                                    "The app's built-in file/fetch tools remain trusted. Venv-only mode is not isolation.\n\n" + approval_warning())

        with gr.Column(visible=False, elem_id="files-panel") as files_panel:
            gr.HTML('<div class="sheet-grip"></div>', elem_id="files-grip")
            with gr.Row(elem_id="files-panel-head"):
                with gr.Column(scale=3, min_width=120):
                    gr.Markdown("**Workspace**", elem_id="files-panel-title")
                    files_usage = gr.Markdown("", elem_id="files-usage")
                files_zip = gr.DownloadButton("Download all", size="sm", scale=0, visible=False,
                                              elem_id="files-zip")
                files_close_btn = gr.Button("Close", size="sm", scale=0, elem_id="files-close")
            files_tree_html = gr.HTML("", elem_id="files-tree")
            files_pick = gr.Textbox(value="", elem_id="files-pick", show_label=False, container=False)
            with gr.Row(elem_id="files-actions"):
                files_copy = gr.Button("Copy", size="sm", scale=0, elem_id="files-copy")
                files_download = gr.DownloadButton("Download", visible=False, size="sm", scale=0, elem_id="files-download")
            files_preview = gr.HTML(render_preview(None), elem_id="files-preview")

        with gr.Column(visible=False, elem_id="artifact-viewer") as artifact_viewer:
            with gr.Row(elem_id="artifact-toolbar"):
                artifact_title = gr.Markdown("", elem_id="artifact-title")
                artifact_download = gr.DownloadButton("Download", visible=False, scale=0)
                artifact_close = gr.Button("Close", scale=0, elem_id="artifact-close")
            artifact_body = gr.HTML("", elem_id="artifact-body")
        artifact_pick = gr.Textbox(value="", elem_id="artifact-pick", show_label=False, container=False)

        # ============================================================
        # EVENT WIRING
        # ============================================================

        uploaded_files = gr.State([])

        def set_thinking(enabled, agent):
            agent.enable_thinking = bool(enabled)
            return agent
        thinking_checkbox.change(set_thinking, inputs=[thinking_checkbox, agent_state],
                                 outputs=[agent_state], queue=False)

        model_dd.change(set_model_choice, inputs=[model_dd, agent_state], outputs=[agent_state], queue=False)

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
            begin_background_chat,
            inputs=chat_inputs,
            outputs=chat_outputs, queue=False,
        ).then(refresh_panels, inputs=[agent_state], outputs=[files_box, sandbox_md],
               show_progress="hidden")
        msg.submit(
            begin_background_chat,
            inputs=chat_inputs,
            outputs=chat_outputs, queue=False,
        ).then(refresh_panels, inputs=[agent_state], outputs=[files_box, sandbox_md],
               show_progress="hidden")
        # Also usable after a dropped connection ("Reconnected"): reload the panels on demand.
        refresh_files_btn.click(refresh_panels, inputs=[agent_state], outputs=[files_box, sandbox_md])
        sheet_outputs = [files_panel, files_usage, files_tree_html, files_preview, files_download,
                         files_pick, files_zip]
        files_btn.click(open_files_panel, inputs=None, outputs=sheet_outputs, queue=False)
        folder_btn.click(open_files_panel, inputs=None, outputs=sheet_outputs, queue=False)
        files_pick.input(pick_file, inputs=[files_pick],
                         outputs=[files_usage, files_tree_html, files_preview, files_download], queue=False)
        files_close_btn.click(close_files_panel, outputs=[files_panel], queue=False)
        files_copy.click(None, js=COPY_JS)
        open_timer = gr.Timer(2.0)
        open_timer.tick(auto_open_files, inputs=[agent_state], outputs=sheet_outputs,
                        queue=False, show_progress="hidden")
        app.load(load_files_only, inputs=None, outputs=[files_box])
        artifact_pick.input(open_artifact, inputs=[artifact_pick],
                            outputs=[artifact_viewer, artifact_title, artifact_body, artifact_download], queue=False)
        artifact_close.click(lambda: gr.update(visible=False), outputs=[artifact_viewer], queue=False)
        open_timer.tick(live_workspace, inputs=[agent_state, files_pick], outputs=[files_usage, files_tree_html],
                        queue=False, show_progress="hidden")
        open_timer.tick(refresh_sandbox_status, inputs=[agent_state],
                        outputs=[sandbox_md], queue=False, show_progress="hidden")
        chat_timer = gr.Timer(1.5)
        chat_timer.tick(poll_background_chat, inputs=[agent_state], outputs=chat_outputs,
                        queue=False, show_progress="hidden")

        activity_timer = gr.Timer(1.0)
        activity_timer.tick(activity_html, inputs=[agent_state], outputs=[activity_bar],
                            queue=False, show_progress="hidden")

        # Approval banner: poll for a pending request from the running agent
        def poll_approval(agent: AIAgent):
            text = approval_gate.pending(agent.session_id)
            if not text:
                return gr.update(visible=False), gr.update(visible=False)
            return (gr.update(value=f"### Approval needed\n```\n{text}\n```", visible=True),
                    gr.update(visible=True))

        def answer_approval(agent: AIAgent, approved: bool):
            approval_gate.answer(agent.session_id, approved)
            return gr.update(visible=False), gr.update(visible=False)

        approval_timer = gr.Timer(2.0)
        approval_timer.tick(poll_approval, inputs=[agent_state], outputs=[approval_md, approval_row],
                            queue=False, show_progress="hidden")
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
            lambda choice: "" if choice == "Choose a starter prompt..." else choice or "",
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
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    print("=" * 50)
    print("   AI Agent - Sandboxed Local Assistant")
    print("   STREAMING ENABLED")
    print("   TEMPORARY SANDBOX")
    print("=" * 50)
    print(f"   Workspace: {WORKSPACE_DIR}")
    print(f"    Safety: ACTIVE")
    print("   Listener and authentication configured by environment")
    print("=" * 50)

    settings = launch_settings()  # validate before constructing any app/session
    app = build_app()
    if os.environ.get("AGENT_SANDBOX_MODE") == "bubblewrap":
        from kernel_sandbox import available
        print("[sandbox] startup capability:", available(), flush=True)
        from sandbox_selftest import startup_test
        startup_test()
    app.launch(**settings, blocked_paths=[
        str(Path(__file__).parent / name)
        for name in (".env", "providers.json", ".agent_logs", ".sandboxes", ".context")
    ])
