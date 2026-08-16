from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = ROOT / "eval" / "cross_model_internal_40"
INPUT_PATH = EXPERIMENT_ROOT / "candidate_inputs_v1.jsonl"
LOCAL_PATH = EXPERIMENT_ROOT / "local_baiweixi_seed42.jsonl"
GPT_PATH = EXPERIMENT_ROOT / "gpt5_6_sol_internal.jsonl"
OUTPUT_PATH = EXPERIMENT_ROOT / "blind_review.html"
MANIFEST_PATH = EXPERIMENT_ROOT / "blind_review_manifest_v1.json"
BLIND_SALT = "baiweixi-internal40-blind-review-delivery-v2-20260811"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _rank(case_id: str, purpose: str) -> str:
    return _sha256_bytes(f"{BLIND_SALT}|{purpose}|{case_id}".encode("utf-8"))


def _build_cases() -> list[dict[str, object]]:
    inputs = _read_jsonl(INPUT_PATH)
    local = {str(row["case_id"]): row for row in _read_jsonl(LOCAL_PATH)}
    gpt = {str(row["case_id"]): row for row in _read_jsonl(GPT_PATH)}
    expected = {str(row["case_id"]) for row in inputs}
    if len(inputs) != 40 or set(local) != expected or set(gpt) != expected:
        raise RuntimeError("blind review inputs must cover the same 40 cases")

    values: list[dict[str, object]] = []
    for item in inputs:
        case_id = str(item["case_id"])
        swap = int(_rank(case_id, "side")[-1], 16) % 2 == 1
        candidates = [
            {
                "candidate_id": "local_baiweixi",
                "model": "本地白未晞",
                "response": str(local[case_id]["response"]),
            },
            {
                "candidate_id": "gpt_5_6_sol",
                "model": "GPT-5.6 Sol",
                "response": str(gpt[case_id]["response"]),
            },
        ]
        if swap:
            candidates.reverse()
        values.append(
            {
                "case_id": case_id,
                "source_ordinal": int(item["ordinal"]),
                "question": str(item["user_text"]),
                "a": candidates[0],
                "b": candidates[1],
                "order_rank": _rank(case_id, "order"),
            }
        )
    values.sort(key=lambda value: str(value["order_rank"]))
    for index, value in enumerate(values, 1):
        value["review_ordinal"] = index
        del value["order_rank"]
    return values


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>白未晞对话双盲审核</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #66717d;
      --line: #d9dee5;
      --surface: #ffffff;
      --page: #f3f5f7;
      --blue: #1769aa;
      --blue-soft: #edf5fb;
      --amber: #a75d00;
      --amber-soft: #fff5e7;
      --green: #1f7a4d;
      --green-soft: #eaf6ef;
      --danger: #a53b32;
      --focus: #111827;
      --radius: 6px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.65;
      letter-spacing: 0;
    }
    button, textarea { font: inherit; letter-spacing: 0; }
    button { cursor: pointer; }
    button:focus-visible, textarea:focus-visible {
      outline: 3px solid rgba(17, 24, 39, .24);
      outline-offset: 2px;
    }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .97);
    }
    .topbar-inner {
      width: min(1180px, calc(100% - 32px));
      min-height: 72px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(240px, 1fr) minmax(260px, 420px) auto;
      align-items: center;
      gap: 24px;
    }
    h1 { margin: 0; font-size: 19px; font-weight: 700; }
    .status-line { color: var(--muted); font-size: 13px; }
    .progress-track {
      width: 100%; height: 8px; overflow: hidden;
      border: 1px solid #cfd6dc; background: #e8ecef; border-radius: 4px;
    }
    .progress-fill { width: 0; height: 100%; background: var(--green); transition: width .18s ease; }
    .top-actions { display: flex; gap: 8px; justify-content: flex-end; }
    .button {
      min-height: 38px; padding: 7px 14px;
      border: 1px solid #b9c1c9; border-radius: var(--radius);
      background: var(--surface); color: var(--ink); font-weight: 600;
    }
    .button:hover { background: #f5f7f9; }
    .button:disabled { cursor: not-allowed; opacity: .45; }
    .button.primary { border-color: var(--green); background: var(--green); color: #fff; }
    .button.primary:hover { background: #17613d; }
    .workspace { width: min(1180px, calc(100% - 32px)); margin: 26px auto 48px; }
    .question-band {
      padding: 0 0 20px; border-bottom: 1px solid var(--line);
    }
    .eyebrow { margin: 0 0 8px; color: var(--muted); font-size: 13px; font-weight: 700; }
    .question { margin: 0; font-size: 21px; line-height: 1.55; font-weight: 650; }
    .answers {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px; margin: 20px 0;
    }
    .answer {
      min-height: 230px; padding: 20px;
      border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface);
    }
    .answer-a { border-top: 4px solid var(--blue); }
    .answer-b { border-top: 4px solid var(--amber); }
    .answer-label { margin-bottom: 12px; font-size: 16px; font-weight: 800; }
    .answer-a .answer-label { color: var(--blue); }
    .answer-b .answer-label { color: var(--amber); }
    .answer-text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
    .decision-strip {
      display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px; margin: 0 0 18px;
    }
    .decision {
      min-height: 46px; padding: 8px 10px;
      border: 1px solid #bdc5cd; border-radius: var(--radius);
      background: var(--surface); color: var(--ink); font-weight: 700;
    }
    .decision:hover { background: #f6f8fa; }
    .decision.selected[data-choice="a"] { border-color: var(--blue); background: var(--blue-soft); color: var(--blue); }
    .decision.selected[data-choice="b"] { border-color: var(--amber); background: var(--amber-soft); color: var(--amber); }
    .decision.selected[data-choice="tie"] { border-color: var(--green); background: var(--green-soft); color: var(--green); }
    .decision.selected[data-choice="both_fail"] { border-color: var(--danger); background: #fff0ef; color: var(--danger); }
    .notes-label { display: block; margin-bottom: 7px; color: var(--muted); font-size: 13px; font-weight: 700; }
    textarea {
      width: 100%; min-height: 88px; resize: vertical;
      padding: 11px 12px; border: 1px solid #bdc5cd; border-radius: var(--radius);
      background: var(--surface); color: var(--ink);
    }
    .navigation {
      display: grid; grid-template-columns: auto 1fr auto;
      align-items: center; gap: 16px; margin-top: 18px;
    }
    .case-grid {
      display: grid; grid-template-columns: repeat(10, 32px);
      justify-content: center; gap: 5px;
    }
    .case-dot {
      width: 32px; height: 32px; padding: 0;
      border: 1px solid #c7ced5; border-radius: 4px; background: #fff; color: #4d5863;
      font-size: 12px; font-weight: 700;
    }
    .case-dot.reviewed { border-color: var(--green); background: var(--green-soft); color: var(--green); }
    .case-dot.current { outline: 2px solid var(--focus); outline-offset: 1px; }
    .reveal-view { display: none; }
    .reveal-view.active { display: block; }
    .review-view.hidden { display: none; }
    .summary-band { padding: 6px 0 22px; border-bottom: 1px solid var(--line); }
    .summary-title { margin: 0 0 8px; font-size: 24px; }
    .summary-grid {
      display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px; margin: 22px 0;
    }
    .metric { padding: 16px 0; border-bottom: 3px solid var(--line); }
    .metric-value { display: block; font-size: 28px; line-height: 1.2; font-weight: 800; }
    .metric-label { color: var(--muted); font-size: 13px; }
    .reveal-table { width: 100%; border-collapse: collapse; background: var(--surface); }
    .reveal-table th, .reveal-table td { padding: 10px 12px; border: 1px solid var(--line); text-align: left; vertical-align: top; }
    .reveal-table th { background: #eef1f4; font-size: 13px; }
    .reveal-table td:nth-child(1) { width: 56px; }
    .reveal-table td:nth-child(3), .reveal-table td:nth-child(4) { width: 150px; }
    dialog { width: min(460px, calc(100% - 32px)); padding: 0; border: 1px solid var(--line); border-radius: var(--radius); }
    dialog::backdrop { background: rgba(17, 24, 39, .46); }
    .dialog-body { padding: 22px; }
    .dialog-body h2 { margin: 0 0 8px; font-size: 19px; }
    .dialog-body p { margin: 0; color: var(--muted); }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 22px; border-top: 1px solid var(--line); background: #f6f7f8; }
    @media (max-width: 820px) {
      .topbar-inner { grid-template-columns: 1fr auto; gap: 10px 14px; padding: 10px 0; }
      .progress-wrap { grid-column: 1 / -1; grid-row: 2; }
      .answers { grid-template-columns: 1fr; }
      .answer { min-height: 0; }
      .decision-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .navigation { grid-template-columns: repeat(2, 1fr); }
      .case-grid { grid-column: 1 / -1; grid-row: 2; grid-template-columns: repeat(8, 32px); }
      .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .table-scroll { overflow-x: auto; }
      .reveal-table { min-width: 760px; }
    }
    @media (max-width: 520px) {
      .topbar-inner, .workspace { width: min(100% - 20px, 1180px); }
      .topbar-inner { grid-template-columns: 1fr; }
      .top-actions { justify-content: stretch; }
      .top-actions .button { flex: 1; }
      .progress-wrap { grid-column: 1; grid-row: auto; }
      .question { font-size: 18px; }
      .answer { padding: 16px; }
      .decision-strip { grid-template-columns: 1fr; }
      .case-grid { grid-template-columns: repeat(5, 32px); }
      .summary-grid { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div>
        <h1>白未晞对话双盲审核</h1>
        <div class="status-line" id="statusLine">已审核 0 / 40</div>
      </div>
      <div class="progress-wrap"><div class="progress-track"><div class="progress-fill" id="progressFill"></div></div></div>
      <div class="top-actions">
        <button class="button" id="nextUnreviewed" type="button">跳到未审</button>
        <button class="button primary" id="revealButton" type="button" disabled>完成并揭盲</button>
      </div>
    </div>
  </header>

  <main class="workspace">
    <section class="review-view" id="reviewView">
      <div class="question-band">
        <p class="eyebrow" id="caseIndex">第 1 题 / 40</p>
        <p class="question" id="question"></p>
      </div>
      <div class="answers">
        <article class="answer answer-a">
          <div class="answer-label">候选 A</div>
          <p class="answer-text" id="answerA"></p>
        </article>
        <article class="answer answer-b">
          <div class="answer-label">候选 B</div>
          <p class="answer-text" id="answerB"></p>
        </article>
      </div>
      <div class="decision-strip" id="decisionStrip">
        <button class="decision" data-choice="a" type="button">A 更好</button>
        <button class="decision" data-choice="b" type="button">B 更好</button>
        <button class="decision" data-choice="tie" type="button">表现相当</button>
        <button class="decision" data-choice="both_fail" type="button">都不通过</button>
      </div>
      <label class="notes-label" for="notes">审核备注（可选）</label>
      <textarea id="notes" maxlength="1000"></textarea>
      <nav class="navigation" aria-label="题目导航">
        <button class="button" id="previousButton" type="button">上一题</button>
        <div class="case-grid" id="caseGrid"></div>
        <button class="button" id="nextButton" type="button">下一题</button>
      </nav>
    </section>

    <section class="reveal-view" id="revealView">
      <div class="summary-band">
        <p class="eyebrow">审核完成</p>
        <h2 class="summary-title">双盲结果</h2>
        <div class="top-actions" style="justify-content:flex-start">
          <button class="button primary" id="exportButton" type="button">导出审核结果</button>
        </div>
      </div>
      <div class="summary-grid" id="summaryGrid"></div>
      <div class="table-scroll"><table class="reveal-table">
        <thead><tr><th>#</th><th>玩家问题</th><th>候选 A</th><th>候选 B</th><th>选择</th><th>备注</th></tr></thead>
        <tbody id="revealRows"></tbody>
      </table></div>
    </section>
  </main>

  <dialog id="revealDialog">
    <div class="dialog-body">
      <h2>确认揭盲</h2>
      <p>揭盲后将显示候选身份和汇总结果，本轮审核不能继续保持盲态。</p>
    </div>
    <div class="dialog-actions">
      <button class="button" id="cancelReveal" type="button">取消</button>
      <button class="button primary" id="confirmReveal" type="button">确认揭盲</button>
    </div>
  </dialog>

  <script>
    const PAGE_ID = __PAGE_ID__;
    const CASES = __DATA__;
    const STORAGE_KEY = `aipeople-blind-review:${PAGE_ID}`;
    const choiceLabels = {a: "A 更好", b: "B 更好", tie: "表现相当", both_fail: "都不通过"};
    let currentIndex = 0;
    let state = loadState();

    function loadState() {
      try {
        const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
        if (parsed && parsed.decisions && typeof parsed.decisions === "object") return parsed;
      } catch (_) {}
      return {decisions: {}, revealed: false};
    }
    function saveState() { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    function reviewedCount() { return CASES.filter(item => state.decisions[item.case_id]?.choice).length; }
    function decisionFor(item) { return state.decisions[item.case_id] || {choice: null, notes: ""}; }

    function render() {
      const item = CASES[currentIndex];
      const decision = decisionFor(item);
      document.getElementById("caseIndex").textContent = `第 ${currentIndex + 1} 题 / ${CASES.length}`;
      document.getElementById("question").textContent = item.question;
      document.getElementById("answerA").textContent = item.a.response;
      document.getElementById("answerB").textContent = item.b.response;
      document.getElementById("notes").value = decision.notes || "";
      document.querySelectorAll(".decision").forEach(button => {
        button.classList.toggle("selected", button.dataset.choice === decision.choice);
      });
      document.getElementById("previousButton").disabled = currentIndex === 0;
      document.getElementById("nextButton").disabled = currentIndex === CASES.length - 1;
      renderProgress();
    }

    function renderProgress() {
      const count = reviewedCount();
      document.getElementById("statusLine").textContent = `已审核 ${count} / ${CASES.length}`;
      document.getElementById("progressFill").style.width = `${(count / CASES.length) * 100}%`;
      document.getElementById("revealButton").disabled = count !== CASES.length;
      document.querySelectorAll(".case-dot").forEach((button, index) => {
        button.classList.toggle("reviewed", Boolean(decisionFor(CASES[index]).choice));
        button.classList.toggle("current", index === currentIndex);
      });
    }

    function buildCaseGrid() {
      const grid = document.getElementById("caseGrid");
      CASES.forEach((item, index) => {
        const button = document.createElement("button");
        button.className = "case-dot";
        button.type = "button";
        button.textContent = String(index + 1);
        button.setAttribute("aria-label", `第 ${index + 1} 题`);
        button.addEventListener("click", () => { currentIndex = index; render(); window.scrollTo({top: 0, behavior: "smooth"}); });
        grid.appendChild(button);
      });
    }

    function choose(choice) {
      const item = CASES[currentIndex];
      const previous = decisionFor(item);
      state.decisions[item.case_id] = {choice, notes: previous.notes || "", reviewed_at: new Date().toISOString()};
      saveState();
      render();
      if (currentIndex < CASES.length - 1) {
        setTimeout(() => { currentIndex += 1; render(); window.scrollTo({top: 0, behavior: "smooth"}); }, 180);
      }
    }

    function goToUnreviewed() {
      const index = CASES.findIndex(item => !decisionFor(item).choice);
      if (index >= 0) { currentIndex = index; render(); window.scrollTo({top: 0, behavior: "smooth"}); }
    }

    function resolvedWinner(item, choice) {
      if (choice === "a") return item.a.candidate_id;
      if (choice === "b") return item.b.candidate_id;
      return choice;
    }

    function reveal() {
      state.revealed = true;
      state.revealed_at = new Date().toISOString();
      saveState();
      const counts = {local_baiweixi: 0, gpt_5_6_sol: 0, tie: 0, both_fail: 0};
      CASES.forEach(item => counts[resolvedWinner(item, decisionFor(item).choice)] += 1);
      const metrics = [
        [counts.local_baiweixi, "本地白未晞胜"],
        [counts.gpt_5_6_sol, "GPT-5.6 Sol 胜"],
        [counts.tie, "表现相当"],
        [counts.both_fail, "都不通过"]
      ];
      document.getElementById("summaryGrid").innerHTML = metrics.map(([value, label]) =>
        `<div class="metric"><span class="metric-value">${value}</span><span class="metric-label">${label}</span></div>`
      ).join("");
      const rows = document.getElementById("revealRows");
      rows.innerHTML = "";
      CASES.forEach((item, index) => {
        const decision = decisionFor(item);
        const row = document.createElement("tr");
        [String(index + 1), item.question, item.a.model, item.b.model, choiceLabels[decision.choice], decision.notes || ""].forEach(value => {
          const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
        });
        rows.appendChild(row);
      });
      document.getElementById("reviewView").classList.add("hidden");
      document.getElementById("revealView").classList.add("active");
      document.getElementById("revealButton").style.display = "none";
      document.getElementById("nextUnreviewed").style.display = "none";
      window.scrollTo({top: 0});
    }

    function exportResults() {
      const payload = {
        schema_version: 1,
        page_id: PAGE_ID,
        completed_at: state.revealed_at,
        decisions: CASES.map(item => {
          const decision = decisionFor(item);
          return {
            review_ordinal: item.review_ordinal,
            case_id: item.case_id,
            question: item.question,
            candidate_a: item.a.model,
            candidate_b: item.b.model,
            choice: decision.choice,
            resolved_winner: resolvedWinner(item, decision.choice),
            notes: decision.notes || "",
            reviewed_at: decision.reviewed_at
          };
        })
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `baiweixi_blind_review_${PAGE_ID.slice(0, 12)}.json`; anchor.click();
      URL.revokeObjectURL(url);
    }

    buildCaseGrid();
    document.querySelectorAll(".decision").forEach(button => button.addEventListener("click", () => choose(button.dataset.choice)));
    document.getElementById("notes").addEventListener("input", event => {
      const item = CASES[currentIndex]; const previous = decisionFor(item);
      state.decisions[item.case_id] = {...previous, notes: event.target.value}; saveState();
    });
    document.getElementById("previousButton").addEventListener("click", () => { if (currentIndex > 0) { currentIndex -= 1; render(); } });
    document.getElementById("nextButton").addEventListener("click", () => { if (currentIndex < CASES.length - 1) { currentIndex += 1; render(); } });
    document.getElementById("nextUnreviewed").addEventListener("click", goToUnreviewed);
    const dialog = document.getElementById("revealDialog");
    document.getElementById("revealButton").addEventListener("click", () => dialog.showModal());
    document.getElementById("cancelReveal").addEventListener("click", () => dialog.close());
    document.getElementById("confirmReveal").addEventListener("click", () => { dialog.close(); reveal(); });
    document.getElementById("exportButton").addEventListener("click", exportResults);
    if (state.revealed && reviewedCount() === CASES.length) reveal(); else render();
  </script>
</body>
</html>
'''


def main() -> int:
    cases = _build_cases()
    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    page_id = _sha256_bytes(canonical)
    html = HTML_TEMPLATE.replace("__PAGE_ID__", json.dumps(page_id)).replace(
        "__DATA__", json.dumps(cases, ensure_ascii=False, separators=(",", ":"))
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "page_id": page_id,
        "status": "ready",
        "review_type": "double_blind_pairwise",
        "case_count": len(cases),
        "question_order": "sha256-ranked with frozen blind salt",
        "candidate_side_assignment": "independent sha256 parity per case",
        "identities_hidden_until_reveal": True,
        "storage": "browser localStorage scoped by page_id",
        "output_path": OUTPUT_PATH.relative_to(ROOT).as_posix(),
        "output_sha256": _sha256(OUTPUT_PATH),
        "source_hashes": {
            "candidate_inputs": _sha256(INPUT_PATH),
            "local_responses": _sha256(LOCAL_PATH),
            "gpt_responses": _sha256(GPT_PATH),
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
