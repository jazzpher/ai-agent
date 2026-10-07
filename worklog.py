"""Display-only helpers: turn the raw tool-step text of a reply into a collapsible work log,
file cards with inline preview, quick-choice chips and a final file summary.
Pure functions; the agent's own message history is never changed."""
import html
import plan_ui
import base64
import json
from functools import lru_cache
import os
import re

CATEGORIES = {
    "update_plan": "Plan updates", "run_bash": "Ran commands", "run_python": "Ran commands", "pip_install": "Ran commands",
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
_DETAIL = re.compile(r"\[\[detail:([A-Za-z0-9+/=]+)\]\]")
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
        f'<span class="wl-t">{_fmt_secs(s[2])}</span><div class="wl-s">{_esc(s[3])}</div>'
        + (s[4] if len(s) > 4 else "") + "</div>" for s in steps)
    return (f'<details class="wl"><summary><span class="wl-i">{ICONS.get(category, "")}</span> {label}'
            f'<span class="wl-t">{_fmt_secs(total)}{" · issue" if bad else ""}</span></summary>{rows}</details>')


def _block_html(inner, running):
    """inner = text after the Action line up to the block end. Returns (html, leftover_text)."""
    groups, left, checks = [], [], []
    current_plan = ""
    for line in inner.split("\n"):
        if line.startswith('<div class="plan-command-label">'):
            current_plan = line
            groups.append((current_plan, []))
            continue
        detail = _DETAIL.fullmatch(line.strip())
        if detail and groups and groups[-1][1]:
            try:
                data = json.loads(base64.b64decode(detail[1]))
                boxes = "".join('<div class="wl-box"><div class="wl-box-head">' + label +
                    '<button type="button" class="wl-copy">Copy</button></div><pre>' + _esc(data.get(key, "")) + '</pre></div>'
                    for label, key in (("Input", "input"), ("Output", "output")))
                group = groups[-1][1]
                group[-1] = (*group[-1][:4], '<div class="wl-detail">' + boxes + '</div>')
            except (ValueError, TypeError, KeyError):
                pass
            continue
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
    out = "".join(c if c.startswith('<div class="plan-command-label">') else _group_html(c, s) for c, s in groups)
    if checks:
        out += ('<details class="wl"><summary><span class="wl-i">&#10003;</span> Self-check'
                '</summary>' + "".join(f'<div class="wl-row"><div class="wl-s">{_esc(c)}</div></div>' for c in checks)
                + "</details>")
    if running and not groups:
        out += '<div class="wl-run">Working…</div>'
    return out, "\n".join(left)


def tool_detail(args, result):
    # Display public tool inputs/outputs only, not private reasoning or provider metadata.
    public = {k: v for k, v in (args or {}).items() if k not in {"session_id", "api_key", "password", "token"}}
    raw = public.get("command") or public.get("code") or json.dumps(public, ensure_ascii=False, indent=2)
    output = result.get("output", "") if isinstance(result, dict) else str(result)
    def clip(text):
        text = str(text)
        return text[:12000] + ("\n[Display cut short]" if len(text) > 12000 else "")
    payload = base64.b64encode(json.dumps({"input": clip(raw), "output": clip(output)}, ensure_ascii=False).encode()).decode()
    return "[[detail:" + payload + "]]\n"


