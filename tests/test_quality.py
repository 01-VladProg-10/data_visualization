"""Testy reguł jakości na małych, ręcznie sprawdzonych danych.

W pliku out_of_range.csv zakres od 0 do 100 obejmuje ceny 0, 10 i 100.
Poza zakresem zostaje tylko 150. Pusta komórka jest brakiem, a nie wartością
spoza zakresu. Reguły niczego nie zmieniają w tabeli.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session import DataSession, QualityRuleError

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class QualityRuleTests(unittest.TestCase):
    def test_range_on_file_finds_only_the_value_above_maximum(self) -> None:
        session = DataSession.from_csv(FIXTURES / "out_of_range.csv")
        before = session.current.copy()

        report = session.range_report("cena", 0, 100)

        self.assertEqual(report.rule_name, "zakres")
        self.assertEqual(report.affected, 1)
        self.assertEqual(report.matched.tolist(), [False, False, False, True, False])
        self.assertEqual(report.lower, 0)
        self.assertEqual(report.upper, 100)
        self.assertIn("Liczba rekordów poza zakresem: 1", report.to_text())
        self.assertIn("Dane nie zostały zmienione", report.to_text())
        self.assertIn("Indeksy wierszy: 3", report.to_text())
        self.assertTrue(session.current.equals(before))
        self.assertTrue(session.raw.equals(before))
        self.assertEqual(session.audit_log, [])

    def test_range_bounds_are_inclusive_and_missing_is_ignored(self) -> None:
        frame = pd.DataFrame({"cena": [0.0, 10.0, 100.0, None]})
        report = DataSession(frame).range_report("cena", 0, 100)
        self.assertEqual(report.affected, 0)
        self.assertEqual(report.matched.tolist(), [False, False, False, False])

    def test_equal_bounds_flag_only_other_numbers(self) -> None:
        frame = pd.DataFrame({"cena": [5.0, 6.0]})
        report = DataSession(frame).range_report("cena", 5, 5)
        self.assertEqual(report.matched.tolist(), [False, True])

    def test_range_does_not_touch_another_column(self) -> None:
        frame = pd.DataFrame({"cena": [1.0, 100.0], "metraz": [40.0, 55.0]})
        session = DataSession(frame)
        report = session.range_report("cena", 0, 10)

        self.assertEqual(report.matched.tolist(), [False, True])
        self.assertEqual(session.current["metraz"].tolist(), [40.0, 55.0])
        self.assertEqual(session.current["cena"].tolist(), [1.0, 100.0])

    def test_iqr_flags_both_tails_with_hand_calculated_fences(self) -> None:
        # Posortowane: -100, 1, 2, 3, 4, 5, 100.
        # Q1 = 1.5, Q3 = 4.5, IQR = 3, mnożnik 1.5.
        # Przedział: od -3 do 9. Poza nim zostają -100 i 100.
        frame = pd.DataFrame({"wynik": [-100, 1, 2, 3, 4, 5, 100]})
        session = DataSession(frame)
        before = session.current.copy()

        report = session.iqr_report("wynik")

        self.assertEqual(report.rule_name, "iqr")
        self.assertEqual(report.affected, 2)
        self.assertEqual(report.matched.tolist(), [True, False, False, False, False, False, True])
        self.assertAlmostEqual(report.lower, -3.0)
        self.assertAlmostEqual(report.upper, 9.0)
        self.assertIn("mnożnik 1.5", report.description)
        self.assertIn("nie jest automatycznie błędem", report.to_text())
        self.assertTrue(session.current.equals(before))
        self.assertEqual(session.audit_log, [])

    def test_larger_iqr_factor_can_keep_a_mild_outlier(self) -> None:
        frame = pd.DataFrame({"wynik": [1, 2, 3, 4, 5, 10]})
        session = DataSession(frame)

        narrow = session.iqr_report("wynik", factor=1.5)
        wide = session.iqr_report("wynik", factor=3)

        self.assertEqual(narrow.matched.tolist(), [False, False, False, False, False, True])
        self.assertEqual(wide.affected, 0)
        self.assertTrue(session.current.equals(frame))

    def test_missing_value_is_not_an_iqr_outlier(self) -> None:
        frame = pd.DataFrame({"wynik": [1, 2, 3, 4, 5, 100, None]})
        report = DataSession(frame).iqr_report("wynik")
        self.assertEqual(report.matched.tolist()[-1], False)
        self.assertEqual(report.affected, 1)

    def test_constant_column_has_no_iqr_outliers(self) -> None:
        frame = pd.DataFrame({"wynik": [5, 5, 5, 5]})
        report = DataSession(frame).iqr_report("wynik")

        self.assertEqual(report.affected, 0)
        self.assertEqual(report.lower, 5)
        self.assertEqual(report.upper, 5)
        self.assertIn("IQR=0", report.to_text())
        self.assertIn("Dane nie zostały zmienione", report.to_text())

    def test_column_without_numbers_explains_why_iqr_stopped(self) -> None:
        frame = pd.DataFrame({"wynik": pd.Series([None, None], dtype="float64")})
        report = DataSession(frame).iqr_report("wynik")

        self.assertEqual(report.affected, 0)
        self.assertIsNone(report.lower)
        self.assertIn("wybierz inną", report.to_text())

    def test_invalid_rule_parameters_are_rejected(self) -> None:
        session = DataSession(pd.DataFrame({"cena": [1.0, 2.0], "miasto": ["A", "B"]}))
        before = session.current.copy()
        cases = [
            lambda: session.range_report("brak", 0, 1),
            lambda: session.range_report("miasto", 0, 1),
            lambda: session.range_report("cena", 10, 0),
            lambda: session.range_report("cena", "dolna", 1),
            lambda: session.range_report("cena", True, 1),
            lambda: session.iqr_report("miasto"),
            lambda: session.iqr_report("cena", factor=0),
            lambda: session.iqr_report("cena", factor=-1),
            lambda: session.iqr_report("cena", factor="dwa"),
        ]
        for call in cases:
            with self.subTest(call=call):
                with self.assertRaises(QualityRuleError):
                    call()
        self.assertTrue(session.current.equals(before))

    def test_messages_tell_how_to_fix_the_rule(self) -> None:
        session = DataSession(
            pd.DataFrame({"cena": [1.0], "flaga": [True], "miasto": ["A"]})
        )
        with self.assertRaises(QualityRuleError) as missing:
            session.range_report("inne", 0, 1)
        self.assertIn("Wybierz kolumnę z listy", str(missing.exception))

        with self.assertRaises(QualityRuleError) as text_column:
            session.range_report("miasto", 0, 1)
        self.assertIn("Wybierz kolumnę z liczbami", str(text_column.exception))

        with self.assertRaises(QualityRuleError) as flag_column:
            session.iqr_report("flaga")
        self.assertIn("nie jest liczbowa", str(flag_column.exception))

        with self.assertRaises(QualityRuleError) as bounds:
            session.range_report("cena", 5, 1)
        self.assertIn("Zamień granice", str(bounds.exception))

        with self.assertRaises(QualityRuleError) as factor:
            session.iqr_report("cena", factor=0)
        self.assertIn("1.5", str(factor.exception))

    def test_repeated_column_name_is_rejected(self) -> None:
        frame = pd.DataFrame([[1, 2]], columns=["cena", "cena"])
        with self.assertRaises(QualityRuleError) as caught:
            DataSession(frame).range_report("cena", 0, 10)
        self.assertIn("więcej niż raz", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
