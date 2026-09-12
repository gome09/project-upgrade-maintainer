---
name: project-upgrade-maintainer-delivery
description: Delivery routing for exception/waiver governance and readiness, final-acceptance, and rollback-readiness gates.
---

# Delivery Domain

If loaded directly, read `../../references/shared/safety-and-lifecycle.md` first.

| Intent | Command | Load |
| --- | --- | --- |
| Review/apply/audit an exception or waiver | `exception-review`, `exception-apply`, `exception-audit` | `references/exception.md` |
| Define/audit project delivery evidence minima | `delivery-policy-review`, `delivery-policy-apply`, `delivery-policy-audit` | `references/delivery-policy.md` |
| Review/apply/audit readiness/final/rollback gate | `delivery-review`, `delivery-apply`, `delivery-audit` | `references/delivery-policy.md`, `references/delivery-gate.md` |

Delivery consumes Core-validated Decision, Risk, Validation, Exception, and Recovery state through the CLI; it does not import Governance command handlers.
