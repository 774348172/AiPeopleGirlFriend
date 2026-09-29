import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from test_optimized_comparison import fixture
from run_optimized_comparison import assemble, audit_material
from finish_optimized_comparison_audit import compact_audit_material
from convert_component_to_game_reply import convert
from build_v5_pilot_dataset import _load_baiweixi_render_context


def test_compaction_preserves_every_fact_and_utterance_with_reversible_ids():
    seed,value=fixture();row=assemble(seed,value);scenario=convert(row)
    _,_,anchor=_load_baiweixi_render_context()
    old,_,catalog,_=audit_material(scenario,{'reply':'好。'},seed['topic'],anchor)
    payload,_,new,_,mapping=compact_audit_material(scenario,{'reply':'好。'},seed['topic'],anchor)
    assert len(mapping)==len(catalog)==len(new)
    for before,after in zip(catalog,new):
        assert mapping[after['id']]==before['id']
        assert {k:v for k,v in before.items() if k!='id'}=={k:v for k,v in after.items() if k!='id'}
    assert len(payload['旧记忆'])==len(payload['最近对话'])==4
    for key in ('旧记忆','最近对话'):
        assert [{k:v for k,v in t.items() if k!='evidence_id'} for t in payload[key]]==old[key]
    assert len(json.dumps(payload,ensure_ascii=False))<len(json.dumps(old,ensure_ascii=False))
    assert set(mapping.values())=={e['id'] for e in catalog}
