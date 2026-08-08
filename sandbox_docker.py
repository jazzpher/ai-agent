"""
OS-level sandbox for bash and python execution using Docker.

When enabled, run_bash and run_python execute inside an ephemeral Docker
container. The container has:
- No network access (--network=none)
- Read-only root filesystem (--read-only)
- A tmpfs at /tmp
- A bind-mount of the workspace at /workspace (read-write, the only writable path)
- Drops all Linux capabilities
- Runs as non-root user (uid 1000)
- Memory limit (default 512m) and CPU limit (default 1.0)
- Auto-removed after each command (--rm)

This is a real isolation layer. Combined with the existing regex blocklist,
it provides defense-in-depth that a clever prompt can't easily bypass.

Requirements:
- Docker Desktop (Win/Mac) or docker engine (Linux)
- The user must be able to run `docker run` without sudo
  (Linux: add yourself to the `docker` group, or set up rootless docker)

Fallback: if Docker is not available, the existing regex-only check is used
and a warning is emitted.
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from config import WORKSPACE_DIR


# Container image - kept tiny, no shell tricks
SANDBOX_IMAGE = os.environ.get("AGENT_SANDBOX_IMAGE", "python:3.12-alpine")

# Default resource limits (overridable via env)
DEFAULT_MEMORY = os.environ.get("AGENT_SANDBOX_MEMORY", "512m")
DEFAULT_CPUS = os.environ.get("AGENT_SANDBOX_CPUS", "1.0")
DEFAULT_TIMEOUT = int(os.environ.get("AGENT_SANDBOX_TIMEOUT", "60"))

# Whether to use Docker at all (set AGENT_NO_DOCKER=1 to force regex-only mode)
USE_DOCKER = os.environ.get("AGENT_NO_DOCKER", "0") != "1"


class DockerNotAvailable(Exception):
    pass


def _check_docker() -> Optional[str]:
    """
    Verify Docker is installed and the daemon is reachable.
    Returns the docker version string, or raises DockerNotAvailable.
    """
    docker = shutil.which("docker")
    if not docker:
        raise DockerNotAvailable("docker binary not found in PATH")
    try:
        result = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            raise DockerNotAvailable(
                f"docker daemon not reachable: {result.stderr.strip()}"
            )
        return result.stdout.strip() or "unknown"
    except subprocess.TimeoutExpired:
        raise DockerNotAvailable("docker version command timed out")
    except FileNotFoundError:
        raise DockerNotAvailable("docker not found")


def _resolve_workspace_for_container(workspace: str) -> str:
    """
    On Windows, bind-mounts need a Windows-style path (or a path that the
    Docker daemon understands). For now we pass through; Docker Desktop
    transparently translates.
    """
    return os.path.realpath(workspace)


def run_in_docker(
    payload_kind: str,   # "bash" or "python"
    payload: str,
    workspace: str = None,
    timeout: int = None,
    memory: str = None,
    cpus: str = None,
) -> dict:
    """
    Execute a bash command or Python code inside an ephemeral Docker container.

    Returns a dict with: status, output, returncode, risk_level, duration_s
    Raises DockerNotAvailable if Docker is not installed/usable.
    """
    if not USE_DOCKER:
        raise DockerNotAvailable("Docker sandbox disabled via AGENT_NO_DOCKER=1")

    _check_docker()  # raises if not available

    workspace = os.path.realpath(workspace or WORKSPACE_DIR)
    timeout = min(max(timeout or DEFAULT_TIMEOUT, 1), 300)
    memory = memory or DEFAULT_MEMORY
    cpus = cpus or DEFAULT_CPUS

    # Choose entrypoint + payload-arg
    if payload_kind == "bash":
        # -c takes the command as a single argv. We pass it via env var to
        # dodge any shell-escaping weirdness inside the container.
        cmd = ["/bin/sh", "-c", "eval \"$AI_CMD\""]
        env_value = payload
        image = "alpine:3.20"
    elif payload_kind == "python":
        cmd = ["/usr/local/bin/python", "-c", "import os; exec(os.environ['AI_CODE'])"]
        env_value = payload
        image = "python:3.12-alpine"
    else:
        return {"status": "error", "output": f"Unknown payload_kind: {payload_kind}"}

    docker_args = [
        "docker", "run",
        "--rm",
        "-i",                      # keep stdin open so we can pipe nothing
        "--network=none",          # no network at all
        "--read-only",             # read-only rootfs
        "--tmpfs", "/tmp:size=64m,mode=1777",
        "-v", f"{workspace}:/workspace:rw",
        "-w", "/workspace",
        "-e", f"AI_CMD={env_value}" if payload_kind == "bash" else f"AI_CODE={env_value}",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PIP_NO_CACHE_DIR=1",
        "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
        "-m", memory,
        "--cpus", cpus,
        "--pids-limit", "256",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "--cap-add", "CHOWN",      # needed for the non-root user mapping on some hosts
        "--user", "1000:1000",
        image,
        *cmd,
    ]

    t0 = time.time()
    try:
        result = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - t0

        out = ""
        if result.stdout:
            out += f"STDOUT:\n{result.stdout}"
        if result.stderr:
            out += f"\nSTDERR:\n{result.stderr}"
        if not out:
            out = "(no output)"

        return {
            "status": "success" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "output": out.strip(),
            "risk_level": "sandboxed",   # so callers can render a different badge
            "duration_s": round(elapsed, 3),
            "sandbox": "docker",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "output": f"Container timed out after {timeout}s (was killed)",
            "risk_level": "sandboxed",
            "duration_s": round(time.time() - t0, 3),
            "sandbox": "docker",
        }
    except FileNotFoundError:
        raise DockerNotAvailable("docker binary disappeared mid-call")


# ===========================================================
# Image preflight
# ===========================================================

def ensure_image(image: str = None) -> bool:
    """
    Pull the sandbox image if it's not present locally. Returns True on success.
    Does NOT raise - logs and returns False on failure so the agent can fall back.
    """
    image = image or SANDBOX_IMAGE
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        # Check if present
        r = subprocess.run(
            [docker, "image", "inspect", image],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True
        # Pull it
        print(f"[sandbox] Pulling {image} (one-time)...")
        r = subprocess.run(
            [docker, "pull", image],
            capture_output=True, text=True, timeout=300,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"[sandbox] ensure_image failed: {e}")
        return False


# ===========================================================
# Status helper
# ===========================================================

def get_status() -> dict:
    """Return a dict describing the sandbox state, for the UI."""
    if not USE_DOCKER:
        return {
            "mode": "regex-only",
            "available": False,
            "reason": "AGENT_NO_DOCKER=1",
        }
    try:
        version = _check_docker()
        return {
            "mode": "docker",
            "available": True,
            "version": version,
            "image": SANDBOX_IMAGE,
            "memory": DEFAULT_MEMORY,
            "cpus": DEFAULT_CPUS,
        }
    except DockerNotAvailable as e:
        return {
            "mode": "regex-only",
            "available": False,
            "reason": str(e),
        }

