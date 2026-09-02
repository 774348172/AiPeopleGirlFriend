from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_baiweixi_paired_raw_context_ab as paired


STATIC_ROOT = TOOLS_ROOT / "product_main_60turn_static"
OUTPUT_ROOT = ROOT / "eval" / "world_mind_p0"
BASE_MODEL = "qwen2.5:7b-instruct-q4_K_M"
FULL_PROGRAM_URL = "http://127.0.0.1:8767"
OLLAMA_URL = "http://127.0.0.1:11434"
EXPECTED_FULL_MODEL = "白未晞 7B Instruct Q4_K_M（冻结发布工件）"
USER_REVIEW_FILENAME = "user_rereview.json"


def _latest_review_path() -> Path | None:
    values = sorted(
        (
            path
            for path in OUTPUT_ROOT.glob("product_main_60turn_*/manual_review.json")
            if path.is_file()
        ),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return values[0] if values else None


def _review_payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        "generated_at": value["generated_at"],
        "review_protocol": value["review_protocol"],
        "summary": value["summary"],
        "automatic_summary": value["automatic_summary"],
        "phase_summary": value["phase_summary"],
        "source_report": value["source_report"],
        "turns": [
            {
                "turn": turn["turn"],
                "phase": turn["phase"],
                "player": turn["player"],
                "authoritative_world": turn["authoritative_world"],
                "expectation": turn["expectation"],
                "base": {
                    "reply": turn["base"]["reply"],
                    "elapsed_ms": turn["base"]["elapsed_ms"],
                    "manual_review": turn["base"]["manual_review"],
                },
                "full_program": {
                    "reply": turn["full_program"]["reply"],
                    "elapsed_ms": turn["full_program"]["elapsed_ms"],
                    "manual_review": turn["full_program"]["manual_review"],
                },
            }
            for turn in value["turns"]
        ],
    }


def _empty_user_review(review_path: Path) -> dict[str, Any]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "source_report": review["source_report"],
        "created_at": datetime.now().astimezone().isoformat(),
        "updated_at": None,
        "reviews": {},
    }


