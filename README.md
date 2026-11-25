# Studio jakości i wizualizacji danych

Aplikacja prowadzi od pliku CSV przez profil i czyszczenie do wykresu oraz zapisu wyników. Przy każdej operacji widać, ile rekordów zostało dotkniętych. Dane surowe zostają w sesji i można do nich wrócić bez ponownego uruchamiania programu.

## Autor

Do uzupełnienia przed oddaniem. Jeśli projekt jest w parze, dopisz też podział pracy.

## Wymagania

- Python 3.11 lub nowszy. Projekt sprawdzono na Pythonie 3.14.
- Biblioteki z pliku `requirements.txt`: pandas 3.1.1, matplotlib 3.11.2, seaborn 0.13.2.
- Tkinter jest w standardowej bibliotece Pythona.

Na tym komputerze polecenie `python` nie jest na ścieżce. Działa launcher `py`. W instrukcji poniżej używam `py`. Jeśli w laboratorium działa `python`, użyj jego.

## Instalacja

W katalogu projektu:

```text
py -m pip install -r requirements.txt
```

## Uruchomienie

```text
py main.py
```

Testy:

```text
py -m unittest discover -s tests -t .
```

Ponowne zbudowanie plików z katalogu `examples`:

```text
py examples/build_examples.py
```

## Funkcje

1. Wczytanie CSV z przecinkiem albo średnikiem. Przy średniku przecinek jest znakiem dziesiętnym. Domyślne kodowanie to UTF-8, dostępne jest też cp1250.
2. Podgląd tabeli oraz profil: liczba rekordów i kolumn, typy, statystyki, braki, pełne duplikaty i liczba wartości unikalnych.
3. Reguła zakresu i wykrywanie obserwacji odstających metodą IQR. Samo wykrycie nie zmienia tabeli.
4. Czyszczenie po decyzji użytkownika: pełne duplikaty, dwie strategie braków, zakres oraz outliery.
5. Dziennik operacji i powrót do danych surowych.
6. Wykresy: histogram, punktowy, pudełkowy, słupkowy i korelacja. Można je rysować dla danych bieżących albo surowych.
7. Zapis bieżącego CSV oraz wykresu do PNG, PDF i SVG.

Błędny plik, zła kolumna albo błąd zapisu pokazują komunikat. Okno pozostaje otwarte.

## Reguły jakości

Braki liczbowe uzupełnia mediana, braki tekstowe wartość najczęstsza. Przy remisie program bierze pierwszą z najczęstszych wartości. Drugą strategią jest usunięcie wierszy: z brakiem w jednej kolumnie albo z brakiem w dowolnej kolumnie.

Pełny duplikat to rekord zgodny we wszystkich kolumnach. Zostaje pierwszy. Zgodność tylko w części kolumn nie jest duplikatem.

Zakres jest domknięty: wartość równa granicy jest poprawna. Pusta komórka nie jest wartością poza zakresem.

IQR używa mnożnika 1,5. Przedział to od Q1 − 1,5 · IQR do Q3 + 1,5 · IQR. Obserwacja odstająca nie jest automatycznie błędem. W przykładzie dochód daleko poza przedziałem jest tylko oznaczany, bo wysoki dochód może być prawdziwy. Długość geograficzna 0 jest usuwana, bo przy przyjętym zakresie Kalifornii od −125 do −114 nie jest współrzędną tego obszaru, tylko błędem zapisu.

Gdy kolumna jest stała albo nie ma dość liczb, IQR nie wskazuje outlierów i opisuje dlaczego.

## Dane testowe i oczekiwany wynik

| Plik | Co sprawdza | Oczekiwany wynik |
|---|---|---|
| `tests/fixtures/comma_sample.csv` | przecinek, kropka dziesiętna, pole w cudzysłowie | 3 rekordy, kolumny `nazwa`, `wartosc`, `opis`; wartości 1.5, 3.5 i 4.0; „Nowak, Anna” zostaje jedną komórką |
| `tests/fixtures/semicolon_sample.csv` | średnik i przecinek dziesiętny | ten sam układ liczb; separator wykryty jako średnik |
| `tests/fixtures/missing_and_duplicates.csv` | pełna kopia, brak liczby i brak tekstu | z 6 rekordów usunięcie duplikatów zostawia 5; wiersz z opisem `inne` zostaje; mediana ceny to 1; moda miasta to `A` |
| `tests/fixtures/out_of_range.csv` | wartość poza zakresem | zakres od 0 do 100 wskazuje tylko cenę 150; ceny 0 i 100 oraz pusta komórka nie są poza zakresem; bez decyzji tabela się nie zmienia |
| `tests/fixtures/bad_row.csv`, `empty.csv`, `cp1250_sample.csv` | błędy wejścia | wadliwy wiersz, pusty plik i złe kodowanie dają komunikat z podpowiedzią; plik cp1250 wczytuje się po wybraniu tego kodowania |

