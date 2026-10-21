# Changelog

All notable changes to Project Upgrade Maintainer are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and releases use [Semantic Versioning](https://semver.org/).

## [18.0.1] - 2026-10-21

### Fixed

- Keep ambiguous PID probes locked and use non-signaling Windows process handles.
- Serialize first initialization and later mutations with one repository-path OS
  guard; determine transaction eligibility only after acquiring that guard.

- Preserve existing file modes during atomic writes on Windows by using
  path-based chmod when descriptor-based fchmod is unavailable. This fixes
  initialization finalization and dependent lifecycle workflows.
- Preserve omitted phase next actions and avoid empty transaction journals for
  commands with no file operations.
- Restrict delivery approval dependencies to consumed scoped evidence while
  retaining full integrity checks and global governance blockers.
- Check module resources once during activation, including repair of active modules.

### Added

- Opt-in approved policy controls for phase exit checks, evidence age, and local
  execution provenance; `validation-record --execute` records actual command outcomes.
- Git subject identity for submodules and executable modes, and Ubuntu/Windows
  CI matrices for unit/integration and E2E acceptance.
- Combined artifact/Git audit through `git-audit --include-artifacts`.
- Validation run identities through `validation-record --request-id`, and safe
  retries of exact sealed Decision/Risk/Exception apply plans.

## [18.0.0] - 2026-09-11

### Added

- Deterministic project-upgrade governance across bootstrap, lifecycle,
  governance, delivery, authority mapping, validation, and recovery workflows.
- Review, exact-plan approval, apply-time revalidation, transaction journals,
  and explicit recovery operations for mutating commands.
- Compact Agent output with progressive evidence inspection through `inspect`.
- Release packaging through `tools/build_skill.py`.

### Security

- Repository-root confinement, symlink safety, protected/sensitive path
  authorization, stale-plan detection, and fail-closed handling for obsolete
  plan schemas.
- Repository-discovered instructions remain untrusted context and cannot
  authorize mutations by themselves.

[18.0.1]: https://github.com/gome09/project-upgrade-maintainer/releases/tag/v18.0.1
[18.0.0]: https://github.com/gome09/project-upgrade-maintainer/releases/tag/v18.0.0
