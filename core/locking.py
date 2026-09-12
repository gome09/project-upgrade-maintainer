"""Workspace mutation lock with conservative stale-lock handling."""
from __future__ import annotations
import json, os, socket
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from .audit import now_iso
from .errors import CliError
from .durability import durable_unlink, fsync_directory
from .paths import require_within, verified_upgrade

def _pid_alive(pid:int)->bool:
    if pid<=0:return False
    try: os.kill(pid,0); return True
    except ProcessLookupError:return False
    except PermissionError:return True
    except OSError:return False

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
