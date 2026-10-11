"""
Per-Session Persistent Sandbox

Two modes:
1. Docker: Long-lived container that stays alive for the entire session
2. Venv: Ephemeral virtual environment (no Docker required)

Both modes ensure:
- Packages installed during the session persist within the session
- The host machine is NEVER permanently modified
- Everything is cleaned up when the session ends
"""

import os
import re
import sys
import json
import subprocess
import shutil
import time
import uuid
import atexit
import threading
from typing import Optional
from config import WORKSPACE_DIR, SANDBOX_DIR, SANDBOX_TIMEOUT, CORE_PACKAGES


_IMPORT_NAMES = {
    "pillow": "PIL", "python-docx": "docx", "python-pptx": "pptx",
    "openpyxl": "openpyxl", "pypdf2": "PyPDF2", "pdfplumber": "pdfplumber",
    "requests": "requests", "beautifulsoup4": "bs4", "pandas": "pandas",
}


def missing_core_packages() -> list[str]:
    """Core packages that cannot be imported by the host interpreter."""
    import importlib.util
    out = []
    for pkg in CORE_PACKAGES:
        mod = _IMPORT_NAMES.get(pkg.lower(), pkg.replace("-", "_"))
        if importlib.util.find_spec(mod) is None:
            out.append(pkg)
    return out


_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
_SECRET_EXACT = {"AGENT_USERNAME", "AGENT_PASSWORD", "AWS_PROFILE", "GH_HOST"}


def host_env(venv_path: str, python_path: str) -> dict:
    """Environment for host (venv mode) children: everything except secrets.

    The app's own provider keys (NVIDIA_API_KEY, GEMINI_API_KEY, ...) and
    login live in this process's environment; a command the model writes
    must never be able to print them."""
    env = {k: v for k, v in os.environ.items()
           if k not in _SECRET_EXACT and not _SECRET_ENV.search(k)}
    venv_bin = os.path.dirname(python_path)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = venv_path
    env["PYTHONNOUSERSITE"] = "1"
    return env


