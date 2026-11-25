"""Okno aplikacji: wczytanie CSV, profil, czyszczenie, wykres i zapis.

Tabela nie leży w zmiennej globalnej. Trzyma ją obiekt okna w polu `session`.
Błędny plik, zły wykres albo błąd zapisu pokazują komunikat i nie zamykają programu.
"""

from collections.abc import Callable
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from charts import (
    ChartError,
    bar_figure,
    box_figure,
    correlation_figure,
    histogram_figure,
    scatter_figure,
)
from session import CleaningError, DataLoadError, DataSession, QualityRuleError

PREVIEW_LIMIT = 100
SEPARATORS = {
    "automatycznie": None,
    "przecinek": ",",
    "średnik": ";",
}
ENCODINGS = ("utf-8", "cp1250")
MISSING_ACTIONS = (
    "uzupełnij braki (mediana albo moda)",
    "usuń wiersze z brakiem w kolumnie",
    "usuń wiersze z brakiem w dowolnej kolumnie",
)
RANGE_ACTIONS = ("zostaw", "oznacz", "usuń")
IQR_ACTIONS = ("zostaw", "oznacz", "przytnij", "usuń")
CHART_TYPES = ("histogram", "punktowy", "pudełkowy", "słupkowy", "korelacja")
CHART_SOURCES = ("bieżące", "surowe")
CHART_SUFFIXES = {".png", ".pdf", ".svg"}


