# AiPeople — 把一个虚构的人烤进 LLM 权重

一个"人型"大模型项目。目标不是推理强，而是**像一个人**：有稳定人格、有自传式过去、能实时产生新记忆、并能随经历缓慢成长。

## 设计要点

- **虚构的人**：人设由 `persona/` 定义，其"一生"被生成器产出为语料，SFT 进权重。
- **双系统记忆**（生物启发，人脑=海马体+新皮层）：
  - **快速轨（海马体）**：每轮对话实时写/读情景记忆，<秒级。这是"实时新记忆"的实现。
  - **慢轨（新皮层）**：周期性把积累的记忆用低 LR LoRA + replay buffer 固化进权重。这是"成长"的实现。
- **人格在权重，不在 prompt**：基座被 SFT 训成这个人本身；运行时只补一个轻量"身份锚"（姓名、当前日期）保证时间/身份清晰。
- **刻意不评推理**：评测只看人格一致性、自传记忆召回、无助手腔。

```
运行时（实时）: 输入→检索快速轨→构造(锚+记忆+对话)→基座生成→抽取salience→DECIDE写回
慢轨（周期）:   反射(情景→高价值样本)+replay→低LR LoRA固化→model soup→新adapter
```

先例：快速轨=Stanford Generative Agents 三分量检索 + mem0 extract→DECIDE；慢轨=RoleLLM 切分 + replay+低LR+model soup；桥=Generative Agents reflection。

## 里程碑

| M | 内容 | DoD |
|---|---|---|
| M0 | 人设 + 人生生成器 + 一致性检查 | 助手腔/结构/emoji <5%，canon 矛盾 <5%，口吻匹配 |
| M1 | QLoRA SFT 基座 → 快照人格 | 口吻像此人 + 自传记忆答对 + 无助手腔 |
| M2 | 海马体接入运行时 | **合格门槛**：实时记住跨轮/跨会话新事 |
| M3 | 反射 + 慢轨固化 | 固化后关快速轨仍能召回；人格不漂移 |
| M4 | 评测套件 + drift 监控 + retention | 全套通过 |

## 安装（Windows 原生，无需 WSL）

```bash
# 1) Python 环境（建议 conda, Python 3.11）
conda create -n aipeople python=3.11 -y && conda activate aipeople

# 2) 先装匹配本机 CUDA 的 PyTorch（M1 起需要）——见 https://pytorch.org
#    M0 阶段可不装 torch，先跑语料生成与一致性检查。

# 3) 装依赖
pip install -r requirements.txt

# 4) 配置模型端点
cp .env.example .env
#   编辑 .env 填 OPENAI_API_KEY / OPENAI_BASE_URL
```

## 接入模型（生成语料 + LLM 裁判）

用 OpenAI 兼容接口，可指向任意供应商/本地。改 `.env`：

| 供应商 | OPENAI_BASE_URL | model（config.yaml 或 STRONG_MODEL） |
|---|---|---|
| OpenAI | https://api.openai.com/v1 | gpt-4o-mini |
| GLM (智谱) | https://open.bigmodel.cn/api/paas/v4 | glm-4-plus |
| DeepSeek | https://api.deepseek.com | deepseek-chat |
| Qwen (DashScope) | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| 本地 Ollama | http://localhost:11434/v1 | qwen2.5:7b |

## 跑 M0（命门验证）

```bash
# 生成 ~50 条样本到 data/life_corpus/
python -m data_gen.life_generator --num-nodes 15 --out data/life_corpus/m0.jsonl

# 一致性检查
python -m data_gen.consistency_check --in data/life_corpus/m0.jsonl
```

看输出报告：助手腔率 / 结构泄漏率 / emoji 率 / canon 矛盾率是否都 <5%。命门成立才扩张到 1k+ 样本进 M1。

M0 也可加 Protective Scene（教模型在人格知识范围外婉拒，防越界幻觉）：
```bash
python -m data_gen.life_generator --num-nodes 15 --protective 20 --out data/life_corpus/m0.jsonl
```

## 跑 M1（人格 SFT，LLaMA-Factory + Qwen3-4B）

M1 不自己写训练代码，用 [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 作引擎。本仓库只产出三样喂给 LF 的东西：sharegpt 数据、数据集注册、QLoRA 配置。

```bash
# 0) 装 LLaMA-Factory（含 torch；按本机 CUDA）
pip install llamafactory[torch,metrics] bitsandbytes

# 1) 把语料转成 sharegpt 格式（life_corpus → data/sft/aipeople_persona.jsonl）
python -m data_gen.to_sharegpt --in data/life_corpus/m0.jsonl --clean
#   --clean 只用通过一致性检查的样本；去掉则用全量

# 2) 把数据集注册 + 数据复制到 LLaMA-Factory 的 data/ 目录
cp data/sft/aipeople_persona.jsonl  <LF路径>/data/
cp data/dataset_info.json           <LF路径>/data/   # 合并到 LF 已有的 dataset_info.json（同名键）

# 3) 训练（Qwen3-4B QLoRA，~8GB）
llamafactory-cli train training/configs/qwen3_4b_qlora.yaml

# 4) 对话验证（加载训练好的 adapter）
llamafactory-cli chat training/configs/qwen3_4b_qlora.yaml
#    （chat 时把该 yaml 的 do_train 删掉/忽略，adapter_name_or_path 指向 output_dir）

# 5) 合并 adapter 为完整权重（可选，便于导出 GGUF / M2 加载）
#    用 LF 的 export 流程，或见 unsloth merge；合并后可转 GGUF 走 Ollama 推理
```

验证 M1 是否达标：口吻像此人 + 自传记忆答对（问它 baked-in 人生事件）+ 无助手腔。可跑 `eval/` 下的脚手架（见下）。

## 目录

```
persona/      人设档案（bible/timeline/canon）——正典种子
data_gen/      人生生成器 + 一致性检查（命门）
data/          life_corpus / sft / consolidated
memory/        海马体：store/extractor/store_writer/retriever/reflector（M2）
training/      SFT + 慢轨固化 + replay buffer（M1/M3）
runtime/       server/loop/persona_anchor（M2）
eval/          人格一致性/自传记忆/反助手腔/漂移监控（M1/M3/M4）
```

## 注

- 实际选定：**~8GB VRAM + Qwen3-4B + LLaMA-Factory QLoRA**。Qwen3-8B 在 8GB 上做 QLoRA 大概率 OOM；若 4B 仍 OOM 降到 Qwen3-1.7B（改 yaml 的 model_name_or_path）。
- 模型确切版本以你 HF 镜像为准；架构与版本解耦，按 VRAM 缩放基座尺寸。
- 基座默认 Qwen（Apache-2.0、QLoRA 工具链熟）；偏好 GLM 系可改用 GLM-4-9B-Chat（license 较严，LF 配置 template 改 glm）。
- LF 的 yaml/数据集注册键以你装的 LF 版本为准；本配置为模板，键不识别时按 LF 文档微调或用 `llamafactory-cli webui` 可视化配置。
