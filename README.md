# Project Upgrade Maintainer

A deterministic Agent Skill for governing project-upgrade workspaces safely across initialization, synchronization, artifact lifecycle, cleanup, governance records, delivery evidence, authority mapping, validation, and recovery.

The Skill is designed around a strict **review → exact approval → apply** contract. Mutating operations are backed by a deterministic Python CLI, persisted plans, apply-time revalidation, transaction journals, and recovery workflows. The Agent-facing output can use a compact decision surface while the full execution evidence remains stored on disk.

## Why this exists

Project upgrades often accumulate migration notes, temporary scripts, stale artifacts, partial delivery evidence, conflicting repository instructions, and cleanup candidates. Asking an Agent to manage all of that directly creates two problems: too much context and too much mutation authority.

Project Upgrade Maintainer separates responsibilities:

- the Agent interprets user intent and routes to the relevant policy;
- the CLI classifies, plans, validates, and executes deterministic operations;
- destructive or governance-sensitive actions require an exact reviewed `plan_id`;
- apply-time validation rejects stale plans, changed policy dependencies, implementation drift, and changed source snapshots;
- compact Agent output exposes the decision surface without discarding the full plan or audit evidence.

## Highlights

- **Adaptive bootstrap** for lean, standard, governed, and legacy-governed workspaces.
- **Project authority mapping** for repository instructions, security policies, ADRs, API contracts, CI policy, migration guidance, and other normative sources.
- **Artifact lifecycle governance** for collection, cleanup, Git policy, and permanent purge.
- **Governance records** for decisions, risks, validation evidence, exceptions, and delivery acceptance.
- **Transactional mutations** with durable journals and explicit recovery operations.
- **Stale-plan protection** through policy digests, implementation digests, source identity/snapshots, and exact plan approval.
- **Token-efficient Agent view** with `--view agent` and progressive detail retrieval through `inspect`.
- **No third-party runtime dependencies**; Python 3.10+ standard library only.

## Safety model

Projects may opt into strict evidence controls through approved Delivery Policy:
`--phase-check 1=suite --max-age-seconds 86400 --require-execution`.
Use `validation-record --check suite --subject-path src --execute python -m unittest discover`
to record a real local run; put `--execute` last. See the
[policy controls](subskills/delivery/references/delivery-policy.md) and
[execution boundaries](subskills/governance/references/validation.md).
CI is configured for Ubuntu and Windows (Python 3.10–3.13), with E2E on both.

Mutation is intentionally harder than inspection.

```text
review
  ↓
persist exact full plan
  ↓
inspect decision surface / request user decision
  ↓
approve exact plan_id
  ↓
apply-time revalidation
  ↓
transactional mutation
```

Important invariants:

1. A review does not authorize an apply by itself.
2. Apply requires the exact reviewed `plan_id`.
3. Plans are rejected if relevant policy, implementation, or source state has drifted.
4. Protected and sensitive collection requires exact-path authorization again at apply time.
5. Permanent purge is separate from recoverable cleanup/trash behavior.
6. Repository-local authority is discovered, but never silently adopted or rewritten.
7. Compact Agent output is a projection of full evidence; it is never the authorization artifact.

See [`SECURITY.md`](SECURITY.md) for the security model and vulnerability reporting guidance.

## Repository layout

```text
SKILL.md                  Thin Skill router
agents/                   Agent metadata
assets/                   Managed workspace templates
cli/                      CLI parser and entry orchestration
commands/                 Operation implementations
core/                     Plans, policy, transactions, schemas, safety primitives
references/               Shared policy references
subskills/                Domain routers and operation references
scripts/upgrade_workspace.py
                          Supported deterministic CLI entry point
tests/                    Unit, characterization, integration, and E2E tests
```

The source repository includes tests and GitHub project files. Release `.skill` archives should omit `tests/`, `.github/`, caches, and repository-only documentation that is not required at runtime.

## Requirements

