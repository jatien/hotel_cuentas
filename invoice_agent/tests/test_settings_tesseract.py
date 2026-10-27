"""La ruta de Tesseract por defecto solo es la de Windows en Windows (en Linux, el del PATH)."""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
PRINT_SETTING = "import mysite.settings.base as s; print(repr(s.TESSERACT_CMD))"


class TesseractDefaultPathTests(SimpleTestCase):
    def run_settings(self, **env):
        clean = {k: v for k, v in os.environ.items() if k != "TESSERACT_CMD"}
        clean.update(DB_PASSWORD="x", **env)
        result = subprocess.run([sys.executable, "-c", PRINT_SETTING], cwd=ROOT, env=clean,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def test_default_depends_on_the_platform(self):
        value = self.run_settings()
        if sys.platform == "win32":
            self.assertIn("tesseract.exe", value)
        else:
            self.assertEqual(value, "''")  # pytesseract usa el «tesseract» del PATH

    def test_env_variable_still_wins(self):
        self.assertEqual(self.run_settings(TESSERACT_CMD="/opt/ocr/tesseract"), "'/opt/ocr/tesseract'")
