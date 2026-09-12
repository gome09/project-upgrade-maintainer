# Adaptive Bootstrap

Use for `bootstrap-review`, first-time `initialize`, custom profile selection, lazy module activation, and fixed-footprint workspace registration.

## Bootstrap Review

`bootstrap-review` is strictly read-only: do not create `.upgrade/`, write a plan, alter Agent instructions, or change ignore rules. For first initialization, run it unless the user already selected a profile/module set, then present observable evidence, recommendation, confidence, and the choices **Lean / Standard / Governed / Custom modules / Cancel** before mutation.

The recommendation is advisory. Repository size, language, framework, infrastructure, or heuristic score must not be presented as user risk acceptance or delivery criticality. Always expose the evidence/reasons behind the recommendation.

Read-only profiling signals may include filtered file count, package/workspace roots, languages, CI, Docker, database migration/schema tooling, infrastructure/deployment files, auth/security surfaces, public APIs, ownership/contribution rules, Agent-instruction depth, existing upgrade/migration artifacts, and advisory project authority candidates. Candidate discovery never grants authority.

## Profiles and Modules

- **Lean**: `core + planning`; canonical governance/config/state/manifest/traces plus requirements and upgrade plan.
- **Standard**: Lean + `phase + validation + reporting + lifecycle` for normal multi-stage upgrades.
- **Governed**: Standard + formal `review`/decision records for broad, long-running, multi-owner, or production-sensitive programs.
- **Custom**: `core` plus explicit modules; dependencies are expanded automatically.

`--profile default` is a supported alias for Governed. If the CLI receives no profile, `initialize` falls back to Lean and warns to run `bootstrap-review`; the Skill workflow should normally present choices first.

`core` is always active. `phase`, `reporting`, and `review` depend on `planning`. `.upgrade/AUTHORITY.json`, `.upgrade/AUTHORITY.md`, and `.upgrade/ARTIFACTS.json` all belong to `core`. The artifact registry stores extension-artifact policy metadata; these core registries/views do not themselves materialize heavyweight optional modules.

## Lazy Activation

Inactive-module resources need not exist. An explicit operation may activate only what it needs: `phase-update` -> `phase`; `validation-record` -> `validation`; collection into reports/reviews/tmp/archive/etc. -> matching destination module; cleanup archive/delete -> `lifecycle`; an approved artifact policy may activate only the module required by that artifact.

Activation updates `.upgrade/CONFIG.json` and materializes that module's managed resources. It does not silently disable modules. Use `sync --enable-module <module>` for proactive expansion.

## Existing Workspace Support

Workspaces without `.upgrade/CONFIG.json` are treated as fixed-footprint legacy workspaces. `sync` adopts such a workspace using the persisted profile identifier `legacy-governed` and repairs the full governed structure instead of guessing that missing directories were intentionally inactive. Existing schema-1 configurations are migration inputs: `sync` preserves the selected modules and user data, advances the config to schema 2, creates the empty authority registry/view when absent, and does not auto-register discovered project authority sources. `health-check` uses a valid `CONFIG.json` to distinguish inactive modules from missing required resources; without it, the required footprint is ambiguous and reported as an error.
