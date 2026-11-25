"""Test osadzenia wykresu i zapisu wyników.

Zapisany CSV ma dać się wczytać z powrotem. PNG, PDF i SVG mają niepustą
zawartość. Zły wykres i zła ścieżka pokazują komunikat, a okno zostaje.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import DataApp
from session import DataSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.app = DataApp(report_error=lambda title, message: self.errors.append((title, message)))
        self.app.withdraw()

    def tearDown(self) -> None:
        self.app.destroy()

    def test_histogram_is_embedded_and_replaced(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        self.app.chart_type.set("histogram")
        self.app._update_chart_columns()
        self.app.draw_chart()

        self.assertEqual(self.errors, [])
        self.assertEqual(self.app._figure.axes[0].get_title(), "Rozkład: wartosc")
        first = self.app._chart_canvas.get_tk_widget()
        self.assertTrue(first.winfo_exists())

        self.app.draw_chart()
        self.assertFalse(first.winfo_exists())
        self.assertTrue(self.app._chart_canvas.get_tk_widget().winfo_exists())
        self.assertTrue(self.app.winfo_exists())

    def test_bar_scatter_and_correlation_use_matching_columns(self) -> None:
        self._load_two_numeric_columns()
        self.app.chart_type.set("słupkowy")
        self.app._update_chart_columns()
        self.assertEqual(list(self.app.chart_x["values"]), ["miasto"])
        self.assertEqual(str(self.app.chart_y["state"]), "disabled")
        self.app.draw_chart()
        self.assertEqual(self.app._figure.axes[0].get_title(), "Liczebność: miasto")

        self.app.chart_type.set("punktowy")
        self.app._update_chart_columns()
        self.assertEqual(self.app.chart_x.get(), "x")
        self.assertEqual(self.app.chart_y.get(), "y")
        self.app.draw_chart()
        self.assertEqual(self.app._figure.axes[0].get_title(), "Zależność: x i y")

        self.app.chart_type.set("korelacja")
        self.app._update_chart_columns()
        self.app.draw_chart()
        self.assertEqual(self.app._figure.axes[0].get_title(), "Korelacja kolumn liczbowych")
        self.assertIn("Nie oznacza, że jedna zmienna powoduje drugą", self.app.chart_note.cget("text"))
        self.assertEqual(self.errors, [])

    def test_constant_and_non_numeric_charts_are_blocked(self) -> None:
        self._load_two_numeric_columns()
        self.app.chart_type.set("histogram")
        self.app.chart_x.set("stala")
        self.app.draw_chart()
        self.assertIn("tylko jedną wartość", self.errors[-1][1])
        self.assertIsNone(self.app._figure)

        self.errors.clear()
        self.app.chart_x.set("miasto")
        self.app.draw_chart()
        self.assertIn("nie jest", self.errors[-1][1])
        self.assertIsNone(self.app._figure)
        self.assertTrue(self.app.winfo_exists())

    def test_saved_csv_can_be_loaded_again(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cleaned.csv"
            self.app.save_cleaned_csv(path)
            reloaded = DataSession.from_csv(path)

        self.assertEqual(list(reloaded.current.columns), ["nazwa", "wartosc", "opis"])
        self.assertEqual(len(reloaded.current), 3)
        self.assertEqual(reloaded.current.loc[2, "nazwa"], "Łódź")
        self.assertEqual(reloaded.current["wartosc"].tolist(), [1.5, 3.5, 4.0])

    def test_semicolon_export_keeps_decimal_comma(self) -> None:
        self.app.separator_box.set("średnik")
        self.app.load_csv_path(FIXTURES / "semicolon_sample.csv")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cleaned.csv"
            self.app.save_cleaned_csv(path)
            reloaded = DataSession.from_csv(path)

        self.assertEqual(reloaded.separator, ";")
        self.assertEqual(list(reloaded.current.columns), ["nazwa", "wartosc", "opis"])
        self.assertEqual(reloaded.current["wartosc"].tolist(), [1.5, 3.5, 4.0])

    def test_chart_exports_png_pdf_and_svg(self) -> None:
        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        self.app.draw_chart()
        with tempfile.TemporaryDirectory() as folder:
            png = Path(folder) / "wykres.png"
            pdf = Path(folder) / "wykres.pdf"
            svg = Path(folder) / "wykres.svg"
            self.app.save_chart(png)
            self.app.save_chart(pdf)
            self.app.save_chart(svg)
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG"))
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertIn("<svg", svg.read_text(encoding="utf-8"))
            self.assertGreater(png.stat().st_size, 0)
            self.assertGreater(pdf.stat().st_size, 0)
            self.assertGreater(svg.stat().st_size, 0)

    def test_bad_export_does_not_close_the_window(self) -> None:
        self.app.save_cleaned_csv(FIXTURES / "cleaned.csv")
        self.assertIn("Najpierw wczytaj plik CSV", self.errors[-1][1])

        self.app.load_csv_path(FIXTURES / "comma_sample.csv")
        with tempfile.TemporaryDirectory() as folder:
            self.app.save_chart(Path(folder) / "wykres.png")
            self.assertIn("Najpierw narysuj wykres", self.errors[-1][1])

            self.app.draw_chart()
            self.app.save_chart(Path(folder) / "wykres.txt")
            self.assertIn(".png, .pdf albo .svg", self.errors[-1][1])

            missing = Path(folder) / "brak" / "wynik.csv"
            self.app.save_cleaned_csv(missing)
            self.assertIn("Nie można zapisać pliku", self.errors[-1][1])
            self.assertFalse(missing.exists())

            self.app.save_cleaned_csv(folder)
            self.assertIn("nazwę pliku", self.errors[-1][1])

        self.assertIsNotNone(self.app._figure)
        self.assertTrue(self.app.winfo_exists())

    def _load_two_numeric_columns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pary.csv"
            path.write_text(
                "x,y,stala,miasto\n1,2,5,A\n2,4,5,A\n3,6,5,B\n4,8,5,B\n",
                encoding="utf-8",
            )
            self.app.load_csv_path(path)


if __name__ == "__main__":
    unittest.main()
