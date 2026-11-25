"""Wykresy: histogram, punktowy, pudełkowy, słupkowy i korelacja.

Funkcje zwracają obiekt Figure. Ten sam rysunek można później osadzić w oknie
i zapisać do pliku. Na wejściu jest ramka, więc wykres da się narysować
dla danych surowych albo dla danych po czyszczeniu.
"""

import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure


class ChartError(Exception):
    """Nie da się narysować wykresu. Treść wyjątku mówi, co zmienić."""


def histogram_figure(frame: pd.DataFrame, column: str) -> Figure:
    """Histogram jednej kolumny liczbowej, która nie jest stała."""
    values = _numeric_values(frame, column, "histogram")
    _require_variation(values, column)
    fig, ax = _new_figure()
    sns.histplot(values, ax=ax, bins="auto", color="#4C78A8", edgecolor="white")
    ax.set_title(f"Rozkład: {column}")
    ax.set_xlabel(column)
    ax.set_ylabel("Liczba rekordów")
    fig.tight_layout()
    return fig


def scatter_figure(frame: pd.DataFrame, x: str, y: str) -> Figure:
    """Wykres punktowy dwóch różnych kolumn liczbowych."""
    if x == y:
        raise ChartError(
            "Wykres punktowy potrzebuje dwóch różnych kolumn. "
            "Wybierz inną zmienną na jedną z osi."
        )
    _require_numeric_type(frame, x, "punktowy")
    _require_numeric_type(frame, y, "punktowy")
    pairs = pd.DataFrame(
        {
            x: pd.to_numeric(frame[x], errors="coerce"),
            y: pd.to_numeric(frame[y], errors="coerce"),
        }
    ).dropna()
    if len(pairs) < 2:
        raise ChartError(
            "Za mało kompletnych par liczb do wykresu punktowego. "
            "Uzupełnij braki albo wybierz inne kolumny."
        )
    _require_variation(pairs[x], x)
    _require_variation(pairs[y], y)

    fig, ax = _new_figure()
    sns.scatterplot(data=pairs, x=x, y=y, ax=ax, color="#4C78A8")
    ax.set_title(f"Zależność: {x} i {y}")
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    fig.tight_layout()
    return fig


def box_figure(frame: pd.DataFrame, column: str) -> Figure:
    """Wykres pudełkowy jednej kolumny liczbowej, która nie jest stała."""
    values = _numeric_values(frame, column, "pudełkowy")
    _require_variation(values, column)
    fig, ax = _new_figure()
    # Ta wersja Seaborna przekazuje do Matplotlib argument, który jest już wycofany.
    # Pudełko rysuje więc sam Matplotlib, a pozostałe wykresy Seaborn.
    ax.boxplot(values.to_numpy(), orientation="vertical", patch_artist=True)
    ax.set_title(f"Wykres pudełkowy: {column}")
    ax.set_xlabel("")
    ax.set_ylabel(column)
    fig.tight_layout()
    return fig


def bar_figure(frame: pd.DataFrame, column: str) -> Figure:
    """Wykres słupkowy liczebności kategorii.

    Kolumna liczbowa jest tu odrzucana: do rozkładu liczb służy histogram.
    """
    values = _require_column(frame, column)
    if _is_numeric_column(values):
        raise ChartError(
            f"Wykres słupkowy pokazuje kategorie, a '{column}' jest liczbowa. "
            "Wybierz kolumnę tekstową albo narysuj histogram."
        )
    present = values.dropna()
    if present.empty:
        raise ChartError(
            f"Kolumna '{column}' nie zawiera wartości. Uzupełnij dane albo wybierz inną kolumnę."
        )
    category_count = int(present.nunique())
    if category_count > 20:
        raise ChartError(
            f"Kolumna '{column}' ma {category_count} różnych wartości. "
            "Wykres słupkowy byłby nieczytelny. Wybierz kolumnę z mniejszą liczbą kategorii."
        )

    counts = present.astype(str).value_counts()
    fig, ax = _new_figure()
    sns.barplot(x=list(counts.index), y=list(counts.to_numpy()), ax=ax, color="#4C78A8")
    ax.set_title(f"Liczebność: {column}")
    ax.set_xlabel(column)
    ax.set_ylabel("Liczba rekordów")
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    return fig


