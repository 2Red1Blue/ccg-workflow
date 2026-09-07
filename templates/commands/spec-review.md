---
description: '归档前双模型交叉审查（结果写入 CCG Review Center）'
---
<!-- CCG:SPEC:REVIEW:START -->
# /ccg:spec-review

对当前 OpenSpec proposal 做一次可追溯的双模型审查。审查器固定使用独立的 Codex 与 Claude leaf；不要用两个 `codeagent-wrapper run` 伪造“双模型”，也不要把任一超时当作通过。

## Steps

1. 运行 `openspec list --json`，确认要审查的 active change；再运行 `openspec status --change "<proposal_id>" --json`。
2. 读取该 proposal 的 `specs/`、`tasks.md` 以及涉及文件的完整上下文；确认当前 diff 是此 proposal 的范围。
3. 先运行项目相关测试和类型检查。
4. 在 proposal 所在 git 工作目录执行一次持久化审查：

   ```bash
   printf '%s\n' 'Review the current change for correctness, security, regression risk, and maintainability. Return Critical/Warning/Info findings with file:line evidence.' | "$HOME/.claude/bin/ccg-agent-supervisor" review --workdir "$(pwd)" --snapshot-base HEAD --include-untracked
   ```

   这条命令会并行执行 Codex 与 Claude review leaf，并将请求、diff 快照、原始结果和结论写入 CCG Review Center。等待两边结束；任何 leaf 超时、失败或返回的模型不符合预期，都应标记为未完成，不得归档。
5. 综合审查器输出与本地验证，按 `Critical` / `Warning` / `Info` 分类。Critical 必须修复后重新执行第 4 步；Warning 说明是否处理或接受。
6. 只有双 leaf 成功且没有未解决 Critical 时，询问用户是否归档。

## Exit Criteria

- 两个独立 leaf 都有成功、可读的报告；
- 审查记录可在 CCG Review Center 查询；
- 所有 Critical 已修复或用户明确接受；
- 归档决定已记录。
<!-- CCG:SPEC:REVIEW:END -->
