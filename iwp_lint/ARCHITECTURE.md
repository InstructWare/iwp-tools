# iwp-lint 架构与工作机制说明

本文档面向后续维护者，说明 `iwp_lint` 的代码架构、核心数据流和关键设计决策。

## 1. 目标与边界

`iwp-lint` 的目标是把 IWP 文档与代码实现之间的映射关系做成可校验、可门禁、可回归的工程流程。

- 输入：
  - IWP Markdown（`InstructWare.iw/**/*.md`）
  - 代码注释映射（`@iwp.link <source_path>::<node_id>`）
  - 差异来源（filesystem snapshot）
- 输出：
  - 结构化诊断（`IWP10x` / `IWP20x`）
  - 覆盖率指标（`NodeLinked%`、`CriticalNodeLinked%`、`NodeTested%`）
  - 控制台报告与 JSON 报告

非目标：

- 不校验业务代码语义正确性
- 不做语言级 lint（类型、风格、格式化）
- 不承担完整构建编排（由 `iwp_build` 负责）

## 2. 模块总览

```mermaid
flowchart LR
  CLI["cli.py\n命令入口"] --> CFG["config.py\n配置加载"]
  CLI --> API["api.py\n稳定库 API"]
  API --> ENG["core/engine.py\n质量规则执行"]

  ENG --> MP["parsers/md_parser.py\nMarkdown 节点解析"]
  MP --> NR["parsers/node_registry.py\n稳定 Node ID 分配"]

  ENG --> CS["parsers/comment_scanner.py\n代码注释扫描"]
  ENG --> SV["schema/schema_validator.py\nSchema 校验"]
  SV --> SL["schema/schema_loader.py\nSchema 读取"]
  SV --> SS["schema/schema_semantics.py\n文件类型/Section 解析"]
  API --> DF["vcs/diff_resolver.py\nSnapshot diff resolver"]
  API --> SP["vcs/snapshot_store.py\n快照/会话 SQLite 存储 + 文件采集"]
  API --> SD["vcs/snapshot_diff.py\nDiff 计算层 (markdown + code)"]
  ENG --> NC["core/node_catalog.py\nCatalog facade + compile/verify orchestration"]
  NC --> NQ["core/catalog_query.py\nQuery/Export ranking"]
  NC --> NI["core/node_index_store.py\nSQLite index IO"]
  NC --> CW["core/compiled_writer.py\n.iwc dual artifact writer"]
  NC --> CV["core/compiled_verifier.py\nCompiled artifact verifier"]
  ENG --> CP["core/coverage_policy.py\nCoverage/profile thresholds"]
  ENG --> LV["core/link_validation.py\nlink->node mapping diagnostics"]
  API --> SES["core/session_service.py\nsession start/diff/commit/current/audit"]
  API --> HIS["core/history_service.py\ncheckpoint list/restore/prune orchestration"]

  ENG --> OUT["console + json report"]
```

### 2.1 目录职责

- `cli.py`：命令行参数解析与输出渲染
- `api.py`：稳定库化入口（供 `iwp_build` 或其他 orchestrator 直接调用）
- `config.py`：配置模型与 `.yaml/.json` 加载
- `core/engine.py`：`full/diff/schema` 质量规则执行
- `parsers/md_parser.py`：Markdown 节点提取（heading/list）+ kind 计算
- `parsers/node_registry.py`：稳定键（stable_key）分配 + canonical 短 `node_id` 写回
- `parsers/comment_scanner.py`：代码注释扫描与协议正则校验
- `schema/*`：schema profile 读取、文件类型匹配、section 合法性校验
  - 支持可选 `schema.page_only.enabled`：在 `views.pages` 中识别 namespaced H2（`Logic.*` / `State.*`）并映射到 `logic/state` 语义
  - namespaced 映射规则来自 schema `authoring_rules.aliases`，lint/build 只做解释执行
- `vcs/diff_resolver.py`：DiffProvider 抽象与受影响节点筛选
- `vcs/snapshot_store.py`：snapshot/session 基线与审计事件（SQLite）+ workspace 文件采集
- `vcs/snapshot_diff.py`：diff 计算逻辑（文件变更、markdown 行级、代码行级/hunk）
- `core/node_catalog.py`：catalog/compiled facade 编排入口
- `core/catalog_query.py`：catalog query/export 与相似度排序
- `core/node_index_store.py`：`node_index.v1.sqlite` 读写
- `core/compiled_writer.py`：`.iwc.json/.iwc.md` 双产物写入与清理
- `core/compiled_verifier.py`：compiled 产物一致性校验
- `core/coverage_policy.py`：覆盖率阈值、tiny-diff 与 profile 规则
- `core/link_validation.py`：注释链接对账与 `IWP103/IWP105` 诊断
- `core/link_normalizer.py`：`@iwp.link` 清理与规范化（stale/重复移除、块内排序与标准格式重写）
- `core/history_service.py`：history list/restore/prune 编排（预检、dry-run、恢复前安全点、保留策略）

