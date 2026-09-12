# Permanent Purge

Cleanup `delete` is reversible and moves files into `.upgrade/tmp/.trash/`. Permanent deletion is allowed only through `purge-review` / `purge-apply` for reviewed trash entries. Show every included file before approval; before apply succeeds, never describe a trashed file as permanently deleted.
