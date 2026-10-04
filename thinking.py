"""Display-only live "Thinking" panel. Reasoning text lives in a side list on the agent,
is shown in the chat, and is never added to the message history sent to the model."""
import html
import time

MAX_CHARS = 20000


def reset(agent):
    agent.reasoning_blocks = []


def add(agent, text):
    """Append streamed reasoning_content to the current block (starts a block if needed)."""
    if not text:
        return
    blocks = getattr(agent, "reasoning_blocks", None)
    if blocks is None:
        blocks = agent.reasoning_blocks = []
    if not blocks or blocks[-1]["end"] is not None:
        blocks.append({"text": "", "start": time.monotonic(), "end": None})
    b = blocks[-1]
    b["text"] = (b["text"] + text)[-MAX_CHARS:]


def finish(agent):
    blocks = getattr(agent, "reasoning_blocks", None)
    if blocks and blocks[-1]["end"] is None:
        blocks[-1]["end"] = time.monotonic()


def render(agent):
    """HTML for all reasoning blocks of the turn; empty string when the model sent none."""
    out = []
    for b in getattr(agent, "reasoning_blocks", None) or []:
        if not b["text"].strip():
            continue
        live = b["end"] is None
        secs = max(1, round((time.monotonic() if live else b["end"]) - b["start"]))
        unit = "second" if secs == 1 else "seconds"
        label = f"Thinking... {secs}s" if live else f"Thought for {secs} {unit}"
        body = html.escape(b["text"], quote=True)
        out.append(f'<details class="wl think{" think-live" if live else ""}"{" open" if live else ""}>'
                   f'<summary><span class="wl-i">&#9679;</span> {label}</summary>'
                   f'<div class="think-body">{body}</div></details>')
    return "\n\n".join(out) + ("\n\n" if out else "")