## 3. 关键数据模型

### 3.1 `MarkdownNode`（`core/models.py`）

关键字段：

- `node_id`：节点 canonical 短 ID（`n.<hex_prefix>`，按 source_path 最短唯一）
- `source_path`：相对 IWP 根路径（例如 `views/pages/home.md`）
- `line_start`/`line_end`：节点在原文中的行范围
- `section_key`、`file_type_id`、`computed_kind`：Schema 语义上下文
  - 在 Page Only 模式下，`source_path` 保持页面路径；`file_type_id`/`computed_kind` 会映射到 `logic.*` 或 `state.*`
- `is_critical`：关键节点标记（由配置关键词匹配）

### 3.2 `LinkAnnotation`

从代码注释扫描得到，包含：

- `source_path`
- `node_id`
- `file_path`, `line`, `column`

### 3.3 覆盖身份键（NodeKey）

覆盖统计统一按 `(source_path, node_id)` 计算，避免不同文件中相同 `node_id` 产生误判。

### 3.4 SnapshotFile（`vcs/snapshot_store.py`）

- `path`：项目相对路径
- `kind`：`markdown` 或 `code`
- `mtime_ns` / `size` / `digest`：变更识别指纹
- `content`：SQLite 文本快照（兼容/回退路径）；主恢复内容源为 Dulwich commit tree

### 3.5 CodeDiffOptions 与代码差异明细（`vcs/snapshot_diff.py`）

- `CodeDiffOptions.level`：`summary` 或 `hunk`
- `CodeDiffOptions.context_lines`：hunk 上下文行数
- `CodeDiffOptions.max_chars`：hunk 总字符上限
- `compute_code_change_details(...)` 默认输出：
  - `file_path`
  - `change_kind`（`added|modified|deleted`）
  - `changed_line_count`
  - `changed_line_ranges`
- 当 `level=hunk` 时附加：
  - `hunks`
  - `hunks_truncated`

### 3.6 Checkpoint / Baseline 状态（`vcs/snapshot_store.py`）

- `baseline_state.current_snapshot_id`：当前 baseline 指针（恢复后会切换）
- `checkpoints`：可回滚检查点元信息（`source/session_id/baseline_snapshot_id/gate_status/git_commit_oid`）
- `history_events`：restore/prune 事件链（`restore_dry_run` / `restore_applied` / `restore_blocked` / `prune_done`）

## 4. 执行路径与运行能力

## 4.1 `full` 模式

```mermaid
sequenceDiagram
  participant CLI as cli.py
  participant ENG as engine.run_full
  participant MD as md_parser
  participant SC as schema_validator
  participant CS as comment_scanner

  CLI->>ENG: run_full(config)
  ENG->>MD: parse_markdown_nodes(...)
  MD->>MD: 解析 heading/list 节点
  MD->>MD: Node Registry 分配/续接 node_id
  ENG->>SC: validate_markdown_schema(...)
  ENG->>CS: discover_code_files + scan_links
  ENG->>ENG: link 协议校验 + source_path/node_id 对账
  ENG->>ENG: 覆盖率计算 + 阈值判断
  ENG-->>CLI: report
```

特点：全量节点、全量代码扫描，适合作为主分支或夜间基线检查。

## 4.2 `diff` 模式

```mermaid
flowchart TD
  A["load_diff(base,head,provider)"] --> B["parse_markdown_nodes(all)"]
  B --> C["impacted_nodes(by changed lines)"]
  A --> D["changed_md_files / changed_code_files"]
  C --> E["_run_core(target_nodes=impacted)"]
  D --> E
  E --> F["增量诊断 + 增量覆盖率 + 全量节点总数摘要"]
```

特点：

- 只对受影响节点进行覆盖门禁，降低 CI 成本
- 固定基于 filesystem snapshot 基线

## 4.3 `snapshot` 模式（API 内部能力）

`snapshot` 通过 `api.py` 提供给编排层（`iwp-build`）使用；不作为 `iwp-lint` 的用户命令入口。

```mermaid
flowchart TD
  A["snapshot init/update"] --> B["collect_workspace_files"]
  B --> C["persist snapshots.sqlite"]

  D["snapshot diff"] --> E["load latest snapshot"]
  E --> F["scan current workspace"]
  F --> G["compute changed files/lines"]
  G --> H["resolve impacted nodes"]
  H --> I["return changed files + impacted nodes"]
```

特点：

