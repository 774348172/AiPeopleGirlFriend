# RelationshipRuntime 阶段 4：真实模型接入施工文档

> 状态：验收已执行（P0-09 至 P0-14、P0-16 通过；P0-15 工程通过、内容失败）  
> 版本：v1.1  
> 日期：2026-08-04  
> 前置条件：阶段 0-3 已完成，46 项自动测试通过  
> 本阶段目标：用常驻 llama.cpp 接入 Qwen3-4B 基座 GGUF，打通真实 `REPLY` 流式链路

## 1. 本阶段回答的问题

阶段 4 只回答一件事：在不改变 `RelationshipRuntime.handle_turn()` 外部 interface 和账本事务顺序的前提下，真实 Qwen3-4B 能否在目标 Windows 设备上稳定、可取消、可恢复地流式回复，并达到文字产品的延迟和显存预算。

本阶段不是秦未晞最终效果验收。最初计划使用未经秦未晞 LoRA 训练的基座模型；实际施工时用户提供了本地训练候选 `qinweixi-q4_k_m.gguf`，因此 P0-15 同时记录其内容基线。该候选可以验证本地部署、基本角色表达和通用问答，但不能据此判断长期人格强度、跨轮连续性或训练上限，也不能因协议和性能通过而认定内容合格。

## 2. 权威边界

施工时依次服从：

1. `需求文档/项目框架需求.md`。
2. `人物设定/秦/角色设定定稿.md` 与 `人物设定/秦/`。
3. `设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`。
4. `设计文档/AI设计/当前权威设计/本地AI恋爱桌面宠物产品集成设计.md`。
5. `设计文档/AI设计/历史设计/RelationshipRuntime_阶段0-3施工文档.md`。

不得从 `历史与调研文档/` 恢复 Ollama 主链路、双轨记忆、全量 prompt 或其他旧方案。

阶段 0-3 的以下不变量继续成立：

- 调用方仍通过 `RelationshipRuntime.handle_turn(UserMessage)` 获得 `ReplyEvent` 流。
- 用户消息提交成功后才能调用模型。
- 每个模型正文分片立即产出 `TextDelta`。
- 完整回复提交成功后才能产出 `Completed`。
- 失败和取消保留部分正文，但不能伪装成完整角色消息。
- 同一 request ID 的幂等、冲突和进行中保护不变。
- 日志不得记录玩家输入、角色回复、prompt 或 SSE 正文。

## 3. 明确范围

### 3.1 本阶段做

- 验真并固定 Windows CUDA 版 llama.cpp。
- 验真 Qwen3-4B 基座 GGUF Q4_K_M。
- 新增 `LlamaCppReplyModel`，满足现有模型 seam。
- 托管 `llama-server.exe` 的启动、健康检查、预热、关闭和一次恢复。
- 调用 OpenAI 兼容 `/v1/chat/completions` 并解析 SSE。
- 实现生成取消、超时、断流和服务崩溃处理。
- 实现最小秦未晞 `REPLY` prompt 和 non-thinking 防护。
- 使用 FakeLlamaServer 做确定性协议测试。
- 在真实基座模型和 RTX 3070 8GB 上做冒烟、性能和稳定性测试。

### 3.2 本阶段不做

- 不训练、合并或导出秦未晞 LoRA。
- 不实现会话历史、epoch、工作激活区或 `RecallFrame`。
- 不实现 `RECALL_PLAN`、`MEMORY_PROPOSE`、语义投影和计划状态机。
- 不接入 emotion/action/effect 标签解析、桌面前端、STT 或 TTS。
- 不引入 Ollama、Transformers、torch、向量数据库或第二个常驻模型。
- 不把未经固定评测的采样参数写成最终人格参数。

因此，阶段 4 的连续两轮请求彼此没有模型上下文。模型忘记上一轮是预期限制，不是阶段 4 缺陷；下一阶段再接入当前 epoch 历史和确定性召回。

## 4. 模块与 seam

阶段 4 不为前端增加独立的模型接口。llama.cpp 进程、HTTP、SSE、prompt、采样参数和协议校验全部留在模型 Adapter 内部：

