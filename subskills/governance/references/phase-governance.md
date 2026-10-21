# Phase Governance

Use this reference for `phase-update`.

## Transition Invariants

`phase-update` is a governed state transition, not a free-form edit of `STATE.md`. A valid first phase operation may lazily activate the `phase` module only after transition inputs pass validation. If `STATE.md` already claims an active phase while the module is disabled, stop and repair the inconsistency with `sync --enable-module phase` rather than materializing around it.

- Refreshing the current phase is allowed and remains idempotent when no managed field changes.
- Advancing by one phase from an active phase requires the user's explicit completion decision through `--complete-previous --completion-note <text>`.
- Skipping one or more phases requires `--allow-skip --transition-reason <text>`.
- Regressing to an earlier phase requires `--allow-regression --transition-reason <text>`.
- A transition from Phase 0 directly to Phase N greater than 1 is a skip and requires the same explicit skip reason.
- Missing current-phase artifacts or malformed `STATE.md` phase values block transitions rather than being silently treated as Phase 0.

## Recorded Evidence

When an active phase is explicitly completed, its stage artifact is updated to `completed` and records the user-confirmed completion note. When a regression occurs, the phase being left is marked `regressed` with the reason.

Every actual phase change writes an append-only transition checkpoint containing the source phase, destination phase, transition kind, reason, and timestamp. `health-check` compares `STATE.md` with the most recent checkpoint by write time, which allows an explicitly recorded regression without treating the highest historical phase number as current.

Use `phase-update --plan-drift` to record meaningful deviations from the approved plan in `STATE.md`; acceptance remains an explicit user decision.

Omitted optional fields retain their existing values in STATE and the phase artifact. Refreshing a phase does not reset its next action.

## Opt-in Phase Exit Gate

The approved Delivery Policy may configure repeated `--phase-check PHASE=CHECK` entries. Before advancing or skipping away from a configured active phase, `phase-update` checks those validations against `--subject-path` (or Git/external subject), `--scope`, and `--environment`. Policy age and local-execution requirements also apply. It reuses final-acceptance governance checks, so open selected/global risks, unresolved recovery, STATE blockers/unverified items/plan drift, expired exceptions, or unsealed earlier-schema governance can prevent exit. Failure occurs before phase writes; success stores policy identity and bound evidence digest in the completed phase artifact.

Only the phase being exited is checked; skipped phases are not claimed as completed. Refresh/regression and projects with no configured phase checks retain existing behavior. Explicit completion and skip permissions remain necessary. No automatic waiver bypass is provided: resolve blockers or review and explicitly approve a replacement policy. Read the Delivery Policy reference when configuring the controls; ordinary phase updates need not repeat policy review or delivery review.
