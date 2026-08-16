import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mirror  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_por_seika.json")


class NormaliseTest(unittest.TestCase):
    def setUp(self):
        raw = open(FIXTURE, "rb").read()
        self.rec = mirror.parse_rms(b"\xef\xbb\xbf" + raw)[0]  # BOM like ORC serves it

    def test_parse_handles_bom(self):
        self.assertEqual(self.rec["YachtName"], "SEIKA")

    def test_allowance_to_knots(self):
        norm = mirror.normalise(self.rec)
        self.assertIsNotNone(norm)
        p = norm["polar"]
        # R90 @ 10 kn: 468.4 s/NM -> 7.69 kn (matches the certificate speed guide)
        i10 = p["windSpeeds"].index(10.0)
        i90 = p["angles"].index(90.0)
        self.assertAlmostEqual(p["speedMatrix"][i10][i90], 7.69, places=2)
        # Beat VMG @ 10 kn ~ 5.25 kn; beat angle passthrough
        self.assertAlmostEqual(p["beatVMG"][i10], 5.25, places=1)
        self.assertAlmostEqual(p["beatAngles"][i10], 38.4, places=1)
        self.assertEqual(len(p["speedMatrix"]), len(p["windSpeeds"]))
        self.assertEqual(len(p["speedMatrix"][0]), len(p["angles"]))
        self.assertEqual(p["angles"], [52.0, 60.0, 75.0, 90.0, 110.0, 120.0, 135.0, 150.0])

    def test_meta(self):
        norm = mirror.normalise(self.rec)
        m = norm["meta"]
        self.assertEqual(m["country"], "POR")
        self.assertEqual(m["sailNo"], "NOR 10116")
        self.assertEqual(m["class"], "IMX 40")
        self.assertTrue(m["issueDate"].startswith("2026-03-04"))
        row = mirror.index_row(m)
        self.assertEqual(set(row), {"refNo", "yachtName", "sailNo", "country", "class", "certNo", "issueDate"})

    def test_missing_allowances_skipped(self):
        rec = dict(self.rec)
        rec["Allowances"] = {}
        self.assertIsNone(mirror.normalise(rec))

    def test_kn_conversion_edge_cases(self):
        self.assertIsNone(mirror._kn(0))
        self.assertIsNone(mirror._kn(None))
        self.assertIsNone(mirror._kn("x"))
        self.assertEqual(mirror._kn(3600), 1.0)


if __name__ == "__main__":
    unittest.main()
