"""Behavioral regressions for repeat calls and independent delivery scopes."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.main import run
from commands import _inventory
from core import workspace
from core.audit import Result
from core.workspace import section_value


class WorkflowEfficiencyCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.call('initialize', '--profile', 'standard')
        (self.root / 'app.txt').write_text('release\n', encoding='utf-8')

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(['--root', str(self.root), '--format', 'json', *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def validation(self, scope='A', *extra, status='passed', expected=0):
        return self.call('validation-record', '--check', 'suite', '--status', status,
                         '--scope', scope, '--subject-path', 'app.txt', *extra, expected=expected)

    def review_delivery(self):
        return self.call('delivery-review', '--gate', 'readiness', '--target', 'release',
                         '--scope', 'A', '--subject-path', 'app.txt', '--require-check', 'suite',
                         '--plan-out', '.upgrade/delivery-plan.json')

    def test_phase_refresh_preserves_omitted_next_action_and_is_noop(self):
        self.call('phase-update', '--phase', '1', '--current-task', 'task', '--next-action', 'retain')
        repeat = self.call('phase-update', '--phase', '1', '--current-task', 'task')
        self.assertEqual([], repeat['changed'])
        stage = (self.root / '.upgrade/stages/phase-1.md').read_text(encoding='utf-8')
        state = (self.root / '.upgrade/STATE.md').read_text(encoding='utf-8')
        self.assertEqual('retain', section_value(stage, 'Next Action'))
        self.assertEqual('retain', section_value(state, 'next_action'))

    def test_noop_sync_creates_no_journal(self):
        directory = self.root / '.upgrade/transactions'
        before = sorted(directory.glob('TXN-*.json'))
        for _ in range(2):
            self.assertEqual([], self.call('sync')['changed'])
        self.assertEqual(before, sorted(directory.glob('TXN-*.json')))

    def test_activation_checks_structure_once_and_repairs_active_module(self):
        for _ in range(2):
            result = Result('probe', self.root)
            with mock.patch('core.workspace.ensure_workspace_structure', wraps=workspace.ensure_workspace_structure) as ensure:
                workspace.activate_workspace_modules(self.root, result, {'review'}, dry_run=False)
            self.assertEqual(1, ensure.call_count)
        target = self.root / '.upgrade/reviews/POST_UPGRADE_REVIEW.md'
        target.unlink()
        workspace.activate_workspace_modules(self.root, Result('probe', self.root), {'review'}, dry_run=False)
        self.assertTrue(target.is_file())

    def test_unrelated_scope_validation_does_not_stale_delivery(self):
        self.validation()
        review = self.review_delivery()
        self.validation('B')
        accepted = self.call('delivery-apply', '--plan', str(self.root / '.upgrade/delivery-plan.json'),
                             '--approve', review['plan_id'])
        self.assertEqual('accepted', accepted['result']['delivery']['outcome'])

    def test_relevant_validation_change_stales_delivery(self):
        self.validation()
        review = self.review_delivery()
        self.validation(status='failed')
        self.call('delivery-apply', '--plan', str(self.root / '.upgrade/delivery-plan.json'),
                  '--approve', review['plan_id'], expected=3)

    def test_global_risk_still_stales_delivery(self):
        self.validation()
        review = self.review_delivery()
        risk = self.call('risk-review', '--title', 'global exposure', '--description', 'applies to all',
                         '--plan-out', '.upgrade/risk-plan.json')
        self.call('risk-apply', '--plan', str(self.root / '.upgrade/risk-plan.json'), '--approve', risk['plan_id'])
        self.call('delivery-apply', '--plan', str(self.root / '.upgrade/delivery-plan.json'),
                  '--approve', review['plan_id'], expected=3)

    def test_unrelated_validation_corruption_still_blocks_delivery(self):
        self.validation()
        review = self.review_delivery()
        self.validation('B')
        path = self.root / '.upgrade/validation/records.jsonl'
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        rows[-1]['note'] = 'tampered'
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
        self.call('delivery-apply', '--plan', str(self.root / '.upgrade/delivery-plan.json'),
                  '--approve', review['plan_id'], expected=2)

    def test_validation_request_retry_noop_conflict_rejected_new_run_recorded(self):
        first = self.validation('A', '--request-id', 'run-1')
        repeated = self.validation('A', '--request-id', 'run-1')
        self.assertTrue(repeated['result']['replayed'])
        self.assertEqual([], repeated['changed'])
        self.assertEqual(first['result']['record'], repeated['result']['record'])
        self.validation('A', '--request-id', 'run-1', status='failed', expected=2)
        self.validation('A', '--request-id', 'run-2')
        records = (self.root / '.upgrade/validation/records.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(2, len(records))

    def test_exact_governance_plan_retry_does_not_duplicate_records(self):
        for kind, fields in (
            ('decision', ['--title', 'choice', '--decision', 'use A']),
            ('risk', ['--title', 'risk', '--description', 'exposure']),
            ('exception', ['--title', 'waiver', '--scope', 'A', '--requirement', 'rule', '--justification', 'reason']),
        ):
            with self.subTest(kind=kind):
                plan = f'.upgrade/{kind}-plan.json'
                review = self.call(f'{kind}-review', *fields, '--plan-out', plan)
                args = (f'{kind}-apply', '--plan', str(self.root / plan), '--approve', review['plan_id'])
                first = self.call(*args)
                second = self.call(*args)
                self.assertTrue(second['result']['replayed'])
                self.assertEqual([], second['changed'])
                self.assertEqual(first['result'][kind], second['result'][kind])

    def test_combined_audit_uses_one_scan(self):
        with mock.patch('commands.diagnostics.audit_entries', wraps=_inventory.audit_entries) as audit:
            output = self.call('git-audit', '--include-artifacts')
        self.assertEqual(1, audit.call_count)
        self.assertIn('app.txt', [entry['path'] for entry in output['result']['artifact_entries']])
