# Fast Mode + 独立 Checkpoint 方案计划

## Summary

目标是在不破坏现有 `session commit` 严格门禁语义的前提下，新增一个**与 session gate 解耦**的纯文件快照能力（`history checkpoint`），并让 `fast mode` 进入 tools 配置层做**可观测与 warning**，但不改变 gate pass/fail 规则。

核心交付：

1. 新增 `history checkpoint` 命令（基于现有 hash snapshot 存储）  
2. 在存在 open session 时，`history restore` 默认阻断并给出明确提示  
3. 增加 `workflow.mode`（`fast|aligned`）配置解析，仅用于提示与冲突告警  
4. 补齐单元测试 + e2e 测试，覆盖 checkpoint/restore/session 交互边界

---

## Current State Analysis

### 1) 快照底座已存在且满足“文件 hash 快照”

- 快照文件由内容 hash（sha256）生成，包含 `digest` 与完整内容：  
  - `tools/iwp_lint/vcs/snapshot_store.py` (`_to_snapshot_file`, `SnapshotFile`)
- 快照/基线/检查点数据结构已存在：  
  - `snapshots` / `snapshot_files` / `baseline_state` / `checkpoints` 表  
  - `create_snapshot(...)`、`create_checkpoint(...)` 已可复用

### 2) 当前“推进基线”绑定在 session commit

- `session commit` 目前包含 gate、sidecar freshness 约束与 checkpoint 记录：  
  - `tools/iwp_lint/core/session_service.py`
- `iwp-build` 命令层目前无 `checkpoint` 子命令：  
  - `tools/iwp_build/commands/history_args.py`（仅 list/restore/prune）

### 3) restore 当前未感知 open session

- `history restore` 目前只检查 dirty workspace，不检查 open session：  
  - `tools/iwp_lint/core/history_service.py`

### 4) fast/aligned 当前未进入 tools 配置层

- `tools/iwp_lint/config.py` 目前没有 `workflow.mode` 配置。

---

## Assumptions & Decisions

### 已确认决策

1. 命令入口采用 `history checkpoint`（而非 `session checkpoint`）  
2. open session 存在时执行 restore：默认阻断并提示  
3. `workflow.mode` 进入 tools 层，但先做 warning，不修改 gate 逻辑

### 语义边界（本次严格保持）

- `session commit` = 通过 gate 的“发布级基线推进”  
- `history checkpoint` = 开发态纯文件快照（可回退），不触发 gate  
- `fast mode` = 编排与可观测层概念，不隐式放宽门禁

---

## Proposed Changes

## A. iwp_lint：新增 checkpoint 服务能力（不影响 commit 语义）

### 文件：`tools/iwp_lint/core/history_service.py`

- 新增 `checkpoint(...)` 方法：
  - 采集当前 workspace 文件（复用 `collect_workspace_files`）
  - `create_snapshot(set_as_baseline=True)` 推进 baseline 指针
  - `create_checkpoint(source="history_checkpoint", gate_status="skipped", ...)`
  - 写入 `history_event`（如 `checkpoint_created`）
  - 返回结构化 payload（checkpoint_id/snapshot_id/file_count/message/created_at）
- 保持恢复逻辑一致性：复用现有 restore/prune 数据模型

### 文件：`tools/iwp_lint/api.py`

- 暴露 `history_checkpoint(...)` API，供 `iwp-build` 调用

### 文件：`tools/iwp_lint/core/history_service.py`（restore 保护增强）

- 在 `restore(...)` 中增加 open session 检查：
  - 若存在 `open|dirty|verified|blocked` session 且未 `force`，返回 `status=blocked` + `blocked_reason="open_session"`
  - 返回 next-step command hints（先结束会话或显式 force）

---

## B. iwp_build：新增 CLI 命令与分发

### 文件：`tools/iwp_build/commands/history_args.py`

- 新增子命令 `history checkpoint`
  - 参数：`--message`、`--json`

### 文件：`tools/iwp_build/commands/option_resolver.py`

- 扩展 `HistoryOptions`：新增 `message`
- 在 `resolve_history_options(...)` 解析 `history_checkpoint` 预设参数

### 文件：`tools/iwp_build/cli.py`

- 引入 `history_checkpoint` API
- 新增 `_handle_history_checkpoint(...)`
- 在 history handler 路由中注册 `checkpoint`
- 输出统一 summary：checkpoint_id/snapshot_id/status

---

## C. workflow.mode（仅可观测与 warning，不改 gate）

### 文件：`tools/iwp_lint/config.py`

- 新增 dataclass：`WorkflowConfig`
  - `mode: str = "aligned"`（可选值 `fast|aligned`，非法值回退默认）
- 挂载至 `LintConfig` 并在 `load_config(...)` 读取 `workflow.mode`

### 文件：`tools/iwp_build/reconcile/payload.py`（或 guidance/summary 层）

- 将 `workflow.mode` 注入输出 payload（例如 `meta.workflow_mode`）
- 增加 warning 规则（不阻断）：
  - `fast + structural`：提示“该组合可能产生高密度未对齐诊断，建议使用 checkpoint 循环后再进入 aligned”

### 文件：`tools/iwp_build/output/*`（必要时）

- 控制台输出补充 mode-aware 提示，不改变 exit code

---

## D. 测试设计（高优先级）

## 1) iwp_lint 单测

### 文件：`tools/iwp_lint/tests/test_history_service.py`

- 用例 A：`history_checkpoint` 成功创建 snapshot/checkpoint 并推进 baseline
- 用例 B：存在 open session 时 `history_restore` 默认 blocked（`blocked_reason=open_session`）
- 用例 C：`force` 时允许恢复

### 文件：`tools/iwp_lint/tests/test_regression.py`

- 用例 D：`workflow.mode` 配置读取与默认值回退
- 用例 E：非法 mode 值处理

## 2) iwp_build CLI/集成测试

### 文件：`tools/iwp_build/tests/test_history_cli.py`

- 用例 F：`history checkpoint --json` 输出字段完整
- 用例 G：checkpoint 后 `history list` 可见 `source=history_checkpoint`

### 文件：`tools/iwp_build/tests/e2e/test_history_restore_flow.py`（扩展）

- 用例 H：open session 下 restore 默认阻断
- 用例 I：先 checkpoint，再 restore，文件回退正确

### 文件：`tools/iwp_build/tests/test_e2e_suite.py`

- 确保新增/扩展 e2e 被总入口覆盖

---

## Verification Steps

按“先单测、后 e2e、最后全套”执行：

1. `uv run python -m unittest iwp_lint.tests.test_history_service`
2. `uv run python -m unittest iwp_lint.tests.test_regression`
3. `uv run python -m unittest iwp_build.tests.test_history_cli`
4. `uv run python -m unittest iwp_build.tests.e2e.test_history_restore_flow`
5. `uv run python -m unittest iwp_build.tests.test_e2e_suite`

验收标准：

- `history checkpoint` 可独立创建回退点（无 gate 依赖）
- `session commit` 语义不变（仍受 gate 约束）
- open session + restore 默认阻断并给出可执行提示
- `workflow.mode` 仅产生可观测 warning，不改变 gate 结果
- 新增与回归测试全部通过

---

## Implementation Order (执行顺序)

1. `iwp_lint`：`history_checkpoint` API + service（含 restore open-session 阻断）
2. `iwp_build`：history checkpoint CLI 接入与输出
3. `config`：`workflow.mode` 解析
4. `reconcile/output`：mode-aware warning 注入
5. 测试补齐与回归验证

