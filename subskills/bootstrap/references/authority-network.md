# Project Authority Network

Use for `authority-review`, `authority-apply`, `authority-audit`, authority discovery in `bootstrap-review`, and authority findings in `health-check`.

## Authority Boundary

Authority discovery is advisory. A detected README, SECURITY policy, Agent instruction file, ADR, API contract, CI workflow, or migration document is only a candidate until the user explicitly reviews and approves registration. Registration does not transfer ownership: project authority sources outside `.upgrade/` remain `managed=false` and must never be rewritten merely because they are registered.

`.upgrade/AUTHORITY.json` is the machine-readable approved registry. `.upgrade/AUTHORITY.md` is a fully managed generated navigation view. `.upgrade/AGENTS.md` remains the scoped behavior contract for `.upgrade/**`; the authority map does not override repository instructions outside their declared scope.

## Review -> Approval -> Apply

- `authority-review` with no selections is read-only and surfaces candidates/current registrations.
- Use explicit `--accept <path>` to register a new candidate or to rebind a changed registered source. Overrides use `PATH=VALUE` for kind, scope, authority level, and required status.
- Use `--remove <path>` to unregister linkage without changing or deleting the project file.
- Apply only the unchanged Plan Schema 3 plan with the exact approved `plan_id`.
- At apply time, revalidate the registry digest and the exact source snapshot. Drift between review and apply makes the plan stale.

## Drift and Synchronization

`sync` may regenerate `.upgrade/AUTHORITY.md` from the already-approved registry and may migrate workspace schema, but it must not silently accept content changes in registered project sources. A digest mismatch is an authority-drift finding and requires a new `authority-review --accept <path>` plus approved `authority-apply` to rebind.

Required registered sources that disappear, unsafe symlinks, invalid registry provenance, or conflicting normative authorities for the same kind/scope are health errors. Content drift is surfaced for review and is not auto-resolved.
