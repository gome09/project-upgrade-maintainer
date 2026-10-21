# Delivery Gate

The approval dependency digest binds selected active governance records, latest validation for the exact subject/scope/environment (including non-required checks), and shared blocker/drift/unverified state. Complete histories remain integrity-validated. Unrelated scoped evidence and informational STATE updates alone do not invalidate approval. Changes to selected/global evidence, Delivery Policy, subject, recovery state, blockers or warnings still require a new review.

`delivery-review` is a subject-bound evidence gate, not a generic checklist. The review must identify the exact delivery subject and the exact validation checks that are required for the requested gate.

## Subject Identity

Use one of these subject identities:

- `--subject-path <path>` hashes the exact file/directory content inside the repository. `.git/`, `.upgrade/`, and recognized saved review-plan files are excluded from directory snapshots.
- `--subject-digest <sha256> --subject-ref <name>` identifies an immutable external build/artifact by digest.
- With neither option, a repository-root Git worktree is fingerprinted automatically from `HEAD` plus tracked and non-ignored untracked file content. Saved Plan Schema files and `.upgrade/**` are excluded because they are control artifacts, not release content.
- If none of those produce a bound subject, the gate is blocked. A target label by itself is not delivery identity.

`delivery-apply` recomputes path/Git subjects before accepting the saved plan. Any subject drift is a stale-plan failure and requires new validation evidence plus a new delivery review.

Git subjects include indexed modes/gitlink commits and recursively fingerprint initialized submodules, including dirty worktrees. Uninitialized submodules have a distinct identity; no network fetch or initialization occurs. Unmerged indexes fail closed. POSIX file subjects include executable permissions; Windows uses indexed modes for Git files rather than unreliable POSIX executable bits. This expanded fingerprint intentionally invalidates older subject-bound evidence: rerun validation and regenerate reviews after upgrading.

## Validation Matrix

Every delivery review must resolve one or more required checks. If `.upgrade/delivery/POLICY.json` exists, its approved checks for the selected gate are mandatory minima; `--require-check <check>` may add extra checks but cannot remove policy-required checks. For projects without a configured Delivery Policy, at least one explicit `--require-check` remains required. Validation evidence satisfies a required check only when all of these match the review exactly:

- check name;
- subject digest;
- `--scope` (empty is distinct from a named scope);
- `--environment` (empty is distinct from a named environment).

The latest matching record must be `passed`. A different check, subject, scope, or environment cannot satisfy the gate. Non-required failed/unverified checks for the same subject/scope/environment remain visible as warnings.

When approved policy controls require local execution or a maximum age, the latest required record must meet those constraints too. Future-dated evidence is rejected. Delivery apply rechecks expiry even when files have not changed; expired evidence needs a new execution/request ID and review. These controls do not authenticate the local machine, prevent clock manipulation, or certify test quality.

## Governance and Workspace State

Delivery aggregates Core-validated active Decision, latest Risk, current Exception/Waiver, Validation, Recovery, and `STATE.md` state. When a delivery scope is provided, global or exact-scope Decisions/Risks and exact/global Exceptions are selected; unrelated scoped records do not control the gate.

Open risks, expired exceptions, missing/failed/unverified required validation, an unbound subject, and unresolved transactions are blockers. Accepted risks and active unexpired exceptions remain warnings. For `final-acceptance`, uncleared `STATE.md` blockers, unverified items, plan drift, and active earlier-schema unsealed governance records are blockers rather than warnings.

New Decision/Risk/Exception records are sealed with their exact approval `plan_id`; Delivery validates those record digests and Risk revision chains before consuming them. Schema v1 governance records remain readable, but final acceptance requires current sealed governance state.

`delivery-apply` revalidates the reviewed subject, the exact Delivery Policy identity/digest (when configured), governance/evidence file snapshot, selected evidence summary, blockers, and warnings, then persists an immutable acceptance record containing the exact approval plan ID, required checks, subject identity, and integrity digest. Readiness and final acceptance remain user decisions.
