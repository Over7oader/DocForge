# Ekstrakcja z dowodami w tekście i ograniczonym czasem API

W trybie konkursowym oba skrypty startowe przekazują do CLI budżet 2640 sekund.
Pierwszy etap klasyfikuje wszystkie PDF-y, wykonuje OCR i zapisuje JSON z reguł.
Drugi etap kieruje do Mistrala w pierwszej kolejności dokumenty zawierające strony OCR,
a w obrębie grupy dokumenty z większą liczbą pustych pól. Każde skończone ulepszenie
zastępuje plik atomowo. Po upływie pozostałego czasu proces API kończy pracę.
Minuta rezerwy obejmuje m.in. start interpretera i zakończenie procesu. Gdy sam etap
lokalny trwa dłużej niż 45 minut, ta strategia nie zapewnia limitu dla partii.

Kolejność zależy od zawartości PDF i wyniku lokalnego. Liczba pustych pól to heurystyka.
Niektóre puste pola są prawidłowe. Parametry i reguły wyboru są takie same dla wszystkich spraw.

W trakcie scalania daty, kwoty i identyfikatory TED z modelu przechodzą kontrolę wystąpienia
ich postaci znormalizowanej w tekście. Osoby wymagają kolejnych członów imienia i nazwiska,
z tolerancją dla odmiany. Gdy odczyty prawie się zgadzają, algorytm zachowuje dosłowną
pisownię z warstwy cyfrowej. Wartość może zostać skrócona o etykietę rozpoznaną przez regułę dla pola.
Nadal możliwy jest wybór niewłaściwej daty lub osoby spośród kilku wymienionych w dokumencie.
Pola występujące w nieobsługiwanym formacie mogą zostać odrzucone.

## Pomiary

Wariant metryki we wszystkich pomiarach to lokalne micro-F1 ignore/strict.

| Próba | Wynik |
|---|---|
| Te same 97 odpowiedzi modelu, 93 cyfrowe i 4 skany z train | Hybryda 0,9890 przed zmianą i 0,9910 po zmianie. Dwa poprawione tytuły przelewów. |
| Kolejne 15 odpowiedzi z train przybyłych po pierwszym zapisie | 0,9944 przed zmianą i 1,0000 po zmianie. Te dokumenty pochodzą z tego samego zbioru roboczego. |
| Symulacja 56 zapytań na 112 dostępnych odpowiedziach train | Kolejność manifestu 0,9885. Priorytet OCR i liczby braków 0,9970. W obu przypadkach pozostałe dokumenty dostały wynik reguł. |
| Same reguły na 207 roboczych i 19 kontrolnych | Odpowiednio 0,9793 i 0,7786, wszystkie pliki obecne. |
| Próba czasowa CPU, same reguły | 150/150 wyników w 207,83 s, F1 0,9717. 123 cyfrowe, 15 skanów, 12 negatywów. Pusty cache tekstu OCR i wcześniej zainstalowane wagi. |
| Cztery własne PDF-y, jeden skan, same reguły | 0,9032. Po zmianie klasyfikacji notatka o pełnomocnictwie otrzymała pusty JSON. Pozostały błędy nazwisk i podpisu. |
| Konkurencyjny start przy odpowiedzi 429 z Retry-After 60 | Budżet 4 s, czas ścienny 4,71 s, 4/4 pełne pliki. Lokalny serwer testowy. |

Pomiar 150 PDF-ów dotyczył etapu reguł przed końcową zmianą sygnału klasyfikacji pełnomocnictw.
Cztery własne PDF-y służą do regresji i nie tworzą ślepego zbioru. Poprawna klasyfikacja notatki
nie zmieniła jej extraction F1, ponieważ wcześniej wynik zawierał wyłącznie wartości null.
Porównanie 56 zapytań symuluje dostępność odpowiedzi po wyborze dokumentu. Pomija czasy odpowiedzi
i retry. Nie jest zegarowym pomiarem 150 PDF-ów z żywym API.
Zbiór testowy organizatora pozostaje ukryty.

Raporty liczbowe w katalogu roboczym to `runs/grounding-v2/paired.json`,
`runs/grounding-v2/bench150/timed-score.json` i `runs/grounding-v2/control.json`.
Cztery PDF-y z etykietami znajdują się w `tests/fixtures/document_variants/`.
