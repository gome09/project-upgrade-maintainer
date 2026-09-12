# Git Policy Review

Use `git-audit` for read-only lifecycle mismatch detection. Use `git-policy-review` / `git-policy-apply` only to maintain reviewed ignore rules.

## Automatic Scope

The only Git-policy mutation is adding approved rules inside the dedicated `.gitignore` marker block. `artifact-policy-apply` has its own reviewed path for exact registered-artifact ignore entries and Docker exclusions.

Tracked generated, temporary, or sensitive files remain `confirm` findings when correction would require repository-level tracking changes. Never remove user-authored ignore rules or silently convert ignored permanent files to tracked state.

## Review

- Run `git-audit` when mismatches need classification; CLI classifier output is authoritative.
- `git-policy-review` may propose only `add-ignore`; show every proposed rule before mutation.
- Repository paths must be escaped as literal Git ignore patterns. If a path cannot be represented safely, propose no automatic rule.
