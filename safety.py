"""
Safety Guardrails & Sandbox for the AI Agent

Protects the user's system by:
1. Restricting file operations to the workspace directory
2. Blocking dangerous shell commands
3. Preventing path traversal attacks (including symlink-based escapes)
4. Warning/blocking destructive operations
5. Protecting system directories and files

IMPORTANT: This is defense-in-depth, not a true OS-level sandbox.
A determined attacker (or a clever LLM prompt) can still bypass regex-based
checks. Do NOT run this agent with admin/root privileges or on your only
laptop. For real isolation, run inside Docker / Windows Sandbox / a VM.
"""
import os
import re
import platform
import shlex
from config import WORKSPACE_DIR


class SafetyViolation(Exception):
    """Raised when an operation violates safety rules."""
    pass


class SafetyGuard:
    """Central safety system that validates all tool operations before they are executed."""

    # ===========================================================
    # DANGEROUS COMMANDS - Blocklist
    # ===========================================================

    BLOCKED_COMMANDS = [
        # Windows destructive commands
        r'\bformat\s+[a-zA-Z]:',
        r'\bdel\s+/[sS]\s+[a-zA-Z]:\\',
        r'\brd\s+/[sS]\s+[a-zA-Z]:\\(Windows|Program|Users)',
        r'\brmdir\s+/[sS]\s+[a-zA-Z]:\\(Windows|Program|Users)',
        r'\breg\s+delete\b',
        r'\bregedit\s+/[sS]',
        r'\bsfc\s+/scannow',
        r'\bbcdedit\b',
        r'\bwmic\s+.*\bdelete\b',
        r'\bnet\s+stop\b',
        r'\btaskkill\s+/[fF]\s+/im\b.*\b(explorer|csrss|winlogon|services|svchost|lsass)\b',
        r'\bshutdown\b',
        r'\brestart\b.*[/\-]',
        r'\bcipher\s+/[wW]',
        r'\bfsutil\b',
        r'\bdiskpart\b',
        r'\bnetsh\s+.*\bdelete\b',
        r'\bpowercfg\s+/h\s+off',
        r'\battrib\s+.*-[rR].*\\Windows',

        # Cross-platform dangerous patterns
        r'\brm\s+-[rR]f\s+/',
        r'\brm\s+-[rR]f\s+~',
        r'\bmkfs\b',
        r'\bdd\s+if=',
        r':\(\)\s*\{.*\|.*&\s*\};:',  # fork bomb variants
        r'\bchmod\s+-R\s+777\s+/',

        # Network attacks / download & execute
        r'\bcurl\b.*\|\s*(bash|sh|cmd|powershell)',
        r'\bwget\b.*\|\s*(bash|sh|cmd|powershell)',
        r'\biwr\b.*\|\s*iex',
        r'\bInvoke-Expression\b.*\bInvoke-WebRequest\b',

        # Encoded/obfuscated execution (cheap defense)
        r'\bbase64\s+--decode\b',
        r'\bFromBase64String\b',
    ]

    RISKY_COMMANDS = [
        r'\brm\b',
        r'\bdel\b',
        r'\brmdir\b',
        r'\brd\s',
        r'\bmv\b.*\s+/',
        r'\bmove\b',
        r'\bpip\s+uninstall\b',
        r'\bnpm\s+uninstall\b',
        r'\bgit\s+reset\s+--hard',
        r'\bgit\s+clean\b',
        r'\bDROP\s+TABLE\b',
        r'\bDROP\s+DATABASE\b',
        r'\bsudo\b',
        r'\bkill\b',
        r'\bStop-Process\b',
        r'\bRemove-Item\s+-Recurse\b',
        r'\bSet-ExecutionPolicy\b',
    ]

    # ===========================================================
    # PROTECTED DIRECTORIES
    # ===========================================================

    PROTECTED_PATHS_WINDOWS = [
        r'C:\Windows',
        r'C:\Program Files',
        r'C:\Program Files (x86)',
        r'C:\ProgramData',
        r'C:\Users\All Users',
        r'C:\Recovery',
        r'C:\System Volume Information',
        r'C:\bootmgr',
        r'C:\ntldr',
    ]

    PROTECTED_PATHS_UNIX = [
        '/bin', '/sbin', '/usr/bin', '/usr/sbin',
        '/etc', '/boot', '/dev', '/proc', '/sys',
        '/var/log', '/root',
    ]

    PROTECTED_EXTENSIONS = [
        '.sys', '.dll', '.exe', '.msi',
        '.reg',
        '.bat', '.cmd',
        '.ps1',
    ]

    # Known dangerous / typosquat pip packages
    KNOWN_BAD_PACKAGES = {
        'colourama', 'python3-dateutil', 'jeIlyfish', 'python-binance',
        'cryptocode', 'colorama-real', 'requirements', 'setup.py',
        'libpeshka', 'libprocesshider', 'windows-glass', 'win32api-loader',
    }

    # Strict regex for pip package spec
    # Allows: name, [extras], version specifiers, url/path? No, only PyPI.
    PIP_SPEC_RE = re.compile(
        r'^[A-Za-z0-9][A-Za-z0-9._-]*'           # package name
        r'(\[[A-Za-z0-9._,\-\s]+\])?'            # optional [extras]
        r'($|'                                     # end
        r'\s*[<>=!~]=?\s*[A-Za-z0-9.*]+'         # version spec
        r'(\s*,\s*[A-Za-z0-9._\-]+\s*[<>=!~]=?\s*[A-Za-z0-9.*]+)*'  # more specs
        r')$'
    )

    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = os.path.realpath(workspace_dir or WORKSPACE_DIR)
        self._blocked_patterns = [re.compile(p, re.IGNORECASE) for p in self.BLOCKED_COMMANDS]
        self._risky_patterns = [re.compile(p, re.IGNORECASE) for p in self.RISKY_COMMANDS]

    # ===========================================================
    # PATH VALIDATION (with symlink resolution)
    # ===========================================================

    def _is_within(self, child: str, parent: str) -> bool:
        """True iff realpath(child) is the same as or inside realpath(parent)."""
        try:
            child_r = os.path.realpath(child)
            parent_r = os.path.realpath(parent)
            # commonpath raises ValueError on different drives (Windows) or roots
            return os.path.commonpath([child_r, parent_r]) == parent_r
        except (ValueError, OSError):
            return False

    def validate_path(self, path: str, allow_workspace_only: bool = True, must_exist: bool = False) -> str:
        """
        Validate and resolve a file path. Returns the resolved (realpath) absolute path.
        Raises SafetyViolation if the path is dangerous.
        """
        if not path:
            raise SafetyViolation("Path cannot be empty")

        # Reject NUL bytes and other control characters that some shells honor
        if any(ord(c) < 32 for c in path):
            raise SafetyViolation(f"Path contains control characters: {path!r}")

        # Resolve to absolute via realpath so symlinks are followed
        try:
            if os.path.isabs(path):
                resolved = os.path.realpath(path)
            else:
                resolved = os.path.realpath(os.path.join(self.workspace_dir, path))
        except (OSError, ValueError) as e:
            raise SafetyViolation(f"Cannot resolve path {path!r}: {e}")

        # Must-exist check (used by read_file, list_files)
        if must_exist and not os.path.exists(resolved):
            raise SafetyViolation(f"Path does not exist: {resolved}")

        # Workspace-only check for write operations
        if allow_workspace_only and not self._is_within(resolved, self.workspace_dir):
            raise SafetyViolation(
                f"🚫 BLOCKED: '{resolved}' is outside the workspace!\n"
                f"File operations are restricted to: {self.workspace_dir}\n"
                f"This prevents accidental damage to your system."
            )

        # Protected system paths (applies even for reads, unless the resolved
        # path is inside the workspace which is already vetted above)
        if not self._is_within(resolved, self.workspace_dir):
            protected = (
                self.PROTECTED_PATHS_WINDOWS if platform.system() == 'Windows'
                else self.PROTECTED_PATHS_UNIX
            )
            resolved_lower = resolved.lower()
            for protected_path in protected:
                if resolved_lower.startswith(protected_path.lower()):
                    raise SafetyViolation(
                        f"🚫 BLOCKED: '{resolved}' is a protected system path!\n"
                        f"Protected: {protected_path}\n"
                        f"You can only work within the workspace: {self.workspace_dir}"
                    )

        return resolved

    # ===========================================================
    # COMMAND VALIDATION
    # ===========================================================

    def validate_command(self, command: str) -> dict:
        if not command or not command.strip():
            return {"safe": False, "risk_level": "blocked", "message": "🚫 Empty command"}

        # Detect shell metacharacter smuggling (best-effort, not bulletproof)
        # If a command contains both base64 + execution markers, flag as risky
        for pattern in self._blocked_patterns:
            if pattern.search(command):
                return {
                    "safe": False,
                    "risk_level": "blocked",
                    "message": (
                        f"🚫 DANGEROUS COMMAND BLOCKED!\n\n"
                        f"Command: `{command}`\n\n"
                        f"This command could damage your system and has been blocked "
                        f"by the safety guardrails.\n\n"
                        f"Matched pattern: `{pattern.pattern}`"
                    ),
                }

        for pattern in self._risky_patterns:
            if pattern.search(command):
                return {
                    "safe": True,
                    "risk_level": "risky",
                    "message": (
                        f"⚠️ RISKY COMMAND detected: `{command}`\n\n"
                        f"This command may delete or modify files. Proceeding with caution."
                    ),
                }

        return {"safe": True, "risk_level": "safe", "message": "✅ Command looks safe"}

    # ===========================================================
    # FILE WRITE VALIDATION
    # ===========================================================

    def validate_file_write(self, path: str) -> dict:
        try:
            resolved = self.validate_path(path, allow_workspace_only=True)
        except SafetyViolation as e:
            return {"safe": False, "risk_level": "blocked", "message": str(e)}

        _, ext = os.path.splitext(resolved)
        if ext.lower() in self.PROTECTED_EXTENSIONS:
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": (
                    f"🚫 BLOCKED: Writing {ext} files is not allowed!\n"
                    f"This protects system files from being overwritten."
                ),
            }

        return {"safe": True, "risk_level": "safe", "message": f"✅ Safe to write: {resolved}"}

    # ===========================================================
    # FILE READ VALIDATION
    # ===========================================================

    SENSITIVE_PATTERNS = [
        re.compile(r'\.ssh[/\\](id_rsa|id_ed25519|authorized_keys)', re.IGNORECASE),
        re.compile(r'\.aws[/\\]credentials', re.IGNORECASE),
        re.compile(r'\.git[/\\]credentials', re.IGNORECASE),
        re.compile(r'\.git-credentials\b', re.IGNORECASE),
        re.compile(r'\.netrc\b', re.IGNORECASE),
        re.compile(r'\.env(\.|$)', re.IGNORECASE),
        re.compile(r'\bid_rsa(\.|$)', re.IGNORECASE),
        re.compile(r'\\SAM\b', re.IGNORECASE),
    ]

    def validate_file_read(self, path: str) -> dict:
        try:
            resolved = self.validate_path(path, allow_workspace_only=False)
        except SafetyViolation as e:
            return {"safe": False, "risk_level": "blocked", "message": str(e)}

        for pattern in self.SENSITIVE_PATTERNS:
            if pattern.search(resolved):
                return {
                    "safe": False,
                    "risk_level": "blocked",
                    "message": (
                        f"🚫 BLOCKED: '{resolved}' appears to contain sensitive credentials.\n"
                        f"Reading credential files is not allowed for security."
                    ),
                }

        return {"safe": True, "risk_level": "safe", "message": f"✅ Safe to read: {resolved}"}

    # ===========================================================
    # PIP INSTALL VALIDATION
    # ===========================================================

    def validate_pip_install(self, package: str) -> dict:
        if not package or not package.strip():
            return {"safe": False, "risk_level": "blocked", "message": "🚫 Empty package name"}

        # Strict format check first - this prevents shell-style injection
        if not self.PIP_SPEC_RE.match(package.strip()):
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": (
                    f"🚫 BLOCKED: '{package}' is not a valid pip package specifier.\n"
                    f"Allowed: name, name[extras], name==1.2.3, name>=1.0,<2.0, etc."
                ),
            }

        # Pull out the base name (before any version specifier or extras)
        base_name = re.split(r'[\[<>=!~,\s]', package.strip(), 1)[0].lower()
        if base_name in self.KNOWN_BAD_PACKAGES:
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": (
                    f"🚫 BLOCKED: '{package}' is a known dangerous/typosquat package!\n"
                    f"This package has been flagged as potentially malicious."
                ),
            }

        if '--system' in package or '--target' in package or '-t' in package.split():
            return {
                "safe": True,
                "risk_level": "risky",
                "message": f"⚠️ Installing with system/target flags: {package}",
            }

        return {"safe": True, "risk_level": "safe", "message": f"✅ Safe to install: {package}"}

    # ===========================================================
    # PYTHON CODE VALIDATION (best-effort, NOT a real sandbox)
    # ===========================================================

    DANGEROUS_PY_PATTERNS = [
        (re.compile(r'\bos\.system\s*\('), "os.system()"),
        (re.compile(r'\bsubprocess\.[A-Za-z_]*\(.*shell\s*=\s*True', re.IGNORECASE), "subprocess(shell=True)"),
        (re.compile(r'(^|[^.\w])eval\s*\('), "eval()"),
        (re.compile(r'(^|[^.\w])exec\s*\('), "exec()"),
        (re.compile(r'\b__import__\s*\('), "__import__()"),
        (re.compile(r'compile\s*\(.*exec', re.IGNORECASE), "compile(...,'exec')"),
        (re.compile(r'\bos\.remove\s*\(.*[\\/]Windows', re.IGNORECASE), "Deleting system files via os.remove"),
        (re.compile(r'\bshutil\.rmtree\s*\(.*[\\/]Windows', re.IGNORECASE), "Deleting system dirs via shutil.rmtree"),
    ]

    def validate_python_code(self, code: str) -> dict:
        warnings = []
        for pattern, desc in self.DANGEROUS_PY_PATTERNS:
            if pattern.search(code):
                warnings.append(desc)

        if warnings:
            return {
                "safe": True,
                "risk_level": "risky",
                "message": (
                    f"⚠️ Potentially risky Python patterns detected:\n"
                    + "\n".join(f"  - {w}" for w in warnings)
                    + "\n\nThis is a warning only. The code WILL run. Review it carefully."
                ),
            }

        return {"safe": True, "risk_level": "safe", "message": "✅ Code looks safe"}

    # ===========================================================
    # UTILITIES
    # ===========================================================

    def get_sandbox_info(self) -> str:
        return (
            f"🛡️ **Sandbox Active**\n"
            f"- Workspace: `{self.workspace_dir}`\n"
            f"- File ops restricted to workspace\n"
            f"- Dangerous commands blocked\n"
            f"- System paths protected\n"
            f"- Sensitive files guarded"
        )


# Singleton - lazy (don't import-time init to allow tests to override)
_guard: SafetyGuard | None = None


def _get_guard() -> SafetyGuard:
    global _guard
    if _guard is None:
        _guard = SafetyGuard()
    return _guard


# Backward-compat: `guard.validate_command(...)` etc.
class _GuardProxy:
    def __getattr__(self, name):
        return getattr(_get_guard(), name)


guard = _GuardProxy()

