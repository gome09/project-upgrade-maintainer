"""Filesystem and CLI regressions for explicit delivery subject identity."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cli.main import run
from core import subjects
from core.constants import PLAN_SCHEMA_VERSION
from core.errors import CliError


def digest(payload):
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class SubjectBindingCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "repository"
        self.root.mkdir()

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(["--root", str(self.root), "--format", "json", *args])
        self.assertEqual(expected, code, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue())

    def write(self, name, contents="fixture content\n"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents.encode("utf-8"))
        return target

    def link(self, name, target, *, directory=False):
        link = self.root / name
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are unavailable: {exc}")
        return link

    def test_plain_relative_and_absolute_subjects_keep_previous_digest(self):
        one = self.write("selected/b.txt", "content b")
        two = self.write("selected/Sub/A.txt", "content a")
        self.write("selected/plan.json", self.saved_plan("sample"))

        def descriptor(file):
            contents = file.read_bytes()
            return {"kind": "file", "sha256": hashlib.sha256(contents).hexdigest(),
                    "size": len(contents), "executable": None if os.name == "nt" else False}

        for file in (one, two):
            relative = file.relative_to(self.root).as_posix()
            expected = {"type": "path", "ref": relative, "file_count": 1,
                        "digest": digest({"root": relative, **descriptor(file)})}
            self.assertEqual(expected, subjects.path_subject(self.root, relative))
            self.assertEqual(expected, subjects.path_subject(self.root, str(file)))
        expected = {"type": "path", "ref": "selected", "file_count": 2,
                    "digest": digest({"root": "selected", "entries": [
                        {"path": "b.txt", **descriptor(one)}, {"path": "Sub/A.txt", **descriptor(two)},
                    ]})}
        self.assertEqual(expected, subjects.path_subject(self.root, "selected"))
        self.assertEqual(expected, subjects.path_subject(self.root, str(self.root / "selected")))

    def test_terminal_links_are_rejected_before_writing_validation_evidence(self):
        self.call("initialize", "--profile", "lean")
        self.write("direct.txt")
        link = self.link("alias", "direct.txt")
        records = self.root / ".upgrade/validation/records.jsonl"
        self.assertFalse(records.exists())
        for value in ("alias", str(link)):
            with self.subTest(value=value):
                rejected = self.call("validation-record", "--check", "suite", "--status", "passed",
                                     "--subject-path", value, expected=2)
                self.assertIn("subject path ends at a symlink", rejected["errors"][0])
                self.assertEqual([], rejected["changed"])
                self.assertFalse(records.exists())

    def test_parent_alias_is_resolved_and_recheck_uses_the_fixed_ref(self):
        one = self.write("one/file.txt")
        two = self.write("two/file.txt")
        alias = self.link("alias", "one", directory=True)
        expected = subjects.path_subject(self.root, str(one))
        reviewed = subjects.path_subject(self.root, "alias/file.txt")
        self.assertEqual(expected, reviewed)
        self.assertEqual("one/file.txt", reviewed["ref"])
        alias.unlink()
        self.link("alias", "two", directory=True)
        self.assertEqual(reviewed, subjects.refresh_subject(self.root, reviewed))
        self.assertEqual(subjects.path_subject(self.root, str(two)),
                         subjects.path_subject(self.root, str(alias / "file.txt")))

        parent = self.link(str(self.base / "parent-alias"), self.base, directory=True)
        through_parent = parent / self.root.name / "one/file.txt"
        self.assertEqual(expected, subjects.path_subject(self.root, str(through_parent)))

        outside = self.base / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside fixture", encoding="utf-8")
        self.link("outside", outside, directory=True)
        with self.assertRaisesRegex(CliError, "outside allowed boundary"):
            subjects.path_subject(self.root, "outside/file.txt")

    def test_directory_links_bind_original_name_and_in_repository_target_content(self):
        self.write("a.txt", "same")
        self.write("b.txt", "same")
        (self.root / "selected").mkdir()
        link = self.link("selected/old", "../a.txt")
        first = subjects.path_subject(self.root, "selected")
        self.assertEqual(1, first["file_count"])
        link = link.rename(link.with_name("new"))
        renamed = subjects.path_subject(self.root, "selected")
        self.assertNotEqual(first["digest"], renamed["digest"])
        link.unlink()
        self.link("selected/new", "../b.txt")
        retargeted = subjects.path_subject(self.root, "selected")
        self.assertNotEqual(renamed["digest"], retargeted["digest"])
        self.write("a.txt", "other file changed")
        self.assertEqual(retargeted, subjects.path_subject(self.root, "selected"))
        self.write("b.txt", "linked file changed")
        self.assertNotEqual(retargeted["digest"], subjects.path_subject(self.root, "selected")["digest"])

    def test_excluded_file_targets_keep_link_identity_without_being_read(self):
        self.write(".git/internal.txt")
        self.write(".upgrade/internal.txt")
        self.write("unselected/plan.json", self.saved_plan("first"))
        (self.root / "selected").mkdir()
        self.link("selected/git-entry", "../.git/internal.txt")
        self.link("selected/upgrade-entry", "../.upgrade/internal.txt")
        self.link("selected/saved-plan", "../unselected/plan.json")

        with mock.patch.object(subjects, "_read_regular_file", wraps=subjects._read_regular_file) as read:
            before = subjects.path_subject(self.root, "selected")
            self.write(".git/internal.txt", "excluded git target changed")
            self.write(".upgrade/internal.txt", "excluded upgrade target changed")
            self.write("unselected/plan.json", self.saved_plan("second"))
            self.assertEqual(before, subjects.path_subject(self.root, "selected"))
            read.assert_not_called()
        self.assertEqual(3, before["file_count"])

    def test_dangling_directory_link_is_rechecked_if_target_appears(self):
        (self.root / "selected").mkdir()
        self.link("selected/file", "../absent.txt")
        before = subjects.path_subject(self.root, "selected")
        self.write("absent.txt")
        self.assertNotEqual(before["digest"], subjects.path_subject(self.root, "selected")["digest"])
        self.link("selected/folder", "../unselected", directory=True)
        subjects.path_subject(self.root, "selected")
        self.write("unselected/data.txt")
        with self.assertRaisesRegex(CliError, "selected.*folder.*not visited"):
            subjects.path_subject(self.root, "selected")

    def test_control_directory_links_are_rejected_without_reading_targets(self):
        (self.root / "selected").mkdir()
        for target in (".git", ".upgrade", ".git/internal", ".upgrade/internal"):
            with self.subTest(target=target):
                self.write(f"{target}/data.txt")
                link = self.link("selected/alias", f"../{target}", directory=True)
                try:
                    with mock.patch.object(subjects, "_read_regular_file") as read:
                        with self.assertRaisesRegex(CliError, "selected.*alias.*control directory"):
                            subjects.path_subject(self.root, "selected")
                        read.assert_not_called()
                finally:
                    link.unlink()

    def test_covered_directory_link_binds_physical_target_and_empty_subdirectories(self):
        one = self.write("selected/one/run.txt", "same")
        two = self.write("selected/two/run.txt", "same")
        outside = self.base / "intermediate"
        self.link(outside, one.parent, directory=True)
        self.link("selected/alias", outside, directory=True)
        first = subjects.path_subject(self.root, "selected")
        self.assertEqual(3, first["file_count"])
        outside.unlink()
        self.link(outside, two.parent, directory=True)
        retargeted = subjects.path_subject(self.root, "selected")
        self.assertNotEqual(first["digest"], retargeted["digest"])
        empty = two.parent / "empty"
        empty.mkdir()
        with_empty = subjects.path_subject(self.root, "selected")
        self.assertNotEqual(retargeted["digest"], with_empty["digest"])
        self.assertEqual(retargeted["file_count"], with_empty["file_count"])
        empty.rmdir()
        self.assertEqual(retargeted, subjects.path_subject(self.root, "selected"))
        two.write_text("changed", encoding="utf-8")
        self.assertNotEqual(retargeted["digest"], subjects.path_subject(self.root, "selected")["digest"])

    def test_covered_directory_link_preserves_literal_backslash_names(self):
        if len(Path(r"dir\\name").parts) != 1:
            self.skipTest("literal backslash filenames are unsupported")
        self.write(r"selected/dir\\name/data.txt")
        self.write("selected/dir/name/data.txt")
        middle = self.link(self.base / "middle", self.root / r"selected/dir\\name", directory=True)
        self.link(r"selected/link\\name", middle, directory=True)
        first = subjects.path_subject(self.root, "selected")
        middle.unlink()
        self.link(middle, self.root / "selected/dir/name", directory=True)
        self.assertNotEqual(first["digest"], subjects.path_subject(self.root, "selected")["digest"])

    def test_directory_link_subtree_must_be_complete(self):
        target = self.write("selected/target/data.txt").parent
        self.link("selected/alias", "target", directory=True)
        subjects.path_subject(self.root, "selected")

        self.write("selected/target/plan.json", self.saved_plan("unbound"))
        with self.assertRaisesRegex(CliError, "selected.*alias.*excluded content"):
            subjects.path_subject(self.root, "selected")
        (target / "plan.json").unlink()

        self.link("selected/target/file-alias", "data.txt")
        with self.assertRaisesRegex(CliError, "selected.*alias.*contains a link"):
            subjects.path_subject(self.root, "selected")
        (target / "file-alias").unlink()

        self.link("selected/target/ancestor", "..", directory=True)
        with self.assertRaisesRegex(CliError, "contains a link"):
            subjects.path_subject(self.root, "selected")
        (target / "ancestor").unlink()

        # A root target would contain both the visible link and the skipped control entry.
        self.write(".upgrade/hidden/data.txt")
        self.link("root-alias", ".", directory=True)
        with self.assertRaisesRegex(CliError, "root-alias.*excluded content"):
            subjects.path_subject(self.root, ".")

    def test_target_not_actually_visited_is_rejected(self):
        selected = self.write("selected/target/data.txt").parent.parent
        self.link("selected/alias", "target", directory=True)
        with mock.patch.object(subjects.os, "walk", return_value=iter([(selected, ["alias", "target"], [])])):
            with self.assertRaisesRegex(CliError, "selected.*alias.*not visited"):
                subjects.path_subject(self.root, "selected")

    def test_scan_and_read_failures_are_controlled(self):
        data = self.write("selected/target/data.txt")
        self.link("selected/alias", "target", directory=True)
        original_scandir, original_open = os.scandir, Path.open

        def restricted_scan(path):
            if Path(path) == data.parent:
                raise PermissionError(errno.EACCES, "fixture directory denied", path)
            return original_scandir(path)

        def restricted_open(path, *args, **kwargs):
            if path == data:
                raise PermissionError(errno.EACCES, "fixture file denied", path)
            return original_open(path, *args, **kwargs)

        with mock.patch.object(subjects.os, "scandir", restricted_scan):
            with self.assertRaisesRegex(CliError, "cannot scan subject directory.*target.*fixture directory denied"):
                subjects.path_subject(self.root, "selected")
        with mock.patch.object(Path, "open", restricted_open):
            with self.assertRaisesRegex(CliError, "cannot read subject file.*data.txt.*fixture file denied"):
                subjects.path_subject(self.root, "selected")

    def test_directory_rejects_outside_file_folder_and_dangling_link(self):
        (self.root / "selected").mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        target = outside / "file.txt"
        target.write_text("outside fixture", encoding="utf-8")
        actual_open = Path.open

        def open_inside_root(path, *args, **kwargs):
            self.assertNotIn(outside, path.parents, f"read outside the repository: {path}")
            return actual_open(path, *args, **kwargs)

        for title, destination, folder in (
            ("file", target, False), ("directory", outside, True), ("dangling", outside / "missing", False)
        ):
            with self.subTest(kind=title), mock.patch.object(Path, "open", open_inside_root):
                link = self.link("selected/alias", destination, directory=folder)
                try:
                    with self.assertRaisesRegex(CliError, "link target resolves outside allowed boundary"):
                        subjects.path_subject(self.root, "selected")
                finally:
                    link.unlink()

    def test_non_regular_link_target_has_controlled_error(self):
        self.write("target.txt")
        (self.root / "selected").mkdir()
        self.link("selected/alias", "../target.txt")
        actual_stat = Path.stat

        def with_nonregular_target(path, *args, **kwargs):
            if path == self.root / "target.txt":
                return SimpleNamespace(st_mode=stat.S_IFIFO)
            return actual_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", with_nonregular_target):
            with self.assertRaisesRegex(CliError, "link target is not a regular file or directory"):
                subjects.path_subject(self.root, "selected")

    def test_unresolvable_link_has_controlled_error(self):
        (self.root / "selected").mkdir()
        link = self.link("selected/alias", "alias")
        with self.assertRaisesRegex(CliError, "cannot fingerprint subject link.*alias"):
            subjects.path_subject(self.root, "selected")
        actual_resolve = Path.resolve

        def with_unresolvable_link(path, *args, **kwargs):
            if path == link:
                raise OSError("unresolvable fixture")
            return actual_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", with_unresolvable_link):
            with self.assertRaisesRegex(CliError, "cannot fingerprint subject link.*unresolvable fixture"):
                subjects.path_subject(self.root, "selected")

    def test_junction_tag_does_not_make_unvisited_directory_eligible(self):
        (self.root / "selected").mkdir()
        junction = self.write("selected/junction/child.txt").parent
        actual_lstat = Path.lstat

        def with_junction_tag(path, *args, **kwargs):
            if path == junction:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
            return actual_lstat(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", with_junction_tag), \
             mock.patch.object(subjects.os, "readlink", return_value="target") as readlink:
            with self.assertRaisesRegex(CliError, "junction.*not visited"):
                subjects.path_subject(self.root, "selected")
            readlink.assert_called_once_with(junction)
            with self.assertRaisesRegex(CliError, "path ends at a junction"):
                subjects.path_subject(self.root, "selected/junction")

    @unittest.skipUnless(os.name == "nt", "native Windows junctions require Windows")
    def test_real_windows_junction_is_bound_without_recursion(self):
        selected = self.root / "selected"
        selected.mkdir()
        target = self.write("target/child.txt").parent
        junction = selected / "junction"
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                                 capture_output=True, check=False)
        if command.returncode:
            self.skipTest(f"directory junction creation failed: {command.stderr!r}")
        self.addCleanup(junction.rmdir)
        with self.assertRaisesRegex(CliError, "junction.*not visited"):
            subjects.path_subject(self.root, "selected")
        before = subjects.path_subject(self.root, ".")
        self.assertEqual(2, before["file_count"])
        self.write("target/child.txt", "changed inside junction target")
        self.assertNotEqual(before["digest"], subjects.path_subject(self.root, ".")["digest"])
        with self.assertRaisesRegex(CliError, "path ends at a junction"):
            subjects.path_subject(self.root, "selected/junction")

    def test_directory_order_has_a_case_sensitive_tiebreaker(self):
        selected = self.root / "selected"
        selected.mkdir()
        directory_names = ["aDir", "ADir", "Z"]
        file_names = ["a.txt", "A.txt", "Z.txt"]

        def snapshot(reverse):
            def walk(*args, **kwargs):
                dirs = directory_names[::-1] if reverse else directory_names.copy()
                files = file_names[::-1] if reverse else file_names.copy()
                yield str(selected), dirs, files
                for name in dirs:
                    yield str(selected / name), [], ["child.txt"]

            def describe(path):
                return {"kind": "file", "sha256": path.relative_to(selected).as_posix()}

            with mock.patch.object(subjects.os, "walk", walk), \
                 mock.patch.object(subjects, "_read_regular_file", describe):
                return subjects.path_subject(self.root, "selected")

        ordered = ["A.txt", "a.txt", "Z.txt", "ADir/child.txt", "aDir/child.txt", "Z/child.txt"]
        expected = digest({"root": "selected", "entries": [
            {"path": name, "kind": "file", "sha256": name} for name in ordered
        ]})
        self.assertEqual(snapshot(False), snapshot(True))
        self.assertEqual(expected, snapshot(True)["digest"])

    def test_retargeting_invalidates_approved_delivery_and_execution_evidence(self):
        self.call("initialize", "--profile", "lean")
        self.write("selected/a.txt", "same")
        self.write("selected/b.txt", "same")
        link = self.link("selected/alias", "a.txt")
        self.call("validation-record", "--check", "suite", "--status", "passed", "--subject-path", "selected")
        delivery = self.call("delivery-review", "--gate", "readiness", "--target", "release",
                             "--require-check", "suite", "--subject-path", "selected", "--plan-out", "delivery.json")
        self.assertTrue(delivery["result"]["eligible"])
        link.unlink()
        self.link("selected/alias", "b.txt")
        rejected = self.call("delivery-apply", "--plan", str(self.root / "delivery.json"),
                             "--approve", delivery["plan_id"], expected=3)
        self.assertIn("delivery subject changed", rejected["errors"][0])
        self.assertEqual([], rejected["changed"])

        change = 'from pathlib import Path\np = Path("selected/alias")\np.unlink()\np.symlink_to("a.txt")'
        recorded = self.call("validation-record", "--check", "suite", "--subject-path", "selected",
                             "--execute", sys.executable, "-c", change)["result"]["record"]
        self.assertEqual(0, recorded["exit_code"])
        self.assertFalse(recorded["execution"]["subject_unchanged"])
        self.assertEqual("failed", recorded["status"])

    def test_execution_and_delivery_use_common_physical_root(self):
        self.call("initialize", "--profile", "lean")
        (self.root / "selected").mkdir()
        target = self.write("shared/data.txt", "version 1\n")
        link = self.link("selected/linked", "../shared", directory=True)
        records = self.root / ".upgrade/validation/records.jsonl"
        validate = ("validation-record", "--check", "suite", "--subject-path")
        read = "from pathlib import Path; print(Path('selected/linked/data.txt').read_text())"
        should_not_run = "from pathlib import Path; Path('ran.txt').touch()"
        rejected = self.call(*validate, "selected", "--execute", sys.executable, "-c", should_not_run, expected=2)
        self.assertIn("selected", rejected["errors"][0])
        self.assertIn("common physical directory", rejected["errors"][0])
        self.assertFalse((self.root / "ran.txt").exists())
        self.assertFalse(records.exists())

        plan = ".upgrade/delivery-plan.json"
        review_args = ("delivery-review", "--gate", "readiness", "--target", "fixture",
                       "--require-check", "suite", "--subject-path", ".", "--plan-out", plan)
        apply_args = ("delivery-apply", "--plan", str(self.root / plan), "--approve")
        record = self.call(*validate, ".", "--request-id", "first", "--execute", sys.executable, "-c", read)
        self.assertEqual("passed", record["result"]["record"]["status"])
        review = self.call(*review_args)
        self.assertTrue(review["result"]["eligible"])
        target.write_text("version 2\n", encoding="utf-8")
        rejected = self.call(*apply_args, review["plan_id"], expected=3)
        self.assertIn("delivery subject changed", rejected["errors"][0])
        self.assertEqual([], rejected["changed"])
        self.assertEqual(0, self.call("delivery-audit")["result"]["count"])

        record = self.call(*validate, ".", "--request-id", "second", "--execute", sys.executable, "-c", read)
        self.assertEqual("passed", record["result"]["record"]["status"])
        (self.root / plan).unlink()
        review = self.call(*review_args)
        self.assertTrue(review["result"]["eligible"])
        self.assertEqual("accepted", self.call(*apply_args, review["plan_id"])["result"]["delivery"]["outcome"])

        with self.subTest("unsupported subject after execution"):
            before = records.read_bytes()
            link_path = link.relative_to(self.root).as_posix()
            change = ("from pathlib import Path; "
                      f"p = Path({link_path!r}); p.unlink(); p.symlink_to('../.upgrade', target_is_directory=True)")
            rejected = self.call(*validate, ".", "--request-id", "third",
                                 "--execute", sys.executable, "-c", change, expected=2)
            self.assertIn("control directory", rejected["errors"][0])
            self.assertEqual(before, records.read_bytes())

    def test_phase_exit_rejects_stale_or_uncomputable_directory_subject(self):
        self.call("initialize", "--profile", "lean")
        selected = self.write("selected/physical/data.txt")
        self.link("selected/alias", "physical", directory=True)
        policy = self.call("delivery-policy-review", "--readiness-check", "suite", "--phase-check", "1=suite",
                           "--plan-out", ".upgrade/policy-plan.json")
        self.call("delivery-policy-apply", "--plan", str(self.root / ".upgrade/policy-plan.json"),
                  "--approve", policy["plan_id"])
        self.call("phase-update", "--phase", "1", "--current-task", "work")
        self.call("validation-record", "--check", "suite", "--status", "passed", "--subject-path", "selected")
        args = ("phase-update", "--phase", "2", "--current-task", "next", "--complete-previous",
                "--completion-note", "accepted", "--subject-path", "selected")
        phase = self.root / ".upgrade/stages/phase-1.md"
        before = phase.read_bytes()
        selected.write_text("updated", encoding="utf-8")
        stale = self.call(*args, expected=2)
        self.assertEqual([], stale["changed"])
        self.assertTrue(any("no evidence" in reason for reason in stale["errors"]), stale)
        self.write("selected/physical/review.json", self.saved_plan("unbound"))
        unsupported = self.call(*args, expected=2)
        self.assertEqual([], unsupported["changed"])
        self.assertIn("excluded content", unsupported["errors"][0])
        self.assertEqual(before, phase.read_bytes())
        self.assertFalse((self.root / ".upgrade/stages/phase-2.md").exists())

    @staticmethod
    def saved_plan(value):
        plan = {"schema_version": PLAN_SCHEMA_VERSION, "kind": "collect", "entries": [], "fixture": value}
        return json.dumps({**plan, "plan_id": digest(plan)})
