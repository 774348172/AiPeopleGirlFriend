> 【归档声明 2026-08-27】意图记账已从设计中删除（世界状态即账本）；进行中动作投影已并入《游戏-LLM 接口契约设计_20260827.md》。本文档归档，仅作历史追溯。

# 长时动作与意图子系统设计(2D 游戏前提版)(2026-08-27)

> 状态：**权威设计（施工依据）** | 日期：2026-08-27
> 依据：《游戏-LLM 接口契约设计_20260827.md》、《Harness组装与单次判断设计_20260827.md》、《动作执行闭环施工方案_2D游戏前提_20260827.md》
> 参照实现：DeepSeek Harness `schedule/` 与 `goal/` 子系统（`D:\AIPeopleGit\deepseek-harness\packages\schedule`、`packages\goal`，MIT）、opencode `todowrite` 工具（`D:\AIPeopleGit\opencode\packages\opencode\src\tool\todo.ts`）
> **前提修正（2026-08-27）**：2D 游戏拥有世界与执行——进行中动作（长时动作）由**游戏模拟循环**负责，runtime 只做状态投影；意图（承诺）层归 runtime，按 opencode 模式简化为"记账 + 提醒 + 证据校验"，不再做严格状态机。

## 一、参照机制回顾（背景，理解用）

### 1.1 DeepSeek schedule（原对应"长时动作"）

- 持久记录以事件追加进会话日志，当前有效提醒 = 事件流 fold 派生；到期时渲染文本经 follow-up 队列进入对话；空闲期维护 + 持久化屏障。
- **在新前提下的位置**：这套机制正是 2D 游戏模拟循环该做的事（动作计时、到期生成实体）——**由游戏实现**，runtime 不复刻。

### 1.2 DeepSeek goal（原对应"意图"）

- `goal/change` 事件携带完整快照入日志，fold 严格校验（revision +1、相位白名单、轮次预算）。
- **在新前提下的位置**：意图层仍归 runtime，但按 opencode `todowrite` 模式**大幅简化**——程序只记录与显示，模型声明即接受；不做事件表/fold/revision/相位白名单/超时强制。

### 1.3 opencode todowrite（简化依据）

- 模型调用 `todowrite` 一次性提交整个清单（pending/in_progress/completed/cancelled），程序只做权限询问 + 持久化，**零校验**；纪律写在工具描述文本里（"completed 必须是真正做完，不能凭意图"）——软约束，模型判断，程序信任。

## 二、设计总览

| 子系统 | 归属 | 机制 | 消费方 |
|---|---|---|---|
| 进行中动作（长时动作） | **游戏** | 游戏模拟循环（动画/计时/实体生成）；runtime 只读投影 | 上下文区块 6（模型判断冲突/打断依据） |
| 女主角意图（承诺） | **LLM runtime** | 一张表记账 + 每轮提醒 + 模型声明 + 一条证据校验 | 上下文区块 7；判断契约 `intention_resolutions` |

## 三、子系统 A：进行中动作 = 游戏状态投影

### 3.1 runtime 侧（只读，无状态机）

- `pending_actions` = **游戏进行中动作的投影**：动作、剩余秒数（接口契约 §五），每次对话快照时读取；
- `current_activity` 在动作期间 = 游戏投影（她在 2D 世界真的在煮面）；动作外由模型自管（字段划分见接口契约 §三）；
- runtime **不建事件表、不 fold、不倒计时、不写效果**——全部是游戏的活。

### 3.2 游戏侧（参考 schedule 机制实现，不在本仓库 runtime 内）

- 游戏模拟循环维护进行中动作：开始（动画播放）、计时、到期（实体生成：面在桌上）、打断（玩家操作触发，如叫住走路中的她）；
- 玩家打断与资源冲突由游戏裁决，结果随投影回注（模型下轮基于真实状态圆场）。

### 3.3 能力边界（机制不变）

- `action_id` 不在游戏导出清单 → 不转发，拒绝原因回注下一轮上下文（《动作执行闭环施工方案》§3.2）；
- 动作语义约束在描述文本中（占用什么/可不可打断），模型读取判断，程序不解析。

## 四、子系统 B：意图记账（承诺层，简化版）