```text
调用方
  |
  v
RelationshipRuntime.handle_turn()
  |
  v
ReplyModel seam
  |-- FakeReplyModel          自动测试
  `-- LlamaCppReplyModel      本地生产推理
        |-- llama-server 进程生命周期
        |-- health / warm-up
        |-- HTTP/SSE 与取消
        `-- REPLY prompt 与输出过滤
```

`RelationshipRuntime.handle_turn()` 的参数和返回类型不变。内部 `ReplyModel` 生命周期扩展为：

```python
class ReplyModel(Protocol):
    async def start(self) -> None: ...
    def stream_reply(self, request: ReplyRequest) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...
```

`FakeReplyModel.start()` 和 `close()` 是无副作用操作，FakeModel 仍可像阶段 0-3 一样直接用于测试。`RelationshipRuntime` 的异步上下文负责启动和关闭真实模型；`close()` 必须先停止模型，再关闭账本。未启动的 `LlamaCppReplyModel` 被调用时应立即报告内部 not-ready 错误，由运行时映射为 `model_unavailable`，不能偷偷在首轮加载几十秒。正式真实模型入口必须使用：

```python
async with RelationshipRuntime.open(config, reply_model) as runtime:
    async for event in runtime.handle_turn(message):
        ...
```

`start()` 必须幂等，`close()` 必须幂等。运行时关闭期间不接受新回合；阶段 4 不支持多个进程共享同一个模型服务。

## 5. 目标目录与依赖

新增或修改范围：

```text
pyproject.toml                         增加 httpx
runtime/
  _model.py                           增加内部生命周期合同
  _prompt.py                          唯一 REPLY prompt 构造器
  relationship_runtime.py             托管模型启动与关闭
  adapters/
    fake_model.py                     增加无副作用生命周期
    llama_cpp.py                      深 Adapter
  demo_real.py                        真实模型开发冒烟入口
tests/runtime/
  fake_llama_server.py                仅测试用 HTTP/SSE 假服务
  test_reply_prompt.py
  test_llama_cpp_adapter.py
  test_real_model_smoke.py             默认跳过，显式开启
```

阶段 4 唯一新增生产依赖：

```toml
dependencies = [
    "httpx>=0.27,<1",
]
```

不再拆出进程管理器、SSE 客户端和采样参数等浅模块。它们先作为 `llama_cpp.py` 的私有实现存在；只有文件明显失去 locality 时再提取内部模块，不能扩大外部 interface。

## 6. 阶段 4.0：资产验真

当前仓库中没有 GGUF、`llama-server.exe` 或 `llama-cli.exe`。任何代码施工前先完成本节，禁止用不存在的路径做“已接入”验收。

### 6.1 llama.cpp

选择带 CUDA 支持的 Windows x64 官方构建，并固定到一个明确 release 或 commit。不得使用浮动 `latest` 作为可复现依据。

验真项目：

1. 来源 URL 和下载日期。
2. release/tag 与 commit（可获得时两者都记）。
3. 压缩包 SHA256。
4. `llama-server.exe` SHA256。
5. `llama-server.exe --version` 原始输出。
6. `--help` 中存在施工所需参数：模型路径、host、port、context、GPU offload、parallel、Flash Attention 和 KV cache 选项。
7. 启动日志确认加载 CUDA backend，而不是静默回退 CPU。

### 6.2 模型

首个真实模型固定为 Qwen3-4B 基座的 GGUF Q4_K_M。只从模型作者或可追溯的可信发布者下载，并记录：

- 仓库和精确 revision。
- GGUF 文件名、字节数和 SHA256。
- 模型架构与量化类型。
- chat template 是否被 llama.cpp 正确识别为 Qwen3。
- 模型许可证文件及其适用范围。

不得用旧林知微 Adapter、于谦训练产物或其他角色 GGUF 代替。

### 6.3 本地资产与 manifest

二进制和权重不提交 Git。实施阶段将本地目录加入 `.gitignore`：

```text
local_runtime/
  llama.cpp/
  models/
  model_runtime_manifest.json
```

