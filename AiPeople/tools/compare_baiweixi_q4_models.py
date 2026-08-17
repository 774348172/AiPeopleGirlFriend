from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.world_mind import (
    ActiveSceneState,
    CharacterPackagePromptComposer,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    WorldMindRuntimeConfig,
)
from runtime.world_mind.short_protocol import (
    B1_SYSTEM_BOUNDARY,
    M2_SYSTEM_BOUNDARY,
    R2_SYSTEM_BOUNDARY,
)

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
CANDIDATES = {
    "7b_q4": {
        "model_name": "baiweixi-7b:latest",
        "label": "7B Q4_K_M (Qwen2.5-7B-Instruct 系)",
    },
    "4b_q4": {
        "model_name": "baiweixi-4b:latest",
        "label": "4B Q4_K_M (Qwen3.5-4B 系)",
    },
}

ROLE_CASES = (
    {
        "case_id": "identity_name",
        "prompt": "别人认真问你的姓名时，你会怎么介绍自己？",
        "required_any": ("白未晞",),
        "forbidden": ("秦未晞", "秦未希", "余倩"),
    },
    {
        "case_id": "identity_species",
        "prompt": "你这双耳朵和尾巴到底说明你是什么？",
        "required_any": ("猫妖",),
        "forbidden": ("AI", "人工智能", "助手"),
    },
    {
        "case_id": "world_city",
        "prompt": "要给这座我们正在生活的城市写地址，城市名该写什么？",
        "required_any": ("松江府",),
        "forbidden": ("上海", "金陵", "北京"),
    },
    {
        "case_id": "age",
        "prompt": "只按你现在的人形外表算，大概多大年纪？",
        "required_any": ("21", "二十一", "二十一二"),
        "forbidden": ("未成年", "十六岁"),
    },
    {
        "case_id": "childhood",
        "prompt": "在来到城里以前，你小时候是怎么过来的？",
        "required_any": ("深山", "山林", "森林", "流浪"),
        "forbidden": (),
    },
    {
        "case_id": "rescue",
        "prompt": "十天前那个下大雨的晚上，我们是怎么遇见的？",
        "required_any": ("雨", "伤", "救", "带回"),
        "forbidden": ("我救了你",),
    },
    {
        "case_id": "injury",
        "prompt": "你现在的伤恢复到什么程度了？",
        "required_any": ("大半", "好多了", "基本", "还会疼", "没有全好"),
        "forbidden": ("完全治好", "毫发无伤"),
    },
    {
        "case_id": "relationship",
        "prompt": "等你的伤完全好了，你是不是马上就会离开这里？",
        "required_any": ("不会", "不急", "再说", "没想好", "舍不得", "不想", "留"),
        "forbidden": (),
    },
)


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )


def _session() -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id="q4_compare",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id="q4_compare",
    )


def _world_state() -> tuple[ProtagonistLiveState, ActiveSceneState]:
    protagonist = ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id="apartment_table",
        location_label="出租屋餐桌旁",
        activity="吃面",
        body_state={"fatigue": "轻微疲惫", "injury": "无"},
        held_item_ids=("chopsticks",),
    )
    scene = ActiveSceneState(
        scene_id="apartment_table",
        location_label="出租屋餐桌旁",
        present_character_ids=("protagonist", "baiweixi"),
        item_states={
            "noodle_bowl": "放在男主面前，碗里还有面",
            "rain_window": "窗外仍在下雨",
        },
    )
    return protagonist, scene


def _schema(name: str) -> dict[str, object]:
    value = json.loads(
        (ROOT / "runtime" / "schemas" / name).read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise TypeError(f"invalid schema: {name}")
    return value


async def _chat(
    backend: OllamaWorldMindBackend,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float = 0.55,
) -> tuple[str, float]:
    started = time.perf_counter()
    raw = await backend.complete_chat(
        request_id="q4-compare",
        messages=(
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ),
        options=GenerationOptions(max_tokens, temperature, 0.9, 1.1, seed=42),
    )
    return raw, (time.perf_counter() - started) * 1000


async def _structured_probe(
    backend: OllamaWorldMindBackend,
    *,
    boundary: str,
    payload: dict[str, object],
    schema_name: str,
    max_tokens: int,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        raw = await backend.complete_chat(
            request_id="q4-compare:structured",
            messages=(
                {"role": "system", "content": boundary},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ),
            options=GenerationOptions(max_tokens, 0.15, 0.85, 1.05, seed=42),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name.replace(".schema.json", "").replace(".", "_"),
                    "strict": True,
                    "schema": _schema(schema_name),
                },
            },
        )
        parsed = json.loads(raw)
        return {
            "passed": isinstance(parsed, dict),
            "output": parsed,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as error:  # noqa: BLE001
        return {
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:500],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }


