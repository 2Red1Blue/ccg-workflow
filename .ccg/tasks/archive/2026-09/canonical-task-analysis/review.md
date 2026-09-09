# Review and validation

## Implemented design

Canonical task identity is selected root/provider + explicit slug and exact title. CCG metadata is initialization-only; ensure cannot reset existing task state. Documents and updates use a validated existing task directory. Trellis owns native tasks where configured. Analysis run identity and task intent are separate, with shared durable supervision and distinct report contracts.

## Findings resolved

- Added 30-second native Trellis create timeout, preserving artifacts and pending creation marker on uncertain failures; doctor reports explicit recovery.
- Restored validate_review_report compatibility wrapper and test.
- Clarified initialization-only metadata semantics; unchanged-state tests cover both providers and replay of a completed task.
- Fixed macOS root aliases while retaining below-root symlink rejection.
- CCG phase completion also updates status; native Trellis state remains provider-owned.

## Evidence

- Real dual analysis run 26b7a6f2-d9c3-479b-840c-cc32d02d749d: both leaves succeeded, no review verdict, canonical task association persisted.
- Full review 147ba589-b41c-4d7b-821a-535f523b2b18: Claude CLI APPROVE; Codex requested the two corrections above.
- Runtime actual Claude-path model: glm-5-3-flash via existing CC Switch mapping. Codex requested model: gpt-5.6-luna; actual response model unavailable in text transport.
- Installer tests: 5 passed. TypeScript typecheck, package build, Python compile and JS syntax checks passed.
- Installed component 1.2.0 file hashes match package source; installed ensure/write/update/doctor exercised successfully.
- Live history API returns mode=dual_leaf_analysis, verdict=analyzed for the real run.
- Host guidance appended with backups, preserving the full existing instructions and creative/media exemptions.

The router coordinates cooperative CLI calls; arbitrary shell mkdir or another native writer bypassing it is outside its concurrency guarantees. Doctor reports legacy issues without automatically modifying other tasks.

## Final outcome

Full Python regression: 130 passed; one additional completed-task replay test passed. Final corrective cross-model review 5c4a8b41-706b-4b54-a49c-2be6abfe9b04: both leaves succeeded and APPROVE. No blocking finding remains.