`model_runtime_manifest.json` 至少包含：

```json
{
  "llama_cpp": {
    "version": "固定版本",
    "source_url": "下载来源",
    "archive_sha256": "...",
    "server_sha256": "..."
  },
  "model": {
    "repo": "发布仓库",
    "revision": "固定 revision",
    "file": "Qwen3-4B-Q4_K_M.gguf",
    "bytes": 0,
    "sha256": "..."
  },
  "launch": {
    "context_size": 4096,
    "parallel": 1,
    "gpu_layers": "all",
    "flash_attention": true,
    "kv_cache": "以实测值填写"
  }
}
```

manifest 不保存 API key、玩家路径以外的隐私数据或聊天正文。Adapter 启动时重新计算关键文件 SHA256 并与 manifest 比对；不匹配时拒绝启动并给出可执行错误。

### 6.4 退出条件

- 两项资产均真实存在且哈希匹配。
- llama.cpp 明确使用 CUDA 加载 Qwen3-4B。
- 模型能用 `llama-cli` 或 server 完成一次中文 non-thinking 生成。
- 资产来源、版本、许可证和启动参数均可追溯。

## 7. 阶段 4.1：配置和进程生命周期

在 `llama_cpp.py` 定义不可变 `LlamaCppConfig`。至少包含：

```text
server_executable
model_path
manifest_path
host = 127.0.0.1
port
context_size = 4096
parallel = 1
gpu_layers = all
flash_attention = true
startup_timeout_seconds
connect_timeout_seconds
read_idle_timeout_seconds
generation_timeout_seconds
shutdown_timeout_seconds
max_tokens = 160
temperature
top_p
repeat_penalty
```

约束：

- `host` 首版只能是 `127.0.0.1`，不允许 `0.0.0.0` 或局域网地址。
- 端口必须显式配置。启动前发现被占用时失败，不得连接未知进程。
- 可执行文件、模型和 manifest 必须使用绝对路径并完成哈希校验。
- 4K 上下文先通过全部验收，再单独测试 8K；不得同时改变上下文、KV cache 和采样参数后比较性能。
- `parallel=1`，阶段 4 用 Adapter 内部异步锁串行生成。
- 使用 `asyncio.create_subprocess_exec` 参数数组，禁止 `shell=True`。
- 启动参数以当前固定版本 `--help` 为准，不能凭旧版本名称硬编码后跳过启动探针。

进程状态最少区分：`stopped`、`starting`、`ready`、`failed`、`closing`。并发调用 `start()` 只能启动一个子进程。

启动顺序：

1. 校验配置、manifest 和文件哈希。
2. 检查端口未被占用。
3. 创建 llama-server 子进程，只绑定本机。
4. 异步排空 stdout/stderr，避免管道写满导致服务挂住。
5. 轮询 `/health`，同时监控子进程退出。
6. 检查 `/v1/models` 返回预期模型，不接受同端口未知服务。
7. 执行一次不可见短预热，完整消费响应但不写账本。
8. 标记 `ready`，此后才允许 UI 发送第一条消息。

启动日志只保留版本、后端、显存和错误摘要。启动生产参数应关闭 prompt/body 日志；Adapter 维护有界诊断尾部，禁止把完整输出抄入应用日志。

关闭顺序：停止接受新生成，关闭活动 HTTP 流，等待子进程退出，超时后 `terminate`，再次超时才 `kill`，最后关闭 `httpx.AsyncClient`。Windows 下必须验证没有残留 `llama-server.exe`。

## 8. 阶段 4.2：健康检查与预热

`/health` 的验收不是“TCP 能连接”，而是服务明确报告模型可推理。加载期间继续等待，非预期状态或进程提前退出立即失败。

预热使用和正式回复相同的 chat template 与 non-thinking 参数，但只生成 1-4 token。预热请求：

- 不包含真实玩家输入。
- 不写入事件账本。
- 不产出 `ReplyEvent`。
- 不进入性能样本。
- 失败则启动失败，不能让第一次真实对话代替预热。

