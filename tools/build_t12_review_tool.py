# -*- coding: utf-8 -*-
"""T12 行为矩阵 119 条 → 本地交互复核工具 HTML。

数据源:
  - 训练数据/qin_v4_t12_matrix.jsonl          (完整对话, 与复核表 #1-119 同序)
  - 训练数据/qin_v4_t12_matrix.metadata.jsonl  (sample_id/task_type/scene/topic/...)
输出:
  - 设计文档/复核工具_T12_v1.html (自包含, 双击打开; 判定存 localStorage, 可导出 JSON)
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__import__("sys").argv[1] if len(__import__("sys").argv) > 1 else ROOT / "训练数据")
SRC_STEM = __import__("sys").argv[2] if len(__import__("sys").argv) > 2 else "qin_v4_t12_matrix"
OUT = ROOT / "设计文档" / (__import__("sys").argv[3] if len(__import__("sys").argv) > 3 else "复核工具_T12_v1.html")

TASK_CN = {
    "reply_supportive": "支持陪伴",
    "reply_correction": "记忆纠正",
    "reply_vague": "含糊回应",
    "reply_quiet_company": "安静陪伴",
    "reply_boundary": "关系边界",
    "reply_canon_qa": "正典问答",
}
TASK_COLOR = {
    "reply_supportive": "#3b82f6",
    "reply_correction": "#8b5cf6",
    "reply_vague": "#10b981",
    "reply_quiet_company": "#06b6d4",
    "reply_boundary": "#ec4899",
    "reply_canon_qa": "#f59e0b",
}

# 预筛标注（编号 → (级别, 说明)）——A=canon 冲突必改, B=行为问题建议改, C=编造细节待定夺
PRESCREEN = {
    17: ("B", "无上下文身份朗读「我是秦未晞」"),
    33: ("B", "「我今年22岁，身体倍儿棒」年龄插入别扭"),
    38: ("A", "「航天基地那边住过」与金陵邻居设定冲突"),
    41: ("B", "丢钱场景无关插入「我今年22岁」"),
    57: ("B", "语义错位：玩家说她叫错名字，她答自己名字"),
    60: ("A", "「从小在航天基地长大」与 0-5 岁邻居设定冲突"),
    82: ("B", "quiet_company 场景顶嘴呛人"),
    97: ("B", "「你是我女朋友吗」答非所问"),
    93: ("C", "玩家台词与池意图不完全对应，疑有前轮上下文"),
    102: ("A", "「爸搞轨道计算」——canon 轨道计算是妈妈"),
    109: ("A", "「爸搞轨道计算」——canon 轨道计算是妈妈"),
    112: ("A", "玩家台词「你那一年」泄漏失忆设定；回应与高中同校冲突"),
    114: ("A", "「航天基地长大+零下二十多度」冲突/编造"),
    116: ("A", "「爸搞轨道计算」——canon 轨道计算是妈妈"),
    5: ("C", "童年口径：老搬家 vs 0-5 邻居"),
    7: ("C", "童年口径：听爸妈吵架 vs 常年不在家"),
    9: ("C", "编造养猫经历（两次一致，canon 无）"),
    12: ("C", "编造搬家丢钱（安慰自述）"),
    19: ("C", "童年口径：老搬家 vs 0-5 邻居"),
    21: ("C", "童年口径：听爸妈吵架 vs 常年不在家"),
    23: ("C", "编造养猫经历（两次一致，canon 无）"),
    28: ("C", "编造家人住院（安慰自述）"),
    35: ("C", "童年口径：常年不在家 vs 0-5 邻居"),
}
LEVEL_CN = {"A": "A · 正典冲突", "B": "B · 行为问题", "C": "C · 细节待定"}


def load_rows() -> list[dict]:
    rows = []
    with open(DATA_DIR / f"{SRC_STEM}.jsonl", encoding="utf-8") as f:
        convs = [json.loads(l)["conversations"] for l in f]
    with open(DATA_DIR / f"{SRC_STEM}.metadata.jsonl", encoding="utf-8") as f:
        metas = [json.loads(l) for l in f]
    assert len(convs) == len(metas), (len(convs), len(metas))
    for i, (conv, meta) in enumerate(zip(convs, metas), start=1):
        sys_text = next((m["value"] for m in conv if m.get("from") == "system"), "")
        turns = [m for m in conv if m.get("from") in ("human", "gpt")]
        prescreen = PRESCREEN.get(i)
        rows.append({
            "idx": i,
            "task_type": meta["task_type"],
            "task_cn": TASK_CN.get(meta["task_type"], meta["task_type"]),
            "scene": meta.get("scene", ""),
            "topic": meta.get("topic", ""),
            "evidence_state": meta.get("evidence_state", ""),
            "desired_policy": meta.get("desired_policy", ""),
            "player": next((m["value"] for m in turns if m["from"] == "human"), ""),
            "reply": next((m["value"] for m in turns if m["from"] == "gpt"), ""),
            "turns": turns,
            "system": sys_text,
            "ps": {"level": prescreen[0], "note": prescreen[1]} if prescreen else None,
        })
    return rows


def build_html(rows: list[dict]) -> str:
    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T12 行为矩阵复核工具 v1</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #f3f4f6; color: #1f2937; padding: 16px; }}
  header {{ max-width: 1080px; margin: 0 auto 14px; background: #fff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); position: sticky; top: 8px; z-index: 10; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; }}
  .sub {{ font-size: 12px; color: #6b7280; margin-bottom: 10px; }}
  .stats {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; font-size: 13px; }}
  .stats .num {{ font-weight: 700; }}
  .bar {{ height: 6px; background: #e5e7eb; border-radius: 3px; overflow: hidden; margin-bottom: 10px; }}
  .bar > div {{ height: 100%; background: #22c55e; transition: width .2s; }}
  .tabs {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .tab {{ border: 1px solid #d1d5db; background: #fff; padding: 4px 10px; border-radius: 999px; font-size: 12px; cursor: pointer; }}
  .tab.active {{ background: #111827; color: #fff; border-color: #111827; }}
  .btn {{ border: none; border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer; font-weight: 600; }}
  .btn.primary {{ background: #111827; color: #fff; }}
  .btn:disabled {{ opacity: .4; cursor: not-allowed; }}
  main {{ max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06); border-left: 4px solid #d1d5db; }}
  .card.pass {{ border-left-color: #22c55e; }}
  .card.fail {{ border-left-color: #ef4444; }}
  .card.doubt {{ border-left-color: #f59e0b; }}
  .card.hidden {{ display: none; }}
  .row1 {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
  .no {{ font-weight: 800; font-size: 15px; }}
  .tag {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; color: #fff; }}
  .ps {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 700; }}
  .ps.A {{ background: #fee2e2; color: #b91c1c; }}
  .ps.B {{ background: #ffedd5; color: #c2410c; }}
  .ps.C {{ background: #f3f4f6; color: #6b7280; }}
  .psnote {{ font-size: 12px; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; padding: 6px 10px; border-radius: 8px; margin-bottom: 10px; }}
  .meta {{ font-size: 12px; color: #6b7280; margin-bottom: 10px; }}
  .bubble {{ border-radius: 10px; padding: 10px 14px; margin-bottom: 8px; font-size: 14px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }}
  .bubble.player {{ background: #eef2ff; border-top-left-radius: 2px; }}
  .bubble.gpt {{ background: #f0fdf4; border-top-right-radius: 2px; }}
  .bubble .who {{ font-size: 11px; color: #6b7280; display: block; margin-bottom: 3px; font-weight: 600; }}
  details.sys {{ margin-bottom: 10px; }}
  details.sys summary {{ font-size: 12px; color: #6b7280; cursor: pointer; }}
  details.sys pre {{ font-size: 11px; color: #374151; background: #f9fafb; padding: 10px; border-radius: 8px; margin-top: 6px; white-space: pre-wrap; max-height: 300px; overflow: auto; }}
  .actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 4px; }}
  .vbtn {{ border: 1.5px solid #d1d5db; background: #fff; padding: 5px 16px; border-radius: 8px; font-size: 13px; cursor: pointer; font-weight: 600; }}
  .vbtn.on-pass {{ background: #22c55e; border-color: #22c55e; color: #fff; }}
  .vbtn.on-fail {{ background: #ef4444; border-color: #ef4444; color: #fff; }}
  .vbtn.on-doubt {{ background: #f59e0b; border-color: #f59e0b; color: #fff; }}
  .note {{ flex: 1; min-width: 220px; border: 1.5px solid #d1d5db; border-radius: 8px; padding: 6px 10px; font-size: 13px; }}
  footer {{ max-width: 1080px; margin: 16px auto 0; text-align: center; font-size: 12px; color: #9ca3af; }}
  .flash {{ animation: flash 1s; }}
  @keyframes flash {{ 0% {{ box-shadow: 0 0 0 3px #22c55e; }} 100% {{ box-shadow: 0 0 0 0 transparent; }} }}
</style>
</head>
<body>
<header>
  <h1>T12 重生成 24 条复核工具 <span style="color:#6b7280;font-size:13px">v2 · {{N}} 条</span></h1>
  <div class="sub">判定后自动保存到本浏览器（localStorage），可随时刷新继续。复核要点：① 承接玩家核心信息 ② 严肃不调侃 ③ 不编共同经历 ④ 关系边界 ⑤ 口癖密度 ⑥ 秘密词泄漏 ⑦ 不朗读身份。</div>
  <div class="stats">
    <span>已判定 <span class="num" id="doneNum">0</span>/<span id="totalNum">119</span></span>
    <span style="color:#16a34a">通过 <span class="num" id="passNum">0</span></span>
    <span style="color:#dc2626">不通过 <span class="num" id="failNum">0</span></span>
    <span style="color:#d97706">存疑 <span class="num" id="doubtNum">0</span></span>
    <button class="btn primary" id="nextBtn" style="margin-left:auto">下一个未判定 ↓</button>
    <button class="btn primary" id="exportBtn">导出 JSON</button>
    <button class="btn primary" id="copyBtn">复制结果</button>
  </div>
  <div class="bar"><div id="bar" style="width:0%"></div></div>
  <div class="tabs" id="tabs"></div>
</header>
<main id="main"></main>
<footer>本工具为本地文件，数据不外传。导出 JSON 后交回即可。</footer>
<script>
const DATA = {data_json};
const KEY = "t12_review_v1";
const LEVEL_CN = {json.dumps(LEVEL_CN, ensure_ascii=False)};
const TASK_COLOR = {json.dumps(TASK_COLOR, ensure_ascii=False)};
let state = {{}};
try {{ state = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch(e) {{}}

function esc(s) {{
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function render() {{
  const main = document.getElementById("main");
  main.innerHTML = "";
  for (const r of DATA) {{
    const v = state[String(r.idx)] || {{}};
    const card = document.createElement("div");
    card.className = "card " + (v.verdict || "");
    card.dataset.idx = r.idx;
    const ps = r.ps ? `<span class="ps ${{r.ps.level}}">${{r.ps.level}}</span>` : "";
    let bubbles = "";
    for (const t of r.turns) {{
      const who = t.from === "human" ? "玩家" : "秦未晞";
      const cls = t.from === "human" ? "player" : "gpt";
      bubbles += `<div class="bubble ${{cls}}"><span class="who">${{who}}</span>${{esc(t.value)}}</div>`;
    }}
    card.innerHTML = `
      <div class="row1">
        <span class="no">#${{r.idx}}</span>
        <span class="tag" style="background:${{TASK_COLOR[r.task_type] || "#6b7280"}}">${{esc(r.task_cn)}}</span>
        ${{ps}}
        ${{r.ps ? `<span style="font-size:11px;color:#6b7280">${{esc(r.ps.note)}}</span>` : ""}}
      </div>
      <div class="meta">话题：${{esc(r.topic)}} ｜ 场景：${{esc(r.scene)}} ｜ ${{esc(r.evidence_state)}} / ${{esc(r.desired_policy)}}</div>
      ${{r.ps ? `<div class="psnote">⚠ 预筛：${{LEVEL_CN[r.ps.level]}} —— ${{esc(r.ps.note)}}</div>` : ""}}
      ${{bubbles}}
      <details class="sys"><summary>查看系统提示（锚/协议，默认不看）</summary><pre>${{esc(r.system)}}</pre></details>
      <div class="actions">
        <button class="vbtn ${{v.verdict==="pass" ? "on-pass" : ""}}" data-v="pass">✓ 通过</button>
        <button class="vbtn ${{v.verdict==="fail" ? "on-fail" : ""}}" data-v="fail">✗ 不通过</button>
        <button class="vbtn ${{v.verdict==="doubt" ? "on-doubt" : ""}}" data-v="doubt">? 存疑</button>
        <input class="note" placeholder="备注（可选）" value="${{esc(v.note || "")}}">
      </div>`;
    main.appendChild(card);
  }}
  refresh();
}}

function save() {{
  localStorage.setItem(KEY, JSON.stringify(state));
  refresh();
}}

function refresh() {{
  let pass=0, fail=0, doubt=0;
  for (const r of DATA) {{
    const v = state[String(r.idx)];
    if (v?.verdict === "pass") pass++;
    else if (v?.verdict === "fail") fail++;
    else if (v?.verdict === "doubt") doubt++;
  }}
  const done = pass+fail+doubt;
  document.getElementById("doneNum").textContent = done;
  document.getElementById("passNum").textContent = pass;
  document.getElementById("failNum").textContent = fail;
  document.getElementById("doubtNum").textContent = doubt;
  document.getElementById("bar").style.width = (done/DATA.length*100).toFixed(1) + "%";
  applyFilter();
}}

const FILTERS = [
  ["all", "全部"],
  ["pending", "未判定"],
  ["A", "A·正典冲突"],
  ["B", "B·行为问题"],
  ["C", "C·细节待定"],
  ["pass", "已通过"],
  ["fail", "不通过"],
  ["doubt", "存疑"],
];
let curFilter = "all";

function applyFilter() {{
  const tabs = document.getElementById("tabs");
  tabs.innerHTML = "";
  for (const [key, label] of FILTERS) {{
    const b = document.createElement("button");
    b.className = "tab" + (key === curFilter ? " active" : "");
    b.textContent = label;
    b.onclick = () => {{ curFilter = key; applyFilter(); }};
    tabs.appendChild(b);
  }}
  document.querySelectorAll(".card").forEach(card => {{
    const r = DATA.find(x => x.idx == card.dataset.idx);
    const v = state[String(r.idx)] || {{}};
    let show = true;
    if (curFilter === "pending") show = !v.verdict;
    else if (curFilter === "pass") show = v.verdict === "pass";
    else if (curFilter === "fail") show = v.verdict === "fail";
    else if (curFilter === "doubt") show = v.verdict === "doubt";
    else if (curFilter === "A" || curFilter === "B" || curFilter === "C") show = r.ps?.level === curFilter;
    card.classList.toggle("hidden", !show);
  }});
}}

document.getElementById("main").addEventListener("click", (e) => {{
  const btn = e.target.closest(".vbtn");
  if (!btn) return;
  const card = btn.closest(".card");
  const idx = card.dataset.idx;
  state[idx] = state[idx] || {{}};
  state[idx].verdict = btn.dataset.v;
  card.querySelectorAll(".vbtn").forEach(b => b.className = "vbtn");
  btn.classList.add("on-" + btn.dataset.v);
  card.className = "card " + btn.dataset.v;
  save();
}});

document.getElementById("main").addEventListener("input", (e) => {{
  if (!e.target.classList.contains("note")) return;
  const idx = e.target.closest(".card").dataset.idx;
  state[idx] = state[idx] || {{}};
  state[idx].note = e.target.value;
  localStorage.setItem(KEY, JSON.stringify(state));
}});

document.getElementById("nextBtn").onclick = () => {{
  const pending = document.querySelector(".card:not(.hidden)");
  for (const card of document.querySelectorAll(".card")) {{
    if (card.classList.contains("hidden")) continue;
    const r = DATA.find(x => x.idx == card.dataset.idx);
    if (!state[String(r.idx)]?.verdict) {{
      card.scrollIntoView({{behavior: "smooth", block: "start"}});
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1000);
      return;
    }}
  }}
  alert("全部已判定！");
}};

function exportJSON() {{
  const verdicts = {{}};
  for (const r of DATA) {{
    const v = state[String(r.idx)];
    verdicts[String(r.idx)] = {{ task_type: r.task_type, topic: r.topic, verdict: v?.verdict || "pending", note: v?.note || "" }};
  }}
  return {{
    tool: "t12_review_v1",
    generated_at: new Date().toISOString(),
    total: DATA.length,
    verdicts,
    summary: {{
      pass: Object.values(verdicts).filter(v => v.verdict === "pass").length,
      fail: Object.values(verdicts).filter(v => v.verdict === "fail").length,
      doubt: Object.values(verdicts).filter(v => v.verdict === "doubt").length,
      pending: Object.values(verdicts).filter(v => v.verdict === "pending").length,
    }},
  }};
}}

document.getElementById("exportBtn").onclick = () => {{
  const blob = new Blob([JSON.stringify(exportJSON(), null, 2)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "t12_review_result.json";
  a.click();
  URL.revokeObjectURL(a.href);
}};

document.getElementById("copyBtn").onclick = async () => {{
  try {{
    await navigator.clipboard.writeText(JSON.stringify(exportJSON(), null, 2));
    alert("已复制到剪贴板");
  }} catch(e) {{ alert("复制失败，请用导出按钮"); }}
}};

render();
</script>
</body>
</html>"""


def main() -> None:
    rows = load_rows()
    html = build_html(rows).replace("{{N}}", str(len(rows)))
    OUT.write_text(html, encoding="utf-8")
    print(f"OK: {len(rows)} 条 → {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    # 抽查顺序与复核表一致性
    for i in (1, 17, 57, 93, 119):
        r = rows[i - 1]
        print(f"  #{i}: {r['task_type']} | {r['topic']} | {r['player'][:28]}")


if __name__ == "__main__":
    main()
