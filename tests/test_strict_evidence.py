"""Opt-in evidence controls and real local command execution."""
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cli.main import run
from core.schemas import governance_record_digest


class StrictEvidenceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.at = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
        self.call('initialize', '--profile', 'standard')
        (self.root / 'app.txt').write_text('release', encoding='utf-8')

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(['--root', str(self.root), '--format', 'json', *args], clock=lambda: self.at.isoformat())
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def policy(self, *extra):
        review = self.call('delivery-policy-review', '--readiness-check', 'suite',
                          '--phase-check', '1=suite', '--max-age-seconds', '60', '--require-execution',
                          '--plan-out', '.upgrade/policy-plan.json', *extra)
        return self.call('delivery-policy-apply', '--plan', str(self.root / '.upgrade/policy-plan.json'),
                         '--approve', review['plan_id'])

    def execution(self, code='print("passed")', *extra):
        return self.call('validation-record', '--check', 'suite', '--subject-path', 'app.txt',
                         *extra, '--execute', sys.executable, '-c', code)

    def delivery(self, *extra, expected=0):
        return self.call('delivery-review', '--gate', 'readiness', '--target', 'release',
                         '--subject-path', 'app.txt', *extra, expected=expected)

    def test_manual_assertions_fail_execution_policy_and_real_execution_passes(self):
        self.policy()
        self.call('validation-record', '--check', 'suite', '--status', 'passed', '--subject-path', 'app.txt')
        self.assertTrue(any('requires local execution' in b for b in self.delivery()['result']['blockers']))
        record = self.execution()['result']['record']
        self.assertEqual('passed', record['status'])
        self.assertEqual(0, record['exit_code'])
        self.assertEqual(64, len(record['execution']['stdout_sha256']))
        self.assertTrue(self.delivery()['result']['eligible'])

    def test_evidence_expires_between_delivery_review_and_apply(self):
        self.policy()
        self.execution()
        review = self.delivery('--plan-out', '.upgrade/delivery-plan.json')
        self.at += dt.timedelta(seconds=61)
        self.assertTrue(any('expired' in b for b in self.delivery()['result']['blockers']))
        self.call('delivery-apply', '--plan', str(self.root / '.upgrade/delivery-plan.json'),
                  '--approve', review['plan_id'], expected=3)

    def test_phase_gate_blocks_then_records_bound_exit_evidence(self):
        self.policy()
        self.call('phase-update', '--phase', '1', '--current-task', 'work')
        args = ('phase-update', '--phase', '2', '--current-task', 'next', '--complete-previous',
                '--completion-note', 'accepted', '--subject-path', 'app.txt')
        failed = self.call(*args, expected=2)
        self.assertEqual([], failed['changed'])
        self.execution()
        accepted = self.call(*args)
        self.assertEqual(['suite'], accepted['result']['phase_exit']['required_checks'])
        self.assertIn('Exit Evidence', (self.root / '.upgrade/stages/phase-1.md').read_text(encoding='utf-8'))

    def test_mixed_history_warns_only_for_exact_validation_context(self):
        (self.root / 'other.txt').write_text('other subject', encoding='utf-8')

        def record(check, status, subject=None, scope='release', environment='local'):
            binding = ('--subject-path', subject) if subject else ()
            return self.call('validation-record', '--check', check, '--status', status,
                             '--scope', scope, '--environment', environment, *binding)['result']['record']

        context = ('--scope', 'release', '--environment', 'local', '--require-check', 'suite')
        self.assertIsNone(record('suite', 'passed')['subject_digest'])
        blocked = self.delivery(*context)['result']
        self.assertFalse(blocked['eligible'])
        self.assertIn("required validation 'suite' has no evidence for subject/scope/environment", blocked['blockers'])

        self.assertIsNotNone(record('suite', 'passed', 'app.txt')['subject_digest'])
        record('z-matching', 'failed', 'app.txt')
        record('a-matching', 'failed', 'app.txt')
        record('a-matching', 'passed', 'app.txt', scope='other')
        record('wrong-subject', 'failed', 'other.txt')
        record('wrong-scope', 'failed', 'app.txt', scope='other')
        record('wrong-environment', 'failed', 'app.txt', environment='other')
        record('unbound-only', 'failed')
        record('passing', 'passed', 'app.txt')
        result = self.delivery(*context)['result']
        self.assertTrue(result['eligible'])
        self.assertEqual([
            "non-required validation 'a-matching' latest status is failed",
            "non-required validation 'z-matching' latest status is failed",
        ], [warning for warning in result['warnings'] if warning.startswith('non-required validation')])

        path = self.root / '.upgrade/validation/records.jsonl'
        history = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        history[0]['note'] = 'fixture tampering'
        history[0]['record_digest'] = governance_record_digest(history[0])
        path.write_text('\n'.join(json.dumps(previous) for previous in history) + '\n', encoding='utf-8')
        tampered = path.read_bytes()
        rejected = self.delivery(*context, expected=2)
        self.assertIn('validation evidence integrity chain', rejected['errors'][0])
        self.assertEqual(tampered, path.read_bytes())

    def test_phase_exit_with_configured_checks_and_mixed_validation_history(self):
        self.policy()
        self.call('phase-update', '--phase', '1', '--current-task', 'work')
        self.call('validation-record', '--check', 'suite', '--status', 'passed',
                  '--scope', 'release', '--environment', 'local')
        args = ('phase-update', '--phase', '2', '--current-task', 'next', '--complete-previous',
                '--completion-note', 'accepted', '--subject-path', 'app.txt',
                '--scope', 'release', '--environment', 'local')
        stages = self.root / '.upgrade/stages'
        phase_one = (stages / 'phase-1.md').read_bytes()
        failed = self.call(*args, expected=2)
        self.assertIn("required validation 'suite' has no evidence", failed['errors'][0])
        self.assertEqual([], failed['changed'])
        self.assertEqual(phase_one, (stages / 'phase-1.md').read_bytes())
        self.assertFalse((stages / 'phase-2.md').exists())

        self.execution('print("passed")', '--scope', 'release', '--environment', 'local')
        accepted = self.call(*args)
        self.assertEqual(['suite'], accepted['result']['phase_exit']['required_checks'])
        self.assertIn('Exit Evidence', (stages / 'phase-1.md').read_text(encoding='utf-8'))
        self.assertTrue((stages / 'phase-2.md').is_file())

    def test_failed_command_and_mutated_subject_never_pass(self):
        self.assertEqual('failed', self.execution('raise SystemExit(7)')['result']['record']['status'])
        changed = self.execution('from pathlib import Path; Path("app.txt").write_text("changed")')['result']['record']
        self.assertEqual('failed', changed['status'])
        self.assertFalse(changed['execution']['subject_unchanged'])

    def test_execution_retry_does_not_run_twice(self):
        code = 'from pathlib import Path; p=Path(".upgrade/count"); p.write_text(str(int(p.read_text())+1) if p.exists() else "1")'
        self.execution(code, '--request-id', 'run-1')
        retry = self.execution(code, '--request-id', 'run-1')
        self.assertTrue(retry['result']['replayed'])
        self.assertEqual('1', (self.root / '.upgrade/count').read_text())

    def test_timeout_records_failure(self):
        record = self.execution('import time; time.sleep(5)', '--timeout-seconds', '1')['result']['record']
        self.assertEqual('failed', record['status'])
        self.assertIsNone(record['exit_code'])
        self.assertTrue(record['execution']['timed_out'])

    def test_phase_gate_honors_global_blockers(self):
        self.policy()
        self.call('phase-update', '--phase', '1', '--current-task', 'work')
        self.execution()
        state = self.root / '.upgrade/STATE.md'
        from core.workspace import replace_section
        state.write_text(replace_section(state.read_text(encoding='utf-8'), 'current_blockers', 'Production approval pending'), encoding='utf-8')
        blocked = self.call('phase-update', '--phase', '2', '--current-task', 'next',
                            '--complete-previous', '--completion-note', 'accepted',
                            '--subject-path', 'app.txt', expected=2)
        self.assertEqual([], blocked['changed'])

    def test_missing_mode_or_dry_run_execution_rejected_without_write(self):
        result = self.call('validation-record', '--check', 'suite', expected=2)
        self.assertEqual([], result['changed'])
        self.call('validation-record', '--check', 'suite', '--dry-run', '--execute', sys.executable,
                  '-c', 'raise SystemExit(0)', expected=2)

    def test_old_project_still_accepts_manual_validation_and_phase_completion(self):
        self.call('validation-record', '--check', 'suite', '--status', 'passed', '--subject-path', 'app.txt')
        self.assertTrue(self.delivery('--require-check', 'suite')['result']['eligible'])
        self.call('phase-update', '--phase', '1', '--current-task', 'work')
        self.call('phase-update', '--phase', '2', '--current-task', 'next', '--complete-previous', '--completion-note', 'accepted')
