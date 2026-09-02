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


def _latest_run_dir() -> Path | None:
    values = sorted(
        (path for path in OUTPUT_ROOT.glob("qwen35_27b_base_lora_60turn_*") if (path / "report.json").is_file()),
        key=lambda path: path.name,
        reverse=True,
    )
    return values[0] if values else None


class ReviewStore:
    def __init__(self, run_dir: Path | None = None) -> None:
        self.run_dir = run_dir

    def resolve_run_dir(self) -> Path:
        value = self.run_dir or _latest_run_dir()
        if value is None or not (value / "report.json").is_file():
            raise HTTPException(status_code=404, detail="27B 基座 / LoRA 60轮报告尚未生成")
        return value

    def report(self) -> dict[str, Any]:
        return json.loads((self.resolve_run_dir() / "report.json").read_text(encoding="utf-8"))

    def review_path(self) -> Path:
        return self.resolve_run_dir() / USER_REVIEW_FILENAME

    def empty_review(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_report": str(self.resolve_run_dir() / "report.json"),
            "created_at": datetime.now().astimezone().isoformat(),
            "updated_at": None,
            "reviews": {},
        }

    def load_review(self) -> dict[str, Any]:
        path = self.review_path()
        if not path.is_file():
            return self.empty_review()
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("source_report") != str(self.resolve_run_dir() / "report.json"):
            return self.empty_review()
        if not isinstance(value.get("reviews"), dict):
            value["reviews"] = {}
        return value

    def write_review(self, value: dict[str, Any]) -> None:
        temporary = self.review_path().with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.review_path())


store = ReviewStore()
app = FastAPI(title="Qwen3.5-27B NF4 base vs Baiweixi LoRA review")


def _empty_arm_summary() -> dict[str, Any]:
    return {
        "turns": 0,
        "address_current": {"pass": 0, "partial": 0, "fail": 0},
        "state_consistency": {"pass": 0, "uncertain": 0, "fail": 0},
        "internal_logic": {"coherent": 0, "local_logic_break": 0},
        "unsupported_claim_turns": 0,
        "all_four_dimensions_clean": 0,
    }


def _audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    turns = []
    for turn in report["turns"]:
        turns.append(
            {
                "turn": turn["turn"],
                "phase": turn["phase"],
                "player": turn["player"],
                "authoritative_world": turn["authoritative_world"],
                "expectation": turn["expectation"],
                "base": {"reply": turn["base"]["response"], "elapsed_ms": turn["base"].get("elapsed_ms"), "manual_review": {}},
                "full_program": {"reply": turn["lora"]["response"], "elapsed_ms": turn["lora"].get("elapsed_ms"), "manual_review": {}},
            }
        )
    return {
        "generated_at": report["generated_at"],
        "review_protocol": {"blind": False, "automatic_quality_judgment": False, "reviewer": "user"},
        "summary": {"base": _empty_arm_summary(), "full_program": _empty_arm_summary()},
        "automatic_summary": report["summary"],
        "phase_summary": [],
        "source_report": str(store.resolve_run_dir() / "report.json"),
        "has_prior_review": False,
        "run_enabled": False,
        "arm_labels": {
            "base": {
                "name": "A 裸基座 Qwen3.5-27B NF4",
                "short_name": "A 裸基座",
                "detail": "官方 Qwen3.5-27B · 运行时 NF4 · 最近6条消息 · 关闭思考",
            },
            "full_program": {
                "name": "B Qwen3.5-27B NF4 + 白未晞 LoRA",
                "short_name": "B LoRA",
                "detail": "同一NF4模型实例 · 仅启用白未晞 rank 8 LoRA · 最近6条消息 · 关闭思考",
            },
        },
        "turns": turns,
    }


def _public_review(value: dict[str, Any]) -> dict[str, Any]:
    reviews = value.get("reviews", {})
    return {
        "source_report": value["source_report"],
        "created_at": value["created_at"],
        "updated_at": value.get("updated_at"),
        "completed": len(reviews),
        "total": 60,
        "reviews": reviews,
    }


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
    raise HTTPException(status_code=409, detail="该页面绑定本次冻结结果，不从浏览器重跑")


@app.post("/api/cancel")
async def cancel() -> dict[str, Any]:
    return await status()


@app.get("/api/report")
async def report() -> FileResponse:
    return FileResponse(store.resolve_run_dir() / "report.json", filename="qwen35_27b_base_lora_60turn_report.json")


@app.get("/api/review")
async def review() -> JSONResponse:
    return JSONResponse(_audit_payload(store.report()))


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    return JSONResponse(_public_review(store.load_review()))


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    value = store.load_review()
    if not store.review_path().is_file():
        store.write_review(value)
    return FileResponse(store.review_path(), filename="qwen35_27b_base_lora_60turn_user_review.json")


@app.put("/api/user-review/{turn_number}")
async def save_user_review(turn_number: int, payload: dict[str, Any]) -> JSONResponse:
    if turn_number < 1 or turn_number > 60:
        raise HTTPException(status_code=404, detail="轮次不存在")
    base_quality = payload.get("base_quality")
    lora_quality = payload.get("full_quality")
    winner = payload.get("winner")
    note = payload.get("note", "")
    if base_quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择裸基座好或差")
    if lora_quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择LoRA好或差")
    if winner not in {"base", "full_program", "tie"}:
        raise HTTPException(status_code=422, detail="请选择本轮更好的一侧或持平")
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise HTTPException(status_code=422, detail="备注必须是1000字以内文本")
    value = store.load_review()
    now = datetime.now().astimezone().isoformat()
    value["updated_at"] = now
    value["reviews"][str(turn_number)] = {
        "turn": turn_number,
        "base_quality": base_quality,
        "full_quality": lora_quality,
        "winner": winner,
        "note": note.strip(),
        "reviewed_at": now,
    }
    store.write_review(value)
    return JSONResponse(_public_review(value))


@app.delete("/api/user-review")
async def reset_user_review() -> JSONResponse:
    value = store.empty_review()
    store.write_review(value)
    return JSONResponse(_public_review(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3.5-27B NF4 base vs LoRA review page")
    parser.add_argument("--run-dir")
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    if args.run_dir:
        store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
