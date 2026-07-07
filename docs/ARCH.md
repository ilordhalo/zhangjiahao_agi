# Architecture

## 技术选择

本项目使用依赖为零的静态网站架构：

- `index.html`：页面语义结构、导航、首屏、核心内容区和来源链接。
- `src/styles.css`：视觉系统、响应式布局、动画、卡片和 reduced-motion 支持。
- `src/main.js`：能力图谱数据、筛选交互、观察指标渲染、渐入展示和首屏 canvas 可视化。
- `docs/research/agi-related-ai-progress-2026-07.md`：ZHA-5 完整研究报告，作为内容来源。
- `scripts/validate_site.py`：静态结构、交互钩子、文档同步和来源可追溯性验证。

## 运行方式

网站不需要构建步骤。可以直接打开 `index.html`，也可以从仓库根目录运行本地静态服务器：

```bash
python3 -m http.server 4173
```

然后访问 `http://127.0.0.1:4173/`。

## 交互模型

页面把报告内容拆成四层：

- 首屏结论：解释 AGI 相关性判断框架。
- 排序列表：展示最高相关的前四类进展。
- 能力图谱：由 `domainData` 驱动，可按方向筛选。
- 观察指标与反信号：把未来验证点和风险边界放在页面后半段。

交互只依赖浏览器原生 API。`IntersectionObserver` 用于渐入展示；若浏览器不支持，则内容直接显示。Canvas 动画尊重系统 reduced-motion 设置。

## 可维护性

新增或调整内容时优先修改 `src/main.js` 的 `domainData` 与 `signalData`，并同步更新研究报告或项目文档。任何结构变化都应同步扩展 `scripts/validate_site.py`，保证本地验证覆盖页面入口、交互控件和文档要求。
