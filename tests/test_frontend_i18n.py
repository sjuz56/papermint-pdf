import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        cls.i18n = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")

    def test_all_interface_languages_are_available(self):
        for language in ("en", "cs", "de", "es", "fr", "zh", "hi", "ja"):
            self.assertIn(f'<option value="{language}">', self.index)

    def test_i18n_loads_before_interactive_scripts(self):
        self.assertLess(
            self.index.index('/static/i18n.js'),
            self.index.index('/static/app.js'),
        )
        self.assertLess(
            self.index.index('/static/i18n.js'),
            self.index.index('/static/account-pricing.js'),
        )

    def test_drop_zone_uses_transparent_mascot_watermark(self):
        self.assertIn('class="drop-mascot"', self.index)
        self.assertIn('octopus-mascot-transparent.png', self.index)
        self.assertIn('.drop-mascot {', self.styles)
        self.assertIn('opacity: .105;', self.styles)
        self.assertIn('pointer-events: none;', self.styles)

    def test_tool_translations_cover_first_and_last_tools(self):
        for translated_name in (
            "Sloučit PDF", "Oříznout PDF",
            "PDF zusammenfügen", "PDF zuschneiden",
            "Unir PDF", "Recortar PDF",
            "Fusionner PDF", "Recadrer PDF",
            "合并 PDF", "裁剪 PDF",
            "PDF मिलाएँ", "PDF क्रॉप करें",
            "PDFを結合", "PDFを切り抜き",
        ):
            self.assertIn(translated_name, self.i18n)

    def test_ask_octo_is_translated_and_marked_pro(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for translated_description in (
            "Shrňte PDF", "PDF zusammenfassen", "Resume un PDF",
            "Résumez un PDF", "总结 PDF", "PDF का सारांश", "PDFを要約",
        ):
            self.assertIn(translated_description, self.i18n)
        self.assertIn("pro-badge", app_js)
        self.assertIn("/api/ai/document", app_js)


if __name__ == "__main__":
    unittest.main()
