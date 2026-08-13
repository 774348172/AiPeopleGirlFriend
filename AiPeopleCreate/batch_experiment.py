"""方案 A 实验：单 prompt 多输出（批量 3 条/请求）vs 单条模式——耗时与质量对比。

公平性：两者都串行执行（消除并行波动），同 9 个 item、同模型、同 max_tokens。
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")

from dataclasses import replace

from openai import OpenAI

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.modes.reply import (
    ReplyModeAdapter,
    _bullet,
    _default_semantic_check,
    _parse_messages_obj,
)
from data_gen_v4.adapters.models.pool import ModelPool, OpenAICompatModelAdapter
from data_gen_v4.adapters.sources.registry import (
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.plan import PackageSetV4, RunSpec
from data_gen_v4.core.sink import MemorySink
from gen_qin_v4 import PROFILES_ROOT, QWX_PACKAGE_SET, ROOT, qin_style_resolver

API_KEY = "cg_d8b27ebbe82a552c0748b4d26a1c1b2b0b844743bf2a7b4b"
BASE_URL = "https://aihub.lmdgame.com/api-product/v1"
MODEL = "deepseek-v4-flash"

registry = FilePackageRegistry(PROFILES_ROOT)
loader = CompositeSourceLoader(ROOT)
factory = RecipeDrivenItemFactory(pools_path=str(ROOT / "profiles/qinweixi/pools.yaml"))
compiler = GenerationPlanCompiler(registry, loader, factory)
result = compiler.compile(RunSpec(run_id="batch-exp", seed=42), QWX_PACKAGE_SET)
facts = result.context["facts"]
facts_text = "\n".join(f"- {f}" for f in facts[:12])
style_contract = qin_style_resolver("style:qinweixi-casual-v1")

# 9 个 item（3 组 × 3，不同 topic）
items = [
    result.plan.items[0], result.plan.items[100], result.plan.items[270],
    result.plan.items[350], result.plan.items[415], result.plan.items[450],
    result.plan.items[500], result.plan.items[543], result.plan.items[555],
]

# ── 基线：单条模式（串行 9 次调用）──
adapter = OpenAICompatModelAdapter(base_url=BASE_URL, api_key_env="OPENAI_API_KEY", default_model=MODEL)
pool = ModelPool(adapters={"o": adapter})
pool.default_id = "o"
engine = GenerationEngineV4(
    {"REPLY": ReplyModeAdapter(style_contract_resolver=qin_style_resolver)},
    package_context=result.context,
)
plan = replace(result.plan, items=items)
t0 = time.time()
run = engine.execute(plan, result.lock, pool, MemorySink())
base_elapsed = time.time() - t0
base_ok = run.completed
print(f"[基线 单条] {len(items)} 条: completed={run.completed} failed={run.failed} "
      f"调用={len(adapter.calls)} 总耗时={base_elapsed:.0f}s")

# ── 批量：3 组 × 3 条/请求（串行 3 次调用）──
PROMPT_TPL = """你不是助手。你就是下面这个角色本身。

【风格合同】（来自角色档案，严格遵守）
{style_contract}

【事实正典】（只能使用这些，不得新增）
{facts}

【任务】
下面有 3 个场景，为每个场景各生成一段 4-6 轮的对话（一问一答算两轮）。
输出一个 JSON 数组，每项对应一个场景：
[
  {{
    "human_turns": ["玩家台词1", "玩家台词2"],
    "assistant_propositions": ["命题1", "命题2"],
    "assistant_tones": ["playful", "tender"],
    "messages": [{{"role": "human", "text": "..."}}, {{"role": "assistant", "text": "..."}}, ...]
  }},
  ...
]

{scenes}

要求：
1. 骨架部分：human 台词口语自然、只基于玩家视角、不含任何角色秘密；propositions 每场景 1-3 条短事实句，以第一人称（主语"我"），禁止"她"、角色名作主语，逐条可被事实正典支持。
2. 实现部分：每条 messages 第一条必须是 human，最后一条必须是 assistant，严格交替。
3. 【命题必须逐字保留】每条 assistant_propositions 的完整原句必须作为连续子串出现在该场景的 assistant 台词中，不得改写、删词、换词。
4. assistant 台词严格遵守风格合同（叙述视角、词汇、句长、标点、禁 emoji、禁 markdown、禁助手腔）。
5. 只允许实现骨架中的命题：不得增加场景之外的人物、地点、物品、因果、时间或共同经历。
6. 3 个场景互不影响，各自独立。

只输出 JSON 数组本身，不要任何解释、代码块标记或思考过程。
"""


def scene_block(item, idx):
    inp = item["input"]
    return (
        f"【场景 {idx}】\n"
        f"场景：{inp.get('scene', '日常')}\n"
        f"玩家视角：{inp.get('player_view', '')}\n"
        f"话题：{inp.get('topic', '闲聊')}\n"
        f"必须完成的行为：{_bullet(item.get('required_behaviors', []))}\n"
        f"禁止出现的命题：{_bullet(item.get('forbidden_behaviors', []))}\n"
    )


client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=180.0)
checker = ReplyModeAdapter(style_contract_resolver=qin_style_resolver)
batch_elapsed = 0.0
batch_ok = 0
batch_fail_reasons = []

for g in range(0, len(items), 3):
    group = items[g : g + 3]
    scenes = "\n".join(scene_block(i.to_dict(), k + 1) for k, i in enumerate(group))
    prompt = PROMPT_TPL.format(
        style_contract=style_contract, facts=facts_text, scenes=scenes
    )
    t0 = time.time()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0.7,
        max_tokens=8192,
    )
    content = getattr(resp.choices[0].message, "content", None) or ""
    if not content:
        content = getattr(resp.choices[0].message, "reasoning_content", None) or ""
    elapsed = time.time() - t0
    batch_elapsed += elapsed
    print(f"  批量请求{g // 3 + 1}: {elapsed:.0f}s（{len(group)} 条）")

    # 解析与检查
    try:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        if not isinstance(data, list) or len(data) != len(group):
            batch_fail_reasons.append(f"组{g//3+1}: 输出条数 {len(data) if isinstance(data, list) else '非数组'} != {len(group)}")
            continue
        for k, (item, entry) in enumerate(zip(group, data)):
            try:
                props = [str(p) for p in entry.get("assistant_propositions", [])]
                msgs = _parse_messages_obj(entry)
                checker._check_structure(msgs, (4, 8))
                missing = _default_semantic_check(props, " ".join(m["content"] for m in msgs if m["role"] == "assistant"))
                if missing:
                    batch_fail_reasons.append(f"组{g//3+1} 条{k+1}: 语义保持失败 {missing}")
                else:
                    batch_ok += 1
            except Exception as e:
                batch_fail_reasons.append(f"组{g//3+1} 条{k+1}: {str(e)[:60]}")
    except Exception as e:
        batch_fail_reasons.append(f"组{g//3+1}: 解析失败 {str(e)[:60]}")

print(f"[批量 3条/请求] {len(items)} 条: 成功={batch_ok} 请求={len(items)//3} 次 "
      f"总耗时={batch_elapsed:.0f}s")
if batch_fail_reasons:
    print("失败明细:")
    for r in batch_fail_reasons:
        print("  -", r)

print(f"\n=== 对比 ===")
print(f"单条: {base_elapsed:.0f}s ({len(adapter.calls)} 次请求)")
print(f"批量: {batch_elapsed:.0f}s ({len(items)//3} 次请求)")
print(f"提速: {(1 - batch_elapsed / base_elapsed) * 100:.0f}%")
