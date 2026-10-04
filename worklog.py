"""Display-only helpers: turn the raw tool-step text of a reply into a collapsible work log,
file cards with inline preview, quick-choice chips and a final file summary.
Pure functions; the agent's own message history is never changed."""
import html
import os
import re

CATEGORIES = {
    "run_bash": "Ran commands", "run_python": "Ran commands", "pip_install": "Ran commands",
    "download_file": "Ran commands", "process_image": "Ran commands", "remove_background": "Ran commands",
    "web_search": "Explored", "fetch_page": "Explored", "job_search": "Explored", "image_search": "Explored",
    "read_file": "Explored", "view_file": "Explored", "list_files": "Explored", "recall_step": "Explored",
    "write_file": "Edited files", "edit_file": "Edited files", "create_letter_docx": "Edited files",
}
ICONS = {"Ran commands": "&gt;_", "Explored": "&#9906;", "Edited files": "&#9998;", "Other": "&#8226;", "Checks": "&#10003;"}
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".py", ".js", ".html", ".css", ".log", ".yaml", ".yml", ".xml", ".sql", ".sh", ".tsv"}
PREVIEW_LINES = 14

_ACTION = re.compile(r"\n*🔧 \*\*Action:\*\*[^\n]*\n+")
_STEP = re.compile(r"^(✅|❌|🚫) `([\w\-]+)` — ([\d.]+)s — (.*)$")
_FILE = re.compile(r"\[\[file:([^\]\n]+)\]\]")
_ASK = re.compile(r"\[\[ask:([^\]\n]+)\]\]")
_END = "\n---\n"


def _esc(s):
    return html.escape(str(s), quote=True)


def _fmt_secs(total):
    return f"{total:.1f}s" if total >= 1 else f"{int(total * 1000)}ms"


def _group_html(category, steps):
    n = len(steps)
    total = sum(s[2] for s in steps)
    label = {"Explored": f"Explored <b>{n}</b> {'read' if n == 1 else 'reads'}"}.get(category, f"{category} <b>{n}</b>")
    bad = any(s[0] != "✅" for s in steps)
    rows = "".join(
        f'<div class="wl-row{" bad" if s[0] != "✅" else ""}"><code>{_esc(s[1])}</code>'
        f'<span class="wl-t">{_fmt_secs(s[2])}</span><div class="wl-s">{_esc(s[3])}</div></div>'
        for s in steps)
    return (f'<details class="wl"><summary><span class="wl-i">{ICONS.get(category, "")}</span> {label}'
            f'<span class="wl-t">{_fmt_secs(total)}{" · issue" if bad else ""}</span></summary>{rows}</details>')


def _block_html(inner, running):
    """inner = text after the Action line up to the block end. Returns (html, leftover_text)."""
    groups, left, checks = [], [], []
    for line in inner.split("\n"):
        m = _STEP.match(line.strip())
        if m:
            cat = CATEGORIES.get(m.group(2), "Other")
            step = (m.group(1), m.group(2), float(m.group(3)), m.group(4))
            if groups and groups[-1][0] == cat:
                groups[-1][1].append(step)
            else:
                groups.append((cat, [step]))
        elif line.strip().startswith(("🔍", "✅ Looks", "🛠️ Needs", "🔄 Re-plan", "   →", "✅ (could")):
            checks.append(line.strip())
        elif line.strip() and line.strip() != "---":
            left.append(line)
    out = "".join(_group_html(c, s) for c, s in groups)
    if checks:
        out += ('<details class="wl"><summary><span class="wl-i">&#10003;</span> Self-check'
                '</summary>' + "".join(f'<div class="wl-row"><div class="wl-s">{_esc(c)}</div></div>' for c in checks)
                + "</details>")
    if running and not groups:
        out += '<div class="wl-run">Working…</div>'
    return out, "\n".join(left)


def file_card(path, rel=None):
    """HTML card with inline preview for text-like files."""
    name = rel or os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    kb = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
    tag = (ext[1:] or "file").upper()[:6]
    head = f'<span class="fc-tag">{_esc(tag)}</span><span class="fc-name">{_esc(name)}</span><span class="wl-t">{kb}</span>'
    if ext in TEXT_EXT and size <= 2_000_000:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read(40_000).splitlines()
        except OSError:
            lines = []
        shown = "\n".join(lines[:PREVIEW_LINES])
        more = f'<div class="fc-more">+{len(lines) - PREVIEW_LINES} more lines. Open in Files.</div>' if len(lines) > PREVIEW_LINES else ""
        return f'<details class="fc" open><summary>{head}</summary><pre class="fc-pre">{_esc(shown)}</pre>{more}</details>'
    return f'<div class="fc fc-bin">{head}<div class="fc-more">Open in Files to preview.</div></div>'


def render(text, workspace_dir=None, running=False):
    """Rewrite raw agent text. Idempotent on text without markers."""
    if not text or ("🔧 **Action:**" not in text and "[[file:" not in text and "[[ask:" not in text):
        return text
    out, pos = [], 0
    for m in _ACTION.finditer(text):
        start = max(m.start(), pos)
        out.append(text[pos:start])
        end = text.find(_END, m.end())
        block_running = end == -1
        inner = text[m.end(): end if end != -1 else len(text)]
        nxt = _ACTION.search(inner)
        if nxt:  # next action starts before a rule: end this block there
            end = m.end() + nxt.start()
            inner = text[m.end():end]
            block_running = False
            pos = end
        else:
            pos = (end + len(_END)) if end != -1 else len(text)
        inner_clean = _FILE.sub("", inner)
        h, left = _block_html(inner_clean, block_running)
        out.append("\n\n" + h + "\n\n" + (left + "\n" if left else ""))
        for fm in _FILE.finditer(inner):
            rel = fm.group(1)
            real = os.path.join(workspace_dir or "", rel)
            card = file_card(real, rel)
            if card:
                out.append("\n\n" + card + "\n\n")
    out.append(text[pos:])
    s = "".join(out)

    def _file_sub(m):
        real = os.path.join(workspace_dir or "", m.group(1))
        c = file_card(real, m.group(1))
        return "\n\n" + c + "\n\n" if c else ""
    s = _FILE.sub(_file_sub, s)

    def _ask_sub(m):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        if not parts:
            return ""
        q, opts = parts[0], parts[1:5]
        chips = "".join(f'<button type="button" class="qc">{_esc(o)}</button>' for o in opts)
        return f'\n\n<div class="qa"><div class="qa-q">{_esc(q)}</div><div class="qa-c">{chips}</div></div>\n\n'
    s = _ASK.sub(_ask_sub, s)
    return re.sub(r"\n{4,}", "\n\n", s)


def files_summary(rel_paths, workspace_dir):
    if not rel_paths:
        return ""
    lines = ["\n\n**Mga file na ginawa**\n"]
    for rel in rel_paths[:20]:
        try:
            kb = os.path.getsize(os.path.join(workspace_dir, rel)) / 1024
        except OSError:
            continue
        lines.append(f"- `{rel}` ({kb:.1f} KB)")
    lines.append("\nBuksan ang 📁 sa taas para makita lahat ng file.\n")
    return "\n".join(lines)
