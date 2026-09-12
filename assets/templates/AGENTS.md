<!-- project-upgrade-maintainer:governance-contract:v1 -->
# Upgrade Governance Contract

This is the canonical machine-readable governance contract for `.upgrade/**`. It is fully managed by `$project-upgrade-maintainer`; do not edit it manually. After a Skill upgrade, use `sync` to refresh this contract and repository bridges.

## Scope, Authority, and User Decisions

- This contract governs `.upgrade/**`; repository instructions remain authoritative outside that scope. If instructions conflict, stop and surface the conflict rather than choosing silently.
- The Skill may maintain workspace structure, lifecycle metadata, plans, approved mutations, validation evidence, Git policy blocks, and maintenance traces.
- Technical direction, risk or exception acceptance, plan-drift acceptance, phase completion, and delivery readiness require explicit user decisions.
- Bootstrap classification is advisory: repository evidence may inform a footprint recommendation, but size or detected technology never implies risk acceptance.

## Adaptive Workspace

- `.upgrade/CONFIG.json` is the machine-readable source of truth for the initial profile, active modules, and approved-registry provenance; `core` is always active.
- Profiles are Lean, Standard, Governed, or a user-selected Custom module set. Missing resources for inactive modules are not damage.
- An explicit command may lazily activate a required module, but must record activation in `CONFIG.json` before completing the mutation. Never silently disable or delete modules to shrink the workspace.

## Canonical Sources of Truth

- Core: `.upgrade/AGENTS.md`, `.upgrade/CONFIG.json`, `.upgrade/AUTHORITY.json`, `.upgrade/AUTHORITY.md`, `.upgrade/ARTIFACTS.json`, `.upgrade/STATE.md`, `.upgrade/MANIFEST.md`, `.upgrade/docs/UPGRADE_REQUIREMENTS.md`, `.upgrade/docs/UPGRADE_PLAN.md`, and `.upgrade/traces/`.
- `.upgrade/AUTHORITY.json` records only user-approved project authority sources outside `.upgrade/`; `.upgrade/AUTHORITY.md` is its generated navigation view. Registration never transfers ownership or permission to rewrite those project files.
- When configured, `.upgrade/delivery/POLICY.json` is the approved project-level minimum evidence matrix for readiness, final acceptance, and rollback readiness. Explicit delivery checks may add requirements but never weaken this policy.
- When active: planning/phase state in `.upgrade/docs/IMPLEMENTATION_PLAN.md`, `.upgrade/stages/`, `.upgrade/checkpoints/`; decisions/reviews in `.upgrade/decisions/`, `.upgrade/reviews/`; validation in `.upgrade/validation/`; durable reports in `.upgrade/reports/`; recoverable cleanup/history in `.upgrade/tmp/.trash/`, `.upgrade/archive/`.

## Mutation Protocol

1. Inspect or audit current state before proposing change.
2. On first setup, run read-only `bootstrap-review`; present its evidence and profile/module choices, unless the user already selected a footprint.
3. For plan-based mutations, including authority registration/rebind/removal, generate a review plan and stop for explicit approval. Apply only the unchanged plan with the exact approved `plan_id`.
4. At apply time, revalidate source identity, policy version, implementation digest, and any required protected/sensitive authorization.
5. Record only checks that actually ran. Use `unverified` when execution cannot be established; refresh managed inventory and append a maintenance trace. If finalization fails after a write, report `partial` and preserve the failure in the durable trace.

## Authorization and Lifecycle Boundaries

- Protected or sensitive collection requires exact-path authorization during review and again during apply after re-evaluating the real source. Collection destinations stay inside `.upgrade/` and never overwrite an existing destination.
- Phase movement is a state transition: normal advancement requires explicit completion of the previous phase; skips/regressions require explicit authorization and a reason. The Skill records the decision but never decides phase completion.
- Project authority discovery is advisory. Register or rebind repository-owned authority sources only through `authority-review` and approved `authority-apply`; source drift is never auto-accepted.
- New governance/evidence artifacts must go through `artifact-policy-review` and approved `artifact-policy-apply`; `.upgrade/ARTIFACTS.json` is the registry and registered permanent/required governance artifacts remain cleanup boundaries until their policy changes.
- Promote retained content into Agent rules only when its policy explicitly exposes it there. Runtime/temporary artifacts should prefer lifecycle and ignore linkage. Conflicting lifecycle evidence is conservative; `unknown` requires review.
- Reversible cleanup uses collision-safe archive/trash destinations fixed during review. Permanent purge is limited to approved entries already under `.upgrade/tmp/.trash/`.
- Outside `.upgrade/`, mutate only supported managed marker blocks and explicitly requested non-overwriting plan/render outputs.

<!-- project-upgrade-maintainer:artifact-policies:start -->
## Registered Artifact Policies

- None registered. Add governed extension artifacts through `artifact-policy-review` and `artifact-policy-apply`.
<!-- project-upgrade-maintainer:artifact-policies:end -->

## Validation and Governance Evidence

- Never fabricate validation, approval, completion, risk, exception, delivery-policy, or delivery evidence. Preserve append-only validation records and maintenance traces.
- Delivery Policy changes require review and exact plan approval. Delivery acceptance remains a user decision even when all policy checks pass.
- `health-check` must report a missing, unmanaged, or drifted `.upgrade/AGENTS.md`, invalid adaptive configuration, invalid authority registry/view or required authority source, missing active-module resources, broken repository bridge markers, invalid Delivery Policy, and unresolved recovery state. Use `--ci` for hard CI failures and `--strict` when warning-only health must also fail the caller.

## Repository Bridges

Repository Agent instruction files may contain a small managed bridge to this contract and `.upgrade/AUTHORITY.md`. A bridge provides discoverability only: it neither replaces local repository instructions nor extends this contract beyond `.upgrade/**`.

## Hard Prohibitions

- Do not edit a reviewed plan and then apply it, hand-edit `.upgrade/AUTHORITY.json`, hand-edit `.upgrade/ARTIFACTS.json`, or hand-edit `.upgrade/delivery/POLICY.json` to bypass governed policy review.
- Do not bypass exact approval, protected/sensitive authorization, phase-transition constraints, module configuration, or path boundaries; do not reinterpret ambiguous lifecycle evidence as automatically temporary/generated.
- Do not permanently delete outside approved `.upgrade/tmp/.trash/` purge plans, and do not run unscoped destructive Git operations such as `git add .`, `git clean`, automatic untracking, `git reset`, or unmanaged `git rm`.
- Do not silently resolve governance conflicts, invent user decisions, or promote a bootstrap recommendation into risk acceptance.
