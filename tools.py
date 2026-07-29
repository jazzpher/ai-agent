"""
Tools for the AI Agent (Sandboxed Version)
Each tool is protected by safety guardrails.
"""
import os
import subprocess
import sys
import json
import time
import re
import shutil
import httpx
import trafilatura

try:
    from duckduckgo_search import DDGS
    HAS_DDG = True
except ImportError:
    HAS_DDG = False

from config import (
    WORKSPACE_DIR,
    MAX_FILE_READ_BYTES,
    MAX_TOOL_OUTPUT_CHARS,
)
from safety import guard, SafetyViolation

# Optional Docker sandbox. Imported lazily so users without Docker can still
# run the agent in regex-only mode.
try:
    from sandbox_docker import run_in_docker, DockerNotAvailable, get_status as _docker_status
    _HAS_DOCKER_SANDBOX = True
except Exception:
    _HAS_DOCKER_SANDBOX = False

    class DockerNotAvailable(Exception):
        pass

    def _docker_status():
        return {"mode": "regex-only", "available": False, "reason": "sandbox_docker not importable"}


def get_sandbox_status() -> dict:
    """Public helper for the UI: describe the current sandbox mode."""
    return _docker_status()


# ============================================================
# TOOL IMPLEMENTATIONS
# ============================================================

