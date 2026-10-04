"""Light agentic helpers: plan parsing and a small persistent memory store.

No network, no keys. Memory lives in the workspace MEMORY.md file (the same file
the agent already loads each turn). On Render Free that file is wiped on restart.
"""
import os
import re
import threading

MAX_PLAN_STEPS = 6
MAX_MEMORY_ITEMS = 40
MAX_ITEM_CHARS = 300
_LOCK = threading.Lock()
_SECRET = re.compile(r"(nvapi-[\w-]{8,}|sk-[\w-]{12,}|gsk_[\w-]{12,}|AIza[\w-]{20,}|ghp_\w{20,}|password\s*[:=]\s*\S+)", re.I)


def parse_plan(analysis: str) -> list:
    """Numbered steps from the **Plan:** block of the analysis. Empty if none."""
    if not analysis:
        return []
    m = re.search(r"\*{0,2}Plan:?\*{0,2}:?\s*\n(.*?)(?:\n\s*\*{0,2}Success criteria|\Z)", analysis, re.S | re.I)
    block = m.group(1) if m else ""
    steps = []
    for line in block.splitlines():
        s = re.match(r"\s*(?:\d+[.)]|[-*])\s+(.+)", line)
        if s:
            steps.append(s.group(1).strip().strip("*").strip()[:200])
    return steps[:MAX_PLAN_STEPS]


def plan_checklist(steps: list) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def _read(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []


def _write(path: str, lines: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def memory_action(path: str, action: str, text: str = "") -> dict:
    """add / list / forget on the memory file. Never stores things that look like keys."""
    action = (action or "").strip().lower()
    text = " ".join((text or "").split())
    with _LOCK:
        lines = _read(path)
        facts = [ln for ln in lines if ln.startswith("- ")]
        if action == "list":
            return {"status": "success", "output": "\n".join(facts) or "(memory is empty)"}
        if action == "add":
            if not text:
                return {"status": "error", "output": "Give the fact to remember in `text`."}
            if _SECRET.search(text):
                return {"status": "error", "output": "Not saved: it looks like a key or password. Never store secrets."}
            text = text[:MAX_ITEM_CHARS]
            if any(f[2:].lower() == text.lower() for f in facts):
                return {"status": "success", "output": "Already remembered."}
            if len(facts) >= MAX_MEMORY_ITEMS:
                return {"status": "error", "output": f"Memory full ({MAX_MEMORY_ITEMS}). Forget something first."}
            _write(path, lines + [f"- {text}"])
            return {"status": "success", "output": f"Remembered: {text}"}
        if action == "forget":
            if not text:
                return {"status": "error", "output": "Give part of the fact to forget in `text`."}
            keep = [ln for ln in lines if not (ln.startswith("- ") and text.lower() in ln.lower())]
            removed = len(lines) - len(keep)
            if removed:
                _write(path, keep)
            return {"status": "success", "output": f"Forgot {removed} item(s)."}
    return {"status": "error", "output": "action must be add, list or forget."}
