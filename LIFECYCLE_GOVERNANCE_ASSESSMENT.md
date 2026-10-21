# Lifecycle Governance Assessment

Date: 2026-10-21

## Overall assessment

The Skill has broad lifecycle governance coverage in its policy and routing documents, but the current implementation is not yet release-ready on Windows. The primary blocking defect is in the durable atomic-write path: initialization and dependent lifecycle workflows fail during finalization because the active Windows Python runtime does not provide `os.fchmod`.

## Covered controls

- Artifact classification, registration policy, collection, cleanup, archive, recoverable trash, permanent purge, and Git ignore governance are defined.
- Mutations use a review, exact `plan_id`, apply-time revalidation workflow.
- Protected and sensitive material requires exact-path authorization at review and apply time.
- Registered governance, required, and permanent artifacts are protected from ordinary cleanup.
- Cleanup deletion is recoverable under `.upgrade/tmp/.trash/`; permanent deletion is a separate purge workflow.
- Path normalization, symlink/junction escape checks, transaction journals, stale-plan checks, and maintenance evidence are specified.
- Tests cover collision-safe destinations, sensitive authorization, forged plans, recovery, cleanup, and purge behavior.

## Blocking finding

`core/durability.py` called `os.fchmod()` unconditionally when preserving an existing file mode. On Windows this raises:

```text
AttributeError: module 'os' has no attribute 'fchmod'
```

The failure occurs while refreshing `MANIFEST.md` during `initialize`, causing a partial result and cascading failures for lifecycle tests and commands that require a valid managed workspace.

## Validation evidence before repair

The full test suite ran 220 tests and reported 139 failures and 1 error. The failures were dominated by initialization finalization errors from the missing Windows `fchmod` API.

## Priority actions

1. Replace the unconditional `os.fchmod()` call with a cross-platform mode-preservation path, using descriptor-based chmod where available and path-based `os.chmod()` on Windows.
2. Re-run the durability tests and initialization/lifecycle acceptance tests on Windows.
3. Reconcile source, metadata, and distributable Skill versions; the workspace contains both 18.0.0 source metadata and a `project-upgrade-maintainer-v1.0.0.skill` archive.
4. Validate a real initialized target project with `bootstrap-review`, `initialize`, `artifact-audit`, `git-audit`, and `health-check --strict`.

## Scope limitation

This assessment evaluates the Skill source repository and implementation. It does not certify lifecycle state for a separate target project until that project has an initialized `.upgrade/` workspace and the lifecycle audits have been run there.

## Windows validation after repair

Date: 2026-10-21. The atomic-write implementation now uses descriptor-based `fchmod` when available and path-based `chmod` on the temporary file otherwise, before atomic publication.

- Full unit/integration suite after repair: 220 tests, passed, 8 skipped.
- Re-run durability, initialization/idempotence, recoverable cleanup followed by permanent purge, and sensitive collection authorization: 15 tests, passed, 1 skipped.
- Command modules, mutation command edges, and release hardening: 17 tests, passed, 1 skipped.
- `python tests/e2e_acceptance.py`: passed. Its reported coverage includes the release contract, adaptive workspace, artifact policy linking, legacy adoption, stale plans, delivery gates/policy, authority network, CI health, and transaction durability.

These results clear the observed Windows `os.fchmod` blocker. Skipped tests are not counted as validated platform behavior. The validation used temporary test workspaces and does not constitute acceptance of a separate production project.

## Version and release artifact reconciliation

后续调用冗余修复已重建本地包；本节摘要是当时的历史构建信息。当前包及验证请以 WORKFLOW_EFFICIENCY_FIXES.md 和 RELEASE_PLAN.md 为准。

On 2026-10-21, the user selected version 18.0.1 and authorized deletion of the misnamed workspace-root `project-upgrade-maintainer-v1.0.0.skill` archive. That exact archive was deleted without using the recycle bin. Existing 18.0.0 archives in `dist/` and `.codex-tmp/` were initially retained as previous build outputs; the subsequent user-requested cleanup removed those obsolete local copies. See CLEANUP_AND_RELEASE_CHECK.md for the current inventory.

The runtime version, Skill metadata, README build example, release plan, version assertions, and E2E expectations now use 18.0.1. CHANGELOG.md records the Windows fix while retaining the historical 18.0.0 entry. Policy 14 and Plan Schema 3 remain unchanged.

Current artifact: `dist/project-upgrade-maintainer-18.0.1.skill`.

SHA-256: `f80d2f59cefb1f0d5c6f6a5a00b1618d0455b0af617a461f09127b78a8ea6334`.

All 88 archive files match the current runtime source. ZIP integrity, runtime-only contents, version metadata, and byte-for-byte reproducibility were verified. After the version update, the full suite ran 220 tests successfully (8 skipped), and E2E acceptance passed. No Git commit, tag, push, or public release was performed.
