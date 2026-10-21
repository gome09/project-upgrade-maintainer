# Shared Safety and Lifecycle

Load for every route. Domain references add only operation-specific semantics.

## Hard Invariants

- The bundled CLI is the mutation/classification authority. Do not hand-edit managed plans, registry/config state, governance contracts, lifecycle policy blocks, or derived managed surfaces instead of using a supported workflow.
- Normalize paths before mutation; reject traversal and symlink/junction escapes. `.upgrade/` itself must be a real local directory.
- Review is not approval. Plan-based apply requires the exact `plan_id` from the unchanged saved Plan Schema 3 plan. Its declared `policy_dependencies`, implementation digest, and reviewed sources are revalidated at apply; scope/digest/source drift makes the plan stale. Schema 2 plans must be regenerated.
- Technical direction, risk/exception acceptance, phase completion or skip/regression, plan-drift acceptance, delivery readiness, and final acceptance are user decisions.
- Sensitive/protected material is conservative by default. Required exact-path authorization cannot be inferred from a plan, destination, or classifier result.
- Agent output is a projection, not an authorization artifact. When `--view agent` is used for a plan-bearing review that may be applied, persist the exact plan with `--plan-out`; expand decision-critical evidence with bounded `inspect` calls instead of ingesting the full plan by default.

## Mutation Boundary

- Outside `.upgrade/`, write only supported managed marker blocks or exact user-requested non-overwriting plan/render outputs inside the repository.
- Preserve user content outside managed blocks. Marker pairs must be exact and unique; duplicate/incomplete markers are conflicts, not repair permission.
- Never run destructive or unscoped Git operations such as `git add`, `git rm`, `git reset`, `git clean`, automatic untracking, or `git add .`.

## Mutation Guarantees

1. Inspect current state and compute the smallest safe change or review plan; review-only commands stop without mutation.
2. Apply only authorized plan entries or marker-managed changes. Write atomically in UTF-8, preserve newline/BOM/file mode where applicable, and skip unchanged content.
3. After mutation, refresh managed inventory and append maintenance evidence. If finalization fails after a write, report `partial` and preserve a best-effort durable failure trace rather than success.
4. Respect `.upgrade/.lock`. A same-host lock may be reclaimed automatically only when its recorded PID is provably dead; unreadable, foreign-host, permission-ambiguous, or apparently live locks remain blocking findings. Recovery never treats an ambiguous lock as stale.
5. All mutations also hold a repository-path OS guard in the system temporary directory, including first initialization before `.upgrade/` exists. It is released automatically on process exit. Its empty backing file is retained to keep one lock identity; do not delete it to bypass contention. This guard coordinates local processes sharing the same canonical path and temporary directory, not distributed hosts or path aliases.

## Trust Boundary

Governance and validation digests/chains provide local tamper evidence, not an external signature or independent trust anchor. A principal able to rewrite the entire repository can also rewrite the local trust root. Where that threat model matters, require an external CI attestation, signed artifact/commit, or append-only remote ledger in addition to the local governance records.
