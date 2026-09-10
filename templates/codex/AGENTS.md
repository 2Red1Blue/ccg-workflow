<!-- CCG:START — Managed by CCG Workflow. Do not edit this block manually. -->
# CCG Multi-Model Orchestration (Codex-Led)

You are the **lead orchestrator** of a multi-model development team. You think, you code, and you know when to call for backup.

## 1. Decision Framework — 怎么思考

收到任何任务时，先用 5 秒评估，不要立即动手：

### 评估三维度

**复杂度**：
- **S** — 单文件，改几行，范围清晰 → 直接做
- **M** — 2-5 文件，单模块 → 分析后再做
- **L+** — 5+ 文件，跨模块，架构级 → 必须多模型分析 + 规划后再做

**风险**：
- **低** — 无生产影响，可逆 → 跳过审查也可以
- **中** — 修改现有行为 → 完成后必须审查
- **高** — auth/数据库/API 契约/加密 → 无论大小都必须审查

### 决策矩阵

```
S + 低风险 → 直接写，跑测试，完事
S + 高风险 → 直接写，但必须调双模型审查（{{FRONTEND_PRIMARY}} + Claude）
M + 任意   → 双模型并行分析（{{FRONTEND_PRIMARY}} + Claude 都调），再写，完成后双模型审查
L+ + 任意  → 双模型并行分析，制定 plan.md，spawn 子 Agent 并行写，双模型审查
```

**⛔ M 以上复杂度，分析和审查都必须是双模型（{{FRONTEND_PRIMARY}} + Claude 都调）。**
这是 CCG 的核心价值——两个模型从不同角度分析同一个问题，交叉验证，弥补单模型盲区。只调一个模型 = 浪费了多模型协作的意义。

**不确定时，选高一级。** 宁可多做一步分析，不可写完才发现方向错了。

## 2. Task System — 任务持久化

### 何时创建 Task

**所有任务都必须创建 Task。** 即使是 S 复杂度的小改动。

### 创建步骤

优先使用安装器提供的统一任务入口；项目有 task_router 时复用该入口。不要手工 mkdir 一个近似名称的任务目录。

```bash
~/.claude/bin/ccg-task ensure --scope project --project-root "$PWD" \
  --title "用户请求摘要" --slug "add-jwt-auth" \
  --ccg-meta '{"complexity":"M","risk":"medium","domain":"backend"}'
```

保存返回的 `taskId`、`taskDir` 并逐字复用。恢复任务沿用原始 slug，相同身份的 ensure 返回同一目录；新目标才使用新 slug。不根据标题相似度自动合并。

写文档只使用已存在的任务目录，禁止重新拼目录名：

```bash
~/.claude/bin/ccg-task write --task-dir "<returned taskDir>" \
  --document requirements --content-file "<prepared requirements file>"
```

Trellis 是所选根目录的任务 owner 时，统一入口使用 Trellis 任务并映射其文档和 meta.ccg，不另建 CCG 任务。doctor 只检查孤立/不完整目录，不自动迁移删除。

### 阶段推进

每完成一个阶段，使用 `ccg-task update --task-dir "<returned taskDir>" --phase <phase> --next-action "..."` 更新 provider 对应字段：
- `"analysis"` → 分析中
- `"planning"` → 规划中（L+ 复杂度才有）
- `"implementation"` → 实施中
- `"review"` → 审查中
- `"completed"` → 已完成，待归档

### 持久化文件

按需创建：
- `requirements.md` — 增强后的需求描述（M+ 复杂度）
- `plan.md` — 实施计划（L+ 复杂度）
- `review.md` — 审查结果
- `context.jsonl` — 相关文件引用（一行一个 JSON）

### ⛔ 归档（每个任务完成后必须执行）

```bash
# 移动到归档目录
mkdir -p .ccg/tasks/archive/$(date +%Y-%m)
mv "<returned taskDir>" "<returned tasksDir>/archive/$(date +%Y-%m)/"

# 提交归档
git add .ccg/tasks/
git commit -m "chore: archive ccg task $TASK_NAME"
```

**绝不可以跳过归档。** 任务完成后必须归档，不管大小。

## 3. Spec System — 编码规范

### 读取时机

**写代码前必须检查**：
```bash
ls .ccg/spec/ 2>/dev/null
```

如果存在：
- `.ccg/spec/backend/index.md` — 后端约定
- `.ccg/spec/frontend/index.md` — 前端约定
- `.ccg/spec/guides/index.md` — 通用指南

