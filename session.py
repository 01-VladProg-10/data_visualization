"""Sesja danych: wczytywanie CSV, profil, reguły jakości i czyszczenie.

Wykresy są w module charts. Ten moduł trzyma dwie kopie tabeli: surowe dane
oraz bieżący stan. Czyszczenie zmienia tylko bieżący stan i dopisuje wpis
do dziennika. Powrót do danych surowych nie wymaga ponownego wczytania pliku.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd


class DataLoadError(Exception):
    """Błąd wczytania CSV. Treść wyjątku mówi, co użytkownik może poprawić."""


class QualityRuleError(Exception):
    """Niepoprawna reguła jakości. Treść wyjątku mówi, co poprawić."""


class CleaningError(Exception):
    """Nie da się wykonać czyszczenia. Treść wyjątku mówi, co poprawić."""


@dataclass
class AuditEntry:
    """Jeden wpis dziennika: operacja, parametry, czas i liczba rekordów."""

    operation: str
    parameters: str
    timestamp: str
    rows_before: int
    rows_after: int
    affected: int


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
class QualityReport:
    """Wynik reguły jakości. Sama reguła niczego nie usuwa ani nie poprawia."""

    column: str
    rule_name: str
    parameters: str
    description: str
    lower: float | None
    upper: float | None
    affected: int
    matched: pd.Series

    def to_text(self) -> str:
        """Raport reguły: opis, liczba rekordów i ich indeksy."""
        if self.affected == 0:
            return self.description
        labels = [str(label) for label, flag in self.matched.items() if bool(flag)]
        lines = [self.description, f"Indeksy wierszy: {', '.join(labels[:20])}"]
        if len(labels) > 20:
            lines.append(f"Pokazano 20 z {len(labels)} indeksów.")
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
    audit_log: list[AuditEntry] = field(default_factory=list)

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

    def range_report(self, column: str, minimum: float, maximum: float) -> QualityReport:
        """Wskazuje rekordy spoza podanego zakresu. Nie zmienia danych.

        Granice są włączone: wartość równa minimum albo maksimum jest poprawna.
        Puste komórki nie są wartościami poza zakresem.
        """
        lower = _require_finite_number(minimum, "Dolna granica")
        upper = _require_finite_number(maximum, "Górna granica")
        if lower > upper:
            raise QualityRuleError(
                "Dolna granica nie może być większa od górnej. "
                "Zamień granice albo popraw wpisane wartości."
            )

        values = _require_numeric_column(self.current, column)
        matched = values.notna() & ((values < lower) | (values > upper))
        matched = matched.fillna(False).astype(bool).copy()
        affected = int(matched.sum())
        parameters = f"minimum={_format_stat(lower)}, maximum={_format_stat(upper)}"
        description = (
            f"Zakres kolumny {column}: od {_format_stat(lower)} do {_format_stat(upper)} włącznie. "
            f"Liczba rekordów poza zakresem: {affected}. "
            "Braki nie są liczone jako wartości poza zakresem. Dane nie zostały zmienione."
        )
        return QualityReport(
            column=str(column),
            rule_name="zakres",
            parameters=parameters,
            description=description,
            lower=lower,
            upper=upper,
            affected=affected,
            matched=matched,
        )

    def iqr_report(self, column: str, factor: float = 1.5) -> QualityReport:
        """Wskazuje obserwacje odstające metodą IQR. Nie zmienia danych.

        Przedział to od Q1 - mnożnik * IQR do Q3 + mnożnik * IQR.
        Domyślny mnożnik 1.5 jest zwykłą granicą Tukey'a. Wartość poza tym
        przedziałem nie jest jeszcze błędem: dopiero późniejsza decyzja użytkownika
        może ją oznaczyć, przyciąć albo usunąć.
        """
        factor_value = _require_finite_number(factor, "Mnożnik IQR")
        if factor_value <= 0:
            raise QualityRuleError(
                "Mnożnik IQR musi być liczbą większą od zera. Zwykle używa się 1.5."
            )

        values = _require_numeric_column(self.current, column)
        q1 = values.quantile(0.25)
        q3 = values.quantile(0.75)
        matched = pd.Series(False, index=self.current.index)
        lower = None
        upper = None
        affected = 0

        if pd.isna(q1) or pd.isna(q3):
            parameters = f"factor={_format_stat(factor_value)}"
            description = (
                f"Metoda IQR nie działa dla kolumny {column}: za mało wartości liczbowych, "
                "żeby policzyć kwartyle. Uzupełnij kolumnę albo wybierz inną. "
                "Dane nie zostały zmienione."
            )
        else:
            iqr = float(q3 - q1)
            if iqr == 0:
                lower = float(q1)
                upper = float(q3)
                parameters = (
                    f"factor={_format_stat(factor_value)}, q1={_format_stat(q1)}, "
                    f"q3={_format_stat(q3)}, iqr=0"
                )
                description = (
                    f"Metoda IQR dla kolumny {column}: Q1={_format_stat(q1)}, "
                    f"Q3={_format_stat(q3)}, IQR=0, mnożnik {_format_stat(factor_value)}. "
                    "Przedział nie wyróżnia obserwacji odstających, bo wartości skupiają się "
                    "w jednym punkcie. Dane nie zostały zmienione."
                )
            else:
                lower = float(q1 - factor_value * iqr)
                upper = float(q3 + factor_value * iqr)
                matched = values.notna() & ((values < lower) | (values > upper))
                matched = matched.fillna(False).astype(bool)
                affected = int(matched.sum())
                parameters = (
                    f"factor={_format_stat(factor_value)}, q1={_format_stat(q1)}, "
                    f"q3={_format_stat(q3)}, iqr={_format_stat(iqr)}"
                )
                description = (
                    f"Metoda IQR dla kolumny {column}: Q1={_format_stat(q1)}, "
                    f"Q3={_format_stat(q3)}, IQR={_format_stat(iqr)}, "
                    f"mnożnik {_format_stat(factor_value)}. "
                    f"Przedział od {_format_stat(lower)} do {_format_stat(upper)}. "
                    f"Liczba rekordów poza przedziałem: {affected}. "
                    "Wartość odstająca nie jest automatycznie błędem. Dane nie zostały zmienione."
                )

        return QualityReport(
            column=str(column),
            rule_name="iqr",
            parameters=parameters,
            description=description,
            lower=lower,
            upper=upper,
            affected=affected,
            matched=matched.copy(),
        )

    def drop_exact_duplicates(self) -> int:
        """Usuwa pełne kopie rekordów. Zostaje pierwszy z każdej grupy.

        Rekord, który zgadza się tylko w części kolumn, zostaje. Metoda zwraca
        liczbę usuniętych wierszy.
        """
        rows_before = len(self.current)
        updated = self.current.drop_duplicates(keep="first").copy()
        removed = rows_before - len(updated)
        self.current = updated
        self._record(
            "duplikaty",
            "pełne duplikaty, zachowany pierwszy rekord",
            rows_before,
            len(updated),
            removed,
        )
        return removed

    def drop_missing(self, column: str | None = None) -> int:
        """Usuwa wiersze z brakami. Bez nazwy kolumny bierze brak w dowolnym polu.

        Wartości w zostawionych wierszach nie są przeliczane. Metoda zwraca
        liczbę usuniętych wierszy.
        """
        rows_before = len(self.current)
        if column is None:
            updated = self.current.dropna(how="any").copy()
            parameters = "strategia=usunięcie, zakres=cały wiersz"
        else:
            self._require_existing_column(column)
            updated = self.current.dropna(subset=[column]).copy()
            parameters = f"strategia=usunięcie, kolumna={column}"
        removed = rows_before - len(updated)
        self.current = updated
        self._record("braki", parameters, rows_before, len(updated), removed)
        return removed

    def fill_missing(self, column: str) -> int:
        """Uzupełnia braki w jednej kolumnie: mediana albo wartość najczęstsza.

        Liczby dostają medianę, pozostałe kolumny najczęstszą wartość. Inne
        kolumny nie są ruszane. Metoda zwraca liczbę uzupełnionych komórek.
        """
        values = self._require_existing_column(column)
        missing_count = int(values.isna().sum())
        rows_before = len(self.current)
        if missing_count == 0:
            self._record(
                "braki",
                f"strategia=imputacja, kolumna={column}, braki=0",
                rows_before,
                rows_before,
                0,
            )
            return 0

        if _is_numeric_column(values):
            fill_value = values.median()
            if pd.isna(fill_value):
                raise CleaningError(
                    f"W kolumnie {column} nie ma żadnej liczby, więc nie da się policzyć mediany. "
                    "Usuń wiersze z brakami albo wybierz inną kolumnę."
                )
            method = "mediana"
            shown = _format_stat(fill_value)
        else:
            present = values.dropna()
            if present.empty:
                raise CleaningError(
                    f"W kolumnie {column} nie ma żadnej wartości, więc nie da się ustalić najczęstszej. "
                    "Usuń wiersze z brakami albo wybierz inną kolumnę."
                )
            fill_value = present.mode().iloc[0]
            method = "moda"
            shown = _format_fill_value(fill_value)

        updated = self.current.copy()
        updated[column] = values.fillna(fill_value)
        self.current = updated
        self._record(
            "braki",
            f"strategia=imputacja, metoda={method}, kolumna={column}, wartość={shown}",
            rows_before,
            len(self.current),
            missing_count,
        )
        return missing_count

    def apply_range(self, column: str, minimum: float, maximum: float, action: str) -> int:
        """Stosuje decyzję użytkownika do wartości spoza zakresu.

        Dozwolone decyzje: zostaw, oznacz, usun. Oznaczenie dodaje kolumnę
        z przyrostkiem _poza_zakresem i nie kasuje wartości. Dane surowe
        pozostają nietknięte.
        """
        decision = _normalize_action(action, ("zostaw", "oznacz", "usun"))
        report = self.range_report(column, minimum, maximum)
        return self._apply_quality(report, decision, "_poza_zakresem")

    def apply_iqr(self, column: str, action: str, factor: float = 1.5) -> int:
        """Stosuje decyzję użytkownika do obserwacji odstających.

        Dozwolone decyzje: zostaw, oznacz, przytnij, usun. Wykrycie IQR samo
        niczego nie kasuje. Przycięcie przesuwa wartości na granice przedziału.
        """
        decision = _normalize_action(action, ("zostaw", "oznacz", "przytnij", "usun"))
        report = self.iqr_report(column, factor)
        if decision == "przytnij" and (report.lower is None or report.upper is None):
            raise CleaningError(
                "Nie da się przyciąć wartości, bo metoda IQR nie wyznaczyła przedziału. "
                "Uzupełnij kolumnę, wybierz inną albo zostaw dane."
            )
        return self._apply_quality(report, decision, "_odstajaca")

    def reset(self) -> None:
        """Przywraca bieżące dane z kopii surowej. Dziennik zostaje."""
        rows_before = len(self.current)
        already_raw = self.current.equals(self.raw)
        self.current = self.raw.copy(deep=True)
        self._record(
            "reset",
            "przywrócono dane surowe",
            rows_before,
            len(self.current),
            0 if already_raw else rows_before,
        )

    def audit_text(self) -> str:
        """Tekst dziennika do okna i do pliku raportu."""
        if not self.audit_log:
            return "Brak operacji. Dane są w stanie surowym."
        lines = ["Dziennik zmian:"]
        for number, entry in enumerate(self.audit_log, start=1):
            lines.append(
                f"{number}. {entry.timestamp} | {entry.operation} | {entry.parameters} | "
                f"rekordy przed: {entry.rows_before}, po: {entry.rows_after}, "
                f"dotknięte: {entry.affected}"
            )
        return "\n".join(lines)

    def export_audit(self, path: str | Path) -> None:
        """Zapisuje dziennik do pliku tekstowego UTF-8."""
        file_path = Path(path)
        if file_path.exists() and file_path.is_dir():
            raise CleaningError(
                f"Ścieżka wskazuje katalog, a nie plik: {file_path}. Podaj nazwę pliku raportu."
            )
        try:
            file_path.write_text(self.audit_text() + "\n", encoding="utf-8")
        except OSError as error:
            reason = error.strerror or "brak dostępu do ścieżki"
            raise CleaningError(
                f"Nie można zapisać raportu: {file_path}. {reason}. Wybierz istniejący folder."
            ) from error

    def _apply_quality(self, report: QualityReport, action: str, flag_suffix: str) -> int:
        rows_before = len(self.current)
        mask = report.matched.reindex(self.current.index).fillna(False).astype(bool)
        parameters = (
            f"kolumna={report.column}, decyzja={action}, wykryto={report.affected}, "
            f"{report.parameters}"
        )

        if action == "zostaw":
            self._record(report.rule_name, parameters, rows_before, rows_before, 0)
            return 0

        if action == "usun":
            updated = self.current.loc[~mask].copy()
            removed = rows_before - len(updated)
            self.current = updated
            self._record(report.rule_name, parameters, rows_before, len(updated), removed)
            return removed

        if action == "oznacz":
            updated = self.current.copy()
            flag = f"{report.column}{flag_suffix}"
            updated[flag] = mask
            self.current = updated
            self._record(
                report.rule_name,
                parameters + f", kolumna_flagi={flag}",
                rows_before,
                len(updated),
                int(mask.sum()),
            )
            return int(mask.sum())

        if report.lower is None or report.upper is None:
            raise CleaningError(
                "Nie da się przyciąć wartości, bo reguła nie wyznaczyła przedziału. "
                "Wybierz inną kolumnę albo zostaw dane."
            )
        updated = self.current.copy()
        original = updated[report.column]
        clipped = original.clip(lower=report.lower, upper=report.upper)
        changed = int((original.notna() & original.ne(clipped)).sum())
        updated[report.column] = clipped
        self.current = updated
        self._record(report.rule_name, parameters, rows_before, len(updated), changed)
        return changed

    def _record(
        self,
        operation: str,
        parameters: str,
        rows_before: int,
        rows_after: int,
        affected: int,
    ) -> None:
        self.audit_log.append(
            AuditEntry(
                operation=operation,
                parameters=parameters,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                rows_before=rows_before,
                rows_after=rows_after,
                affected=affected,
            )
        )

    def _require_existing_column(self, column: object) -> pd.Series:
        if column not in self.current.columns:
            available = ", ".join(str(name) for name in self.current.columns) or "brak kolumn"
            raise CleaningError(
                f"W zbiorze nie ma kolumny '{column}'. Wybierz kolumnę z listy: {available}."
            )
        values = self.current[column]
        if isinstance(values, pd.DataFrame):
            raise CleaningError(
                f"Kolumna '{column}' występuje więcej niż raz. Zostaw jedną kolumnę o tej nazwie."
            )
        return values


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


def _require_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or value is None:
        raise QualityRuleError(f"{label} musi być liczbą. Podaj na przykład 0 albo 1.5.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise QualityRuleError(f"{label} musi być liczbą. Podaj na przykład 0 albo 1.5.") from error
    if pd.isna(number) or number == float("inf") or number == float("-inf"):
        raise QualityRuleError(f"{label} musi być skończoną liczbą. Podaj na przykład 0 albo 1.5.")
    return number


def _require_numeric_column(frame: pd.DataFrame, column: object) -> pd.Series:
    if column not in frame.columns:
        available = ", ".join(str(name) for name in frame.columns) or "brak kolumn"
        raise QualityRuleError(
            f"W zbiorze nie ma kolumny '{column}'. Wybierz kolumnę z listy: {available}."
        )
    values = frame[column]
    if isinstance(values, pd.DataFrame):
        raise QualityRuleError(
            f"Kolumna '{column}' występuje więcej niż raz. "
            "Zostaw w pliku jedną kolumnę o tej nazwie."
        )
    if pd.api.types.is_bool_dtype(values) or not pd.api.types.is_numeric_dtype(values):
        raise QualityRuleError(
            f"Kolumna '{column}' nie jest liczbowa. "
            "Wybierz kolumnę z liczbami, na przykład cenę albo wiek."
        )
    return pd.to_numeric(values, errors="coerce")


def _is_numeric_column(values: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values))


def _normalize_action(action: object, allowed: tuple[str, ...]) -> str:
    choices = ", ".join(allowed)
    if not isinstance(action, str):
        raise CleaningError(f"Nieznana operacja. Wybierz jedną z: {choices}.")
    decision = action.strip().lower()
    if decision not in allowed:
        raise CleaningError(f"Nieznana operacja '{action}'. Wybierz jedną z: {choices}.")
    return decision


def _format_fill_value(value: object) -> str:
    try:
        return _format_stat(value)
    except (TypeError, ValueError):
        return str(value)