def _truncate(text: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Truncate long outputs and append a notice."""
    if not text:
        return text or ""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n... [truncated {len(text) - max_chars} chars; "
          "use a more specific tool (grep, head, tail, edit_file) to see more]"
    )


def run_bash(command: str, timeout: int = 30, use_docker: bool = True) -> dict:
    """Run a shell command. Uses Docker sandbox if available, else host+regex."""
    check = guard.validate_command(command)
    if not check["safe"]:
        return {
            "status": "blocked",
            "output": check["message"],
            "risk_level": check["risk_level"],
        }

    timeout = min(max(timeout, 1), 120)

    # ---- Try Docker first if requested ----
    if use_docker and _HAS_DOCKER_SANDBOX:
        try:
            r = run_in_docker("bash", command, timeout=timeout)
            r["output"] = _truncate(r.get("output", ""))
            return r
        except DockerNotAvailable as e:
            # Fall through to host execution
            fallback_note = f"⚠️ Docker sandbox unavailable ({e}); ran on host with regex check only.\n\n"
        except Exception as e:
            return {"status": "error", "output": f"Docker error: {e}", "risk_level": "sandboxed"}
    else:
        fallback_note = ""

    # ---- Host execution (with regex blocklist) ----
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=WORKSPACE_DIR,
            encoding='utf-8',
            errors='replace',
        )
        out = ""
        if result.stdout:
            out += f"STDOUT:\n{result.stdout}"
        if result.stderr:
            out += f"\nSTDERR:\n{result.stderr}"
        if not out:
            out = "(no output)"

        prefix = fallback_note
        if check["risk_level"] == "risky":
            prefix += check["message"] + "\n\n---\n\n"

        return {
            "status": "success" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "output": _truncate(prefix + out.strip()),
            "risk_level": check["risk_level"],
            "sandbox": "host",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": fallback_note + f"Command timed out after {timeout} seconds"}
    except Exception as e:
        return {"status": "error", "output": fallback_note + str(e)}


def read_file(path: str, max_bytes: int = MAX_FILE_READ_BYTES, offset: int = 0) -> dict:
    """Read a file with safety checks. Supports offset for large files."""
    check = guard.validate_file_read(path)
    if not check["safe"]:
        return {"status": "blocked", "output": check["message"], "risk_level": "blocked"}

    try:
        resolved = guard.validate_path(path, allow_workspace_only=False, must_exist=True)

        if os.path.isdir(resolved):
            return {
                "status": "error",
                "output": f"'{resolved}' is a directory, not a file. Use list_files instead.",
            }

        # Binary-file guard
        binary_extensions = {
            '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt',
            '.pdf', '.zip', '.tar', '.gz', '.rar', '.7z',
            '.exe', '.dll', '.so', '.dylib',
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico',
            '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
            '.pyc', '.pyo', '.class', '.o', '.obj',
        }
        _, ext = os.path.splitext(resolved.lower())
        if ext in binary_extensions:
            file_size = os.path.getsize(resolved)
            return {
                "status": "error",
                "output": (
                    f"Cannot read binary file: {resolved}\n"
                    f"File type: {ext}\n"
                    f"File size: {file_size} bytes\n\n"
                    f"💡 Use run_python with python-docx / openpyxl / "
                    f"PyPDF2 / Pillow / zipfile, etc."
                ),
            }

        file_size = os.path.getsize(resolved)
        if file_size > max_bytes + offset:
            with open(resolved, 'rb') as f:
                f.seek(offset)
                raw = f.read(max_bytes)
            content = raw.decode('utf-8', errors='replace')
            content = (
                f"[Showing bytes {offset}..{offset+max_bytes} of {file_size}. "
                f"Pass offset= to read more.]\n\n" + content
            )
        else:
            with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
                if offset:
                    f.seek(offset)
                content = f.read()

        # Heuristic binary sniff
        if content.count('\x00') > 10:
            return {
                "status": "error",
                "output": (
                    f"File appears to be binary: {resolved}\n"
                    f"File size: {file_size} bytes\n"
                    f"Null bytes detected. Use run_python with appropriate libraries."
                ),
            }

        return {
            "status": "success",
            "output": _truncate(content),
            "file_size": file_size,
            "truncated": file_size > max_bytes + offset,
        }
    except SafetyViolation as e:
        return {"status": "blocked", "output": str(e), "risk_level": "blocked"}
    except UnicodeDecodeError:
        file_size = os.path.getsize(resolved) if os.path.exists(resolved) else 0
        return {
            "status": "error",
            "output": (
                f"Cannot decode file as text: {resolved}\n"
                f"File size: {file_size} bytes\n\n"
                f"💡 This file appears to be binary. Use run_python."
            ),
        }
    except Exception as e:
        return {"status": "error", "output": str(e)}


def write_file(path: str, content: str) -> dict:
    """Write a file with safety checks (workspace only)."""
    check = guard.validate_file_write(path)
    if not check["safe"]:
        return {"status": "blocked", "output": check["message"], "risk_level": "blocked"}

    try:
        resolved = guard.validate_path(path, allow_workspace_only=True)

        parent_dir = os.path.dirname(resolved)
        os.makedirs(parent_dir, exist_ok=True)

        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)

        try:
            display_path = os.path.relpath(resolved, WORKSPACE_DIR)
        except ValueError:
            display_path = resolved

        return {
            "status": "success",
            "output": f"✅ File written: {display_path} ({len(content)} bytes)\nLocation: {resolved}",
        }
    except SafetyViolation as e:
        return {"status": "blocked", "output": str(e), "risk_level": "blocked"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict:
    """
    Surgical edit: replace `old_string` with `new_string` in `path`.
    Fails if old_string is not found, or (when replace_all=False) appears more than once.
    Workspace only.
    """
    check = guard.validate_file_write(path)
    if not check["safe"]:
        return {"status": "blocked", "output": check["message"], "risk_level": "blocked"}

    if not old_string:
        return {"status": "error", "output": "old_string cannot be empty"}

    try:
        resolved = guard.validate_path(path, allow_workspace_only=True, must_exist=True)

        with open(resolved, 'r', encoding='utf-8') as f:
            content = f.read()

        occurrences = content.count(old_string)
        if occurrences == 0:
            return {
                "status": "error",
                "output": (
                    f"old_string not found in {resolved}.\n"
                    f"💡 Read the file first to see the exact text (whitespace matters)."
                ),
            }
        if occurrences > 1 and not replace_all:
            return {
                "status": "error",
                "output": (
                    f"old_string appears {occurrences} times in {resolved}.\n"
                    f"💡 Provide more surrounding context to make it unique, "
                    f"or set replace_all=true to replace every occurrence."
                ),
            }

        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )

        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(new_content)

        try:
            display_path = os.path.relpath(resolved, WORKSPACE_DIR)
        except ValueError:
            display_path = resolved

        return {
            "status": "success",
            "output": (
                f"✅ Edited {display_path}\n"
                f"Replaced {occurrences if replace_all else 1} occurrence(s). "
                f"({len(old_string)} -> {len(new_string)} chars)"
            ),
        }
    except SafetyViolation as e:
        return {"status": "blocked", "output": str(e), "risk_level": "blocked"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def list_files(path: str = ".") -> dict:
    """List files in workspace."""
    try:
        resolved = guard.validate_path(path, allow_workspace_only=True, must_exist=True)
        items = []
        for item in sorted(os.listdir(resolved)):
            full_path = os.path.join(resolved, item)
            item_type = "📁 DIR " if os.path.isdir(full_path) else "📄 FILE"
            size = os.path.getsize(full_path) if os.path.isfile(full_path) else "-"
            items.append(f"  {item_type} {item} ({size})")
        header = f"Contents of: {os.path.relpath(resolved, WORKSPACE_DIR) or 'workspace'}\n"
        return {
            "status": "success",
            "output": header + ("\n".join(items) if items else "(empty directory)"),
        }
    except SafetyViolation as e:
        return {"status": "blocked", "output": str(e), "risk_level": "blocked"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web. Uses duckduckgo_search (real results) if installed, else falls back."""
    if not HAS_DDG:
        return {
            "status": "error",
            "output": (
                "duckduckgo-search is not installed. Run:\n"
                "  pip install duckduckgo-search==6.3.5\n"
                "and restart the agent."
            ),
        }
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = r.get("title", "")
                href = r.get("href", "")
                body = r.get("body", "")
                results.append(f"### {title}\n{body}\nURL: {href}\n")
        if not results:
            results.append("No results. Try a different query.")
        return {"status": "success", "output": _truncate("\n".join(results), max_chars=6000)}
    except Exception as e:
        return {"status": "error", "output": f"Search error: {e}"}


def fetch_page(url: str, max_chars: int = 12000) -> dict:
    """Fetch a URL and return the main text content (trafilatura-extracted)."""
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"status": "error", "output": "URL must start with http:// or https://"}
    try:
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ai-agent/1.0)"},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text

        extracted = trafilatura.extract(html, include_links=False, include_images=False) or ""
        if not extracted.strip():
            # Fallback: very crude text-only extraction
            extracted = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
            extracted = re.sub(r"<style.*?</style>", "", extracted, flags=re.S | re.I)
            extracted = re.sub(r"<[^>]+>", " ", extracted)
            extracted = re.sub(r"\s+", " ", extracted).strip()

        head = f"Source: {url}\nStatus: {resp.status_code}\nLength: {len(extracted)} chars\n\n"
        return {
            "status": "success",
            "output": _truncate(head + extracted, max_chars=max_chars),
        }
    except httpx.HTTPError as e:
        return {"status": "error", "output": f"HTTP error: {e}"}
    except Exception as e:
        return {"status": "error", "output": f"Fetch error: {e}"}


