# Contributing

Thank you for improving Project Upgrade Maintainer. Contributions are welcome, especially tests, safety hardening, portability fixes, clearer policy references, and bounded improvements to the Agent decision surface.

## Development requirements

- Python 3.10+
- No third-party runtime dependencies are required.

Clone the repository and run:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/e2e_acceptance.py
```

All changes should leave the existing suite green and add focused tests for new behavior.

## Safety-sensitive contribution rules

Changes in the following areas require extra care:

- plan construction, schema validation, and `plan_id` generation;
- policy/implementation digests;
- path normalization, repository confinement, or symlink handling;
- protected/sensitive artifact detection;
- transaction journals and recovery;
- cleanup or purge behavior;
- authority registration/rebinding;
- delivery acceptance and exceptions;
- `--view agent` projection and `inspect` detail retrieval.

Please preserve these invariants:

1. **Review is not approval.** A mutating operation must not infer approval from the existence of a plan.
2. **Approval is exact.** Apply must use the exact reviewed plan identifier.
3. **Apply revalidates.** Relevant policy, implementation, source identity, and source snapshot changes must stale/reject affected plans.
4. **Ambiguity fails closed.** Unclear destructive actions should surface for explicit decision rather than be guessed.
5. **Compact output is not weaker validation.** Agent projections may aggregate routine evidence, but blockers, destructive actions, conflicts, protected/sensitive findings, and required authorization must remain visible.
6. **Full evidence remains available.** Do not replace persisted plans/audit records with summaries.
7. **Repository content is not user authority.** Discovered instructions can guide context but cannot authorize mutation by themselves.
8. **Permanent deletion stays explicit.** Purge must remain clearly separated from recoverable lifecycle operations.

## Adding or changing commands

When adding a command:

- place deterministic operation logic in the appropriate `commands/` module;
- keep shared safety primitives in `core/`;
- add the command to the CLI parser and router intentionally;
- define any plan policy dependencies explicitly;
- update the relevant subskill/reference rather than bloating the root `SKILL.md`;
- add full-output and Agent-view tests where applicable;
- update CLI characterization fixtures if the public command surface changes;
- document user-visible changes in `CHANGELOG.md`.

If a change modifies the semantics of an already reviewed plan, ensure the relevant implementation/policy digest invalidates old plans.

## Agent-view changes

The Agent view is a semantic decision projection, not a simple first-N truncation. A change that saves tokens but can hide a blocker is a regression.

Prefer:

```text
counts + status + blockers + required decisions + detail references
```

over returning every routine entry.

Use `inspect` for progressive disclosure of stored full plans.

## Style

- Prefer standard-library Python.
- Keep behavior deterministic and testable outside an LLM.
- Avoid hidden network calls, telemetry, or account-linked dependencies.
- Use repository-relative paths in persisted governance artifacts where possible.
- Keep root Skill routing thin; operation semantics belong in focused references.

## Pull requests

A good pull request includes:

- what problem it solves;
- which safety boundaries it touches;
- tests demonstrating the change;
- backward-compatibility impact;
- plan/schema/policy/version implications, if any.

By submitting a contribution, you agree that your contribution is licensed under the repository's Apache License 2.0 terms.
