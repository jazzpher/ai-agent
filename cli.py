#!/usr/bin/env python3
"""Terminal coding agent for AI Agent - the same loop as the web UI, in a shell.

    python cli.py                  chat in the current folder
    python cli.py -p "run tests"   one-shot: print the answer and exit
    python cli.py -C ../project    work in another folder
    python cli.py -y               approve every command without asking

The web UI (app.py) is unchanged and keeps using the app's own workspace folder.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import sys
import threading

DEFAULT_APPROVAL_TIMEOUT = "3600"

HELP = (
    "/help            show this help\n"
    "/model <name>    switch model (keeps the provider that owns it)\n"
    "/models          list the configured models\n"
    "/yes             toggle auto-approve for commands\n"
    "/clear           forget the conversation (keeps memory and files)\n"
    "/cwd             show the folder this session works in\n"
    "/exit            quit\n"
)


# ===========================================================
# ARGUMENTS AND ENVIRONMENT
# ===========================================================

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="Terminal coding agent (same engine as the web UI).",
    )
    p.add_argument("-p", "--prompt", help="run one prompt, print the answer, exit")
    p.add_argument("-C", "--cwd", help="folder to work in (default: current folder)")
    p.add_argument("-m", "--model", help="model to use for this session")
    p.add_argument("-y", "--yes", action="store_true",
                   help="approve commands without asking")
    return p.parse_args(argv)


def prepare_env(cwd: str) -> str:
    """Point the agent at the folder we work in. Must run before importing agent."""
    workspace = os.path.realpath(os.path.abspath(cwd))
    os.environ["AGENT_WORKSPACE"] = workspace
    os.environ.setdefault("AGENT_APPROVAL_TIMEOUT", DEFAULT_APPROVAL_TIMEOUT)
    return workspace


# ===========================================================
# TERMINAL STYLING
# ===========================================================

class Style:
    def __init__(self, color: bool = False):
        self.color = bool(color)

    def _wrap(self, code: str, text: str) -> str:
        return "\033[" + code + "m" + text + "\033[0m" if self.color else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def code(self, text: str) -> str:
        return self._wrap("36", text)


def supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# ===========================================================
# SNAPSHOT -> TERMINAL TEXT
# ===========================================================

_PLAN_RE = re.compile(r"\[\[plan:([A-Za-z0-9+/=]+)\]\]")
_STEP_RE = re.compile(r"\[\[planstep:\d+\]\]")
_DETAIL_RE = re.compile(r"\[\[detail:[A-Za-z0-9+/=]+\]\]")
_FILE_RE = re.compile(r"\[\[file:([^\]\n]+)\]\]")
_ASK_RE = re.compile(r"\[\[ask:([^\]\n]+)\]\]")
_WRITING_RE = re.compile(r'<div class="writing-live">.*?</pre></div>', re.S)
_STATUS_RE = re.compile(r"\n*\u23f3[^\n]*\n*")
_ANALYZE_RE = re.compile(r"\n*\U0001f4ad Analyzing your request\u2026\n*")
_APPROVE_HINT_RE = re.compile(
    r"\n*Pindutin ang \u2705 Approve o \u274c Deny sa itaas ng chat\.\n*")
_SUB_RE = re.compile(r"</?sub>")
_TAG_RE = re.compile(r"</?(?:div|span|pre|section|ol|li|br|p)\b[^>]*>")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`\n]+)`")

PLAN_ICONS = {"pending": "\u25cb", "running": "\u25d0", "done": "\u2713", "blocked": "!"}


def _ask_text(match) -> str:
    parts = [p.strip() for p in match.group(1).split("|") if p.strip()]
    if not parts:
        return ""
    lines = ["\n  ? " + parts[0]]
    lines += ["    - " + opt for opt in parts[1:5]]
    return "\n".join(lines) + "\n"


def clean(text: str, style: Style = None) -> str:
    """Turn one raw agent snapshot into plain terminal text (idempotent)."""
    style = style or Style(False)
    if not text:
        return ""
    text = _WRITING_RE.sub("", text)
    text = _STATUS_RE.sub("\n\n", text)
    text = _ANALYZE_RE.sub("\n\n", text)
    text = _DETAIL_RE.sub("", text)
    text = _STEP_RE.sub("", text)
    text = _PLAN_RE.sub("", text)
    text = _APPROVE_HINT_RE.sub("\n", text)
    text = _FILE_RE.sub(lambda m: "\n  + " + m.group(1) + "\n", text)
    text = _ASK_RE.sub(_ask_text, text)
    text = _SUB_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = _BOLD_RE.sub(lambda m: style.bold(m.group(1)), text)
    text = _CODE_RE.sub(lambda m: style.code(m.group(1)), text)
    return text


def extract_plan(text: str):
    """The plan checklist carried in a snapshot, as [(step text, status)]."""
    match = _PLAN_RE.search(text or "")
    if not match:
        return None
    try:
        steps = json.loads(base64.b64decode(match.group(1)))
    except (ValueError, TypeError):
        return None
    if not isinstance(steps, list):
        return None
    out = []
    for step in steps:
        if isinstance(step, dict):
            out.append((str(step.get("text", "")), str(step.get("status", "pending"))))
    return out


# ===========================================================
# RENDERER
# ===========================================================

class Renderer:
    """Prints only what is new in each cumulative snapshot."""

    def __init__(self, stream=None, style: Style = None):
        self.stream = stream if stream is not None else sys.stdout
        self.style = style or Style(supports_color(self.stream))
        self.reset()

    def reset(self):
        self.shown = ""
        self.plan = None

    def write(self, text: str):
        if not text:
            return
        self.stream.write(text)
        self.stream.flush()

    def feed(self, snapshot: str):
        plan = extract_plan(snapshot)
        text = clean(snapshot, self.style)
        cut = text.rfind("[[")
        if cut != -1 and "]]" not in text[cut:]:
            text = text[:cut]  # a marker still arriving
        if plan is not None and plan != self.plan:
            self.emit_plan(plan)
            self.plan = plan
        self.emit_text(text)

    def emit_text(self, text: str):
        if not text.startswith(self.shown):
            same = 0
            limit = min(len(text), len(self.shown))
            while same < limit and text[same] == self.shown[same]:
                same += 1
            self.write("\n")
            self.shown = text[:same]
        new = text[len(self.shown):]
        self.shown = text
        self.write(new)

    def emit_plan(self, plan):
        if self.plan is None:
            self.write("\n" + self.style.dim("plan") + "\n")
            for i, (step, status) in enumerate(plan, 1):
                self.write("  " + PLAN_ICONS.get(status, "\u25cb") + " " + str(i) + ". " + step + "\n")
            self.write("\n")
            return
        for i, (step, status) in enumerate(plan, 1):
            before = self.plan[i - 1][1] if i - 1 < len(self.plan) else None
            if status != before:
                self.write("  " + PLAN_ICONS.get(status, "\u25cb") + " " + str(i) + ". " + step + "\n")


# ===========================================================
# ONE SESSION
# ===========================================================

class Session:
    def __init__(self, agent, renderer: Renderer, auto_yes: bool = False,
                 stdin=None, out=None):
        self.agent = agent
        self.renderer = renderer
        self.auto_yes = bool(auto_yes)
        self.stdin = stdin if stdin is not None else sys.stdin
        self.out = out if out is not None else renderer.stream
        self.style = renderer.style

    # ---------- one turn ----------

    def turn(self, message: str) -> str:
        """Stream one message through the agent. Returns the last snapshot."""
        from approvals import gate

        self.renderer.reset()
        box = queue.Queue()

        def work():
            try:
                for snapshot in self.agent.chat_stream(message):
                    box.put(("snap", snapshot))
            except BaseException as exc:  # noqa: BLE001 - shown to the user below
                box.put(("err", exc))
            finally:
                box.put(("done", None))

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        last = ""
        prompted = None
        cancelled = False
        try:
            while True:
                try:
                    kind, value = box.get(timeout=0.15)
                except queue.Empty:
                    kind, value = None, None
                if kind == "snap":
                    last = value
                    self.renderer.feed(value)
                    continue
                if kind == "err":
                    raise value
                if kind == "done":
                    break
                pending = gate.pending(self.agent.session_id)
                if pending is None:
                    prompted = None
                    continue
                if pending == prompted:
                    continue
                prompted = pending
                approved = True if self.auto_yes else self.ask_approval()
                gate.answer(self.agent.session_id, approved)
                if not approved:
                    self.out.write(self.style.dim("denied") + "\n")
        except KeyboardInterrupt:
            cancelled = True
            self.agent.cancel()
            if gate.pending(self.agent.session_id) is not None:
                gate.answer(self.agent.session_id, False)
            worker.join(timeout=15)
            self.out.write("\n" + self.style.dim("cancelled") + "\n")
        finally:
            worker.join(timeout=5)
        if not cancelled and self.renderer.shown and not self.renderer.shown.endswith("\n"):
            self.out.write("\n")
        return last

    def ask_approval(self) -> bool:
        self.out.write("\n" + self.style.bold("approve?") + " [y/N/a] ")
        self.out.flush()
        try:
            line = (self.stdin.readline() or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if line in ("a", "always"):
            self.auto_yes = True
            return True
        return line in ("y", "yes")

    # ---------- interactive loop ----------

    def banner(self):
        agent = self.agent
        model = getattr(agent, "model", "?")
        provider = getattr(agent, "provider_name", "") or ""
        where = os.environ.get("AGENT_WORKSPACE", "")
        self.out.write(self.style.dim("workspace " + where) + "\n")
        self.out.write(self.style.dim("model " + model + (" via " + provider if provider else "")) + "\n")
        self.out.write(self.style.dim("type /help for commands, /exit to quit") + "\n\n")

    def repl(self) -> int:
        self.banner()
        while True:
            try:
                line = input(self.style.bold("> "))
            except (EOFError, KeyboardInterrupt):
                self.out.write("\n")
                return 0
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    return 0
                continue
            self.turn(line)

    def command(self, line: str) -> bool:
        parts = line.split(None, 1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if name in ("/exit", "/quit", "/q"):
            return False
        if name == "/help":
            self.out.write(HELP)
        elif name == "/model":
            if not arg:
                self.out.write("model: " + str(self.agent.model) + "\n")
            else:
                known = self.agent.use_model(arg)
                note = "" if known else " (not in providers.json, using the current provider)"
                self.out.write("model: " + str(self.agent.model) + note + "\n")
        elif name == "/models":
            providers = getattr(self.agent, "_providers", None) or []
            if not providers:
                self.out.write("no providers configured\n")
            for item in providers:
                mark = "*" if item.get("model") == self.agent.model else " "
                self.out.write(mark + " " + str(item.get("model")) + "  (" + str(item.get("preset", "")) + ")\n")
        elif name == "/yes":
            self.auto_yes = not self.auto_yes
            self.out.write("auto-approve: " + ("on" if self.auto_yes else "off") + "\n")
        elif name == "/clear":
            self.agent.reset()
            self.out.write("conversation cleared\n")
        elif name == "/cwd":
            self.out.write(os.environ.get("AGENT_WORKSPACE", "") + "\n")
        else:
            self.out.write("unknown command " + name + " - try /help\n")
        return True


# ===========================================================
# ENTRY POINT
# ===========================================================

def build_agent(model: str = None):
    """Create the agent and pick a provider (providers.json, then env keys)."""
    import agent as agent_mod

    instance = agent_mod.AIAgent()
    instance.refresh_providers()
    if model:
        if not instance.use_model(model):
            print("note: " + model + " is not in providers.json, using it on the current provider",
                  file=sys.stderr)
    return instance


def main(argv=None) -> int:
    args = parse_args(argv)
    cwd = os.path.abspath(args.cwd or os.getcwd())
    if not os.path.isdir(cwd):
        print("no such folder: " + cwd, file=sys.stderr)
        return 2
    prepare_env(cwd)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows code pages can't print every symbol
        except (AttributeError, ValueError):
            pass

    instance = build_agent(args.model)
    if not getattr(instance, "api_key", ""):
        print("no provider configured. Put a key in .env (NVIDIA_API_KEY, GROQ_API_KEY, "
              "GEMINI_API_KEY or OPENROUTER_API_KEY), or save one in the web UI's Providers tab.",
              file=sys.stderr)
        return 2

    renderer = Renderer(sys.stdout)
    session = Session(instance, renderer, auto_yes=args.yes)
    try:
        if args.prompt:
            session.turn(args.prompt)
            return 0
        if not sys.stdin.isatty():
            data = sys.stdin.read().strip()
            if data:
                session.turn(data)
            return 0
        return session.repl()
    finally:
        try:
            instance.cleanup_sandbox()
        except Exception:  # noqa: BLE001 - never fail on the way out
            pass


if __name__ == "__main__":
    sys.exit(main())