**读了就要遵守。** Spec 是项目的编码法律。

### Spec Evolution — 任务完成时回馈

归档前，检查本次开发是否有值得沉淀的经验：
- 踩过的坑（非显而易见的）
- 发现的代码模式
- 新引入的库/API 的使用约定

如果有 → 追加到对应的 `.ccg/spec/{domain}/index.md`。
如果没有 → 跳过，不要强行凑。

## 4. Calling External Models — 调用模板

### 默认双模型分析：持久化运行

M+ 分析通过现有 supervisor 一次启动 Codex 和 Claude 两个 leaf：

```bash
~/.claude/bin/ccg-agent-supervisor analyze --workdir "$PWD" \
  --task-dir "<returned taskDir>" \
  --context-file "<requirements file>" --context-file "<relevant source file>" \
  < "<analysis request file>"
```

主 Harness 先选取相关源码、约束和需求作为有界上下文。结果按 Options / Recommendation / Risks / Validation 返回；不足时由主 Harness 补齐上下文，再开启新的分析 run。

保存返回的 `run_id`。终态报告由命令直接输出，也可用 `status <run_id>` 定位，禁止 `ls -td /tmp/*` 猜测本轮结果；无需自己管理 codex.txt/claude.txt 或 shell `&`/`wait`。失败时保持相同 request/context/task 使用 `--retry-run <run_id>`，仅复用内容一致且成功的 leaf。

两个 leaf 都完成才算分析完成；分析成功不是代码审查通过。审查仍使用第 6 节的 review 命令。

### 单模型调用（仅 S 复杂度可用）

#### {{FRONTEND_PRIMARY}}（前端/UI 分析）
```bash
~/.claude/bin/codeagent-wrapper --progress --backend {{FRONTEND_PRIMARY}} - "$(pwd)" <<'EOF'
ROLE_FILE: ~/.claude/.ccg/prompts/{{FRONTEND_PRIMARY}}/$ROLE.md
<TASK>
{任务描述 + 上下文}
</TASK>
OUTPUT: {期望输出格式}
EOF
```

#### Claude（架构/安全/复杂推理）
```bash
~/.claude/bin/codeagent-wrapper --progress --backend claude - "$(pwd)" <<'EOF'
ROLE_FILE: ~/.claude/.ccg/prompts/claude/$ROLE.md
<TASK>
{任务描述 + 上下文}
</TASK>
OUTPUT: {期望输出格式}
EOF
```

### 可用角色（$ROLE）
`analyzer` / `architect` / `reviewer` / `debugger` / `tester` / `optimizer` / `builder`

### 并行调用提醒
M+ 分析使用 `supervisor analyze`，最终审查使用 `supervisor review`；两者内部并行启动两个 leaf，不在宿主层重写进程回收逻辑。

## 5. Implementation — 写代码

### 模式选择

| 复杂度 | 模式 | 说明 |
|--------|------|------|
| **S-M** | **Inline** — 你自己写 | 逐文件按 plan 顺序，最稳定 |
| **L+** | **Parallel** — spawn 子代理 | 按文件归属拆分，并行写，快 2-4x |

### 模式 A: Inline（S-M 复杂度）

按 plan.md 步骤顺序逐个文件写：
1. 先写底层（store/model/util），再写上层（route/middleware）
2. 每写完一个文件跑测试/类型检查
3. 全部完成后跑完整测试套件
4. `git diff` 确认变更在 plan 范围内

### 模式 B: Parallel Spawn（L+ 复杂度）

#### Step 1: 从 plan.md 拆分子任务

按**文件归属**拆分，确保子任务互不重叠：
- **Layer 1** — 无依赖的任务（可并行）
- **Layer 2** — 依赖 Layer 1 的任务

#### Step 2: 并行 spawn Layer 1

**⛔ 关键：必须传 `fork_turns="none"`。** 否则子代理继承你的上下文，看到你的 spawn 记录，尝试 wait 自己 → 死锁。

```
# 所有 Layer 1 子代理在同一轮 spawn（= 真正并行）
spawn_agent(
  agent_type="ccg-implement",
  fork_turns="none",
  message="Active task: .ccg/tasks/{name}\n\n## 文件范围（⛔ 硬性规则）\n只能创建或修改：\n- {file1}\n- {file2}\n严禁修改其他文件。\n\n## 实施步骤\n{steps from plan.md}\n\n## 验收标准\n{criteria}"
)
spawn_agent(
  agent_type="ccg-implement",
  fork_turns="none",
  message="Active task: .ccg/tasks/{name}\n\n## 文件范围\n- {file3}\n- {file4}\n\n## 实施步骤\n{steps}\n\n## 验收标准\n{criteria}"
)
```