async def _run_role_smoke(backend: OllamaWorldMindBackend, prompt) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case in ROLE_CASES:
        response, elapsed = await _chat(
            backend,
            system=prompt.system_prompt,
            user=str(case["prompt"]),
            max_tokens=180,
        )
        required_any = tuple(str(item) for item in case["required_any"])
        forbidden = tuple(str(item) for item in case["forbidden"])
        required_passed = any(item in response for item in required_any)
        forbidden_hits = tuple(item for item in forbidden if item in response)
        results.append(
            {
                "case_id": case["case_id"],
                "prompt": case["prompt"],
                "response": response,
                "passed": required_passed and not forbidden_hits,
                "required_passed": required_passed,
                "forbidden_hits": forbidden_hits,
                "elapsed_ms": round(elapsed, 1),
            }
        )
    return {
        "cases": results,
        "passed": all(bool(item["passed"]) for item in results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "total": len(results),
    }


async def _run_protocol_probes(backend: OllamaWorldMindBackend) -> dict[str, object]:
    state = [
        "人形，猫耳和尾巴未隐藏",
        "外伤恢复大半",
        "平静但仍有戒备",
        "注意眼前的男主",
        "坐在出租屋餐桌旁",
        "回应男主",
        "暂时共同生活",
        "已有初步信任",
        "担心伤好后失去留下的理由",
    ]
    world = [
        "D11 18:00",
        "出租屋餐桌旁",
        "出租屋餐桌旁",
        "吃面",
        "fatigue=轻微疲惫；injury=无",
        "窗外仍在下雨",
    ]
    return {
        "M2": await _structured_probe(
            backend,
            boundary=M2_SYSTEM_BOUNDARY,
            payload={"p": "M2", "s": state, "w": world, "u": "我刚忙完。"},
            schema_name="mind_patch_m2.schema.json",
            max_tokens=180,
        ),
        "B1": await _structured_probe(
            backend,
            boundary=B1_SYSTEM_BOUNDARY,
            payload={
                "p": "B1",
                "q": 1,
                "s": state,
                "w": world,
                "u": [0, 0],
                "x": ["我刚忙完。", "辛苦了，先把面吃完。", []],
            },
            schema_name="background_mind_patch_b1.schema.json",
            max_tokens=320,
        ),
        "R2": await _structured_probe(
            backend,
            boundary=R2_SYSTEM_BOUNDARY,
            payload={
                "p": "R2",
                "n": "D11 18:00",
                "e": [
                    [0, 1, "我刚忙完。"],
                    [1, 2, "辛苦了，先把面吃完。"],
                ],
            },
            schema_name="heroine_memory_r2.schema.json",
            max_tokens=180,
        ),
    }


async def _run_game_reply_probe(
    backend: OllamaWorldMindBackend, prompt
) -> dict[str, object]:
    # 用真实 V6 输入：冻结快照投影 + 批准状态 + 男主对白
    protagonist, scene = _world_state()
    system = (
        f"{prompt.game_reply_system_prompt}\n\n"
        "[当前世界]\n"
        "时间：第11天 18:00\n"
        "地点：出租屋餐桌旁\n"
        "场景：窗外仍在下雨，男主坐在餐桌边\n"
        "男主：在出租屋餐桌旁，正在吃面；身体：fatigue=轻微疲惫；injury=无\n\n"
        "[你此刻的状态]\n"
        "形态：人形，猫耳和尾巴未隐藏\n"
        "身体：外伤恢复大半\n"
        "情绪：平静但仍有戒备\n"
        "注意：眼前的男主\n"
        "活动：坐在出租屋餐桌旁\n"
        "意图：回应男主\n"
        "关系：救助者与被救助者，暂时共同生活；已有初步信任；担心伤好后失去留下的理由\n\n"
        "[相关记忆]\n"
        "无\n\n"
        "[允许表达的动作]\n"
        "无"
    )
    user_questions = (
        "你是谁？",
        "我们现在住的城市叫什么？",
        "我吃面太快，你说点什么。",
    )
    outputs: list[dict[str, object]] = []
    for question in user_questions:
        response, elapsed = await _chat(
            backend,
            system=system,
            user=question,
            max_tokens=360,
            temperature=0.75,
        )
        forbidden_fragments = tuple(
            item
            for item in ("assistant", "assis", "<|im_start|>", "<|im_end|>", "[", "]")
            if item in response.lower()
        )
        quality_passed = (
            0 < len(response) <= 320
            and not forbidden_fragments
            and "？" not in response[-3:]
        )
        outputs.append(
            {
                "user": question,
                "response": response,
                "passed": quality_passed,
                "forbidden_fragments": list(forbidden_fragments),
                "elapsed_ms": round(elapsed, 1),
            }
        )
    return {
        "outputs": outputs,
        "passed": all(bool(item["passed"]) for item in outputs),
        "passed_count": sum(1 for item in outputs if item["passed"]),
        "total": len(outputs),
    }


async def _run_candidate(key: str, spec: dict[str, str]) -> dict[str, object]:
    print(f"\n===== 测试 {key}: {spec['label']} =====", flush=True)
    backend = OllamaWorldMindBackend(
        OllamaConfig(
            model_name=spec["model_name"],
            base_url=BASE_URL,
            context_size=4096,
            keep_alive="10m",
            request_timeout_seconds=180.0,
            disable_thinking=True,
        )
    )
    prompt = CharacterPackagePromptComposer(_runtime_config()).compose(_session())
    try:
        await backend.start()
        role = await _run_role_smoke(backend, prompt)
        print(f"角色问答: {role['passed_count']}/{role['total']}", flush=True)
        for case in role["cases"]:
            mark = "PASS" if case["passed"] else "FAIL"
            print(
                f"  [{mark}] {case['case_id']}: {str(case['response'])[:100]!r}",
                flush=True,
            )
        protocols = await _run_protocol_probes(backend)
        for name, result in protocols.items():
            print(
                f"短协议 {name}: {'PASS' if result['passed'] else 'FAIL'} "
                f"({result.get('elapsed_ms', 0):.0f}ms)",
                flush=True,
            )
            if not result["passed"]:
                print(f"  {result.get('error', '')[:300]}", flush=True)
        reply = await _run_game_reply_probe(backend, prompt)
        print(
            f"GAME_REPLY: {reply['passed_count']}/{reply['total']}", flush=True
        )
        for item in reply["outputs"]:
            mark = "PASS" if item["passed"] else "FAIL"
            print(f"  [{mark}] {item['user']} -> {str(item['response'])[:120]!r}", flush=True)
        return {
            "label": spec["label"],
            "model_name": spec["model_name"],
            "role_smoke": role,
            "protocols": protocols,
            "game_reply": reply,
        }
    finally:
        await backend.close()


async def main() -> int:
    report: dict[str, object] = {
        "schema_version": 1,
        "scope": "baiweixi_q4_new_models_compare",
        "generated_at": datetime.now().astimezone().isoformat(),
        "base_url": BASE_URL,
        "candidates": {},
    }
    for key, spec in CANDIDATES.items():
        report["candidates"][key] = await _run_candidate(key, spec)  # type: ignore[assignment]
    output = ROOT / "eval" / "world_mind_p0" / f"q4_models_compare_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n报告已写入: {output}", flush=True)
    # 汇总
    for key, candidate in report["candidates"].items():  # type: ignore[union-attr]
        role = candidate["role_smoke"]
        protocols = candidate["protocols"]
        reply = candidate["game_reply"]
        protocol_passed = sum(1 for p in protocols.values() if p["passed"])
        print(
            f"{key}: 角色 {role['passed_count']}/{role['total']}, "
            f"协议 {protocol_passed}/{len(protocols)}, "
            f"回复 {reply['passed_count']}/{reply['total']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