- 无版本控制工具依赖
- 可在脏工作区工作
- 直接产出变更摘要供编排层消费

## 4.4 `session` 模式（API 内部能力）

`session` 是基于 snapshot 的任务会话运行时，提供给 `iwp-build` 编排层消费：

- `session_start`：创建 open session，记录 `baseline_id_before`
- `session_current`：读取当前 open session
- `session_diff`：对比当前工作区与 session baseline，输出：
  - `changed_md_files` / `changed_code_files`
  - `changed_code_details`（summary/hunk）
  - `impacted_nodes`
  - `link_targets_suggested`
  - `link_density_signals`
- `session_commit`：执行 gate（可配置）并原子推进 baseline，同时写入 Dulwich checkpoint 并记录 `git_commit_oid`
- `session_gate`：执行 compiled + lint gate，返回 `gate_status` 与阻断原因
- `session_audit`：读取事件链（`session_events`）

## 4.5 `schema` 模式

仅执行 markdown schema 校验，不执行链接覆盖率逻辑。

## 4.6 `history` 模式（API 内部能力）

`history` 是文件级历史回滚能力（`SQLite + Dulwich Git objects`），供 `iwp-build history *` 消费：

- `history_checkpoint`：创建开发态 checkpoint（不跑 gate）
- `history_list`：列出 checkpoint 与当前 baseline
- `history_restore`：
  - 支持 `dry_run` 预览影响清单
  - 默认脏工作区阻断，`force` 才允许覆盖
  - 可选自动创建 `restore_before_apply` 安全点
  - 默认按 `git_commit_oid` 读取 Dulwich 内容；可通过 `history.safety.strict_dulwich_restore` 与 `allow_sqlite_fallback` 调整恢复策略
  - 恢复后返回 `next_required_actions`（`verify` / `session reconcile`）
- `history_prune`：按保留策略清理历史 checkpoint 与孤儿 snapshot
  - prune 后触发 `gc v1`（仅清理不可达 loose objects，保守模式）

健壮性补充：

- 并发互斥使用 OS 文件锁（Windows `msvcrt.locking` / Unix-like `fcntl.flock`）
- snapshot 采集在读文件前执行大小阈值校验（`tracking.snapshot.max_file_size_kb`）
- Dulwich 仓库损坏时支持一次性自动备份并重建，随后通过事件审计记录

## 5. Node ID 稳定策略（重点）

早期实现使用结构序号，重排容易导致 ID 漂移。当前版本改为：

- 语义签名：`source_path + file_type_id + section_key + node_type + parent_chain + anchor_text`
- 文本归一化：Unicode NFKC + `casefold` + 去噪
- stable_key 分配策略：
  1. 精确签名命中（复用历史 stable_key）
  2. 同池模糊匹配（相似度阈值）
  3. 新建 stable_key（内部键）
- canonical `node_id` 生成策略：
  1. 对同一 `source_path` 内全部 stable_key 取 hash 前缀
  2. 默认最短长度 4（可配置 `node_id_min_length`）
  3. 若发生碰撞，对冲突项逐位扩展直到唯一

注册表文件（默认）：

- `.iwp/node_registry.v1.json`

```mermaid
flowchart LR
  S["当前节点签名"] --> E{"历史精确命中?"}
  E -- Yes --> U1["复用 stable_key"]
  E -- No --> F{"同池模糊匹配>=阈值?"}
  F -- Yes --> U2["复用 stable_key"]
  F -- No --> N["生成新 stable_key"]
  U1 --> P["按 source_path 生成最短唯一前缀 node_id"]
  U2 --> P
  N --> P
  P --> W["写回 registry (uid=node_id, stable_key=内部键)"]
```

维护建议：

- 团队协作场景建议将 registry 纳入版本控制
- 对 registry 冲突采用“以最新文档解析结果覆盖并重跑 full”策略

## 6. 诊断与退出码

## 6.1 典型错误码

- 链接协议与映射：`IWP101`~`IWP109`
- Schema 结构：`IWP201`~`IWP205`

## 6.2 退出码约定

- `0`：无 error（warning 不阻断）
- `1`：存在 error（门禁失败）
- `2`：运行时错误（例如 snapshot 基线缺失或配置错误）

## 7. Catalog 索引策略

`nodes build` 会同时产出：

- 人类/审计导出：`.iwp/node_catalog.v1.json`
- 查询索引：`.iwp/cache/node_index.v1.sqlite`

查询逻辑：

1. 优先读取 sqlite 索引
2. 索引不存在时回退 JSON

这样兼顾了可读导出与高效查询。

### 7.1 Agent 上下文 sidecar（`.iwc`）