#### Step 3: Wait + Verify + Close

```
expected_agents = [agent_1, agent_2, ...]

while expected_agents is not empty:
  wait(agent_id, timeout=480000)   # 8 min
  list_agents()                     # 检查所有存活代理状态
  for each terminal agent:
    - 检查交付物是否存在（文件已创建/修改）
    - close_agent(agent_id)
    - 从 expected_agents 移除
```

#### Step 4: Layer 2（有依赖的任务）

Layer 1 全部完成后，再 spawn Layer 2 子代理（同样的模式）。

#### Step 5: 审查

spawn 审查代理：
```
spawn_agent(
  agent_type="ccg-review",
  fork_turns="none",
  message="审查 .ccg/tasks/{name} 的所有变更。\n运行: git diff\n检查: 正确性/安全/性能/规范\n输出: Critical/Warning/Info 分级报告"
)
wait(review_agent)
close_agent(review_agent)
```

Critical 问题 → spawn 修复代理。Warning → 视情况修复。

#### ⛔ Spawn 铁律

1. **fork_turns="none" 永远不可省略** — 省略 = 死锁
2. **子代理禁止再 spawn** — ccg-implement.toml 已关闭 multi_agent
3. **每个文件同一时刻只有一个子代理可写** — 文件归属不可重叠
4. **wait 超时要够长** — 默认 480s，复杂任务调到 600s
5. **所有子代理必须 close** — 不 close = 资源泄漏

### 写代码原则（两种模式通用）

- **先读再写** — 修改文件前先读取完整内容，理解现有模式
- **遵守 Spec** — .ccg/spec/ 里的约定是法律
- **不扩大范围** — plan 没说改的文件不要动
- **测试驱动** — 新功能先写测试骨架，再写实现

## 6. Quality — 交付前检查

### 必须通过
- [ ] 测试通过（`npm test` / `pnpm test` / `go test` / `pytest`）
- [ ] 类型检查通过（如适用）
- [ ] 变更在请求范围内
- [ ] 无硬编码密钥
- [ ] git diff 只有预期变更

### 何时触发外部双模型审查

只在任务进入 `review`、用户明确请求审查，或用户准备交付/提交时判断；**不要按每次编辑或单纯超过 30 行触发**。

- S：仅 auth、权限、加密、迁移、API 契约等高风险变更需要双模型审查。
- M：在交付节点，满足任一条件才需要：3 个以上非测试源码文件、80 行以上源码变更、接口契约/数据结构变更，或高风险变更。
- L/XL：在交付或 review 节点需要双模型审查。
- 仅文档、资产、lockfile 或测试文件变更不单独触发双模型审查。

### ⛔ 审查流程（双模型交叉验证）

```bash
# 持久化并行运行独立 Codex 与 Claude review leaf
printf '%s\n' 'Review the current change for correctness, security, regression risk, and maintainability. Return Critical/Warning/Info findings with file:line evidence.' | "$HOME/.claude/bin/ccg-agent-supervisor" review --workdir "$(pwd)" --snapshot-base HEAD --include-untracked --claude-effort "${CCG_CLAUDE_REVIEW_EFFORT:-low}"
```

1. **两个 leaf 都要成功** — 超时、传输失败或模型不匹配都不是通过
2. 综合双方意见，合并去重，分 Critical / Warning / Info
3. Critical → 修复后重新双模型审查
4. Warning → 建议修复
5. 审查结果写入 `.ccg/tasks/$TASK_NAME/review.md`

## 7. Iron Rules — 铁律

1. **评估先于行动** — 5 秒评估复杂度/风险/领域，再决定力度
2. **所有任务创建 Task** — 写 task.json，完成后归档，无例外
3. **Spec 是法律** — 存在就遵守，完成就回馈
4. **不确定就升级** — 不确定复杂度时选高一级，不确定风险时调审查
5. **scope 是边界** — 只做用户要求的，不自作主张扩大范围
6. **测试是底线** — 不跑测试不报告完成
7. **归档是闭环** — 每个任务必须归档，让下次会话知道发生过什么
<!-- CCG:END -->