### 4.1 数据（一张表，无事件流）

```sql
CREATE TABLE heroine_intentions (
  intention_id      TEXT PRIMARY KEY,
  save_id           TEXT NOT NULL,
  text              TEXT NOT NULL,        -- "我去给你做饭"
  status            TEXT NOT NULL,        -- open | closed
  evidence_event_id TEXT,                 -- 承诺所在回合的 assistant 事件
  created_game_time TEXT NOT NULL
);
```

### 4.2 生命周期（程序只记录与显示，模型声明即接受）

```
模型回复含承诺（或 actions 提议）→ 程序记一条 open（带证据 event_id）
每轮上下文区块 7 注入 unresolved_intentions：[{id, text, status, 已过轮数}]
模型在判断契约声明 fulfilled / cancelled / deferred
  → cancelled / deferred：程序直接置 closed（模型说算）
  → fulfilled：程序查游戏投影证据（面实体存在 / 世界变化）
       有证据 → closed；无证据 → 保持 open（下轮继续提醒，不销账）
```

### 4.3 收口提醒（软提示，非强制）

- 超过 3 轮仍 open 的意图，区块 7 注入文本加注"（已经拖了 3 轮了）"——**软提示**，裁决仍归模型；
- 不做超时强制取消、不做相位白名单、不做 revision——对齐 opencode 模式。

### 4.4 与动作层的关系

- 意图是**关系层**（她承诺了什么、对玩家的交代）；动作执行是**游戏层**（世界真的发生了什么）；
- 她承诺做饭 → 意图 open + 动作转发游戏；游戏完成煮面（面实体生成）→ **游戏投影即为 fulfilled 的证据**，runtime 直接比对，无需自建判断。

## 五、与现有代码的集成点

| 集成点 | 位置 | 改动 |
|---|---|---|
| pending_actions 投影 | `runtime/world_mind/model_payloads.py` | 快照构建时读游戏进行中动作（接口契约 §五） |
| heroine_intentions 表 | `runtime/world_mind/persistence.py` | 新增一张表（无 fold，普通读写） |
| 意图注入/收口 | `runtime/world_mind/model_payloads.py` + judge 契约解析 | 区块 7 渲染 + `intention_resolutions` 处理（4.2） |
| 动作转发 | runtime → 游戏（接口契约 §四） | 校验后转发执行请求 |

## 六、验收标准

1. 游戏进行中动作投影进区块 6（动作 + 剩余时间）；游戏完成煮面后，下轮上下文出现面实体；
2. 意图 open 入账 → 每轮区块 7 可见 → 模型声明处理；
3. fulfilled 有游戏投影证据 → closed；无证据 → 保持 open 并继续提醒；
4. cancelled/deferred 直接接受（模型说算）；
5. 回归：chat01/02/diagnostic120 无回归。

## 七、施工顺序（runtime 侧）

| 步骤 | 内容 | 依赖 |
|---|---|---|
| 1 | heroine_intentions 表 + 区块 7 注入 + intention_resolutions 处理 | 无 |
| 2 | 游戏投影接入（pending_actions/current_activity/item_states 读游戏） | 游戏接口就绪 |
| 3 | 动作转发通道（校验 → 游戏执行 → 拒绝回注） | 游戏接口就绪 |
| 4 | 评测用例（意图收口/证据校验/软提示）+ 回归 | 步骤 1-3 |

## 八、不做（本轮边界）

- runtime 不做进行中动作状态机（游戏内部实现）；
- 不做意图事件表 / fold / revision / 相位白名单 / 超时强制取消；
- 不做玩家侧意图操作界面（文本层模型自然处理）。

## 附：关键文件

- 参照实现：`D:\AIPeopleGit\deepseek-harness\packages\schedule\schedule\src\{types,runtime,domain}.ts`、`packages\goal\goal\src\{domain,fold}.ts`、`D:\AIPeopleGit\opencode\packages\opencode\src\tool\todo.ts`
- 本仓库：《游戏-LLM 接口契约设计_20260827.md》、《Harness组装与单次判断设计_20260827.md》、《动作执行闭环施工方案_2D游戏前提_20260827.md》
