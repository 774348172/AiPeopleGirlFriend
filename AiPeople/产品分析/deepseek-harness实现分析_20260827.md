# DeepSeek Harness 实现分析（2026-08-27）

> 来源：https://github.com/deepseek-ai/deepseek-harness（官方开源，MIT，TypeScript + pnpm monorepo，shallow clone 至 `D:\AIPeopleGit\deepseek-harness`）
> 定位：DeepSeek AI 官方 agent harness（`dsh`），核心口号 **"Everything is a Plugin"**，基于 Cordis 插件框架，处于开发者预览阶段。

## 一、架构总览

- **Cordis 驱动**：插件向共享上下文贡献服务、类型化事件和可逆副作用。产品每一部分都是插件——模型适配器、工具注册表、会话日志、**agent loop 本身**。没有特权内核，注册是副作用、卸载时撤销。
- **Profile / 组合包分层**：运行中的 dsh 是一棵插件树，由启动时按序叠加的各层组合（profile → bundle → 用户 patch）。`dsh --dump-config` 可打印实际配置树，任何条目可被 patch 替换。
- **能力 seam**：一项可替换能力 = Service Definition（声明接口）+ Service Provider（实现）+ Consumer（面向模型的工具）。换提供方 = 换整个产品行为（如把文件系统/进程提供方指向远程沙箱，Bash/PTY/LSP 一并搬走）。
- **事件三域**：会话事件（持久事实，追加日志并广播）、agent 事件（携带活跃 Agent：inbox/步骤/状态/请求/验证/续跑）、能力事件（向 seam 附加策略，避免循环导入）。

## 二、轮次流程（turn flow）

```text
turn/start
  claim 下一条输入 + 一条排队消息
  组装提示词片段 + 工具 schema
  -> agent/pre-step                   reject | enter(messages)   ← 决定模型看到什么
     step/start
     追加输入为用户消息；从会话日志派生模型历史
     agent/request -> llm/stream -> assistant/chunk* -> assistant/message
     tool/call* -> tools/pre-execute -> tools/execute -> tools/post-execute -> tool/result*
     step/end
     工具还欠请求，或新输入到达 -> claim -> 下一个 step
  -> agent/turn-stopping
turn/end
```

- **步骤 = 一次模型请求 + 它调用的工具；轮次 = 零或多个步骤**。
- 输入经同一个 inbox 到达；注入的上下文留在 inbox，直到另一条消息唤醒。
- `agent/pre-step` 可改写或拒绝消息；`tools/*` 与 `agent/request`、`llm/stream` 是 waterfall 事件，监听器必须 `next()` 委托。

## 三、工具执行流水线（带把关，最值得抄的部分）

```mermaid
model 输出 tool-call
  → tool/call 会话事件（执行前落日志）
  → tools/pre-execute waterfall（钩子、权限、沙箱）
  → 单调守卫（deny / abstain）
  → ctx.approval 一次性审批（absent → deny）
  → tools/execute waterfall（超时、重试、指标环绕分发）
  → 工具本体 execute() → fs 守卫（fs/write-intent、fs/edit-intent）
  → tools/post-execute waterfall（接受 / 阻止 / 替换 / 附加上下文）
  → 结果规范化（throw → isError）→ finalizeContent 内容不变式
  → tools/result 同步通知（冻结的权威结果）
  → additionalContexts FIFO 注入（作为 user/message 追加，位于已记录的工具结果之后）
  → tool/result 会话事件（唯一的模型可见结果）
```

要点：拒绝/错误**规范化后作为工具结果回注**（模型看到的是对话内容不是异常）；钩子跨工具系列通用，工具不耦合策略服务；工具自身产出的事件（todo/write、fs/observed、hook/result）也是会话事件。

## 四、与动作执行直接相关的三个子系统

### 4.1 schedule（仅限 Session 内的提醒）

持久状态在**会话日志**中；到期工作通过 Agent 的**普通 follow-up 队列进入同一对话**。不公开可变数据库，工具与 runtime 只向 Session stream 追加事件。冷会话再次 live 后恢复逾期工作。

### 4.2 goal（持久化的同会话目标）

目标状态是会话日志的一部分（`ctx.goals`），独立于 agent loop：goal 标识、生命周期快照、激活、变更记录；面向模型的工具（tool-goal）、面向用户的命令（command-goal）、同会话目标续行（goal-round-driver）四件套分离。

### 4.3 guard（循环卫生）

- **repeat-tool-reminder**：对重复工具调用的建议性提醒——以 `additionalContexts` 随 post-execute 决策传给模型，并作为 user/message 事件记录。
- **timeout-policy**：以部署策略形式设置单次工具调用截止时间（注册 tools/execute 监听器）。

## 五、对动作执行方案的启示

1. **动作执行器 = 工具流水线，不是 if/else**：白名单（单调守卫）、玩家审批（ctx.approval）、超时（timeout-policy）、结果规范化、动作结果事件（tool/result）——五个环节各自独立、可插拔，替换策略不碰工具本体。
2. **"做饭 20 分钟"的现成答案 = schedule 模式**：进行中动作登记为 schedule 事件，到期后作为 follow-up 消息进入同一对话，模型在下一轮自然处理"饭好了"——这就是我们方案里"延迟到达的 tool-result"，DeepSeek 官方把它做成了标准机制。
3. **承诺/意图的正确形态 = goal 子系统**：目标状态是一等会话状态（标识、生命周期、变更记录、续行驱动），不是文本层账本——验证了我们放弃 pending_intentions 文本追踪的判断，goal 就是它的工业实现。
4. **"模型可见即已记录"**：一切模型可见输入必须能从会话事件日志重建（运行时不变式断言）。对你们：世界状态变更/动作结果必须走事件流，模型看到的每个事实都要能追溯到事件——强化"世界状态本身就是账本"。
5. **一切皆插件的边界**：能力集（白未晞）可以做成角色包插件，运行时只提供流水线；换角色 = 换插件包，agent loop 与工具策略都不动。
6. **拒绝回注的标准通道**：post-execute 的 additionalContexts FIFO（在已记录工具结果之后注入）——"cook_meal 不在能力内"这类反馈就通过这个通道进对话，与 opencode 的 InvalidArgumentsError 结论一致。

## 六、结论

opencode 展示了"宿主固定工具集 + 校验回注"的最小正确实现；DeepSeek Harness 展示了同一思想的工程化形态：显式轮次状态机、五段式工具流水线、事件日志即事实来源、schedule/goal/guard 三个直接对应我们需求（长时动作、意图、循环卫生）的子系统。两者结论互相印证，且后者的 schedule 与 goal 可直接作为《动作执行闭环施工方案》的参照实现。
