"""Durable write-ahead filesystem transaction journal and crash recovery."""
from __future__ import annotations
import contextvars, hashlib, json, os, re, socket, uuid
from pathlib import Path
from typing import Any
from .audit import now_iso
from .constants import TRANSACTION_SCHEMA_VERSION
from .errors import CliError
from .durability import atomic_write_bytes, durable_copy_file, durable_mkdir, durable_move_file, durable_rmtree, durable_unlink
from .paths import relative_posix, require_within, verified_upgrade

_CURRENT: contextvars.ContextVar['Transaction|None']=contextvars.ContextVar('upgrade_transaction',default=None)

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
    def analysis(self)->list[dict[str,Any]]:return [{'index':op['index'],'kind':op['kind'],'state':self.classify(op)} for op in self.data.get('operations',[])]
    def rollback(self):
        analysis={x['index']:x['state'] for x in self.analysis()}
        if any(v=='conflict' for v in analysis.values()):raise CliError('transaction recovery conflict; automatic rollback refused')
        self.data['state']='rolling_back';self._flush()
        for op in reversed(self.data.get('operations',[])):
            if analysis[op['index']] not in {'applied','duplicated'}:continue
            kind=op['kind'];src=self._path(op.get('source'));dst=self._path(op.get('target'));backup=self._path(op.get('backup'))
            if kind=='write':
                if op['pre_target'].get('exists'):
                    if not backup or not backup.is_file():raise CliError('transaction rollback payload is missing')
                    durable_copy_file(backup,dst)
                elif dst.exists():durable_unlink(dst)
            elif kind=='move':
                if analysis[op['index']]=='duplicated':
                    durable_unlink(dst)
                else:
                    durable_move_file(dst,src)
            elif kind=='copy':
                if dst.exists():durable_unlink(dst)
            elif kind=='unlink':
                if not backup or not backup.is_file():raise CliError('transaction rollback payload is missing')
                durable_copy_file(backup,dst)
            op['state']='rolled_back';self._flush()
        self.data['state']='rolled_back';self.data['rolled_back_at']=now_iso();self._flush();self._cleanup_payload()

def transaction_move(source:Path,target:Path)->None:
    tx=current_transaction();index=tx.prepare_move(source,target) if tx else None;durable_move_file(source,target)
    if tx:tx.mark_applied(index)
def transaction_copy(source:Path,target:Path)->None:
    tx=current_transaction();index=tx.prepare_copy(source,target) if tx else None;durable_copy_file(source,target)
    if tx:tx.mark_applied(index)
def transaction_unlink(path:Path)->None:
    tx=current_transaction();index=tx.prepare_unlink(path) if tx else None;durable_unlink(path)
    if tx:tx.mark_applied(index)
