# RelationshipRuntime 阶段 6 P0-24 验收报告

> 日期：2026-08-05  
> 结论：通过  
> 下一检查点：P0-25

## 已实现

- 新增无副作用的 `parse_prospective_command()` 内部 interface，只依赖输入原文、发生时间和 IANA timezone。
- 每条输入最多输出一个 `create`、`confirm`、`cancel` 或 `complete` 候选，不查库、不写库、不调用模型。
- 明确支持 reminder、shared_plan 和 promise 建立；确认、取消、完成必须同时包含类型和可定位目标短语。
- `YYYY-MM-DD HH:MM`、完整中文年月日加阿拉伯数字时刻，以及今天/明天/后天加阿拉伯数字时刻可归一化为 UTC。
- 周末、下周、以后、过几天、只有日期、只有时刻、左右、时间范围、过去时间和非法时间都不生成确定 `due_at`。
- reminder/shared_plan 缺少明确时间时建立为 `proposed`；带模糊时间的 promise 同样保持 `proposed`。
- 明确且无截止时间的 promise 可以直接 `confirmed`，例如“我答应你不再熬夜”。
- 条件句、转述、问题、否定提醒、普通聊天、泛指目标和无类型的“完成了”不产生命令。

## 明确边界

当前解析器只接受冻结的保守中文句式和阿拉伯数字时刻，不声称理解所有自然语言。中文数字时刻、复杂周期规则、多个命令、修改计划、代词目标和跨句指代均不自动执行。无法确定时宁可 `proposed` 或 `no_command`，不能猜测玩家意图。

P0-24 只生成候选。目标短语还没有与数据库中的活跃计划匹配，聊天也不会在本检查点自动创建或转换 plan；这些属于 P0-25。

## 自动验收

- P0-24 定向测试：35 passed。
- 全仓：460 passed、3 skipped、5 warnings。
- `compileall` 通过。
- 5 条 warning 来自未修改的 `data_gen_v4` schema validator 弃用提示。

