import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from run_candidate_campaign_batch import sync_completed_status


class CompletedStatusTests(unittest.TestCase):
    def test_migrates_without_calling_model_and_preserves_old_status(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'formal_run';out.mkdir()
            (out/'batch_status.json').write_text(json.dumps({'status':'complete'}))
            (out/'report.json').write_text(json.dumps({'count':100,'auto_pass':92}))
            old={'status':'awaiting_codex_quality_review','report':{'auto_pass':92}}
            (root/'execution_status.json').write_text(json.dumps(old))
            with patch('run_candidate_campaign_batch.run') as model:
                sync_completed_status(root);sync_completed_status(root)
                model.assert_not_called()
            self.assertEqual(json.loads((root/'execution_status_before_flow_sync.json').read_text()),old)
            current=json.loads((root/'execution_status.json').read_text())
            self.assertEqual(current['status'],'machine_processing_complete')
            self.assertFalse(current['codex_reply_review_required'])
            self.assertFalse(current['human_queue_blocks_next_batch'])

    def test_incomplete_cannot_be_marked_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'formal_run').mkdir()
            (root/'formal_run/batch_status.json').write_text(json.dumps({'status':'running'}))
            with self.assertRaises(ValueError):sync_completed_status(root)
            self.assertFalse((root/'execution_status.json').exists())
