"""Testy profilu na małej, ręcznie policzonej ramce.

Ramka kontrolna ma 4 rekordy i 3 kolumny. Jeden rekord jest pełną kopią
pierwszego. Brak jest w mieście i w cenie, po 1 na 4, czyli 25%. Kolumna
opis nie ma braków. Unikalne wartości nie liczą braków.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session import DataSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def controlled_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "miasto": ["Łódź", "Łódź", "Kraków", None],
            "cena": [10.0, 10.0, None, 40.0],
            "opis": ["a", "a", "b", "b"],
        }
    )


class ProfileTests(unittest.TestCase):
    def test_controlled_frame_matches_hand_counted_profile(self) -> None:
        profile = DataSession(controlled_frame()).profile()

        self.assertEqual(profile.row_count, 4)
        self.assertEqual(profile.column_count, 3)
        self.assertEqual(profile.duplicate_count, 1)

        summary = profile.column_summary
        self.assertEqual(list(summary.index), ["miasto", "cena", "opis"])
        self.assertEqual(int(summary.loc["miasto", "missing"]), 1)
        self.assertEqual(int(summary.loc["cena", "missing"]), 1)
        self.assertEqual(int(summary.loc["opis", "missing"]), 0)
        self.assertEqual(float(summary.loc["miasto", "missing_pct"]), 25.0)
        self.assertEqual(float(summary.loc["cena", "missing_pct"]), 25.0)
        self.assertEqual(float(summary.loc["opis", "missing_pct"]), 0.0)
        self.assertEqual(int(summary.loc["miasto", "unique"]), 2)
        self.assertEqual(int(summary.loc["cena", "unique"]), 2)
        self.assertEqual(int(summary.loc["opis", "unique"]), 2)

        cena = profile.numeric_stats.loc["cena"]
        self.assertEqual(int(cena["count"]), 3)
        self.assertAlmostEqual(float(cena["mean"]), 20.0)
        self.assertAlmostEqual(float(cena["std"]), 300 ** 0.5)
        self.assertAlmostEqual(float(cena["min"]), 10.0)
        self.assertAlmostEqual(float(cena["q1"]), 10.0)
        self.assertAlmostEqual(float(cena["median"]), 10.0)
        self.assertAlmostEqual(float(cena["q3"]), 25.0)
        self.assertAlmostEqual(float(cena["max"]), 40.0)
        self.assertEqual(list(profile.numeric_stats.index), ["cena"])

        self.assertEqual(int(profile.text_stats.loc["miasto", "count"]), 3)
        self.assertEqual(int(profile.text_stats.loc["miasto", "unique"]), 2)
        self.assertEqual(profile.text_stats.loc["miasto", "top"], "Łódź")
        self.assertEqual(int(profile.text_stats.loc["miasto", "freq"]), 2)
        self.assertEqual(profile.text_stats.loc["opis", "top"], "a")
        self.assertEqual(int(profile.text_stats.loc["opis", "freq"]), 2)

    def test_profile_does_not_change_session(self) -> None:
        session = DataSession(controlled_frame())
        before_current = session.current.copy()
        before_raw = session.raw.copy()

        session.profile()

        self.assertTrue(session.current.equals(before_current))
        self.assertTrue(session.raw.equals(before_raw))
        self.assertEqual(session.audit_log, [])

    def test_profile_reads_current_not_raw(self) -> None:
        session = DataSession(controlled_frame())
        session.current = session.current.iloc[:1].copy()

        profile = session.profile()

        self.assertEqual(profile.row_count, 1)
        self.assertEqual(profile.duplicate_count, 0)
        self.assertEqual(len(session.raw), 4)

    def test_partial_match_is_not_a_full_duplicate(self) -> None:
        frame = pd.DataFrame({"a": [1, 1], "b": ["x", "y"]})
        self.assertEqual(DataSession(frame).profile().duplicate_count, 0)

    def test_repeated_full_copies_count_each_extra_row(self) -> None:
        frame = pd.DataFrame({"a": [1, 1, 1], "b": ["x", "x", "x"]})
        self.assertEqual(DataSession(frame).profile().duplicate_count, 2)

    def test_rows_with_only_missing_values_are_duplicates(self) -> None:
        frame = pd.DataFrame({"a": [None, None], "b": [None, None]})
        self.assertEqual(DataSession(frame).profile().duplicate_count, 1)

    def test_text_only_frame_has_no_numeric_stats(self) -> None:
        frame = pd.DataFrame({"nazwa": ["a", "a", None]})
        profile = DataSession(frame).profile()

        self.assertTrue(profile.numeric_stats.empty)
        self.assertEqual(profile.duplicate_count, 1)
        self.assertEqual(int(profile.column_summary.loc["nazwa", "missing"]), 1)
        self.assertEqual(float(profile.column_summary.loc["nazwa", "missing_pct"]), 33.33)
        self.assertEqual(int(profile.text_stats.loc["nazwa", "unique"]), 1)
        self.assertEqual(profile.text_stats.loc["nazwa", "top"], "a")

    def test_numeric_only_frame_has_no_text_stats(self) -> None:
        frame = pd.DataFrame({"x": [1.0, 2.0, 2.0]})
        profile = DataSession(frame).profile()

        self.assertTrue(profile.text_stats.empty)
        self.assertEqual(profile.duplicate_count, 1)
        self.assertEqual(int(profile.column_summary.loc["x", "unique"]), 2)
        self.assertAlmostEqual(float(profile.numeric_stats.loc["x", "median"]), 2.0)

    def test_empty_numeric_column_does_not_break_profile(self) -> None:
        frame = pd.DataFrame({"x": pd.Series([None, None], dtype="float64")})
        profile = DataSession(frame).profile()
        stats = profile.numeric_stats.loc["x"]

        self.assertEqual(int(stats["count"]), 0)
        self.assertTrue(pd.isna(stats["mean"]))
        self.assertIn("średnia brak", profile.to_text())

    def test_text_report_contains_every_profile_part(self) -> None:
        text = DataSession(controlled_frame()).profile().to_text()

        self.assertIn("Rekordy: 4", text)
        self.assertIn("Kolumny: 3", text)
        self.assertIn("Nadmiarowe pełne duplikaty: 1", text)
        self.assertIn("typ", text)
        self.assertIn("25.00%", text)
        self.assertIn("Statystyki liczbowe:", text)
        self.assertIn("Statystyki tekstowe:", text)
        self.assertIn("mediana 10", text)
        self.assertIn("najczęstsza Łódź", text)

    def test_loaded_samples_profile_as_three_numeric_values(self) -> None:
        for file_name in ("comma_sample.csv", "semicolon_sample.csv"):
            with self.subTest(file_name=file_name):
                profile = DataSession.from_csv(FIXTURES / file_name).profile()
                self.assertEqual(profile.row_count, 3)
                self.assertEqual(profile.column_count, 3)
                self.assertEqual(profile.duplicate_count, 0)
                self.assertEqual(int(profile.column_summary.loc["wartosc", "missing"]), 0)
                self.assertAlmostEqual(float(profile.numeric_stats.loc["wartosc", "mean"]), 3.0)
                self.assertEqual(int(profile.text_stats.loc["nazwa", "unique"]), 3)


if __name__ == "__main__":
    unittest.main()
