# IWP 全局动态注解低侵入改造 Plan（首发版）

## Summary

本次改造目标是在不重写现有 lint/build 骨架的前提下，引入全局可用的动态注解语义：

- 支持单标签多参数：`@iwp(...)`、`@no-iwp`；
- 首发版支持 `file/section/kind` 可选参数，允许部分为空；
- 映射优先级采用“注解优先、规则兜底、unknown 允许”；
- `trace_required` 继续作为硬门禁，`kind=unknown` 首发仅告警；
- 全局能力接入，但通过策略分级控制严格度，避免一次性全量阻断。

本方案采用“策略对象 + 解析链 + 统一决策对象”，控制影响面、避免参数透传与 if/else 膨胀。

---

## Current State Analysis

### 1) 当前语义判定分散在解析与 schema 语义函数

- 解析入口在 `tools/iwp_lint/parsers/md_parser.py`，目前主要处理 heading/list，并通过：
  - `resolve_page_only_h2(...)`
  - `resolve_section_keys(...)`
  - `resolve_section_semantic_context(...)`
  进行 file_type/section 的语义映射。
- 以上逻辑在 `tools/iwp_lint/schema/schema_semantics.py` 内，以函数式实现，Page Only 开关为中心。

### 2) 当前数据结构可复用

- `MarkdownNode` 已具备 `file_type_id / section_key / computed_kind`，可作为 canonical IR 节点核心字段。
- `engine.py` 覆盖率、链接、诊断、summary 主流程稳定，适合“加策略、不改骨架”。

### 3) 当前问题与风险

- 若直接扩展现有函数参数，容易引入大面积透传；
- 若在 parser 主循环堆条件，容易出现分支膨胀；
- 若将 kind 作为硬门禁，会大幅提高首发采用门槛。

---

## Proposed Changes

### A. 语法与解析契约（统一、可扩展）

#### A1. 首发语法（单标签多参数）

- `@iwp`
- `@no-iwp`
- `@iwp(file=<file_type_id>,section=<section_key>)`
- `@iwp(kind=<file_type_id>.<section_key>)`

约束：

- 首发支持上述参数可选且允许部分为空；
- 同一行最多一个控制 token（冲突直接诊断）；
- 仅行尾 token 生效；代码块内禁用；
- `@iwp(kind=...)` 与 `file/section` 同时出现时，`kind` 优先并要求一致性校验。

#### A2. 决策对象（单出口）

新增内部语义决策对象（不暴露给用户）：

- `canonical_file_type_id`
- `canonical_section_key`
- `canonical_kind`
- `trace_required`
- `trace_source`
- `diagnostics`（可选，供 parser/schema 汇总）

所有语义判定最终只返回这一对象，避免调用方拼装逻辑。

---

### B. 架构模式（低侵入）

#### B1. SemanticResolver（策略对象）

在 `schema_semantics.py` 引入 resolver 抽象与工厂：

- `SemanticResolver` 协议：
  - `resolve_heading(...)`
  - `resolve_list_item(...)`
  - `resolve_text_line(...)`（后续扩展点）
- `build_semantic_resolver(profile, config) -> SemanticResolver`

#### B2. Resolver Chain（职责链）

解析顺序固定为：

1. `ExplicitAnnotationResolver`（`@iwp(file/section/kind)`）
2. `AliasResolver`（复用现有 authoring aliases）
3. `SchemaTitleResolver`（复用 section_i18n）
4. `PathFallbackResolver`
5. `UnknownResolver`（最终兜底）

所有步骤只做“尝试+返回决策片段”，由聚合器统一归并，避免重复分支。

#### B3. Policy 对象（门禁与严格度）

新增 `AuthoringPolicy`（配置对象）：

- `enable_tokens_globally: bool`
- `kind_unknown_policy: warn|error`（首发默认 `warn`）
- `trace_required_policy: strict|required_only`（首发 `required_only`）
- `scope_profiles`（按路径/文件类型分级严格度）

`engine.py` 不新增散落条件，只调用 policy 方法决定诊断等级与是否阻断。

---

### C. 文件级改造清单（精确到模块）

#### C1. `tools/iwp_lint/parsers/md_parser.py`

改造点：

- 将分散语义调用替换为 `context.semantic_resolver.resolve_*` 单入口；
- 增加 token 提取与剥离（保证 `anchor_text/signature` 稳定，不被 token 污染）；
- 在 `MarkdownNode` 构造时附加 `trace_required/trace_source`（若字段已存在则直接赋值）；
- 维持现有 node_id 分配与短 ID 收敛逻辑不变。

