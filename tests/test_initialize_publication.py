"""Failure and retry boundaries for publishing the first managed workspace."""

import contextlib
import errno
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.main import run
from commands import bootstrap
from core import workspace
from core.artifacts import artifact_registry_digest, load_artifact_registry
from core.authority import authority_registry_digest, load_authority_registry
from core.config import load_workspace_config
from core.constants import GUARD_START, MODULE_ORDER


class InitializePublicationCase(unittest.TestCase):
    CUSTOM = ('--profile', 'custom', '--module', 'validation')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def project(self, name):
        root = self.base / name
        root.mkdir()
        return root

    def cli(self, root, *args, expected=0, writer=workspace.atomic_write):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(['--root', str(root), *args], atomic_write_fn=writer)
        self.assertEqual(expected, code, out.getvalue() + err.getvalue())
        return json.loads(out.getvalue())

    def assert_config(self, root, profile):
        found = load_workspace_config(root / '.upgrade')
        scope = ('core', 'planning') if profile == 'lean' else ('core', 'validation')
        self.assertEqual(profile, found['initial_profile'])
        self.assertEqual(list(scope), found['active_modules'])
        self.assertEqual('safe-default' if profile == 'lean' else 'explicit', found['selection_source'])
        for name in ('artifact_registry_sha256', 'authority_registry_sha256'):
            self.assertRegex(found[name], r'^[0-9a-f]{64}$')
        return found

    def assert_completed(self, root, profile):
        upgrade = root / '.upgrade'
        config = self.assert_config(root, profile)
        self.assertEqual(artifact_registry_digest(load_artifact_registry(upgrade)), config['artifact_registry_sha256'])
        self.assertEqual(authority_registry_digest(load_authority_registry(upgrade)), config['authority_registry_sha256'])
        manifest = (upgrade / 'MANIFEST.md').read_text(encoding='utf-8')
        self.assertIn(', '.join(config['active_modules']), manifest)
        latest = upgrade / 'traces' / 'last-maintenance.md'
        self.assertIn('Upgrade Maintenance Report', latest.read_text(encoding='utf-8'))
        self.assertFalse(self.cli(root, 'health-check', '--ci')['errors'])
        before = {path.name: path.read_bytes() for path in latest.parent.glob('*.md')}
        self.assertEqual([], self.cli(root, 'initialize')['changed'])
        self.assertEqual(before, {path.name: path.read_bytes() for path in latest.parent.glob('*.md')})

    def test_prepublication_failures_leave_no_workspace_or_bridge(self):
        for profile, args in (('lean', ()), ('custom', self.CUSTOM)):
            for fault in ('before-config', 'after-config', 'rename'):
                with self.subTest(profile=profile, fault=fault):
                    root = self.project(f'{profile}-{fault}')

                    def writer(path, text, **kwargs):
                        self.assertEqual('CONFIG.json', path.name)
                        self.assertEqual('.upgrade', path.parent.name)
                        self.assertTrue(path.parent.parent.name.startswith(bootstrap._INITIALIZE_STAGE_PREFIX))
                        self.assertFalse((root / '.upgrade').exists())
                        if fault == 'before-config':
                            raise OSError(fault)
                        result = workspace.atomic_write(path, text, **kwargs)
                        if fault == 'after-config':
                            self.assertTrue(path.exists())
                            raise OSError(fault)
                        return result

                    with contextlib.ExitStack() as stack:
                        if fault == 'rename':
                            rename = stack.enter_context(mock.patch.object(Path, 'rename', side_effect=OSError(errno.EXDEV, fault)))
                        result = self.cli(root, 'initialize', *args, '--create-root-bridge', expected=2, writer=writer)
                        if fault == 'rename':
                            rename.assert_called_once_with(root / '.upgrade')
                    self.assertEqual([], result['changed'])
                    self.assertIn(fault, result['errors'][0])
                    self.assertEqual([], list(root.iterdir()))
                    self.cli(root, 'initialize', *args, '--create-root-bridge')
                    self.assert_completed(root, profile)

    def test_unpublished_custom_intent_must_be_supplied_on_retry(self):
        root = self.project('unpublished-custom')
        with mock.patch.object(Path, 'rename', side_effect=OSError('rename failed')):
            self.cli(root, 'initialize', *self.CUSTOM, expected=2)
        self.cli(root, 'initialize')
        self.assert_completed(root, 'lean')

    def test_dangling_new_destination_appearing_after_config_is_preserved(self):
        root = self.project('publication-conflict')
        target = root / '.upgrade'
        probe = root / 'probe'
        try:
            probe.symlink_to('missing', target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f'symbolic links are unavailable: {exc}')
        probe.unlink()

        def writer(path, text, **kwargs):
            written = workspace.atomic_write(path, text, **kwargs)
            target.symlink_to('missing', target_is_directory=True)
            return written

        blocked = self.cli(root, 'initialize', expected=2, writer=writer)
        self.assertIn('.upgrade appeared', blocked['errors'][0])
        self.assertEqual([], blocked['changed'])
        self.assertTrue(target.is_symlink())
        self.assertEqual([target], list(root.iterdir()))

    def test_postpublication_failures_keep_config_then_allow_no_argument_retry(self):
        for profile, args in (('lean', ()), ('custom', self.CUSTOM)):
            for fault in ('sync-target-parent', 'sync-source-parent', 'template', 'bridge', 'latest'):
                with self.subTest(profile=profile, fault=fault):
                    root = self.project(f'{profile}-{fault}')
                    upgrade = root / '.upgrade'
                    config_path = upgrade / 'CONFIG.json'
                    bridge = root / 'AGENTS.md'
                    destinations = {'template': upgrade / 'STATE.md', 'bridge': bridge,
                                    'latest': upgrade / 'traces' / 'last-maintenance.md'}
                    synchronization = bootstrap.fsync_directory
                    config_writes = []
                    parent_syncs = []

                    def writer(path, text, **kwargs):
                        if path.name == 'CONFIG.json':
                            config_writes.append(path)
                        if path == destinations.get(fault):
                            raise OSError(fault)
                        return workspace.atomic_write(path, text, **kwargs)

                    def sync_parent(path):
                        parent_syncs.append(path)
                        self.assertTrue(config_path.is_file())
                        if (fault == 'sync-target-parent' and path == root
                                or fault == 'sync-source-parent' and path != root):
                            raise OSError(fault)
                        return synchronization(path)

                    with mock.patch.object(bootstrap, 'fsync_directory', side_effect=sync_parent):
                        failed = self.cli(root, 'initialize', *args, '--create-root-bridge', expected=4, writer=writer)
                    self.assertEqual('partial', failed['status'])
                    self.assertTrue(any(fault in message for message in failed['errors']))
                    self.assertEqual(['.upgrade', '.upgrade/CONFIG.json'], [x['path'] for x in failed['changed'][:2]])
                    self.assertEqual(root, parent_syncs[0])
                    if fault != 'sync-target-parent':
                        self.assertTrue(parent_syncs[1].name.startswith(bootstrap._INITIALIZE_STAGE_PREFIX))
                    self.assertEqual(1, len(config_writes))
                    self.assertNotEqual(config_path, config_writes[0])
                    self.assertFalse(list(root.glob(f'{bootstrap._INITIALIZE_STAGE_PREFIX}*')))
                    self.assertEqual(fault == 'latest', bridge.exists())
                    self.assert_config(root, profile)
                    config_before = config_path.read_bytes()
                    history = {path.name: path.read_bytes() for path in (upgrade / 'traces').glob('*.md')}
                    repaired = self.cli(root, 'initialize')
                    self.assertEqual(config_before, config_path.read_bytes())
                    self.assertEqual(fault == 'latest', bridge.exists())
                    if fault == 'latest':
                        self.assertTrue(repaired['changed'])
                        self.assertTrue(all(change['path'].startswith('.upgrade/traces/') for change in repaired['changed']))
                        for name, data in history.items():
                            self.assertEqual(data, (upgrade / 'traces' / name).read_bytes())
                        self.assertIn('## Changed\n\n- None.', destinations['latest'].read_text(encoding='utf-8'))
                    else:
                        self.cli(root, 'initialize', '--create-root-bridge')
                        self.assertTrue(bridge.is_file())
                    self.assert_completed(root, profile)

    def test_retry_transaction_can_recover_before_initialize_finishes(self):
        root = self.project('transactional-retry')
        with mock.patch.object(bootstrap, 'fsync_directory', side_effect=OSError('after publication')):
            self.cli(root, 'initialize', *self.CUSTOM, expected=4)

        real_write = workspace.atomic_write_bytes

        def fail_latest(path, *args, **kwargs):
            if path.name == 'last-maintenance.md':
                raise OSError('after prepare')
            return real_write(path, *args, **kwargs)

        with mock.patch.object(workspace, 'atomic_write_bytes', side_effect=fail_latest):
            self.cli(root, 'initialize', expected=4)
        blocked = self.cli(root, 'initialize', expected=2)
        self.assertIn('unresolved transaction', blocked['errors'][0])
        unresolved = self.cli(root, 'recovery-audit')['result']['transactions']
        self.assertEqual(1, len(unresolved))
        self.assertTrue(unresolved[0]['recoverable'])
        review = self.cli(root, 'recovery-review', '--transaction-id', unresolved[0]['transaction_id'],
                          '--plan-out', '.upgrade/recovery.json')
        self.cli(root, 'recovery-apply', '--plan', str(root / '.upgrade/recovery.json'), '--approve', review['plan_id'])
        self.cli(root, 'initialize')
        self.assert_completed(root, 'custom')

    def test_cleanup_failure_after_rename_preserves_configuration(self):
        root = self.project('cleanup-error')
        with mock.patch.object(tempfile.TemporaryDirectory, 'cleanup', side_effect=OSError('cleanup failure')):
            partial = self.cli(root, 'initialize', *self.CUSTOM, expected=4)
        self.assertIn('published', partial['errors'][0])
        self.assertEqual(['.upgrade', '.upgrade/CONFIG.json'], [item['path'] for item in partial['changed']])
        self.assert_config(root, 'custom')
        self.cli(root, 'initialize')
        self.assert_completed(root, 'custom')

    def test_read_only_dry_run_and_preflight(self):
        root = self.project('preflight')
        guarded = root / 'AGENTS.md'
        guarded.write_text(GUARD_START, encoding='utf-8')
        self.cli(root, 'bootstrap-review')
        self.assertEqual([], self.cli(root, 'initialize', expected=2)['changed'])
        self.assertEqual([guarded], list(root.iterdir()))
        guarded.unlink()
        dry = self.cli(root, 'initialize', *self.CUSTOM, '--create-root-bridge', '--dry-run')
        self.assertEqual('custom', dry['result']['workspace']['initial_profile'])
        self.assertIn('.upgrade/CONFIG.json', [item['path'] for item in dry['changed']])
        self.assertEqual([], list(root.iterdir()))

    def test_private_staged_workspace_is_pruned_but_same_prefix_bridge_is_processed(self):
        root = self.project('private-leftovers')
        container = root / '.upgrade-initialize-leftover'
        staged = container / '.upgrade'
        ordinary = root / '.upgrade-initialize-project'
        for directory in (staged, ordinary):
            directory.mkdir(parents=True)
        (staged / 'AGENTS.md').write_text(GUARD_START, encoding='utf-8')
        (staged / 'CONFIG.json').write_bytes(b'invalid staged configuration\n')
        user_bridge = ordinary / 'AGENTS.md'
        original = b'Project rules\n'
        user_bridge.write_bytes(original)
        before = {path.name: path.read_bytes() for path in staged.iterdir()}
        scandir = os.scandir
        path_open = Path.open

        def guard_scan(path):
            if isinstance(path, (str, os.PathLike)):
                current = Path(path)
                self.assertNotEqual(staged, current, 'staged directory must not be enumerated')
                self.assertNotIn(staged, current.parents)
            return scandir(path)

        def guard_read(path, *args, **kwargs):
            self.assertNotIn(staged, path.parents, 'staged files must not be read')
            return path_open(path, *args, **kwargs)

        with mock.patch.object(os, 'scandir', side_effect=guard_scan), \
             mock.patch.object(Path, 'open', guard_read):
            dry = self.cli(root, 'initialize', *self.CUSTOM, '--sync-nested-bridges', '--dry-run')
            relative = user_bridge.relative_to(root).as_posix()
            self.assertIn({'path': relative, 'action': 'would_update'}, dry['changed'])
            self.assertEqual(original, user_bridge.read_bytes())
            self.assertEqual({container, ordinary}, set(root.iterdir()))
            initialized = self.cli(root, 'initialize', '--sync-nested-bridges')
            self.assertIn({'path': relative, 'action': 'updated'}, initialized['changed'])
            self.assertFalse(any(item['path'].startswith(container.name + '/') for item in initialized['changed']))

        content = user_bridge.read_text(encoding='utf-8')
        self.assertIn(original.decode().strip(), content)
        self.assertIn(GUARD_START, content)
        self.assertEqual(before, {path.name: path.read_bytes() for path in staged.iterdir()})
        self.assert_config(root, 'lean')

    def test_same_prefix_bridge_conflict_blocks_dry_run_and_real_publication(self):
        root = self.project('same-prefix-conflict')
        ordinary = root / '.upgrade-initialize-project'
        ordinary.mkdir()
        user_bridge = ordinary / 'AGENTS.md'
        original = (GUARD_START + '\nProject rules\n').encode()
        user_bridge.write_bytes(original)
        for dry_run in ((), ('--dry-run',)):
            with self.subTest(dry_run=bool(dry_run)):
                conflict = self.cli(root, 'initialize', *self.CUSTOM, '--create-root-bridge',
                                    '--sync-nested-bridges', *dry_run, expected=2)
                self.assertIn('managed marker conflict', conflict['errors'][0])
                self.assertEqual([], conflict['changed'])
                self.assertEqual([ordinary], list(root.iterdir()))
                self.assertEqual([user_bridge], list(ordinary.iterdir()))
                self.assertEqual(original, user_bridge.read_bytes())

    def test_legacy_empty_fixed_footprint_and_schema_migration(self):
        root = self.project('empty')
        (root / '.upgrade').mkdir()
        legacy = self.cli(root, 'initialize')['result']['workspace']
        self.assertEqual(('legacy-governed', 'legacy-adopted', list(MODULE_ORDER)),
                         (legacy['initial_profile'], legacy['selection_source'], legacy['active_modules']))
        root = self.project('existing')
        upgrade = root / '.upgrade'
        upgrade.mkdir()
        user = upgrade / 'STATE.md'
        user.write_bytes(b'original user state\n')
        adopted = self.cli(root, 'initialize', *self.CUSTOM)['result']['workspace']
        self.assertEqual('explicit-legacy-adoption', adopted['selection_source'])
        old_config = load_workspace_config(upgrade)
        old_config['schema_version'] = 1
        old_config.pop('authority_registry_sha256')
        (upgrade / 'CONFIG.json').write_text(json.dumps(old_config), encoding='utf-8')
        self.cli(root, 'initialize')
        current = load_workspace_config(upgrade)
        self.assertEqual(2, current['schema_version'])
        self.assertEqual('explicit-legacy-adoption', current['selection_source'])
        self.assertEqual(['core', 'validation'], current['active_modules'])
        self.assertEqual(b'original user state\n', user.read_bytes())

    def test_nonempty_registry_requires_existing_config_provenance(self):
        root = self.project('unbound-registry')
        self.cli(root, 'initialize')
        review = self.cli(root, 'artifact-policy-review', '--path', 'security/SECURITY_REVIEW.md',
                          '--create', '--plan-out', '.upgrade/artifact.json')
        self.cli(root, 'artifact-policy-apply', '--plan', str(root / '.upgrade/artifact.json'),
                 '--approve', review['plan_id'])
        upgrade = root / '.upgrade'
        registry = upgrade / 'ARTIFACTS.json'
        before = registry.read_bytes()
        (upgrade / 'CONFIG.json').unlink()
        blocked = self.cli(root, 'initialize', expected=2)
        self.assertIn('non-empty ARTIFACTS.json without managed config provenance', blocked['errors'][0])
        self.assertEqual([], blocked['changed'])
        self.assertEqual(before, registry.read_bytes())
        self.assertFalse((upgrade / 'CONFIG.json').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX directory modes required')
    def test_workspace_permissions_match_ordinary_mkdir(self):
        root = self.project('mode')
        sibling = root / 'ordinary'
        sibling.mkdir()
        self.cli(root, 'initialize')
        self.assertEqual(stat.S_IMODE(sibling.stat().st_mode), stat.S_IMODE((root / '.upgrade').stat().st_mode))

    def test_concurrent_first_initialize_does_not_publish_another_selection(self):
        root = self.project('concurrent')
        code = (
            'import sys\n'
            'from cli.main import run\n'
            'from core.workspace import atomic_write\n'
            'def writer(path, text, **kwargs):\n'
            '    if path.name == "CONFIG.json":\n'
            '        print("staged", flush=True)\n'
            '        sys.stdin.readline()\n'
            '    return atomic_write(path, text, **kwargs)\n'
            'raise SystemExit(run(["--root", sys.argv[1], "initialize", "--profile", "custom", '
            '"--module", "validation"], atomic_write_fn=writer))'
        )
        with subprocess.Popen([sys.executable, '-B', '-c', code, str(root)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as first:
            try:
                self.assertEqual('staged', first.stdout.readline().strip())
                self.assertFalse((root / '.upgrade').exists())
                rejected = self.cli(root, 'initialize', expected=2)
                self.assertIn('repository guard', rejected['errors'][0])
                self.assertFalse((root / '.upgrade').exists())
                out, err = first.communicate('\n', timeout=15)
                self.assertEqual(0, first.returncode, err + out)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.communicate(timeout=10)
        self.assert_completed(root, 'custom')
