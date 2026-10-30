"""Small publication regression: required assets and closed file allowlist."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.site import check_publish as P


class Assets(unittest.TestCase):
    def test_entry_scripts_are_explicitly_allowed(self):
        frontend=Path(__file__).resolve().parents[1]/'frontend'
        scripts=re.findall(r'<script\s+src="([^"]+)"', (frontend/'index.html').read_text(encoding='utf8'))
        self.assertTrue(scripts)
        for script in scripts:
            self.assertIn(script,P.ALLOWED_FILES)
            self.assertTrue((frontend/script).is_file())

    def test_unknown_files_and_database_still_rejected(self):
        parent=Path(__file__).resolve().parents[1]/'data'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            root=Path(folder)
            for name in ('unreviewed.js','history.sqlite'):
                (root/name).write_text('not permitted',encoding='utf8')
            failures=P.check_file_set(root)
            self.assertTrue(any('unreviewed.js' in f for f in failures))
            self.assertTrue(any('history.sqlite' in f for f in failures))


if __name__=='__main__':unittest.main()
