# Release Guide

Use this checklist for public releases.

## 1. Confirm version metadata

Keep these values intentionally aligned with the release being published:

- `core/constants.py` → `SKILL_VERSION`
- `SKILL.md` → reported Skill / Policy / Plan Schema metadata
- tests that characterize the public version
- `CHANGELOG.md`

Policy and plan schema versions are compatibility controls and do not need to change for every Skill release.

## 2. Run validation

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/e2e_acceptance.py
```

Also review the repository for accidentally committed credentials, local workspace state, bytecode, generated archives, and private paths.

## 3. Build the installable Skill

```bash
python tools/build_skill.py \
  --output dist/project-upgrade-maintainer-<version>.skill
```

The builder uses a fixed ZIP timestamp and sorted file order, so an unchanged source tree produces a byte-for-byte reproducible archive.

The release archive intentionally includes `LICENSE` and excludes repository-only assets such as tests, GitHub configuration, and release tooling.

## 4. Verify the archive

Inspect the archive before publishing:

```bash
python - <<'PY'
import zipfile
from pathlib import Path
p = Path('dist/project-upgrade-maintainer-<version>.skill')
with zipfile.ZipFile(p) as zf:
    assert zf.testzip() is None
    names = set(zf.namelist())
    assert 'SKILL.md' in names
    assert 'LICENSE' in names
    assert not any(n.startswith('tests/') for n in names)
    assert not any('__pycache__' in n or n.endswith('.pyc') for n in names)
print('archive OK')
PY
```

Record the SHA-256 printed by the builder in the GitHub release notes.

## 5. Publish source and release artifact

1. Commit the reviewed source tree.
2. Push the tag matching the release version.
3. Create a GitHub Release from that tag.
4. Attach the `.skill` archive.
5. Include the SHA-256 and notable safety/compatibility changes in the release notes.

Do not commit generated `.skill` files to the main source tree; publish them as release assets instead.

## Repository settings recommended for public release

After creating the GitHub repository, enable where available:

- branch protection / required CI checks for the default branch;
- secret scanning and push protection;
- private vulnerability reporting;
- dependency/security alerts for GitHub Actions;
- signed tags or verified release commits if your workflow supports them.
