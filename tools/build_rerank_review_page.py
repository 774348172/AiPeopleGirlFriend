# -*- coding: utf-8 -*-
"""reranker 训练数据复核页生成（2026-08-11，SELECT-01 配套）。

从 MEMORY_RERANK 生成的 sqlite 候选提取 query + 候选标签，渲染成
可勾选复核页（复用 REPLY 复核页的勾选/导出/localStorage 机制）。

判定单位：整条样本（一条 = 一个场景 query + 全部候选标签）。
判定标准（SELECT-01 标签语义）：
- positive：有助于理解并自然回应当前对话（背景性相关也算）
- hard_negative：语义相关/同主题，但此刻不应激活（时间、事件或关系不符）
- easy_negative：无关记忆，明显不应激活
人工复核重点：positive 是否该召回、hard_negative 是否确实相似但不应激活、
score 是否合理（positive>=0.7 / hard 0.3-0.69 / easy<0.3）。

用法:
  python tools/build_rerank_review_page.py --sqlite 训练数据/_rerank40.sqlite \
      --out 训练数据/_rerank40_review_baiweixi.html
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LABEL_COLOR = {
    "positive": "#16a34a",
    "hard_negative": "#d97706",
    "easy_negative": "#64748b",
}
LABEL_CN = {
    "positive": "positive（应激活）",
    "hard_negative": "hard_negative（相似但不应激活）",
    "easy_negative": "easy_negative（无关）",
}


def load_rerank_rows(sqlite_path: Path) -> list[dict]:
    con = sqlite3.connect(str(sqlite_path))
    rows = []
    try:
        for (payload,) in con.execute(
            "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
        ):
            p = json.loads(payload)
            if p.get("task_type") != "rerank_memory":
                continue
            tgt = p.get("target") or {}
            inp = p.get("input") or {}
            scenario = inp.get("scenario") or {}
            candidates = inp.get("candidates") or []
            cand_text = {c["memory_id"]: c.get("selector_text", "") for c in candidates}
            labels = []
            for l in tgt.get("labels", []):
                mid = l.get("memory_id", "")
                labels.append(
                    {
                        "memory_id": mid,
                        "label": l.get("label", "?"),
                        "label_cn": LABEL_CN.get(l.get("label", "?"), l.get("label", "?")),
                        "color": LABEL_COLOR.get(l.get("label", "?"), "#94a3b8"),
                        "score": l.get("relevance_score"),
                        "should_recall": l.get("should_recall"),
                        "evidence": l.get("label_evidence", ""),
                        "text": cand_text.get(mid, ""),
                    }
                )
            q = tgt.get("query") or {}
            rows.append(
                {
                    "scene": scenario.get("scene", ""),
                    "working_state": scenario.get("working_state", ""),
                    "current_user_message": q.get("current_user_message", ""),
                    "recent_dialogue": q.get("recent_dialogue", []),
                    "labels": labels,
                    "sample_id": p.get("sample_id", ""),
                }
            )
    finally:
        con.close()
    return rows


def build_html(rows: list[dict]) -> str:
    cards_js = json.dumps(rows, ensure_ascii=False)
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>白未晞 Reranker 标签复核表（__N__ 条）</title>
<style>
  body { font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }
  header { background: #0f172a; color: #fff; padding: 14px 24px; position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 18px; }
  #progress { margin-top: 6px; font-size: 13px; color: #94a3b8; }
  #tips { margin-top: 4px; font-size: 12px; color: #cbd5e1; }
  .toolbar { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .btn { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .btn.primary { background: #3b82f6; color: #fff; }
  .btn.gray { background: #334155; color: #fff; }
  .btn.green { background: #16a34a; color: #fff; }
  main { max-width: 900px; margin: 20px auto; padding: 0 16px; }
  .card { background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); border-left: 4px solid #cbd5e1; }
  .card.pass { border-left-color: #22c55e; }
  .card.fail { border-left-color: #ef4444; }
  .card h2 { margin: 0 0 8px; font-size: 15px; }
  .meta { font-size: 12px; color: #64748b; margin-bottom: 10px; line-height: 1.7; }
  .querybox { background: #eff6ff; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px; }
  .querybox .qlabel { font-weight: 600; color: #1d4ed8; }
  .lab { margin: 8px 0; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0; font-size: 13px; }
  .lab .ltag { display: inline-block; padding: 2px 10px; border-radius: 999px; color: #fff; font-size: 12px; margin-right: 8px; }
  .lab .lmeta { color: #64748b; font-size: 12px; margin: 4px 0; }
  .lab .ltext { color: #334155; margin: 4px 0; }
  .lab .levid { color: #78716c; font-size: 12px; font-style: italic; margin-top: 4px; }
  .verdict { margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .vbtn { padding: 6px 18px; border: 2px solid #cbd5e1; border-radius: 8px; cursor: pointer; font-size: 14px; background: #fff; }
  .vbtn.on-pass { border-color: #22c55e; background: #dcfce7; color: #166534; }
  .vbtn.on-fail { border-color: #ef4444; background: #fee2e2; color: #991b1b; }
  .note { flex: 1; min-width: 180px; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; }
  .sample { font-size: 11px; color: #94a3b8; margin-top: 8px; }
</style>
</head>
<body>
<header>
  <h1>白未晞 Reranker 标签复核表（__N__ 条）</h1>
  <div id="progress">已判定 0 / __N__</div>
  <div id="tips">判定标准（SELECT-01）：positive=应激活（背景相关即可，score>=0.7）；hard_negative=相似但此刻不应激活（时间/事件/关系不符，0.3-0.69）；easy_negative=无关（<0.3）。重点看：positive 是否该召回、hard 是否确实相似、score 是否合理。</div>
  <div class="toolbar">
    <button class="btn primary" id="nextBtn">下一个未判定 ↓</button>
    <button class="btn primary" id="exportBtn">导出 JSON</button>
    <button class="btn green" id="copyBtn">复制结果</button>
    <button class="btn gray" id="resetBtn">重置</button>
  </div>
</header>
<main id="main"></main>
<script>
const KEY = "baiweixi_rerank_review_" + location.pathname.split("/").pop();
const ROWS = __CARDS__;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch(e) {}

function render() {
  const main = document.getElementById("main");
  main.innerHTML = "";
  let judged = 0;
  ROWS.forEach(r => {
    const v = state[r.no] || {};
    if (v.verdict) judged++;
    const card = document.createElement("div");
    card.className = "card " + (v.verdict === "pass" ? "pass" : v.verdict === "fail" ? "fail" : "");
    let recent = "";
    if (r.recent_dialogue && r.recent_dialogue.length) {
      recent = '<div class="qlabel">最近对话：</div>' + r.recent_dialogue.map(d => '<div>  ' + d + '</div>').join("");
    }
    const labels = r.labels.map(l =>
      '<div class="lab">' +
        '<span class="ltag" style="background:' + l.color + '">' + l.label_cn + '</span>' +
        '<span>score=' + l.score + ' ｜ should_recall=' + l.should_recall + ' ｜ ' + l.memory_id + '</span>' +
        '<div class="ltext">记忆：' + l.text + '</div>' +
        '<div class="levid">理由：' + l.evidence + '</div>' +
      '</div>').join("");
    card.innerHTML =
      '<h2>#' + r.no + ' 场景：' + r.scene + '</h2>' +
      '<div class="querybox">' +
        '<div><span class="qlabel">工作状态：</span>' + r.working_state + '</div>' +
        '<div><span class="qlabel">当前消息：</span>' + (r.current_user_message || "（无）") + '</div>' +
        recent +
      '</div>' +
      labels +
      '<div class="verdict">' +
        '<button class="vbtn ' + (v.verdict === "pass" ? "on-pass" : "") + '" data-no="' + r.no + '" data-v="pass">✓ 通过</button>' +
        '<button class="vbtn ' + (v.verdict === "fail" ? "on-fail" : "") + '" data-no="' + r.no + '" data-v="fail">✗ 不通过</button>' +
        '<input class="note" data-no="' + r.no + '" placeholder="备注（可选）" value="' + (v.note || "").replace(/"/g, "&quot;") + '">' +
      '</div>' +
      '<div class="sample">' + r.sample_id + '</div>';
    main.appendChild(card);
  });
  document.getElementById("progress").textContent = "已判定 " + judged + " / " + ROWS.length;
}

document.addEventListener("click", e => {
  const btn = e.target.closest(".vbtn");
  if (btn) {
    const no = btn.dataset.no;
    state[no] = state[no] || {};
    state[no].verdict = btn.dataset.v;
    localStorage.setItem(KEY, JSON.stringify(state));
    render();
  }
  if (e.target.id === "nextBtn") {
    const first = ROWS.find(r => !(state[r.no] || {}).verdict);
    if (first) document.getElementById("main").children[first.no - 1].scrollIntoView({behavior: "smooth"});
  }
  if (e.target.id === "resetBtn") { state = {}; localStorage.removeItem(KEY); render(); }
  if (e.target.id === "exportBtn") {
    const out = ROWS.map(r => ({no: r.no, sample: r.sample_id, task: "rerank_memory",
                                verdict: (state[r.no] || {}).verdict, note: (state[r.no] || {}).note || ""}));
    const blob = new Blob([JSON.stringify(out, null, 2)], {type: "application/json"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "review_result_baiweixi_rerank40.json"; a.click();
  }
  if (e.target.id === "copyBtn") {
    const NL = String.fromCharCode(10);
    const out = ROWS.map(r => r.no + "," + ((state[r.no] || {}).verdict || "?") + "," + ((state[r.no] || {}).note || "")).join(NL);
    navigator.clipboard.writeText(out).then(() => alert("已复制（编号,判定,备注）"));
  }
});

document.addEventListener("input", e => {
  if (e.target.classList.contains("note")) {
    const no = e.target.dataset.no;
    state[no] = state[no] || {};
    state[no].note = e.target.value;
    localStorage.setItem(KEY, JSON.stringify(state));
  }
});

render();
</script>
</body>
</html>"""
    html = html.replace("__N__", str(len(rows))).replace("__CARDS__", cards_js)
    return html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True, help="MEMORY_RERANK 生成结果的 sqlite")
    ap.add_argument("--out", required=True, help="输出 html 路径")
    args = ap.parse_args()

    rows = load_rerank_rows(Path(args.sqlite))
    if not rows:
        print("[error] sqlite 中无 rerank_memory 候选")
        return 1
    for i, r in enumerate(rows, 1):
        r["no"] = i
    Path(args.out).write_text(build_html(rows), encoding="utf-8")
    print(f"rerank 复核页已生成: {args.out}（{len(rows)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