原因：

- parser 只负责“结构提取 + 调 resolver”，不再承担语义策略判断。

#### C2. `tools/iwp_lint/schema/schema_semantics.py`

改造点：

- 保留现有函数（兼容内部调用），新增 resolver 与 chain 实现；
- 将 page-only alias 逻辑下沉为 chain 的一个 resolver，避免 mode 分支散落；
- 提供统一工厂创建 resolver（由 profile + config 驱动）。

原因：

- 将“语义决定”集中在一个模块，降低 parser/engine 耦合。

#### C3. `tools/iwp_lint/core/models.py`

改造点：

- `MarkdownNode` 新增（或确认）：
  - `trace_required: bool = False`
  - `trace_source: str = "default_policy"`
- 保持 `to_dict()` 序列化行为不变。

原因：

- 不引入新节点模型，沿用现有数据路径最小改造。

#### C4. `tools/iwp_lint/core/engine.py`

改造点：

- 在 uncovered 检查时改为基于 policy 判断：
  - `trace_required=true` 且无 link：error（硬门禁）
  - `kind=unknown`：按 policy 默认 warning
- summary 增加：
  - `trace_required_nodes`
  - `trace_required_uncovered_nodes`
  - `trace_token_profile_enabled`
  - `kind_unknown_nodes`

原因：

- 保持 engine 主骨架不变，仅在诊断决策点接 policy。

#### C5. `tools/iwp_lint/config.py`

改造点：

- 新增 `AuthoringConfig`（归并动态注解配置）：
  - `tokens.enabled`
  - `tokens.scope`
  - `kind_unknown_policy`
  - `strict_scopes`（可选）
- `load_config` 仅在单处解析该块，避免散乱布尔参数。

原因：

- 用配置对象替代多布尔开关扩散。

#### C6. `tools/schema/iwp-schema.v1.json`

改造点：

- 不重写现有 section/type 结构；
- 增补 authoring token 规则元数据（可选），声明 file/section/kind 的合法来源和校验方式；
- 保持现有 `kind_rules.format` 与 file_type schemas 可复用。

原因：

- 延续原 schema 资产，避免协议首发即推倒重建。

---

## Assumptions & Decisions

### 已确认决策

1. 首发语法采用“单标签多参数”（不是双标签拼接）。
2. 全局能力接入，但严格度策略分级（不是全局统一强阻断）。
3. `kind=unknown` 首发默认仅告警，不阻断发布。
4. `trace_required` 仍为首发硬约束核心。

### 实施假设

1. 现有 parser 仍以 heading/list 为主，普通文本节点支持可在同一 resolver 架构下后续扩展。
2. 现有 `node_id` 稳定性策略继续保留，token 必须在签名前剥离。
3. iwp_build 先透传 lint summary 新字段，不单独新增复杂逻辑。

---

## Verification Steps

### 1) Parser 单元回归

- 语法解析：
  - `@iwp`
  - `@no-iwp`
  - `@iwp(file=...,section=...)`
  - `@iwp(kind=...)`
- 冲突与非法格式：
  - 同行多 token
  - 非行尾 token
  - 代码块 token
- 稳定性：
  - token 增删不应导致同 anchor 的 `node_id` 非预期漂移。

### 2) Lint E2E

- 全局路径启用注解能力后，非 Page 文档可被解析并参与 trace_required gate；
- `trace_required=true` 且无 link -> fail；
- `kind=unknown` -> warning（默认策略）；
- `file/section` 非法组合 -> 结构化诊断。

### 3) Build E2E

- build payload 含新增 summary 字段；
- `gap_error_count` 对 required-trace 缺失可正确阻断；
- verify 流程不受 parser 架构重构影响。

### 4) 兼容与性能

- 现有 `@iwp.link` 链路与 session 流程回归通过；
- diff/full 模式性能不出现明显退化（关注 parser 与 schema pass）。

---

## Implementation Order

1. 先落 `SemanticResolver + Resolver Chain` 抽象（不改行为）；
2. 接入 token 解析与决策对象；
3. 扩展 `MarkdownNode` 与 engine policy 诊断；
4. 增加 config authoring 块与默认策略；
5. 更新 schema 元数据；
6. 补齐 parser/lint/build 测试矩阵并跑全量回归。

