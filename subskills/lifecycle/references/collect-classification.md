# Collect Classification

Use for `collect-review` and `collect-apply`. Scan outside `.upgrade/` for path/content signals associated with upgrades, migrations, refactors, cleanup, rollout/rollback, phases, reports, drafts, temporary work, debugging, experiments, prompts, notes, or checklists. Git-ignored matching items still enter review; ignored discovery remains bounded to the same signals and excludes known heavy dependency/generated directories.

## Actions and Destinations

Actions: `move` for unreferenced low-risk temporary/draft material; `copy` when useful material may still be referenced; `archive` for historical/superseded material worth retaining; `confirm` for ambiguity and temporary scripts until an exact decision exists; `forbidden` for protected or unauthorized sensitive material; `ignore` for normal project material or an explicit leave-in-place decision.

Destinations: requirements/plans -> `.upgrade/docs/`; phase/final reports -> `.upgrade/reports/`; decisions -> `.upgrade/decisions/`; reviews -> `.upgrade/reviews/`; prompts -> `.upgrade/prompts/`; logs -> `.upgrade/logs/`; temporary analysis -> `.upgrade/tmp/`; active temporary scripts -> `.upgrade/tmp/scripts/`; historical scripts -> `.upgrade/archive/scripts/`; other superseded files -> `.upgrade/archive/`.

## Authorization and Apply

- Pass exact user decisions as `--decision <path>=move|copy|archive|ignore`.
- Protected source, tests, official docs, CI/CD, Docker files, dependency manifests/lockfiles, licenses, and primary readmes require both an explicit action and review-time `--allow-protected <path>` before becoming executable.
- Secret-like filenames or credential/private-key/token content likewise require an explicit action plus review-time `--allow-sensitive <path>`.
- `collect-apply` re-evaluates protected/sensitive status from actual source path/content. Every executable protected/sensitive entry must retain review-time authorization in the plan and receive matching exact-path apply-time `--allow-protected` / `--allow-sensitive` authorization again.
- A destination inside `.upgrade/` does not make collection safe. Never overwrite. Managed canonical workspace files/module templates and targets registered in `.upgrade/ARTIFACTS.json` are reserved destinations, including while their module is inactive; use artifact-policy workflow for registered targets.
- After the entire reviewed plan passes stale/source/destination validation, its destinations may lazily activate required modules. Module activation is governance state, not permission to mutate unrelated files.
