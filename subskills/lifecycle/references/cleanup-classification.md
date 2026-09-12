# Cleanup Classification

Use this reference for `cleanup-review` and `cleanup-apply`. Audit only `.upgrade/`.

## Classifications

- `keep`: current state, approved plans, final reports, reviews, validation evidence, and confirmed decisions.
- `update`: retained summaries needing a complete reviewed replacement payload.
- `merge`: related fragments consolidated into a complete reviewed replacement payload.
- `archive`: old drafts, old traces, old checkpoints, or historical artifacts worth retaining.
- `delete`: disposable files under `.upgrade/tmp/`; apply moves them to recoverable `.upgrade/tmp/.trash/` rather than permanently deleting them.
- `confirm`: logs, ambiguous ownership, or material that may affect open work.
- `purge`: permanent deletion of files already in recoverable trash; requires a separate purge plan and approval.

## Default Thresholds

- Suggest cleanup for `.upgrade/tmp/` over 20 MB or more than 10 files, excluding recoverable trash.
- Suggest cleanup for `.upgrade/logs/` over 50 MB or more than 15 files.
- Suggest review for append-only traces older than 30 days.
- Suggest review for checkpoint markers older than the current phase minus two.

## Review and Apply

- Cleanup review emits file-level plans; apply never performs recursive directory deletion.
- Treat any registered artifact that is governance-classified, required, or permanent as a dynamic `keep` boundary. To retire it, first use an approved artifact-policy plan to change or unregister the policy; unregistering does not delete the file.
- For `update` or `merge`, write the complete reviewed replacement to a project-local file and pass `--replacement <path>=<file>` so its content is covered by `plan_id`.
- `delete` is reversible: review assigns a collision-safe destination under `.upgrade/tmp/.trash/`, apply moves only to that reviewed destination, and the result reports it as recoverable rather than permanently deleted.
- `archive` review assigns a collision-safe non-existing path under `.upgrade/archive/`; repeated cleanup of a later file appearing at the same source path must not collide with an earlier archive.
- An approved `archive` or reversible `delete` may lazily activate the `lifecycle` module at apply time, but only after the unchanged cleanup plan has been fully validated. Review remains read-only.
- Cleanup itself never permanently deletes. For permanent removal of approved trash, use the separate `purge-review` / `purge-apply` workflow in `purge.md`.
