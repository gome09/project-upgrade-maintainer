from __future__ import annotations

import contextlib
import errno
import os
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.main import run
from core import durability, policy
from core.errors import CliError
from core.locking import mutation_lock
from core.transactions import Transaction
from core.workspace import atomic_write


class Round17DurabilityCase(unittest.TestCase):

    def test_directory_fsync_degrades_only_for_explicitly_unsupported_errors(self) -> None:
        if os.name == "nt":
            self.skipTest("directory handles do not support POSIX fsync semantics on Windows")
        with mock.patch("core.durability.os.open", side_effect=OSError(errno.EINVAL, "unsupported")):
            self.assertFalse(durability.fsync_directory(Path("/does-not-matter")))
        with mock.patch("core.durability.os.open", side_effect=OSError(errno.EIO, "io failure")):
            with self.assertRaises(OSError):
                durability.fsync_directory(Path("/does-not-matter"))

    def test_atomic_write_failure_cleans_temporary_file_without_replacing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("before\n", encoding="utf-8")
            with mock.patch("core.durability.os.replace", side_effect=OSError(errno.EIO, "replace failed")):
                with self.assertRaises(OSError):
                    durability.atomic_write_bytes(target, b"after\n")
            self.assertEqual("before\n", target.read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".target.txt.*.tmp")))

    def test_exdev_move_fallback_is_durable_copy_then_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            target = root / "nested" / "target.bin"
            payload = b"cross-device-payload"
            source.write_bytes(payload)
            real_replace = os.replace

            def replace_with_exdev_once(src, dst):
                if Path(src) == source:
                    raise OSError(errno.EXDEV, "cross-device")
                return real_replace(src, dst)

            with mock.patch("core.durability.os.replace", side_effect=replace_with_exdev_once):
                durability.durable_move_file(source, target)
            self.assertFalse(source.exists())
            self.assertEqual(payload, target.read_bytes())

    def test_durable_copy_unlink_and_private_tree_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            target = root / "nested" / "copy.txt"
            source.write_text("copy me\n", encoding="utf-8")
            durability.durable_copy_file(source, target)
            self.assertEqual("copy me\n", target.read_text(encoding="utf-8"))
            durability.durable_unlink(target)
            self.assertFalse(target.exists())
            private = root / "private"
            (private / "nested").mkdir(parents=True)
            (private / "nested" / "payload").write_text("x", encoding="utf-8")
            durability.durable_rmtree(private)
            self.assertFalse(private.exists())
            durability.durable_rmtree(private)  # idempotent cleanup of an already absent payload dir

    def test_atomic_write_publishes_parent_directory_entry_durably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "state.json"
            with mock.patch("core.durability.fsync_directory", wraps=durability.fsync_directory) as fsync_dir:
                durability.atomic_write_bytes(target, b'{"ok":true}\n')
            self.assertEqual(b'{"ok":true}\n', target.read_bytes())
            synced = [call.args[0] for call in fsync_dir.call_args_list]
            self.assertIn(target.parent, synced)

    def test_durable_move_syncs_source_and_destination_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir, target_dir = root / "from", root / "to"
            source_dir.mkdir(); target_dir.mkdir()
            source = source_dir / "artifact.txt"; target = target_dir / "artifact.txt"
            source.write_text("payload\n", encoding="utf-8")
            with mock.patch("core.durability.fsync_directory", wraps=durability.fsync_directory) as fsync_dir:
                durability.durable_move_file(source, target)
            self.assertFalse(source.exists())
            self.assertEqual("payload\n", target.read_text(encoding="utf-8"))
            synced = [call.args[0] for call in fsync_dir.call_args_list]
            self.assertIn(source_dir, synced)
            self.assertIn(target_dir, synced)

    def test_lock_acquisition_fsyncs_workspace_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".upgrade").mkdir()
            with mock.patch("core.locking.fsync_directory", return_value=True) as fsync_dir:
                with mutation_lock(root, operation="test"):
                    self.assertTrue((root / ".upgrade" / ".lock").is_file())
            fsync_dir.assert_called_with(root / ".upgrade")
            self.assertFalse((root / ".upgrade" / ".lock").exists())

    def test_crash_after_file_publish_before_mark_applied_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".upgrade").mkdir()
            target = root / "state.txt"
            target.write_text("before\n", encoding="utf-8")
            tx = Transaction.start(root, "test", "TXN-A1B2C3D4E5F6")
            try:
                with mock.patch.object(tx, "mark_applied", side_effect=RuntimeError("simulated crash window")):
                    with self.assertRaisesRegex(RuntimeError, "simulated crash window"):
                        atomic_write(target, "after\n")
                tx.fail("simulated crash after durable publish")
            finally:
                tx.close()
            loaded = Transaction.load(root, tx.transaction_id)
            self.assertEqual("applied", loaded.analysis()[0]["state"])
            loaded.rollback()
            self.assertEqual("before\n", target.read_text(encoding="utf-8"))

    def test_cross_filesystem_move_duplicate_window_has_safe_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".upgrade").mkdir()
            source = root / "source.txt"; target = root / "nested" / "target.txt"
            source.write_text("payload\n", encoding="utf-8")
            tx = Transaction.start(root, "test", "TXN-112233445566")
            tx.prepare_move(source, target)
            # This is the observable state if an EXDEV fallback durably copied the
            # destination and the process died before unlinking the source.
            durability.durable_copy_file(source, target)
            tx.fail("simulated crash between copy and unlink")
            loaded = Transaction.load(root, tx.transaction_id)
            self.assertEqual("duplicated", loaded.analysis()[0]["state"])
            loaded.rollback()
            self.assertTrue(source.is_file())
            self.assertEqual("payload\n", source.read_text(encoding="utf-8"))
            self.assertFalse(target.exists())

    def test_seeded_crash_state_fuzz_fails_closed_or_restores_preimage(self) -> None:
        rng = random.Random(170017)
        states = ("not_applied", "applied", "conflict")
        for index in range(24):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / ".upgrade").mkdir()
                    target = root / f"odd [artifact] {index} !.bin"
                    before = b"before:" + rng.randbytes(32)
                    after = b"after:" + rng.randbytes(47)
                    third_party = b"third-party:" + rng.randbytes(29)
                    target.write_bytes(before)
                    tx = Transaction.start(root, "fuzz", f"TXN-{index:012X}")
                    tx.prepare_write(target, after)
                    state = states[index % len(states)]
                    if state == "applied":
                        durability.atomic_write_bytes(target, after)
                    elif state == "conflict":
                        durability.atomic_write_bytes(target, third_party)
                    tx.fail(f"seeded crash state: {state}")
                    loaded = Transaction.load(root, tx.transaction_id)
                    observed = loaded.analysis()[0]["state"]
                    self.assertEqual(state, observed)
                    if state == "conflict":
                        with self.assertRaisesRegex(CliError, "conflict"):
                            loaded.rollback()
                        self.assertEqual(third_party, target.read_bytes())
                    else:
                        loaded.rollback()
                        self.assertEqual(before, target.read_bytes())

    def test_existing_workspace_initialize_is_transactional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile in ("lean", "standard"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = run(["--root", str(root), "--format", "json", "initialize", "--profile", profile])
                self.assertEqual(0, code, output.getvalue())
            journals = sorted((root / ".upgrade" / "transactions").glob("TXN-*.json"))
            self.assertTrue(journals)
            payloads = [json.loads(path.read_text(encoding="utf-8")) for path in journals]
            self.assertTrue(any(item["command"] == "initialize" and item["state"] == "committed" for item in payloads))

    def test_durability_core_is_bound_into_all_plan_implementation_digests(self) -> None:
        self.assertIn("core/durability.py", policy.CORE_IMPLEMENTATION_FILES)


if __name__ == "__main__":
    unittest.main()
