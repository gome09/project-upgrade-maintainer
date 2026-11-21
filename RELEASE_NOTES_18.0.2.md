# Project Upgrade Maintainer 18.0.2

- Validation rejects directory links when the subject snapshot cannot cover
  their targets. Changes to bound links invalidate prior evidence and approval.
- Recovery can roll back interrupted transactions that wrote the same file
  several times. It still checks for external changes and requires exact approval.
- Retrying initialization preserves the workspace profile once the configuration
  has been published and restores missing maintenance records. If the first
  attempt stopped before publishing a custom configuration, retry with the
  original options to keep that selection.
- Delivery reviews handle mixed subject-bound and unbound evidence, and
  `inspect` can expand details from compact review output.

Regenerate and review any plans saved before the upgrade. If directory links
are rejected during validation, select a physical subject that covers the
dependencies, then rerun validation and review.

Download `project-upgrade-maintainer-18.0.2.skill` from this release.

SHA-256 `52e9e952e3f0ed858d637f61b7baf69441cef04cd2bb893d9fb5c0a999281900`
