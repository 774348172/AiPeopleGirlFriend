from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "v5_manual_hard_gate10_static"
SHARED_STYLES = Path(__file__).resolve().parent / "qwen35_no_action_60turn_static" / "styles.css"
USER_REVIEW_FILENAME = "user_review.json"
ALLOWED_ISSUES = {
    "did_not_answer",
    "fact_error",
    "subject_error",
    "logic_error",
    "unsupported_claim",
    "unknown_handling",
    "other",
}
app = FastAPI(title="V5 10轮硬门人工审核")


class Store:
    run_dir: Path | None = None

    def resolve(self) -> Path:
        if self.run_dir is None or not (self.run_dir / "report.json").is_file():
            raise HTTPException(status_code=404, detail="10轮报告尚未生成")
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
        path = self.review_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2 * (attempt + 1))


store = Store()


def public_review(value: dict[str, Any], total: int) -> dict[str, Any]:
    reviews = value.get("reviews", {})
    qualities = [item.get("quality") for item in reviews.values()]
    return {
        "source_report": value["source_report"],
        "created_at": value["created_at"],
        "updated_at": value.get("updated_at"),
        "completed": len(reviews),
        "total": total,
        "good": qualities.count("good"),
        "bad": qualities.count("bad"),
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
    return FileResponse(SHARED_STYLES, media_type="text/css")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    report = store.report()
    return {"status": report["status"], "progress": len(report["turns"]), "total": len(report["turns"]), "error": None}


@app.get("/api/report")
async def report_file() -> FileResponse:
    return FileResponse(store.resolve() / "report.json", filename="v5_manual_hard_gate10_report.json")


@app.get("/api/review")
async def review() -> JSONResponse:
    report = store.report()
    turns = []
    for turn in report["turns"]:
        turns.append(
            {
                "turn": turn["turn"],
                "phase": turn["category"],
                "player": turn["player"],
                "visible_context": turn["visible_context"],
                "review_focus": turn["review_focus"],
                "model_input": turn["model_input"],
                "response": turn["response"],
                "elapsed_ms": turn["generation"].get("elapsed_ms"),
            }
        )
    return JSONResponse(
        {
            "generated_at": report["generated_at"],
            "model": report["model"],
            "controls": report["controls"],
            "review_protocol": {"automatic_quality_judgment": False, "reviewer": "user"},
            "turns": turns,
        }
    )


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    total = len(store.report()["turns"])
    return JSONResponse(public_review(store.load_review(), total))


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    value = store.load_review()
    if not store.review_path().is_file():
        store.write_review(value)
    return FileResponse(store.review_path(), filename="v5_manual_hard_gate10_user_review.json")


@app.put("/api/user-review/{turn_number}")
async def save_review(turn_number: int, payload: dict[str, Any]) -> JSONResponse:
    total = len(store.report()["turns"])
    if turn_number < 1 or turn_number > total:
        raise HTTPException(status_code=404, detail="轮次不存在")
    quality = payload.get("quality")
    if quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择好或差")
    issues = payload.get("issues", [])
    if not isinstance(issues, list) or any(issue not in ALLOWED_ISSUES for issue in issues):
        raise HTTPException(status_code=422, detail="问题类型无效")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise HTTPException(status_code=422, detail="备注必须是1000字以内文本")
    value = store.load_review()
    now = datetime.now().astimezone().isoformat()
    value["updated_at"] = now
    value["reviews"][str(turn_number)] = {
        "turn": turn_number,
        "quality": quality,
        "issues": issues if quality == "bad" else [],
        "note": note.strip(),
        "reviewed_at": now,
    }
    store.write_review(value)
    return JSONResponse(public_review(value, total))


@app.delete("/api/user-review")
async def reset_review() -> JSONResponse:
    value = store.empty_review()
    store.write_review(value)
    return JSONResponse(public_review(value, len(store.report()["turns"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
