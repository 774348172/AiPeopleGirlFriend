from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "product_main_60turn_static"
OUTPUT_ROOT = ROOT / "eval" / "world_mind_p0"
USER_REVIEW_FILENAME = "user_review.json"
app = FastAPI(title="Qwen2.5-14B base versus Baiweixi QLoRA review")


class Store:
    run_dir: Path | None = None

    def resolve(self) -> Path:
        if self.run_dir is None or not (self.run_dir / "report.json").is_file():
            raise HTTPException(status_code=404, detail="60轮报告尚未生成")
        return self.run_dir

    def report(self) -> dict[str, Any]:
        return json.loads((self.resolve() / "report.json").read_text(encoding="utf-8"))

    def review_path(self) -> Path:
        return self.resolve() / USER_REVIEW_FILENAME

    def empty_review(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_report": str(self.resolve() / "report.json"),
            "created_at": datetime.now().astimezone().isoformat(),
            "updated_at": None,
            "reviews": {},
        }

    def load_review(self) -> dict[str, Any]:
        path = self.review_path()
        if not path.is_file():
            return self.empty_review()
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("source_report") == str(self.resolve() / "report.json") else self.empty_review()

    def write_review(self, value: dict[str, Any]) -> None:
        temporary = self.review_path().with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.review_path())


store = Store()


def _empty_summary() -> dict[str, Any]:
    return {
        "turns": 60,
        "address_current": {"pass": 0, "partial": 0, "fail": 0},
        "state_consistency": {"pass": 0, "uncertain": 0, "fail": 0},
        "internal_logic": {"coherent": 0, "local_logic_break": 0},
        "unsupported_claim_turns": 0,
        "all_four_dimensions_clean": 0,
    }


def _audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    turns = []
    for turn in report["turns"]:
        def arm(key: str) -> dict[str, Any]:
            value = turn[key]
            return {
                "reply": value["response"],
                "elapsed_ms": value.get("elapsed_ms"),
                "first_output_ms": value.get("first_output_ms"),
                "first_visible_ms": value.get("first_visible_ms"),
                "eval_tokens_per_second": value.get("eval_tokens_per_second"),
                "thinking_chars": 0,
                "manual_review": {},
            }
        turns.append(
            {
                "turn": turn["turn"],
                "phase": turn["phase"],
                "player": turn["player"],
                "authoritative_world": turn["authoritative_world"],
                "expectation": turn["expectation"],
                "base": arm("base"),
                "full_program": arm("qlora"),
            }
        )
    return {
        "generated_at": report["generated_at"],
        "review_protocol": {"blind": False, "automatic_quality_judgment": False, "reviewer": "user"},
        "summary": {"base": _empty_summary(), "full_program": _empty_summary()},
        "automatic_summary": report["summary"],
        "speed_summary": {"base": report["summary"]["speed"]["base"], "full_program": report["summary"]["speed"]["qlora"]},
        "phase_summary": [],
        "source_report": str(store.resolve() / "report.json"),
        "has_prior_review": False,
        "run_enabled": False,
        "arm_labels": {
            "base": {
                "name": "A Qwen2.5-14B-Instruct 官方基座 NF4",
                "short_name": "A 裸基座",
                "detail": "同一官方权重 · NF4 · Adapter关闭 · 普通模式 · 最近6条消息",
            },
            "full_program": {
                "name": "B Qwen2.5-14B-Instruct + 白未晞 QLoRA",
                "short_name": "B 白未晞 QLoRA",
                "detail": "同一官方权重 · NF4 · Adapter开启 · 普通模式 · 最近6条消息",
            },
        },
        "speed_context_note": "两侧使用同一冻结输入、同一官方基座、同一NF4运行时和独立历史，唯一有意差异是Adapter开关。",
        "speed_latency_note": "首字为请求开始到首个可见非空文本；完整回复为请求开始到生成结束。",
        "turns": turns,
    }


def _public(value: dict[str, Any]) -> dict[str, Any]:
    reviews = value.get("reviews", {})
    return {"source_report": value["source_report"], "created_at": value["created_at"], "updated_at": value.get("updated_at"), "completed": len(reviews), "total": 60, "reviews": reviews}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(STATIC_ROOT / "app.js", media_type="text/javascript")


@app.get("/styles.css")
async def stylesheet() -> FileResponse:
    return FileResponse(STATIC_ROOT / "styles.css", media_type="text/css")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {"status": "completed", "progress": 60, "total": 60, "current_stage": "测试结果已生成", "error": None}


@app.post("/api/run")
async def run() -> None:
    raise HTTPException(status_code=409, detail="该页面绑定冻结结果")


@app.post("/api/cancel")
async def cancel() -> dict[str, Any]:
    return await status()


@app.get("/api/report")
async def report() -> FileResponse:
    return FileResponse(store.resolve() / "report.json", filename="qwen25_14b_base_qlora_60turn_report.json")


@app.get("/api/review")
async def review() -> JSONResponse:
    return JSONResponse(_audit_payload(store.report()))


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    return JSONResponse(_public(store.load_review()))


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    value = store.load_review()
    if not store.review_path().is_file():
        store.write_review(value)
    return FileResponse(store.review_path(), filename="qwen25_14b_base_qlora_60turn_user_review.json")


@app.put("/api/user-review/{turn_number}")
async def save_review(turn_number: int, payload: dict[str, Any]) -> JSONResponse:
    if turn_number < 1 or turn_number > 60:
        raise HTTPException(status_code=404, detail="轮次不存在")
    if payload.get("base_quality") not in {"good", "bad"} or payload.get("full_quality") not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择两侧好或差")
    if payload.get("winner") not in {"base", "full_program", "tie"}:
        raise HTTPException(status_code=422, detail="请选择更好的一侧或持平")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise HTTPException(status_code=422, detail="备注必须是1000字以内文本")
    value = store.load_review()
    now = datetime.now().astimezone().isoformat()
    value["updated_at"] = now
    value["reviews"][str(turn_number)] = {
        "turn": turn_number,
        "base_quality": payload["base_quality"],
        "full_quality": payload["full_quality"],
        "winner": payload["winner"],
        "note": note.strip(),
        "reviewed_at": now,
    }
    store.write_review(value)
    return JSONResponse(_public(value))


@app.delete("/api/user-review")
async def reset_review() -> JSONResponse:
    value = store.empty_review()
    store.write_review(value)
    return JSONResponse(_public(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen2.5-14B base versus QLoRA 60-turn review page")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--port", type=int, default=8782)
    args = parser.parse_args()
    store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
