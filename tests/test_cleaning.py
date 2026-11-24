"""Testy czyszczenia na małym, ręcznie sprawdzonym pliku.

missing_and_duplicates.csv ma 6 rekordów. Drugi wiersz jest pełną kopią
pierwszego. Wiersz A,1.0,inne nie jest duplikatem, bo opis się różni.
Pusta cena to brak liczbowy, puste miasto to brak tekstowy. Operacja na
jednej kolumnie nie zmienia wartości w pozostałych.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session import CleaningError, DataSession, QualityRuleError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def load_sample() -> DataSession:
    return DataSession.from_csv(FIXTURES / "missing_and_duplicates.csv")


class CleaningTests(unittest.TestCase):
    def test_only_full_duplicate_pair_is_removed(self) -> None:
        session = load_sample()
        raw = session.raw.copy()

        removed = session.drop_exact_duplicates()

        self.assertEqual(removed, 1)
        self.assertEqual(len(session.current), 5)
        self.assertEqual(session.profile().duplicate_count, 0)
        self.assertEqual(int((session.current["opis"] == "inne").sum()), 1)
        self.assertEqual(int((session.current["opis"] == "dom").sum()), 3)
        self.assertEqual(int((session.current["opis"] == "blok").sum()), 1)
        self.assertTrue(session.raw.equals(raw))
        entry = session.audit_log[-1]
        self.assertEqual(entry.operation, "duplikaty")
        self.assertEqual(entry.rows_before, 6)
        self.assertEqual(entry.rows_after, 5)
        self.assertEqual(entry.affected, 1)
        self.assertIsNotNone(TIMESTAMP.match(entry.timestamp))

    def test_same_duplicate_removal_after_reset(self) -> None:
        session = load_sample()
        session.drop_exact_duplicates()
        first = session.current.copy()

        session.reset()
        session.drop_exact_duplicates()

        self.assertTrue(session.current.equals(first))
        self.assertEqual(session.audit_log[-1].operation, "duplikaty")
        self.assertEqual(session.audit_log[-2].operation, "reset")

    def test_numeric_and_text_imputation_touch_only_the_chosen_column(self) -> None:
        numeric = load_sample()
        others = numeric.current.drop(columns=["cena"])
        filled = numeric.fill_missing("cena")

        self.assertEqual(filled, 1)
        self.assertEqual(float(numeric.current.loc[3, "cena"]), 1.0)
        self.assertTrue(pd.isna(numeric.current.loc[5, "miasto"]))
        self.assertTrue(numeric.current.drop(columns=["cena"]).equals(others))
        self.assertIn("metoda=mediana", numeric.audit_log[-1].parameters)
        self.assertEqual(numeric.audit_log[-1].rows_before, 6)
        self.assertEqual(numeric.audit_log[-1].rows_after, 6)

        text = load_sample()
        others = text.current.drop(columns=["miasto"])
        filled = text.fill_missing("miasto")

        self.assertEqual(filled, 1)
        self.assertEqual(text.current.loc[5, "miasto"], "A")
        self.assertTrue(pd.isna(text.current.loc[3, "cena"]))
        self.assertTrue(text.current.drop(columns=["miasto"]).equals(others))
        self.assertIn("metoda=moda", text.audit_log[-1].parameters)
        self.assertIn("wartość=A", text.audit_log[-1].parameters)

    def test_dropping_missing_values_keeps_the_other_cells(self) -> None:
        session = load_sample()
        opis = session.current["opis"].copy()

        removed = session.drop_missing("cena")

        self.assertEqual(removed, 1)
        self.assertEqual(len(session.current), 5)
        self.assertFalse(session.current["cena"].isna().any())
        self.assertTrue(session.current["opis"].equals(opis.loc[session.current.index]))
        self.assertTrue(pd.isna(session.current.loc[5, "miasto"]))

        fresh = load_sample()
        removed_any = fresh.drop_missing()
        self.assertEqual(removed_any, 2)
        self.assertEqual(len(fresh.current), 4)
        self.assertFalse(fresh.current.isna().any().any())

    def test_range_remove_mark_and_leave_do_not_change_raw(self) -> None:
        session = DataSession.from_csv(FIXTURES / "out_of_range.csv")
        raw = session.raw.copy()

        session.apply_range("cena", 0, 100, "zostaw")
        self.assertTrue(session.current.equals(raw))
        self.assertEqual(session.audit_log[-1].affected, 0)
        self.assertIn("wykryto=1", session.audit_log[-1].parameters)
        self.assertIn("decyzja=zostaw", session.audit_log[-1].parameters)

        marked = DataSession.from_csv(FIXTURES / "out_of_range.csv")
        before = marked.current.copy()
        affected = marked.apply_range("cena", 0, 100, "oznacz")
        self.assertEqual(affected, 1)
        self.assertEqual(
            marked.current["cena_poza_zakresem"].tolist(),
            [False, False, False, True, False],
        )
        self.assertTrue(marked.current[["miasto", "cena"]].equals(before))
        self.assertTrue((marked.raw["cena"] == 150).any())

        removed = DataSession.from_csv(FIXTURES / "out_of_range.csv")
        deleted = removed.apply_range("cena", 0, 100, "usun")
        self.assertEqual(deleted, 1)
        self.assertEqual(len(removed.current), 4)
        self.assertFalse((removed.current["cena"] == 150).any())
        self.assertTrue(removed.current["cena"].isna().any())
        self.assertTrue(removed.raw.equals(raw))

    def test_iqr_actions_use_the_fences_and_leave_other_columns(self) -> None:
        frame = pd.DataFrame(
            {
                "wynik": [-100, 1, 2, 3, 4, 5, 100],
                "inne": [7, 7, 7, 7, 7, 7, 7],
            }
        )
        session = DataSession(frame)
        raw = session.raw.copy()

        left = DataSession(frame)
        left.apply_iqr("wynik", "zostaw")
        self.assertTrue(left.current.equals(left.raw))
        self.assertIn("wykryto=2", left.audit_log[-1].parameters)

        marked = DataSession(frame)
        marked.apply_iqr("wynik", "oznacz")
        self.assertEqual(
            marked.current["wynik_odstajaca"].tolist(),
            [True, False, False, False, False, False, True],
        )
        self.assertEqual(marked.current["wynik"].tolist(), frame["wynik"].tolist())
        self.assertEqual(marked.current["inne"].tolist(), [7, 7, 7, 7, 7, 7, 7])

        clipped = DataSession(frame)
        changed = clipped.apply_iqr("wynik", "przytnij")
        self.assertEqual(changed, 2)
        self.assertEqual(clipped.current["wynik"].tolist(), [-3, 1, 2, 3, 4, 5, 9])
        self.assertEqual(clipped.current["inne"].tolist(), [7, 7, 7, 7, 7, 7, 7])

        removed = DataSession(frame)
        deleted = removed.apply_iqr("wynik", "usun")
        self.assertEqual(deleted, 2)
        self.assertEqual(removed.current["wynik"].tolist(), [1, 2, 3, 4, 5])
        self.assertEqual(removed.current["inne"].tolist(), [7, 7, 7, 7, 7])
        self.assertTrue(session.raw.equals(raw))

    def test_reset_restores_raw_after_several_operations(self) -> None:
        session = load_sample()
        raw = session.raw.copy()
        session.fill_missing("cena")
        session.apply_iqr("cena", "oznacz")
        self.assertIn("cena_odstajaca", session.current.columns)

        session.reset()

        self.assertTrue(session.current.equals(raw))
        self.assertNotIn("cena_odstajaca", session.current.columns)
        self.assertEqual(session.audit_log[-1].operation, "reset")
        self.assertEqual(session.audit_log[-1].rows_after, 6)
        self.assertTrue(session.raw.equals(raw))

    def test_audit_file_contains_before_and_after_counts(self) -> None:
        session = load_sample()
        session.drop_exact_duplicates()
        session.drop_missing("cena")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "raport.txt"
            session.export_audit(path)
            saved = path.read_text(encoding="utf-8")

        self.assertEqual(saved, session.audit_text() + "\n")
        self.assertIn("rekordy przed: 6, po: 5, dotknięte: 1", saved)
        self.assertIn("rekordy przed: 5, po: 4, dotknięte: 1", saved)
        self.assertIn("duplikaty", saved)
        self.assertIn("braki", saved)

    def test_invalid_cleaning_does_not_change_data_or_log(self) -> None:
        session = load_sample()
        before = session.current.copy()

        with self.assertRaises(CleaningError) as missing_column:
            session.fill_missing("brak")
        self.assertIn("Wybierz kolumnę z listy", str(missing_column.exception))

        with self.assertRaises(CleaningError) as empty_number:
            empty_numbers = pd.DataFrame(
                {"cena": pd.Series([None, None], dtype="float64"), "miasto": ["A", "B"]}
            )
            DataSession(empty_numbers).fill_missing("cena")
        self.assertIn("mediany", str(empty_number.exception))

        with self.assertRaises(CleaningError) as empty_text:
            DataSession(pd.DataFrame({"miasto": [None, None]})).fill_missing("miasto")
        self.assertIn("najczęstszej", str(empty_text.exception))

        with self.assertRaises(CleaningError) as bad_action:
            session.apply_range("cena", 0, 10, "przytnij")
        self.assertIn("zostaw, oznacz, usun", str(bad_action.exception))

        with self.assertRaises(QualityRuleError):
            session.apply_range("miasto", 0, 10, "usun")

        with self.assertRaises(CleaningError) as no_fence:
            DataSession(pd.DataFrame({"cena": pd.Series([None, None], dtype="float64")})).apply_iqr(
                "cena",
                "przytnij",
            )
        self.assertIn("zostaw dane", str(no_fence.exception))

        self.assertTrue(session.current.equals(before))
        self.assertEqual(session.audit_log, [])
        self.assertTrue(session.raw.equals(before))

    def test_export_rejects_a_directory_and_a_missing_folder(self) -> None:
        session = load_sample()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(CleaningError) as directory:
                session.export_audit(folder)
            self.assertIn("nazwę pliku", str(directory.exception))

            missing = Path(folder) / "brak" / "raport.txt"
            with self.assertRaises(CleaningError) as missing_folder:
                session.export_audit(missing)
            self.assertIn("Nie można zapisać raportu", str(missing_folder.exception))


if __name__ == "__main__":
    unittest.main()
