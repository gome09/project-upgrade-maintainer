---
name: project-upgrade-maintainer-lifecycle
description: Lifecycle routing for artifact policy, collection, cleanup/archive/trash/purge, Git/Docker ignore governance, and lifecycle protection.
---

# Lifecycle Domain

If loaded directly, read `../../references/shared/safety-and-lifecycle.md` first.

| Intent | Command | Load |
| --- | --- | --- |
| Classify artifacts | `artifact-audit` | `references/artifact-lifecycle.md` |
| Review/apply registered artifact policy | `artifact-policy-review` / `artifact-policy-apply` | `references/artifact-policy-linking.md` |
| Audit Git lifecycle mismatches | `git-audit` | `references/artifact-lifecycle.md` |
| Review/apply Git ignore policy | `git-policy-review` / `git-policy-apply` | `references/git-policy.md` |
| Review/apply collection | `collect-review` / `collect-apply` | `references/collect-classification.md` |
| Review/apply reversible cleanup | `cleanup-review` / `cleanup-apply` | `references/cleanup-classification.md` |
| Review/apply permanent trash purge | `purge-review` / `purge-apply` | `references/purge.md` |

CLI classification and lazy module activation are authoritative; do not load Bootstrap merely to restate them.
