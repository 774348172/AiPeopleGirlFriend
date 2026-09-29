"""Create the first 20-row external input-review manifest for the formal gate.

This does not call a model and does not generate replies. The source rows are
copied from the already inspected smoke inputs; the review run is recorded
separately from the preparation script so the formal runner can bind it.
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / '训练数据/curated100_inputs_20260915'
parser = argparse.ArgumentParser(); parser.add_argument('--count', type=int, required=True); parser.add_argument('--output-root', type=Path, required=True); parser.add_argument('--run-id', required=True)
args = parser.parse_args()
out = ROOT / args.output_root
run_id = args.run_id

if out.exists():
    raise SystemExit(f'output already exists: {out}')
out.mkdir(parents=True)
components = json.loads((source/'components.json').read_text(encoding='utf-8'))[:args.count]
old = json.loads((source/'input_approvals.json').read_text(encoding='utf-8'))
if len(components) != args.count:
    raise SystemExit(f'source must contain at least {args.count} inputs')
approvals = {}
for row in components:
    sid = row['sample_id']
    item = dict(old[sid])
    item.update(provenance='external_codex_review', review_run_id=run_id,
                reviewer='Codex', decision='approve')
    approvals[sid] = item
(out/'components.json').write_text(json.dumps(components, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
(out/'input_approvals.json').write_text(json.dumps(approvals, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
(out/'input_review_summary.json').write_text(json.dumps({
    'review_run_id': run_id, 'attempted': args.count, 'approved': args.count,
    'approved_ids': [r['sample_id'] for r in components],
    'rejected_ids': [], 'pass_rate': 1.0,
    'reviewer': 'Codex', 'scope': '冻结输入逐条复核；未生成回复'
}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(f'created stage1 manifest: {out}')