class SessionSandbox:
    """A persistent sandbox that lives for the duration of a session."""

    def __init__(self, session_id: str = None, force_mode: str = None):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.mode = None  # "docker" or "venv"
        self._container_id = None
        self._container_name = f"agent-sandbox-{self.session_id}"
        self._venv_path = None
        self._python_path = None
        self._pip_path = None
        self._created_at = time.time()
        self._last_active = time.time()
        self._installed_packages: set[str] = set()
        self._lock = threading.Lock()
        self._initialized = False

        # Determine mode
        force_mode = force_mode or os.environ.get("AGENT_SANDBOX_MODE", "auto")
        if force_mode == "bubblewrap":
            from kernel_sandbox import available
            if not available():
                raise RuntimeError("Kernel sandbox requested but unavailable; refusing host execution")
            self._init_venv()
            from kernel_sandbox import backend
            self.mode = backend()
        elif force_mode == "docker":
            if self._init_docker():
                self.mode = "docker"
            else:
                raise RuntimeError("Docker requested but not available")
        elif force_mode == "venv":
            self._init_venv()
            self.mode = "venv"
        else:
            # Auto-detect: try Docker first, fall back to venv
            if self._init_docker():
                self.mode = "docker"
            else:
                self._init_venv()
                self.mode = "venv"

        self._initialized = True

    # ================================================================
    # DOCKER MODE: Long-lived container
    # ================================================================

    def _check_docker(self) -> bool:
        """Check if Docker is available and running."""
        docker = shutil.which("docker")
        if not docker:
            return False
        try:
            result = subprocess.run(
                [docker, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _init_docker(self) -> bool:
        """Try to start a long-lived Docker container for this session."""
        if not self._check_docker():
            return False

        try:
            workspace_real = os.path.realpath(WORKSPACE_DIR)

            # Check if container name already exists
            check = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={self._container_name}",
                 "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            if self._container_name in (check.stdout or ""):
                # Remove stale container
                subprocess.run(
                    ["docker", "rm", "-f", self._container_name],
                    capture_output=True, timeout=10,
                )

            # Start a long-lived container (NOT --rm, we'll clean up manually)
            result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--network=none",
                    "--read-only",
                    "--tmpfs", "/tmp:size=256m,mode=1777",
                    "-v", f"{workspace_real}:/workspace:rw",
                    "-w", "/workspace",
                    "-m", "512m",
                    "--cpus", "1.0",
                    "--pids-limit", "256",
                    "--security-opt", "no-new-privileges:true",
                    "--cap-drop", "ALL",
                    "--name", self._container_name,
                    "python:3.12-alpine",
                    "sleep", str(SANDBOX_TIMEOUT),
                ],
                capture_output=True, text=True, timeout=60,
            )

            if result.returncode != 0:
                return False

            self._container_id = result.stdout.strip()

            # Install core packages in the background
            self._docker_exec(
                "pip install --quiet --no-cache-dir " + " ".join(CORE_PACKAGES),
                timeout=300,
            )

            return True

        except Exception:
            return False

    def _docker_exec(self, command: str, timeout: int = 120) -> dict:
        """Execute a command in the persistent Docker container."""
        if not self._container_id:
            return {"status": "error", "output": "No container"}

        try:
            result = subprocess.run(
                [
                    "docker", "exec",
                    self._container_id,
                    "/bin/sh", "-c", command,
                ],
                capture_output=True, text=True,
                timeout=timeout,
                encoding="utf-8", errors="replace",
            )

            out = ""
            if result.stdout:
                out += result.stdout
            if result.stderr:
                out += result.stderr

            return {
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "output": out.strip() or "(no output)",
                "sandbox": "docker",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "output": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    # ================================================================
    # VENV MODE: Ephemeral virtual environment
    # ================================================================

    def _init_venv(self):
        """Create an ephemeral virtual environment for this session."""
        self._venv_path = os.path.join(SANDBOX_DIR, f"session-{self.session_id}-{uuid.uuid4().hex[:8]}")
        os.makedirs(self._venv_path, exist_ok=True)

        # Create venv
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", self._venv_path],
            capture_output=True, text=True, timeout=60,
        )

        # Determine python/pip path inside venv
        if os.name == "nt":
            self._python_path = os.path.join(self._venv_path, "Scripts", "python.exe")
            self._pip_path = os.path.join(self._venv_path, "Scripts", "pip.exe")
        else:
            self._python_path = os.path.join(self._venv_path, "bin", "python")
            self._pip_path = os.path.join(self._venv_path, "bin", "pip")

        # --system-site-packages only exposes the BASE interpreter's packages. When the
        # app itself runs inside a venv (Render installs requirements into one), the
        # app's packages (Pillow, python-docx, pandas...) are NOT visible in this venv.
        # Link them in with a .pth file, then install only what is truly missing,
        # checked with the venv's own interpreter (not the app's).
        self._link_parent_site_packages()
        missing = self._missing_in_venv()
        if missing:
            self._venv_pip_install(" ".join(missing), timeout=300)

    def _link_parent_site_packages(self):
        """Make the app's installed packages importable inside the sandbox venv."""
        try:
            if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
                return  # app is not in a venv; --system-site-packages is enough
            import site
            parent = [p for p in site.getsitepackages() if os.path.isdir(p)]
            r = subprocess.run(
                [self._python_path, "-c",
                 "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                capture_output=True, text=True, timeout=30,
            )
            purelib = r.stdout.strip()
            if parent and purelib and os.path.isdir(purelib):
                with open(os.path.join(purelib, "_app_site_packages.pth"), "w") as f:
                    f.write("\n".join(parent) + "\n")
        except Exception:
            pass  # fall back to installing whatever is missing

    def _missing_in_venv(self) -> list[str]:
        """Core packages the venv's own interpreter cannot import."""
        names = {pkg: _IMPORT_NAMES.get(pkg.lower(), pkg.replace("-", "_")) for pkg in CORE_PACKAGES}
        code = (
            "import importlib.util,sys,json;"
            "print(json.dumps([p for p,m in json.loads(sys.argv[1]).items() "
            "if importlib.util.find_spec(m) is None]))"
        )
        try:
            r = subprocess.run(
                [self._python_path, "-c", code, json.dumps(names)],
                capture_output=True, text=True, timeout=60,
            )
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            return missing_core_packages()

    def _venv_pip_install(self, packages: str, timeout: int = 180) -> dict:
        """Install packages in the ephemeral venv."""
        try:
            pkg_list = packages.strip().split()
            if not pkg_list:
                return {"status": "error", "output": "No packages specified"}

            result = subprocess.run(
                [self._python_path, "-m", "pip", "install",
                 "--quiet", "--no-cache-dir"] + pkg_list,
                capture_output=True, text=True,
                timeout=timeout,
                env=host_env(self._venv_path, self._python_path),
                encoding="utf-8", errors="replace",
            )

            # Track installed packages
            for pkg in pkg_list:
                base = pkg.split("==")[0].split(">=")[0].split("<=")[0]
                base = base.split("[")[0].lower()
                self._installed_packages.add(base)

            if result.returncode == 0:
                return {
                    "status": "success",
                    "output": f"✅ Installed: {', '.join(pkg_list)}",
                    "sandbox": "venv",
                }
            else:
                return {
                    "status": "error",
                    "output": (result.stdout or "") + (result.stderr or ""),
                    "sandbox": "venv",
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "output": f"Install timed out after {timeout}s"}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    # ================================================================
    # UNIFIED API
    # ================================================================

    def run_command(self, command: str, timeout: int = 30) -> dict:
        """Run a shell command in the sandbox."""
        self._touch()
        with self._lock:
            if self.mode in {"bubblewrap", "landlock"}:
                from kernel_sandbox import run
                return run(["/bin/bash", "-c", command], WORKSPACE_DIR, self._venv_path, timeout)
            if self.mode == "docker":
                return self._docker_exec(command, timeout)
            else:
                return self._host_exec(command, timeout)

    def run_python(self, code: str, timeout: int = 60) -> dict:
        """Run Python code in the sandbox."""
        self._touch()
        with self._lock:
            if self.mode in {"bubblewrap", "landlock"}:
                from kernel_sandbox import run
                return run([self._python_path, "-c", code], WORKSPACE_DIR, self._venv_path, timeout)
            if self.mode == "docker":
                # Write code to a temp file inside the container to avoid
                # shell escaping issues with python -c '...'
                import base64
                encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
                cmd = (
                    f"echo '{encoded}' | base64 -d > /tmp/_agent_code.py && "
                    f"python /tmp/_agent_code.py"
                )
                return self._docker_exec(cmd, timeout)
            else:
                return self._host_python(code, timeout)

    def install_package(self, package: str) -> dict:
        """Install a package in the sandbox (temporary)."""
        self._touch()
        with self._lock:
            if self.mode in {"bubblewrap", "landlock"}:
                from kernel_sandbox import run
                return run([self._python_path, "-m", "pip", "install", "--no-cache-dir"] + package.split(),
                           WORKSPACE_DIR, self._venv_path, 180, network=True, install=True)
            if self.mode == "docker":
                result = self._docker_exec(
                    f"pip install --quiet --no-cache-dir {package}",
                    timeout=180,
                )
                if result["status"] == "success":
                    base = package.split("==")[0].split(">=")[0].split("<=")[0]
                    base = base.split("[")[0].lower()
                    self._installed_packages.add(base)
                return result
            else:
                return self._venv_pip_install(package)

    def get_installed_packages(self) -> list[str]:
        """Return list of packages installed in this session."""
        with self._lock:
            return sorted(self._installed_packages)

    def get_status(self) -> dict:
        """Return sandbox status for the UI."""
        with self._lock:
            return {
                "mode": self.mode,
                "session_id": self.session_id,
                "packages_installed": sorted(self._installed_packages),
                "uptime_seconds": round(time.time() - self._created_at, 1),
                "last_active": round(time.time() - self._last_active, 1),
            }

    # ================================================================
    # HOST EXECUTION (via venv)
    # ================================================================

    def _host_exec(self, command: str, timeout: int) -> dict:
        """Execute on host using the ephemeral venv."""
        try:
            env = host_env(self._venv_path, self._python_path)

            result = subprocess.run(
                command, shell=True,
                capture_output=True, text=True,
                timeout=timeout,
                cwd=WORKSPACE_DIR,
                env=env,
                encoding="utf-8", errors="replace",
            )

            out = ""
            if result.stdout:
                out += result.stdout
            if result.stderr:
                out += result.stderr

            return {
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "output": out.strip() or "(no output)",
                "sandbox": "venv",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "output": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    def _host_python(self, code: str, timeout: int) -> dict:
        """Run Python code using the ephemeral venv's interpreter."""
        try:
            env = host_env(self._venv_path, self._python_path)

            result = subprocess.run(
                [self._python_path, "-c", code],
                capture_output=True, text=True,
                timeout=timeout,
                cwd=WORKSPACE_DIR,
                env=env,
                encoding="utf-8", errors="replace",
            )

            out = ""
            if result.stdout:
                out += f"Output:\n{result.stdout}"
            if result.stderr:
                out += f"\nErrors:\n{result.stderr}"

            return {
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "output": out.strip() or "(no output - code executed successfully)",
                "sandbox": "venv",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "output": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    # ================================================================
    # CLEANUP
    # ================================================================

    def cleanup(self):
        """Destroy the sandbox. Called on session end or process exit."""
        if self.mode == "docker" and self._container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self._container_id],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass

        if self.mode == "venv" and self._venv_path:
            try:
                shutil.rmtree(self._venv_path, ignore_errors=True)
            except Exception:
                pass

    def _touch(self):
        """Update last_active timestamp."""
        self._last_active = time.time()


# ================================================================
# SESSION MANAGER
# ================================================================

class SessionManager:
    """Manages per-session sandboxes. One sandbox per user session."""

    def __init__(self):
        self._sessions: dict[str, SessionSandbox] = {}
        self._lock = threading.Lock()

    def peek_status(self, session_id: str) -> dict:
        """Read an initialized sandbox without creating one or waiting for installs.

        Creation holds _lock while installing packages. Do not take that lock
        in read-only UI paths. A completed sandbox is published to _sessions
        only after initialization; get_status returns metadata, never I/O.
        """
        sb = self._sessions.get(session_id)
        return sb.get_status() if sb is not None else {"mode": "not started", "available": False}

    def get_or_create(self, session_id: str = None) -> SessionSandbox:
        """Get existing sandbox or create a new one."""
        session_id = session_id or "default"
        self._cleanup_expired()

        with self._lock:
            if session_id not in self._sessions:
                try:
                    self._sessions[session_id] = SessionSandbox(session_id)
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to create sandbox: {e}\n"
                        f"Try: python -m venv --help to check if venv is available."
                    )
            return self._sessions[session_id]

    def destroy(self, session_id: str):
        """Destroy a session's sandbox."""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].cleanup()
                del self._sessions[session_id]

    def destroy_all(self):
        """Destroy all sandboxes."""
        with self._lock:
            for sb in self._sessions.values():
                sb.cleanup()
            self._sessions.clear()

    def _cleanup_expired(self):
        """Clean up sandboxes that have been inactive too long."""
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, sb in self._sessions.items()
                if now - sb._last_active > SANDBOX_TIMEOUT
            ]
            for sid in expired:
                self._sessions[sid].cleanup()
                del self._sessions[sid]

    def get_all_status(self) -> list[dict]:
        """Status of all active sessions (for admin/debugging)."""
        with self._lock:
            return [
                {"session_id": sid, **sb.get_status()}
                for sid, sb in self._sessions.items()
            ]


# Global singleton
session_manager = SessionManager()

# Register cleanup on process exit (once, not per-session)
atexit.register(session_manager.destroy_all)
