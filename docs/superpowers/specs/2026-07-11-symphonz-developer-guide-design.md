# Symphonz 开发者链路介绍页设计

## 目标

在仓库内提供一个无需构建、可直接由浏览器打开的中文静态页面，帮助开发者快速理解当前 Symphonz 服务如何监听 Linear、何时触发 Codex、Issue workspace 如何产生，以及代码最终如何通过 GitHub PR 或 GitLab MR 进入人工审核和合并流程。

页面只描述当前代码与 `WORKFLOW.md` 已实现的行为。规划中但尚未由 Python 调度器原生实现的并发、持久化重试和终态清理，不写成已交付能力。

## 交付形式

- 新增独立静态页面 `docs/index.html`。
- 页面内联 CSS 和 JavaScript，不依赖 npm、CDN、字体服务或网络资源。
- 直接双击文件即可阅读，也可通过任意静态文件服务器托管。
- 页面使用中文，代码标识、协议方法名和路径保持原始英文。
- 页面适配宽屏、平板和手机，主要阅读场景为桌面开发环境。

## 设计方向

采用“系统地图”方向。页面首先展示组件边界，再按一次 Issue 的执行时序深入细节。视觉语言延续现有 Runtime Dashboard 的克制、浅色、Linear 风格，但静态页面不伪装成可操作后台。

页面不使用大型营销 Hero、渐变背景、装饰性插画或嵌套卡片。图示使用 HTML/CSS 构建，确保文字清晰、可选择、可缩放，并能在离线环境工作。

## 信息架构

### 1. 顶部导航与摘要

顶部显示 Symphonz、页面标题、当前版本和章节锚点。摘要用一句话说明核心机制：Symphonz 把 Linear Issue 转换为隔离 workspace 中的一次 Codex app-server turn。

同时提供三个事实标签：默认 5 秒轮询、每个 Issue 独立 workspace、Codex 使用 JSON-RPC 通信。

### 2. 系统组件地图

以 Symphonz Runtime 为中心，展示六个相邻组件：

- Linear GraphQL：提供项目内指定活动状态的 Issue。
- `WORKFLOW.md`：同时提供调度配置和传给 Codex 的 Prompt 模板。
- Issue Workspace：位于 `.symphonz/workspace/<issue_identifier>`，首次创建时运行 `after_create` hook。
- Codex app-server：接收 `initialize`、`thread/start` 和 `turn/start`。
- Runtime State / Dashboard：记录 running、completed、blocked 与事件流。
- GitHub / GitLab：由 Codex 根据工作流状态规则推送分支和创建 review request。

连线分成两种语义：实线表示 Symphonz Python Runtime 的直接调用，虚线表示 Codex 按 `WORKFLOW.md` 执行的外部操作。

### 3. Linear 监听机制

说明 `run_service()` 的循环：加载工作流，创建 Linear client，按 `polling.interval_ms` 调用 `Orchestrator.poll_once()`，通过 `SymphonzPoll` GraphQL query 获取最多 50 条候选 Issue，再按 `active_states`、`project_slug` 与 `required_labels` 筛选。

页面必须明确当前调度是同步逐条执行。`agent.max_concurrent_agents` 当前存在于工作流配置中，但尚未被调度器用于并发执行。

### 4. 单次 Codex 触发时序

使用横向时序图展示：

1. Runner 触发一次轮询。
2. Linear 返回候选 Issue。
3. Orchestrator 创建或复用 Issue workspace。
4. 首次创建时执行 `after_create`，默认克隆目标仓库。
5. `render_prompt()` 把 Issue 字段填入 `WORKFLOW.md` 模板。
6. 启动配置中的 `codex.command`。
7. 发送带 `clientInfo` 的 `initialize` 请求。
8. 发送 `initialized` 通知。
9. 发送 `thread/start`，工作目录和 sandbox 指向 Issue workspace。
10. 发送 `turn/start`，输入为渲染后的 Prompt。
11. 持续读取 Codex 事件，直到 `turn/completed` 或失败。
12. 更新内存中的 Runtime State 为 completed 或 blocked。

时序图必须单独放大 Codex JSON-RPC 握手，避免读者误以为每次轮询只是执行一次 `codex exec` 命令。

### 5. Issue 状态与 Git 发布链路

使用状态轨道说明 `WORKFLOW.md` 规定的语义：

- `Todo`：Codex 先移动到 `In Progress`，再开始实现。
- `In Progress`：继续已有 workspace 与 workpad。
- `Done`：不是终态，而是发布触发器；推送分支并创建 GitHub PR 或 GitLab MR，然后进入 `Human Review`。
- `Human Review`：等待人工审查。
- `Rework`：在原分支处理反馈并重新验证。
- `Merging`：同步目标分支、检查 CI 并合并。
- `Closed` / `Cancelled` / `Canceled` / `Duplicate`：终态，不再执行。

页面必须说明上述 Linear 状态更新、Git commit/push 和 review request 操作由 Codex 按 Prompt 执行，而不是由 `Orchestrator` 直接调用 provider API。

### 6. 文件与运行命令

展示安装后目录树和常用命令：

```text
.symphonz/
├── WORKFLOW.md
├── config.toml
├── logs/
└── workspace/
    └── ZHA-8/
```

```bash
symphonz install
symphonz run --port 4100
symphonz version
```

说明 `--port` 只控制 Runtime Dashboard；静态开发者介绍页独立存在于仓库 `docs/index.html`。

### 7. 当前实现边界

以醒目的工程说明区列出：

- Runtime State 当前保存在进程内存中，服务重启后不会恢复。
- Issue 当前同步逐个执行。
- completed/blocked 仅代表当前 Codex turn 的结果，不等同于 Linear 的终态。
- 只要 Issue 仍处于 `active_states`，后续轮询仍可能再次调度。
- GitHub/GitLab 发布能力依赖 Codex 环境中的凭据和工具。

## 交互

- 顶部导航点击后滚动到对应章节。
- 系统地图中的组件可点击或通过键盘聚焦；右侧详情区域显示组件职责、输入和输出。
- 时序图支持“自动播放”和逐步选择，默认完整展示，不依赖 JavaScript 也能阅读关键内容。
- 尊重 `prefers-reduced-motion`，禁用非必要动画。
- 所有交互控件提供可访问名称和清晰焦点状态。

## 视觉规范

- 主色使用现有 Dashboard 的 `#5e6ad2`，仅用于连接线、焦点和关键状态。
- 背景使用中性浅灰与白色，成功、阻塞、等待分别使用绿色、红色和琥珀色。
- 正文字体使用系统 sans-serif，协议与路径使用系统 monospace。
- 圆角不超过 8px；图表和代码区域使用 1px 边框，不使用大面积阴影。
- 标题不随视口宽度缩放，移动端通过换行和布局重排保证可读性。

## 测试与验收

- 自动测试验证静态页面存在、标题和关键中文内容完整。
- 自动测试验证系统地图、Linear 轮询、Codex JSON-RPC、状态轨道、目录树和实现边界均有稳定的 section id。
- 自动测试验证页面不引用远程 CSS、JavaScript、图片或字体。
- 在桌面宽度和手机宽度下用浏览器截图检查无溢出、遮挡和不可读文本。
- 浏览器控制台不得出现错误。
- 全量 Python 单元测试必须通过。
