# Decision Governance

When an apply response is lost, retry the same saved plan and exact approval ID. An already persisted unchanged sealed proposal returns without another write. Audit current records before generating another review; semantically similar new reviews remain distinct user decisions.

Use `decision-review`, `decision-apply`, and `decision-audit` for user-selected technical direction. Decision records under `.upgrade/decisions/` are immutable and never overwritten; a new record may explicitly `supersedes` the current one. The CLI may structure and validate evidence but never chooses the technical direction.

New records are sealed only at apply time, after exact Plan Schema 3 approval. The persisted record includes the actual `approval_plan_id` and a canonical `record_digest`; audit and Delivery reject content that no longer matches that seal. Schema v1 records remain readable, but they are reported as unsealed evidence in strict final-acceptance governance.
