import json, sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
src=root/'训练数据/curated100_inputs_20260915/components.json'
approvals=root/'训练数据/curated100_inputs_20260915/input_approvals.json'
out=root/'训练数据/formal_smoke20_inputs_20260915'
rows=json.loads(src.read_text(encoding='utf-8'))[:20]
ap=json.loads(approvals.read_text(encoding='utf-8'))
out.mkdir(parents=True,exist_ok=False)
(out/'components.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(out/'input_approvals.json').write_text(json.dumps({r['sample_id']:ap[r['sample_id']] for r in rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'prepared {len(rows)} frozen reviewed seeds')
