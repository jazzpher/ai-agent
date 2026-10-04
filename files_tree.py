"""Files sheet helpers: usage line, folder tree HTML, zip-all. Free tier: stdlib only."""
import html
import os
import tempfile
import zipfile

QUOTA_MB = 128
ICON = {".py": ("py", "PY"), ".json": ("js", "{}"), ".csv": ("tb", "CSV"), ".tsv": ("tb", "TSV"), ".txt": ("tx", "TXT"),
        ".md": ("tx", "MD"), ".pdf": ("pd", "PDF"), ".docx": ("dc", "DOC"), ".zip": ("zp", "ZIP"),
        ".html": ("hm", "HTML"), ".png": ("im", "IMG"), ".jpg": ("im", "IMG"), ".jpeg": ("im", "IMG"),
        ".xlsx": ("tb", "XLS"), ".pptx": ("dc", "PPT")}


def scan(root, limit=500):
    """[(relpath, size, mtime)] visible files under root."""
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((os.path.relpath(p, root), st.st_size, st.st_mtime))
            if len(out) >= limit:
                return out
    return out


def _size(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.1f} KB"


def usage_text(root):
    files = scan(root)
    total = sum(f[1] for f in files)
    return f"{_size(total)} / {QUOTA_MB} MB · {len(files)} files"


def tree_html(root, selected=None):
    files = scan(root)
    if not files:
        return '<div class="ft-empty">Wala pang file. Lalabas dito ang gagawin ng agent.</div>'
    folders, top = {}, []
    for rel, size, mt in files:
        d = os.path.dirname(rel)
        (folders.setdefault(d, []) if d else top).append((rel, size, mt))

    def row(rel, size, depth):
        kind, label = ICON.get(os.path.splitext(rel)[1].lower(), ("fl", "FILE"))
        sel = " sel" if rel == selected else ""
        return (f'<button type="button" class="ft-row d{depth}{sel}" data-fpath="{html.escape(rel, quote=True)}">'
                f'<span class="ft-ic {kind}">{label}</span><span class="ft-n">{html.escape(os.path.basename(rel))}</span>'
                f'<span class="ft-s">{_size(size)}</span></button>')
    parts = []
    for d in sorted(folders):
        parts.append(f'<details class="ft-dir" open><summary>{html.escape(d)}</summary>'
                     + "".join(row(r, s, 1) for r, s, _ in folders[d]) + "</details>")
    # newest loose files first so a fresh PDF is at the top of the list
    parts.extend(row(r, s, 0) for r, s, _ in sorted(top, key=lambda x: -x[2]))
    return '<div class="ft">' + "".join(parts) + "</div>"


def make_zip(root, max_bytes=60 * 1024 * 1024):
    """Zip the workspace (skipping hidden files); returns a path or None when empty."""
    files = scan(root)
    if not files:
        return None
    path = os.path.join(tempfile.gettempdir(), "workspace-files.zip")
    total = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, size, _ in files:
            total += size
            if total > max_bytes:
                break
            z.write(os.path.join(root, rel), rel)
    return path
