# claw-code 项目分析与"是否为 Claude Code 泄露源码"判定（2026-08-27）

> 来源：https://github.com/ultraworkers/claw-code（shallow clone 至 `D:\AIPeopleGit\claw-code`，HEAD `08106b0`，2026-08-16）
> 结论先行：**不是 Claude Code 泄露源码**。这是一个从零重写的、受 Claude Code 启发的开源 CLI 编码代理（Rust + Python），由"claw"智能体生态维护，仓库自述为"agent 管理的博物馆展品"。

## 一、项目概况

- **GitHub 元数据**：创建于 2026-03-31，MIT License（Copyright (c) 2026 UltraWorkers and Claw Code contributors），描述为 "An agent-managed museum exhibit, built in Rust"。
- **自述**（README）："Claw Code is not the serious production project here... closer to a museum exhibit than a product pitch"——明确表示这不是产品仓库，而是由其开发者生态（Gajae-Code、LazyCodex、oh-my-codex，作者 Yeachan-Heo 系）用多智能体协作自动维护的"展品"。
- **哲学**（PHILOSOPHY.md）：重点不是仓库里的代码，而是"产生这些代码的系统"——人类在 Discord 里下指令，多个编码 agent 并行规划、执行、审查、重试。仓库本身是这套工作流的演示产物。

## 二、结构分析

- **Rust workspace**（`rust/`，9 个 crates）：`rusty-claude-cli`（主 CLI，REPL + OAuth + 工具集 + 流式）、`claw-analog`（轻量 CI 版）、`claw-rag-service`（RAG 索引服务）、`api`、`commands`、`plugins`、`runtime`、`telemetry`、`tools`、`compat-harness`、**`mock-anthropic-service`**（自建的 Anthropic 兼容 mock 服务，用于 parity 测试）。
- **Python 源码树**（`src/`）：早期的 Python 版本遗留（QueryEngine.py、Tool.py、coordinator、command_graph.py 等）。
- **文档体系**：PARITY.md（与 Claude Code 行为对齐的 9 车道检查表）、USAGE.md、ROADMAP.md（数千行的缺陷跟踪）、concept.md（俄语）、AGENTS.md/CLAUDE.md 分层知识库。

## 三、判定依据：不是泄露源码

| 证据 | 说明 |
|---|---|
| 技术栈完全不符 | 真实 Claude Code 是 npm 包（JS/TS，巨大压缩的 cli.js）；claw-code 无任何 package.json，是 Rust + Python 从零实现 |
| 许可证与版权 | 泄露的 Anthropic 专有代码不可能带 MIT + UltraWorkers 版权；真实 Claude Code 无 license |
| 自建 API mock | 泄露源码不需要 `mock-anthropic-service`（对自己 API 的模拟服务）；这是 clean-room 客户端为做行为对齐测试才需要的东西 |
| 自述明示 | README/PHILOSOPHY 明确说这是"展品"、由 agent 生态生成维护，指向 LazyCodex / Gajae-Code 为实际生产项目 |
| 代码内对照语气 | ROADMAP.md 提到 Claude Code 时是作为"上游对照/兼容目标"（如读取 Claude Code manifests 的 compat 工具），即它是消费方，不是本体 |
| 命名即致敬 | 主 CLI 叫 `claw`（蟹钳），是 Claude 的双关，典型的致敬重实现命名 |
| 时间线 | 仓库创建于 2026-03，parity 检查表跨 2026-03-31→04-03；与 2025 年 1 月的真实泄露事件无承接关系 |

## 四、背景补充：真实泄露事件

2025 年 1 月前后，`@anthropic-ai/claude-code` npm 包的源码（含压缩 CLI 与内部文件）确实曾被泄露并在 GitHub 镜像流传，其特征是：npm 包结构、`cli.js`、`services/`、`vendor/` 目录、无 license、直接可 `npm install` 的完整依赖。claw-code 与这些特征无一吻合。

## 五、参考价值

与 opencode 一样，claw-code 是一个完整的工具调用循环实现（工具集、权限、流式、压缩），可对照学习；其 `mock-anthropic-service` + parity 检查表模式（用 mock 服务做确定性行为对齐测试）与我们的评测思路（eval 套件 + 确定性验收）同构，值得借鉴。
