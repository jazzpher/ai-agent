"""
Context Manager — TencentDB-inspired context offloading and task tracking

Instead of truncating tool results (losing data permanently), we:
1. Save full tool outputs to .context/ files
2. Inject compact summaries into the LLM context
3. Track task state (goal, progress, steps)
4. Extract key facts from conversations (L1 atoms)

The agent can "drill down" by reading .context/ files when it needs details.
"""

import os
import json
import time
import re
from datetime import datetime
from config import WORKSPACE_DIR, CONTEXT_DIR, FACTS_FILE, MAX_SUMMARY_CHARS


class ContextManager:
    """Manages context offloading, task state, and fact extraction."""

    def __init__(self, session_id: str = None):
        self.session_id = session_id or "default"
        self.step_count = 0
        self.task_goal = ""
        self.task_steps: list[str] = []
        self.current_step = 0
        self.task_status = "idle"  # idle, planning, in_progress, done, blocked
        self.facts: list[str] = []

        # Session-specific context directory
        self.context_dir = os.path.join(CONTEXT_DIR, f"session-{self.session_id}")
        os.makedirs(self.context_dir, exist_ok=True)

        # Load existing facts
        self._load_facts()

    # ================================================================
    # CONTEXT OFFLOADING
    # ================================================================

    def offload(self, tool_name: str, arguments: dict, full_output: str) -> str:
        """
        Save full tool output to file, return compact summary for context.

        Args:
            tool_name: Name of the tool that was called
            arguments: Tool arguments (for context)
            full_output: Full tool output (may be thousands of chars)

        Returns:
            Compact summary (under MAX_SUMMARY_CHARS) for injection into context
        """
        self.step_count += 1

        # Save full output to file
        step_file = os.path.join(self.context_dir, f"step_{self.step_count:03d}.md")
        try:
            with open(step_file, "w", encoding="utf-8") as f:
                f.write(f"# Step {self.step_count}: {tool_name}\n\n")
                f.write(f"**Time:** {datetime.now().isoformat(timespec='seconds')}\n\n")
                f.write(f"**Arguments:**\n```json\n{json.dumps(arguments, indent=2, ensure_ascii=False)}\n```\n\n")
                f.write(f"**Output:**\n```\n{full_output}\n```\n")
        except OSError:
            pass

        # Generate compact summary
        summary = self._summarize_tool_result(self.step_count, tool_name, full_output)

        # Save step metadata
        self._save_step_metadata(self.step_count, tool_name, summary)

        return summary

    def _summarize_tool_result(self, step_id: int, tool_name: str, output: str) -> str:
        """Generate a compact summary of a tool result."""
        if not output:
            return f"Step {step_id}: {tool_name} — (no output)"

        # Get first meaningful line
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        first_line = lines[0] if lines else ""

        # Detect status from output
        status = "OK"
        if output.startswith("✅"):
            status = "OK"
        elif output.startswith("❌"):
            status = "FAILED"
        elif output.startswith("🚫"):
            status = "BLOCKED"
        elif "error" in output.lower()[:100]:
            status = "ERROR"
        elif "blocked" in output.lower()[:100]:
            status = "BLOCKED"

        # Compact first line (max 80 chars)
        compact = first_line[:80]
        if len(first_line) > 80:
            compact += "…"

        # Add file reference for drill-down
        return f"Step {step_id}: {tool_name} — {status} — {compact} [→ step_{step_id:03d}.md]"

    def _save_step_metadata(self, step_id: int, tool_name: str, summary: str):
        """Save step metadata for the task state tracker."""
        meta_file = os.path.join(self.context_dir, "steps.jsonl")
        try:
            record = {
                "step": step_id,
                "tool": tool_name,
                "summary": summary,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
            with open(meta_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def recall(self, step_id: int) -> str:
        """Read full output for a specific step (drill-down)."""
        step_file = os.path.join(self.context_dir, f"step_{step_id:03d}.md")
        if os.path.exists(step_file):
            try:
                with open(step_file, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return f"Error reading step {step_id}"
        return f"Step {step_id} not found. Available steps: 1-{self.step_count}"

    def get_recent_summaries(self, n: int = 5) -> str:
        """Get the last N step summaries for context injection."""
        summaries = []
        meta_file = os.path.join(self.context_dir, "steps.jsonl")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[-n:]:
                    record = json.loads(line)
                    summaries.append(record.get("summary", ""))
            except (OSError, json.JSONDecodeError):
                pass
        return "\n".join(summaries) if summaries else ""

    # ================================================================
    # TASK STATE TRACKING
    # ================================================================

    def set_goal(self, goal: str):
        """Set the current task goal."""
        self.task_goal = goal[:200]
        self.task_steps = []
        self.current_step = 0
        self.task_status = "planning"

    def set_plan(self, steps: list[str]):
        """Set the task plan (from analyze pass)."""
        self.task_steps = steps[:10]  # Max 10 steps
        self.current_step = 0
        self.task_status = "in_progress"

    def advance_step(self):
        """Mark current step as done, move to next."""
        if self.current_step < len(self.task_steps):
            self.current_step += 1
        if self.current_step >= len(self.task_steps):
            self.task_status = "done"

    def set_status(self, status: str):
        """Set task status directly."""
        self.task_status = status

    def get_task_context(self) -> str:
        """Get compact task state for context injection."""
        if not self.task_goal:
            return ""

        lines = [f"📋 **Task:** {self.task_goal}"]

        if self.task_steps:
            step_markers = []
            for i, step in enumerate(self.task_steps):
                if i < self.current_step:
                    step_markers.append(f"✅ {step}")
                elif i == self.current_step:
                    step_markers.append(f"⏳ {step}")
                else:
                    step_markers.append(f"⬜ {step}")
            lines.append(" → ".join(step_markers))

        status_emoji = {
            "idle": "💤", "planning": "📝", "in_progress": "⚙️",
            "done": "✅", "blocked": "🚧",
        }
        emoji = status_emoji.get(self.task_status, "❓")
        lines.append(f"{emoji} Status: {self.task_status}")

        return "\n".join(lines)

    # ================================================================
    # L1 FACT EXTRACTION
    # ================================================================

    def add_facts(self, new_facts: list[str]):
        """Add new facts to the fact store."""
        for fact in new_facts:
            fact = fact.strip()
            if fact and fact not in self.facts:
                self.facts.append(fact)
        # Keep max 50 facts
        if len(self.facts) > 50:
            self.facts = self.facts[-50:]
        self._save_facts()

    def get_facts_context(self, max_facts: int = 5) -> str:
        """Get recent facts for context injection."""
        if not self.facts:
            return ""
        recent = self.facts[-max_facts:]
        return "🧠 **Known facts:**\n" + "\n".join(f"- {f}" for f in recent)

    def _load_facts(self):
        """Load facts from file."""
        if os.path.exists(FACTS_FILE):
            try:
                with open(FACTS_FILE, "r", encoding="utf-8") as f:
                    self.facts = [line.strip() for line in f if line.strip()]
            except OSError:
                self.facts = []

    def _save_facts(self):
        """Save facts to file."""
        try:
            with open(FACTS_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(self.facts) + "\n")
        except OSError:
            pass

    # ================================================================
    # FULL CONTEXT BUILDER
    # ================================================================

    def build_context_block(self) -> str:
        """
        Build the full context block to inject into the system prompt.
        This replaces the old simple truncation approach.
        """
        parts = []

        # Task state
        task_ctx = self.get_task_context()
        if task_ctx:
            parts.append(task_ctx)

        # Recent facts
        facts_ctx = self.get_facts_context(max_facts=5)
        if facts_ctx:
            parts.append(facts_ctx)

        # Recent step summaries
        if self.step_count > 0:
            recent = self.get_recent_summaries(n=3)
            if recent:
                parts.append(f"📊 **Recent actions:**\n{recent}")

        # Drill-down hint
        if self.step_count > 0:
            parts.append(
                f"💡 **Tip:** Use `recall_step(step_id)` to read full details "
                f"of any step (1-{self.step_count})."
            )

        return "\n\n".join(parts) if parts else ""

    # ================================================================
    # FACT EXTRACTION PROMPT (for LLM)
    # ================================================================

    EXTRACTION_PROMPT = """Extract key facts from this conversation turn. Only include NEW information not already known.

Rules:
- Each fact should be a single, complete statement
- Focus on: user preferences, project details, decisions made, constraints
- Ignore: tool outputs, error messages, system messages
- Maximum 5 facts per extraction

Conversation:
{conversation}

Existing facts:
{existing_facts}

Return as a JSON array of strings. Example: ["User prefers dark mode", "Project uses Python 3.12"]
If no new facts, return: []"""

    # ================================================================
    # CLEANUP
    # ================================================================

    def cleanup(self):
        """Clean up context files for this session."""
        import shutil
        try:
            shutil.rmtree(self.context_dir, ignore_errors=True)
        except Exception:
            pass

    def get_stats(self) -> dict:
        """Get context manager statistics."""
        return {
            "session_id": self.session_id,
            "steps_offloaded": self.step_count,
            "facts_count": len(self.facts),
            "task_status": self.task_status,
            "task_goal": self.task_goal,
            "context_dir": self.context_dir,
        }
