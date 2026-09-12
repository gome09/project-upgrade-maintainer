# Security Policy

Project Upgrade Maintainer intentionally performs filesystem and governance mutations, so security reports are treated as high priority.

## Supported versions

Security fixes are provided for the latest tagged release. Older releases may be asked to upgrade before a fix is backported.

## Reporting a vulnerability

Prefer GitHub Private Vulnerability Reporting / Security Advisories for the repository when available. Please do not publish exploit details, secrets, private repository content, or destructive proof-of-concept instructions in a public issue.

If private reporting is not available, open a minimal public issue asking the maintainers for a private security contact. Do not include sensitive reproduction data in that issue.

Useful information in a private report includes:

- affected version and command;
- operating system and Python version;
- whether the operation was review-only or mutating;
- the expected safety invariant;
- the observed behavior;
- a minimal reproduction using synthetic/non-secret data;
- whether filesystem escape, stale-plan acceptance, authorization bypass, secret disclosure, transaction corruption, or unsafe deletion is involved.

## Security invariants

The following behaviors are considered security boundaries rather than convenience features:

- repository-root path confinement and symlink safety;
- review → exact `plan_id` approval → apply separation;
- apply-time revalidation of policy, implementation, and source state;
- exact-path authorization for protected/sensitive collection at both review and apply;
- fail-closed handling of malformed or obsolete plan schemas;
- transaction durability and explicit recovery for interrupted mutations;
- permanent purge remaining distinct from recoverable cleanup;
- Agent decision projection never suppressing blockers or required authorization;
- repository-discovered authority never replacing explicit user approval.

A bypass of any of these boundaries should be reported as a security issue.

## Untrusted repositories and prompt injection

Repository content is untrusted input. Agent instruction files, READMEs, migration notes, and other discovered authority candidates can contain misleading or malicious instructions.

The Skill therefore treats discovery separately from authorization: discovered sources are candidates until reviewed, and mutating commands remain bound to deterministic CLI validation and user-approved plans. Users should still avoid granting broad execution authority to untrusted repositories.

## Local evidence and privacy

Generated transaction journals can contain local repository paths, hostnames, process IDs, timestamps, and operation metadata. They are local execution evidence and should not be committed to public repositories.

Never attach real credentials, private keys, `.env` contents, access tokens, or proprietary repository data to a vulnerability report unless a private channel has been established and the data is strictly necessary.
