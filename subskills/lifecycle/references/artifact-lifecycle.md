# Repository Artifact Lifecycle

Use this reference for `artifact-audit`, `git-audit`, and Git policy review.

## Classifications

- `permanent`: source, tests, retained project documentation, manifests, and durable configuration. Expected Git state: tracked.
- `generated`: reproducible build, coverage, generated dependency, or test output. Expected Git state: ignored.
- `temporary`: scratch, debug, one-off logs, or transient reports. Expected Git state: ignored.
- `sensitive`: credentials, private keys, tokens, or files with secret-like content signals. Expected Git state: ignored; never collect without exact-path authorization.
- `historical`: superseded material intentionally retained for audit or rollback. Git intent requires review.
- `unknown`: insufficient evidence for a safe lifecycle decision. Do not mutate automatically.

When a file has a permanent-project signal (for example `src/`, `app/`, `lib/`, tests, retained docs, or a source-code extension) and also has generated, temporary, or historical path/name signals, classify it as `unknown` rather than automatically preferring the transient signal. This prevents paths such as `src/logs/worker.py` from producing a broad ignore rule without review.

Classification is advisory. Project-local rules in `AGENTS.md`, repository documentation, and explicit user decisions override generic path heuristics.

Registered artifacts retain their reviewed registry policy alongside generic lifecycle classification. Governance, permanent, or required registrations remain cleanup-retained until their policy is changed or unregistered through the artifact-policy workflow.

## Git Lifecycle

When a new artifact type appears:

1. Classify its lifecycle before deciding where it belongs, then compare expected Git state with actual tracked, untracked, or ignored state.
2. Never automatically untrack a tracked file; mismatches that require repository-level tracking changes remain explicit decisions.
3. Add ignore rules only through an approved `git-policy` plan. Prefer directory-scoped rules only for unambiguous generated/temporary directories and exact-file rules for isolated sensitive artifacts.
4. Emit repository-path ignore rules literally with Git metacharacters escaped; if a filename cannot be represented safely, emit no automatic rule.
