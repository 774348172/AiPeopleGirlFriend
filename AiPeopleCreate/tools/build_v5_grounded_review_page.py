"""Build the human-review page for the V5 grounded 80-row spot check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "训练数据" / "baiweixi_v5_pilot_v1"

TASK_CN = {
    "reply_accept_authoritative_update": "接受权威更新",
    "reply_reject_stale_claim": "拒绝旧事实",
    "reply_correct_false_premise": "纠正错误前提",
    "reply_resolve_world_memory_conflict": "解决世界/记忆冲突",
    "reply_confirm_current_state": "确认当前状态",
    "reply_subject_attribution": "主体归属",
    "reply_insufficient_information": "信息不足",
    "reply_direct_answer": "直接回答",
}

TASK_COLORS = {
    "reply_accept_authoritative_update": "#15803d",
    "reply_reject_stale_claim": "#b45309",
    "reply_correct_false_premise": "#b91c1c",
    "reply_resolve_world_memory_conflict": "#7c3aed",
    "reply_confirm_current_state": "#0369a1",
    "reply_subject_attribution": "#0f766e",
    "reply_insufficient_information": "#475569",
    "reply_direct_answer": "#be185d",
}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_rows(root: Path) -> list[dict]:
    candidates = {
        row["sample_id"]: row
        for row in _read_jsonl(root / "inputs" / "grounded_candidates.jsonl")
    }
    spot = json.loads((root / "inputs" / "grounded_spot_check_80.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for index, review in enumerate(spot["reviews"], 1):
        candidate = candidates.get(review["sample_id"])
        if not candidate:
            raise RuntimeError(f"spot-check candidate missing: {review['sample_id']}")
        scenario = candidate["scenario"]
        model_view = scenario["model_view"]
        oracle = scenario["oracle_view"]
        rows.append(
            {
                "no": index,
                "sample_id": review["sample_id"],
                "task": scenario["task_type"],
                "task_cn": TASK_CN.get(scenario["task_type"], scenario["task_type"]),
                "color": TASK_COLORS.get(scenario["task_type"], "#64748b"),
                "utterance": model_view["current_protagonist_utterance"],
                "history": model_view.get("recent_dialogue") or [],
                "feedback": model_view.get("game_feedback") or [],
                "memory": model_view.get("selected_memory_frame", {}).get("selected_memories") or [],
                "facts": oracle.get("required_assertions") or [],
                "forbidden": oracle.get("forbidden_assertions") or [],
                "reply": candidate["teacher_target"]["reply"],
                "claims": candidate["semantic_audit"].get("extracted_claims") or [],
                "gate_report": candidate["gate_report"],
                "attempt": candidate.get("provenance", {}).get("attempt", 1),
                "auto_pass": review.get("approved") is True,
            }
        )
    if len(rows) != 80:
        raise RuntimeError(f"expected 80 spot-check rows, got {len(rows)}")
    return rows


def build_html(rows: list[dict]) -> str:
    rows_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    task_json = json.dumps(TASK_CN, ensure_ascii=False, separators=(",", ":"))
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>白未晞 V5 grounded 80 条人工抽检</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#64748b; --line:#d8dee9; --panel:#fff; --bg:#f4f6f8; --navy:#162235; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Microsoft YaHei", "Noto Sans SC", sans-serif; color:var(--ink); background:var(--bg); }}
header {{ position:sticky; top:0; z-index:20; background:var(--navy); color:#fff; box-shadow:0 3px 14px #1720332b; }}
.head {{ max-width:1220px; margin:auto; padding:18px 24px 12px; }}
h1 {{ margin:0; font-size:21px; letter-spacing:0; }}
.sub {{ margin-top:6px; color:#c5cfdd; font-size:13px; }}
.toolbar {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:14px; }}
button, select, input {{ font:inherit; }}
button {{ border:1px solid transparent; border-radius:6px; cursor:pointer; }}
.tool {{ padding:8px 12px; color:#fff; background:#2b3c55; border-color:#51627b; }}
.tool.primary {{ background:#2563eb; border-color:#60a5fa; }}
.tool.danger {{ background:#8f2634; border-color:#f59aaa; }}
.counter {{ margin-left:auto; font-size:13px; color:#e2e8f0; }}
.filters {{ max-width:1220px; margin:auto; padding:10px 24px 14px; display:flex; gap:7px; flex-wrap:wrap; overflow:auto; }}
.filter {{ padding:6px 10px; border-color:#52647c; background:#22324a; color:#dbe6f4; white-space:nowrap; }}
.filter.active {{ background:#fff; color:#162235; border-color:#fff; }}
main {{ max-width:1220px; margin:24px auto; padding:0 24px 60px; }}
.notice {{ margin-bottom:18px; padding:12px 14px; border-left:4px solid #2563eb; background:#eaf2ff; color:#214a83; line-height:1.55; font-size:13px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-left:5px solid #94a3b8; border-radius:8px; margin:0 0 14px; overflow:hidden; box-shadow:0 1px 2px #1720330b; }}
.card.good {{ border-left-color:#16a34a; }} .card.bad {{ border-left-color:#dc2626; }} .card.severe {{ border-left-color:#991b1b; background:#fff8f8; }} .card.skip {{ border-left-color:#64748b; opacity:.72; }}
.card-head {{ display:flex; align-items:center; gap:10px; padding:13px 16px 8px; flex-wrap:wrap; }}
.num {{ color:var(--muted); font-size:12px; min-width:30px; }}
.tag {{ color:#fff; padding:4px 9px; border-radius:4px; font-size:12px; }}
.id {{ color:var(--muted); font:12px ui-monospace, Consolas, monospace; margin-left:auto; }}
.body {{ padding:0 16px 14px; }}
.label {{ color:#64748b; font-size:11px; text-transform:uppercase; letter-spacing:.04em; margin:12px 0 5px; }}
.utterance {{ background:#edf5ff; border:1px solid #cfe1fb; border-radius:6px; padding:10px 12px; line-height:1.6; }}
.line {{ padding:7px 10px; margin:5px 0; border-radius:5px; line-height:1.55; white-space:pre-wrap; }}
.line.history {{ background:#f5f7fa; border:1px solid #e6eaf0; }}
.line.feedback {{ background:#fff8e8; border:1px solid #f6dfad; }}
.who {{ font-weight:700; margin-right:6px; }}
.reply {{ background:#fffdf1; border:1px solid #eadf9b; padding:12px; border-radius:6px; line-height:1.7; font-size:15px; }}
.columns {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.factbox {{ background:#f8fafc; border:1px solid #e3e8ef; border-radius:6px; padding:9px 11px; line-height:1.55; font-size:13px; }}
.fact {{ margin:4px 0; }} .fact strong {{ color:#334155; }} .forbidden {{ color:#9f1239; }}
.audit {{ color:#475569; font-size:12px; line-height:1.55; }}
.verdict {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; border-top:1px solid #edf0f4; padding-top:12px; margin-top:14px; }}
.vbtn {{ padding:8px 13px; background:#fff; border-color:#cbd5e1; color:#334155; }}
.vbtn.good.on {{ background:#dcfce7; border-color:#16a34a; color:#166534; }} .vbtn.bad.on {{ background:#fee2e2; border-color:#dc2626; color:#991b1b; }} .vbtn.severe.on {{ background:#fce7f3; border-color:#be123c; color:#9f1239; }} .vbtn.skip.on {{ background:#e2e8f0; border-color:#64748b; color:#334155; }}
.note {{ flex:1; min-width:230px; border:1px solid #cbd5e1; border-radius:6px; padding:8px 10px; }}
.status {{ color:#64748b; font-size:12px; margin-left:auto; }}
.empty {{ text-align:center; padding:44px; color:#64748b; background:#fff; border:1px dashed #cbd5e1; border-radius:8px; }}
@media (max-width:760px) {{ .head, .filters, main {{ padding-left:14px; padding-right:14px; }} h1 {{ font-size:18px; }} .counter {{ margin-left:0; width:100%; }} .columns {{ grid-template-columns:1fr; }} .id {{ width:100%; margin-left:40px; }} .note {{ min-width:100%; }} }}
</style>
</head>
<body>
<header>
  <div class="head">
    <h1>白未晞 V5 grounded 80 条人工抽检</h1>
    <div class="sub">这是最终包固定哈希抽样。自动门已通过不等于人工结论；请重点看是否切题、事实、主体、人称和句子逻辑。</div>
    <div class="toolbar">
      <button class="tool primary" id="next">跳到下一条未判定</button>
      <button class="tool" id="export">导出我的审核 JSON</button>
      <button class="tool" id="copy">复制审核摘要</button>
      <button class="tool danger" id="reset">清空本页判断</button>
      <span class="counter" id="counter"></span>
    </div>
  </div>
  <div class="filters" id="filters"></div>
</header>
<main>
  <div class="notice"><strong>判定建议：</strong>“好”表示回复切题、事实正确、主体和人称正确且句子逻辑通顺；“差”表示一般质量问题；“严重错误”表示事实错误、主体错位、继续使用 stale 状态或明显答非所问。每条备注会实时保存在当前浏览器。</div>
  <div id="main"></div>
</main>
<script>
const ROWS = {rows_json};
const TASKS = {task_json};
const KEY = "baiweixi_v5_grounded_spot_review_80_v2";
let state = {{}};
let activeTask = "all";
try {{ state = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{ state = {{}}; }}
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\\"":"&quot;","'":"&#39;"}}[c]));
const text = (value) => escapeHtml(value);
const verdictLabel = {{good:"好", bad:"差", severe:"严重错误", skip:"跳过"}};
function selectedRows() {{ return ROWS.filter(r => activeTask === "all" || r.task === activeTask); }}
function verdictCount() {{ return ROWS.filter(r => state[r.sample_id]?.verdict).length; }}
function saveVerdict(id, verdict) {{ state[id] = state[id] || {{}}; state[id].verdict = verdict; localStorage.setItem(KEY, JSON.stringify(state)); render(); }}
function renderFilters() {{
  const counts = {{}}; ROWS.forEach(r => counts[r.task] = (counts[r.task] || 0) + 1);
  document.getElementById("filters").innerHTML = `<button class="filter ${{activeTask === "all" ? "active" : ""}}" data-filter-task="all">全部 80</button>` +
    Object.entries(TASKS).map(([key, label]) => `<button class="filter ${{activeTask === key ? "active" : ""}}" data-filter-task="${{key}}">${{label}} ${{counts[key] || 0}}</button>`).join("");
}}
function factText(assertion) {{
  if (!assertion) return "";
  return `${{assertion.subject}} / ${{assertion.predicate}} / ${{assertion.object || "（未知）"}}`;
}}
function render() {{
  renderFilters();
  document.getElementById("counter").textContent = `已判定 ${{verdictCount()}} / ${{ROWS.length}} · 好 ${{ROWS.filter(r => state[r.sample_id]?.verdict === "good").length}} · 差 ${{ROWS.filter(r => state[r.sample_id]?.verdict === "bad").length}} · 严重 ${{ROWS.filter(r => state[r.sample_id]?.verdict === "severe").length}}`;
  const visible = selectedRows();
  document.getElementById("main").innerHTML = visible.length ? visible.map(r => card(r)).join("") : `<div class="empty">没有符合筛选条件的记录。</div>`;
}}
function card(r) {{
  const v = state[r.sample_id] || {{}};
  const cls = v.verdict || "";
  const history = r.history.map(line => `<div class="line history"><span class="who">${{text(line.split("：")[0] || "历史")}}</span>${{text(line.includes("：") ? line.slice(line.indexOf("：") + 1) : line)}}</div>`).join("");
  const feedback = r.feedback.map(line => `<div class="line feedback">${{text(line)}}</div>`).join("");
  const facts = r.facts.map(f => `<div class="fact"><strong>必答：</strong>${{text(factText(f))}}</div>`).join("");
  const forbidden = r.forbidden.map(f => `<div class="fact forbidden"><strong>禁止：</strong>${{text(factText(f))}}</div>`).join("");
  const audit = r.gate_report?.decision || "unknown";
  return `<article class="card ${{cls}}" id="row-${{r.sample_id}}" data-task="${{r.task}}">
    <div class="card-head"><span class="num">#${{r.no}}</span><span class="tag" style="background:${{r.color}}">${{text(r.task_cn)}}</span><span class="id">${{text(r.sample_id)}}</span></div>
    <div class="body">
      <div class="label">玩家当前输入</div><div class="utterance">${{text(r.utterance)}}</div>
      ${{history ? `<div class="label">相关历史</div>${{history}}` : ""}}
      ${{feedback ? `<div class="label">游戏事实反馈</div>${{feedback}}` : ""}}
      <div class="columns"><div><div class="label">权威必答事实</div><div class="factbox">${{facts || "无"}}${{forbidden}}</div></div><div><div class="label">模型回复</div><div class="reply">${{text(r.reply)}}</div></div></div>
      <div class="label">自动审核摘要</div><div class="audit">RG 决策：${{text(audit)}} ｜ 生成尝试：${{r.attempt}} ｜ 自动抽检：${{r.auto_pass ? "通过" : "未通过"}}</div>
      <div class="verdict"><button type="button" class="vbtn good ${{v.verdict === "good" ? "on" : ""}}" data-id="${{r.sample_id}}" data-verdict="good" onclick="saveVerdict('${{r.sample_id}}','good')">好</button><button type="button" class="vbtn bad ${{v.verdict === "bad" ? "on" : ""}}" data-id="${{r.sample_id}}" data-verdict="bad" onclick="saveVerdict('${{r.sample_id}}','bad')">差</button><button type="button" class="vbtn severe ${{v.verdict === "severe" ? "on" : ""}}" data-id="${{r.sample_id}}" data-verdict="severe" onclick="saveVerdict('${{r.sample_id}}','severe')">严重错误</button><button type="button" class="vbtn skip ${{v.verdict === "skip" ? "on" : ""}}" data-id="${{r.sample_id}}" data-verdict="skip" onclick="saveVerdict('${{r.sample_id}}','skip')">跳过</button><input class="note" data-id="${{r.sample_id}}" value="${{text(v.note || "")}}" placeholder="备注（可选）"><span class="status">${{v.verdict ? verdictLabel[v.verdict] : "未判定"}}</span></div>
    </div></article>`;
}}
document.addEventListener("click", event => {{
  const filter = event.target.closest(".filter");
  if (filter) {{ activeTask = filter.dataset.filterTask; render(); return; }}
  if (event.target.id === "next") {{ const next = selectedRows().find(r => !state[r.sample_id]?.verdict); if (next) document.getElementById("row-" + next.sample_id)?.scrollIntoView({{behavior:"smooth", block:"center"}}); }}
  if (event.target.id === "reset" && confirm("确定清空本页所有人工判断吗？")) {{ state = {{}}; localStorage.removeItem(KEY); render(); }}
  if (event.target.id === "export") {{ const out = ROWS.map(r => ({{sample_id:r.sample_id, no:r.no, task:r.task, verdict:state[r.sample_id]?.verdict || "", note:state[r.sample_id]?.note || ""}})); const blob = new Blob([JSON.stringify(out, null, 2)], {{type:"application/json"}}); const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="baiweixi_v5_grounded_80_user_review.json"; a.click(); }}
  if (event.target.id === "copy") {{ const out = ROWS.map(r => `${{r.no}}\\t${{r.task_cn}}\\t${{state[r.sample_id]?.verdict || "未判定"}}\\t${{state[r.sample_id]?.note || ""}}`).join("\\n"); navigator.clipboard.writeText(out).then(() => alert("已复制审核摘要")); }}
}});
document.addEventListener("input", event => {{ if (event.target.classList.contains("note")) {{ const id=event.target.dataset.id; state[id]=state[id]||{{}}; state[id].note=event.target.value; localStorage.setItem(KEY, JSON.stringify(state)); }} }});
render();
</script>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    rows = load_rows(args.root)
    output = args.output or args.root / "grounded_spot_check_80_review.html"
    output.write_text(build_html(rows), encoding="utf-8")
    print(f"review page: {output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
