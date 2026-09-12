"""Transaction recovery audit, review, and apply commands."""
from __future__ import annotations
import argparse,re
from pathlib import Path
from core.audit import Result
from core.errors import CliError,StalePlanError
from core.plans import validate_apply_plan,write_plan_output
from core.transactions import Transaction,unresolved_transactions
from core.workspace import sha256_file
from ._runtime import build_review_plan,load_plan, finalize_mutation

def _id(value:str)->str:
    v=value.strip().upper()
    if not re.fullmatch(r'TXN-[0-9A-F]{12}',v):raise CliError(f'invalid transaction id: {value}')
    return v

def _recoverable(tx:Transaction)->None:
    state=tx.data.get('state')
    if state not in {'active','failed','rolling_back'}:raise CliError(f'transaction is not recoverable: {state}')
    # Refuse to recover an apparently live local transaction.
    if state=='active':
        import os,socket
        if tx.data.get('hostname')==socket.gethostname() and isinstance(tx.data.get('pid'),int):
            try:os.kill(tx.data['pid'],0)
            except ProcessLookupError:pass
            except PermissionError:raise CliError('transaction owner process is still alive; recovery refused')
            else:raise CliError('transaction owner process is still alive; recovery refused')

def recovery_audit(root:Path,args:argparse.Namespace,result:Result)->None:
    items=unresolved_transactions(root)
    rendered=[]
    for item in items:
        tx=Transaction.load(root,item['transaction_id']);analysis=tx.analysis();rendered.append({**item,'operation_analysis':analysis,'recoverable':not any(x['state']=='conflict' for x in analysis)})
    result.result={'count':len(rendered),'transactions':rendered}
    if rendered:result.status='warning';result.warnings.append('unresolved transaction(s) require recovery review')

def recovery_review(root:Path,args:argparse.Namespace,result:Result)->None:
    tid=_id(args.transaction_id);tx=Transaction.load(root,tid);_recoverable(tx);analysis=tx.analysis()
    conflicts=[x for x in analysis if x['state']=='conflict']
    entry={'action':'rollback','transaction_id':tid,'journal_sha256':tx.journal_sha256(),'operation_analysis':analysis,'conflicts':conflicts,'confirmation_required':True}
    plan=build_review_plan(root,'recovery',[entry]);result.plan_id=plan['plan_id'];result.result={'plan':plan,'transaction_id':tid,'recoverable':not conflicts,'conflicts':conflicts};
    if conflicts:result.status='warning';result.warnings.append('recovery has filesystem conflicts and cannot be automatically applied')
    write_plan_output(root,args.plan_out,plan,result)

def recovery_apply(root:Path,args:argparse.Namespace,result:Result)->None:
    plan=load_plan(Path(args.plan).resolve());entries=validate_apply_plan(root,plan,args.approve,'recovery')
    if len(entries)!=1 or entries[0].get('action')!='rollback':raise CliError('recovery plan is invalid')
    e=entries[0];tx=Transaction.load(root,_id(e.get('transaction_id','')));_recoverable(tx)
    if sha256_file(tx.journal_path)!=e.get('journal_sha256'):raise StalePlanError('transaction journal changed since recovery review; regenerate the recovery plan')
    analysis=tx.analysis()
    if analysis!=e.get('operation_analysis'):raise StalePlanError('transaction filesystem state changed since recovery review; regenerate the recovery plan')
    if any(x['state']=='conflict' for x in analysis):raise CliError('transaction recovery conflict; automatic rollback refused')
    if args.dry_run:result.result={'transaction_id':tx.transaction_id,'would_rollback':analysis};return
    tx.rollback();result.change(f'.upgrade/transactions/{tx.transaction_id}.json','rolled_back');result.result={'transaction_id':tx.transaction_id,'state':'rolled_back'};finalize_mutation(root,result)