`examples/housing_sample.csv` jest małym zbiorem ilustracyjnym, a nie plikiem California Housing od prowadzącej. Ma 8 rekordów: jedną pełną kopię, pustą liczbę sypialni, pustą etykietę położenia, długość geograficzną 0 i dochód 50.

Kolejność w `examples/build_examples.py`, potwierdzona dziennikiem:

1. Usunięcie pełnego duplikatu: 8 rekordów, potem 7.
2. Usunięcie długości poza zakresem od −125 do −114: zostaje 6 rekordów.
3. Mediana `total_bedrooms` równa 129 uzupełnia jedną komórkę.
4. Najczęstsza wartość `ocean_proximity` to `NEAR BAY` i uzupełnia jedną komórkę.
5. IQR dochodu: Q1 = 2.675, Q3 = 8.3193, IQR = 5.6443. Poza przedziałem jest dochód 50. Zostaje w tabeli, a kolumna `median_income_odstajaca` ma przy nim `True`.

Pliki wynikowe:

- `examples/cleaned.csv`
- `examples/raport_zmian.txt`
- `examples/histogram_median_income.png`
- `examples/scatter_income_value.png`
- `examples/korelacja.png` i `examples/korelacja.txt`

Na tych sześciu rekordach program podaje najsilniejszy związek `total_rooms` i `households` jako r = 1.00. Dokładna wartość to około 0.995, a napis zaokrągla do dwóch miejsc. Przy sześciu punktach nie jest to wniosek o rynku mieszkań. Korelacja opisuje współwystępowanie i nie oznacza, że jedna zmienna powoduje drugą.

## Raport testów

Uruchomienie 25 listopada 2026:

```text
py -m unittest discover -s tests -t .
```

Wynik: 71 testów, wszystkie zaliczone.

| Przypadek z instrukcji | Gdzie jest sprawdzony |
|---|---|
| CSV z przecinkiem i ze średnikiem | `tests/test_loading.py` oraz oba małe pliki w `tests/fixtures` |
| Braki liczbowe i tekstowe, raport liczby zmian | `tests/test_cleaning.py` i `missing_and_duplicates.csv` |
| Tylko pełne duplikaty | `tests/test_cleaning.py` i `tests/test_profile.py` |
| Wartość poza zakresem bez zmiany danych przed decyzją | `tests/test_quality.py` i `out_of_range.csv` |
| Kolumna stała albo nieliczbowa blokuje wykres | `tests/test_charts.py` i `tests/test_export.py` |
| CSV da się wczytać ponownie, a PNG, PDF i SVG mają poprawną zawartość | `tests/test_export.py` |
| Błędny plik nie zamyka okna, listy kolumn się odświeżają | `tests/test_gui_smoke.py` |

Okno było budowane w testach i rysowało wykres. Systemowe okno wyboru pliku jest modalne, więc test wywołuje metodę, którą przycisk uruchamia po wybraniu ścieżki. Nie klikałem tego okna ręcznie.

Podczas rysowania mapy korelacji Seaborn wypisuje własne ostrzeżenie o przyszłej zmianie palety. Testy mimo to przechodzą.

## Znane ograniczenia

- W repozytorium nie ma oficjalnego, zmodyfikowanego zbioru California Housing. Przykład w `examples` ma tylko osiem rekordów.
- Automatycznie rozpoznawane kodowania to UTF-8 (także ze znacznikiem BOM) i ręcznie wskazane cp1250. Inne kodowanie trzeba podać wprost. Program go nie zgaduje.
- Separator jest odczytywany z nagłówka. Przy takiej samej liczbie przecinków i średników trzeba wybrać go ręcznie.
- Wykres słupkowy przyjmuje kolumnę kategorialną i odrzuca kolumnę o więcej niż 20 różnych wartościach.
- Wykres pudełkowy rysuje Matplotlib. Ta wersja Seaborna przekazuje argument, który Matplotlib 3.11 już wycofuje. Pozostałe wykresy i mapa korelacji używają Seaborna.
- Brak w komórce nie jest traktowany jak wartość poza zakresem ani jak outlier.

## Źródła i narzędzia

- pandas.read_csv: https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html
- Pandas User Guide: https://pandas.pydata.org/docs/user_guide/index.html
- Matplotlib, osadzanie w Tkinter: https://matplotlib.org/stable/gallery/user_interfaces/embedding_in_tk_sgskip.html
- Seaborn: https://seaborn.pydata.org/tutorial.html
- Instrukcja przedmiotu: Programowanie V, Projekt 2, wersja z 5 października 2026. Fragment z klasą `DataSession` jest wzorem z tej instrukcji. Strategie braków, reguły, pozostałe wykresy, okno i testy są dopisane w projekcie.

Do pomocy w przygotowaniu kodu, testów i tego opisu użyto asystenta w Cursorze.