- Python **3.10+**
- A filesystem-backed project repository/workspace
- Git is optional for most operations but required for Git-specific audits/policy workflows

There are no third-party Python runtime dependencies.

## Skill invocation

The Skill is intended to be explicitly invoked as:

```text
$project-upgrade-maintainer
```

The primary CLI entry point is:

```bash
python scripts/upgrade_workspace.py \
  --root /path/to/repository \
  --format json \
  --view agent \
  <command> [options]
```

Use `--view agent` for normal Agent execution. Use `--view full` for explicit debugging, forensic inspection, or human review of complete execution evidence.

For plan-bearing review operations, persist the full plan:

```bash
python scripts/upgrade_workspace.py \
  --root . \
  --format json \
  --view agent \
  cleanup-review \
  --plan-out .upgrade/plans/cleanup.json
```

Then inspect only the required decision evidence:

```bash
python scripts/upgrade_workspace.py \
  --root . \
  --format json \
  --view agent \
  inspect \
  --plan .upgrade/plans/cleanup.json \
  --section attention
```

An apply uses the stored plan and exact approval identifier:

```bash
python scripts/upgrade_workspace.py \
  --root . \
  --format json \
  cleanup-apply \
  --plan .upgrade/plans/cleanup.json \
  --approve <plan_id>
```

Do not hand-edit a stored plan to represent partial approval. Regenerate or re-review the exact intended scope so the new authorization boundary has its own `plan_id`.

## Command families

| Domain | Commands |
| --- | --- |
| Bootstrap | `bootstrap-review`, `initialize`, `sync`, `authority-review`, `authority-apply`, `authority-audit`, `health-check` |
| Lifecycle | `artifact-audit`, `artifact-policy-*`, `git-audit`, `git-policy-*`, `collect-*`, `cleanup-*`, `purge-*` |
| Governance | `phase-update`, `validation-record`, `decision-*`, `risk-*` |
| Delivery | `delivery-policy-*`, `exception-*`, `delivery-*` |
| Core | `version`, `inspect`, `recovery-*` |

Run CLI help for the current complete command surface:

```bash
python scripts/upgrade_workspace.py --help
```

## Agent decision view

`--view agent` is not a blind truncation mode. It preserves the information needed for safe decisions while aggregating routine entries. Blockers, destructive operations, protected/sensitive findings, ambiguity, conflicts, stale state, and user-authorization requirements remain on the decision surface.

Full plans stay on disk and can be queried progressively with `inspect`, including bounded filters such as index, action, class, path, and pagination.

This keeps token usage more stable on large repositories without weakening apply-time validation.

## Testing

Run the unit and integration suite:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Run the acceptance flow separately:

```bash
python tests/e2e_acceptance.py
```

The GitHub Actions workflow runs the test suite across supported Python versions and executes E2E acceptance on a representative version.

## Release packaging

Build the installable archive with:

```bash
python tools/build_skill.py --output dist/project-upgrade-maintainer-18.0.1.skill
```

The release contains the runtime surface plus the license:

```text
SKILL.md
LICENSE
agents/
assets/
cli/
commands/
core/
references/
scripts/
subskills/
```

`LICENSE` is intentionally distributed with the archive. Do not include `tests/`, `.github/`, `tools/`, repository-only documentation, `__pycache__/`, `*.pyc`, or local `.upgrade/` state in release archives.

## Security notes

This project can perform filesystem mutations and permanent purge after explicit approval. Treat untrusted repository content as untrusted input. Repository instructions can influence routing/context, but they must not replace user authorization or bypass deterministic CLI validation.

Transaction journals may contain local repository paths, hostnames, and process IDs. Generated transaction state is ignored by managed workspace policy and should not be committed publicly.

## Contributing

Contributions are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing safety-sensitive behavior. Changes to mutation semantics, policy dependencies, plan validation, transaction handling, or Agent decision projection require tests that demonstrate fail-closed behavior.

## License

Licensed under the [Apache License 2.0](LICENSE).
