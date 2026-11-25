"""Test okna bez klikania: wczytanie, zły plik, profil i czyszczenie.

Komunikaty trafiają do listy, a nie do modalnego okna dialogowego.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import DataApp

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class GuiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.app = DataApp(report_error=lambda title, message: self.errors.append((title, message)))
        self.app.withdraw()

    def tearDown(self) -> None:
        self.app.destroy()

    def test_window_starts_without_data(self) -> None:
        self.assertIsNone(self.app.session)
        self.assertEqual(self.app.profile_text.get("1.0", "end").strip(), "Wczytaj plik, aby zobaczyć profil.")
        self.assertTrue(self.app.winfo_exists())

    def test_load_shows_preview_profile_and_columns(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")

        self.assertIsNotNone(self.app.session)
        self.assertEqual(len(self.app.preview.get_children()), 3)
        preview = " ".join(
            " ".join(self.app.preview.item(item, "values"))
            for item in self.app.preview.get_children()
        )
        self.assertIn("Łódź", preview)
        profile = self.app.profile_text.get("1.0", "end")
        self.assertIn("Rekordy: 3", profile)
        self.assertIn("Kolumny: 3", profile)
        self.assertIn("wartosc", self.app.range_column["values"])
        self.assertIn("nazwa", self.app.missing_column["values"])
        self.assertNotIn("nazwa", self.app.range_column["values"])
        self.assertEqual(self.errors, [])
        self.assertTrue(self.app.winfo_exists())

    def test_semicolon_file_and_cp1250_follow_the_controls(self) -> None:
        self.app.separator_box.set("średnik")
        self.app.load_csv_path(FIXTURES / "semicolon_sample.csv")
        self.assertEqual(self.app.session.separator, ";")
        self.assertEqual(list(self.app.session.current.columns), ["nazwa", "wartosc", "opis"])

        self.app.encoding_box.set("cp1250")
        self.app.separator_box.set("automatycznie")
        self.app.load_csv_path(FIXTURES / "cp1250_sample.csv")
        self.assertEqual(self.app.session.raw.loc[0, "nazwa"], "Zażółć")

    def test_bad_file_keeps_the_previous_session_and_the_window(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        rows_before = len(self.app.session.current)

        self.app.load_csv_path(FIXTURES / "bad_row.csv")

        self.assertEqual(len(self.app.session.current), rows_before)
        self.assertTrue(self.errors)
        self.assertIn("Wadliwy rekord", self.errors[-1][1])
        self.assertTrue(self.app.winfo_exists())

        self.app.load_csv_path(FIXTURES / "nie_ma_takiego_pliku.csv")
        self.assertEqual(len(self.app.session.current), rows_before)
        self.assertIn("Nie znaleziono pliku", self.errors[-1][1])

    def test_first_bad_file_does_not_create_a_session(self) -> None:
        self.app.load_csv_path(FIXTURES / "empty.csv")
        self.assertIsNone(self.app.session)
        self.assertTrue(self.errors)
        self.assertTrue(self.app.winfo_exists())

    def test_cleaning_updates_profile_audit_and_can_reset(self) -> None:
        self.app.load_csv_path(FIXTURES / "missing_and_duplicates.csv")
        self.app.remove_duplicates()

        self.assertEqual(len(self.app.session.current), 5)
        self.assertIn("Rekordy: 5", self.app.profile_text.get("1.0", "end"))
        self.assertIn("duplikaty", self.app.audit_box.get("1.0", "end"))
        self.assertIn("Usunięto pełne duplikaty: 1", self.app.status.cget("text"))

        self.app.restore_raw()
        self.assertEqual(len(self.app.session.current), 6)
        self.assertIn("Przywrócono dane surowe", self.app.status.cget("text"))
        self.assertIn("reset", self.app.audit_box.get("1.0", "end"))

    def test_missing_choice_does_not_change_data(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        before = self.app.session.current.copy()
        self.app.missing_column.set("")
        self.app.missing_strategy.set("uzupełnij braki (mediana albo moda)")

        self.app.apply_missing()

        self.assertTrue(self.app.session.current.equals(before))
        self.assertEqual(self.app.session.audit_log, [])
        self.assertIn("Wybierz kolumnę", self.errors[-1][1])

    def test_operation_without_file_does_not_close_the_window(self) -> None:
        self.app.remove_duplicates()
        self.app.apply_range_rule()
        self.assertIsNone(self.app.session)
        self.assertTrue(self.app.winfo_exists())
        self.assertIn("Najpierw wczytaj plik CSV", self.errors[-1][1])

    def test_marking_out_of_range_adds_a_column_to_the_lists(self) -> None:
        self.app.load_csv_path(FIXTURES / "out_of_range.csv")
        self.app.range_column.set("cena")
        self.app.range_min.delete(0, "end")
        self.app.range_min.insert(0, "0")
        self.app.range_max.delete(0, "end")
        self.app.range_max.insert(0, "100")
        self.app.range_action.set("oznacz")

        self.app.apply_range_rule()

        self.assertIn("cena_poza_zakresem", self.app.session.current.columns)
        self.assertIn("cena_poza_zakresem", self.app.missing_column["values"])
        self.assertEqual(len(self.app.session.current), 5)
        self.assertIn("zakres", self.app.audit_box.get("1.0", "end"))
        self.assertEqual(self.errors, [])

    def test_empty_range_bound_is_rejected(self) -> None:
        self.app.load_csv_path(FIXTURES / "out_of_range.csv")
        before = self.app.session.current.copy()
        self.app.range_min.delete(0, "end")
        self.app.range_max.delete(0, "end")

        self.app.apply_range_rule()

        self.assertTrue(self.app.session.current.equals(before))
        self.assertIn("Dolna granica jest pusta", self.errors[-1][1])


if __name__ == "__main__":
    unittest.main()
