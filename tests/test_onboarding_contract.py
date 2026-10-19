"""PR-12 ilk kullanım turunun güvenli statik sözleşmesi."""
import unittest
from pathlib import Path


class OnboardingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.html = (cls.root / "ui" / "index.html").read_text(encoding="utf-8")
        cls.js = (cls.root / "ui" / "onboarding.js").read_text(encoding="utf-8")

    def test_tur_markupi_ve_tercih_secimleri_var(self):
        for parca in ("onboardingDialog", "onboardingNext", "onboardingSkip", "onboardingNever", "onboardingHelp"):
            self.assertIn(parca, self.html)
        self.assertIn("touche.finans.onboarding.v1.dismissed", self.js)

    def test_tur_finansal_yazma_yapmaz(self):
        self.assertNotIn("fetch(", self.js)
        self.assertNotIn("/api/", self.js)
        self.assertIn("localStorage", self.js)

    def test_tur_asseti_sunucu_whitelistinde(self):
        server = (self.root / "scripts" / "finans_asistani_sunucu.py").read_text(encoding="utf-8")
        self.assertIn('"/onboarding.js": "onboarding.js"', server)
        self.assertIn('<script src="/onboarding.js" defer></script>', self.html)


if __name__ == "__main__":
    unittest.main()
