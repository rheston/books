"""Guards for the single-file web app (docs/index.html), which is generated from web/src/."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_web  # noqa: E402

BUILT = ROOT / "docs" / "index.html"


class WebBuildTests(unittest.TestCase):
    def test_built_file_is_up_to_date_with_its_sources(self):
        self.assertEqual(
            BUILT.read_text(encoding="utf-8"), build_web.render(),
            "docs/index.html is stale. Run: python tools/build_web.py",
        )

    def test_check_flag_agrees(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_web.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_self_contained_no_external_resources(self):
        html = BUILT.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'(?:src|href)="https?://[^"]+"', html), [])
        self.assertNotIn("/*__", html)  # every placeholder was replaced
        self.assertEqual(html.count("<script"), html.count("</script>"))

    def test_embedded_library_directory(self):
        html = BUILT.read_text(encoding="utf-8")
        blob = re.search(r'<script type="application/json" id="library-directory">(.*?)</script>', html, re.S).group(1)
        rows = json.loads(blob)
        keys = {r[0] for r in rows}
        self.assertGreater(len(rows), 1000)
        self.assertEqual(len(keys), len(rows), "duplicate library keys")
        self.assertLessEqual({"kcls", "spl", "nypl"}, keys)
        self.assertTrue(all(isinstance(r[0], str) and isinstance(r[1], str) and r[2] in (0, 1) for r in rows))


if __name__ == "__main__":
    unittest.main()
