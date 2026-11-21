"""Durable write-ahead filesystem transaction journal and crash recovery."""
from __future__ import annotations
import contextvars, hashlib, json, os, re, socket, stat, uuid
from pathlib import Path
from typing import Any
from .audit import now_iso
from .constants import TRANSACTION_SCHEMA_VERSION
from .errors import CliError, StalePlanError
from .durability import atomic_write_bytes, durable_copy_file, durable_mkdir, durable_move_file, durable_rmtree, durable_unlink
from .paths import relative_posix, require_within, verified_upgrade

_CURRENT: contextvars.ContextVar['Transaction|None']=contextvars.ContextVar('upgrade_transaction',default=None)
_JOURNAL_DIR = Path(".upgrade") / "transactions"
_MOUNT_POINT_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)

def current_transaction(): return _CURRENT.get()

def _hash_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def _sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def _raw_json(path:Path,payload:dict[str,Any])->None:
    data=(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
    atomic_write_bytes(path,data)

def _descriptor(path:Path)->dict[str,Any]:
    if not path.exists():return {'exists':False}
    if path.is_symlink() or not path.is_file():raise CliError(f'transaction target must be a regular file or absent: {path}')
    return {'exists':True,'sha256':_sha(path),'size':path.stat().st_size}

def transaction_dir(root:Path)->Path:
    upgrade=verified_upgrade(root); raw=upgrade/'transactions'
    if raw.is_symlink():raise CliError('transaction directory must not be a symlink')
    durable_mkdir(raw)
    return require_within(raw,upgrade,'transaction directory')

def _validate_descriptor(value:Any,label:str)->None:
    if not isinstance(value,dict) or not isinstance(value.get('exists'),bool):
        raise CliError(f'invalid transaction journal descriptor: {label}')
    if value['exists']:
        if set(value)!={'exists','sha256','size'} or not isinstance(value.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['sha256']) or not isinstance(value.get('size'),int) or value['size']<0:
            raise CliError(f'invalid transaction journal descriptor: {label}')
    elif set(value)!={'exists'}:
        raise CliError(f'invalid transaction journal descriptor: {label}')

def _validate_operation(value:Any,index:int)->None:
    expected={'index','kind','source','target','pre_source','pre_target','post_sha256','backup','state'}
    if not isinstance(value,dict) or set(value)!=expected:
        raise CliError(f'invalid transaction journal operation {index}')
    if value.get('index')!=index or value.get('kind') not in {'write','move','copy','unlink'} or value.get('state') not in {'prepared','applied','rolled_back'}:
        raise CliError(f'invalid transaction journal operation {index}')
    for key in ('source','target','backup'):
        if value.get(key) is not None and not isinstance(value.get(key),str):
            raise CliError(f'invalid transaction journal operation {index} path')
    for key in ('pre_source','pre_target'):
        if value.get(key) is not None:_validate_descriptor(value[key],f'operation {index} {key}')
    post=value.get('post_sha256')
    if post is not None and (not isinstance(post,str) or not re.fullmatch(r'[0-9a-f]{64}',post)):
        raise CliError(f'invalid transaction journal operation {index} post digest')

def _validate_journal(value:Any,name:str)->dict[str,Any]:
    required={'schema_version','transaction_id','command','root','pid','hostname','started_at','updated_at','state','operations','error'}
    optional={'committed_at','rolled_back_at'}
    if not isinstance(value,dict) or not required.issubset(value) or set(value)-required-optional:
        raise CliError(f'invalid transaction journal schema: {name}')
    if value.get('schema_version')!=TRANSACTION_SCHEMA_VERSION:
        raise CliError(f'unsupported transaction journal: {name}')
    tid=value.get('transaction_id')
    if not isinstance(tid,str) or not re.fullmatch(r'TXN-[0-9A-F]{12}',tid):
        raise CliError(f'invalid transaction journal transaction_id: {name}')
    if not isinstance(value.get('command'),str) or not value['command'] or not isinstance(value.get('root'),str) or not value['root']:
        raise CliError(f'invalid transaction journal identity: {name}')
    if not isinstance(value.get('pid'),int) or value['pid']<=0 or not isinstance(value.get('hostname'),str) or not value['hostname']:
        raise CliError(f'invalid transaction journal owner: {name}')
    for key in ('started_at','updated_at'):
        if not isinstance(value.get(key),str) or not value[key]:raise CliError(f'invalid transaction journal timestamp: {name}')
    if value.get('state') not in {'active','failed','rolling_back','committed','aborted','rolled_back'}:
        raise CliError(f'invalid transaction journal state: {name}')
    if not isinstance(value.get('error'),str) or not isinstance(value.get('operations'),list):
        raise CliError(f'invalid transaction journal payload: {name}')
    for index,operation in enumerate(value['operations'],1):_validate_operation(operation,index)
    return value

def _load_journal(path:Path)->dict[str,Any]:
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc:raise CliError(f'invalid transaction journal {path.name}: {exc}') from exc
    value=_validate_journal(value,path.name)
    if path.stem!=value['transaction_id']:
        raise CliError(f'transaction journal filename does not match transaction_id: {path.name}')
    return value

def unresolved_transactions(root:Path)->list[dict[str,Any]]:
    raw=root/'.upgrade'/'transactions'
    if not raw.exists():return []
    if raw.is_symlink() or not raw.is_dir():raise CliError('transaction directory is unsafe')
    out=[]
    for path in sorted(raw.glob('TXN-*.json')):
        j=_load_journal(path)
        if j.get('root')!=str(root.resolve()):raise CliError(f'transaction journal root does not match workspace: {path.name}')
        if j.get('state') in {'active','failed','rolling_back'}:out.append(j)
    return out

class Transaction:
    def __init__(self,root:Path,command:str,transaction_id:str|None=None):
        self.root=root.resolve();self.command=command;self.transaction_id=transaction_id or f"TXN-{uuid.uuid4().hex[:12].upper()}";self.dir:Path|None=None;self.journal_path:Path|None=None;self.payload_dir:Path|None=None;self.data:dict[str,Any]={};self.token=None
    @classmethod
    def start(cls,root:Path,command:str,transaction_id:str|None=None)->'Transaction':
        tx=cls(root,command,transaction_id);tx._start();return tx
    def _start(self, *, deferred: bool = False):
        existing=unresolved_transactions(self.root)
        if existing:raise CliError(f"unresolved transaction exists: {existing[0].get('transaction_id')}; run recovery-audit")
        self.dir=transaction_dir(self.root);self.journal_path=self.dir/f'{self.transaction_id}.json';self.payload_dir=self.dir/self.transaction_id
        self.data={'schema_version':TRANSACTION_SCHEMA_VERSION,'transaction_id':self.transaction_id,'command':self.command,'root':str(self.root),'pid':os.getpid(),'hostname':socket.gethostname(),'started_at':now_iso(),'updated_at':now_iso(),'state':'active','operations':[],'error':''}
        # CLI calls defer publication until the first prepared mutation.
        # Explicit Transaction.start() retains its immediate journal contract.
        if not deferred:
            _raw_json(self.journal_path,self.data)
        self.token=_CURRENT.set(self)
    def should_track(self,path:Path)->bool:
        try:r=path.resolve(strict=False);require_within(r,self.root,'transaction target')
        except CliError:return False
        return self.dir is None or not (r==self.dir or self.dir in r.parents)
    def _flush(self):
        self.data['updated_at']=now_iso();_raw_json(self.journal_path,self.data)
    def _backup(self,path:Path,index:int)->str|None:
        if not path.exists():return None
        assert self.payload_dir is not None;durable_mkdir(self.payload_dir);backup=self.payload_dir/f'{index:06d}.bak';durable_copy_file(path,backup)
        return relative_posix(backup,self.root)
    def _prepare(self,kind:str,source:Path|None,target:Path|None,post_sha:str|None=None)->int:
        index=len(self.data['operations'])+1
        src=require_within(source,self.root,'transaction source') if source else None;dst=require_within(target,self.root,'transaction target') if target else None
        op={'index':index,'kind':kind,'source':relative_posix(src,self.root) if src else None,'target':relative_posix(dst,self.root) if dst else None,'pre_source':_descriptor(src) if src else None,'pre_target':_descriptor(dst) if dst else None,'post_sha256':post_sha,'backup':None,'state':'prepared'}
        backup_target=dst if kind in {'write','unlink'} else src if kind=='move' else None
        if backup_target is not None and backup_target.exists():op['backup']=self._backup(backup_target,index)
        self.data['operations'].append(op);self._flush();return index
    def prepare_write(self,path:Path,data:bytes)->int:return self._prepare('write',None,path,_hash_bytes(data))
    def prepare_move(self,source:Path,target:Path)->int:
        source=require_within(source,self.root,'move source');target=require_within(target,self.root,'move target')
        if not source.is_file() or source.is_symlink():raise CliError(f'move source is not a regular file: {source}')
        if target.exists() or target.is_symlink():raise CliError(f'move destination already exists: {target}')
        return self._prepare('move',source,target,_sha(source))
    def prepare_copy(self,source:Path,target:Path)->int:
        source=require_within(source,self.root,'copy source');target=require_within(target,self.root,'copy target')
        if not source.is_file() or source.is_symlink():raise CliError(f'copy source is not a regular file: {source}')
        if target.exists() or target.is_symlink():raise CliError(f'copy destination already exists: {target}')
        return self._prepare('copy',source,target,_sha(source))
    def prepare_unlink(self,path:Path)->int:
        path=require_within(path,self.root,'unlink target')
        if not path.is_file() or path.is_symlink():raise CliError(f'unlink target is not a regular file: {path}')
        return self._prepare('unlink',None,path,None)
    def mark_applied(self,index:int):self.data['operations'][index-1]['state']='applied';self._flush()
    def commit(self):
        if not self.data['operations']:
            if self.journal_path.exists():
                self.data['state']='committed';self.data['committed_at']=now_iso();self._flush()
            self.close();return
        self.data['state']='committed';self.data['committed_at']=now_iso();self._flush();self._cleanup_payload();self.close()
    def fail(self,message:str):
        self.data['error']=message
        if not self.data['operations']:
            self.data['state']='aborted'
            if self.journal_path.exists():self._flush()
            self._cleanup_payload()
        else:self.data['state']='failed';self._flush()
        self.close()
    def close(self):
        if self.token is not None:_CURRENT.reset(self.token);self.token=None
    def _cleanup_payload(self):
        if self.payload_dir and self.payload_dir.exists():durable_rmtree(self.payload_dir)
    @classmethod
    def load(cls,root:Path,transaction_id:str)->'Transaction':
        tx=cls(root,'recovery',transaction_id);tx.dir=transaction_dir(root);tx.journal_path=tx.dir/f'{transaction_id}.json';tx.payload_dir=tx.dir/transaction_id
        if not tx.journal_path.is_file():raise CliError(f'transaction journal not found: {transaction_id}')
        tx.data=_load_journal(tx.journal_path)
        if tx.data.get('root')!=str(root.resolve()):raise CliError(f'transaction journal root does not match workspace: {tx.journal_path.name}')
        return tx
    def journal_sha256(self)->str:return _sha(self.journal_path)
    def _path(self,value:str|None)->Path|None:return require_within(self.root/value,self.root,'transaction record path') if value else None
    def classify(self,op:dict[str,Any])->str:
        kind=op['kind'];src=self._path(op.get('source'));dst=self._path(op.get('target'));post=op.get('post_sha256')
        def same(path,desc):return bool(desc and desc.get('exists') and path and path.is_file() and not path.is_symlink() and _sha(path)==desc.get('sha256'))
        if kind=='write':
            if dst and dst.is_file() and _sha(dst)==post:return 'applied'
            pre=op.get('pre_target') or {}
            if (not pre.get('exists') and dst and not dst.exists()) or same(dst,pre):return 'not_applied'
        elif kind=='move':
            if src and not src.exists() and dst and dst.is_file() and _sha(dst)==post:return 'applied'
            if same(src,op.get('pre_source')) and dst and dst.is_file() and _sha(dst)==post:return 'duplicated'
            if same(src,op.get('pre_source')) and dst and not dst.exists():return 'not_applied'
        elif kind=='copy':
            if dst and dst.is_file() and _sha(dst)==post:return 'applied'
            if dst and not dst.exists():return 'not_applied'
        elif kind=='unlink':
            pre=op.get('pre_target') or {}
            if dst and not dst.exists():return 'applied'
            if same(dst,pre):return 'not_applied'
        return 'conflict'
    def _path_key(self, value: str) -> Path:
        # Group even a noncanonical spelling of the same address. The on-disk
        # checks still require the exact relative spelling emitted by the writer.
        return Path(os.path.normpath(self.root / value))

    def _write_chains(self) -> dict[Path, list[dict[str, Any]]]:
        groups: dict[Path, list[dict[str, Any]]] = {}
        for op in self.data['operations']:
            if op['kind'] == 'write' and op['target']:
                groups.setdefault(self._path_key(op['target']), []).append(op)
        return {path: ops for path, ops in groups.items() if len(ops) > 1}

    def _checked_recovery_path(self, value: str, *, directory: bool = False) -> Path:
        try:
            raw = Path(value)
            if raw.anchor or not raw.parts or '..' in raw.parts or raw.as_posix() != value:
                raise CliError(f'noncanonical transaction record path: {value}')
            path = self.root
            for index, part in enumerate(raw.parts, 1):
                path /= part
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', None) == _MOUNT_POINT_TAG:
                    raise CliError(f'transaction recovery path contains a link: {path}')
                is_expected_type = stat.S_ISDIR if directory or index < len(raw.parts) else stat.S_ISREG
                if not is_expected_type(info.st_mode):
                    raise CliError(f'transaction recovery path has an unexpected type: {path}')
            path = self.root / raw
            if require_within(path, self.root, 'transaction record path') != path:
                raise CliError(f'noncanonical physical transaction record path: {value}')
            return path
        except (OSError, RuntimeError, ValueError) as exc:
            raise CliError(f'cannot verify transaction record path: {value}: {exc}') from exc

    @staticmethod
    def _recovery_descriptor(path: Path) -> dict[str, Any]:
        try:
            return _descriptor(path)
        except OSError as exc:
            raise CliError(f'cannot read transaction recovery file: {path}: {exc}') from exc

    def _checked_backup(self, op: dict[str, Any]) -> Path | None:
        before = op['pre_target']
        if not before['exists']:
            if op['backup'] is not None:
                raise CliError(f'unexpected backup for operation {op["index"]}')
            return None
        filename = f'{op["index"]:06d}.bak'
        expected = (_JOURNAL_DIR / self.transaction_id / filename).as_posix()
        if op['backup'] != expected or len(filename) != 10:
            raise CliError(f'invalid backup location for operation {op["index"]}')
        backup = self._checked_recovery_path(expected)
        if self._recovery_descriptor(backup) != before:
            raise CliError(f'missing or changed transaction backup: {expected}')
        return backup

    @staticmethod
    def _post_matches(op: dict[str, Any], descriptor: dict[str, Any]) -> bool:
        return descriptor['exists'] and descriptor['sha256'] == op['post_sha256']

    def _write_action(
        self, op: dict[str, Any], current: dict[str, Any],
        next_pre: dict[str, Any] | None, *, may_have_undone: bool,
    ) -> str:
        at_pre = current == op['pre_target']
        expected = (current == next_pre if next_pre is not None else
                    self._post_matches(op, current) or (op['state'] == 'prepared' and at_pre))
        if expected or (may_have_undone and at_pre):
            return 'not_applied' if at_pre else 'applied'
        return 'conflict'

    def _rollback_boundary(self) -> int | None:
        state = self.data['state']
        previously_undone = False
        next_index = None
        for op in self.data['operations']:
            if op['state'] == 'rolled_back':
                if state != 'rolling_back':
                    raise CliError('transaction has rollback progress outside recovery')
                previously_undone = True
            else:
                if previously_undone:
                    raise CliError('transaction rollback progress is not a suffix of the log')
                next_index = op['index']
        return next_index if state == 'rolling_back' else None

    def _chain_actions(self, ops: list[dict[str, Any]], boundary: int | None) -> dict[int, str]:
        destination = self._checked_recovery_path(ops[0]['target'])
        journal_dir = self.root / _JOURNAL_DIR
        if destination == journal_dir or journal_dir in destination.parents:
            raise CliError(f'transaction write chain targets recovery storage: {destination}')
        actions: dict[int, str] = {}
        pre_sizes: dict[str, int] = {}
        for node, op in enumerate(ops):
            before = op['pre_target']
            if (type(op['index']) is not int or op['source'] is not None or op['pre_source'] is not None
                    or before is None or op['post_sha256'] is None
                    or self._checked_recovery_path(op['target']) != destination):
                raise CliError(f'invalid transaction write chain operation: {op["index"]}')
            if before['exists']:
                sha, size = before['sha256'], before['size']
                if type(size) is not int or pre_sizes.setdefault(sha, size) != size:
                    raise CliError(f'inconsistent transaction write chain pre-image: {op["index"]}')
            self._checked_backup(op)
            if node + 1 < len(ops):
                successor_pre = ops[node + 1]['pre_target']
                if not successor_pre or not (self._post_matches(op, successor_pre) or
                        (op['state'] != 'applied' and successor_pre == before)):
                    raise CliError(f'inconsistent adjacent transaction writes: {op["index"]} and {ops[node + 1]["index"]}')
                actions[op['index']] = 'applied' if successor_pre != before else 'not_applied'
            if op['state'] == 'rolled_back':
                actions[op['index']] = 'not_applied'

        current = self._recovery_descriptor(destination)
        for node in range(len(ops) - 1, -1, -1):
            op = ops[node]
            if op['state'] == 'rolled_back':
                continue
            next_pre = ops[node + 1]['pre_target'] if node + 1 < len(ops) else None
            action = self._write_action(op, current, next_pre, may_have_undone=op['index'] == boundary)
            if action == 'conflict':
                raise CliError(f'transaction write chain filesystem conflict: {destination}')
            actions[op['index']] = action
            break
        else:
            if current != ops[0]['pre_target']:
                raise CliError(f'rolled back transaction write chain changed: {destination}')
        return actions

    def _analysis(self, chains: dict[Path, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        operations = self.data['operations']
        states: dict[int, str] = {}
        if chains:
            invalid_paths: set[Path] = set()
            try:
                boundary = self._rollback_boundary()
                self._checked_recovery_path((_JOURNAL_DIR / f'{self.transaction_id}.json').as_posix())
                self._checked_recovery_path((_JOURNAL_DIR / self.transaction_id).as_posix(), directory=True)
            except CliError:
                return [{'index': op['index'], 'kind': op['kind'], 'state': 'conflict'} for op in operations]

            for op in operations:
                for field in ('source', 'target'):
                    value = op[field]
                    if value is None:
                        continue
                    key = self._path_key(value)
                    if field == 'target' and op['kind'] == 'write' and key in chains:
                        continue
                    if key in chains:
                        invalid_paths.add(key)
                        states[op['index']] = 'conflict'

            for key, ops in chains.items():
                try:
                    if key in invalid_paths or any(op['index'] in states for op in ops):
                        raise CliError(f'transaction write chain has a mixed operation: {key}')
                    states.update(self._chain_actions(ops, boundary))
                except CliError:
                    states.update((op['index'], 'conflict') for op in ops)

        for op in operations:
            index = op['index']
            if index in states:
                continue
            states[index] = self._independent_recovery_action(op) if chains else self.classify(op)
        return [{'index': op['index'], 'kind': op['kind'], 'state': states[op['index']]} for op in operations]

    def _independent_recovery_action(self, op: dict[str, Any]) -> str:
        try:
            for field in ('source', 'target'):
                if op[field] is not None:
                    self._checked_recovery_path(op[field])
            if op['state'] == 'rolled_back':
                return 'not_applied'
            state = self.classify(op)
            if state == 'applied' and op['kind'] in {'write', 'unlink'}:
                if op['pre_target'] is None:
                    return 'conflict'
                self._checked_backup(op)
            return state
        except (CliError, OSError, RuntimeError, ValueError):
            return 'conflict'

    def analysis(self) -> list[dict[str, Any]]:
        return self._analysis(self._write_chains())

    def _rollback_operation(self, op: dict[str, Any], action: str, *, checked: bool) -> None:
        kind = op['kind']
        path_for = self._checked_recovery_path if checked else self._path
        src = path_for(op['source']) if op['source'] else None
        dst = path_for(op['target']) if op['target'] else None
        if kind == 'write' and not op['pre_target']['exists']:
            if checked or dst.exists():
                durable_unlink(dst)
        elif kind in {'write', 'unlink'}:
            backup = self._checked_backup(op) if checked else self._path(op['backup'])
            if not backup or not backup.is_file():
                raise CliError('transaction rollback payload is missing')
            durable_copy_file(backup, dst)
        elif kind == 'move' and action != 'duplicated':
            durable_move_file(dst, src)
        elif kind == 'move' or checked or dst.exists():
            durable_unlink(dst)

    def rollback(self, *, expected_analysis: list[dict[str, Any]] | None = None) -> None:
        chains = self._write_chains()
        current_analysis = self._analysis(chains)
        if expected_analysis is not None and current_analysis != expected_analysis:
            raise StalePlanError('transaction filesystem state changed since recovery review; regenerate the recovery plan')
        actions = {entry['index']: entry['state'] for entry in current_analysis}
        if 'conflict' in actions.values():
            raise CliError('transaction recovery conflict; automatic rollback refused')
        next_pre_by_index = {
            op['index']: ops[node + 1]['pre_target'] if node + 1 < len(ops) else None
            for ops in chains.values() for node, op in enumerate(ops)
        }

        self.data['state'] = 'rolling_back'
        self._flush()
        for op in reversed(self.data['operations']):
            if chains and op['state'] == 'rolled_back':
                continue
            index = op['index']
            action = actions[index]
            if index in next_pre_by_index:
                target = self._checked_recovery_path(op['target'])
                current = self._recovery_descriptor(target)
                observed = self._write_action(op, current, next_pre_by_index[index], may_have_undone=True)
                if observed != action:
                    raise CliError(f'transaction recovery state changed at operation {index}: {target}; run recovery-review again')
            elif chains and self._independent_recovery_action(op) != action:
                raise CliError(f'transaction recovery state changed at operation {index}; run recovery-review again')
            if action in {'applied', 'duplicated'}:
                self._rollback_operation(op, action, checked=bool(chains))
            elif not chains:
                continue
            op['state'] = 'rolled_back'
            self._flush()

        for ops in chains.values():
            first = ops[0]
            target = self._checked_recovery_path(first['target'])
            if self._recovery_descriptor(target) != first['pre_target']:
                raise CliError(f'rolled back transaction write chain changed: {target}; run recovery-review again')
        self.data['state'] = 'rolled_back'
        self.data['rolled_back_at'] = now_iso()
        self._flush()
        self._cleanup_payload()

def transaction_move(source:Path,target:Path)->None:
    tx=current_transaction();index=tx.prepare_move(source,target) if tx else None;durable_move_file(source,target)
    if tx:tx.mark_applied(index)
def transaction_copy(source:Path,target:Path)->None:
    tx=current_transaction();index=tx.prepare_copy(source,target) if tx else None;durable_copy_file(source,target)
    if tx:tx.mark_applied(index)
def transaction_unlink(path:Path)->None:
    tx=current_transaction();index=tx.prepare_unlink(path) if tx else None;durable_unlink(path)
    if tx:tx.mark_applied(index)
