"""Conservative owner probing and real cross-process bootstrap exclusion."""
import contextlib
import errno
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.main import run
from core import locking
from core.errors import CliError


class LockingConcurrencyCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_ambiguous_owner_probe_never_removes_workspace_lock(self):
        upgrade = self.root / '.upgrade'
        upgrade.mkdir()
        path = upgrade / '.lock'
        payload = json.dumps({'pid': 12345, 'hostname': socket.gethostname()})
        path.write_text(payload, encoding='utf-8')
        with mock.patch.object(locking, '_pid_alive', return_value=True):
            with self.assertRaises(CliError):
                with locking.mutation_lock(self.root):
                    self.fail('ambiguous lock admitted a writer')
        self.assertEqual(payload, path.read_text(encoding='utf-8'))

    @unittest.skipIf(os.name == 'nt', 'POSIX probing; Windows has separate handle tests')
    def test_posix_errors_are_conservative_and_missing_process_is_dead(self):
        for error in (OSError(errno.EIO, 'io'), OSError(errno.EINVAL, 'unknown'), PermissionError('denied'), OverflowError()):
            with mock.patch.object(locking.os, 'kill', side_effect=error):
                self.assertTrue(locking._pid_alive(12345))
        with mock.patch.object(locking.os, 'kill', side_effect=ProcessLookupError()):
            self.assertFalse(locking._pid_alive(12345))

    def test_windows_probe_uses_wait_handle_and_preserves_unknown(self):
        import ctypes
        kernel = mock.Mock()
        with mock.patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
             mock.patch.object(ctypes, 'get_last_error', return_value=5, create=True):
            kernel.OpenProcess.return_value = 0
            self.assertTrue(locking._windows_pid_alive(12345))
            with mock.patch.object(ctypes, 'get_last_error', return_value=87, create=True):
                self.assertFalse(locking._windows_pid_alive(12345))
            kernel.OpenProcess.return_value = 42
            for result, alive in ((258, True), (0xFFFFFFFF, True), (0, False)):
                kernel.WaitForSingleObject.return_value = result
                self.assertEqual(alive, locking._windows_pid_alive(12345))
            self.assertEqual(3, kernel.CloseHandle.call_count)

    def test_invalid_pid_is_not_proof_of_exit(self):
        for pid in (0, -1, True):
            self.assertTrue(locking._pid_alive(pid))

    def test_live_current_process_is_detected_without_signaling_it(self):
        self.assertTrue(locking._pid_alive(os.getpid()))

    def test_proven_dead_workspace_lock_can_be_reclaimed(self):
        upgrade = self.root / '.upgrade'
        upgrade.mkdir()
        path = upgrade / '.lock'
        path.write_text(json.dumps({'pid': 12345, 'hostname': socket.gethostname()}), encoding='utf-8')
        with mock.patch.object(locking, '_pid_alive', return_value=False):
            with locking.mutation_lock(self.root):
                self.assertEqual(os.getpid(), json.loads(path.read_text(encoding='utf-8'))['pid'])
        self.assertFalse(path.exists())

    def start_holder(self):
        code = '''
import sys
from pathlib import Path
from core.locking import mutation_lock
with mutation_lock(Path(sys.argv[1]), operation='initialize'):
    print('locked', flush=True)
    sys.stdin.readline()
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(self.root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_holder, child)
        self.assertEqual('locked', child.stdout.readline().strip())
        return child

    @staticmethod
    def stop_holder(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)

    def initialize(self, expected):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(['--root', str(self.root), 'initialize', '--profile', 'lean'])
        self.assertEqual(expected, code, out.getvalue())

    def test_first_initialize_excluded_before_and_after_workspace_creation(self):
        child = self.start_holder()
        self.assertFalse((self.root / '.upgrade').exists())
        self.initialize(2)
        self.assertFalse((self.root / '.upgrade').exists())
        # The same guard must still exclude contenders once the directory
        # appears; switching from an external to an internal lock would race.
        (self.root / '.upgrade').mkdir()
        self.initialize(2)
        self.assertEqual([], list((self.root / '.upgrade').iterdir()))
        child.stdin.write('\n')
        child.stdin.flush()
        child.communicate(timeout=10)
        self.initialize(0)

    def test_process_crash_releases_bootstrap_guard(self):
        child = self.start_holder()
        child.kill()
        child.communicate(timeout=10)
        self.initialize(0)
