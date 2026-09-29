from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stable_key(row: dict) -> str:
    return hashlib.sha256(("final-spotcheck-v1:" + row["sample_id"]).encode()).hexdigest()


def allocate(rows: list[dict], total: int) -> dict[str, int]:
    groups = Counter(row["task_type"] for row in rows)
    if total < len(groups) * 2:
        raise ValueError("total is too small to cover every task twice")
    quota = {task: min(2, count) for task, count in groups.items()}
    remaining = total - sum(quota.values())
    while remaining:
        candidates = [task for task, count in groups.items() if quota[task] < count]
        task = max(candidates, key=lambda value: (groups[value] / (quota[value] + 1), value))
        quota[task] += 1
        remaining -= 1
    return quota


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--total", type=int, default=80)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    rows = []
    for split in ("train", "dev", "test"):
        for row in read_jsonl(args.package / f"{split}.accepted.jsonl"):
            row = dict(row)
            row["source_split"] = split
            rows.append(row)
    quota = allocate(rows, args.total)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["task_type"]].append(row)
    selected = []
    for task in sorted(grouped):
        selected.extend(sorted(grouped[task], key=stable_key)[: quota[task]])
    selected.sort(key=lambda row: (row["task_type"], stable_key(row)))

    review_rows = []
    for index, row in enumerate(selected, 1):
        target_index = row["supervised_message_indexes"][-1]
        review_rows.append({
            "index": index,
            "sample_id": row["sample_id"],
            "source_split": row["source_split"],
            "source": row.get("source"),
            "task_type": row["task_type"],
            "render_profile_id": row["render_profile_id"],
            "messages": row["messages"][:target_index],
            "target": row["messages"][target_index]["content"],
            "rating": None,
            "note": "",
        })
    report = {
        "schema_version": "aip.frontend_final_spotcheck80.v1",
        "status": "awaiting_human_review",
        "standard_id": "reply_quality_v1_20260909",
        "selection": "SHA256 stable stratification; every accepted task_type has at least two rows",
        "source_package": str(args.package.resolve()),
        "source_manifest_sha256": hashlib.sha256((args.package / "manifest.json").read_bytes()).hexdigest(),
        "counts": {
            "total": len(review_rows),
            "by_task": dict(Counter(row["task_type"] for row in review_rows)),
            "by_split": dict(Counter(row["source_split"] for row in review_rows)),
            "by_source": dict(Counter(row["source"] for row in review_rows)),
        },
        "reviews": review_rows,
    }
    (args.output / "spotcheck80.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>训练前最终抽检 80 条</title><style>
:root{{--bg:#f6f7f9;--panel:#fff;--line:#d9dde5;--text:#20242b;--muted:#68707d;--good:#197243;--bad:#b42318;--accent:#265d97}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
header{{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid var(--line);padding:12px 20px}}
.top{{max-width:1180px;margin:auto;display:flex;gap:14px;align-items:center;flex-wrap:wrap}}h1{{font-size:19px;margin:0}}.stats{{color:var(--muted);margin-left:auto}}
button{{border:1px solid var(--line);background:#fff;padding:7px 12px;cursor:pointer}}button:hover{{border-color:var(--accent)}}main{{max-width:1180px;margin:18px auto;padding:0 16px 60px}}
.filters{{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}}select{{padding:7px;border:1px solid var(--line);background:#fff}}
.item{{background:var(--panel);border:1px solid var(--line);border-radius:6px;margin:0 0 12px;padding:14px}}.meta{{color:var(--muted);font-size:12px}}
.question,.target{{white-space:pre-wrap;margin-top:9px;padding:10px 12px;border-left:3px solid #aab4c1;background:#f8f9fb}}.target{{border-color:var(--accent);background:#f3f7fb}}
details{{margin-top:9px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:10px;border:1px solid var(--line)}}
.judge{{display:flex;gap:9px;align-items:center;margin-top:11px;flex-wrap:wrap}}.judge button.active.good{{background:#e7f5ec;border-color:var(--good);color:var(--good)}}.judge button.active.bad{{background:#fdecea;border-color:var(--bad);color:var(--bad)}}
input{{flex:1;min-width:220px;padding:8px;border:1px solid var(--line)}}.hidden{{display:none}}
</style></head><body><header><div class="top"><h1>训练前最终抽检 80 条</h1><div class="stats" id="stats"></div><button id="download">下载人工审核结果</button></div></header>
<main><div class="filters"><select id="task"><option value="">全部类型</option></select><select id="state"><option value="">全部状态</option><option value="pending">未审核</option><option value="good">好</option><option value="bad">差</option></select></div><div id="list"></div></main>
<script>const report={data};const KEY='frontend-final-spotcheck80:'+report.source_manifest_sha256;const saved=JSON.parse(localStorage.getItem(KEY)||'{{}}');
for(const r of report.reviews){{if(saved[r.sample_id])Object.assign(r,saved[r.sample_id]);}}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const tasks=[...new Set(report.reviews.map(r=>r.task_type))];task.innerHTML+=[...tasks].map(x=>`<option>${{esc(x)}}</option>`).join('');
function persist(){{const out={{}};for(const r of report.reviews)out[r.sample_id]={{rating:r.rating,note:r.note}};localStorage.setItem(KEY,JSON.stringify(out));}}
function render(){{const tf=task.value,sf=state.value;let shown=0;list.innerHTML=report.reviews.map(r=>{{const state=r.rating||'pending';if((tf&&r.task_type!==tf)||(sf&&state!==sf))return '';shown++;const history=r.messages.map(m=>`[${{m.role}}]\\n${{m.content}}`).join('\\n\\n');const users=r.messages.filter(m=>m.role==='user');const q=users.length?users[users.length-1].content:'';return `<section class="item" data-id="${{esc(r.sample_id)}}"><div class="meta">#${{r.index}} · ${{esc(r.task_type)}} · ${{esc(r.source_split)}} · ${{esc(r.source)}} · ${{esc(r.sample_id)}}</div><div class="question"><b>玩家：</b>${{esc(q)}}</div><div class="target"><b>训练目标：</b>${{esc(r.target)}}</div><details><summary>查看模型完整输入</summary><pre>${{esc(history)}}</pre></details><div class="judge"><button class="good ${{r.rating==='good'?'active':''}}" data-v="good">好</button><button class="bad ${{r.rating==='bad'?'active':''}}" data-v="bad">差</button><input placeholder="备注（可选）" value="${{esc(r.note)}}"></div></section>`;}}).join('');stats.textContent=`已审核 ${{report.reviews.filter(r=>r.rating).length}} / 80 · 当前显示 ${{shown}}`;}}
list.addEventListener('click',e=>{{const b=e.target.closest('button[data-v]');if(!b)return;const box=b.closest('.item'),r=report.reviews.find(x=>x.sample_id===box.dataset.id);r.rating=b.dataset.v;persist();render();}});
list.addEventListener('input',e=>{{if(e.target.tagName!=='INPUT')return;const box=e.target.closest('.item'),r=report.reviews.find(x=>x.sample_id===box.dataset.id);r.note=e.target.value;persist();}});task.onchange=state.onchange=render;
download.onclick=()=>{{const output={{...report,status:report.reviews.every(r=>r.rating)?'human_review_complete':'human_review_incomplete',exported_at:new Date().toISOString()}};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)],{{type:'application/json'}}));a.download='frontend_final_spotcheck80_user_review.json';a.click();URL.revokeObjectURL(a.href);}};render();</script></body></html>"""
    (args.output / "review.html").write_text(page, encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
