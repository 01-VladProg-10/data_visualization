"""Testy wczytywania CSV na małych, ręcznie sprawdzonych plikach.

Oczekiwany wynik plików comma_sample.csv i semicolon_sample.csv:
trzy kolumny (nazwa, wartosc, opis), trzy rekordy, a kolumna wartosc
to liczby 1.5, 3.5 i 4.0. Pole z separatorem w cudzysłowie zostaje jedną
wartością. Pliki błędne nie tworzą sesji i zwracają komunikat z podpowiedzią.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session import DataLoadError, DataSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LoadCsvTests(unittest.TestCase):
    def test_comma_and_semicolon_files_have_the_same_shape(self) -> None:
        expected_values = [1.5, 3.5, 4.0]
        cases = [
            ("comma_sample.csv", ",", "Nowak, Anna", "opis, z przecinkiem"),
            ("semicolon_sample.csv", ";", "Nowak; Anna", "opis; ze srednikiem"),
        ]
        for file_name, separator, quoted_name, quoted_note in cases:
            with self.subTest(file_name=file_name):
                session = DataSession.from_csv(FIXTURES / file_name)
                self.assertEqual(session.separator, separator)
                self.assertEqual(session.encoding, "utf-8")
                self.assertEqual(list(session.raw.columns), ["nazwa", "wartosc", "opis"])
                self.assertEqual(len(session.raw), 3)
                self.assertEqual(session.raw["wartosc"].tolist(), expected_values)
                self.assertEqual(session.raw.loc[1, "nazwa"], quoted_name)
                self.assertEqual(session.raw.loc[1, "opis"], quoted_note)
                self.assertEqual(session.raw.loc[2, "nazwa"], "Łódź")
                self.assertIn("rekordy: 3", session.load_summary())
                self.assertIn("kolumny: 3", session.load_summary())

    def test_single_column_file_without_separator(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one_column.csv"
            path.write_text("nazwa\nAla\nOla\n", encoding="utf-8")
            session = DataSession.from_csv(path)
        self.assertEqual(session.separator, ",")
        self.assertEqual(list(session.raw.columns), ["nazwa"])
        self.assertEqual(session.raw["nazwa"].tolist(), ["Ala", "Ola"])

    def test_explicit_separator_is_not_overridden(self) -> None:
        session = DataSession.from_csv(FIXTURES / "semicolon_sample.csv", separator=";")
        self.assertEqual(session.separator, ";")
        self.assertEqual(len(session.raw.columns), 3)

    def test_unsupported_separator_is_rejected(self) -> None:
        with self.assertRaises(DataLoadError) as caught:
            DataSession.from_csv(FIXTURES / "comma_sample.csv", separator="|")
        self.assertIn("przecinek albo średnik", str(caught.exception))

    def test_ambiguous_header_asks_for_manual_separator(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ambiguous.csv"
            path.write_text("a,b;c\n1,2;3\n", encoding="utf-8")
            with self.assertRaises(DataLoadError) as caught:
                DataSession.from_csv(path)
        self.assertIn("Wybierz separator ręcznie", str(caught.exception))

    def test_bad_row_does_not_create_session(self) -> None:
        with self.assertRaises(DataLoadError) as caught:
            DataSession.from_csv(FIXTURES / "bad_row.csv")
        message = str(caught.exception)
        self.assertIn("Wadliwy rekord nr 2", message)
        self.assertIn("cudzysłów", message)

    def test_too_few_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "short.csv"
            path.write_text("nazwa,wartosc\nA,1\nB\n", encoding="utf-8")
            with self.assertRaises(DataLoadError) as caught:
                DataSession.from_csv(path)
        self.assertIn("Wadliwy rekord nr 2", str(caught.exception))
        self.assertIn("liczba pól w rekordzie to 1", str(caught.exception))

    def test_empty_file_and_header_only_are_rejected(self) -> None:
        for file_name in ("empty.csv", "header_only.csv"):
            with self.subTest(file_name=file_name):
                with self.assertRaises(DataLoadError) as caught:
                    DataSession.from_csv(FIXTURES / file_name)
                self.assertIn("rekord", str(caught.exception).lower())

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(DataLoadError) as caught:
            DataSession.from_csv(FIXTURES / "nie_ma_takiego_pliku.csv")
        self.assertIn("Nie znaleziono pliku", str(caught.exception))

    def test_cp1250_file_needs_explicit_encoding(self) -> None:
        path = FIXTURES / "cp1250_sample.csv"
        with self.assertRaises(DataLoadError) as caught:
            DataSession.from_csv(path)
        self.assertIn("cp1250", str(caught.exception))

        session = DataSession.from_csv(path, encoding="cp1250")
        self.assertEqual(session.encoding, "cp1250")
        self.assertEqual(session.raw.loc[0, "nazwa"], "Zażółć")
        self.assertEqual(session.raw.loc[0, "wartosc"], 1.5)

    def test_unknown_encoding_name_is_rejected(self) -> None:
        with self.assertRaises(DataLoadError) as caught:
            DataSession.from_csv(FIXTURES / "comma_sample.csv", encoding="nie-ma-takiego")
        self.assertIn("utf-8", str(caught.exception))

    def test_raw_and_current_are_independent_copies(self) -> None:
        frame = pd.DataFrame({"miasto": ["Łódź"], "wartosc": [1.0]})
        session = DataSession(frame)
        self.assertTrue(session.current.equals(session.raw))

        frame.loc[0, "miasto"] = "Kraków"
        session.current.loc[0, "miasto"] = "Gdańsk"

        self.assertEqual(session.raw.loc[0, "miasto"], "Łódź")
        self.assertEqual(session.current.loc[0, "miasto"], "Gdańsk")
        self.assertFalse(session.current.equals(session.raw))
        self.assertEqual(session.audit_log, [])

    def test_preview_returns_copy_of_first_rows(self) -> None:
        session = DataSession.from_csv(FIXTURES / "comma_sample.csv")
        view = session.preview(2)
        self.assertEqual(len(view), 2)
        self.assertEqual(list(view.columns), ["nazwa", "wartosc", "opis"])

        view.loc[0, "nazwa"] = "zmieniony podgląd"
        self.assertNotEqual(session.current.loc[0, "nazwa"], "zmieniony podgląd")

        with self.assertRaises(ValueError):
            session.preview(0)

    def test_preview_can_ask_for_more_rows_than_exist(self) -> None:
        session = DataSession.from_csv(FIXTURES / "comma_sample.csv")
        self.assertEqual(len(session.preview(100)), 3)


if __name__ == "__main__":
    unittest.main()
