# 白未晞本地模型 vs GPT-5.6 Sol 内部四十题对比

本目录保存一次临时的 Lane R 角色直答质量对比。候选为本地
`baiweixi:latest` 与 Codex 环境内部的 `gpt-5.6-sol`。

这不是 OpenAI API 接入测试，不验证 V6 五模式结构化协议、长期记忆、存档恢复、
API 延迟、费用或限流。GPT 候选还受到 Codex 宿主指令影响，因此结果只能用于
判断当前角色资料下的初步回答质量，不能替代正式跨供应商实验。

四十题从冻结质量集的 `character_direct` 层按类别配额和固定 SHA256 排名选择。
候选只读取共享角色 Prompt 和不含 Oracle 的问题文件。评测时才重新关联冻结
Oracle，禁止把标准答案或本地答案提供给 GPT 候选。
