"""Sesja danych: wczytywanie CSV i profil zbioru.

Czyszczenie i wykresy dochodzą w kolejnych etapach. Moduł trzyma dwie kopie
tabeli: surowe dane oraz bieżący stan. Profil tylko czyta bieżący stan.
"""

import csv
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

import pandas as pd


class DataLoadError(Exception):
    """Błąd wczytania CSV. Treść wyjątku mówi, co użytkownik może poprawić."""


@dataclass
class DataProfile:
    """Opis bieżących danych. Sam profil nie trzyma kopii tabeli i niczego nie zmienia."""

    row_count: int
    column_count: int
    duplicate_count: int
    column_summary: pd.DataFrame
    numeric_stats: pd.DataFrame
    text_stats: pd.DataFrame

    def to_text(self) -> str:
        """Tekstowy raport profilu, do podglądu w GUI i do omówienia na obronie."""
        lines = [
            f"Rekordy: {self.row_count}",
            f"Kolumny: {self.column_count}",
            f"Nadmiarowe pełne duplikaty: {self.duplicate_count}",
            "",
            "Kolumny:",
        ]
        if self.column_summary.empty:
            lines.append("(brak kolumn)")
        else:
            for name, row in self.column_summary.iterrows():
                lines.append(
                    f"- {name}: typ {row['dtype']}, braki {int(row['missing'])} "
                    f"({float(row['missing_pct']):.2f}%), unikalne {int(row['unique'])}"
                )

        lines.extend(["", "Statystyki liczbowe:"])
        if self.numeric_stats.empty:
            lines.append("(brak kolumn liczbowych)")
        else:
            for name, row in self.numeric_stats.iterrows():
                lines.append(
                    f"- {name}: n={int(row['count'])}, średnia {_format_stat(row['mean'])}, "
                    f"odch. std. {_format_stat(row['std'])}, min {_format_stat(row['min'])}, "
                    f"Q1 {_format_stat(row['q1'])}, mediana {_format_stat(row['median'])}, "
                    f"Q3 {_format_stat(row['q3'])}, max {_format_stat(row['max'])}"
                )

        lines.extend(["", "Statystyki tekstowe:"])
        if self.text_stats.empty:
            lines.append("(brak kolumn tekstowych)")
        else:
            for name, row in self.text_stats.iterrows():
                lines.append(
                    f"- {name}: n={int(row['count'])}, unikalne {int(row['unique'])}, "
                    f"najczęstsza {_format_category(row['top'])}, "
                    f"liczba wystąpień {int(row['freq'])}"
                )
        return "\n".join(lines)


@dataclass
class DataSession:
    """Przechowuje dane surowe, bieżącą kopię i dziennik operacji.

    Pole `raw` zostaje takie, jak w chwili wczytania. Późniejsze czyszczenie
    będzie zmieniało tylko `current`, więc da się wrócić do danych surowych
    bez ponownego uruchamiania programu.
    """

    raw: pd.DataFrame
    separator: str = ","
    encoding: str = "utf-8"
    source_path: Path | None = None
    current: pd.DataFrame = field(init=False)
    audit_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.raw, pd.DataFrame):
            raise TypeError("Dane sesji muszą być obiektem pandas.DataFrame.")
        # Własne kopie, żeby zmiana ramki z zewnątrz nie ruszyła sesji.
        self.raw = self.raw.copy(deep=True)
        self.current = self.raw.copy(deep=True)
        if self.source_path is not None:
            self.source_path = Path(self.source_path)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        separator: str | None = None,
        encoding: str = "utf-8",
    ) -> "DataSession":
        """Wczytuje CSV i buduje sesję.

        `separator=None` oznacza wykrycie przecinka albo średnika z nagłówka.
        Przy błędzie kodowania lub wadliwym rekordzie metoda zgłasza
        DataLoadError i nie tworzy sesji.
        """
        frame, used_separator, used_encoding = load_csv(path, separator, encoding)
        return cls(
            frame,
            separator=used_separator,
            encoding=used_encoding,
            source_path=Path(path),
        )

    def preview(self, rows: int = 10) -> pd.DataFrame:
        """Zwraca pierwsze wiersze bieżących danych. Nie zmienia sesji."""
        if rows < 1:
            raise ValueError("Liczba wierszy podglądu musi być większa od zera.")
        return self.current.head(rows).copy()

    def load_summary(self) -> str:
        """Krótki opis wczytanego pliku, do komunikatu i późniejszego GUI."""
        return (
            f"Wczytano rekordy: {len(self.raw)}, kolumny: {len(self.raw.columns)}, "
            f"separator: '{self.separator}', kodowanie: {self.encoding}."
        )

    def profile(self) -> DataProfile:
        """Buduje profil bieżących danych i nie zmienia sesji.

        `duplicate_count` liczy nadmiarowe pełne kopie: tyle rekordów usunęłoby
        późniejsze kasowanie duplikatów. Brak nie jest liczony jako unikalna
        wartość. Odchylenie standardowe jest próbkowe (dzielenie przez n - 1).
        """
        frame = self.current
        return DataProfile(
            row_count=int(len(frame)),
            column_count=int(len(frame.columns)),
            duplicate_count=_full_duplicate_count(frame),
            column_summary=_column_summary(frame),
            numeric_stats=_numeric_stats(frame),
            text_stats=_text_stats(frame),
        )


