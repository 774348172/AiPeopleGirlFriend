# tools/（AI 程序侧）

本目录保留 AI 程序侧工具；生成器工具位于 `F:\ai-girlfriend\AiPeopleCreate\tools\`。

| 文件 | 用途 |
|---|---|
| `v2500_chat_proxy.py` | 本地聊天代理（基于 `runtime._prompt.QIN_WEIXI_REPLY_SYSTEM` 转发请求） |
| `refreeze_v3.py` | CHAT-01 v3 评测契约重冻结工具（配合 `eval/chat01/` 冻结资产） |
| `run_qinweixi_ollama_world_mind_smoke.py` | 用秦未晞旧 Ollama 模型对 V6 五种 World Mind 模式做隔离工程兼容性测试；使用独立数据库，不作为白未晞语义验收 |
| `run_baiweixi_ollama_world_mind_smoke.py` | 校验白未晞 GGUF SHA256 与 Ollama digest，运行 V6 五模式、前后台事务、模型身份审计和基础角色 Smoke；工程接入与角色质量分别出结论 |
| `run_baiweixi_quality_suite.py` | 正式执行白未晞冻结质量集的 89 个自动案例与冻结种子，输出原始尝试、独立语义初筛、模式级失败归因、评测完整性审计和人工复核队列 |
| `apply_baiweixi_manual_adjudication.py` | 校验逐案裁决是否完整覆盖 59 个机器失败案例，并生成带最终决定、归因和理由的已裁决复核队列 |
| `render_baiweixi_failed_cases_review.py` | 将逐案裁决后仍未通过的 43 个案例渲染为审核稿，包含冻结要求、三种子回复或结构失败、机器评测信号和最终裁决理由 |