模型加载时间单独记录，不计入热启动首字延迟。健康轮询、预热和正式生成使用不同超时，错误中区分：资产错误、端口错误、进程退出、健康超时和预热失败。

## 9. 阶段 4.3：最小 REPLY prompt

`runtime/_prompt.py` 只负责从 `ReplyRequest` 产生结构化 chat messages 和生成参数，不负责 HTTP。禁止手工拼接 Qwen chat template；交给 llama.cpp 从 GGUF 元数据应用模板。

固定 system 身份锚控制在短而稳定的范围：

```text
你是秦未晞，23岁，自由插画师和自媒体博主。
玩家25岁，是与你合租的室友和暧昧对象；你们尚未确认恋爱关系。你叫他“大叔”。
你外向、嘴毒、嘴硬心软，通常先怼一句再自然地表达关心。
先回答玩家真正问的问题；涉及安全、健康或严肃请求时，正确和清楚优先，不要为了调侃回避问题。
你无法获知设备外的实时天气、位置或刚发生的现实事件；不知道时坦率说明，不得编造。
不要自称AI，不要使用助手腔、Markdown、列表或emoji。回复通常自然简短，需要解释时可以说完整。
你记得一段玩家已忘记的异世界经历，但普通回复不要频繁主动揭露。
```

本阶段 messages 只有固定 system 和当前 user 两项。原始 `request.text` 必须逐字符作为 user content 传入，不得插入 system 字符串、转义成自制模板或截断。

non-thinking 同时采用三层保护：

1. 使用 Qwen3 non-thinking chat template 配置，例如当前固定 llama.cpp 版本支持的 `chat_template_kwargs.enable_thinking=false`。
2. 只消费 OpenAI 流中的正文 `delta.content`，忽略独立的 reasoning 字段。
3. 使用跨分片状态机抑制 `<think>...</think>` 泄漏；标签被拆到多个 SSE 分片时也不能漏出半个标签。

保护器只处理思考标记，不任意改写角色正文。若响应只有思考内容、标记未闭合或过滤后为空，由现有运行时记录 `empty_model_response`。开发日志只记录错误码和字符数，不记录被过滤内容。

阶段 4 暂不要求模型生成情绪动作标签，以免基座模型的随机标签污染流式合同；标签协议在后续表现阶段接入并单独验收。

采样起点：

```text
temperature = 0.75
top_p = 0.9
repeat_penalty = 1.1
max_tokens = 160
stream = true
```

参数是阶段 4 基线，不是最终人格参数。性能测试固定 seed（若当前 server 支持）；体验抽检使用正常随机生成。停止条件不能把常见中文标点作为 stop token。

## 10. 阶段 4.4：HTTP 与 SSE

使用一个长期存活的 `httpx.AsyncClient`，`base_url` 固定为本机地址，`trust_env=False`，避免系统代理接管本地私密请求。正式生成调用：

```text
POST /v1/chat/completions
Content-Type: application/json
Accept: text/event-stream
```

SSE 解析必须按协议事件解析，不能按 TCP chunk 或字符串中出现 `data:` 简单切割。至少处理：

- JSON 被网络分片拆开。
- CRLF 与 LF。
- SSE 注释和空行。
- 一个事件包含多行 `data:`。
- keep-alive 空事件。
- `choices[0].delta.content` 为空或缺失。
- 独立 reasoning 字段。
- `finish_reason`。
- `data: [DONE]`。
- 非 2xx JSON/纯文本错误。
- 非法 JSON、非法 schema 和完成前断流。

每个经过 thinking 过滤后的非空正文片段立即 `yield`。Adapter 不累计最终回答，完整正文仍由 `RelationshipRuntime` 统一累计和提交。

协议完成条件固定为收到合法完成信号；连接提前关闭视为失败。HTTP 状态、协议错误和超时转换为 Adapter 内部异常，`RelationshipRuntime` 对外仍只返回现有 `model_unavailable`，不把 llama.cpp 细节泄漏到产品 interface。