def pip_install(package: str, use_docker: bool = True) -> dict:
    """
    Install a Python package.
    - If Docker sandbox is available, installs INSIDE the container (and is
      thrown away when the container exits). This is the safest mode.
    - Otherwise, installs on the host. The strict package-spec regex has
      already passed by the time we get here.
    """
    check = guard.validate_pip_install(package)
    if not check["safe"]:
        return {"status": "blocked", "output": check["message"], "risk_level": "blocked"}

    pip_command = f"python -m pip install {package} --quiet --disable-pip-version-check --no-warn-script-location"

    if use_docker and _HAS_DOCKER_SANDBOX:
        try:
            r = run_in_docker("bash", pip_command, timeout=180)
            r["output"] = _truncate(r.get("output", ""), max_chars=2000)
            if r.get("status") == "success":
                r["output"] = f"✅ Installed in container: {package}\n" + r["output"]
            else:
                r["output"] = f"❌ Failed to install {package} in container\n" + r["output"]
            return r
        except DockerNotAvailable:
            pass

    # Host fallback
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet",
             "--disable-pip-version-check", "--no-warn-script-location"],
            capture_output=True, text=True, timeout=180,
            encoding='utf-8', errors='replace',
        )
        out = result.stdout or ""
        if result.stderr:
            out += result.stderr
        if result.returncode == 0:
            return {
                "status": "success",
                "output": f"✅ Installed on host: {package}\n{out.strip()[-1000:]}",
                "sandbox": "host",
            }
        return {
            "status": "error",
            "output": f"❌ Failed to install {package}\n{out.strip()[-1500:]}",
            "sandbox": "host",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Installation timed out (180s)"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


def run_python(code: str, use_docker: bool = True) -> dict:
    """Execute Python code. Uses Docker sandbox if available, else host+regex."""
    check = guard.validate_python_code(code)
    if not check["safe"]:
        return {"status": "blocked", "output": check["message"], "risk_level": "blocked"}

    # ---- Docker path ----
    if use_docker and _HAS_DOCKER_SANDBOX:
        try:
            r = run_in_docker("python", code, timeout=60)
            r["output"] = _truncate(r.get("output", ""))
            return r
        except DockerNotAvailable as e:
            fallback_note = f"⚠️ Docker sandbox unavailable ({e}); ran on host with regex check only.\n\n"
        except Exception as e:
            return {"status": "error", "output": f"Docker error: {e}", "risk_level": "sandboxed"}
    else:
        fallback_note = ""

    # ---- Host path ----
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=WORKSPACE_DIR,
            encoding='utf-8',
            errors='replace',
        )
        out = ""
        if result.stdout:
            out += f"Output:\n{result.stdout}"
        if result.stderr:
            out += f"\nErrors:\n{result.stderr}"
        if not out:
            out = "(no output - code executed successfully)"

        prefix = fallback_note
        if check["risk_level"] == "risky":
            prefix += (
                check["message"]
                + "\n\n---\n\n"
                + "⚠️ This is a warning, not a block. The code ran.\n\n"
            )

        return {
            "status": "success" if result.returncode == 0 else "error",
            "output": _truncate(prefix + out.strip()),
            "risk_level": check["risk_level"],
            "sandbox": "host",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": fallback_note + "Python execution timed out (60s)"}
    except Exception as e:
        return {"status": "error", "output": fallback_note + str(e)}


# ============================================================
# TOOL DEFINITIONS (OpenAI function-calling format)
# ============================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": (
                "Run a shell command in the sandboxed workspace directory. "
                "DANGEROUS COMMANDS (format, del on system files, shutdown, "
                "rm -rf /, etc.) will be BLOCKED. Use this for git, npm, "
                "compiling, running programs, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (max 120)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file. Supports an offset/max_bytes for large files. "
                "Binary files and credential files are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "max_bytes": {"type": "integer", "description": "Max bytes to read (default 50000)."},
                    "offset": {"type": "integer", "description": "Byte offset to start from."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file. ONLY works within the workspace directory. "
                "Use edit_file for surgical changes to existing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (within workspace)."},
                    "content": {"type": "string", "description": "The full content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Surgically edit a file by replacing a specific string. "
                "Fails if the string is not found, or appears more than once "
                "(unless replace_all=true). This is much cheaper than write_file "
                "for small changes. Workspace only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (within workspace)."},
                    "old_string": {"type": "string", "description": "The exact string to replace."},
                    "new_string": {"type": "string", "description": "What to replace it with."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence (default false).",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: workspace root)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using DuckDuckGo. Returns real results "
                "(title, snippet, URL). Use this for current information, "
                "documentation, or facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "max_results": {"type": "integer", "description": "How many results (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch a URL and return the main text content (article body). "
                "Use after web_search to read a specific page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch (http/https)."},
                    "max_chars": {"type": "integer", "description": "Max chars to return (default 12000)."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pip_install",
            "description": (
                "Install a Python package using pip. Known malicious/typosquat "
                "packages are blocked. The package spec is strictly validated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "Package name (e.g. 'requests', 'numpy==1.26.0').",
                    },
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute Python code in the workspace directory. "
                "Dangerous patterns (eval, exec, os.system) are FLAGGED but "
                "the code still runs - this is a warning, not a sandbox. "
                "Use for calculations, data processing, quick scripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The Python code to execute."},
                },
                "required": ["code"],
            },
        },
    },
]


TOOL_FUNCTIONS = {
    "run_bash": run_bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "web_search": web_search,
    "fetch_page": fetch_page,
    "pip_install": pip_install,
    "run_python": run_python,
}

