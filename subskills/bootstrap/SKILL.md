---
name: project-upgrade-maintainer-bootstrap
description: Bootstrap routing for project profiling, initialization, synchronization, project authority discovery/registration, governance-context bridges, fixed-footprint workspace migration, module activation, and diagnostics.
---

# Bootstrap Domain

If loaded directly, read `../../references/shared/safety-and-lifecycle.md` first.

| Intent | Command | Load |
| --- | --- | --- |
| Profile project / recommend footprint + authority candidates | `bootstrap-review` | `references/adaptive-bootstrap.md`, `references/authority-network.md` |
| Initialize workspace | `initialize` | `references/adaptive-bootstrap.md`, `references/governance-context.md`, `references/authority-network.md` |
| Sync bridges, modules, authority view, or fixed-footprint/schema migration | `sync` | `references/governance-context.md`, `references/authority-network.md`; add `references/adaptive-bootstrap.md` for profile/module/registration findings |
| Review/apply/audit project authority sources | `authority-review`, `authority-apply`, `authority-audit` | `references/authority-network.md`, `references/governance-context.md` |
| Diagnose workspace/governance context / CI gate | `health-check`, `health-check --ci`, `health-check --strict` | `references/governance-context.md`, `references/authority-network.md`; add `references/adaptive-bootstrap.md` for profile/module/registration findings |

For `health-check`, `--ci` uses exit `5` for health errors and `6` for recovery-required state; `--strict` additionally uses `7` for warning-only health. Treat each as failure.
