# Symphonz Workflow 讲解与案例模拟设计

## 目标

扩展 `docs/index.html`，让开发者不仅知道 Symphonz 的调用链，还能理解当前 `WORKFLOW.md` 每一组规则为什么存在，以及同一个 Linear Issue 在完整生命周期中每次如何生成 Codex 指令、更新 Linear Workpad、同步状态并改变 Git review request。

页面必须继续描述当前实现，不把预留的重试、并发、日志或 Human Review 自动监听写成已实现能力。

## 内容方案

采用“固定指令框架 + 每轮可变上下文 + 同步结果”方案。

- 固定指令框架只解释一次，避免四次重复整份 `WORKFLOW.md`。
- 每次 Codex turn 展示真实会变化的 Issue 上下文、状态路由和本轮动作清单。
- 每轮同时展示 Linear 状态、唯一 Workpad 内容、Git 分支/提交/PR 状态和 Runtime 内存状态。
- 明确案例是离线的真实结构模拟，不访问 Linear 或 GitHub，不产生外部写操作。

## WORKFLOW 讲解章节

新增 `#workflow-anatomy`，放在 Codex 触发时序和 Issue 生命周期之间。章节包含：

1. `tracker`：解释为什么 API key 只引用环境变量，为什么 `active_states` 包含 `Done`、`Rework` 和 `Merging`，为什么不包含 `Human Review`。
2. `polling`、`workspace`、`hooks`：解释轮询间隔、每 Issue 独立目录，以及 `after_create` 只在首次创建时 clone 仓库的目的。
3. `codex`：解释 app-server、approval policy、workspace sandbox 和网络访问的边界。
4. Prompt 上下文：解释 Issue 字段和 Runtime context 为什么每轮都附带。
5. Operating Rules：解释无人值守、目录隔离、验收条件和状态同步规则。
6. 单一 Workpad：解释为什么只维护一个评论，以及固定 Plan、Acceptance Criteria、Validation、Review Request、Notes 结构如何支持续跑和人工审核。
7. Status Map：解释 Linear 状态是外部控制面，`Done` 是发布命令而非终态。
8. 分支与 review request 约定：解释确定性命名、复用开放 PR/MR 和目标分支选择如何保证幂等。
9. Guardrails：解释“不推未验证代码”“不静默扩展范围”“阻塞时进入 Human Review”的安全目的。

页面使用双栏结构：左侧显示简化后的配置或规则片段，右侧显示“设计目的”“Runtime 是否直接执行”“Codex 是否执行”和“当前限制”。移动端改为单栏。

## 案例模拟章节

新增 `#real-case-simulation`。示例 Issue 固定为：

- Identifier：`PAY-214`
- 标题：`防止支付页重复提交造成重复扣款`
- 初始状态：`Todo`
- 标签：`bug`、`payments`、`codex-ready`
- 验收条件：同一支付请求只能有一个进行中的提交；提交期间按钮禁用；增加覆盖快速双击和 3DS 返回路径的测试。
- Git provider：GitHub
- 基础分支：`main`
- Issue 分支：`symphonz/PAY-214-prevent-duplicate-payment`
- Review request：`PAY-214: 防止支付页重复提交造成重复扣款`

### Turn 1：Todo / 实现

展示传给 Codex 的 Issue context 和路由指令：移动到 `In Progress`、创建 Workpad、创建分支、复现问题、实现互斥提交、运行验证、提交本地 commit。结果区展示 Linear Workpad 的初始计划与验证记录，状态保持 `In Progress` 等待人工将 Issue 标为 `Done`。

### Turn 2：Done / 发布

展示 `Done` 状态触发的发布指令：确认 workspace 和验证结果、推送分支、创建 GitHub PR、把 URL 写入 Workpad、移动到 `Human Review`。明确 `Human Review` 不在默认活动状态中，因此 Runtime 暂停。

### Turn 3：Rework / 返工

模拟 reviewer 提出“3DS 返回后的重试按钮也必须防重复”的反馈，并由人工把 Issue 改为 `Rework`。展示 Codex 读取已有 Workpad、分支和 PR 评论，补充测试和实现、推送同一分支、更新 Workpad，再回到 `Human Review`。

### Turn 4：Merging / 合并

模拟人工批准并把状态改为 `Merging`。展示 Codex 同步 `origin/main`、检查 CI、合并 PR、记录最终结果并把 Linear 状态改为 `Closed`。

## 每轮展示契约

每个 turn 必须包含以下四列或四个明确区域：

- `触发前`：Linear 状态、外部事件、Runtime 发现条件。
- `发送给 Codex`：动态 Issue context、本轮状态路由和动作清单；说明完整固定规则同时附带。
- `Linear 同步`：状态变化和 `## Symphonz Workpad` 的具体增量。
- `代码与 Review`：workspace、branch、commit、push、PR 和 CI 的变化。

页面增加一条总时间线，展示 `Todo → In Progress → Done → Human Review → Rework → Human Review → Merging → Closed`，并在人工状态切换处使用不同视觉标识。

## 准确性约束

- Runtime 每次创建新的 Codex thread/turn，不把案例写成 app-server thread resume。
- `attempt` 当前未由 Orchestrator传入，案例不展示自动 retry attempt。
- `RuntimeState.completed` 只代表某次 turn 完成，不代表 Linear 已关闭。
- Human Review 阶段不会自动轮询 PR 评论；案例中的 `Rework` 和 `Merging` 均由人工或外部自动化改变 Linear 状态后触发。
- Workpad、Linear 状态、Git push 和 PR 操作由 Codex 根据 Prompt 执行，而不是 Python Orchestrator 直接执行。
- 模拟内容使用明确的“预期执行记录”标签，不宣称发生了真实外部写入。

## 交互与视觉

- 顶部导航新增 `WORKFLOW` 和 `真实案例` 锚点。
- WORKFLOW 讲解使用紧凑的规则行和代码片段，不创建嵌套卡片。
- 案例使用四个 turn 的纵向时间线；每个 turn 使用原生 `<details>`，默认展开第一轮，后续可独立展开。
- 状态、外部人工动作和 Codex 动作使用不同颜色与图例。
- 手机端保持单栏，无文档级横向滚动；代码块允许自身横向滚动。
- 页面仍完全离线，并尊重 reduced motion。

## 版本与测试

- 本次文档功能发布将版本从 `0.2.0` 升级为 `0.2.1`。
- 自动测试验证两个新 section id、九组 WORKFLOW 解释、四个 turn、每轮展示契约、完整状态时间线和模拟免责声明。
- 自动测试验证页面版本与 `symphonz.__version__` 一致，并继续验证离线资源约束。
- 浏览器验证桌面和 390px 手机布局、details 展开、导航锚点、无横向溢出和无控制台错误。
- 全量 Python 测试和语法检查必须通过。