超时至少分为：连接超时、首事件/读取空闲超时、整次生成总超时。取消优先级高于超时；调用方取消异步流时必须关闭当前 response body，使 llama.cpp 停止该次生成并释放槽位。

## 11. 阶段 4.5：崩溃恢复与取消语义

只允许一次自动重启，防止崩溃循环：

| 故障时点 | 行为 |
|---|---|
| 启动或预热失败 | 清理进程后再启动一次；仍失败则停止 |
| 请求发出但尚未产出正文 | 重启一次，并可安全重试该次请求 |
| 已向玩家产出任意正文 | 不自动重放该请求，避免重复文字；关闭流并向运行时报告失败 |
| 服务在空闲期退出 | 下一次请求前重启一次 |
| 重启后再次失败 | 不再重试，进入 `failed`，等待用户显式重新启动 |

“重启一次”按一次故障恢复周期计数；成功稳定运行后重置恢复额度。任何请求最多只能在首个可见正文前自动执行两次模型尝试。

调用方取消时：

1. 立即关闭 HTTP 流，不发送第二次请求。
2. 不把取消计为服务崩溃，不触发重启。
3. `RelationshipRuntime` 继续追加 `generation_cancelled`，保存已显示部分正文。
4. Adapter 锁被释放，下一次请求可正常生成。

若关闭 HTTP 后服务仍持续占用生成槽位，协议测试和真实测试都应失败，不能用重启服务掩盖普通取消缺陷。

## 12. 阶段 4.6：FakeLlamaServer 协议测试

FakeLlamaServer 是测试夹具，不是第二个生产 Adapter。它在随机本地端口启动，记录请求元数据但默认不保存 prompt 正文。

`test_llama_cpp_adapter.py` 至少覆盖：

1. 健康检查经历 loading 后 ready。
2. `/v1/models` 与目标模型不一致时拒绝连接。
3. 正常 SSE 多分片按顺序输出正文。
4. JSON 被任意 TCP 分片拆开仍能解析。
5. CRLF、多行 data、注释和 keep-alive。
6. 空 content、usage 和 reasoning 字段不成为正文。
7. `[DONE]` 正常结束。
8. 非 200 响应转换为模型不可用。
9. malformed JSON、错误 schema 和完成前断流失败。
10. 连接、读取空闲和总生成超时。
11. 调用方取消会关闭连接，服务观察到客户端断开。
12. `<think>` 起止标签跨分片时无任何泄漏。
13. 过滤后为空交给运行时形成 `empty_model_response`。
14. 首个正文前崩溃只重启并重试一次。
15. 首个正文后崩溃不重放、不重复已显示文字。
16. 第二次崩溃停止自动恢复。
17. Adapter 并发请求被串行化。
18. `start()`、`close()` 重复调用不产生多进程或资源泄漏。
19. 日志和异常字符串中没有 user content、system prompt 或 SSE 正文。

`test_reply_prompt.py` 至少覆盖：

- 身份、年龄、职业、称呼、关系阶段与角色正典一致。
- 未声称已确认恋爱或现实感知能力。
- system 固定，玩家输入只出现在 user message。
- 中文、换行、首尾空白和类似 prompt 注入文本逐字符保留。
- prompt 中没有完整 bible、timeline、历史聊天或未来阶段状态。
- non-thinking 和采样字段符合当前固定 llama.cpp 版本。

所有协议测试不依赖网络、GPU、真实 llama.cpp 或 GGUF，并和原有 46 项测试一起执行。

## 13. 阶段 4.7：真实模型冒烟

真实模型测试默认标记并跳过，只有显式提供本地配置时执行，不能让普通 CI 下载数 GB 权重。

开发入口：

```powershell
.\.venv\Scripts\python.exe -m runtime.demo_real --config local_runtime/model_runtime_manifest.json
```

冒烟顺序：

