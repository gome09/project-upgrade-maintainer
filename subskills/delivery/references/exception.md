# Exception and Waiver Governance

Use `exception-review`, `exception-apply`, and `exception-audit`. Records under `.upgrade/exceptions/` are immutable; changes create a new record with `supersedes`. Acceptance is a user decision. Active exceptions/waivers may link Decision/Risk evidence, may expire, and remain visible to Delivery. Resolved/revoked records require explicit resolution and must supersede an existing record.

New records are sealed only after exact plan approval and persist both `approval_plan_id` and a canonical `record_digest`. Audit and Delivery reject out-of-band content changes that do not match the seal. Delivery scope selection prevents an unrelated scoped exception from silently controlling another release scope. Schema v1 records remain readable but are unsealed evidence for strict final acceptance.
