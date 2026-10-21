"""Real Git fixtures for submodule and executable-mode subject changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.subjects import git_subject, path_subject
from core.errors import CliError


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

    def init(self, root):
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.email', 'test@example.com')
        self.git(root, 'config', 'user.name', 'Subject Test')

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