def _json_string_prefix(raw, key):
    """Decode the complete prefix of one JSON string, including split escapes."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', raw)
    if not m:
        return ""
    out, i = [], m.end()
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '"': '"', '/': '/', '\\': '\\'}
    while i < len(raw):
        c = raw[i]
        if c == '"':
            break
        if c == '\\':
            if i + 1 >= len(raw):
                break
            e = raw[i + 1]
            if e == 'u':
                if i + 6 > len(raw):
                    break
                try:
                    out.append(chr(int(raw[i+2:i+6], 16)))
                except ValueError:
                    break
                i += 6
                continue
            out.append(escapes.get(e, e)); i += 2
        else:
            out.append(c); i += 1
    return "".join(out)


def writing_activity(tool_calls):
    sections = []
    for item in tool_calls.values():
        name, raw = item.get("name", ""), item.get("arguments", "")
        if name not in {"write_file", "edit_file"}:
            continue
        path = _json_string_prefix(raw, "path")
        code = _json_string_prefix(raw, "content" if name == "write_file" else "new_string")
        # Last 24 lines keep streaming output bounded even for a large document.
        lines = code.splitlines(); start = max(0, len(lines) - 24)
        shown = "\n".join(f"{i+1:>4}  {line[:400]}" for i, line in enumerate(lines[start:], start))
        sections.append('<div class="writing-live"><div class="writing-title"><span class="busy-dot"></span>' +
                        ("Writing " if name == "write_file" else "Editing ") + _esc(path or "file…") +
                        '</div><pre>' + _esc(shown) + '</pre></div>')
    return "\n\n" + "".join(sections) if sections else ""


def file_card(path, rel=None):
    try:
        stat = os.stat(path)
        return _file_card_cached(path, rel, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""


@lru_cache(maxsize=24)
def _file_card_cached(path, rel, mtime, size):
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
    if ext in {".html", ".htm", ".md", ".markdown"} and size <= 2_000_000:
        from file_preview import webpage_source, markdown_body
        raw = open(path, encoding="utf-8", errors="replace").read(200_000)
        body = webpage_source(path) if ext in {".html", ".htm"} else markdown_body(raw)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = re.sub(r"<[^>]*>", "", title_match[1]) if title_match else name
        # Chat sanitizes iframes. PAGE_JS hydrates a sandboxed frame from this bounded envelope.
        envelope = base64.b64encode(json.dumps({"path": name, "html": body, "web": ext in {".html", ".htm"}}, ensure_ascii=False).encode()).decode()
        return ('<div class="fc fc-artifact"><div class="fc-head">' + head + '</div>' +
                '<div class="fc-artifact-preview"><div class="fc-placeholder">' + _esc(title[:120]) + '</div></div>' +
                '<button type="button" class="fc-open" value="' + envelope + '">Open</button></div>')
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
    if not text:
        return text
    if ("🔧 **Action:**" not in text and "[[file:" not in text and "[[ask:" not in text):
        return plan_ui.render(text)
    out, pos = [], 0
    plan_commands = {}
    has_plan = bool(plan_ui.MARKER.search(text))
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
        chunks = re.split(r"(\[\[planstep:\d+\]\])", inner_clean)
        step_id = None
        if not inner_clean.strip() and block_running:
            out.append('<div class="wl-run">Working…</div>')
        for chunk in chunks:
            marker = plan_ui.STEP_MARKER.fullmatch(chunk)
            if marker:
                step_id = int(marker[1])
                continue
            if not chunk.strip():
                if step_id is not None and block_running and has_plan:
                    plan_commands[step_id] = plan_commands.get(step_id, "") + '<div class="wl-run">Working…</div>'
                continue
            h, left = _block_html(chunk, block_running)
            if step_id is not None and has_plan:
                plan_commands[step_id] = plan_commands.get(step_id, "") + h
                if left:
                    out.append(left + "\n")
            else:
                out.append("\n\n" + h + "\n\n" + (left + "\n" if left else ""))
        for fm in _FILE.finditer(inner):
            rel = fm.group(1)
            from file_preview import safe_workspace_path
            real = safe_workspace_path(os.path.join(workspace_dir or "", rel), workspace_dir or ".")
            card = file_card(real, rel) if real else ""
            if card:
                out.append("\n\n" + card + "\n\n")
    out.append(text[pos:])
    s = plan_ui.render("".join(out), plan_commands)

    def _file_sub(m):
        from file_preview import safe_workspace_path
        real = safe_workspace_path(os.path.join(workspace_dir or "", m.group(1)), workspace_dir or ".")
        c = file_card(real, m.group(1)) if real else ""
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


def render_turn(text, agent, workspace_dir=None):
    """Keep all work available, promoting only the accepted final answer."""
    import thinking
    final = getattr(agent, "_display_final", None)
    if final and final in text:
        start = text.rfind(final)
        before, after = text[:start], text[start + len(final):]
        # File deliverables and model attribution remain with the answer.
        extras_at = [i for i in (after.find("**Mga file na ginawa**"), after.find("🤖 Sumagot:")) if i >= 0]
        split = min(extras_at) if extras_at else len(after)
        work = before + after[:split]
        extras = after[split:]
        body = thinking.render(agent) + render(work, workspace_dir)
        if not body.strip():
            return render(final + ("\n\n" + extras if extras else ""), workspace_dir)
        return ('<details class="turn-work"><summary>Thinking and work</summary>\n\n'
                + body + '\n\n</details>\n\n' + render(final + ("\n\n" + extras if extras else ""), workspace_dir))
    body = thinking.render(agent) + render(text, workspace_dir)
    return ('<details class="turn-work" open><summary>Thinking and work · In progress</summary>\n\n'
            + body + '\n\n</details>')
