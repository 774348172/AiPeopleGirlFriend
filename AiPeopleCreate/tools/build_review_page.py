# -*- coding: utf-8 -*-
"""通用人工复核页生成器（自包含 HTML，判定存 localStorage 可导出）。

数据源（按 --stem 读取）:
  - 训练数据/<stem>.jsonl            (ShareGPT 对话, 行序与 metadata 对齐)
  - 训练数据/<stem>.metadata.jsonl   (task_type/scene/topic/evidence_state/...)
可选 --ledger：并入 G7 待审候选（full 类：protective/supportive/safety，
人工复核记录写入 ledger 后放行）。

多角色（2026-08-09）：--character 决定角色显示名、复核要点与 localStorage key；
默认 qinweixi 保持历史行为。

输出:
  - 训练数据/<stem>_review.html (双击打开; 通过/不通过 + 备注; 导出 JSON)

用法:
  python tools/build_review_page.py --stem qin_v4_20_final
  python tools/build_review_page.py --stem baiweixi_review30 --character baiweixi \
      --ledger 训练数据/baiweixi_review30.sqlite
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "训练数据"

TASK_CN = {
    "reply_casual": "日常闲聊",
    "reply_romance": "暧昧互动",
    "reply_identity": "身份问答",
    "reply_emotion": "情绪回应",
    "reply_protective": "秘密守护",
    "reply_supportive": "支持陪伴",
    "reply_correction": "记忆纠正",
    "reply_vague": "含糊回应",
    "reply_quiet_company": "安静陪伴",
    "reply_boundary": "关系边界",
    "reply_canon_qa": "正典问答",
    "reply_general": "通用能力",
    "reply_safety": "安全守护",
    "rerank_memory": "记忆标注",
}
TASK_COLOR = {
    "reply_casual": "#3b82f6",
    "reply_romance": "#ec4899",
    "reply_identity": "#8b5cf6",
    "reply_emotion": "#f59e0b",
    "reply_protective": "#ef4444",
    "reply_supportive": "#10b981",
    "reply_correction": "#8b5cf6",
    "reply_vague": "#06b6d4",
    "reply_quiet_company": "#14b8a6",
    "reply_boundary": "#f43f5e",
    "reply_canon_qa": "#f59e0b",
    "reply_general": "#64748b",
    "reply_safety": "#ef4444",
    "rerank_memory": "#a855f7",
}

EVIDENCE_CN = {
    "supported": "supported（有支撑）",
    "insufficient": "insufficient（支撑不足）",
    "false_premise": "false_premise（错误前提）",
    "conflicted": "conflicted（冲突）",
    "not_required": "not_required（不要求）",
}

CHARACTERS = {
    "qinweixi": {
        "display_name": "秦未晞",
        "tips": "复核要点：承接/严肃不调侃/不编经历/边界/口癖（哼喂啧）/秘密词（地堡裂缝异世界）/身份朗读",
    },
    "baiweixi": {
        "display_name": "白未晞",
        "tips": "复核要点：承接/严肃不调侃/不编经历/关系边界（救助-暂住-未确认恋爱）/口癖（喵哼喂啧应≈0）/秘密词（妖果上古传承深山上海）/妖力编造现实（天气温度位置）/人称混乱",
    },
}


def load_rows(stem: str, character: str, ledger: str | None = None) -> list[dict]:
    data_path = DATA_DIR / f"{stem}.jsonl"
    meta_path = DATA_DIR / f"{stem}.metadata.jsonl"
    data_lines = [
        json.loads(line)
        for line in data_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    meta_lines = [
        json.loads(line)
        for line in meta_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(data_lines) != len(meta_lines):
        raise SystemExit(f"[error] {stem}.jsonl {len(data_lines)} 行 ≠ metadata {len(meta_lines)} 行")
    rows = []
    for i, (data, meta) in enumerate(zip(data_lines, meta_lines), 1):
        dialogue = [
            {"from": m.get("from", ""), "value": m.get("value", "")}
            for m in data.get("conversations", [])
            if m.get("from") != "system"
        ]
        rows.append(
            {
                "no": i,
                "task": meta["task_type"],
                "task_cn": TASK_CN.get(meta["task_type"], meta["task_type"]),
                "color": TASK_COLOR.get(meta["task_type"], "#94a3b8"),
                "topic": meta.get("topic", ""),
                "scene": meta.get("scene", ""),
                "evidence": EVIDENCE_CN.get(meta.get("evidence_state", ""), meta.get("evidence_state", "")),
                "policy": meta.get("desired_policy", ""),
                "sample": meta["sample_id"],
                "dialogue": dialogue,
                "pending_g7": False,
            }
        )
    # 2026-08-09：并入 G7 待审候选（full 类未导出，人工复核记录写入 ledger 后放行）
    if ledger:
        import sqlite3

        full_tasks = ("reply_protective", "reply_supportive", "reply_safety")
        con = sqlite3.connect(str(ledger))
        try:
            for (payload,) in con.execute(
                "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
            ):
                p = json.loads(payload)
                if p.get("task_type") not in full_tasks:
                    continue
                target = p.get("target") or {}
                messages = target.get("messages") or []
                if not messages:
                    continue
                dialogue = [
                    {"from": "human" if m.get("role") == "human" else "gpt",
                     "value": m.get("content", "")}
                    for m in messages
                ]
                rows.append(
                    {
                        "no": len(rows) + 1,
                        "task": p.get("task_type", "?"),
                        "task_cn": TASK_CN.get(p.get("task_type", ""), p.get("task_type", "?")) + "（G7待审）",
                        "color": TASK_COLOR.get(p.get("task_type", ""), "#94a3b8"),
                        "topic": (p.get("input") or {}).get("topic", ""),
                        "scene": (p.get("input") or {}).get("scene", ""),
                        "evidence": "not_required（G7 人工审核）",
                        "policy": "",
                        "sample": p.get("sample_id", ""),
                        "dialogue": dialogue,
                        "pending_g7": True,
                    }
                )
        finally:
            con.close()
    return rows


def build_html(rows: list[dict], character: str) -> str:
    cards_js = json.dumps(rows, ensure_ascii=False)
    display_name = CHARACTERS[character]["display_name"]
    tips = CHARACTERS[character]["tips"]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{display_name} 数据人工复核表（{len(rows)} 条）</title>
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }}
  header {{ background: #0f172a; color: #fff; padding: 14px 24px; position: sticky; top: 0; z-index: 10; }}
  header h1 {{ margin: 0; font-size: 18px; }}
  #progress {{ margin-top: 6px; font-size: 13px; color: #94a3b8; }}
  #tips {{ margin-top: 4px; font-size: 12px; color: #cbd5e1; }}
  .toolbar {{ display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }}
  .btn {{ padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  .btn.primary {{ background: #3b82f6; color: #fff; }}
  .btn.gray {{ background: #334155; color: #fff; }}
  .btn.green {{ background: #16a34a; color: #fff; }}
  main {{ max-width: 860px; margin: 20px auto; padding: 0 16px; }}
  .card {{ background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); border-left: 4px solid #cbd5e1; }}
  .card.pass {{ border-left-color: #22c55e; }}
  .card.fail {{ border-left-color: #ef4444; }}
  .card h2 {{ margin: 0 0 8px; font-size: 16px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .tag {{ padding: 2px 8px; border-radius: 999px; color: #fff; font-size: 12px; }}
  .meta {{ font-size: 12px; color: #64748b; margin-bottom: 10px; }}
  .msg {{ margin: 6px 0; padding: 8px 12px; border-radius: 8px; font-size: 14px; line-height: 1.6; }}
  .msg.human {{ background: #eff6ff; }}
  .msg.gpt {{ background: #fefce8; }}
  .msg .who {{ font-weight: 600; margin-right: 6px; }}
  .verdict {{ margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .vbtn {{ padding: 6px 18px; border: 2px solid #cbd5e1; border-radius: 8px; cursor: pointer; font-size: 14px; background: #fff; }}
  .vbtn.on-pass {{ border-color: #22c55e; background: #dcfce7; color: #166534; }}
  .vbtn.on-fail {{ border-color: #ef4444; background: #fee2e2; color: #991b1b; }}
  .note {{ flex: 1; min-width: 180px; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; }}
  .sample {{ font-size: 11px; color: #94a3b8; margin-top: 8px; }}
</style>
</head>
<body>
<header>
  <h1>{display_name} 数据人工复核表（{len(rows)} 条）</h1>
  <div id="progress">已判定 0 / {len(rows)}</div>
  <div id="tips">{tips}</div>
  <div class="toolbar">
    <button class="btn primary" id="nextBtn">下一个未判定 ↓</button>
    <button class="btn primary" id="exportBtn">导出 JSON</button>
    <button class="btn green" id="copyBtn">复制结果</button>
    <button class="btn gray" id="resetBtn">重置</button>
  </div>
</header>
<main id="main"></main>
<script>
const KEY = "{character}_review_" + location.pathname.split("/").pop();
const ROWS = {cards_js};
let state = {{}};
try {{ state = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch(e) {{}}

function render() {{
  const main = document.getElementById("main");
  main.innerHTML = "";
  let judged = 0;
  ROWS.forEach(r => {{
    const v = state[r.no] || {{}};
    if (v.verdict) judged++;
    const card = document.createElement("div");
    card.className = "card " + (v.verdict === "pass" ? "pass" : v.verdict === "fail" ? "fail" : "");
    const msgs = r.dialogue.map(m =>
      `<div class="msg ${{m.from}}"><span class="who">${{m.from === "human" ? "玩家" : "{display_name}"}}</span>${{m.value}}</div>`
    ).join("");
    card.innerHTML = `
      <h2><span class="tag" style="background:${{r.color}}">${{r.task_cn}}</span>
          <span style="font-size:14px">${{r.topic}}</span></h2>
      <div class="meta">场景：${{r.scene}} ｜ evidence：${{r.evidence}} ｜ policy：${{r.policy}}</div>
      ${{msgs}}
      <div class="verdict">
        <button class="vbtn ${{v.verdict === "pass" ? "on-pass" : ""}}" data-no="${{r.no}}" data-v="pass">✓ 通过</button>
        <button class="vbtn ${{v.verdict === "fail" ? "on-fail" : ""}}" data-no="${{r.no}}" data-v="fail">✗ 不通过</button>
        <input class="note" data-no="${{r.no}}" placeholder="备注（可选）" value="${{(v.note || "").replace(/"/g, "&quot;")}}">
      </div>
      <div class="sample">${{r.sample}}${{r.pending_g7 ? " ｜ G7 待人工审核" : ""}}</div>`;
    main.appendChild(card);
  }});
  document.getElementById("progress").textContent = `已判定 ${{judged}} / ${{ROWS.length}}`;
}}

document.addEventListener("click", e => {{
  const btn = e.target.closest(".vbtn");
  if (btn) {{
    const no = btn.dataset.no;
    state[no] = state[no] || {{}};
    state[no].verdict = btn.dataset.v;
    localStorage.setItem(KEY, JSON.stringify(state));
    render();
  }}
  if (e.target.id === "nextBtn") {{
    const first = ROWS.find(r => !(state[r.no] || {{}}).verdict);
    if (first) document.getElementById("main").children[first.no - 1].scrollIntoView({{behavior: "smooth"}});
  }}
  if (e.target.id === "resetBtn") {{ state = {{}}; localStorage.removeItem(KEY); render(); }}
  if (e.target.id === "exportBtn") {{
    const out = ROWS.map(r => ({{no: r.no, sample: r.sample, task: r.task,
                                verdict: (state[r.no] || {{}}).verdict, note: (state[r.no] || {{}}).note || ""}}));
    const blob = new Blob([JSON.stringify(out, null, 2)], {{type: "application/json"}});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "review_result_{character}.json"; a.click();
  }}
  if (e.target.id === "copyBtn") {{
    const out = ROWS.map(r => `${{r.no}},${{(state[r.no] || {{}}).verdict || "?"}},${{(state[r.no] || {{}}).note || ""}}`).join("\\n");
    navigator.clipboard.writeText(out).then(() => alert("已复制（编号,判定,备注）"));
  }}
}});

document.addEventListener("input", e => {{
  if (e.target.classList.contains("note")) {{
    const no = e.target.dataset.no;
    state[no] = state[no] || {{}};
    state[no].note = e.target.value;
    localStorage.setItem(KEY, JSON.stringify(state));
  }}
}});

render();
</script>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True, help="数据 stem（训练数据/<stem>.jsonl）")
    ap.add_argument("--character", default="qinweixi",
                    help="角色 id（默认 qinweixi 历史行为；baiweixi 用白未晞）")
    ap.add_argument("--ledger", default=None,
                    help="ledger sqlite（可选）：并入 G7 待审候选（full 类）")
    args = ap.parse_args()
    if args.character not in CHARACTERS:
        raise SystemExit(f"[error] 未知角色: {args.character}（可选: {sorted(CHARACTERS)}）")
    rows = load_rows(args.stem, args.character, args.ledger)
    out = DATA_DIR / f"{args.stem}_review_{args.character}.html"
    out.write_text(build_html(rows, args.character), encoding="utf-8")
    print(f"复核页已生成: {out}（{len(rows)} 条，判定存 localStorage 可导出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