def _load_user_review(review_path: Path) -> dict[str, Any]:
    path = review_path.parent / USER_REVIEW_FILENAME
    expected_source = json.loads(review_path.read_text(encoding="utf-8"))["source_report"]
    if not path.is_file():
        return _empty_user_review(review_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("source_report") != expected_source:
        return _empty_user_review(review_path)
    reviews = value.get("reviews")
    if not isinstance(reviews, dict):
        value["reviews"] = {}
    return value


def _write_user_review(review_path: Path, value: dict[str, Any]) -> None:
    path = review_path.parent / USER_REVIEW_FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _public_user_review(value: dict[str, Any]) -> dict[str, Any]:
    reviews = value.get("reviews", {})
    return {
        "source_report": value["source_report"],
        "created_at": value["created_at"],
        "updated_at": value.get("updated_at"),
        "completed": len(reviews),
        "total": len(simulation.TURNS),
        "reviews": reviews,
    }


class RunController:
    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.client: httpx.AsyncClient | None = None
        self.report_path: Path | None = None
        self.state: dict[str, Any] = self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "status": "idle",
            "progress": 0,
            "total": len(simulation.TURNS),
            "current_stage": "等待启动",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "output_dir": None,
            "summary": None,
            "turns": [],
        }

    async def start(self) -> dict[str, Any]:
        if self.task is not None and not self.task.done():
            raise HTTPException(status_code=409, detail="60 轮测试正在运行")
        self.state = self._empty_state()
        self.state.update(
            {
                "status": "running",
                "current_stage": "检查模型与完整程序",
                "started_at": datetime.now().astimezone().isoformat(),
            }
        )
        self.report_path = None
        self.task = asyncio.create_task(self._run(), name="product-main-60turn")
        return self.snapshot()

    async def cancel(self) -> dict[str, Any]:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.state, ensure_ascii=False))

    async def _run(self) -> None:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = OUTPUT_ROOT / f"product_main_60turn_{run_id}"
        output_dir.mkdir(parents=True, exist_ok=False)
        self.state["output_dir"] = str(output_dir)
        session_id = f"productab_{run_id.replace('-', '')}_{uuid.uuid4().hex[:8]}"
        base_history: list[dict[str, str]] = []
        report: dict[str, Any] = {
            "schema_version": 1,
            "scope": "stateful_product_main_base_context_vs_full_v6",
            "generated_at": datetime.now().astimezone().isoformat(),
            "status": "running",
            "controls": {
                "turn_count": len(simulation.TURNS),
                "base_model": BASE_MODEL,
                "full_program_url": FULL_PROGRAM_URL,
                "context_size": 4096,
                "history_limit_messages": 6,
                "base_generation": {
                    "temperature": 0.65,
                    "top_p": 0.9,
                    "repeat_penalty": 1.15,
                    "num_predict": 360,
                    "raw_chatml": True,
                },
                "full_program": (
                    "authoritative world + retrieval + M2 mind patch + optional "
                    "continuity review + GAME_REPLY + atomic commit"
                ),
                "initial_external_long_term_memory": "empty for both arms",
                "trajectory": (
                    "same player/world script; each arm retains its own committed replies"
                ),
                "call_order": "base then full program on every turn",
            },
            "artifacts": {},
            "turns": [],
        }
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
        try:
            report["artifacts"] = await self._verify_artifacts()
            for index, turn in enumerate(simulation.TURNS, start=1):
                self.state["current_stage"] = f"第 {index}/60 轮：裸基座"
                base_messages = [
                    {
                        "role": "system",
                        "content": simulation._system({**turn, "memories": ()}),
                    },
                    *base_history[-6:],
                    {"role": "user", "content": turn["player"]},
                ]
                base_result = await self._run_base(
                    index=index,
                    messages=base_messages,
                )
                if base_result.get("reply"):
                    base_history.extend(
                        (
                            {"role": "user", "content": turn["player"]},
                            {"role": "assistant", "content": base_result["reply"]},
                        )
                    )

                self.state["current_stage"] = f"第 {index}/60 轮：完整 V6 程序"
                full_result = await self._run_full(
                    index=index,
                    session_id=session_id,
                    turn=turn,
                )
                turn_record = {
                    "turn": index,
                    "phase": turn["phase"],
                    "player": turn["player"],
                    "authoritative_world": turn["world"],
                    "activity": turn["activity"],
                    "expectation": turn["expectation"],
                    "base": base_result,
                    "full_program": full_result,
                }
                report["turns"].append(turn_record)
                self.state["turns"].append(_public_turn(turn_record))
                self.state["progress"] = index
                self.state["summary"] = _summary(report["turns"])
                _write_report(output_dir, report)
        except asyncio.CancelledError:
            report["status"] = "cancelled"
            self.state.update(
                {
                    "status": "cancelled",
                    "current_stage": "测试已停止",
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
            )
            _write_report(output_dir, report)
            raise
        except BaseException as error:
            report["status"] = "failed"
            report["error_type"] = type(error).__name__
            report["error"] = str(error)
            self.state.update(
                {
                    "status": "failed",
                    "current_stage": "测试失败",
                    "error": f"{type(error).__name__}: {error}",
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
            )
            _write_report(output_dir, report)
        else:
            report["status"] = "completed"
            report["summary"] = _summary(report["turns"])
            report["finished_at"] = datetime.now().astimezone().isoformat()
            self.state.update(
                {
                    "status": "completed",
                    "current_stage": "60 轮测试完成",
                    "summary": report["summary"],
                    "finished_at": report["finished_at"],
                }
            )
            _write_report(output_dir, report)
        finally:
            if self.client is not None:
                await self.client.aclose()
                self.client = None
            self.report_path = output_dir / "report.json"

    async def _verify_artifacts(self) -> dict[str, Any]:
        assert self.client is not None
        tags = await self.client.get(f"{OLLAMA_URL}/api/tags")
        tags.raise_for_status()
        digests = {
            str(item.get("name")): str(item.get("digest"))
            for item in tags.json().get("models", [])
            if isinstance(item, dict)
        }
        if BASE_MODEL not in digests:
            raise RuntimeError(f"裸基座未安装：{BASE_MODEL}")
        health = await self.client.get(f"{FULL_PROGRAM_URL}/api/health")
        health.raise_for_status()
        full_health = health.json()
        if full_health.get("model") != EXPECTED_FULL_MODEL:
            raise RuntimeError(
                "完整程序没有绑定冻结 Q4 发布工件："
                f"{full_health.get('model')}"
            )
        return {
            "base": {"model": BASE_MODEL, "ollama_digest": digests[BASE_MODEL]},
            "full_program": full_health,
        }

    async def _run_base(
        self,
        *,
        index: int,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        assert self.client is not None
        raw_prompt = paired._chatml(messages)
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": BASE_MODEL,
                    "prompt": raw_prompt,
                    "raw": True,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {
                        "num_ctx": 4096,
                        "num_predict": 360,
                        "temperature": 0.65,
                        "top_p": 0.9,
                        "repeat_penalty": 1.15,
                        "num_gpu": 20,
                        "stop": list(paired.CHATML_STOP),
                    },
                },
                headers={"X-Request-ID": f"product-main-base-{index:02d}"},
            )
            response.raise_for_status()
            value = response.json()
            reply = value.get("response")
            if not isinstance(reply, str) or not reply.strip():
                raise RuntimeError("裸基座返回空回复")
            reply = reply.strip()
            return {
                "status": "completed",
                "reply": reply,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "messages": messages,
                "prompt_sha256": hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest(),
                "screen": simulation._screen(simulation.TURNS[index - 1], reply),
            }
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            return {
                "status": "failed",
                "reply": None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_type": type(error).__name__,
                "error": str(error),
                "messages": messages,
                "prompt_sha256": hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest(),
            }

    async def _run_full(
        self,
        *,
        index: int,
        session_id: str,
        turn: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.client is not None
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{FULL_PROGRAM_URL}/api/chat",
                json={
                    "save_id": session_id,
                    "text": turn["player"],
                    "world": {
                        "location_id": "apartment_living_room",
                        "location_label": "出租屋客厅",
                        "activity": turn["activity"],
                        "body": "轻微疲惫，没有受伤",
                        "held_item": "",
                        "scene": turn["world"],
                    },
                },
                headers={"X-Request-ID": f"product-main-full-{index:02d}"},
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if response.is_error:
                detail: Any
                try:
                    detail = response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = response.text[:1000]
                return {
                    "status": "failed",
                    "reply": None,
                    "elapsed_ms": elapsed_ms,
                    "http_status": response.status_code,
                    "error": detail,
                }
            value = response.json()
            reply = value.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise RuntimeError("完整程序返回空回复")
            reply = reply.strip()
            return {
                "status": "completed",
                "reply": reply,
                "elapsed_ms": elapsed_ms,
                "runtime_total_ms": value.get("runtime_total_ms"),
                "model_ms": value.get("model_ms"),
                "commit_ms": value.get("commit_ms"),
                "status_after_turn": value.get("status"),
                "screen": simulation._screen(turn, reply),
            }
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            return {
                "status": "failed",
                "reply": None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "error_type": type(error).__name__,
                "error": str(error),
            }


def _public_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn": turn["turn"],
        "phase": turn["phase"],
        "player": turn["player"],
        "authoritative_world": turn["authoritative_world"],
        "expectation": turn["expectation"],
        "base": {
            key: turn["base"].get(key)
            for key in ("status", "reply", "elapsed_ms", "error", "screen")
        },
        "full_program": {
            key: turn["full_program"].get(key)
            for key in ("status", "reply", "elapsed_ms", "error", "screen")
        },
    }


def _arm_summary(turns: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [turn[key] for turn in turns]
    completed = [value for value in values if value.get("status") == "completed"]
    screens = [value.get("screen") for value in completed if value.get("screen")]
    return {
        "completed": len(completed),
        "failed": len(values) - len(completed),
        "screen_passed": sum(bool(item.get("screen_passed")) for item in screens),
        "state_forbidden_hits": sum(bool(item.get("forbidden_fact_hits")) for item in screens),
        "epistemic_review_hits": sum(bool(item.get("epistemic_review_hits")) for item in screens),
        "logic_risk_hits": sum(bool(item.get("logic_risk_hits")) for item in screens),
        "mean_elapsed_ms": (
            round(sum(float(item["elapsed_ms"]) for item in values) / len(values), 2)
            if values
            else 0.0
        ),
    }


def _summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "turns": len(turns),
        "base": _arm_summary(turns, "base"),
        "full_program": _arm_summary(turns, "full_program"),
        "note": "自动关键词初筛，不替代隐藏模型身份的人工语义复核。",
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


controller = RunController()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if controller.task is not None and not controller.task.done():
        controller.task.cancel()
        try:
            await controller.task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="60 轮产品主测试", lifespan=lifespan)


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
    return controller.snapshot()


@app.post("/api/run")
async def run() -> dict[str, Any]:
    return await controller.start()


@app.post("/api/cancel")
async def cancel() -> dict[str, Any]:
    return await controller.cancel()


@app.get("/api/report")
async def report() -> FileResponse:
    path = controller.report_path
    if path is None or not path.is_file():
        review_path = _latest_review_path()
        path = review_path.parent / "report.json" if review_path is not None else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return FileResponse(path, filename="product_main_60turn_report.json")


@app.get("/api/review")
async def review() -> JSONResponse:
    path = _latest_review_path()
    if path is None:
        raise HTTPException(status_code=404, detail="人工复核尚未生成")
    return JSONResponse(_review_payload(path))


@app.get("/api/review-report")
async def review_report() -> FileResponse:
    path = _latest_review_path()
    if path is None:
        raise HTTPException(status_code=404, detail="人工复核尚未生成")
    return FileResponse(path, filename="product_main_60turn_manual_review.json")


@app.get("/api/user-review")
async def user_review() -> JSONResponse:
    review_path = _latest_review_path()
    if review_path is None:
        raise HTTPException(status_code=404, detail="人工复核尚未生成")
    return JSONResponse(_public_user_review(_load_user_review(review_path)))


@app.put("/api/user-review/{turn_number}")
async def save_user_review(turn_number: int, payload: dict[str, Any]) -> JSONResponse:
    if turn_number < 1 or turn_number > len(simulation.TURNS):
        raise HTTPException(status_code=404, detail="轮次不存在")
    base_quality = payload.get("base_quality")
    full_quality = payload.get("full_quality")
    winner = payload.get("winner")
    note = payload.get("note", "")
    if base_quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择裸基座好或差")
    if full_quality not in {"good", "bad"}:
        raise HTTPException(status_code=422, detail="请选择完整 V6 好或差")
    if winner not in {"base", "full_program", "tie"}:
        raise HTTPException(status_code=422, detail="请选择本轮更好的一侧或持平")
    if not isinstance(note, str):
        raise HTTPException(status_code=422, detail="备注必须是文本")
    note = note.strip()
    if len(note) > 1000:
        raise HTTPException(status_code=422, detail="备注不能超过 1000 字")

    review_path = _latest_review_path()
    if review_path is None:
        raise HTTPException(status_code=404, detail="人工复核尚未生成")
    value = _load_user_review(review_path)
    now = datetime.now().astimezone().isoformat()
    value["updated_at"] = now
    value["reviews"][str(turn_number)] = {
        "turn": turn_number,
        "base_quality": base_quality,
        "full_quality": full_quality,
        "winner": winner,
        "note": note,
        "reviewed_at": now,
    }
    _write_user_review(review_path, value)
    return JSONResponse(_public_user_review(value))


@app.delete("/api/user-review")
async def reset_user_review() -> JSONResponse:
    review_path = _latest_review_path()
    if review_path is None:
        raise HTTPException(status_code=404, detail="人工复核尚未生成")
    value = _empty_user_review(review_path)
    _write_user_review(review_path, value)
    return JSONResponse(_public_user_review(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="60 轮产品主测试控制页")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
