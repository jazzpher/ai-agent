"""Approval gate for risky commands when there is no Docker isolation.

The agent loop asks the gate and blocks until the user approves or denies in
the UI (or until the timeout, which counts as a denial). The gate holds only
the command text shown to the user; it stores no keys.
"""
import os
import threading
import time

APPROVAL_TOOLS = {"run_bash", "run_python", "pip_install"}


def approval_timeout() -> float:
    try:
        return max(5.0, float(os.environ.get("AGENT_APPROVAL_TIMEOUT", "120")))
    except ValueError:
        return 120.0


def approval_mode() -> str:
    """auto = ask only without Docker (default), on = always ask, off = never."""
    mode = os.environ.get("AGENT_APPROVAL", "auto").strip().lower()
    return mode if mode in ("auto", "on", "off") else "auto"


def needs_approval(tool_name: str, risk_level: str, sandbox_mode: str) -> bool:
    """Pure decision. Blocked commands never reach here; they stay blocked."""
    if tool_name not in APPROVAL_TOOLS:
        return False
    mode = approval_mode()
    if mode == "off":
        return False
    if tool_name == "pip_install":
        risk_level = "risky"  # installing code is always worth a look without isolation
    if risk_level != "risky":
        return False
    return mode == "on" or sandbox_mode != "docker"


class ApprovalGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = {}  # session_id -> {"text", "event", "answer"}

    def pending(self, session_id: str):
        with self._lock:
            item = self._pending.get(session_id)
            return item["text"] if item else None

    def request(self, session_id: str, text: str, timeout: float = None,
                cancelled=lambda: False) -> bool:
        """Block until answered. Timeout, cancel, or a second request = deny."""
        timeout = approval_timeout() if timeout is None else timeout
        item = {"text": text, "event": threading.Event(), "answer": False}
        with self._lock:
            self._pending[session_id] = item
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if item["event"].wait(0.25):
                    return bool(item["answer"])
                if cancelled():
                    return False
            return False
        finally:
            with self._lock:
                if self._pending.get(session_id) is item:
                    del self._pending[session_id]

    def answer(self, session_id: str, approved: bool) -> bool:
        with self._lock:
            item = self._pending.get(session_id)
        if not item:
            return False
        item["answer"] = bool(approved)
        item["event"].set()
        return True


gate = ApprovalGate()
