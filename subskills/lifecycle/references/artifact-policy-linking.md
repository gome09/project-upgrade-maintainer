# Artifact Policy Linking

Use for adding, registering, updating, or unlinking extension artifacts under `.upgrade/**`. `.upgrade/ARTIFACTS.json` is the deterministic registry; never hand-edit it or use `sync` to bypass artifact-policy review.

## Review

- Run `artifact-policy-review --path ...` first; it is read-only except for an explicitly requested `--plan-out`.
- Present classifier evidence and proposed kind, module, lifecycle, Git/Docker policy, health requirement, and AGENTS/README exposure. Conflicting governance/runtime signals require explicit `--kind`.
- Use review-time `--create` only when the user requested a new artifact; apply creates a deterministic minimal stub and never overwrites an existing file.
- Use `--remove` to unlink governance. Apply removes registry linkage but retains the file for a later lifecycle decision.

## Kind Defaults

- `governance`: durable control/evidence (risk, decision, security review, rollback, exception/waiver, acceptance, delivery gate) -> permanent, tracked, Docker-excluded, health-required, AGENTS + README exposed.
- `content`: durable upgrade context without governance authority -> permanent, tracked, Docker-excluded, health-required, README exposed only.
- `runtime`: logs/debug/tmp/raw execution output -> temporary, Git-ignored, Docker-excluded, not health-required or exposed.

Classifier output is advisory. Explicit user choices may override kind, module, lifecycle, Git/Docker policy, requiredness, and exposure; the reviewed plan remains the authorization boundary.

## Managed Linkage

Apply may update `.upgrade/AGENTS.md`, `.upgrade/README.md`, `.upgrade/CONFIG.json`, `.upgrade/MANIFEST.md`, managed `.gitignore` / `.dockerignore` entries, and corresponding `health-check` expectations. It may activate only the reviewed artifact's required module; activation authorizes no unrelated mutation. Root/nested Agent bridges continue to point at `.upgrade/AGENTS.md` rather than duplicating every artifact rule.

## Lifecycle Boundary

Governance-classified, required, or permanent registrations are cleanup-retained, and collection never overwrites a registered target. Change or unregister policy through this workflow before ordinary cleanup handles the file.
