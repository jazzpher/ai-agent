"""Fail-closed bubblewrap execution. App/provider secrets never enter tool children."""
import os
import signal
import subprocess
import sys
import tempfile
import shutil
import ctypes
import ctypes.util
import errno


def seccomp_file():
    """Block namespace/mount/host introspection syscalls even in nested children."""
    lib = ctypes.CDLL(ctypes.util.find_library('seccomp') or 'libseccomp.so.2')
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)  # ALLOW
    if not context:
        raise RuntimeError('Seccomp unavailable; not run')
    target = tempfile.TemporaryFile()
    try:
        denied = 0x00050000 | errno.EPERM
        for name in ['unshare', 'setns', 'mount', 'umount2', 'pivot_root', 'ptrace',
                     'process_vm_readv', 'process_vm_writev', 'bpf', 'keyctl',
                     'add_key', 'request_key', 'open_by_handle_at']:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add(context, denied, number, 0) != 0:
                raise RuntimeError('Seccomp rule failed; not run')
        clone3 = lib.seccomp_syscall_resolve_name(b'clone3')
        if clone3 >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.ENOSYS, clone3, 0) != 0:
            raise RuntimeError('Seccomp clone3 rule failed; not run')
        class Compare(ctypes.Structure):
            _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_uint),
                        ('mask', ctypes.c_uint64), ('value', ctypes.c_uint64)]
        clone = lib.seccomp_syscall_resolve_name(b'clone')
        # Reject clone with any namespace flag, allow ordinary fork/thread workers.
        for flag in [0x20000, 0x4000000, 0x8000000, 0x10000000, 0x20000000, 0x40000000]:
            if lib.seccomp_rule_add(context, denied, clone, 1, Compare(0,7,flag,flag)) != 0:
                raise RuntimeError('Seccomp clone rule failed; not run')
        if lib.seccomp_export_bpf(context, target.fileno()) != 0:
            raise RuntimeError('Seccomp export failed; not run')
        target.seek(0)
        return target
    except Exception:
        target.close()
        raise
    finally:
        lib.seccomp_release(context)


def available():
    binary = shutil.which('bwrap')
    if not binary:
        return False
    try:
        r = subprocess.run([binary, '--unshare-all', '--ro-bind', '/usr', '/usr',
                            '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
                            '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev',
                            '/bin/true'], capture_output=True, timeout=5)
        if r.returncode != 0:
            print("[sandbox] bubblewrap probe failed:", (r.stderr or b"").decode(errors="replace")[:500], flush=True)
        return r.returncode == 0
    except Exception:
        return False


def command(argv, workspace, venv, *, network=False, install=False):
    """Only runtime paths, workspace, and the current session venv are visible."""
    binary = shutil.which('bwrap')
    if not binary:
        raise RuntimeError('Kernel sandbox unavailable; command not run')
    root = os.path.realpath(workspace)
    args = [binary, '--die-with-parent', '--new-session', '--unshare-all', '--cap-drop', 'ALL']
    if network:
        args += ['--share-net']  # Explicit approved install only.
    args += ['--clearenv', '--setenv', 'PATH', venv+'/bin:/usr/local/bin:/usr/bin:/bin',
             '--setenv', 'HOME', '/tmp', '--setenv', 'TMPDIR', '/tmp',
             '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'VIRTUAL_ENV', venv,
             '--setenv', 'PYTHONNOUSERSITE', '1', '--setenv', 'OPENBLAS_NUM_THREADS', '1']
    paths = ['/usr', '/lib', '/lib64', '/bin', '/sbin']
    # Parent interpreter libraries are runtime only, never the app source directory.
    if sys.prefix != sys.base_prefix:
        paths.append(sys.prefix)
    if sys.base_prefix not in ['/usr', '/usr/local']:
        paths.append(sys.base_prefix)
    for path in dict.fromkeys(paths):
        if os.path.exists(path):
            args += ['--ro-bind', path, path]
    args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
             '--dir', '/etc']
    if network:
        for path in ['/etc/resolv.conf', '/etc/hosts', '/etc/ssl/certs']:
            if os.path.exists(path):
                args += ['--ro-bind', path, path]
    if sys.prefix.startswith('/tmp/'):
        args += ['--ro-bind', sys.prefix, sys.prefix]
    args += ['--bind', root, os.path.abspath(workspace),
             '--bind' if install else '--ro-bind', os.path.realpath(venv), venv,
             '--chdir', os.path.abspath(workspace), '--'] + list(argv)
    return args


def run(argv, workspace, venv, timeout, *, network=False, install=False):
    """Resource-limited child group; timeout kills descendants, not only the shell."""
    args = command(argv, workspace, venv, network=network, install=install)
    prlimit = shutil.which('prlimit')
    if not prlimit:
        return {'status':'error','sandbox':'unavailable','output':'Resource limiter unavailable; not run'}
    args = [prlimit, '--as=536870912', '--cpu=60', '--fsize=16777216',
            '--nofile=128', '--nproc=64', '--core=0', '--'] + args
    with seccomp_file() as policy, tempfile.TemporaryFile() as output:
        pos = args.index('--', args.index(shutil.which('bwrap')))
        args[pos:pos] = ['--seccomp', str(policy.fileno())]
        p = subprocess.Popen(args, stdout=output, stderr=output, start_new_session=True,
                             pass_fds=(policy.fileno(),), env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'})
        timed_out = False
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        output.seek(0)
        text = output.read(65536).decode('utf-8', errors='replace')
        if output.read(1):
            text += '\n[Output capped at 64 KB]'
    return {'status':'error' if timed_out or p.returncode else 'success',
            'returncode':p.returncode, 'sandbox':'bubblewrap',
            'network':'approved install' if network else 'blocked',
            'output':f'Timed out after {timeout}s' if timed_out else text.strip() or '(no output)'}