def load_csv(
    path: str | Path,
    separator: str | None = None,
    encoding: str = "utf-8",
) -> tuple[pd.DataFrame, str, str]:
    """Wczytuje CSV i zwraca ramkę, użyty separator oraz kodowanie.

    Obsługiwane separatory to przecinek i średnik. Plik ze średnikiem jest
    traktowany jak eksport z polskiego Excela: przecinek jest wtedy znakiem
    dziesiętnym. Plik z przecinkiem używa kropki dziesiętnej.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise DataLoadError(
            f"Nie znaleziono pliku: {file_path}. Wybierz istniejący plik CSV."
        )
    if not file_path.is_file():
        raise DataLoadError(f"Ścieżka nie wskazuje pliku: {file_path}.")

    text = _read_text(file_path, encoding)
    used_separator = _resolve_separator(text, separator)
    _validate_records(text, used_separator)

    try:
        frame = pd.read_csv(
            StringIO(text),
            sep=used_separator,
            engine="python",
            decimal=_decimal_mark(used_separator),
        )
    except pd.errors.EmptyDataError as error:
        raise DataLoadError(
            "Plik jest pusty albo nie zawiera nagłówka. "
            "Dodaj wiersz z nazwami kolumn i co najmniej jeden rekord."
        ) from error
    except pd.errors.ParserError as error:
        raise DataLoadError(
            "Nie udało się odczytać pliku CSV. Sprawdź separator i cudzysłowy. "
            f"Szczegóły: {error}"
        ) from error

    frame.columns = [str(name).strip() for name in frame.columns]
    if frame.empty:
        raise DataLoadError(
            "Plik nie zawiera rekordów danych. Dodaj co najmniej jeden wiersz pod nagłówkiem."
        )
    return frame, used_separator, encoding


def _read_text(path: Path, encoding: str) -> str:
    # utf-8-sig pomija znacznik BOM dopisywany przez Excel do plików UTF-8.
    codec = "utf-8-sig" if encoding.lower().replace("_", "-") in {"utf-8", "utf8"} else encoding
    try:
        return path.read_text(encoding=codec)
    except LookupError as error:
        raise DataLoadError(
            f"Nieznane kodowanie: {encoding}. Użyj na przykład utf-8 albo cp1250."
        ) from error
    except UnicodeDecodeError as error:
        raise DataLoadError(
            f"Plik nie jest zapisany w kodowaniu {encoding} "
            f"(błąd przy znaku o pozycji {error.start}). "
            "Zapisz plik jako UTF-8 albo wskaż właściwe kodowanie, na przykład cp1250."
        ) from error
    except OSError as error:
        reason = error.strerror or "brak dostępu do pliku"
        raise DataLoadError(f"Nie można odczytać pliku: {path}. {reason}") from error


def _resolve_separator(text: str, separator: str | None) -> str:
    if separator is None:
        return _detect_separator(text)
    if separator not in {",", ";"}:
        raise DataLoadError(
            f"Separator '{separator}' nie jest obsługiwany. Wybierz przecinek albo średnik."
        )
    return separator


def _detect_separator(text: str) -> str:
    """Wybiera separator na podstawie pierwszego niepustego wiersza.

    Separator jest wspólny dla całego pliku, więc wystarczy nagłówek.
    Liczone są tylko znaki poza cudzysłowem. Przy remisie użytkownik
    musi wskazać separator sam, zamiast zgadywać.
    """
    header = ""
    for line in text.splitlines():
        if line.strip():
            header = line
            break

    commas = _count_outside_quotes(header, ",")
    semicolons = _count_outside_quotes(header, ";")
    if semicolons > commas:
        return ";"
    if commas > semicolons:
        return ","
    if commas == 0 and semicolons == 0:
        return ","
    raise DataLoadError(
        "W nagłówku jest tyle samo przecinków i średników. "
        "Wybierz separator ręcznie: przecinek albo średnik."
    )


def _count_outside_quotes(line: str, separator: str) -> int:
    count = 0
    in_quotes = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if in_quotes and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            in_quotes = not in_quotes
        elif char == separator and not in_quotes:
            count += 1
        index += 1
    return count


def _decimal_mark(separator: str) -> str:
    if separator == ";":
        return ","
    return "."


def _validate_records(text: str, separator: str) -> None:
    """Sprawdza, czy każdy rekord ma tyle pól, ile nagłówek.

    Puste linie są pomijane. Wiersz z inną liczbą pól zatrzymuje wczytywanie,
    zanim pandas zgubi albo sklei kolumny.
    """
    reader = csv.reader(StringIO(text), delimiter=separator)
    try:
        header = next(reader)
    except StopIteration as error:
        raise DataLoadError(
            "Plik jest pusty. Dodaj nagłówek i co najmniej jeden rekord."
        ) from error

    if not header or all(not cell.strip() for cell in header):
        raise DataLoadError("Brak nazw kolumn w pierwszym wierszu. Dodaj nagłówek.")

    expected = len(header)
    data_records = 0
    for row in reader:
        if len(row) == 0:
            continue
        data_records += 1
        if len(row) != expected:
            raise DataLoadError(
                f"Wadliwy rekord nr {data_records}: liczba pól w rekordzie to {len(row)}, "
                f"a w nagłówku {expected}. "
                "Uzupełnij brakujące pola albo ujmij w cudzysłów pola, które zawierają separator."
            )

    if data_records == 0:
        raise DataLoadError(
            "Plik nie zawiera rekordów danych. Dodaj co najmniej jeden wiersz pod nagłówkiem."
        )


def _full_duplicate_count(frame: pd.DataFrame) -> int:
    """Ile rekordów jest dokładną kopią wcześniejszego rekordu.

    Porównanie obejmuje wszystkie kolumny. Zgodność tylko w części kolumn
    nie jest duplikatem. Braki w tych samych miejscach są traktowane jak równe,
    tak samo jak później przy usuwaniu pełnych duplikatów.
    """
    if frame.empty:
        return 0
    return int(frame.duplicated().sum())


def _column_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["dtype", "missing", "missing_pct", "unique"]
    if len(frame.columns) == 0:
        summary = pd.DataFrame(columns=columns)
        summary.index.name = "column"
        return summary

    summary = pd.DataFrame(
        {
            "dtype": frame.dtypes.astype(str),
            "missing": frame.isna().sum().astype(int),
            "missing_pct": frame.isna().mean().mul(100).round(2),
            "unique": frame.nunique(dropna=True).astype(int),
        }
    )
    summary.index.name = "column"
    return summary


def _numeric_stats(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["count", "mean", "std", "min", "q1", "median", "q3", "max"]
    numeric = frame.select_dtypes(include="number")
    rows = []
    for name in numeric.columns:
        values = numeric[name]
        rows.append(
            {
                "count": int(values.notna().sum()),
                "mean": values.mean(),
                "std": values.std(),
                "min": values.min(),
                "q1": values.quantile(0.25),
                "median": values.median(),
                "q3": values.quantile(0.75),
                "max": values.max(),
            }
        )
    stats = pd.DataFrame(rows, index=numeric.columns, columns=columns)
    stats.index.name = "column"
    return stats


def _text_stats(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["count", "unique", "top", "freq"]
    other = frame.select_dtypes(exclude="number")
    rows = []
    for name in other.columns:
        values = other[name]
        present = values.dropna()
        if present.empty:
            top = pd.NA
            frequency = 0
        else:
            # Przy remisie mode() zwraca kilka wartości. Pokazujemy pierwszą.
            top = present.mode().iloc[0]
            frequency = int((present == top).sum())
        rows.append(
            {
                "count": int(present.shape[0]),
                "unique": int(values.nunique(dropna=True)),
                "top": top,
                "freq": frequency,
            }
        )
    stats = pd.DataFrame(rows, index=other.columns, columns=columns)
    stats.index.name = "column"
    return stats


def _format_stat(value: object) -> str:
    if pd.isna(value):
        return "brak"
    number = float(value)
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _format_category(value: object) -> str:
    if pd.isna(value):
        return "brak"
    return str(value)
