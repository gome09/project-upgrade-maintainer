"""Workspace mutation lock with conservative stale-lock handling."""
from __future__ import annotations
import hashlib, json, os, socket, tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from .audit import now_iso
from .errors import CliError
from .durability import durable_unlink, fsync_directory
from .paths import require_within, verified_upgrade

def _pid_alive(pid:int)->bool:
    # Invalid IDs and failed probes are not proof that an owner has exited.
    if isinstance(pid,bool) or pid<=0:return True
    if os.name == 'nt':
        return _windows_pid_alive(pid)
    try: os.kill(pid,0); return True
    except ProcessLookupError:return False
    except PermissionError:return True
    except (OSError, OverflowError):return True

def _windows_pid_alive(pid:int)->bool:
    """Query process completion without Windows os.kill's termination semantics."""
    import ctypes
    from ctypes import wintypes
    if pid > 0xFFFFFFFF:return True
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no PID
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0  # only signaled proves exit
    finally:
        kernel.CloseHandle(handle)

@contextmanager
def _repository_guard(root:Path)->Iterator[None]:
    """Stable OS lock covering both missing and existing workspaces.

    Never unlink this file: replacing its inode would split the lock domain.
    The OS releases ownership even when a process crashes.
    """
    identity = os.path.normcase(str(root.resolve())).encode('utf-8')
    lock = Path(tempfile.gettempdir()) / ('project-upgrade-maintainer-' + hashlib.sha256(identity).hexdigest() + '.lock')
    if lock.is_symlink():raise CliError('repository guard must not be a symlink')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise CliError('upgrade workspace is locked by another mutation (repository guard)') from exc
        yield
    finally:
        try:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

def _remove_provably_stale(lock:Path)->bool:
    try:payload=json.loads(lock.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):return False
    if not isinstance(payload,dict) or payload.get('hostname')!=socket.gethostname():return False
    pid=payload.get('pid')
    if not isinstance(pid,int) or _pid_alive(pid):return False
    try:durable_unlink(lock);return True
    except OSError:return False

@contextmanager
def mutation_lock(root:Path,*,operation:str|None=None,transaction_id:str|None=None)->Iterator[None]:
    with _repository_guard(root):
        with _workspace_lock(root,operation=operation,transaction_id=transaction_id):
            yield

@contextmanager
def _workspace_lock(root:Path,*,operation:str|None=None,transaction_id:str|None=None)->Iterator[None]:
    raw=root/'.upgrade'
    if not raw.exists() and not raw.is_symlink():yield;return
    upgrade=verified_upgrade(root);lock=require_within(upgrade/'.lock',upgrade,'workspace lock')
    payload=json.dumps({'pid':os.getpid(),'hostname':socket.gethostname(),'operation':operation,'transaction_id':transaction_id,'command_started_at':now_iso()},ensure_ascii=False)+'\n'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError as exc:
        if _remove_provably_stale(lock):fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        else:
            try:detail=lock.read_text(encoding='utf-8').strip()
            except OSError:detail='unreadable lock'
            raise CliError(f'upgrade workspace is locked: {detail}') from exc
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        fsync_directory(lock.parent)
        yield
    finally:
        try:durable_unlink(lock)
        except FileNotFoundError:pass
