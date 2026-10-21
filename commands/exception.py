"""Exception/Waiver review, apply, and audit commands."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from typing import Any
from core.audit import Result
from core.constants import EXCEPTION_SCHEMA_VERSION,EXCEPTION_STATUSES,EXCEPTION_TYPES
from core.errors import CliError
from core.paths import relative_posix,require_within,verified_upgrade
from core.workspace import activate_workspace_modules,preflight_mutation_workspace
from core.plans import assert_file_snapshot_fresh,validate_apply_plan,write_plan_output
from core.schemas import normalize_governance_id,seal_governance_record,validate_decision_record,validate_exception_record,validate_risk_history
from ._delivery_state import exception_expired,load_exception_records
from ._governance_records import governance_record_id,read_json_object,read_jsonl_objects,record_snapshot,is_approved_replay
from ._runtime import atomic_write,build_review_plan,load_plan,now_iso, finalize_mutation

def _path(upgrade:Path,id:str)->Path:return require_within(upgrade/'exceptions'/f'{id}.json',upgrade,'exception record')
def _existing_child(records,parent):return next((r['exception_id'] for r in records if r['supersedes']==parent),None)
def _linked_snapshot(root,upgrade,prefix,id):
    if prefix=='DEC':
        p=require_within(upgrade/'decisions'/f'{id}.json',upgrade,'linked decision');r=validate_decision_record(read_json_object(p,upgrade,'linked decision'))
        if r['decision_id']!=id:raise CliError('linked decision id does not match its filename')
        kind='decision'
    else:
        p=require_within(upgrade/'risks'/f'{id}.jsonl',upgrade,'linked risk');h=validate_risk_history(read_jsonl_objects(p,upgrade,'linked risk'))
        if h[-1]['risk_id']!=id:raise CliError('linked risk id does not match its filename')
        kind='risk'
    return {'kind':kind,'id':id,'path':relative_posix(p,root),**record_snapshot(p)}
def _same_state(a,b):return all(a.get(k)==b.get(k) for k in ('exception_type','status','title','scope','requirement','justification','impact','compensating_controls','evidence','linked_decisions','linked_risks','expires_at','resolution'))

def exception_review(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True);records=load_exception_records(upgrade);previous=None;previous_snapshot=None
    supersedes=normalize_governance_id(args.supersedes,'EXC') if args.supersedes else None
    if supersedes:
        p=_path(upgrade,supersedes)
        if not p.is_file():raise CliError(f'exception record not found: {supersedes}')
        previous=validate_exception_record(read_json_object(p,upgrade,'exception record'))
        child=_existing_child(records,supersedes)
        if child:raise CliError(f'exception {supersedes} is already superseded by {child}')
        previous_snapshot={'path':relative_posix(p,root),**record_snapshot(p)}
    def field(name,default=''):
        v=getattr(args,name)
        return v if v is not None else previous.get(name,default) if previous else default
    title,scope,requirement,justification=field('title'),field('scope'),field('requirement'),field('justification')
    if not all((title,scope,requirement,justification)):raise CliError('new exception/waiver requires --title, --scope, --requirement, and --justification')
    controls=args.compensating_control if args.compensating_control is not None else previous.get('compensating_controls',[]) if previous else []
    evidence=args.evidence if args.evidence is not None else previous.get('evidence',[]) if previous else []
    ldec=args.linked_decision if args.linked_decision is not None else previous.get('linked_decisions',[]) if previous else []
    lrisk=args.linked_risk if args.linked_risk is not None else previous.get('linked_risks',[]) if previous else []
    ldec=list(dict.fromkeys(normalize_governance_id(x,'DEC') for x in ldec));lrisk=list(dict.fromkeys(normalize_governance_id(x,'RISK') for x in lrisk))
    semantic={'recorded_at':now_iso(),'exception_type':field('exception_type','exception') or 'exception','status':field('status','active') or 'active','title':title,'scope':scope,'requirement':requirement,'justification':justification,'impact':field('impact'),'compensating_controls':controls,'evidence':evidence,'linked_decisions':ldec,'linked_risks':lrisk,'expires_at':field('expires_at',None),'resolution':field('resolution'),'supersedes':supersedes}
    eid=governance_record_id('EXC',semantic);record=validate_exception_record({'schema_version':EXCEPTION_SCHEMA_VERSION,'kind':'exception','exception_id':eid,**semantic,'authority':'user','approval_basis':'exact-plan-id'}, allow_unsealed=True)
    if previous and _same_state(previous,record):raise CliError('exception review does not change governed state')
    target=_path(upgrade,eid)
    if target.exists() or target.is_symlink():raise CliError(f'exception record already exists: {relative_posix(target,root)}')
    linked=[_linked_snapshot(root,upgrade,'DEC',x) for x in ldec]+[_linked_snapshot(root,upgrade,'RISK',x) for x in lrisk]
    entry={'action':'record','target':relative_posix(target,root),'record':record,'linked_snapshots':linked}
    if previous_snapshot:entry['supersedes_snapshot']=previous_snapshot
    plan=build_review_plan(root,'exception',[entry]);result.plan_id=plan['plan_id'];result.result={'plan':plan,'exception_id':eid,'exception_type':record['exception_type']};write_plan_output(root,args.plan_out,plan,result)

def exception_apply(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=preflight_mutation_workspace(root,require_config=True);plan=load_plan(Path(args.plan).resolve());entries=validate_apply_plan(root,plan,args.approve,'exception')
    if len(entries)!=1:raise CliError('exception plan must contain exactly one entry')
    e=entries[0]
    if e.get('action')!='record' or not isinstance(e.get('record'),dict):raise CliError('exception plan entry is invalid')
    proposal=validate_exception_record(e['record'], allow_unsealed=True);target=_path(upgrade,proposal['exception_id'])
    if e.get('target')!=relative_posix(target,root):raise CliError('exception plan target does not match exception_id')
    if target.is_file() and not target.is_symlink():
        existing=validate_exception_record(read_json_object(target,upgrade,'exception record'))
        if is_approved_replay(existing,proposal,plan['plan_id']):
            result.result={'exception':existing,'replayed':True};return
    records=load_exception_records(upgrade);snap=e.get('supersedes_snapshot')
    if proposal['supersedes']:
        if not isinstance(snap,dict):raise CliError('exception plan is missing superseded-record snapshot')
        p=_path(upgrade,proposal['supersedes']);assert_file_snapshot_fresh(p,snap,message=f"superseded exception changed or disappeared: {proposal['supersedes']}")
        if _existing_child(records,proposal['supersedes']):raise CliError(f"exception {proposal['supersedes']} is already superseded")
    elif snap is not None:raise CliError('exception plan has an unexpected superseded-record snapshot')
    linked=e.get('linked_snapshots')
    if not isinstance(linked,list):raise CliError('exception plan linked snapshots are invalid')
    expected={('decision',x) for x in proposal['linked_decisions']}|{('risk',x) for x in proposal['linked_risks']};seen=set()
    for x in linked:
        if not isinstance(x,dict):raise CliError('exception plan linked snapshot is invalid')
        kind,id=x.get('kind'),x.get('id');seen.add((kind,id))
        if kind=='decision':p=require_within(upgrade/'decisions'/f'{id}.json',upgrade,'linked decision');rr=validate_decision_record(read_json_object(p,upgrade,'linked decision'));ok=rr['decision_id']==id
        elif kind=='risk':p=require_within(upgrade/'risks'/f'{id}.jsonl',upgrade,'linked risk');hh=validate_risk_history(read_jsonl_objects(p,upgrade,'linked risk'));ok=hh[-1]['risk_id']==id
        else:raise CliError('exception plan linked snapshot kind is invalid')
        if not ok:raise CliError(f'linked {kind} id does not match its filename')
        if x.get('path')!=relative_posix(p,root):raise CliError('exception plan linked snapshot path is invalid')
        assert_file_snapshot_fresh(p,x,message=f'linked {kind} changed or disappeared: {id}')
    if seen!=expected:raise CliError('exception plan linked snapshot set does not match record links')
    if target.exists() or target.is_symlink():raise CliError(f'exception record already exists: {relative_posix(target,root)}')
    r=validate_exception_record(seal_governance_record(proposal,plan['plan_id']))
    activate_workspace_modules(root,result,{'review'}, dry_run=args.dry_run, clock=now_iso, writer=atomic_write)
    if atomic_write(target,json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n',dry_run=args.dry_run):result.change(relative_posix(target,root),'would_create' if args.dry_run else 'created')
    result.result={'exception':r}
    if not args.dry_run:finalize_mutation(root,result)

def exception_audit(root:Path,args:argparse.Namespace,result:Result)->None:
    upgrade=verified_upgrade(root);requested=normalize_governance_id(args.exception_id,'EXC') if args.exception_id else None;statuses=set(args.status or []);types=set(args.exception_type or []);records=load_exception_records(upgrade);sup={r['supersedes'] for r in records if r['supersedes']};at=now_iso();rendered=[];counts={s:0 for s in EXCEPTION_STATUSES};type_counts={t:0 for t in EXCEPTION_TYPES}
    for r in records:
        current=r['exception_id'] not in sup;expired=current and r['status']=='active' and exception_expired(r,at)
        if current:counts[r['status']]+=1;type_counts[r['exception_type']]+=1
        item={**r,'current':current,'expired':expired}
        if requested and r['exception_id']!=requested:continue
        if statuses and r['status'] not in statuses:continue
        if types and r['exception_type'] not in types:continue
        rendered.append(item)
    if requested and not rendered:raise CliError(f'exception record not found for requested filter: {requested}')
    result.result={'count':len(rendered),'current_count':sum(x['current'] for x in rendered),'active_unexpired_count':sum(x['current'] and x['status']=='active' and not x['expired'] for x in rendered),'expired_count':sum(x['expired'] for x in rendered),'counts_by_current_status':counts,'counts_by_current_type':type_counts,'exceptions':rendered}
