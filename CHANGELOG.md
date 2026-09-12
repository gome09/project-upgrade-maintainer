# Changelog

All notable changes to Project Upgrade Maintainer are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and releases use [Semantic Versioning](https://semver.org/).

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

[18.0.0]: https://github.com/gome09/project-upgrade-maintainer/releases/tag/v18.0.0
