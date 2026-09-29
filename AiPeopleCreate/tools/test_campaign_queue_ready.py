import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from run_reviewed_campaign_queue import ready


class ReadyTests(unittest.TestCase):
    def test_approval_files_alone_do_not_start_and_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            names=('approved_components.json','input_approvals.json','input_review_summary.json',
                   'formal_preflight/inputs/frozen_inputs.json')
            for name in names:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            self.assertFalse(ready(root))
            marker={'status':'sealed','files':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}}
            (root/'sealed_ready.json').write_text(json.dumps(marker))
            self.assertTrue(ready(root))
            (root/names[0]).write_text('[]')
            with self.assertRaisesRegex(ValueError,'changed'):ready(root)
