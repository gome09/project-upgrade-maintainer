# Validation Evidence

Record actual validation with `validation-record` rather than treating a planned command as proof. A valid operation may lazily activate the `validation` module after status/exit-code and subject checks pass; invalid evidence must not create or activate validation resources.

- `passed`: the check was actually run and succeeded.
- `failed`: the check was actually run and failed.
- `unverified`: the check was not run or could not be completed.

For retryable recording, pass `--request-id <run-identity>` identifying one actual check run. Repeating that ID with identical evidence returns the existing record without writing; reuse with different evidence fails. Each new execution needs a new ID, even if the result is identical. Without an ID, each call records a separate execution assertion. IDs are unique across the workspace validation history.

## Subject Binding

Validation intended for a Delivery Gate should be bound to the exact validated subject:

- `--subject-path <path>` hashes a repository-local file/directory;
- `--subject-digest <sha256> --subject-ref <name>` records an immutable external artifact/build identity;
- otherwise the CLI automatically fingerprints the repository-root Git worktree when available.

Outside a readable Git repository root, evidence without an explicit subject remains `unbound` and cannot satisfy a Delivery Gate. Record the environment and scope deliberately: Delivery matches check + subject digest + scope + environment exactly.

Validation JSONL is append-only integrity evidence. Each new record stores the digest of the previous raw record and its own canonical record digest. `health-check` and Delivery validate the chain before trusting it. Unversioned validation lines remain readable, but they have no subject binding and cannot satisfy subject-bound Delivery requirements.

## Local Execution Evidence

Manual `--status` records remain assertions. To have the CLI run the actual check, use:

```text
validation-record --check suite --subject-path src --environment dev --timeout-seconds 600 --request-id run-123 --execute python -m unittest discover
```

Put `--execute` last: everything following it is the command argv. Execution uses the project root as cwd, no shell, and no stdin. Use only a user-authorized check; command side effects are not sandboxed or rolled back by the governance transaction. Do not invoke mutating governance commands recursively while the workspace lock is held. `--status`, `--exit-code`, and `--dry-run` cannot be combined with execution.

The CLI stores argv, start/finish timestamps, exit code, stdout/stderr SHA-256, timeout state, and whether the subject stayed unchanged. It does not retain raw output. Avoid secrets in argv, which is persisted. Zero exit with an unchanged subject passes; nonzero exit, timeout, or subject drift fails. Timeout terminates the direct process; descendants are not guaranteed to stop. Commands should not detach background work. The CLI records the outcome; inspect `result.record.status` rather than treating a successful recording operation as a passed test.

Repeating an execution request ID with the same argv and binding returns the original record without rerunning, even if it failed or expired. Use a new ID for a new run. Local evidence is not a signature or proof that an arbitrary command meaningfully tested the declared subject. External-digest binding still trusts the supplied artifact digest. Use a local path/Git subject when before/after file verification is needed.

An approved Delivery Policy can require execution provenance and set a maximum age; manual evidence cannot satisfy that strict mode. Use the same subject/scope/environment for recording and gates. Choose a subject excluding test-generated files, or clean those outputs before validating it.
