# Model compatibility implementation

## Accepted design
Existing cc-switch is the verified running provider gateway. Its request override supports merge, not conditional removal, and management is Tauri IPC only. Avoid additional HTTP proxy and avoid direct database writes. CCG owns a private shared rule file; cc-switch reads it after all provider/model mapping and overrides, immediately before outbound serialization. CCG review UI edits the file with revision compare and same-origin CSRF protection. All Claude Code clients already routed through an updated cc-switch, including Stop evaluator and CCG leaves, use the same rule. Existing running binary is not updated by source edits; installation/build status must be reported explicitly.

## Frozen cross-language format
Default path ~/.claude/.ccg/model-compatibility/config.json; explicit override CCG_MODEL_COMPAT_CONFIG is absolute and used by both applications. JSON {"version":1,"rules":[{"provider_id":"exact cc-switch provider id","model":"exact outbound model id","omit_disabled_thinking":true}]}. No wildcards, case folding, implicit punctuation normalization or inferred aliases. Multiple exact entries represent intentionally configured spellings. No credentials/URLs in rules. Missing file means empty rules. Valid file canonical SHA256 is revision; CCG API requires If-Match when saving. Unsupported fields/version/duplicates/invalid types/oversize are rejected. UI offers editable glm-5.3-flash example but never enables a wildcard/default global rule. Exact output model may be glm-5-3-flash; must be deliberately selected from evidence.

## Runtime
After model mapping and local provider body overrides, for Claude app Anthropic-compatible messages only, match provider.id + final body.model, then iff thinking.type is exactly disabled remove top-level thinking. No mutation of enabled/adaptive/absent/nonobject thinking or any other field; other provider/model/API paths unchanged. Use bounded regular private file reads, consistent atomic snapshots and diagnostics without prompts/credentials. On malformed/unreadable policy retain the baseline request and emit sanitized warning; CCG management GET reports the invalid file. This preserves unrelated model behavior; the GLM fix is explicitly unavailable until repaired, never counted as applied. Include config digest/provider/model only in successful application diagnostics, never prompts/headers. No retries or synthetic approvals. No API requests via new proxy.

## Parallel Terra ownership
A: CCG Python policy storage module and tests only.
B: CCG web .py/.js/.html/.css + new policy-web tests only, consume A API.
C: isolated cc-switch worktree Rust runtime module, mod wiring/forwarder, contract tests/docs only.
After A/B: Terra handles CCG installer/package/README integration and tests. Parent orchestrates and validates, no authored implementation under other model.

## Acceptance
Cross-language shared fixture, save/load/conflict/security/bounded IO tests, exact provider/model/mode truth table, actual final gateway forwarding path fake-upstream proof of omitted field and unaffected model, user-visible settings save/reload/disable browser proof, build/package tests, independent dual-model final review. Never claim source tests prove installed live Stop fix. Retain existing dirty gateway worktrees.
