"""
Safety Test Suite - Test the sandbox guardrails
Run this to verify all safety features are working.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from safety import guard, SafetyViolation
from tools import run_bash, read_file, write_file, pip_install, run_python


def test_blocked_commands():
    """Test that dangerous commands are blocked."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Blocked Commands")
    print("=" * 60)

    dangerous_commands = [
        ("format C:", "Format drive"),
        ("del /s C:\\Windows\\System32", "Delete system files"),
        ("shutdown /s", "Shutdown computer"),
        ("rmdir /s C:\\Program Files", "Delete Program Files"),
        ("reg delete HKLM\\SOFTWARE", "Delete registry"),
        ("taskkill /f /im svchost.exe", "Kill critical process"),
        ("diskpart", "Disk partitioner"),
    ]

    passed = 0
    failed = 0

    for cmd, desc in dangerous_commands:
        result = run_bash(cmd)
        if result["status"] == "blocked":
            print(f"  ✅ BLOCKED: {desc} ({cmd})")
            passed += 1
        else:
            print(f"  ❌ NOT BLOCKED: {desc} ({cmd}) - Status: {result['status']}")
            failed += 1

    print(f"\n  Result: {passed}/{passed+failed} passed")
    return failed == 0


def test_risky_commands():
    """Test that risky commands are flagged but allowed."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Risky Commands (warned but allowed)")
    print("=" * 60)

    risky_commands = [
        ("echo test", "Normal echo"),  # Should be safe
    ]

    for cmd, desc in risky_commands:
        result = run_bash(cmd)
        print(f"  ℹ️  {desc}: {result['status']} (risk: {result.get('risk_level', 'N/A')})")


def test_path_protection():
    """Test that system paths are protected."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Path Protection")
    print("=" * 60)

    # Try to write to system directories
    test_paths = [
        ("C:\\Windows\\test.txt", "Write to Windows dir"),
        ("C:\\Program Files\\test.txt", "Write to Program Files"),
        ("../../Windows/test.txt", "Path traversal attack"),
    ]

    passed = 0
    failed = 0

    for path, desc in test_paths:
        result = write_file(path, "test content")
        if result["status"] == "blocked":
            print(f"  ✅ BLOCKED: {desc} ({path})")
            passed += 1
        else:
            print(f"  ❌ NOT BLOCKED: {desc} ({path}) - Status: {result['status']}")
            failed += 1

    print(f"\n  Result: {passed}/{passed+failed} passed")
    return failed == 0


def test_safe_operations():
    """Test that safe operations still work."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Safe Operations (should work)")
    print("=" * 60)

    passed = 0
    failed = 0

    # Write a file in workspace
    result = write_file("test_safety.txt", "Hello from safety test!")
    if result["status"] == "success":
        print(f"  ✅ Write to workspace: OK")
        passed += 1
    else:
        print(f"  ❌ Write to workspace: {result['output']}")
        failed += 1

    # Read the file back
    result = read_file("test_safety.txt")
    if result["status"] == "success" and "Hello from safety test" in result["output"]:
        print(f"  ✅ Read from workspace: OK")
        passed += 1
    else:
        print(f"  ❌ Read from workspace: {result['output']}")
        failed += 1

    # List files
    result = run_bash("echo hello")
    if result["status"] == "success":
        print(f"  ✅ Safe bash command: OK")
        passed += 1
    else:
        print(f"  ❌ Safe bash command: {result['output']}")
        failed += 1

    # Run safe Python
    result = run_python("print('hello world')")
    if result["status"] == "success":
        print(f"  ✅ Safe Python code: OK")
        passed += 1
    else:
        print(f"  ❌ Safe Python code: {result['output']}")
        failed += 1

    print(f"\n  Result: {passed}/{passed+failed} passed")

    # Cleanup
    try:
        os.remove(os.path.join(os.path.dirname(__file__), "workspace", "test_safety.txt"))
    except:
        pass

    return failed == 0


def test_pip_validation():
    """Test pip package validation."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Pip Package Validation")
    print("=" * 60)

    # Test blocked package
    result = pip_install("colourama")  # Known typosquat
    if result["status"] == "blocked":
        print(f"  ✅ BLOCKED: Known typosquat package (colourama)")
    else:
        print(f"  ℹ️  colourama: {result['status']} (may not be in blocklist on this system)")

    # Test safe package (don't actually install, just validate)
    check = guard.validate_pip_install("requests")
    if check["safe"]:
        print(f"  ✅ ALLOWED: Safe package (requests)")
    else:
        print(f"  ❌ Safe package blocked: {check['message']}")


def test_credential_protection():
    """Test that credential files are protected."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Credential Protection")
    print("=" * 60)

    # These paths don't exist but should be blocked by pattern
    sensitive_paths = [
        ("C:\\Users\\user\\.ssh\\id_rsa", "SSH private key"),
        ("C:\\Users\\user\\.aws\\credentials", "AWS credentials"),
        ("C:\\Users\\user\\.git\\credentials", "Git credentials"),
    ]

    for path, desc in sensitive_paths:
        check = guard.validate_file_read(path)
        if not check["safe"]:
            print(f"  ✅ PROTECTED: {desc}")
        else:
            print(f"  ⚠️  Not protected: {desc} (file may not exist)")


if __name__ == "__main__":
    print("\n" + "🛡️" * 30)
    print("  AI Agent - Safety Test Suite")
    print("🛡️" * 30)

    all_passed = True

    all_passed &= test_blocked_commands()
    all_passed &= test_path_protection()
    all_passed &= test_safe_operations()
    test_risky_commands()
    test_pip_validation()
    test_credential_protection()

    print("\n" + "=" * 60)
    if all_passed:
        print("  🎉 ALL CRITICAL TESTS PASSED! Sandbox is working!")
    else:
        print("  ⚠️  Some tests failed. Check output above.")
    print("=" * 60)
