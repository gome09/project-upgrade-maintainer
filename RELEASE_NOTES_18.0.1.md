# Project Upgrade Maintainer 18.0.1

## Improvements

- Fix Windows atomic-write compatibility and preserve file permissions.
- Keep ambiguous PID probes locked; serialize first initialization and subsequent mutations.
- Reduce redundant audits, module checks, empty transactions, and unrelated approval invalidation.
- Add opt-in approved phase-exit checks, validation age limits, and local execution provenance.
- Run authorized validation argv with `validation-record --execute`; record exit code, output hashes, timeout, and subject stability.
- Fingerprint Git submodules, gitlinks, and executable modes; reject unresolved index conflicts.
- Add Ubuntu/Windows CI matrices for Python 3.10–3.13 and E2E acceptance.

## Compatibility and trust boundaries

Strict evidence controls are optional and require an approved Delivery Policy.
Expanded subject fingerprints require fresh validation evidence and delivery reviews.
Local execution evidence is not an external signature or a guarantee of test quality.
Command side effects are not rolled back by governance transactions; timeout does
not guarantee termination of detached descendants. Avoid secrets in command argv.

## Artifact verification

- Asset: `project-upgrade-maintainer-18.0.1.skill`
- SHA-256: `72e77302e19db05ed2eddced7dc9e08be1550bc0391e3263c3e8547d980d5d6f`
- 88 runtime files; tests, GitHub configuration, caches, and release tooling excluded.
- Source/archive equality, ZIP integrity, and reproducible builds verified locally.
- Local Windows suite: 251 tests run, OK (10 skipped); E2E and Skill format validation passed.
