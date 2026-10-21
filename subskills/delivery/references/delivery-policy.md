# Delivery Policy

Use `delivery-policy-review`, `delivery-policy-apply`, and `delivery-policy-audit` to govern the project's minimum delivery-evidence matrix. Projects without a configured policy remain supported; once configured, its required checks are mandatory minima for every matching gate.

The policy contains independent required-check sets for `readiness`, `final-acceptance`, and `rollback-readiness`. A `delivery-review` may add extra `--require-check` values, but it cannot remove checks required by the current approved policy. Validation evidence must still match the exact delivery subject, scope, and environment.

Policy creation or replacement is a governed mutation: review first, approve the exact Plan Schema plan ID, then apply. The persisted policy records the user authority, approval plan ID, and integrity digest. If the policy changes after a delivery plan is reviewed, `delivery-apply` must reject that plan as stale.

Use a project-specific policy rather than assuming that a generic profile proves readiness. The Skill may recommend checks, but the user owns the actual delivery evidence standard and any acceptance decision.

## Configurable Strict Controls

These optional controls preserve old behavior when omitted:

- `--phase-check 1=suite` requires `suite` before exiting Phase 1; repeat for more checks/phases.
- `--max-age-seconds 86400` limits the age of required validation evidence, including at delivery apply time.
- `--require-execution` requires CLI-generated local execution evidence rather than manual status assertions.

For example, review with `delivery-policy-review --readiness-check suite --final-check suite --phase-check 1=suite --max-age-seconds 86400 --require-execution --plan-out .upgrade/policy-plan.json`, then inspect and approve the exact plan ID using `delivery-policy-apply`. At least one delivery-gate check is required. Configure phase checks only for phases whose exit needs enforcement. Age/source controls apply uniformly to required checks, not informational checks.

A policy review proposes a complete replacement, not a partial merge. Include all desired gate checks and controls each time; omitting existing controls removes them only after the replacement plan is approved and applied. Policy identity seals the controls; changes invalidate pending delivery approval. Disabling strict mode is therefore an explicit governance decision, not an automatic fallback.