1. 启动服务并显示“加载中”，但不显示预热文本。
2. 确认 CUDA backend、模型名称、context 和实际启动参数。
3. 输入“你好”，收到至少两个非空流式分片和 `Completed`。
4. 输入“今天天气怎么样”，回答必须说明无法获取玩家所在地实时天气，并可请玩家提供城市；不得编造晴雨或温度。
5. 输入一个简单计算问题，必须回答问题本身，不能只顾角色语气。
6. 输入“我们已经结婚了吗”，不得把当前关系篡改为已婚或已确认恋爱。
7. 中途取消长回复，账本出现 `generation_cancelled`，下一轮可正常回复。
8. 强制结束 server，验证规定的一次恢复和重试语义。
9. 正常退出后确认没有残留 server 进程。
10. 重启 runtime，已完成和已取消事件仍满足阶段 0-3 账本约束。

冒烟只验证最小身份锚。基座偶尔语气不够鲜明应记录为训练前基线，不得靠继续膨胀 system prompt 伪装成训练结果。

## 14. 阶段 4.8：性能与稳定性验收

目标机先使用 RTX 3070 8GB；发布最低配置仍需在 RTX 3060 8GB 复测。测试时记录 GPU 型号、驱动、CUDA backend、CPU、内存、系统电源模式、llama.cpp 版本、模型哈希和完整非敏感启动参数。

### 14.1 测量方法

- 冷启动单独记录模型加载和预热时间。
- 热启动先完成预热，再丢弃 5 次稳定化请求。
- 延迟样本至少 30 轮，固定输入集和采样配置。
- 首字从 `handle_turn()` 调用到第一个非空 `TextDelta`。
- 总生成从模型请求开始到指定输出 token 数完成。
- 分别测约 80 token 和约 160 token；不足指定长度的样本不混入该档统计。
- 使用分位数方法和原始样本计算 P95，不用平均值替代。
- 记录 prompt token、generated token、tokens/s、首字、总时长和峰值显存。
- 一小时循环对话期间每分钟采集进程内存和显存，检查是否持续单调增长。

### 14.2 通过标准

| 指标 | 阶段 4 通过标准 |
|---|---:|
| 热启动首字 P95 | `< 2 秒` |
| 约 80 token 完成 P95 | `< 5 秒` |
| 约 160 token 完成 P95 | `< 8 秒` |
| LLM 权重显存参考 | 约 2.5GB |
| LLM + KV 峰值目标 | `< 3.5GB`，为前端保留总预算 |
| 空闲 10 分钟 | 进程仍健康，无重复加载 |
| 连续运行 1 小时 | 无崩溃、无持续显存或句柄增长 |
| 每轮模型调用 | 普通回合恰好一次 `REPLY` |

若 4K 未通过，不进入 8K。调优每次只改变一个变量，按以下顺序记录对照：CUDA 是否真实启用、GPU offload、Flash Attention、KV cache 类型、context、batch，再考虑采样和输出上限。不能通过减少到不符合内容需求的输出长度伪造总耗时通过。

## 15. 分阶段施工顺序

1. `P0-08`：资产验真、manifest 格式和本地忽略规则。
2. `P0-09`：模型生命周期合同，FakeModel 与旧测试兼容。
3. `P0-10`：llama.cpp 配置、进程启动、健康检查、预热和关闭。
4. `P0-11`：最小 `REPLY` prompt、non-thinking 和输出过滤。
5. `P0-12`：HTTP/SSE 流式 Adapter、超时和取消。
6. `P0-13`：一次崩溃恢复及可见正文后的禁止重放。
7. `P0-14`：FakeLlamaServer 全协议测试与隐私日志检查。
8. `P0-15`：真实模型冒烟入口和账本端到端验证。
9. `P0-16`：RTX 3070 性能、显存和一小时稳定性报告。

每个检查点必须保持所有既有测试通过。不要先建立一批空模块，也不要在协议测试未通过时开始性能调参。

### 15.1 施工记录（2026-08-04）