`nodes compile` 会在 `.iwp/compiled/**` 生成按源 markdown 拆分的双产物，供 agent 与校验流程协同使用：

- 机器权威格式：`.iwp/compiled/json/**/*.iwc.json`
- agent 友好格式：`.iwp/compiled/md/**/*.iwc.md`

`.iwc.json`（canonical）包含：

- 文档级：`artifact=iwc`、`version=1`、`source_path`、`source_hash`、`generated_at`、`schema_version`
- 字典池：`dict.kinds`、`dict.titles`、`dict.sections`、`dict.file_types`
- 节点级：固定 10 列 tuple（`node_id`、`anchor_text`、`kind_idx`、`title_idx`、`section_idx`、`file_type_idx`、`is_critical`、`source_line_start`、`source_line_end`、`block_text`）
- `block_text` 为必需字段，用于给 agent 提供原始 markdown 片段锚点

`.iwc.md`（agent view）包含：

- 文档头部元信息注释：`@iwp.meta artifact=iwc_md`、`version=1`、`source_path`、`source_hash`、`schema_version`、`generated_at`、`entry_count`
- 节点注释：`<!-- @iwp.node id=<node_id> -->`（仅保留 `id`，避免注释噪音）
- 节点正文：保持原始 markdown 片段顺序，不在可见正文中混入 node id

`nodes verify-compiled` 用于校验双产物一致性：

- 缺失：源 markdown 存在但 `.iwc.json` 或 `.iwc.md` 缺失
- 过期：`source_hash` 与当前源文件不一致
- 非法：JSON/Markdown 结构异常，或 `.iwc.json` 与 `.iwc.md` 的 `entry_count`、节点顺序不一致

当 `verify-compiled` 发现上述问题时，CLI 返回非 0，用于 CI 门禁。

## 8. API 稳定入口

稳定入口在 `iwp_lint/api.py`，用于被 `iwp_build` 或第三方编排器复用：

- `snapshot_action(config, action)`
- `baseline_status(config)`
- `run_quality_gate(config)`
- `run_gate_suite(config)`
- `compile_context(config, source_paths=None)`
- `verify_compiled(config, source_paths=None)`
- `normalize_annotations(config, write=False)`
- `build_code_sidecar(config)`
- `session_start(config, ...)`
- `session_current(config)`
- `resolve_session_id(config, ..., session_id=None, action, auto_start_session=False)`
- `session_diff(config, ..., code_diff_level=None, code_diff_context_lines=None, code_diff_max_chars=None)`
- `session_reconcile(config, ..., session_id=None, normalize_links=False, auto_build_sidecar=False)`
- `session_commit(config, ..., enforce_gate=True, message=None)`
- `session_gate(config, ..., session_id)`
- `session_audit(config, ..., session_id)`
- `history_list(config, ..., limit=None, include_stats=True)`
- `history_checkpoint(config, ..., actor=None, message=None)`
- `history_restore(config, ..., to_checkpoint_id, dry_run=False, force=False)`
- `history_prune(config, ..., max_snapshots=None, max_days=None, max_bytes=None)`

规则：

- 业务逻辑应优先落在 API/核心模块，不放在 CLI 渲染层
- API 返回结构需要保持稳定与可预测

## 9. 可扩展点

推荐按以下位置扩展：

- 新增注释协议：`parsers/comment_scanner.py`
- 新增 node 解析规则：`parsers/md_parser.py`
- 新增 schema 约束：`schema/schema_validator.py`
- 新增 coverage 口径：`core/engine.py` 与 `core/models.py`
- 新增 diff provider：`vcs/diff_resolver.py`
- 新增 diff 算法与输出契约：`vcs/snapshot_diff.py`
- 新增 snapshot/session 存储策略：`vcs/snapshot_store.py`

原则：

- 先扩展数据模型，再扩展校验流程，最后补测试
- CLI 只做参数与输出，核心逻辑放 API
- 对外报告字段尽量保持稳定

## 10. 维护检查清单

每次改动建议至少执行：

```bash
python -m unittest iwp_lint.tests.test_regression
python -m unittest iwp_lint.tests.test_session_service
python -m unittest iwp_lint.tests.test_history_service
python -m iwp_lint schema --config .iwp-lint.yaml
python -m iwp_lint full --config .iwp-lint.yaml
```

如果修改了 diff、snapshot 或 node_id 逻辑，额外建议：

- 构造“重排列表”“文案微调”“跨语言文档”场景做回归
- 检查 `.iwp/node_registry.v1.json` 是否符合预期演化
- 检查 `.iwp/cache/snapshots.sqlite` 与 `.iwp/compiled/` 产物是否符合预期
- 检查 `history list/restore/prune` 的事件链与 checkpoint 保留是否符合预期
