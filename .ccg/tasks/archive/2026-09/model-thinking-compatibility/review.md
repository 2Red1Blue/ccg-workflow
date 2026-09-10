# Model compatibility delivery review

## Outcome
Implemented by GPT-5.6 Terra in CCG and an isolated cc-switch worktree. CCG manages private revisioned exact-provider/model rules; the gateway removes thinking only for an enabled exact match with type disabled after model mapping and overrides. Missing/invalid policy preserves baseline requests; invalid policy is reported. Settings default is ~/.claude/.ccg/model-compatibility/config.json, with explicit absolute CCG_MODEL_COMPAT_CONFIG override.

## Evidence
- CCG final policy/web/history tests:23 passed. Installer tests:5 passed. Typecheck and build passed. Real temporary package install imported both modules and served policy GET/PUT.
- Browser: add/save, second save to disable, reload persistence,390px width with no horizontal overflow, corrupt-file recovery disables edit until repaired/reloaded. Only temporary policies were used.
- Gateway: cargo fmt --check,2 real HTTP RequestForwarder tests (exact cases plus provider failover),6 policy tests, cargo clippy --lib and git diff --check passed.
- Removing the production hook made the HTTP test fail with exit101 and thinking present; restored source passes. Four normal cases cover exact match, enabled mode, another provider and another model. Failover503→200 preserves thinking for the unmatched fallback.
- Shared Rust/Python canonical fixture digest:88d71177b78c0b62f2b96a89683fd8254bf182e40edcfd267b97b737caeab035.

## Independent review
- Analysis1cd9fa76-3ec7-466a-9cb3-faa2b572da12:both reports succeeded.
- Initial review13ee79fd-8a29-441f-b6de-dd91f77e9226:deep-JSON RecursionError fixed; Claude thinking timeout did not grant approval. Socket-timeout/C1 complaints were checked against existing5s deadline and explicit C0/DEL schema.
- CCG fixed review4e9fa45c-e777-4194-acc2-015c3a82a6dd:both APPROVE; recovery diagnostics and exact short-body checks subsequently included in combined review.
- Combined e8ac9194-d579-4f31-8abe-4002a6425434:both executions succeeded; Claude APPROVE. Codex REQUEST_CHANGES rested solely on an incorrect duplicate-env-import claim:the two imports are in separate files, one per file, verified against snapshot and successful compiled tests. Finding rejected, not silently changed to approval.
- Final delta b0d84328-2b65-44f2-98d4-6151a29ccdd1:both APPROVE, no Critical/Warning. Covers platform home resolution, POSIX wording and genuine failover regression. Remaining optional test-message refinements are not acceptance gaps.
- Final index-tree audit confirmed current implementation equals the reviewed full snapshots plus reviewed delta.

## Deployment scope
No installed app, live database, credentials, hooks or official policy were modified. Gateway source is isolated on codex/model-thinking-compatibility from v3.20.1; installed CC Switch is v3.20.2. Merge the gateway change into the intended current release before deployment; do not downgrade/replace the installed app with the older base. Source implementation and local verification are complete; live Stop-hook acceptance was not claimed.

## Source commits
CCG:6ce3450. Gateway:b6c853f4. Both are local commits; no push or live installation.
