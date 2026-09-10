# Model request compatibility

User outcome: maintain model-specific compatibility in the existing CCG review center and apply it before upstream API requests; implement with GPT-5.6 Terra. A configured provider/model that rejects thinking.type=disabled (current glm-5.3-flash) must receive the same request without the thinking object; other models, providers, and thinking modes remain unchanged.

Both the original main Claude Code Stop evaluator path and CCG review/analyze leaves must have a concrete supported integration path. Do not claim a supervisor-only change repairs the original Stop hook. Exact on-wire request model and configured provider identify rules; aliases require explicit configuration, never inference from UI display names or prior responses.

Requirements: persisted validated configuration and management UI; default passthrough; explicit provider/model matching; private atomic writes and conflict handling; preserve streaming, upstream status/errors and credentials; no credential/request-body logging; visible saved-vs-active scope; narrow rule disable/rollback; package/installer coverage; documentation; regression tests proving modified and untouched paths; browser verification. Do not globally change thinking, remove hooks, spoof evaluator approval, or modify external gateway state without knowing its ownership.

Design pending transport reconnaissance and independent Codex/Claude analysis run 1cd9fa76-3ec7-466a-9cb3-faa2b572da12.
