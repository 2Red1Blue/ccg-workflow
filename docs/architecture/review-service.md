# ReviewService contract

Status: current design, 2026-09-13.

A review request binds an immutable target snapshot, review policy revision, reviewer slot, requested independence constraints and acceptance schema.

Required fields include `review_request_id`, `target_snapshot`, `policy_revision`, `reviewer_slot`, `cause_chain`, `role`, requested/effective Harness/model/session identity and result refs.

Two reviewers are not merged merely because they inspect the same target. Independence requirements are checked against actual effective model/provider/session facts when observable; unknown diversity is reported as unknown.

Timeout/transport failure is not a clean review. A completed review remains a claim until its expected artifact/schema and relevant evidence are validated. Target changes after review make the review historical for that snapshot; they do not retroactively corrupt it.
