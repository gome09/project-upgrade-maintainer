<!-- project-upgrade-maintainer:start -->
## Project Upgrade Governance

This repository contains a governed upgrade workspace at `.upgrade/`. Before reading, planning, or modifying upgrade-workflow artifacts, read `.upgrade/AGENTS.md`, which is the canonical scoped governance contract for `.upgrade/**`.

Canonical context: `.upgrade/CONFIG.json` (active modules), `.upgrade/ARTIFACTS.json` (registered artifact policies), `.upgrade/STATE.md` (current state), `.upgrade/MANIFEST.md` (inventory), `.upgrade/docs/UPGRADE_REQUIREMENTS.md` (requirements), and `.upgrade/docs/UPGRADE_PLAN.md` (upgrade plan). Additional phase/evidence/lifecycle resources materialize only when their modules are active.

Do not bypass the review -> exact approval -> apply lifecycle for governed mutations. Protected or sensitive collection requires exact-path authorization again at apply time. Repository/local instructions remain authoritative for their own scope; surface conflicts instead of silently resolving them.
<!-- project-upgrade-maintainer:end -->
