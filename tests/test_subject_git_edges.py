"""Real Git fixtures for worktree subject identity."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cli.main import run
from core import subjects
from core.constants import PLAN_SCHEMA_VERSION
from core.subjects import git_subject, path_subject
from core.errors import CliError


def digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


@unittest.skipUnless(shutil.which('git'), 'Git required')
class SubjectGitEdgesCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'main'
        self.root.mkdir()
        self.init(self.root)

    def git(self, root, *args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)

    def cli(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(['--root', str(self.root), '--format', 'json', *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def init(self, root):
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.email', 'test@example.com')
        self.git(root, 'config', 'user.name', 'Subject Test')

    def symlink(self, name, target, *, directory=False):
        link = self.root / name
        try:
            link.symlink_to(Path(target), target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'symbolic links are unavailable: {exc}')
        return link

    def require_literal_backslash(self):
        probe = Path(r'probe\name')
        if len(probe.parts) != 1:
            self.skipTest('literal backslash filenames are unsupported')
        try:
            (self.root / probe).touch(exist_ok=False)
        except OSError as exc:
            self.skipTest(f'literal backslash filenames are unsupported: {exc}')
        (self.root / probe).unlink()

    def test_plain_git_snapshot_keeps_previous_digest(self):
        files = {'alpha.txt': b'one', 'Beta.txt': b'two'}
        for name, content in files.items():
            (self.root / name).write_bytes(content)
        self.git(self.root, 'add', 'alpha.txt')
        (self.root / '.upgrade').mkdir()
        (self.root / '.upgrade/data').write_text('excluded fixture', encoding='utf-8')
        entries = [
            {'path': name, 'index_mode': '100644' if name == 'alpha.txt' else None, 'kind': 'file',
             'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
             'executable': None if os.name == 'nt' else False}
            for name, content in files.items()
        ]
        self.assertEqual({
            'type': 'git-worktree', 'ref': 'unborn', 'digest': digest({'head': 'unborn', 'entries': entries}),
            'file_count': 2,
        }, git_subject(self.root))

    def test_plain_git_literal_backslashes_keep_legacy_digest_and_exclusions(self):
        self.require_literal_backslash()
        contents = b'ordinary fixture'
        (self.root / r'd\item').write_bytes(contents)
        (self.root / r'.upgrade\excluded').write_bytes(b'legacy excluded fixture')
        entries = [
            {'path': 'd/item', 'index_mode': None, 'kind': 'file', 'size': len(contents),
             'sha256': hashlib.sha256(contents).hexdigest(), 'executable': False},
        ]
        self.assertEqual({
            'type': 'git-worktree', 'ref': 'unborn', 'digest': digest({'head': 'unborn', 'entries': entries}),
            'file_count': 1,
        }, git_subject(self.root))

    def test_git_link_name_classification_with_literal_backslashes(self):
        listed = [r'.git\alias', r'.upgrade\alias', r'd\item', 'd/item', '.git/inside', '.upgrade/inside']
        expected_paths = [r'.git\alias', r'.upgrade\alias', 'd/item', r'd\item']

        def classify(root, paths):
            self.assertEqual(self.root, root)
            self.assertEqual(expected_paths, paths)
            return {path: 'symlink' for path in paths}, set()

        with mock.patch.object(subjects, '_git_paths', side_effect=[[], [], listed]), \
             mock.patch.object(subjects, '_git_worktree_links', side_effect=classify), \
             mock.patch.object(subjects, '_read_link', return_value={'kind': 'symlink', 'target': 'missing'}):
            actual = git_subject(self.root)
        entries = [{'path': name, 'index_mode': None, 'kind': 'symlink', 'target': 'missing'} for name in expected_paths]
        self.assertEqual(digest({'head': 'unborn', 'entries': entries}), actual['digest'])
        self.assertEqual(len(expected_paths), actual['file_count'])

    def test_literal_backslash_and_separator_are_distinct_for_tracked_and_untracked_links(self):
        self.require_literal_backslash()
        self.git(self.root, 'config', 'core.symlinks', 'true')
        (self.root / 'd').mkdir()
        source = self.symlink(r'd\item', 'missing')
        destination = self.root / 'd/item'

        untracked = git_subject(self.root)
        source.rename(destination)
        renamed = git_subject(self.root)
        self.assertEqual((1, 1), (untracked['file_count'], renamed['file_count']))
        self.assertNotEqual(untracked['digest'], renamed['digest'])

        destination.rename(source)
        self.git(self.root, 'add', '--', source.name)
        stage = self.git(self.root, 'ls-files', '--stage').stdout
        self.assertTrue(stage.startswith(b'120000 '), stage)
        tracked = git_subject(self.root)
        self.git(self.root, 'mv', '--', source.name, 'd/item')
        renamed = git_subject(self.root)
        self.assertEqual((1, 1), (tracked['file_count'], renamed['file_count']))
        self.assertNotEqual(tracked['digest'], renamed['digest'])

    def test_literal_backslash_links_are_distinct_from_excluded_directories(self):
        self.require_literal_backslash()
        before = git_subject(self.root)
        self.assertEqual(0, before['file_count'])
        for count, name in enumerate((r'.git\alias', r'.upgrade\alias'), start=1):
            self.symlink(name, 'missing')
            current = git_subject(self.root)
            self.assertEqual(count, current['file_count'])
            self.assertNotEqual(before['digest'], current['digest'])
            before = current

    def test_tracked_link_binds_retarget_and_ignored_target_content(self):
        self.git(self.root, 'config', 'core.symlinks', 'true')
        (self.root / '.gitignore').write_text('*.target\n', encoding='utf-8')
        for name in ('one.target', 'two.target'):
            (self.root / name).write_text('same content', encoding='utf-8')
        self.git(self.root, 'check-ignore', 'one.target', 'two.target')
        link = self.symlink('alias', 'one.target')
        self.git(self.root, 'add', '.gitignore', 'alias')
        stage = self.git(self.root, 'ls-files', '--stage', 'alias').stdout
        self.assertTrue(stage.startswith(b'120000 '), stage)
        first = git_subject(self.root)
        self.assertEqual(2, first['file_count'])
        link.unlink()
        self.symlink('alias', 'two.target')
        second = git_subject(self.root)
        self.assertNotEqual(first['digest'], second['digest'])
        (self.root / 'one.target').write_text('unrelated edit', encoding='utf-8')
        self.assertEqual(second, git_subject(self.root))
        (self.root / 'two.target').write_text('linked edit', encoding='utf-8')
        self.assertNotEqual(second['digest'], git_subject(self.root)['digest'])

    def test_git_links_keep_excluded_file_identity_without_reading_content(self):
        (self.root / '.upgrade').mkdir()
        (self.root / '.upgrade/data.txt').write_text('fixture', encoding='utf-8')
        self.symlink('upgrade-link', '.upgrade/data.txt')
        self.symlink('git-link', '.git/config')
        artifact = self.root / 'review.json'
        self.symlink('plan-link', 'review.json')

        def write_plan(title):
            plan = {'kind': 'collect', 'schema_version': PLAN_SCHEMA_VERSION, 'entries': [], 'title': title}
            artifact.write_text(json.dumps({**plan, 'plan_id': digest(plan)}), encoding='utf-8')

        write_plan('first fixture plan')
        before = git_subject(self.root)
        self.assertEqual(3, before['file_count'])
        (self.root / '.upgrade/data.txt').write_text('new fixture', encoding='utf-8')
        self.git(self.root, 'config', 'test.flag', 'changed')
        write_plan('second fixture plan')
        self.assertEqual(before, git_subject(self.root))
        self.git(self.root, 'add', 'review.json')
        tracked = git_subject(self.root)
        write_plan('tracked plans contribute their content')
        self.assertNotEqual(tracked['digest'], git_subject(self.root)['digest'])

    def test_git_dangling_link_rechecks_file_or_directory_appearance(self):
        self.symlink('file-link', 'missing')
        before = git_subject(self.root)
        (self.root / 'missing').write_text('present', encoding='utf-8')
        self.assertNotEqual(before['digest'], git_subject(self.root)['digest'])
        self.symlink('directory-link', 'later', directory=True)
        git_subject(self.root)
        (self.root / 'later').mkdir()
        with self.assertRaisesRegex(CliError, 'directory-link.*Git subjects'):
            git_subject(self.root)

    def test_git_rejects_visible_links_to_tracked_or_ignored_directories(self):
        (self.root / '.gitignore').write_text('/ignored/\n/invisible\n', encoding='utf-8')
        for name in ('tracked', 'ignored'):
            (self.root / name).mkdir()
            (self.root / name / 'data.txt').write_text('fixture', encoding='utf-8')
        self.git(self.root, 'add', 'tracked/data.txt')
        before = git_subject(self.root)
        self.symlink('invisible', 'ignored', directory=True)
        self.assertEqual(before, git_subject(self.root))
        for target in ('tracked', 'ignored'):
            with self.subTest(target=target):
                link = self.symlink('visible', target, directory=True)
                try:
                    with self.assertRaisesRegex(CliError, 'visible.*Git subjects'):
                        git_subject(self.root)
                finally:
                    link.unlink()

    def test_git_rejects_control_directories_and_link_loops(self):
        (self.root / '.upgrade/internal').mkdir(parents=True)
        (self.root / '.git/internal').mkdir()
        for target in ('.git', '.upgrade', '.git/internal', '.upgrade/internal', 'alias'):
            with self.subTest(target=target):
                link = self.symlink('alias', target, directory=True)
                try:
                    error = 'cannot fingerprint subject link.*alias' if target == 'alias' else 'alias.*control directory'
                    with self.assertRaisesRegex(CliError, error):
                        git_subject(self.root)
                finally:
                    link.unlink()

    def test_external_links_block_git_and_directory_validation_review_and_apply(self):
        self.cli('initialize', '--profile', 'lean')
        selected = self.root / 'selected'
        selected.mkdir()
        (selected / 'file.txt').write_text('fixture', encoding='utf-8')
        outside = self.base / 'outside'
        outside.mkdir()
        external_file = outside / 'file.txt'
        external_file.write_text('outside fixture', encoding='utf-8')
        actual_open = Path.open

        def open_inside_root(path, *args, **kwargs):
            self.assertNotIn(outside, path.parents, f'read outside the repository: {path}')
            return actual_open(path, *args, **kwargs)

        required = ('--require-check', 'suite', '--gate', 'readiness', '--target', 'release')
        validation = ('validation-record', '--check', 'suite', '--status', 'passed')
        records = self.root / '.upgrade/validation/records.jsonl'
        for name, binding in (('git', ()), ('directory', ('--subject-path', 'selected'))):
            self.cli(*validation, *binding)
            plan = f'.upgrade/{name}-delivery.json'
            review = self.cli('delivery-review', *required, *binding, '--plan-out', plan)
            self.assertTrue(review['result']['eligible'])
            self.assertEqual('git-worktree' if name == 'git' else 'path', review['result']['subject']['type'])
            original_records = records.read_bytes()
            for kind, target, folder in (
                ('file', external_file, False), ('directory', outside, True), ('dangling', outside / 'missing', False)
            ):
                with self.subTest(subject=name, target=kind), mock.patch.object(Path, 'open', open_inside_root):
                    link = self.symlink('selected/alias', target, directory=folder)
                    try:
                        rejected = (
                            self.cli(*validation, *binding, expected=2),
                            self.cli('delivery-review', *required, *binding, expected=2),
                            self.cli('delivery-apply', '--plan', str(self.root / plan),
                                     '--approve', review['plan_id'], expected=2),
                        )
                        for attempt in rejected:
                            self.assertIn('link target resolves outside allowed boundary', attempt['errors'][0])
                            self.assertEqual([], attempt['changed'])
                        self.assertEqual(original_records, records.read_bytes())
                        self.assertEqual(0, self.cli('delivery-audit')['result']['count'])
                    finally:
                        link.unlink()

    def test_literal_backslash_link_rename_stales_git_approval_and_execution(self):
        self.require_literal_backslash()
        self.cli('initialize', '--profile', 'lean')
        (self.root / 'd').mkdir()
        source = self.symlink(r'd\item', 'missing')
        destination = self.root / 'd/item'
        self.cli('validation-record', '--check', 'suite', '--status', 'passed')
        plan = '.upgrade/git-delivery.json'
        review = self.cli('delivery-review', '--gate', 'readiness', '--target', 'release',
                          '--require-check', 'suite', '--plan-out', plan)
        self.assertTrue(review['result']['eligible'])
        self.assertEqual('git-worktree', review['result']['subject']['type'])

        source.rename(destination)
        rejected = self.cli('delivery-apply', '--plan', str(self.root / plan),
                            '--approve', review['plan_id'], expected=3)
        self.assertIn('delivery subject changed', rejected['errors'][0])
        self.assertEqual([], rejected['changed'])
        self.assertEqual(0, self.cli('delivery-audit')['result']['count'])

        change = 'from pathlib import Path\nimport sys\nPath(sys.argv[1]).rename(sys.argv[2])'
        result = self.cli('validation-record', '--check', 'suite', '--execute', sys.executable,
                          '-c', change, 'd/item', source.name)
        record = result['result']['record']
        self.assertEqual(0, record['exit_code'])
        self.assertFalse(record['execution']['subject_unchanged'])
        self.assertEqual('failed', record['status'])

    def test_git_indexed_paths_below_directory_link_reject_it(self):
        former = self.root / 'folder'
        former.mkdir()
        child = former / 'data.txt'
        child.write_text('indexed fixture', encoding='utf-8')
        self.git(self.root, 'add', 'folder/data.txt')
        (self.root / '.gitignore').write_text('ignored/\n/folder\n', encoding='utf-8')
        git_subject(self.root)

        child.unlink()
        former.rmdir()
        target = self.root / 'ignored'
        target.mkdir()
        (target / 'data.txt').write_text('indexed fixture', encoding='utf-8')
        self.symlink('folder', 'ignored', directory=True)
        with self.assertRaisesRegex(CliError, 'folder.*Git subjects'):
            git_subject(self.root)

    def test_git_snapshot_is_stable_across_hash_seeds_for_casefold_ties(self):
        upper, lower = self.root / 'A.txt', self.root / 'a.txt'
        upper.write_text('upper', encoding='utf-8')
        if lower.exists():
            self.skipTest('a case-sensitive filesystem is required')
        lower.write_text('lower', encoding='utf-8')
        self.git(self.root, 'add', 'A.txt')
        checkout = Path(__file__).resolve().parents[1]
        code = ('from pathlib import Path; import sys; from core.subjects import git_subject; '
                'print(git_subject(Path(sys.argv[1]))["digest"])')
        digests = set()
        for seed in range(12):
            completed = subprocess.run([sys.executable, '-B', '-c', code, str(self.root)],
                                       cwd=checkout, env={**os.environ, 'PYTHONHASHSEED': str(seed)},
                                       check=True, capture_output=True)
            digests.add(completed.stdout.decode('ascii').strip())
        self.assertEqual(2, git_subject(self.root)['file_count'])
        self.assertEqual({git_subject(self.root)['digest']}, digests)

    def test_submodule_content_and_gitlink_change_subject(self):
        source = self.base / 'source'
        source.mkdir()
        self.init(source)
        (source / 'app.txt').write_text('one', encoding='utf-8')
        self.git(source, 'add', 'app.txt')
        self.git(source, 'commit', '-qm', 'baseline')
        self.git(self.root, '-c', 'protocol.file.allow=always', 'submodule', 'add', str(source), 'module')
        before = git_subject(self.root)
        (self.root / 'module/app.txt').write_text('dirty', encoding='utf-8')
        self.assertNotEqual(before['digest'], git_subject(self.root)['digest'])
        self.git(self.root / 'module', 'checkout', '--', 'app.txt')
        self.assertEqual(before, git_subject(self.root))
        oid = self.git(source, 'rev-parse', 'HEAD').stdout.decode().strip()
        self.git(self.root, 'update-index', '--cacheinfo', '160000', '1' * 40, 'module')
        self.assertNotEqual(before['digest'], git_subject(self.root)['digest'])
        self.git(self.root, 'update-index', '--cacheinfo', '160000', oid, 'module')
        self.assertEqual(before, git_subject(self.root))
        self.git(self.root, 'submodule', 'deinit', '-f', 'module')
        uninitialized = git_subject(self.root)
        self.assertNotEqual(before['digest'], uninitialized['digest'])
        self.assertEqual(uninitialized, git_subject(self.root))
        (self.root / 'module').rmdir()
        (self.root / 'replacement').mkdir()
        self.symlink('module', 'replacement', directory=True)
        with self.assertRaisesRegex(CliError, 'module.*Git subjects'):
            git_subject(self.root)

    def test_index_executable_mode_changes_subject(self):
        (self.root / 'run.sh').write_text('echo hello\n', encoding='utf-8')
        self.git(self.root, 'add', 'run.sh')
        before = git_subject(self.root)
        self.git(self.root, 'update-index', '--chmod=+x', 'run.sh')
        self.assertNotEqual(before['digest'], git_subject(self.root)['digest'])

    def test_unmerged_index_fails_closed(self):
        (self.root / 'conflict.txt').write_text('content', encoding='utf-8')
        oid = self.git(self.root, 'hash-object', '-w', 'conflict.txt').stdout.decode().strip()
        subprocess.run(['git', 'update-index', '--index-info'], cwd=self.root,
                       input=f'100644 {oid} 1\tconflict.txt\n'.encode(), check=True, capture_output=True)
        with self.assertRaises(CliError):
            git_subject(self.root)

    @unittest.skipIf(os.name == 'nt', 'POSIX executable permissions required')
    def test_worktree_and_path_executable_changes_subject(self):
        target = self.root / 'run.sh'
        target.write_text('echo hello\n', encoding='utf-8')
        target.chmod(0o644)
        self.git(self.root, 'add', 'run.sh')
        before, direct = git_subject(self.root), path_subject(self.root, 'run.sh')
        target.chmod(0o755)
        self.assertNotEqual(before['digest'], git_subject(self.root)['digest'])
        self.assertNotEqual(direct['digest'], path_subject(self.root, 'run.sh')['digest'])
