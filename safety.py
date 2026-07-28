"""
Safety Guardrails & Sandbox for the AI Agent

Protects the user's system by:
1. Restricting file operations to the workspace directory
2. Blocking dangerous shell commands
3. Preventing path traversal attacks
4. Warning/blocking destructive operations
5. Protecting system directories and files
"""
import os
import re
import platform
from config import WORKSPACE_DIR


class SafetyViolation(Exception):
    """Raised when an operation violates safety rules."""
    pass


class SafetyGuard:
    """
    Central safety system that validates all tool operations
    before they are executed.
    """

    # ===========================================================
    # DANGEROUS COMMANDS - Blocklist
    # ===========================================================
    
    # Commands that should NEVER be executed
    BLOCKED_COMMANDS = [
        # Windows destructive commands
        r'\bformat\s+[a-zA-Z]:',           # format C:
        r'\bdel\s+/[sS]\s+[a-zA-Z]:\\',    # del /s C:\...
        r'\brd\s+/[sS]\s+[a-zA-Z]:\\(Windows|Program|Users)',  # rd /s C:\Windows
        r'\brmdir\s+/[sS]\s+[a-zA-Z]:\\(Windows|Program|Users)',
        r'\breg\s+delete\b',               # Registry deletion
        r'\bregedit\s+/[sS]',              # Silent registry edit
        r'\bsfc\s+/scannow',               # System file checker
        r'\bbcdedit\b',                    # Boot config editor
        r'\bwmic\s+.*\bdelete\b',          # WMI deletion
        r'\bnet\s+stop\b',                 # Stop services
        r'\btaskkill\s+/[fF]\s+/im\b.*\b(explorer|csrss|winlogon|services|svchost|lsass)\b',
        r'\bshutdown\b',                   # Shutdown
        r'\brestart\b.*[/\-]',             # Restart
        r'\bcipher\s+/[wW]',              # Wipe free space
        r'\bfsutil\b',                     # File system utility
        r'\bdiskpart\b',                   # Disk partitioner
        r'\bnetsh\s+.*\bdelete\b',         # Network config deletion
        r'\bpowercfg\s+/h\s+off',          # Disable hibernation
        r'\battrib\s+.*-[rR].*\\Windows',  # Remove read-only from system
        
        # Cross-platform dangerous patterns
        r'\brm\s+-[rR]f\s+/',             # rm -rf /
        r'\brm\s+-[rR]f\s+~',             # rm -rf ~
        r'\bmkfs\b',                        # Format filesystem
        r'\bdd\s+if=',                      # Disk destroyer
        r':(){ :\|:& };:',                 # Fork bomb
        r'\bchmod\s+-R\s+777\s+/',         # Open permissions on root
        
        # Network attacks
        r'\bcurl\b.*\|\s*(bash|sh|cmd|powershell)',   # Download & execute
        r'\bwget\b.*\|\s*(bash|sh|cmd|powershell)',
        r'\biwr\b.*\|\s*iex',                          # PowerShell download & execute
        r'\bInvoke-Expression\b.*\bInvoke-WebRequest\b',
    ]

    # Commands that need extra caution (warn but allow)
    RISKY_COMMANDS = [
        r'\brm\b',                          # Any delete
        r'\bdel\b',                         # Windows delete
        r'\brmdir\b',                       # Remove directory
        r'\brd\s',                          # Remove directory
        r'\bmv\b.*\s+/',                    # Move to root paths
        r'\bmove\b',                        # Windows move
        r'\bpip\s+uninstall\b',            # Uninstall packages
        r'\bnpm\s+uninstall\b',            # Uninstall npm packages
        r'\bgit\s+reset\s+--hard',         # Git hard reset
        r'\bgit\s+clean\b',               # Git clean
        r'\bDROP\s+TABLE\b',              # SQL drop
        r'\bDROP\s+DATABASE\b',           # SQL drop database
        r'\bsudo\b',                        # Sudo (if somehow on Linux)
        r'\bkill\b',                        # Kill processes
        r'\bStop-Process\b',              # PowerShell kill
        r'\bRemove-Item\s+-Recurse\b',    # PowerShell recursive delete
    ]

    # ===========================================================
    # PROTECTED DIRECTORIES - Never touch these
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

    # ===========================================================
    # PROTECTED FILE EXTENSIONS - Be careful with these
    # ===========================================================
    
    PROTECTED_EXTENSIONS = [
        '.sys', '.dll', '.exe', '.msi',    # System files
        '.reg',                              # Registry
        '.bat', '.cmd',                      # Batch files (outside workspace)
        '.ps1',                              # PowerShell scripts (outside workspace)
    ]

    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = workspace_dir or WORKSPACE_DIR
        self._blocked_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.BLOCKED_COMMANDS
        ]
        self._risky_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.RISKY_COMMANDS
        ]

    # ===========================================================
    # PATH VALIDATION
    # ===========================================================

    def validate_path(self, path: str, allow_workspace_only: bool = True) -> str:
        """
        Validate and resolve a file path.
        Returns the resolved absolute path.
        Raises SafetyViolation if the path is dangerous.
        """
        if not path:
            raise SafetyViolation("Path cannot be empty")

        # Resolve to absolute path
        if not os.path.isabs(path):
            resolved = os.path.normpath(os.path.join(self.workspace_dir, path))
        else:
            resolved = os.path.normpath(path)

        # Check for path traversal attempts
        if '..' in path:
            # Allow only if resolved path is still within workspace
            if not resolved.startswith(os.path.normpath(self.workspace_dir)):
                raise SafetyViolation(
                    f"🚫 Path traversal detected! '{path}' tries to escape the workspace.\n"
                    f"All file operations must stay within: {self.workspace_dir}"
                )

        # Check against protected paths
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

        # Check workspace restriction for write operations
        if allow_workspace_only:
            workspace_norm = os.path.normpath(self.workspace_dir)
            if not resolved.startswith(workspace_norm):
                raise SafetyViolation(
                    f"🚫 BLOCKED: '{resolved}' is outside the workspace!\n"
                    f"File operations are restricted to: {self.workspace_dir}\n"
                    f"This prevents accidental damage to your system."
                )

        return resolved

    # ===========================================================
    # COMMAND VALIDATION
    # ===========================================================

    def validate_command(self, command: str) -> dict:
        """
        Validate a shell command before execution.
        Returns:
            {
                "safe": True/False,
                "risk_level": "safe" | "risky" | "blocked",
                "message": explanation
            }
        """
        if not command or not command.strip():
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": "🚫 Empty command"
            }

        # Check blocked commands
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
                    )
                }

        # Check risky commands (warn but allow)
        for pattern in self._risky_patterns:
            if pattern.search(command):
                return {
                    "safe": True,
                    "risk_level": "risky",
                    "message": (
                        f"⚠️ RISKY COMMAND detected: `{command}`\n\n"
                        f"This command may delete or modify files. "
                        f"Proceeding with caution."
                    )
                }

        return {
            "safe": True,
            "risk_level": "safe",
            "message": "✅ Command looks safe"
        }

    # ===========================================================
    # FILE WRITE VALIDATION
    # ===========================================================

    def validate_file_write(self, path: str) -> dict:
        """
        Validate a file write operation.
        Returns safety check result dict.
        """
        try:
            resolved = self.validate_path(path, allow_workspace_only=True)
        except SafetyViolation as e:
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": str(e)
            }

        # Check protected extensions outside workspace
        _, ext = os.path.splitext(resolved)
        if ext.lower() in self.PROTECTED_EXTENSIONS:
            workspace_norm = os.path.normpath(self.workspace_dir)
            if not resolved.startswith(workspace_norm):
                return {
                    "safe": False,
                    "risk_level": "blocked",
                    "message": (
                        f"🚫 BLOCKED: Writing {ext} files outside workspace is not allowed!\n"
                        f"This protects system files from being overwritten."
                    )
                }

        return {
            "safe": True,
            "risk_level": "safe",
            "message": f"✅ Safe to write: {resolved}"
        }

    # ===========================================================
    # FILE READ VALIDATION
    # ===========================================================

    def validate_file_read(self, path: str) -> dict:
        """
        Validate a file read operation.
        Reading is more permissive than writing, but still blocks sensitive files.
        """
        try:
            resolved = self.validate_path(path, allow_workspace_only=False)
        except SafetyViolation as e:
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": str(e)
            }

        # Block reading sensitive files
        sensitive_patterns = [
            r'\.ssh[/\\](id_rsa|id_ed25519|authorized_keys)',
            r'\.aws[/\\]credentials',
            r'\.env\b',
            r'\.git[/\\]credentials',
            r'\.netrc',
            r'SAM\b',
            r'SYSTEM\b.*\bRegistry',
            r'\.git-credentials',
        ]
        
        for pattern in sensitive_patterns:
            if re.search(pattern, resolved, re.IGNORECASE):
                return {
                    "safe": False,
                    "risk_level": "blocked",
                    "message": (
                        f"🚫 BLOCKED: '{resolved}' appears to contain sensitive credentials.\n"
                        f"Reading credential files is not allowed for security."
                    )
                }

        return {
            "safe": True,
            "risk_level": "safe",
            "message": f"✅ Safe to read: {resolved}"
        }

    # ===========================================================
    # PIP INSTALL VALIDATION
    # ===========================================================

    def validate_pip_install(self, package: str) -> dict:
        """
        Validate a pip install request.
        Blocks known malicious or dangerous packages.
        """
        # Known dangerous/typosquat packages
        KNOWN_BAD_PACKAGES = [
            'colourama',       # Typosquat of colorama
            'python3-dateutil', # Typosquat
            'jeIlyfish',       # Typosquat of jellyfish (capital I)
            'python-binance',  # Often targeted
            'cryptocode',      # Known malware
            'colorama-real',   # Typosquat
        ]

        package_lower = package.lower().strip()

        if package_lower in [p.lower() for p in KNOWN_BAD_PACKAGES]:
            return {
                "safe": False,
                "risk_level": "blocked",
                "message": (
                    f"🚫 BLOCKED: '{package}' is a known dangerous/typosquat package!\n"
                    f"This package has been flagged as potentially malicious."
                )
            }

        # Warn about packages with --user or system-wide flags
        if '--system' in package or '--target' in package:
            return {
                "safe": True,
                "risk_level": "risky",
                "message": f"⚠️ Installing with system flags: {package}"
            }

        return {
            "safe": True,
            "risk_level": "safe",
            "message": f"✅ Safe to install: {package}"
        }

    # ===========================================================
    # PYTHON CODE VALIDATION
    # ===========================================================

    def validate_python_code(self, code: str) -> dict:
        """
        Basic validation of Python code before execution.
        """
        dangerous_patterns = [
            (r'\bos\.system\s*\(', "os.system() - use subprocess instead"),
            (r'\beval\s*\(', "eval() - potentially dangerous"),
            (r'\bexec\s*\(', "exec() - potentially dangerous"),
            (r'\b__import__\s*\(', "__import__() - dynamic import"),
            (r'\bos\.remove\s*\(.*/(Windows|Program)', "Deleting system files"),
            (r'\bshutil\.rmtree\s*\(.*/(Windows|Program)', "Deleting system dirs"),
        ]

        warnings = []
        for pattern, desc in dangerous_patterns:
            if re.search(pattern, code):
                warnings.append(desc)

        if warnings:
            return {
                "safe": True,
                "risk_level": "risky",
                "message": (
                    f"⚠️ Potentially risky Python code detected:\n"
                    + "\n".join(f"  - {w}" for w in warnings)
                )
            }

        return {
            "safe": True,
            "risk_level": "safe",
            "message": "✅ Code looks safe"
        }

    # ===========================================================
    # SUMMARY / STATUS
    # ===========================================================

    def get_sandbox_info(self) -> str:
        """Return a human-readable sandbox description."""
        return (
            f"🛡️ **Sandbox Active**\n"
            f"- Workspace: `{self.workspace_dir}`\n"
            f"- File ops restricted to workspace\n"
            f"- Dangerous commands blocked\n"
            f"- System paths protected\n"
            f"- Sensitive files guarded"
        )


# ============================================================
# SINGLETON INSTANCE
# ============================================================

guard = SafetyGuard()
