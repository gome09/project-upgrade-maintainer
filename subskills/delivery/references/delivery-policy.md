# Delivery Policy

Use `delivery-policy-review`, `delivery-policy-apply`, and `delivery-policy-audit` to govern the project's minimum delivery-evidence matrix. Projects without a configured policy remain supported; once configured, its required checks are mandatory minima for every matching gate.

The policy contains independent required-check sets for `readiness`, `final-acceptance`, and `rollback-readiness`. A `delivery-review` may add extra `--require-check` values, but it cannot remove checks required by the current approved policy. Validation evidence must still match the exact delivery subject, scope, and environment.

Policy creation or replacement is a governed mutation: review first, approve the exact Plan Schema plan ID, then apply. The persisted policy records the user authority, approval plan ID, and integrity digest. If the policy changes after a delivery plan is reviewed, `delivery-apply` must reject that plan as stale.

Use a project-specific policy rather than assuming that a generic profile proves readiness. The Skill may recommend checks, but the user owns the actual delivery evidence standard and any acceptance decision.
