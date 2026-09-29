"""Review boundaries: missing or stale reviews must never reach formal preflight."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
import io

import build_codex_authored_input_batch as batch


class ReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-input-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        (self.out/'source').mkdir()
        self.write('source/world_plan.json', {'facts': {}})
        self.write('components.json', [{'sample_id':'test.1'}])
        self.write('draft_inputs.json', [{'sample_id':'test.1'}])
        self.manifest = {
            'components_sha256':batch.file_hash(self.out/'components.json'),
            'draft_inputs_sha256':batch.file_hash(self.out/'draft_inputs.json'),
            'source_hashes':{'world_plan.json':batch.file_hash(self.out/'source/world_plan.json')},
        }
        self.write('compile_manifest.json', self.manifest)
        self.ledger = {'review_run_id':'test',
                      'reviewed_draft_sha256':self.manifest['draft_inputs_sha256'], 'items':{}}

    def write(self, name, value):
        (self.out/name).write_text(json.dumps(value), encoding='utf-8')

    def assert_blocked(self, error):
        with patch.object(batch, 'run') as formal:
            with self.assertRaises(error):
                batch.seal_batch(self.out)
            formal.assert_not_called()
        self.assertFalse((self.out/'input_approvals.json').exists())

    def test_compile_artifacts_are_not_approvals(self):
        self.assert_blocked(FileNotFoundError)

    def test_missing_per_row_decision_is_not_approved(self):
        self.write('review_ledger.json', self.ledger)
        self.assert_blocked(ValueError)

    def test_changed_model_messages_invalidate_review(self):
        self.write('review_ledger.json', self.ledger)
        self.write('draft_inputs.json', [{'sample_id':'test.1','generation_messages':'changed'}])
        self.assert_blocked(ValueError)

    def test_source_change_cannot_reuse_old_review(self):
        self.write('review_ledger.json', self.ledger)
        self.write('source/world_plan.json', {'facts': {'injected':'unreviewed'}})
        self.assert_blocked(ValueError)

    def test_ledger_from_another_draft_is_rejected(self):
        self.ledger['reviewed_draft_sha256'] = 'another batch'
        self.write('review_ledger.json', self.ledger)
        self.assert_blocked(ValueError)


class SmallBatchCompileTests(unittest.TestCase):
    def test_explicit_counts_keep_contiguous_ids_and_task_mapping(self):
        with tempfile.TemporaryDirectory(prefix='small-authored-') as folder:
            root = Path(folder)
            source = root/'source'
            source.mkdir()
            plan = dict(scene_groups=[['a.txt','songjiangfu.home_downstairs','一楼门厅'],
                                      ['b.txt','songjiangfu.neighborhood_park','环形步道']],
                        group_counts={'a.txt':1,'b.txt':1}, world_extension='life_state_20260917',
                        task_types={'2':'emotion_attunement'}, snapshot_times={'2':'2026-09-17T09:00:00+08:00'},
                        memory_minutes_ago={}, known_profile={}, relation_overrides={}, facts={}, events={})
            (source/'world_plan.json').write_text(json.dumps(plan),encoding='utf-8')
            (source/'a.txt').write_text('停一会|我不想赶路。~那慢点。~不用催我。~好。|先站一会。~嗯。~就待这里。~可以。|你陪我吧。\n',encoding='utf-8')
            (source/'b.txt').write_text('颜色|浅色好吗？~我想想。~也能选深色。~都可以。|今天想看花。~听你的。~只是假设。~明白。|你会选什么颜色？\n',encoding='utf-8')
            actual_read = batch.read
            with patch.object(batch,'read',side_effect=lambda p: actual_read(p) if p.parent == source else []):
                with redirect_stdout(io.StringIO()):
                    batch.compile_batch(root)
            rows=actual_read(root/'components.json')
            self.assertEqual([r['sample_id'].split('.')[-1] for r in rows],['001','002'])
            self.assertEqual(rows[1]['task_type'],'emotion_attunement')
            self.assertEqual(rows[1]['snapshot']['time'],'2026-09-17T09:00:00+08:00')
            self.assertFalse((root/'input_approvals.json').exists())
            plan['require_history_claims']=True
            (source/'world_plan.json').write_text(json.dumps(plan),encoding='utf-8')
            with patch.object(batch,'read',side_effect=lambda p: actual_read(p) if p.parent == source else []):
                with self.assertRaisesRegex(ValueError,'Missing explicit history claim plan'):
                    batch.compile_batch(root)


if __name__ == '__main__':
    unittest.main()
