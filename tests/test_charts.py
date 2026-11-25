"""Testy wykresów na małych ramkach o znanym układzie.

Histogram, wykres pudełkowy i punktowy wymagają liczb, które się zmieniają.
Wykres słupkowy jest do kategorii. Korelacja ignoruje tekst i kolumny stałe,
a opis mówi wprost, że współczynnik nie jest przyczyną.
"""

import sys
import unittest
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from charts import (
    ChartError,
    bar_figure,
    box_figure,
    correlation_figure,
    histogram_figure,
    scatter_figure,
)
from session import DataSession

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": [1, 2, 3, 4, 5],
            "y": [2, 4, 6, 8, 10],
            "z": [1, 1, 1, 2, 10],
            "stala": [3, 3, 3, 3, 3],
            "miasto": ["A", "A", "B", "B", "C"],
        }
    )


def assert_draws(test: unittest.TestCase, fig: object) -> None:
    FigureCanvasAgg(fig)
    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    test.assertGreater(buffer.tell(), 0)


class ChartTests(unittest.TestCase):
    def test_histogram_has_labels_and_does_not_change_data(self) -> None:
        frame = sample_frame()
        before = frame.copy()

        fig = histogram_figure(frame, "x")

        ax = fig.axes[0]
        self.assertEqual(ax.get_title(), "Rozkład: x")
        self.assertEqual(ax.get_xlabel(), "x")
        self.assertEqual(ax.get_ylabel(), "Liczba rekordów")
        assert_draws(self, fig)
        self.assertTrue(frame.equals(before))

    def test_scatter_box_and_bar_have_readable_labels(self) -> None:
        frame = sample_frame()

        scatter = scatter_figure(frame, "x", "y")
        self.assertEqual(scatter.axes[0].get_title(), "Zależność: x i y")
        self.assertEqual(scatter.axes[0].get_xlabel(), "x")
        self.assertEqual(scatter.axes[0].get_ylabel(), "y")
        assert_draws(self, scatter)

        box = box_figure(frame, "x")
        self.assertEqual(box.axes[0].get_title(), "Wykres pudełkowy: x")
        self.assertEqual(box.axes[0].get_ylabel(), "x")
        assert_draws(self, box)

        bar = bar_figure(frame, "miasto")
        self.assertEqual(bar.axes[0].get_title(), "Liczebność: miasto")
        self.assertEqual(bar.axes[0].get_xlabel(), "miasto")
        self.assertEqual(bar.axes[0].get_ylabel(), "Liczba rekordów")
        heights = sorted(patch.get_height() for patch in bar.axes[0].patches)
        self.assertEqual(heights, [1, 2, 2])
        assert_draws(self, bar)

    def test_wrong_column_type_is_blocked_with_a_hint(self) -> None:
        frame = sample_frame()
        cases = [
            lambda: histogram_figure(frame, "miasto"),
            lambda: box_figure(frame, "miasto"),
            lambda: scatter_figure(frame, "miasto", "x"),
            lambda: scatter_figure(frame, "x", "miasto"),
            lambda: bar_figure(frame, "x"),
            lambda: histogram_figure(frame, "stala"),
            lambda: box_figure(frame, "stala"),
            lambda: scatter_figure(frame, "stala", "x"),
            lambda: scatter_figure(frame, "x", "x"),
        ]
        for call in cases:
            with self.subTest(call=call):
                with self.assertRaises(ChartError) as caught:
                    call()
                message = str(caught.exception)
                self.assertTrue(
                    "Wybierz" in message or "Wykres punktowy potrzebuje" in message
                )

    def test_messages_name_the_problem(self) -> None:
        frame = sample_frame()
        with self.assertRaises(ChartError) as missing:
            histogram_figure(frame, "brak")
        self.assertIn("Wybierz kolumnę z listy", str(missing.exception))

        with self.assertRaises(ChartError) as text_column:
            histogram_figure(frame, "miasto")
        self.assertIn("Wybierz kolumnę z liczbami", str(text_column.exception))

        with self.assertRaises(ChartError) as constant:
            box_figure(frame, "stala")
        self.assertIn("tylko jedną wartość", str(constant.exception))

        with self.assertRaises(ChartError) as numeric_bar:
            bar_figure(frame, "y")
        self.assertIn("histogram", str(numeric_bar.exception))

        with self.assertRaises(ChartError) as same_axis:
            scatter_figure(frame, "x", "x")
        self.assertIn("dwóch różnych kolumn", str(same_axis.exception))

        with self.assertRaises(ChartError) as too_few_pairs:
            scatter_figure(pd.DataFrame({"x": [1, None], "y": [2, 3]}), "x", "y")
        self.assertIn("Uzupełnij braki", str(too_few_pairs.exception))

    def test_correlation_states_that_it_is_not_causation(self) -> None:
        frame = sample_frame()
        fig, note = correlation_figure(frame)

        self.assertEqual(fig.axes[0].get_title(), "Korelacja kolumn liczbowych")
        self.assertEqual(fig.axes[0].get_xlabel(), "Zmienna")
        self.assertEqual(fig.axes[0].get_ylabel(), "Zmienna")
        labels = [tick.get_text() for tick in fig.axes[0].get_xticklabels()]
        self.assertIn("x", labels)
        self.assertIn("y", labels)
        self.assertNotIn("miasto", labels)
        self.assertNotIn("stala", labels)
        self.assertIn("x i y", note)
        self.assertIn("r = 1.00", note)
        self.assertIn("Nie oznacza, że jedna zmienna powoduje drugą", note)
        self.assertIn("Kolumny stałe pominięto", note)
        assert_draws(self, fig)

    def test_correlation_needs_two_changing_numeric_columns(self) -> None:
        with self.assertRaises(ChartError) as caught:
            correlation_figure(pd.DataFrame({"miasto": ["A", "B"], "stala": [1, 1]}))
        self.assertIn("co najmniej dwóch kolumn", str(caught.exception))

    def test_same_functions_work_before_and_after_cleaning(self) -> None:
        loaded = DataSession.from_csv(FIXTURES / "missing_and_duplicates.csv")
        raw_figure = histogram_figure(loaded.raw, "cena")
        loaded.drop_missing("cena")
        current_figure = histogram_figure(loaded.current, "cena")
        self.assertEqual(raw_figure.axes[0].get_title(), "Rozkład: cena")
        self.assertEqual(current_figure.axes[0].get_title(), "Rozkład: cena")

        session = DataSession(sample_frame())
        raw_corr, raw_note = correlation_figure(session.raw)
        session.apply_iqr("z", "usun")
        current_corr, current_note = correlation_figure(session.current)
        self.assertIn("Nie oznacza, że jedna zmienna powoduje drugą", raw_note)
        self.assertIn("Nie oznacza, że jedna zmienna powoduje drugą", current_note)
        assert_draws(self, raw_figure)
        assert_draws(self, current_figure)
        assert_draws(self, raw_corr)
        assert_draws(self, current_corr)


if __name__ == "__main__":
    unittest.main()
