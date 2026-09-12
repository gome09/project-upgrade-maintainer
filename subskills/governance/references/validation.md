# Validation Evidence

Record actual validation with `validation-record` rather than treating a planned command as proof. A valid operation may lazily activate the `validation` module after status/exit-code and subject checks pass; invalid evidence must not create or activate validation resources.

- `passed`: the check was actually run and succeeded.
- `failed`: the check was actually run and failed.
- `unverified`: the check was not run or could not be completed.

## Subject Binding

Validation intended for a Delivery Gate should be bound to the exact validated subject:

- `--subject-path <path>` hashes a repository-local file/directory;
- `--subject-digest <sha256> --subject-ref <name>` records an immutable external artifact/build identity;
- otherwise the CLI automatically fingerprints the repository-root Git worktree when available.

Outside a readable Git repository root, evidence without an explicit subject remains `unbound` and cannot satisfy a Delivery Gate. Record the environment and scope deliberately: Delivery matches check + subject digest + scope + environment exactly.

Validation JSONL is append-only integrity evidence. Each new record stores the digest of the previous raw record and its own canonical record digest. `health-check` and Delivery validate the chain before trusting it. Unversioned validation lines remain readable, but they have no subject binding and cannot satisfy subject-bound Delivery requirements.
