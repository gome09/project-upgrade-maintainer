# Upgrade Workspace

Read `.upgrade/AGENTS.md` first for the canonical machine-readable governance contract that applies to this workspace. Repository Agent instruction files may contain managed bridges pointing here.

This workspace is adaptively bootstrapped. `.upgrade/CONFIG.json` records the user-selected initial profile and the modules that are currently active. A module may be absent until the user selects it or an explicit command needs it; absence of an inactive module is not workspace damage.

Core context is intentionally small:

- `.upgrade/AGENTS.md`: canonical scoped governance contract
- `.upgrade/CONFIG.json`: initialization profile and active modules
- `.upgrade/ARTIFACTS.json`: registered extension-artifact policies and linkage metadata
- `.upgrade/STATE.md`: current upgrade state
- `.upgrade/MANIFEST.md`: managed inventory
- `.upgrade/docs/`: requirements and upgrade plan when the planning module is active
- `.upgrade/traces/`: append-only maintenance evidence

Optional modules materialize on demand:

- `phase`: `stages/`, `checkpoints/`, and `docs/IMPLEMENTATION_PLAN.md`
- `validation`: `validation/`
- `reporting`: `reports/` and `FINAL_REPORT.md`
- `review`: `decisions/`, `reviews/`, and `POST_UPGRADE_REVIEW.md`
- `lifecycle`: `tmp/`, recoverable trash, `archive/`, `logs/`, and `prompts/`

Use `bootstrap-review` before first initialization to obtain a read-only, evidence-based recommendation for Lean, Standard, or Governed. The recommendation is advisory: project size does not by itself determine upgrade risk, and the user chooses the final profile or custom module set.

Use `sync` to repair resources for already-active modules and refresh governance bridges. `sync --enable-module <module>` explicitly adds a module without deleting or disabling existing modules. Nested repository bridges remain opt-in with `--sync-nested-bridges`.

<!-- project-upgrade-maintainer:artifact-index:start -->
## Registered Artifact Index

- No extension artifacts are registered.
<!-- project-upgrade-maintainer:artifact-index:end -->
