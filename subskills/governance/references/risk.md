# Risk Governance

Retry the same saved plan after a lost response. An unchanged latest revision sealed by that exact plan returns without appending again. A newer revision or changed content requires a fresh review; repeating risk-review is not the retry mechanism.

Use `risk-review`, `risk-apply`, and `risk-audit`. Risk history is append-only JSONL under `.upgrade/risks/`; states are `open`, `accepted`, `mitigated`, and `closed`. Every revision after the first needs an explicit `--transition-reason`, even when the status is unchanged. Acceptance needs explicit user rationale, mitigation needs mitigation/evidence, and closure needs resolution.

Risks may carry an optional scope. Delivery with a named scope consumes global or exact-scope risks and ignores unrelated scoped risks.

New revisions are sealed at apply with the exact `approval_plan_id`, their own canonical `record_digest`, and a `previous_record_digest` linking the revision to the exact previous record content. Apply validates the complete proposed history before writing; audit and Delivery reject a broken chain. A v2 revision may safely extend a Schema v1 history by hashing the raw predecessor as its chain anchor.