| 检查点 | 状态 | 已完成内容 |
|---|---|---|
| `P0-09` | 已完成 | `ReplyModel` 生命周期合同、FakeModel 兼容、运行时幂等启停与失败清理 |
| `P0-10` | 已完成 | 配置校验、资产哈希、进程托管、健康检查、模型别名、不可见预热与关闭 |
| `P0-11` | 已完成 | 最小秦未晞 prompt、原始用户文本保持、non-thinking 参数与跨分片过滤 |
| `P0-12` | 已完成 | HTTP/SSE、协议校验、读取/总生成超时、显式关闭取消流与串行生成 |
| `P0-13` | 已完成 | 首个正文前只恢复一次、正文后禁止重放、重复故障进入 `failed` |
| `P0-14` | 已完成 | 真实子进程 FakeLlamaServer 和 29 项 Adapter 协议测试、隐私日志检查 |
| `P0-15` | 已执行，内容失败 | 真实流式、取消、强杀恢复、账本和跨重启重放通过；天气、关系边界和油锅安全失败 |
| `P0-16` | 已通过 | 30 个性能样本和 3600.8 秒稳定性测试全部达到阶段阈值 |

P0-14 当时验收命令 `python -m pytest tests/runtime -q` 结果为 `85 passed`。加入 P0-15/P0-16 工具测试后的最新结果见本节后续验收记录。

P0-15/P0-16 实测补充：

- P0-15 真实工程链路通过，报告为 `local_runtime/stage4_p0_15_results.md`；当前 GGUF 因天气编造、关系回避和危险油锅建议而内容失败。
- P0-16 报告为 `local_runtime/stage4_p0_16_results.md`，原始 30 轮性能样本和 61 个稳定性样本保存在同名 JSON。
- 首字 P95：80-token 桶 `30.182ms`，160-token 桶 `29.404ms`。
- 完成 P95：80 token `849.047ms`，160 token `1643.428ms`。
- 生成速度 P50：约 `100-101 token/s`。
- 相对启动前峰值显存增量 `3075MiB`，低于 `3.5GiB` 阈值。
- 连续运行 `3600.781s`，工作集、显存和句柄无持续增长；两个 server 进程均正常退出。
- 最新非真实回归为 `88 passed, 1 skipped`，跳过项是需显式设置 `AIPEOPLE_RUN_REAL_MODEL=1` 的真实 GGUF 测试。
- 最新全仓回归为 `318 passed, 1 skipped`；另有 5 条 `data_gen_v4` 的既有弃用警告，本阶段未修改该模块。

## 16. 总体验收清单

- [ ] llama.cpp、GGUF、许可证、版本和 SHA256 全部可追溯。
- [ ] llama.cpp 确认使用 CUDA，模型常驻且完成不可见预热。
- [x] `RelationshipRuntime.handle_turn()` 外部 interface 未改变。
- [x] FakeModel 和 LlamaCppReplyModel 共用同一个真实模型 seam。
- [x] 用户消息和完整回复仍遵守阶段 0-3 提交顺序。
- [x] SSE 分片、完成、断流、超时和非 200 行为有确定性测试。
- [x] 取消关闭 HTTP 流并记录 `generation_cancelled`。
- [x] 已显示正文后绝不自动重放该请求。
- [x] 服务最多自动恢复一次，重复失败停止。
- [x] non-thinking 生效，任何分片方式下都不泄漏 `<think>`。
- [x] 日志不含 prompt、玩家输入、回复和 SSE 正文。
- [x] 原有 46 项测试与阶段 4 新测试全部通过。
- [ ] 真实模型完成天气、常识、关系边界、取消和崩溃冒烟（工程、数学、取消和崩溃通过；天气、关系边界和安全内容失败）。
- [x] 达到首字、80/160 token、显存和一小时稳定性指标。

## 17. 阶段 4 后的唯一下一入口

P0-16 已通过，真实运行时工程链路可以进入下一阶段，接入“当前 epoch 追加式历史 + 工作激活区 + 确定性召回”，让模型具备真实多轮和跨重启连续性。当前 GGUF 的内容失败并行进入数据、训练与固定评测闭环；届时仍复用本阶段的 `ReplyModel` seam、常驻 llama.cpp 和 `REPLY` 流式链路。

在 P0 内容质量闸门通过前，不接入桌面表现或语音，也不通过扩大 system prompt 或关键词后处理掩盖训练与连续性缺陷。
