"""Recovery of repeated writes recorded by the existing transaction writer."""

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cli.main import run
from core import transactions
from core.durability import atomic_write_bytes
from core.errors import CliError
from core.transactions import Transaction


class TransactionWriteChainCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / '.upgrade').mkdir()
        self.tx = Transaction.start(self.root, 'sync')
        self.addCleanup(self.tx.close)

    def cli(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run(['--root', str(self.root), *args])
        self.assertEqual(expected, code, stdout.getvalue() + stderr.getvalue())
        return json.loads(stdout.getvalue())

    def write(self, name, payload, *, published=True, marked=True):
        target = self.root / name
        index = self.tx.prepare_write(target, payload)
        if published:
            atomic_write_bytes(target, payload)
        if marked:
            self.tx.mark_applied(index)
        return target

    def reload(self):
        return Transaction.load(self.root, self.tx.transaction_id)

    def fail(self):
        self.tx.fail('simulated command error')
        return self.reload()

    def review(self, filename):
        return self.cli('recovery-review', '--transaction-id', self.tx.transaction_id,
                        '--plan-out', filename)

    def apply(self, review, filename, *, expected=0):
        return self.cli('recovery-apply', '--plan', str(self.root / filename),
                        '--approve', review['plan_id'], expected=expected)

    @staticmethod
    def states(tx):
        return [op['state'] for op in tx.analysis()]

    def test_interleaved_chains_restore_missing_and_existing_preimages(self):
        b = self.root / 'b'
        b.write_bytes(b'original\x00bytes')
        a = self.write('a', b'a1')
        self.write('b', b'b1')
        c = self.write('c', b'never published', published=False, marked=False)
        self.write('a', b'a2')
        self.write('b', b'b2')
        loaded = self.fail()
        self.assertEqual(['applied', 'applied', 'not_applied', 'applied', 'applied'], self.states(loaded))
        loaded.rollback()
        self.assertFalse(a.exists())
        self.assertFalse(c.exists())
        self.assertEqual(b'original\x00bytes', b.read_bytes())
        self.assertEqual(['rolled_back'] * 5, [op['state'] for op in self.reload().data['operations']])

    def test_prepared_non_tail_can_be_unpublished_then_published(self):
        a = self.write('a', b'unpublished', published=False, marked=False)
        self.write('a', b'published', marked=False)
        self.write('a', b'final')
        self.write('a', b'unpublished tail', published=False, marked=False)
        loaded = self.fail()
        self.assertEqual(['not_applied', 'applied', 'applied', 'not_applied'], self.states(loaded))
        loaded.rollback()
        self.assertFalse(a.exists())

    def test_prepared_tail_transition_invalidates_approved_plan(self):
        a = self.write('a', b'first')
        self.write('a', b'published but not marked', marked=False)
        loaded = self.fail()
        self.assertEqual(['applied', 'applied'], self.states(loaded))
        review = self.review('apply.json')
        a.write_bytes(b'first')
        self.assertEqual(['applied', 'not_applied'], self.states(loaded))
        stale = self.apply(review, 'apply.json', expected=3)
        self.assertIn('filesystem state changed', stale['errors'][0])
        self.assertEqual(b'first', a.read_bytes())
        self.assertTrue(self.review('fresh.json')['result']['recoverable'])

    def test_restart_uses_persisted_progress_at_each_io_and_journal_boundary(self):
        for cut in ('before_io', 'after_io', 'before_progress', 'after_progress'):
            with self.subTest(cut=cut):
                # Earlier cases leave a resolved journal. Each case uses distinct bytes.
                if cut != 'before_io':
                    self.tx = Transaction.start(self.root, 'sync')
                target = self.root / cut
                target.write_bytes(b'original')
                self.write(cut, b'first')
                self.write(cut, b'tail')
                loaded = self.fail()
                real_copy = transactions.durable_copy_file
                real_publish = transactions._raw_json

                def copy_with_cut(backup, destination):
                    if cut == 'before_io':
                        raise OSError(cut)
                    real_copy(backup, destination)
                    if cut == 'after_io':
                        raise OSError(cut)

                def publish_with_cut(path, data):
                    progress = (data['state'] == 'rolling_back'
                                and data['operations'][-1]['state'] == 'rolled_back')
                    if progress and cut == 'before_progress':
                        raise OSError(cut)
                    real_publish(path, data)
                    if progress and cut == 'after_progress':
                        raise OSError(cut)

                with mock.patch.object(transactions, 'durable_copy_file', side_effect=copy_with_cut), \
                     mock.patch.object(transactions, '_raw_json', side_effect=publish_with_cut):
                    with self.assertRaisesRegex(OSError, cut):
                        loaded.rollback()

                loaded = self.reload()
                self.assertEqual('rolling_back', loaded.data['state'])
                self.assertEqual('rolled_back' if cut == 'after_progress' else 'applied',
                                 loaded.data['operations'][-1]['state'])
                self.assertEqual(b'tail' if cut == 'before_io' else b'first', target.read_bytes())
                review = self.review(f'{cut}.json')
                self.assertTrue(review['result']['recoverable'])
                self.apply(review, f'{cut}.json')
                self.assertEqual(b'original', target.read_bytes())

    def test_completed_chain_cannot_adopt_reappearing_post(self):
        a = self.write('a', b'first')
        self.write('a', b'tail')
        loaded = self.fail()
        original = transactions._raw_json

        def fail_to_finish(path, data):
            if data['state'] == 'rolled_back':
                raise OSError('journal completion interrupted')
            return original(path, data)

        with mock.patch.object(transactions, '_raw_json', side_effect=fail_to_finish):
            with self.assertRaisesRegex(OSError, 'completion interrupted'):
                loaded.rollback()
        loaded = self.reload()
        self.assertFalse(a.exists())
        self.assertEqual(['not_applied', 'not_applied'], self.states(loaded))
        a.write_bytes(b'tail')
        self.assertEqual(['conflict', 'conflict'], self.states(loaded))
        with self.assertRaisesRegex(CliError, 'recovery conflict'):
            loaded.rollback()
        self.assertEqual(b'tail', a.read_bytes())
        a.unlink()
        with mock.patch.object(transactions, 'durable_unlink') as unlink, \
             mock.patch.object(transactions, 'durable_copy_file') as copy:
            loaded.rollback()
            unlink.assert_not_called()
            copy.assert_not_called()

    def test_only_next_global_operation_may_already_be_undone(self):
        a = self.write('a', b'first')
        self.write('a', b'tail')
        self.write('untouched', b'prepared', published=False, marked=False)
        loaded = self.fail()
        loaded.data['state'] = 'rolling_back'
        loaded._flush()
        a.write_bytes(b'first')
        self.assertEqual(['conflict', 'conflict', 'not_applied'], self.states(loaded))
        loaded.data['operations'][-1]['state'] = 'rolled_back'
        self.assertEqual(['applied', 'not_applied', 'not_applied'], self.states(loaded))
        loaded.data['operations'][0]['state'] = 'rolled_back'
        self.assertEqual(['conflict'] * 3, self.states(loaded))

    def test_invalid_backup_or_adjacent_history_refuses_every_write_up_front(self):
        a = self.write('a', b'first')
        self.write('a', b'tail')
        b = self.write('b', b'other independent operation')
        loaded = self.fail()
        backup = self.root / loaded.data['operations'][1]['backup']
        before = backup.read_bytes()
        operations = loaded.data['operations']

        def assert_conflict():
            self.assertEqual(['conflict', 'conflict', 'applied'], self.states(loaded))
            with self.assertRaisesRegex(CliError, 'recovery conflict'):
                loaded.rollback()
            self.assertEqual(b'tail', a.read_bytes())
            self.assertEqual(b'other independent operation', b.read_bytes())

        backup.unlink()
        assert_conflict()
        backup.write_bytes(b'other')
        assert_conflict()
        backup.write_bytes(before)
        backup_name = operations[1]['backup']
        operations[1]['backup'] = backup_name.replace('000002', '000001')
        assert_conflict()
        operations[1]['backup'] = backup_name
        post = operations[0]['post_sha256']
        operations[0]['post_sha256'] = '0' * 64
        assert_conflict()
        operations[0]['post_sha256'] = post
        operations[0]['target'] = 'alternate/../a'
        assert_conflict()

    def test_backup_target_and_parent_links_are_refused(self):
        a = self.write('nested/a', b'first')
        self.write('nested/a', b'tail')
        loaded = self.fail()
        backup = self.root / loaded.data['operations'][1]['backup']
        for index, replaced in enumerate((a, a.parent, backup, backup.parent)):
            with self.subTest(replaced=replaced):
                moved = self.root / f'stand-in-{index}'
                is_directory = replaced.is_dir()
                replaced.rename(moved)
                try:
                    try:
                        replaced.symlink_to(moved, target_is_directory=is_directory)
                    except (NotImplementedError, OSError) as exc:
                        self.skipTest(f'symbolic links are unavailable: {exc}')
                    self.assertEqual(['conflict', 'conflict'], self.states(loaded))
                    with self.assertRaisesRegex(CliError, 'recovery conflict'):
                        loaded.rollback()
                finally:
                    if replaced.is_symlink():
                        replaced.unlink()
                    moved.rename(replaced)
                self.assertEqual(b'tail', a.read_bytes())
                self.assertEqual(b'first', backup.read_bytes())

    def test_junction_parent_and_control_destinations_are_refused(self):
        a = self.write('nested/a', b'first')
        self.write('nested/a', b'tail')
        loaded = self.fail()
        original_lstat = Path.lstat

        def junction(path):
            if path == a.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
            return original_lstat(path)

        with mock.patch.object(Path, 'lstat', junction):
            self.assertEqual(['conflict', 'conflict'], self.states(loaded))
        for op in loaded.data['operations']:
            op['target'] = f'.upgrade/transactions/{loaded.transaction_id}/arbitrary.bin'
        self.assertEqual(['conflict', 'conflict'], self.states(loaded))

    def test_chain_cannot_share_source_or_non_write_destination(self):
        a = self.write('a', b'first')
        copy = self.root / 'copied'
        self.tx.prepare_copy(a, copy)
        self.write('a', b'tail')
        loaded = self.fail()
        self.assertEqual(['conflict'] * 3, self.states(loaded))
        self.assertFalse(copy.exists())
        loaded.data['operations'][1].update(kind='unlink', source=None, target='a')
        self.assertEqual(['conflict'] * 3, self.states(loaded))

    def test_conflict_between_operations_keeps_prior_progress_and_new_bytes(self):
        a = self.write('a', b'a1')
        b = self.write('b', b'b1')
        self.write('a', b'a2')
        self.write('b', b'b2')
        loaded = self.fail()
        flush = loaded._flush

        def flush_and_change_earlier_target():
            flush()
            if loaded.data['operations'][-1]['state'] == 'rolled_back':
                a.write_bytes(b'external content')

        with mock.patch.object(loaded, '_flush', side_effect=flush_and_change_earlier_target):
            with self.assertRaisesRegex(CliError, 'state changed at operation 3'):
                loaded.rollback()
        loaded = self.reload()
        self.assertEqual('rolling_back', loaded.data['state'])
        self.assertEqual(['applied', 'applied', 'applied', 'rolled_back'],
                         [op['state'] for op in loaded.data['operations']])
        self.assertEqual(b'external content', a.read_bytes())
        self.assertEqual(b'b1', b.read_bytes())
        self.assertFalse(self.review('conflict.json')['result']['recoverable'])
