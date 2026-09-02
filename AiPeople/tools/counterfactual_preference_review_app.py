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


STATIC_ROOT = Path(__file__).resolve().parent / "counterfactual_preference_review_static"
SHARED_STYLES = Path(__file__).resolve().parent / "qwen35_no_action_60turn_static" / "styles.css"
ALLOWED_ISSUES = {
    "did_not_answer", "fact_error", "subject_error", "logic_error",
    "unsupported_claim", "unknown_handling", "persona_drift", "other",
}
app = FastAPI(title="反事实偏好质量路线人工审核")


class Store:
    run_dir: Path | None = None

    def resolve(self) -> Path:
        if self.run_dir is None or not (self.run_dir / "report.json").is_file():
            raise HTTPException(status_code=404, detail="A/B 报告不存在")
        return self.run_dir

    def report(self) -> dict[str, Any]:
        return json.loads((self.resolve() / "report.json").read_text(encoding="utf-8"))

    def path(self) -> Path:
        return self.resolve() / "user_review.json"

    def empty(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_report": str(self.resolve() / "report.json"),
            "created_at": datetime.now().astimezone().isoformat(),
            "updated_at": None,
            "reviews": {},
        }

    def load(self) -> dict[str, Any]:
        if not self.path().is_file():
            return self.empty()
        value = json.loads(self.path().read_text(encoding="utf-8"))
        return value if value.get("source_report") == str(self.resolve() / "report.json") else self.empty()

    def write(self, value: dict[str, Any]) -> None:
        temporary = self.path().with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(self.path())
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2 * (attempt + 1))


store = Store()


def public(value: dict[str, Any], total: int) -> dict[str, Any]:
    arms = [review for case in value.get("reviews", {}).values() for review in case.values()]
    return {
        **value,
        "completed_arms": len(arms),
        "total_arms": total * 2,
        "good": sum(item.get("quality") == "good" for item in arms),
        "bad": sum(item.get("quality") == "bad" for item in arms),
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


@app.get("/api/review")
async def review() -> JSONResponse:
    return JSONResponse(store.report())


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    return JSONResponse(public(store.load(), len(store.report()["cases"])))


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    value = store.load()
    if not store.path().is_file():
        store.write(value)
    return FileResponse(store.path(), filename="counterfactual_preference_ab_user_review.json")


@app.get("/api/report")
async def report() -> FileResponse:
    return FileResponse(store.resolve() / "report.json", filename="counterfactual_preference_ab_report.json")


@app.put("/api/user-review/{ordinal}/{arm}")
async def save_review(ordinal: int, arm: str, payload: dict[str, Any]) -> JSONResponse:
    report = store.report()
    if ordinal < 1 or ordinal > len(report["cases"]) or arm not in {"base", "preference"}:
        raise HTTPException(status_code=404, detail="测试项不存在")
    quality = payload.get("quality")
    if quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择好或差")
    issues = payload.get("issues", [])
    if not isinstance(issues, list) or any(issue not in ALLOWED_ISSUES for issue in issues):
        raise HTTPException(status_code=422, detail="问题类型无效")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise HTTPException(status_code=422, detail="备注必须在1000字以内")
    value = store.load()
    now = datetime.now().astimezone().isoformat()
    case = value["reviews"].setdefault(str(ordinal), {})
    case[arm] = {
        "ordinal": ordinal,
        "arm": arm,
        "quality": quality,
        "issues": issues if quality == "bad" else [],
        "note": note.strip(),
        "reviewed_at": now,
    }
    value["updated_at"] = now
    store.write(value)
    return JSONResponse(public(value, len(report["cases"])))


@app.delete("/api/user-review")
async def reset_review() -> JSONResponse:
    value = store.empty()
    store.write(value)
    return JSONResponse(public(value, len(store.report()["cases"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    store.run_dir = Path(args.run_dir).resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
