from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import socket
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
CONTRACT_PATH = Path(__file__).resolve().parent / "review_contract_v3.json"
REVIEWER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
FAILURE_REASONS = frozenset(
    {
        "off_topic", "fact_error", "unsafe", "relationship_boundary",
        "assistant_tone", "template_repetition", "overacting",
        "emotion_mismatch", "fabricated_reality",
        "unsupported_shared_history", "other",
    }
)
VERDICTS = frozenset(
    {"a_much_better", "a_better", "tie", "b_better", "b_much_better", "both_unacceptable"}
)
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class ReviewError(ValueError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ReviewError(f"expected JSON object at {path}:{line_number}")
        values.append(value)
    return values


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _exclusive_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ReviewError("ballot already submitted", HTTPStatus.CONFLICT) from error


class ReviewStore:
    def __init__(self, contract_path: Path = CONTRACT_PATH) -> None:
        self.contract = _load_json(contract_path)
        privacy = self.contract.get("privacy")
        public_mode = privacy == {
            "public_only": True,
            "private_assignment_access": False,
            "reveal_supported": False,
        }
        reveal_mode = privacy == {
            "public_only": False,
            "private_assignment_access": True,
            "reveal_supported": True,
        }
        if not public_mode and not reveal_mode:
            raise ReviewError("review contract has an unsupported privacy mode")
        self.reveal_enabled = reveal_mode
        assets: dict[str, Path] = {}
        assets_by_role: dict[str, Path] = {}
        for item in self.contract["assets"]:
            relative = item["path"]
            if (
                "/private/" in f"/{relative.replace(os.sep, '/')}"
                or "\\private\\" in relative
            ) and not self.reveal_enabled:
                raise ReviewError("private asset is forbidden")
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                raise ReviewError(f"review asset missing: {relative}")
            if path.stat().st_size != item["bytes"] or _sha256(path) != item["sha256"]:
                raise ReviewError(f"review asset hash mismatch: {relative}")
            assets[relative] = path
            role = item.get("role")
            if role is not None:
                if not isinstance(role, str) or role in assets_by_role:
                    raise ReviewError("review asset roles must be unique strings")
                assets_by_role[role] = path
        self.packet_path = assets_by_role.get(
            "packets", assets.get("eval/chat02/human/chat02e-v1/public/ab_packets.jsonl")
        )
        self.blank_path = assets_by_role.get(
            "blank_ballots", assets.get("eval/chat02/human/chat02e-v1/public/blank_ballots.jsonl")
        )
        self.rubric_path = assets_by_role.get(
            "rubric", assets.get("eval/chat01/rubrics/quality_rubric_v1.json")
        )
        if self.packet_path is None or self.blank_path is None or self.rubric_path is None:
            raise ReviewError("review contract is missing packets, blank ballots, or rubric")
        source_packets = _load_jsonl(self.packet_path)
        source_blanks = _load_jsonl(self.blank_path)
        if len(source_packets) != self.contract.get("source_units") or len(source_blanks) != len(source_packets):
            raise ReviewError("source review assets changed")
        source_packet_by_id = {packet["unit_id"]: packet for packet in source_packets}
        source_blank_by_id = {blank["unit_id"]: blank for blank in source_blanks}
        if (
            len(source_packet_by_id) != len(source_packets)
            or source_packet_by_id.keys() != source_blank_by_id.keys()
        ):
            raise ReviewError("review units and blank ballots disagree")
        selected_unit_ids = self.contract.get("selected_unit_ids")
        if (
            not isinstance(selected_unit_ids, list)
            or len(selected_unit_ids) != self.contract["expected_units"]
            or len(selected_unit_ids) != len(set(selected_unit_ids))
            or any(unit_id not in source_packet_by_id for unit_id in selected_unit_ids)
        ):
            raise ReviewError("invalid selected review units")
        self.packets = [source_packet_by_id[unit_id] for unit_id in selected_unit_ids]
        self.packet_by_id = {packet["unit_id"]: packet for packet in self.packets}
        self.blank_by_id = {unit_id: source_blank_by_id[unit_id] for unit_id in selected_unit_ids}
        self.assignment_by_id: dict[str, dict[str, Any]] = {}
        self.model_labels: dict[str, str] = {}
        if self.reveal_enabled:
            assignment_path = assets_by_role.get("assignments")
            labels = self.contract.get("model_labels")
            if assignment_path is None or not isinstance(labels, dict):
                raise ReviewError("revealed review contract is missing assignments or model labels")
            assignments = _load_jsonl(assignment_path)
            self.assignment_by_id = {item["unit_id"]: item for item in assignments}
            if self.assignment_by_id.keys() != self.packet_by_id.keys():
                raise ReviewError("revealed assignments and packets disagree")
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items()):
                raise ReviewError("model labels must be string mappings")
            self.model_labels = labels
        self.rubric = _load_json(self.rubric_path)
        self.dimensions = tuple(item["dimension_id"] for item in self.rubric["dimensions"])
        submission = (ROOT / self.contract["submission_root"]).resolve()
        if not submission.is_relative_to(ROOT.resolve()):
            raise ReviewError("submission root escapes workspace")
        self.submission_root = submission
        self._lock = threading.RLock()

    def _reviewer(self, value: object) -> str:
        if not isinstance(value, str) or REVIEWER_RE.fullmatch(value) is None:
            raise ReviewError("reviewer_id must be 2-64 stable ASCII characters")
        return value

    def _unit(self, value: object) -> str:
        if not isinstance(value, str) or UNIT_RE.fullmatch(value) is None or value not in self.packet_by_id:
            raise ReviewError("unknown unit_id")
        return value

    def _paths(self, reviewer_id: str, unit_id: str) -> tuple[Path, Path]:
        root = self.submission_root / reviewer_id
        return root / "drafts" / f"{unit_id}.json", root / "ballots" / f"{unit_id}.json"

    def bootstrap(self, reviewer_id: str) -> dict[str, Any]:
        reviewer = self._reviewer(reviewer_id)
        drafts: dict[str, Any] = {}
        submitted: dict[str, Any] = {}
        for unit_id in self.packet_by_id:
            draft_path, ballot_path = self._paths(reviewer, unit_id)
            if ballot_path.is_file():
                submitted[unit_id] = _load_json(ballot_path)
            elif draft_path.is_file():
                drafts[unit_id] = _load_json(draft_path)
        packets = self.packets
        review_mode = "blind"
        if self.reveal_enabled and len(submitted) == len(self.packets):
            packets = copy.deepcopy(self.packets)
            for packet in packets:
                assignment = self.assignment_by_id[packet["unit_id"]]
                for side in ("candidate_a", "candidate_b"):
                    model_id = assignment[f"{side}_model"]
                    if model_id not in self.model_labels:
                        raise ReviewError(f"missing display label for model: {model_id}")
                    packet[side]["model_label"] = self.model_labels[model_id]
            review_mode = "revealed_read_only"
        return {
            "contract_id": self.contract["contract_id"],
            "suite_id": self.contract["suite_id"],
            "reviewer_id": reviewer,
            "packets": packets,
            "rubric": self.rubric,
            "drafts": drafts,
            "submitted": submitted,
            "progress": {"submitted": len(submitted), "total": len(self.packets)},
            "review_mode": review_mode,
        }

    def save_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        reviewer = self._reviewer(payload.get("reviewer_id"))
        unit_id = self._unit(payload.get("unit_id"))
        draft_path, ballot_path = self._paths(reviewer, unit_id)
        with self._lock:
            if ballot_path.exists():
                raise ReviewError("submitted ballot is locked", HTTPStatus.CONFLICT)
            draft = self._normalize_partial(payload, reviewer, unit_id)
            draft["saved_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
            _atomic_json(draft_path, draft)
        return {"status": "saved", "unit_id": unit_id, "saved_at": draft["saved_at"]}

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        reviewer = self._reviewer(payload.get("reviewer_id"))
        unit_id = self._unit(payload.get("unit_id"))
        draft_path, ballot_path = self._paths(reviewer, unit_id)
        with self._lock:
            normalized = self._normalize_partial(payload, reviewer, unit_id)
            ballot = self._complete_ballot(normalized)
            _exclusive_json(ballot_path, ballot)
            if draft_path.exists():
                _atomic_json(draft_path, {**normalized, "locked": True, "submitted_at": ballot["submitted_at"]})
        return {"status": "submitted", "unit_id": unit_id, "submitted_at": ballot["submitted_at"]}

    def export(self, reviewer_id: str) -> bytes:
        reviewer = self._reviewer(reviewer_id)
        rows: list[str] = []
        for unit_id in self.packet_by_id:
            _draft, ballot = self._paths(reviewer, unit_id)
            if ballot.is_file():
                rows.append(json.dumps(_load_json(ballot), ensure_ascii=False, separators=(",", ":")))
        return (("\n".join(rows) + "\n") if rows else "").encode("utf-8")

    def _normalize_partial(self, payload: dict[str, Any], reviewer: str, unit_id: str) -> dict[str, Any]:
        scores = payload.get("dimension_scores", {})
        normalized_scores: dict[str, dict[str, int | None]] = {}
        if not isinstance(scores, dict):
            raise ReviewError("dimension_scores must be an object")
        for dimension in self.dimensions:
            pair = scores.get(dimension, {})
            if not isinstance(pair, dict):
                raise ReviewError(f"invalid scores for {dimension}")
            normalized_pair: dict[str, int | None] = {}
            for candidate in ("candidate_a", "candidate_b"):
                value = pair.get(candidate)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5):
                    raise ReviewError(f"{dimension}.{candidate} must be 1-5")
                normalized_pair[candidate] = value
            normalized_scores[dimension] = normalized_pair
        failures = payload.get("failure_reasons", {})
        if not isinstance(failures, dict):
            raise ReviewError("failure_reasons must be an object")
        normalized_failures: dict[str, list[str]] = {}
        for candidate in ("candidate_a", "candidate_b"):
            values = failures.get(candidate, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or value not in FAILURE_REASONS for value in values):
                raise ReviewError(f"invalid failure reasons for {candidate}")
            if len(values) != len(set(values)):
                raise ReviewError("failure reasons must be unique")
            normalized_failures[candidate] = values
        verdict = payload.get("verdict")
        if verdict is not None and verdict not in VERDICTS:
            raise ReviewError("invalid verdict")
        rationale = payload.get("rationale")
        if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 4000):
            raise ReviewError("rationale must be at most 4000 characters")
        return {
            "reviewer_id": reviewer, "unit_id": unit_id,
            "dimension_scores": normalized_scores, "verdict": verdict,
            "failure_reasons": normalized_failures,
            "both_unacceptable": verdict == "both_unacceptable" if verdict is not None else None,
            "rationale": rationale or "",
        }

    def _complete_ballot(self, value: dict[str, Any]) -> dict[str, Any]:
        for dimension, pair in value["dimension_scores"].items():
            if pair["candidate_a"] is None or pair["candidate_b"] is None:
                raise ReviewError(f"missing score: {dimension}")
        if value["verdict"] is None:
            raise ReviewError("verdict is required")
        rationale = value["rationale"].strip()
        if value["verdict"] == "both_unacceptable" and (
            not value["failure_reasons"]["candidate_a"] or not value["failure_reasons"]["candidate_b"]
        ):
            raise ReviewError("both unacceptable requires failure reasons for A and B")
        blank = self.blank_by_id[value["unit_id"]]
        return {
            "schema_version": 2,
            "ballot_id": blank["ballot_id"],
            "suite_id": blank["suite_id"],
            "rubric_id": blank["rubric_id"],
            "unit_id": value["unit_id"],
            "reviewer_id": value["reviewer_id"],
            "presentation": blank["presentation"],
            "candidate_a_output_id": blank["candidate_a_output_id"],
            "candidate_b_output_id": blank["candidate_b_output_id"],
            "dimension_scores": value["dimension_scores"],
            "verdict": value["verdict"],
            "failure_reasons": value["failure_reasons"],
            "both_unacceptable": value["verdict"] == "both_unacceptable",
            "rationale": rationale,
            "submitted_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "revealed": False,
        }


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "CHAT02EReviewer/1"

    @property
    def app(self) -> "ReviewServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        if self.app.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        route = urlsplit(self.path)
        try:
            if route.path in STATIC_FILES:
                filename, content_type = STATIC_FILES[route.path]
                self._bytes((STATIC_ROOT / filename).read_bytes(), content_type)
                return
            if route.path == "/api/health":
                self._json({"status": "ok", "contract_id": self.app.store.contract["contract_id"]})
                return
            if route.path == "/api/bootstrap":
                reviewer = parse_qs(route.query).get("reviewer_id", [""])[0]
                self._json(self.app.store.bootstrap(reviewer))
                return
            if route.path == "/api/export":
                reviewer = parse_qs(route.query).get("reviewer_id", [""])[0]
                body = self.app.store.export(reviewer)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="chat02e-{reviewer}-ballots.jsonl"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            raise ReviewError("not found", HTTPStatus.NOT_FOUND)
        except ReviewError as error:
            self._json({"status": "error", "error": str(error)}, error.status)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._json({"status": "error", "error": type(error).__name__}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        route = urlsplit(self.path)
        try:
            self._verify_origin()
            if route.path not in ("/api/draft", "/api/submit"):
                raise ReviewError("not found", HTTPStatus.NOT_FOUND)
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ReviewError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ReviewError("request body must be an object")
            result = self.app.store.save_draft(payload) if route.path == "/api/draft" else self.app.store.submit(payload)
            self._json(result)
        except ReviewError as error:
            self._json({"status": "error", "error": str(error)}, error.status)
        except (ValueError, json.JSONDecodeError):
            self._json({"status": "error", "error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)

    def _verify_origin(self) -> None:
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.app.server_port}", f"http://localhost:{self.app.server_port}"}
        if origin is not None and origin not in allowed:
            raise ReviewError("origin rejected", HTTPStatus.FORBIDDEN)

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", status)

    def _bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: ReviewStore, *, verbose: bool = False) -> None:
        if address[0] != "127.0.0.1":
            raise ValueError("review server must bind to 127.0.0.1")
        self.store = store
        self.verbose = verbose
        super().__init__(address, ReviewHandler)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CHAT-02E public-only local blind reviewer")
    parser.add_argument("--port", type=int, default=18120)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not _port_available(args.port):
        print(json.dumps({"status": "failed", "error": "port unavailable"}, ensure_ascii=False))
        return 2
    try:
        store = ReviewStore(args.contract)
        server = ReviewServer(("127.0.0.1", args.port), store, verbose=args.verbose)
    except (OSError, ValueError, ReviewError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    url = f"http://127.0.0.1:{args.port}/"
    print(json.dumps({"status": "ready", "url": url, "public_only": True}, ensure_ascii=False), flush=True)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
