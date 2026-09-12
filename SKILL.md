---
name: project-upgrade-maintainer
description: Route explicitly invoked project-upgrade governance work to focused Bootstrap, Lifecycle, Governance, Delivery, or Core recovery workflows, including project-authority mapping, backed by the deterministic CLI.
---

# Project Upgrade Maintainer

Thin router for `.upgrade/` governance. Use the bundled deterministic CLI; do not reimplement its classifiers or hand-edit managed policy/state.

## Execution Boundary

- Require explicit `$project-upgrade-maintainer` invocation.
- Always read [references/shared/safety-and-lifecycle.md](references/shared/safety-and-lifecycle.md).
- Route each intent to one domain subskill, except `version` and `recovery-*`, which are Core operations. Load only the routed references.
- For cross-domain requests, execute each operation under its own route instead of loading all domains together.

## Intent Router

| Intent / commands | Domain | Load |
| --- | --- | --- |
| `bootstrap-review`, `initialize`, `sync`, `authority-*`, workspace/config/governance `health-check` | Bootstrap | [subskills/bootstrap/SKILL.md](subskills/bootstrap/SKILL.md) |
| `artifact-audit`, `artifact-policy-*`, `git-audit`, `git-policy-*`, `collect-*`, `cleanup-*`, `purge-*` | Lifecycle | [subskills/lifecycle/SKILL.md](subskills/lifecycle/SKILL.md) |
| `phase-update`, `validation-record`, `decision-*`, `risk-*` | Governance | [subskills/governance/SKILL.md](subskills/governance/SKILL.md) |
| `delivery-policy-*`, `exception-*`, `delivery-*` | Delivery | [subskills/delivery/SKILL.md](subskills/delivery/SKILL.md) |
| `recovery-*` | Core recovery | [references/shared/transactions-and-recovery.md](references/shared/transactions-and-recovery.md) |
| `version` | Core diagnostics | no additional reference |

## Shared Safety

The shared safety reference is authoritative; domain references add only operation-specific semantics.

## CLI Contract

Use Python 3.10+ and the supported CLI entry point:

```text
python scripts/upgrade_workspace.py --root <repo> --format json --view agent <command> [options]
```

Prefer JSON execution evidence. Use `--view agent` for normal Skill execution and reserve `--view full` for explicit debugging/forensics. For plan-bearing reviews that may proceed to apply, always persist the exact full plan with `--plan-out <repository-local-path>`; the compact agent view is a decision surface, not the plan artifact. Use `inspect --plan <path> --section attention` or bounded filters such as `--index`, `--action`, `--class`, and `--path` to expand only required evidence.

Do not request full output merely to approve/apply a plan: apply revalidates the stored full plan, exact `plan_id`, policy dependencies, implementation digest, and source snapshots.

Report exit codes `2` (validation), `3` (stale plan), `4` (partial execution), and any command-specific nonzero exit as failures. Current metadata is Skill `18.0.0`, Policy `14`, Plan Schema `3`; Schema 2 plans must be regenerated.
