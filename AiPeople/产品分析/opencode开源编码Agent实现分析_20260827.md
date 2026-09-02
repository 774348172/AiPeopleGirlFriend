# opencode 开源编码 Agent 实现分析（2026-08-27）

> 来源：https://github.com/sst/opencode（MIT License，shallow clone 至 `D:\AIPeopleGit\opencode`，commit 以当日 master 为准）
> 用途：研究"模型提议 → 宿主校验执行 → 结果回注"工具调用架构的工业级实现，为《动作执行闭环施工方案_工具调用模式_20260827.md》提供设计参照。

## 一、总览

- **技术栈**：TypeScript + Bun，monorepo（workspaces），核心包 `packages/opencode/src`。
- **框架**：Effect（函数式错误处理 + 依赖注入）、Vercel AI SDK（统一多模型调用）、SQLite（会话与消息持久化）、TUI/Web 双端 UI。
- **架构本质**：完整实现了"模型只提议、宿主校验并执行、结果回注上下文、宿主驱动循环"的 agent loop，与 ChatGPT 工具调用同构。

## 二、主循环（核心）

位置：`session/prompt.ts` 的 `runLoop`（宿主驱动的 `while(true)`）：

```
循环 {
  1. 检查上一条 assistant 消息：finish 原因非 "tool-calls" 且无挂起工具调用 → break
  2. 创建新的 assistant 消息
  3. 解析本次可用工具集（固定注册表 + MCP，按权限过滤）
  4. 组装 system（agent 提示词 + 环境 + 指令 + MCP 说明 + skills）+ 全部历史消息
  5. 调模型，消费流式事件（session/processor.ts）
  6. 结果："stop" → break；"compact" → 排队压缩；"continue" → 回到 1
}
```

关键设计：**停止权在宿主手里**。模型输出 `finish="tool-calls"` 时强制继续下一轮，工具结果作为普通消息喂回。工具结果不是特例，它就是消息流的一部分。

## 三、工具系统

### 3.1 工具定义（tool/tool.ts:55）

每个工具是一个带 Schema 的函数：

```ts
{ id, description, parameters: Effect.Schema, execute(args, ctx) → {title, metadata, output} }
```

模型看到的永远只有 `{id, description, parameters}`（JSON Schema），执行函数是宿主代码。工具集由宿主固定（tool/registry.ts 注册 read/write/edit/bash/glob/grep/task/plan/todo/websearch 等），模型无权发明。

### 3.2 执行路径（session/tools.ts:102）

每次请求把工具包装成 AI SDK 格式，执行时：

1. **参数校验**：`Schema.decodeUnknownEffect(args)`，不通过抛 `InvalidArgumentsError`。
2. **错误回注**（tool/tool.ts:31，最值得抄的设计）：校验失败的错误消息是给模型看的——"The {tool} tool was called with invalid arguments: {detail}. Please rewrite the input so it satisfies the expected schema."。错误作为 tool-result 返回，模型看到后重写参数再调一次，循环继续。**参数错误不是异常，是对话的一部分**。
3. **权限检查**：`ctx.ask()` → 规则求值（allow/ask/deny）。
4. **输出截断**：`truncate.output()` 宿主统一截断工具输出控制上下文预算，标记 truncated + 落盘路径。

### 3.3 工具调用生命周期（session/processor.ts）

`pending → running → completed/error` 四状态，每一步持久化到 SQLite 并推送 UI。

## 四、权限系统 = 能力边界（permission/index.ts:28）

规则为 `{permission, pattern, action: allow|ask|deny}` 三元组，通配符匹配，findLast 命中，默认 ask。

三个关键行为：

1. **deny = 工具从可见集合移除**（permission/index.ts:204）：deny 的工具模型根本看不到，连提议的机会都没有——即"能力边界程序化"。
2. **ask = 人机闸门**：宿主挂起工具执行，等用户 once/always/拒绝；拒绝可带反馈（CorrectedError），反馈作为错误回注给模型。
3. **always = 会话级批准**：写入 approved 规则，本会话后续自动放行。

每个 agent 自带规则集（agent/agent.ts:140）：build 全开、plan 禁 edit、explore 只读……**agent = 工具集 + 提示词 + 权限**。

## 五、其他要点

- **Doom loop 防护**（processor.ts:356）：连续 3 次相同参数的同工具调用 → 触发权限询问。
- **上下文管理**：溢出检测 → 自动压缩（compaction）；工具输出截断；后台摘要——全部宿主侧。
- **事件流持久化**：文本/推理/工具状态均为持久化消息 part，可回放。
- **子代理**（tool/task.ts）：task 工具复用同一循环跑子代理，结果作为工具输出回来。

## 六、对动作执行方案的启示

1. **抄 InvalidArgumentsError 回注模式**：执行器校验失败时把拒绝原因作为本轮上下文回注（"cook_meal 不在能力内，只能作为意愿表达"），让模型学会收敛。校验错误 = 对话的一部分，不是异常。
2. **抄"deny 工具不可见"**：P0 动作集为空时，上下文里根本不出现动作槽；能力集外动作模型连提议都提不了。
3. **长时动作变体**：opencode 工具是"一轮内同步完成"；"做饭 20 分钟"是跨轮长时动作。变体：execute 返回"已开始"瞬时结果（登记进行中动作），后台循环（FIVE_MINUTE_RECONCILE）到期后把完成效果写进世界状态，下一轮模型从 snapshot 读到——相当于"延迟到达的 tool-result"。

## 七、结论

opencode 证明"模型做事"的全部机制就是三层：宿主固定的工具清单、参数校验失败当对话处理、权限 deny 让模型连提议都做不到。动作执行器复杂度只有其十分之一，但设计要点可逐条照搬。
