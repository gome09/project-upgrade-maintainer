# Public Release Plan

Repository: https://github.com/gome09/project-upgrade-maintainer.git
Remote transport: SSH (`git@github.com:gome09/project-upgrade-maintainer.git`)
Release version: 18.0.1

## Checklist

- [x] Initialize the local Git repository and configure the GitHub remote.
- [x] Add public-repository ignore rules and release metadata.
- [x] Consolidate GitHub Actions into one workflow.
- [x] Fix the Windows durable-copy `Bad file descriptor` failure.
- [x] Make platform-specific durability tests pass on supported CI platforms.
- [x] Run the complete unit/integration test suite.
- [x] Run the E2E acceptance suite.
- [x] Build and inspect the reproducible `.skill` archive.
- [ ] Commit all release and compatibility fixes.
- [ ] Push `main` to GitHub over SSH.
- [ ] Create and push tag `v18.0.1`.
- [ ] Create the GitHub Release and attach the `.skill` archive.
- [ ] Verify GitHub Actions and the published release.

## Release evidence

Local validation for the Windows durability fix: 220 tests ran successfully, 8 skipped; E2E acceptance passed. Validation is repeated after the 18.0.1 metadata update and recorded in LIFECYCLE_GOVERNANCE_ASSESSMENT.md.

Release artifact: `dist/project-upgrade-maintainer-18.0.1.skill`.

The archive contains 88 runtime files matching the current source and excludes tests/.github/cache files. ZIP integrity and byte-for-byte reproducibility were verified.

SHA-256: `72e77302e19db05ed2eddced7dc9e08be1550bc0391e3263c3e8547d980d5d6f`.

After workflow-efficiency fixes: 230 tests ran successfully (8 skipped), E2E acceptance passed, Skill validation passed, and the rebuilt 88-file archive matches runtime source and is reproducible. See WORKFLOW_EFFICIENCY_FIXES.md.

After PID-probe and first-initialization locking fixes: 238 tests ran successfully (9 skipped on Windows), E2E acceptance and Skill validation passed. The current 88-file archive matches runtime source; ZIP integrity and byte-for-byte reproducibility were verified. No Git commit, tag, push, or public release was performed.

After the four remaining governance/engineering fixes: the full Windows suite ran
251 tests, OK (10 skipped). E2E acceptance and Skill validation passed. New tests
cover opt-in phase exit controls, manual versus local execution evidence, age
expiry at apply, timeout/failure/subject mutation, execution retry, submodule
gitlinks/dirty/uninitialized state, executable index modes, and unmerged indexes.
POSIX executable-bit behavior is covered by a platform-specific test skipped on
Windows. The current 18.0.1 archive contains 88 files matching runtime source;
ZIP integrity and byte-for-byte reproducibility passed. Ubuntu/Windows CI is
configured but has not been run remotely. No commit, tag, push, or publication.

## Channel check and local cleanup (2026-10-21)

The GitHub SSH origin remains configured. GitHub's latest-release query returned
`v18.0.0` with the 18.0.0 Skill asset, not the current local 18.0.1 package.
Remote main resolves to `160e146d154f63a9f8d835d9ef65681c846b0039`;
the current changes remain uncommitted. The workflow uploads Actions artifacts
but does not create GitHub Releases automatically. SSH read-access verification
was blocked by permission to read the local known_hosts file; push permission
has not been verified. Some GitHub CLI API calls also returned HTTP 401.

Obsolete local 18.0.0 archives, stale scratch notes/logs, and bytecode caches were
removed at the user's request. Current package contents and SHA-256 still match
the 88 runtime source files. See CLEANUP_AND_RELEASE_CHECK.md.
