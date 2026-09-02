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
STATIC_ROOT = Path(__file__).resolve().parent / "qwen35_no_action_60turn_static"
OUTPUT_ROOT = ROOT / "eval" / "world_mind_p0"
USER_REVIEW_FILENAME = "user_review.json"
ISSUE_TYPES = {
    "off_topic",
    "state_error",
    "logic_error",
    "action_narration",
    "unsupported_claim",
    "other",
}


def _latest_run_dir() -> Path | None:
    values = sorted(
        (
            path
            for path in OUTPUT_ROOT.glob("qwen35_9b_q6_no_action_60turn_*")
            if (path / "report.json").is_file()
        ),
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
            raise HTTPException(status_code=404, detail="纯对白 60 轮报告尚未生成")
        return value

    def report(self) -> dict[str, Any]:
        return json.loads(
            (self.resolve_run_dir() / "report.json").read_text(encoding="utf-8")
        )

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
        path = self.review_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)


store = ReviewStore()
app = FastAPI(title="Qwen3.5-9B Q6 pure dialogue 60-turn review")


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    reviews = value.get("reviews", {})
    qualities = [item.get("quality") for item in reviews.values()]
    issue_counts = {issue: 0 for issue in sorted(ISSUE_TYPES)}
    for item in reviews.values():
        for issue in item.get("issues", []):
            if issue in issue_counts:
                issue_counts[issue] += 1
    return {
        "completed": len(reviews),
        "total": 60,
        "good": qualities.count("good"),
        "bad": qualities.count("bad"),
        "issue_counts": issue_counts,
    }


def _public_review(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_report": value["source_report"],
        "created_at": value["created_at"],
        "updated_at": value.get("updated_at"),
        **_summary(value),
        "reviews": value.get("reviews", {}),
    }


def _audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": report["generated_at"],
        "model": report["model"],
        "controls": report["controls"],
        "automatic_summary": report["summary"],
        "turns": [
            {
                "turn": turn["turn"],
                "phase": turn["phase"],
                "player": turn["player"],
                "authoritative_world": turn["authoritative_world"],
                "expectation": turn["expectation"],
                "response": turn["response"],
                "elapsed_ms": turn.get("elapsed_ms"),
                "format_flags": turn.get("format_flags", {}),
            }
            for turn in report["turns"]
        ],
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
    return {
        "status": "completed",
        "progress": 60,
        "total": 60,
        "current_stage": "纯对白 60 轮结果已生成",
        "error": None,
    }


@app.get("/api/review")
async def review() -> JSONResponse:
    return JSONResponse(_audit_payload(store.report()))


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    return JSONResponse(_public_review(store.load_review()))


@app.get("/api/report")
async def report() -> FileResponse:
    return FileResponse(
        store.resolve_run_dir() / "report.json",
        filename="qwen35_9b_q6_no_action_60turn_report.json",
    )


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    value = store.load_review()
    if not store.review_path().is_file():
        store.write_review(value)
    return FileResponse(
        store.review_path(),
        filename="qwen35_9b_q6_no_action_60turn_user_review.json",
    )


@app.put("/api/user-review/{turn_number}")
async def save_user_review(turn_number: int, payload: dict[str, Any]) -> JSONResponse:
    if turn_number < 1 or turn_number > 60:
        raise HTTPException(status_code=404, detail="轮次不存在")
    quality = payload.get("quality")
    if quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择本轮回复为好或差")
    issues = payload.get("issues", [])
    if not isinstance(issues, list) or any(issue not in ISSUE_TYPES for issue in issues):
        raise HTTPException(status_code=422, detail="问题类型不合法")
    if quality == "good" and issues:
        raise HTTPException(status_code=422, detail="判断为好时不能同时选择问题类型")
    note = payload.get("note", "")
    if not isinstance(note, str):
        raise HTTPException(status_code=422, detail="备注必须是文本")
    note = note.strip()
    if len(note) > 1000:
        raise HTTPException(status_code=422, detail="备注不能超过 1000 字")

    value = store.load_review()
    now = datetime.now().astimezone().isoformat()
    value["updated_at"] = now
    value["reviews"][str(turn_number)] = {
        "turn": turn_number,
        "quality": quality,
        "issues": sorted(set(issues)),
        "note": note,
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
    parser = argparse.ArgumentParser(description="Review Qwen pure-dialogue 60 turns")
    parser.add_argument("--run-dir")
    parser.add_argument("--port", type=int, default=8773)
    args = parser.parse_args()
    if args.run_dir:
        store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
