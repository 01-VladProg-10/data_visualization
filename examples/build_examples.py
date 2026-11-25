"""Buduje pliki przykładowe z małego zbioru housing_sample.csv.

To nie jest oficjalny plik California Housing od prowadzącej. Skrypt pokazuje
kolejność decyzji: duplikaty, błędna długość geograficzna, braki, oznaczenie
outlierów bez ich usuwania.
"""

import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent
PROJECT = EXAMPLES.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from matplotlib.backends.backend_agg import FigureCanvasAgg

from charts import correlation_figure, histogram_figure, scatter_figure
from session import DataSession


def save_figure(figure, path: Path) -> None:
    FigureCanvasAgg(figure)
    figure.savefig(path)


def main() -> None:
    session = DataSession.from_csv(EXAMPLES / "housing_sample.csv")
    session.drop_exact_duplicates()
    session.apply_range("longitude", -125, -114, "usun")
    session.fill_missing("total_bedrooms")
    session.fill_missing("ocean_proximity")
    session.apply_iqr("median_income", "oznacz")
    session.current.to_csv(EXAMPLES / "cleaned.csv", index=False)
    session.export_audit(EXAMPLES / "raport_zmian.txt")

    save_figure(
        histogram_figure(session.current, "median_income"),
        EXAMPLES / "histogram_median_income.png",
    )
    save_figure(
        scatter_figure(session.current, "median_income", "median_house_value"),
        EXAMPLES / "scatter_income_value.png",
    )
    figure, note = correlation_figure(session.current)
    save_figure(figure, EXAMPLES / "korelacja.png")
    (EXAMPLES / "korelacja.txt").write_text(note + "\n", encoding="utf-8")
    print(session.audit_text())
    print()
    print(note)
    print("rekordy", len(session.current), "kolumny", list(session.current.columns))


if __name__ == "__main__":
    main()
