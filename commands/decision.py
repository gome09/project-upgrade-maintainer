"""Immutable Decision governance commands."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from core.audit import Result
from core.constants import DECISION_SCHEMA_VERSION
from core.errors import CliError
from core.paths import relative_posix,require_within,verified_upgrade
from core.workspace import activate_workspace_modules,ensure_workspace_structure,preflight_mutation_workspace
from core.plans import assert_file_snapshot_fresh,validate_apply_plan,write_plan_output
from core.schemas import normalize_governance_id,seal_governance_record,validate_decision_record
from ._governance_records import governance_record_id,read_json_object,record_snapshot
from ._runtime import atomic_write,build_review_plan,load_plan,now_iso, finalize_mutation

def _path(upgrade:Path,id:str)->Path:return require_within(upgrade/'decisions'/f'{id}.json',upgrade,'decision record')

def _records(upgrade:Path):
    d=upgrade/'decisions'
    if not d.exists():return []
    if d.is_symlink() or not d.is_dir():raise CliError('decision directory is unsafe')
    out=[]
    for p in sorted(d.glob('DEC-*.json')):
        r=validate_decision_record(read_json_object(p,upgrade,'decision record'))
        if p.name!=f"{r['decision_id']}.json":raise CliError(f'decision filename does not match record id: {p.name}')
        out.append(r)
    ids={r['decision_id'] for r in out}
    for r in out:
        if r['supersedes'] and r['supersedes'] not in ids:raise CliError(f"decision supersedes a missing record: {r['supersedes']}")
    return out

def decision_review(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True); previous=None; snap=None
    supersedes=normalize_governance_id(args.supersedes,'DEC') if args.supersedes else None
    if supersedes:
        pp=_path(upgrade,supersedes)
        if not pp.is_file():raise CliError(f'decision record not found: {supersedes}')
        previous=validate_decision_record(read_json_object(pp,upgrade,'decision record'))
        if any(r['supersedes']==supersedes for r in _records(upgrade)):raise CliError(f'decision {supersedes} is already superseded')
        snap={"path":relative_posix(pp,root),**record_snapshot(pp)}
    def field(name,default=''):
        value=getattr(args,name)
        return value if value is not None else previous.get(name,default) if previous else default
    title,decision=field('title'),field('decision')
    if not title or not decision:raise CliError('new decision requires --title and --decision')
    semantic={"recorded_at":now_iso(),"title":title,"decision":decision,"rationale":field('rationale'),"scope":field('scope'),"evidence":args.evidence if args.evidence is not None else previous.get('evidence',[]) if previous else [],"supersedes":supersedes}
    did=governance_record_id('DEC',semantic)
    record=validate_decision_record({"schema_version":DECISION_SCHEMA_VERSION,"kind":"decision","decision_id":did,**semantic,"authority":"user","approval_basis":"exact-plan-id"}, allow_unsealed=True)
    target=_path(upgrade,did)
    if target.exists() or target.is_symlink():raise CliError(f'decision record already exists: {relative_posix(target,root)}')
    entry={"action":"record","target":relative_posix(target,root),"record":record}
    if snap:entry['supersedes_snapshot']=snap
    plan=build_review_plan(root,'decision',[entry]);result.plan_id=plan['plan_id'];result.result={"plan":plan,"decision_id":did};write_plan_output(root,args.plan_out,plan,result)

def decision_apply(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True);plan=load_plan(Path(args.plan).resolve());entries=validate_apply_plan(root,plan,args.approve,'decision')
    if len(entries)!=1:raise CliError('decision plan must contain exactly one entry')
    e=entries[0]
    if e.get('action')!='record' or not isinstance(e.get('record'),dict):raise CliError('decision plan entry is invalid')
    proposal=validate_decision_record(e['record'], allow_unsealed=True);target=_path(upgrade,proposal['decision_id'])
    if e.get('target')!=relative_posix(target,root):raise CliError('decision plan target does not match decision_id')
    if proposal['supersedes']:
        snap=e.get('supersedes_snapshot');prev=_path(upgrade,proposal['supersedes'])
        if not isinstance(snap,dict):raise CliError('decision plan is missing superseded-record snapshot')
        assert_file_snapshot_fresh(prev,snap,message=f"superseded decision changed or disappeared: {proposal['supersedes']}")
        if any(x['supersedes']==proposal['supersedes'] for x in _records(upgrade)):raise CliError(f"decision {proposal['supersedes']} is already superseded")
    if target.exists() or target.is_symlink():raise CliError(f'decision record already exists: {relative_posix(target,root)}')
    r=validate_decision_record(seal_governance_record(proposal,plan['plan_id']))
    activate_workspace_modules(root,result,{'review'}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write);ensure_workspace_structure(upgrade,result,args.dry_run,{'review'},writer=atomic_write)
    if atomic_write(target,json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n',dry_run=args.dry_run):result.change(relative_posix(target,root),'would_create' if args.dry_run else 'created')
    result.result={'decision':r}
    if not args.dry_run:finalize_mutation(root,result)

def decision_audit(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=verified_upgrade(root); records=_records(upgrade); superseded={r['supersedes'] for r in records if r['supersedes']}; requested=normalize_governance_id(args.decision_id,'DEC') if args.decision_id else None
    rendered=[{**r,'current':r['decision_id'] not in superseded} for r in records if not requested or r['decision_id']==requested]
    if requested and not rendered:raise CliError(f'decision record not found for requested filter: {requested}')
    result.result={'count':len(rendered),'current_count':sum(1 for r in rendered if r['current']),'decisions':rendered}