def correlation_figure(frame: pd.DataFrame) -> tuple[Figure, str]:
    """Mapa korelacji kolumn liczbowych i zdanie o tym, czego korelacja nie oznacza.

    Kolumny stałe są pomijane, bo współczynnik nie jest dla nich określony.
    """
    if frame.columns.duplicated().any():
        raise ChartError(
            "W zbiorze są powtórzone nazwy kolumn. "
            "Zostaw jedną kolumnę o każdej nazwie, zanim policzysz korelację."
        )

    numeric = frame.select_dtypes(include="number")
    bool_columns = [name for name in numeric.columns if pd.api.types.is_bool_dtype(numeric[name])]
    numeric = numeric.drop(columns=bool_columns)
    varying = [name for name in numeric.columns if numeric[name].nunique(dropna=True) >= 2]
    skipped = len(numeric.columns) - len(varying)
    numeric = numeric[varying]
    if numeric.shape[1] < 2:
        raise ChartError(
            "Do korelacji potrzeba co najmniej dwóch kolumn liczbowych, które się zmieniają. "
            "Wybierz inny zbiór albo nie opieraj wniosku na kolumnie stałej."
        )

    corr = numeric.corr()
    fig = Figure(figsize=(7, 5.5), dpi=100)
    ax = fig.add_subplot()
    sns.heatmap(
        corr,
        ax=ax,
        vmin=-1,
        vmax=1,
        center=0,
        cmap="coolwarm",
        annot=numeric.shape[1] <= 8,
        fmt=".2f",
        square=True,
    )
    ax.set_title("Korelacja kolumn liczbowych")
    ax.set_xlabel("Zmienna")
    ax.set_ylabel("Zmienna")
    fig.tight_layout()
    return fig, _correlation_note(corr, skipped)


def _new_figure() -> tuple[Figure, object]:
    fig = Figure(figsize=(7, 4), dpi=100)
    return fig, fig.add_subplot()


def _correlation_note(corr: pd.DataFrame, skipped_constant: int) -> str:
    left, right, value = _strongest_pair(corr)
    if value > 0:
        direction = "dodatni"
    elif value < 0:
        direction = "ujemny"
    else:
        direction = "zerowy"
    note = (
        f"Najsilniejszy związek jest {direction}: {left} i {right}, r = {value:.2f}. "
        "Korelacja opisuje tylko współwystępowanie. "
        "Nie oznacza, że jedna zmienna powoduje drugą."
    )
    if skipped_constant:
        note += " Kolumny stałe pominięto, bo korelacja nie jest dla nich określona."
    return note


def _strongest_pair(corr: pd.DataFrame) -> tuple[str, str, float]:
    columns = list(corr.columns)
    best_value = None
    best_pair = (str(columns[0]), str(columns[1]))
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            value = corr.iloc[i, j]
            if pd.isna(value):
                continue
            if best_value is None or abs(float(value)) > abs(best_value):
                best_value = float(value)
                best_pair = (str(columns[i]), str(columns[j]))
    if best_value is None:
        raise ChartError(
            "Nie udało się policzyć korelacji. Uzupełnij kolumny liczbowe albo wybierz inne."
        )
    return best_pair[0], best_pair[1], best_value


def _require_column(frame: pd.DataFrame, column: object) -> pd.Series:
    if not isinstance(frame, pd.DataFrame):
        raise ChartError("Wykres potrzebuje tabeli pandas. Wczytaj plik CSV albo podaj ramkę danych.")
    if column not in frame.columns:
        available = ", ".join(str(name) for name in frame.columns) or "brak kolumn"
        raise ChartError(
            f"W zbiorze nie ma kolumny '{column}'. Wybierz kolumnę z listy: {available}."
        )
    values = frame[column]
    if isinstance(values, pd.DataFrame):
        raise ChartError(
            f"Kolumna '{column}' występuje więcej niż raz. Zostaw jedną kolumnę o tej nazwie."
        )
    return values


def _require_numeric_type(frame: pd.DataFrame, column: str, chart_name: str) -> None:
    values = _require_column(frame, column)
    if not _is_numeric_column(values):
        raise ChartError(
            f"Wykres {chart_name} wymaga kolumny liczbowej, a '{column}' nią nie jest. "
            "Wybierz kolumnę z liczbami."
        )


def _numeric_values(frame: pd.DataFrame, column: str, chart_name: str) -> pd.Series:
    _require_numeric_type(frame, column, chart_name)
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        raise ChartError(
            f"Kolumna '{column}' nie zawiera liczb. Uzupełnij dane albo wybierz inną kolumnę."
        )
    values.name = str(column)
    return values


def _require_variation(values: pd.Series, column: str) -> None:
    if int(values.nunique(dropna=True)) < 2:
        raise ChartError(
            f"Kolumna '{column}' ma tylko jedną wartość, więc ten wykres nic nie pokaże. "
            "Wybierz kolumnę, która się zmienia."
        )


def _is_numeric_column(values: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values))