class DataApp(tk.Tk):
    """Główne okno. `report_error` można podmienić w teście, bez klikania w dialog."""

    def __init__(self, report_error: Callable[[str, str], None] | None = None) -> None:
        super().__init__()
        self.title("Studio jakości i wizualizacji danych")
        self.geometry("980x680")
        self.minsize(800, 520)
        self._report_error = report_error or messagebox.showerror
        self.session: DataSession | None = None
        self._chart_canvas: FigureCanvasTkAgg | None = None
        self._figure: Figure | None = None
        self._build()

    def choose_file(self) -> None:
        """Otwiera okno wyboru pliku. Anulowanie nic nie zmienia."""
        path = filedialog.askopenfilename(
            title="Wybierz plik CSV",
            filetypes=[("Pliki CSV", "*.csv"), ("Wszystkie pliki", "*.*")],
        )
        if not path:
            return
        self.load_csv_path(path)

    def load_csv_path(self, path: str | Path) -> None:
        """Wczytuje CSV według kodowania i separatora wybranych w oknie.

        Przy błędzie zostaje poprzednia sesja, a okno pozostaje otwarte.
        """
        separator_label = self.separator_box.get()
        if separator_label not in SEPARATORS:
            self._show_error(
                "Zły separator",
                "Wybierz separator: automatycznie, przecinek albo średnik.",
            )
            return
        try:
            session = DataSession.from_csv(
                path,
                separator=SEPARATORS[separator_label],
                encoding=self.encoding_box.get() or "utf-8",
            )
        except DataLoadError as error:
            self._show_error("Błąd wczytywania", str(error))
            return
        self.session = session
        self._refresh()
        self._set_status(session.load_summary())
        self.notebook.select(self.preview_tab)

    def remove_duplicates(self) -> None:
        """Usuwa pełne duplikaty z bieżących danych."""
        self._run(lambda session: _duplicate_message(session.drop_exact_duplicates()))

    def apply_missing(self) -> None:
        """Uruchamia wybraną strategię braków."""
        session = self._require_session()
        if session is None:
            return
        strategy = self.missing_strategy.get()
        if strategy not in MISSING_ACTIONS:
            self._show_error("Brak wyboru", "Wybierz strategię braków.")
            return
        if strategy == MISSING_ACTIONS[2]:
            self._run(lambda current: _drop_message(current.drop_missing()))
            return
        column = self._selected_column(self.missing_column)
        if column is None:
            return
        if strategy == MISSING_ACTIONS[0]:
            self._run(lambda current: _fill_message(current.fill_missing(column)))
            return
        self._run(lambda current: _drop_message(current.drop_missing(column)))

    def apply_range_rule(self) -> None:
        """Stosuje decyzję użytkownika do wartości spoza zakresu."""
        session = self._require_session()
        if session is None:
            return
        column = self._selected_column(self.range_column)
        if column is None:
            return
        minimum = self._read_number(self.range_min.get(), "Dolna granica")
        if minimum is None:
            return
        maximum = self._read_number(self.range_max.get(), "Górna granica")
        if maximum is None:
            return
        action = self._selected_action(self.range_action)
        if action is None:
            return
        self._run(
            lambda current: _decision_message(
                current.apply_range(column, minimum, maximum, action)
            )
        )

    def apply_outlier_rule(self) -> None:
        """Stosuje decyzję użytkownika do obserwacji odstających."""
        session = self._require_session()
        if session is None:
            return
        column = self._selected_column(self.iqr_column)
        if column is None:
            return
        factor = self._read_number(self.iqr_factor.get(), "Mnożnik IQR")
        if factor is None:
            return
        action = self._selected_action(self.iqr_action)
        if action is None:
            return
        self._run(lambda current: _decision_message(current.apply_iqr(column, action, factor)))

    def restore_raw(self) -> None:
        """Wraca do kopii surowych danych bez ponownego wczytywania pliku."""
        self._run(lambda session: _reset_message(session))

    def draw_chart(self) -> None:
        """Rysuje wybrany wykres w oknie i usuwa poprzedni rysunek."""
        if self._require_session() is None:
            return
        frame = self._chart_frame()
        if frame is None:
            return
        kind = self.chart_type.get()
        if kind not in CHART_TYPES:
            self._show_error("Brak wyboru", "Wybierz rodzaj wykresu.")
            return
        try:
            figure, note = self._make_figure(frame, kind)
        except ChartError as error:
            self._show_error("Nie narysowano wykresu", str(error))
            return
        if figure is None:
            return
        self._show_figure(figure)
        self.chart_note.config(text=note)
        self._set_status(f"Narysowano wykres: {kind}.")

    def choose_chart_export(self) -> None:
        """Pyta o plik PNG, PDF albo SVG i zapisuje bieżący wykres."""
        path = filedialog.asksaveasfilename(
            title="Zapisz wykres",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")],
        )
        if not path:
            return
        self.save_chart(path)

    def choose_csv_export(self) -> None:
        """Pyta o plik i zapisuje bieżącą tabelę."""
        path = filedialog.asksaveasfilename(
            title="Zapisz oczyszczony CSV",
            defaultextension=".csv",
            filetypes=[("Pliki CSV", "*.csv")],
        )
        if not path:
            return
        self.save_cleaned_csv(path)

    def save_chart(self, path: str | Path) -> None:
        """Zapisuje ostatni wykres. Bez rysunku albo przy złej ścieżce nic nie zamyka okna."""
        if self._figure is None:
            self._show_error("Brak wykresu", "Najpierw narysuj wykres, a potem wybierz zapis.")
            return
        file_path = Path(path)
        if file_path.suffix.lower() not in CHART_SUFFIXES:
            self._show_error(
                "Zły format",
                "Wybierz plik z końcówką .png, .pdf albo .svg.",
            )
            return
        if not self._write_file(file_path, lambda: self._figure.savefig(file_path)):
            return
        self._set_status(f"Zapisano wykres: {file_path}")

    def save_cleaned_csv(self, path: str | Path) -> None:
        """Zapisuje bieżące dane z separatorem, którego użyto przy wczytaniu."""
        session = self._require_session()
        if session is None:
            return
        file_path = Path(path)
        separator = session.separator if session.separator in {",", ";"} else ","
        decimal = "," if separator == ";" else "."

        def write() -> None:
            session.current.to_csv(file_path, index=False, sep=separator, decimal=decimal)

        if not self._write_file(file_path, write):
            return
        self._set_status(f"Zapisano CSV: {file_path}")

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=8)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Button(bar, text="Wybierz CSV", command=self.choose_file).pack(side="left")
        ttk.Label(bar, text="Kodowanie").pack(side="left", padx=(12, 4))
        self.encoding_box = ttk.Combobox(bar, values=ENCODINGS, width=10, state="readonly")
        self.encoding_box.set("utf-8")
        self.encoding_box.pack(side="left")
        ttk.Label(bar, text="Separator").pack(side="left", padx=(12, 4))
        self.separator_box = ttk.Combobox(
            bar,
            values=tuple(SEPARATORS),
            width=16,
            state="readonly",
        )
        self.separator_box.set("automatycznie")
        self.separator_box.pack(side="left")

        self.notebook = ttk.Notebook(self, padding=8)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.preview_tab = self._build_preview(self.notebook)
        self.profile_tab = self._build_text_tab(self.notebook, "Profil")
        self.cleaning_tab = self._build_cleaning(self.notebook)
        self.chart_tab = self._build_chart(self.notebook)
        self.audit_tab = self._build_text_tab(self.notebook, "Dziennik")
        self.profile_text = self.profile_tab
        self.audit_box = self.audit_tab

        self.status = ttk.Label(self, anchor="w", padding=(8, 4))
        self.status.grid(row=2, column=0, sticky="ew")
        self._set_status("Wybierz plik CSV, aby zacząć.")
        self._clear_views()

    def _build_preview(self, notebook: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(notebook, padding=4)
        notebook.add(tab, text="Podgląd")
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)
        self.preview_info = ttk.Label(tab, text="Brak danych.")
        self.preview_info.grid(row=0, column=0, sticky="w")
        table = ttk.Frame(tab)
        table.grid(row=1, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.preview = ttk.Treeview(table, show="headings")
        y_scroll = ttk.Scrollbar(table, orient="vertical", command=self.preview.yview)
        x_scroll = ttk.Scrollbar(table, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        return tab

    def _build_text_tab(self, notebook: ttk.Notebook, title: str) -> tk.Text:
        tab = ttk.Frame(notebook, padding=4)
        notebook.add(tab, text=title)
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        widget = tk.Text(tab, wrap="word", height=12)
        scroll = ttk.Scrollbar(tab, command=widget.yview)
        widget.configure(yscrollcommand=scroll.set)
        widget.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        return widget

    def _build_cleaning(self, notebook: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Czyszczenie")
        tab.columnconfigure(0, weight=1)

        duplicates = ttk.LabelFrame(tab, text="Duplikaty", padding=8)
        duplicates.grid(row=0, column=0, sticky="ew", pady=4)
        ttk.Label(duplicates, text="Usuwane są tylko pełne kopie. Zostaje pierwszy rekord.").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(duplicates, text="Usuń pełne duplikaty", command=self.remove_duplicates).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

        missing = ttk.LabelFrame(tab, text="Braki", padding=8)
        missing.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Label(missing, text="Kolumna").grid(row=0, column=0, sticky="w")
        self.missing_column = ttk.Combobox(missing, state="readonly", width=28)
        self.missing_column.grid(row=1, column=0, sticky="w")
        ttk.Label(missing, text="Strategia").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.missing_strategy = ttk.Combobox(
            missing,
            values=MISSING_ACTIONS,
            state="readonly",
            width=46,
        )
        self.missing_strategy.set(MISSING_ACTIONS[0])
        self.missing_strategy.grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Button(missing, text="Wykonaj", command=self.apply_missing).grid(
            row=1, column=2, padx=(12, 0)
        )

        ranges = ttk.LabelFrame(tab, text="Zakres wartości", padding=8)
        ranges.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Label(ranges, text="Kolumna liczbowa").grid(row=0, column=0, sticky="w")
        self.range_column = ttk.Combobox(ranges, state="readonly", width=24)
        self.range_column.grid(row=1, column=0, sticky="w")
        ttk.Label(ranges, text="Od").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.range_min = ttk.Entry(ranges, width=10)
        self.range_min.grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Label(ranges, text="Do").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.range_max = ttk.Entry(ranges, width=10)
        self.range_max.grid(row=1, column=2, sticky="w", padx=(12, 0))
        ttk.Label(ranges, text="Decyzja").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.range_action = ttk.Combobox(ranges, values=RANGE_ACTIONS, state="readonly", width=12)
        self.range_action.set("zostaw")
        self.range_action.grid(row=1, column=3, sticky="w", padx=(12, 0))
        ttk.Button(ranges, text="Zastosuj", command=self.apply_range_rule).grid(
            row=1, column=4, padx=(12, 0)
        )

        outliers = ttk.LabelFrame(tab, text="Obserwacje odstające (IQR)", padding=8)
        outliers.grid(row=3, column=0, sticky="ew", pady=4)
        ttk.Label(outliers, text="Kolumna liczbowa").grid(row=0, column=0, sticky="w")
        self.iqr_column = ttk.Combobox(outliers, state="readonly", width=24)
        self.iqr_column.grid(row=1, column=0, sticky="w")
        ttk.Label(outliers, text="Mnożnik").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.iqr_factor = ttk.Entry(outliers, width=8)
        self.iqr_factor.insert(0, "1.5")
        self.iqr_factor.grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Label(outliers, text="Decyzja").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.iqr_action = ttk.Combobox(outliers, values=IQR_ACTIONS, state="readonly", width=12)
        self.iqr_action.set("zostaw")
        self.iqr_action.grid(row=1, column=2, sticky="w", padx=(12, 0))
        ttk.Button(outliers, text="Zastosuj", command=self.apply_outlier_rule).grid(
            row=1, column=3, padx=(12, 0)
        )
        ttk.Label(
            outliers,
            text="Domyślnie dane zostają. Usunięcie albo przycięcie wymaga jawnej decyzji.",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Button(tab, text="Przywróć dane surowe", command=self.restore_raw).grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )
        return tab

    def _build_chart(self, notebook: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Wykres")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        controls = ttk.Frame(tab)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="Dane").grid(row=0, column=0, sticky="w")
        self.chart_source = ttk.Combobox(
            controls, values=CHART_SOURCES, state="readonly", width=12
        )
        self.chart_source.set("bieżące")
        self.chart_source.grid(row=1, column=0, sticky="w")
        self.chart_source.bind("<<ComboboxSelected>>", lambda _event: self._update_chart_columns())
        ttk.Label(controls, text="Rodzaj").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.chart_type = ttk.Combobox(controls, values=CHART_TYPES, state="readonly", width=14)
        self.chart_type.set("histogram")
        self.chart_type.grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.chart_type.bind("<<ComboboxSelected>>", lambda _event: self._update_chart_columns())
        ttk.Label(controls, text="Kolumna").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.chart_x = ttk.Combobox(controls, state="readonly", width=22)
        self.chart_x.grid(row=1, column=2, sticky="w", padx=(12, 0))
        ttk.Label(controls, text="Druga oś").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.chart_y = ttk.Combobox(controls, state="disabled", width=22)
        self.chart_y.grid(row=1, column=3, sticky="w", padx=(12, 0))
        ttk.Button(controls, text="Narysuj", command=self.draw_chart).grid(
            row=1, column=4, padx=(12, 0)
        )

        self.chart_note = ttk.Label(tab, wraplength=860, justify="left")
        self.chart_note.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        self.chart_panel = ttk.Frame(tab)
        self.chart_panel.grid(row=2, column=0, sticky="nsew")

        buttons = ttk.Frame(tab)
        buttons.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Zapisz wykres", command=self.choose_chart_export).pack(side="left")
        ttk.Button(buttons, text="Zapisz oczyszczony CSV", command=self.choose_csv_export).pack(
            side="left", padx=(8, 0)
        )
        return tab

    def _run(self, action: Callable[[DataSession], str]) -> None:
        session = self._require_session()
        if session is None:
            return
        try:
            message = action(session)
        except (CleaningError, QualityRuleError, DataLoadError) as error:
            self._show_error("Nie wykonano operacji", str(error))
            return
        self._refresh()
        self._set_status(message)

    def _require_session(self) -> DataSession | None:
        if self.session is None:
            self._show_error("Brak danych", "Najpierw wczytaj plik CSV.")
            return None
        return self.session

    def _selected_column(self, combo: ttk.Combobox) -> str | None:
        column = combo.get().strip()
        if not column:
            self._show_error("Brak wyboru", "Wybierz kolumnę z listy.")
            return None
        return column

    def _selected_action(self, combo: ttk.Combobox) -> str | None:
        label = combo.get().strip()
        if not label:
            self._show_error(
                "Brak wyboru",
                "Wybierz decyzję: zostaw, oznacz, przytnij albo usuń.",
            )
            return None
        return label.replace("usuń", "usun")

    def _read_number(self, text: str, label: str) -> float | None:
        cleaned = text.strip().replace(",", ".")
        if not cleaned:
            self._show_error("Brak liczby", f"{label} jest pusta. Podaj na przykład 0 albo 1.5.")
            return None
        try:
            return float(cleaned)
        except ValueError:
            self._show_error("Zła liczba", f"{label} musi być liczbą. Podaj na przykład 0 albo 1.5.")
            return None

    def _refresh(self) -> None:
        session = self.session
        if session is None:
            self._clear_views()
            return
        frame = session.current
        self._show_preview(frame)
        self._set_text(self.profile_text, session.profile().to_text())
        self._set_text(self.audit_box, session.audit_text())
        self._fill_combo(self.missing_column, [str(name) for name in frame.columns])
        self._fill_combo(self.range_column, _numeric_column_names(frame))
        self._fill_combo(self.iqr_column, _numeric_column_names(frame))
        self._clear_chart()
        self.chart_note.config(text="")
        self._update_chart_columns()

    def _clear_views(self) -> None:
        self.preview.delete(*self.preview.get_children())
        self.preview["columns"] = ()
        self.preview_info.config(text="Brak danych.")
        self._set_text(self.profile_text, "Wczytaj plik, aby zobaczyć profil.")
        self._set_text(self.audit_box, "Brak operacji. Dane są w stanie surowym.")
        for combo in (self.missing_column, self.range_column, self.iqr_column):
            self._fill_combo(combo, [])
        self._clear_chart()
        self.chart_note.config(text="")
        self._update_chart_columns()

    def _show_preview(self, frame: pd.DataFrame) -> None:
        columns = [str(name) for name in frame.columns]
        self.preview.delete(*self.preview.get_children())
        self.preview["columns"] = columns
        for name in columns:
            self.preview.heading(name, text=name)
            self.preview.column(name, width=130, anchor="w", stretch=True)
        shown = frame.head(PREVIEW_LIMIT)
        for _, row in shown.iterrows():
            values = ["" if pd.isna(value) else str(value) for value in row]
            self.preview.insert("", "end", values=values)
        self.preview_info.config(text=f"Podgląd: {len(shown)} z {len(frame)} rekordów.")

    def _fill_combo(self, combo: ttk.Combobox, values: list[str]) -> None:
        current = combo.get()
        combo["values"] = values
        if current in values:
            combo.set(current)
        elif values:
            combo.set(values[0])
        else:
            combo.set("")

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status.config(text=message)

    def _show_error(self, title: str, message: str) -> None:
        self._set_status(message)
        self._report_error(title, message)

    def _chart_frame(self) -> pd.DataFrame | None:
        if self.session is None:
            return None
        if self.chart_source.get() == "surowe":
            return self.session.raw
        return self.session.current

    def _make_figure(self, frame: pd.DataFrame, kind: str) -> tuple[Figure, str] | tuple[None, str]:
        if kind == "korelacja":
            figure, note = correlation_figure(frame)
            return figure, note
        column = self._selected_column(self.chart_x)
        if column is None:
            return None, ""
        if kind == "histogram":
            return histogram_figure(frame, column), ""
        if kind == "pudełkowy":
            return box_figure(frame, column), ""
        if kind == "słupkowy":
            return bar_figure(frame, column), ""
        other = self._selected_column(self.chart_y)
        if other is None:
            return None, ""
        return scatter_figure(frame, column, other), ""

    def _show_figure(self, figure: Figure) -> None:
        self._clear_chart()
        canvas = FigureCanvasTkAgg(figure, master=self.chart_panel)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self._chart_canvas = canvas
        self._figure = figure

    def _clear_chart(self) -> None:
        if self._chart_canvas is not None:
            self._chart_canvas.get_tk_widget().destroy()
            self._chart_canvas = None
        self._figure = None

    def _update_chart_columns(self) -> None:
        frame = self._chart_frame()
        kind = self.chart_type.get()
        if frame is None or kind == "korelacja":
            names: list[str] = []
        elif kind == "słupkowy":
            names = _text_column_names(frame)
        else:
            names = _numeric_column_names(frame)
        self._fill_combo(self.chart_x, names)
        self._fill_combo(self.chart_y, names)
        if kind == "punktowy" and len(names) >= 2 and self.chart_x.get() == self.chart_y.get():
            self.chart_y.set(names[1])
        if kind == "punktowy":
            self.chart_x.configure(state="readonly")
            self.chart_y.configure(state="readonly")
        elif kind == "korelacja":
            self.chart_x.set("")
            self.chart_y.set("")
            self.chart_x.configure(state="disabled")
            self.chart_y.configure(state="disabled")
        else:
            self.chart_x.configure(state="readonly")
            self.chart_y.set("")
            self.chart_y.configure(state="disabled")

    def _write_file(self, path: Path, write: Callable[[], None]) -> bool:
        if path.exists() and path.is_dir():
            self._show_error(
                "Błąd zapisu",
                f"Ścieżka wskazuje katalog, a nie plik: {path}. Podaj nazwę pliku.",
            )
            return False
        try:
            write()
        except OSError as error:
            reason = error.strerror or "brak dostępu do ścieżki"
            self._show_error(
                "Błąd zapisu",
                f"Nie można zapisać pliku: {path}. {reason}. Wybierz istniejący folder.",
            )
            return False
        return True


def _duplicate_message(removed: int) -> str:
    return f"Usunięto pełne duplikaty: {removed}."


def _drop_message(removed: int) -> str:
    return f"Usunięto wiersze z brakami: {removed}."


def _fill_message(filled: int) -> str:
    return f"Uzupełniono braki: {filled}."


def _decision_message(affected: int) -> str:
    return f"Zastosowano decyzję. Dotknięte rekordy: {affected}."


def _reset_message(session: DataSession) -> str:
    session.reset()
    return "Przywrócono dane surowe."


def _numeric_column_names(frame: pd.DataFrame) -> list[str]:
    names = []
    for name in frame.columns:
        values = frame[name]
        if isinstance(values, pd.DataFrame):
            continue
        if pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values):
            names.append(str(name))
    return names


def _text_column_names(frame: pd.DataFrame) -> list[str]:
    numeric = set(_numeric_column_names(frame))
    return [str(name) for name in frame.columns if str(name) not in numeric]
