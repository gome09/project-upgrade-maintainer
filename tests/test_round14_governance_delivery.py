from __future__ import annotations
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from cli.main import run
from core.errors import CliError
from core.transactions import Transaction, unresolved_transactions
from core.workspace import atomic_write

class Round14GovernanceDeliveryCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()
    def cli(self,*args,expected=0):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=run(['--root',str(self.root),'--format','json',*args])
        self.assertEqual(expected,code,err.getvalue()+out.getvalue());return json.loads(out.getvalue())
    def init(self):
        self.cli('initialize','--profile','lean');(self.root/'subject.txt').write_text('release-subject\n',encoding='utf-8')
    def apply(self,review,plan,command,expected=0):return self.cli(command,'--plan',str(self.root/plan),'--approve',review['plan_id'],expected=expected)
    def validate(self,status='passed'):
        self.cli('validation-record','--check','release-suite','--status',status,'--exit-code','0' if status=='passed' else '1','--subject-path','subject.txt')
    def decision(self,title='Direction'):
        r=self.cli('decision-review','--title',title,'--decision','Ship governed release','--plan-out','decision.json');a=self.apply(r,'decision.json','decision-apply');return a['result']['decision']['decision_id']
    def risk(self,status='open',name='risk.json'):
        args=['risk-review','--title','Release risk','--description','Known bounded risk','--status',status]
        if status=='accepted':args+=['--acceptance-reason','User explicitly accepts bounded exposure']
        args+=['--plan-out',name];r=self.cli(*args);a=self.apply(r,name,'risk-apply');return a['result']['risk']['risk_id']
    def exception(self,*extra,plan='exception.json'):
        r=self.cli('exception-review','--exception-type','waiver','--title','Compatibility waiver','--scope','release-v1','--requirement','Legacy check','--justification','Temporary compatibility',*extra,'--plan-out',plan);return r,self.apply(r,plan,'exception-apply')

    def test_decision_is_immutable_and_supersedes_instead_of_overwrite(self):
        self.init(); first=self.decision();
        r=self.cli('decision-review','--supersedes',first,'--decision','Ship with revised boundary','--plan-out','decision2.json');a=self.apply(r,'decision2.json','decision-apply');second=a['result']['decision']['decision_id']
        self.assertNotEqual(first,second);audit=self.cli('decision-audit');self.assertEqual(2,audit['result']['count']);self.assertEqual(1,audit['result']['current_count']);self.assertEqual('user',a['result']['decision']['authority'])

    def test_risk_transition_requires_reason_and_acceptance_is_explicit(self):
        self.init();rid=self.risk('open')
        bad=self.cli('risk-review','--risk-id',rid,'--status','accepted','--acceptance-reason','accept',expected=2);self.assertTrue(any('transition' in x for x in bad['errors']))
        r=self.cli('risk-review','--risk-id',rid,'--status','accepted','--acceptance-reason','User accepts','--transition-reason','Release decision','--plan-out','risk2.json');a=self.apply(r,'risk2.json','risk-apply');self.assertEqual(2,a['result']['risk']['revision']);self.assertEqual('accepted',a['result']['risk']['status'])
        audit=self.cli('risk-audit','--risk-id',rid);self.assertEqual(2,audit['result']['risks'][0]['history_length'])

    def test_linked_risk_revision_stales_exception_plan(self):
        self.init();rid=self.risk('open')
        review=self.cli('exception-review','--title','Risk waiver','--scope','release','--requirement','Close risk','--justification','Governed exception','--linked-risk',rid,'--plan-out','e.json')
        rr=self.cli('risk-review','--risk-id',rid,'--status','accepted','--acceptance-reason','User accepts','--transition-reason','Approval','--plan-out','r2.json');self.apply(rr,'r2.json','risk-apply')
        stale=self.apply(review,'e.json','exception-apply',expected=3);self.assertTrue(any('linked risk changed' in x for x in stale['errors']))

    def test_transaction_crash_recovery_restores_preimage_and_blocks_intervening_mutation(self):
        self.init();target=self.root/'governed.txt';target.write_text('before\n',encoding='utf-8')
        tx=Transaction.start(self.root,'fault-injection');atomic_write(target,'after\n');tx.fail('simulated crash')
        self.assertEqual('after\n',target.read_text());self.assertEqual(1,len(unresolved_transactions(self.root)))
        blocked=self.cli('validation-record','--check','blocked','--status','passed','--exit-code','0',expected=2);self.assertTrue(any('unresolved transaction' in x for x in blocked['errors']))
        review=self.cli('recovery-review','--transaction-id',tx.transaction_id,'--plan-out','recovery.json');self.assertTrue(review['result']['recoverable']);self.apply(review,'recovery.json','recovery-apply');self.assertEqual('before\n',target.read_text());self.assertEqual([],unresolved_transactions(self.root))

    def test_transaction_recovery_refuses_third_party_conflict(self):
        self.init();target=self.root/'governed.txt';target.write_text('before\n');tx=Transaction.start(self.root,'fault-injection');atomic_write(target,'after\n');tx.fail('crash');target.write_text('third-party\n')
        review=self.cli('recovery-review','--transaction-id',tx.transaction_id,'--plan-out','recovery.json');self.assertFalse(review['result']['recoverable']);rejected=self.apply(review,'recovery.json','recovery-apply',expected=2);self.assertTrue(any('conflict' in x for x in rejected['errors']));self.assertEqual('third-party\n',target.read_text())

    def test_delivery_without_validation_is_blocked_even_with_exact_plan(self):
        self.init();review=self.cli('delivery-review','--gate','readiness','--target','v1','--require-check','release-suite','--subject-path','subject.txt','--plan-out','delivery.json');self.assertFalse(review['result']['eligible']);self.assertIn('no validation evidence is recorded',review['result']['blockers']);rejected=self.apply(review,'delivery.json','delivery-apply',expected=2);self.assertTrue(any('remains blocked' in x for x in rejected['errors']))

    def test_open_risk_blocks_but_accepted_risk_is_warning_and_can_be_accepted(self):
        self.init();self.validate();rid=self.risk('open');blocked=self.cli('delivery-review','--gate','readiness','--target','v1','--require-check','release-suite','--subject-path','subject.txt');self.assertTrue(any(rid in x for x in blocked['result']['blockers']))
        rr=self.cli('risk-review','--risk-id',rid,'--status','accepted','--acceptance-reason','User accepts','--transition-reason','Release approval','--plan-out','ra.json');self.apply(rr,'ra.json','risk-apply')
        ready=self.cli('delivery-review','--gate','readiness','--target','v1','--require-check','release-suite','--subject-path','subject.txt','--plan-out','delivery.json');self.assertTrue(ready['result']['eligible']);self.assertTrue(any(rid in x and 'accepted by the user' in x for x in ready['result']['warnings']));accepted=self.apply(ready,'delivery.json','delivery-apply');self.assertEqual('accepted',accepted['result']['delivery']['outcome']);self.assertEqual('user',accepted['result']['delivery']['authority'])

    def test_expired_exception_blocks_while_unexpired_exception_is_warning(self):
        self.init();self.validate();self.exception('--expires-at','2000-01-01T00:00:00+00:00',plan='old.json');blocked=self.cli('delivery-review','--gate','readiness','--target','v1','--require-check','release-suite','--subject-path','subject.txt');self.assertTrue(any('expired' in x for x in blocked['result']['blockers']))
        # Resolve the expired waiver then create an unexpired one.
        audit=self.cli('exception-audit');old=[x for x in audit['result']['exceptions'] if x['current']][0]['exception_id'];r=self.cli('exception-review','--supersedes',old,'--status','resolved','--resolution','obsolete waiver resolved','--plan-out','resolved.json');self.apply(r,'resolved.json','exception-apply')
        self.exception('--expires-at','2099-01-01T00:00:00+00:00',plan='new.json');ready=self.cli('delivery-review','--gate','readiness','--target','v1','--require-check','release-suite','--subject-path','subject.txt');self.assertTrue(ready['result']['eligible']);self.assertTrue(any('waiver' in x for x in ready['result']['warnings']))

    def test_delivery_plan_stales_when_governance_evidence_changes(self):
        self.init();self.validate();review=self.cli('delivery-review','--gate','final-acceptance','--target','v1','--require-check','release-suite','--subject-path','subject.txt','--plan-out','delivery.json');self.decision('Late direction');stale=self.apply(review,'delivery.json','delivery-apply',expected=3);self.assertTrue(any('evidence changed' in x for x in stale['errors']))

    def test_round14_health_detects_corrupt_governance_record(self):
        self.init();self.cli('sync','--enable-module','review');bad=self.root/'.upgrade'/'exceptions'/'EXC-AAAAAAAAAAAA.json';bad.write_text('{}\n');health=self.cli('health-check');self.assertEqual('error',health['status']);self.assertTrue(any('exception record fields' in x for x in health['result']['错误项']))

if __name__=='__main__':unittest.main()
