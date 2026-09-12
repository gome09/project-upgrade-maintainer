"""Append-only Risk governance commands."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from core.audit import Result
from core.constants import RISK_SCHEMA_VERSION
from core.errors import CliError
from core.paths import relative_posix,require_within,verified_upgrade
from core.workspace import activate_workspace_modules,ensure_workspace_structure,preflight_mutation_workspace
from core.plans import assert_file_snapshot_fresh,validate_apply_plan,write_plan_output
from core.schemas import governance_record_digest,normalize_governance_id,seal_governance_record,validate_risk_history,validate_risk_record
from ._governance_records import governance_record_id,read_jsonl_objects,record_snapshot
from ._runtime import atomic_write,build_review_plan,load_plan,now_iso, finalize_mutation

def _path(upgrade:Path,id:str)->Path:return require_within(upgrade/'risks'/f'{id}.jsonl',upgrade,'risk history')
def _load(path:Path,upgrade:Path):return validate_risk_history(read_jsonl_objects(path,upgrade,'risk history'))

def risk_review(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True); previous=None;snapshot=None
    rid=normalize_governance_id(args.risk_id,'RISK') if args.risk_id else None
    if rid:
        p=_path(upgrade,rid)
        if not p.is_file():raise CliError(f'risk record not found: {rid}')
        history=_load(p,upgrade);previous=history[-1];snapshot={"path":relative_posix(p,root),**record_snapshot(p)};revision=len(history)+1
    else: revision=1
    def field(name,default=''):
        value=getattr(args,name)
        return value if value is not None else previous.get(name,default) if previous else default
    title,description=field('title'),field('description')
    if not title or not description:raise CliError('new risk requires --title and --description')
    status=field('status','open') or 'open'; recorded_at=now_iso()
    if previous and not (args.transition_reason or '').strip():raise CliError('risk state revision requires --transition-reason')
    semantic={"recorded_at":recorded_at,"title":title,"description":description,"likelihood":field('likelihood','unknown') or 'unknown',"impact":field('impact','unknown') or 'unknown',"scope":field('scope'),"status":status,"mitigation":field('mitigation'),"evidence":args.evidence if args.evidence is not None else previous.get('evidence',[]) if previous else [],"acceptance_reason":field('acceptance_reason'),"resolution":field('resolution'),"transition_reason":args.transition_reason or ''}
    if rid is None: rid=governance_record_id('RISK',{**semantic,'seed':title})
    record=validate_risk_record({"schema_version":RISK_SCHEMA_VERSION,"kind":"risk","risk_id":rid,"revision":revision,**semantic,"authority":"user","approval_basis":"exact-plan-id"}, allow_unsealed=True)
    entry={"action":"append","target":relative_posix(_path(upgrade,rid),root),"record":record}
    if snapshot:entry['history_snapshot']=snapshot
    plan=build_review_plan(root,'risk',[entry]);result.plan_id=plan['plan_id'];result.result={"plan":plan,"risk_id":rid,"revision":revision};write_plan_output(root,args.plan_out,plan,result)

def risk_apply(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True);plan=load_plan(Path(args.plan).resolve());entries=validate_apply_plan(root,plan,args.approve,'risk')
    if len(entries)!=1:raise CliError('risk plan must contain exactly one entry')
    e=entries[0]
    if e.get('action')!='append' or not isinstance(e.get('record'),dict):raise CliError('risk plan entry is invalid')
    proposal=validate_risk_record(e['record'], allow_unsealed=True);p=_path(upgrade,proposal['risk_id'])
    if e.get('target')!=relative_posix(p,root):raise CliError('risk plan target does not match risk_id')
    if proposal['revision']==1:
        if p.exists() or p.is_symlink():raise CliError(f'risk history already exists: {proposal["risk_id"]}')
        existing=[];source=None
    else:
        snap=e.get('history_snapshot')
        if not isinstance(snap,dict):raise CliError('risk plan is missing history snapshot')
        assert_file_snapshot_fresh(p,snap,message=f"risk history changed or disappeared: {proposal['risk_id']}")
        existing=_load(p,upgrade);source_text='\n'.join(json.dumps(x,ensure_ascii=False,sort_keys=True) for x in existing)+'\n';source=None
        if proposal['revision']!=len(existing)+1:raise CliError('risk revision does not follow current history')
    previous_digest=governance_record_digest(existing[-1]) if existing else None
    r=validate_risk_record(seal_governance_record(proposal,plan['plan_id'],previous_record_digest=previous_digest))
    validate_risk_history([*existing,r])
    activate_workspace_modules(root,result,{'review'}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write);ensure_workspace_structure(upgrade,result,args.dry_run,{'review'},writer=atomic_write)
    old=p.read_text(encoding='utf-8') if p.exists() else ''
    text=old+json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n'
    from core.workspace import read_text_file
    if atomic_write(p,text,source=read_text_file(p) if p.exists() else None,dry_run=args.dry_run):result.change(relative_posix(p,root),'would_update' if args.dry_run and p.exists() else 'would_create' if args.dry_run else 'updated' if p.exists() else 'created')
    result.result={'risk':r}
    if not args.dry_run:finalize_mutation(root,result)

def risk_audit(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=verified_upgrade(root);d=upgrade/'risks';requested=normalize_governance_id(args.risk_id,'RISK') if args.risk_id else None;statuses=set(args.status or []);rendered=[];counts={}
    if d.exists():
        if d.is_symlink() or not d.is_dir():raise CliError('risk directory is unsafe')
        for p in sorted(d.glob('RISK-*.jsonl')):
            h=_load(p,upgrade);latest=h[-1]
            if p.name!=f"{latest['risk_id']}.jsonl":raise CliError(f'risk filename does not match record id: {p.name}')
            counts[latest['status']]=counts.get(latest['status'],0)+1
            if requested and latest['risk_id']!=requested:continue
            if statuses and latest['status'] not in statuses:continue
            rendered.append({**latest,'history_length':len(h)})
    if requested and not rendered:raise CliError(f'risk record not found for requested filter: {requested}')
    result.result={'count':len(rendered),'counts_by_status':counts,'risks':rendered}
