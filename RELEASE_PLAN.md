# Public Release Plan

Repository: https://github.com/gome09/project-upgrade-maintainer.git
Remote transport: SSH (`git@github.com:gome09/project-upgrade-maintainer.git`)
Release version: 18.0.0

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
- [ ] Create and push tag `v18.0.0`.
- [ ] Create the GitHub Release and attach the `.skill` archive.
- [ ] Verify GitHub Actions and the published release.

## Release evidence

Final local validation: 220 tests passed, 8 skipped on Windows; E2E acceptance passed; archive contains 88 files and excludes tests/.github/cache files.\n\nArchive SHA-256 will be recorded after the final build.