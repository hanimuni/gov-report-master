"""Offline regression tests: python -m unittest discover -s evals -p test_style_guard.py."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("style_guard", ROOT / "scripts/style_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

HEADER = f'''<hh:head xmlns:hh="{guard.HH[1:-1]}" xmlns:hc="{guard.HC[1:-1]}">
<hh:font id="0" face="함초롬바탕"/>
<hh:charPr id="0" height="1500" textColor="#FFFFFF"><hh:fontRef hangul="0"/></hh:charPr>
<hh:charPr id="1" height="1500" textColor="#000000"><hh:fontRef hangul="0"/></hh:charPr>
<hh:charPr id="2" height="1500" textColor="#FFFFFF" shadeColor="#1F3864"><hh:fontRef hangul="0"/></hh:charPr>
<hh:paraPr id="0"/>
<hh:borderFill id="1"><hc:fillBrush><hc:winBrush faceColor="#1F3864"/></hc:fillBrush></hh:borderFill>
<hh:borderFill id="2"><hc:fillBrush><hc:winBrush faceColor="#FFFFFF"/></hc:fillBrush></hh:borderFill>
<hh:style id="0"/></hh:head>'''


def paragraph(char="0"):
    return f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="{char}"><hp:t>검증</hp:t></hp:run></hp:p>'


def rect(content, color="#1F3864", alpha="0", extra=""):
    fill = f'<hc:fillBrush><hc:winBrush faceColor="{color}" alpha="{alpha}"/>{extra}</hc:fillBrush>' if color else ''
    return f'<hp:rect>{fill}<hp:drawText><hp:subList>{content}</hp:subList></hp:drawText></hp:rect>'


def table(content, fill="1"):
    return f'<hp:tbl><hp:tr><hp:tc borderFillIDRef="{fill}"><hp:subList>{content}</hp:subList></hp:tc></hp:tr></hp:tbl>'


class StyleGuardRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.hwpx"

    def fixture(self, content, header=HEADER):
        with ZipFile(self.path, "w") as z:
            z.writestr("Contents/header.xml", header)
            z.writestr("Contents/section0.xml", f'<section xmlns:hp="{guard.HP[1:-1]}" xmlns:hc="{guard.HC[1:-1]}">{content}</section>')

    def test_background_precedence_and_existing_cells(self):
        p = paragraph()
        cases = [
            ("paper", p, 2), ("dark_rect", rect(p), 0),
            ("white_rect", rect(p, "#FFFFFF"), 2),
            ("dark_cell", table(p), 0), ("white_cell", table(p, "2"), 2),
            ("white_rect_in_dark_cell", table(rect(p, "#FFFFFF")), 2),
            ("dark_rect_in_white_cell", table(rect(p), "2"), 0),
            ("white_cell_in_dark_rect", rect(table(p, "2")), 2),
            ("unfilled_rect_in_dark_cell", table(rect(p, "")), 0),
            ("sibling_is_not_background", rect("") + p, 2),
            ("translucent_not_proven", rect(p, alpha="128"), 2),
            ("mixed_fill_not_proven", rect(p, extra="<hc:gradation/>"), 2),
            ("malformed_color_not_proven", rect(p, "none"), 2),
            ("black_text", paragraph("1"), 0),
            ("existing_char_shade", paragraph("2"), 0),
        ]
        for name, content, expected in cases:
            with self.subTest(name=name):
                self.fixture(content)
                rows, page, order = guard.read_hwpx(str(self.path))
                findings, metrics = guard.check(rows, page, guard.load_typography(None), order)
                self.assertEqual(metrics["invisible_chars"], expected)
                self.assertEqual(any(f["id"] == "T10" for f in findings), bool(expected))

    def test_id_order_gate_preserved(self):
        self.fixture(paragraph("1"), HEADER.replace('charPr id="1"', 'charPr id="9"'))
        rows, page, order = guard.read_hwpx(str(self.path))
        findings, _ = guard.check(rows, page, guard.load_typography(None), order)
        self.assertTrue(any(f["id"] == "T9" and f["severity"] == "CRITICAL" for f in findings))

    def test_cli_contract_and_verification_boundary(self):
        cases = [(rect(paragraph()), 0), (paragraph(), 1),
                 ('<hp:pageMargin left="0" right="0"/>' + paragraph("1"), 2),
                 (paragraph("1"), 3)]
        for content, expected in cases:
            with self.subTest(exit=expected):
                self.fixture(content, "<invalid" if expected == 3 else HEADER)
                result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/style_guard.py"), str(self.path), "--json-only"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                data = json.loads(result.stdout)
                if expected == 3:
                    self.assertEqual(data["verdict"], "ERROR")
                    self.assertIn("message", data)
                    continue
                self.assertEqual(set(data), {"verdict", "findings", "metrics", "unverifiable"})
                self.assertTrue(data["unverifiable"])


if __name__ == "__main__":
    unittest.main()
