"""Sesja danych i wczytywanie pliku CSV.

Operacje czyszczenia i wykresy dochodzą w kolejnych etapach. Ten moduł tylko
wczytuje plik i trzyma dwie kopie tabeli: surowe dane oraz bieżący stan.
"""

import csv
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

import pandas as pd


class DataLoadError(Exception):
    """Błąd wczytania CSV. Treść wyjątku mówi, co użytkownik może poprawić."""


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
