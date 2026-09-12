# Governance Context Network

Use for `initialize`, `sync`, and governance-related `health-check` findings. Also read `adaptive-bootstrap.md` when profile, module activation, or fixed-footprint workspace registration is involved.

## Canonical State

- `.upgrade/AGENTS.md` is the canonical governance contract for `.upgrade/**`; `.upgrade/CONFIG.json` is the source of truth for selected profile, active modules, and registry provenance; `.upgrade/AUTHORITY.json` is the approved external project authority registry; `.upgrade/AUTHORITY.md` is its generated navigation view; `.upgrade/ARTIFACTS.json` is the source of truth for extension-artifact linkage policy.
- Do not hand-edit managed state or use `sync` to bypass artifact-policy approval.
- The managed governance contract begins with `<!-- project-upgrade-maintainer:governance-contract:v1 -->`. `initialize` creates it; `sync` refreshes it only when that ownership marker is present.
- Never overwrite a pre-existing `.upgrade/AGENTS.md` without the marker. Surface the conflict for user reconciliation.
- Repository-level instructions remain authoritative outside `.upgrade/`.

## Agent Bridges

Existing supported root instruction files receive only a managed bridge to `.upgrade/AGENTS.md`, `.upgrade/AUTHORITY.md`, `.upgrade/CONFIG.json`, and canonical core/planning state; do not duplicate the full policy or assume optional modules exist. Synchronize existing root bridges by default, preserve user content outside markers, and create a root `AGENTS.md` only when `--create-root-bridge` is explicit.

Supported names: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `CURSOR_RULES`, `.clinerules`, `.ai-instructions`, `.windsurfrules`, and supported file-form `.windsurf`.

For nested supported instruction files, detect without creating. Normal initialize/sync does not modify them; `--sync-nested-bridges` may append/refresh only the managed bridge in existing files. Preserve subtree-local rules outside the bridge. Missing nested bridges are optional candidates; stale/conflicting managed nested bridges become findings once this Skill has managed them.

## Health Invariants

`health-check` validates the adaptive governance network, including authority/artifact registry provenance, authority-source drift/conflicts, registered required paths, module consistency, managed AGENTS/AUTHORITY/README views, and applicable managed ignore linkage.

- Missing, symlinked, unmanaged, or drifted `.upgrade/AGENTS.md` is an error.
- Missing, invalid, or unsafe `.upgrade/CONFIG.json` is an error because required active-module footprint is otherwise ambiguous.
- Require resources only for modules active in `CONFIG.json`; inactive modules may be absent. An active phase in `STATE.md` requires the `phase` module.
- Missing/stale managed bridges in existing root instruction files are warnings. Root bridge absence includes an explicit `--create-root-bridge` repair action.
- The structured result exposes workspace profile/modules plus governance-contract, root-bridge, current nested-bridge, and optional nested-candidate status.
