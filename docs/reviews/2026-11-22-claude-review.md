# Przegląd zmian Codexa, poprawki i wdrożenie na VM (2026-11-22)

## Poprawione błędy (każdy z testem regresyjnym)

| Miejsce | Błąd | Poprawka |
|---|---|---|
| `extraction/ownership.py` `_bind_relation` | Podpisujący podmiotu bez nagłówka „W imieniu X:” był usuwany i kopiowany do ostatniego bloku z nagłówkiem (blok sięga końca tekstu) | Usuwamy tylko z podmiotu, który ma własny blok; nie kopiujemy osoby, którą trzyma podmiot bez nagłówka |
| `extraction/ownership.py` `_bind_identifiers` | Identyfikatory wypisane pod listą nazw wpadały do zakresu ostatniego podmiotu, a poprawny NIP/KRS wcześniejszego był zerowany jako „obcy” | Obcy jest tylko identyfikator, który jest jedynym identyfikatorem tego rodzaju w zakresie innego podmiotu |
| `ocr.py` `_parse_tsv` | `csv.reader` z domyślnym cytowaniem: jeden `"` z OCR wchłaniał resztę strony Tesseracta | `quoting=csv.QUOTE_NONE` |
| `llm/extract.py` `is_grounded` | Identyfikator z LLM przechodził jako fragment dłuższej liczby (9 cyfr z 14-cyfrowego REGON, 10 cyfr z rachunku); przybliżone dopasowanie akceptowało inną cyfrę w numerze postępowania (`ZP.271.16` vs `ZP.271.15`) | Identyfikator musi stać między nie-cyframi; przy dopasowaniu przybliżonym cyfry muszą być identyczne (z tolerancją l/o/i obok cyfr, typowe pomyłki OCR) |
| `budget.py` `_baseline` | Wyjątek w postprocessingu jednego dokumentu zatrzymywał cały proces roboczy (bez etapu LLM dla wszystkich) | Postprocessing w tym samym `try` co ekstrakcja; dokument dostaje `{}`, partia idzie dalej |
| `requirements.txt` | `cv2` importowany wprost, a nie było go w wymaganiach | `opencv-python-headless==5.1.0.95` |

## Czas na maszynie oceniającej (2 vCPU)

Pomiar `taskset -c 0,1`, 12 stron skanów train, bez cache: **ensemble3 16,4 s/stronę, sam PP-OCRv6
6,0 s/stronę**. Przy dużym udziale skanów ensemble przekroczyłby 44 min i zostawił puste wyniki.
Dodano `OCRPace` (`ocr.py`): w trybie z budżetem proces roboczy liczy strony do OCR i dla każdej
strony wybiera ensemble tylko wtedy, gdy wszystkie pozostałe strony zmieszczą się w czasie nawet
na jednym silniku. Inaczej strona idzie przez PP-OCRv6 (wynik nie trafia do cache ensemble).
Tempo jest korygowane średnią z rzeczywistych czasów. Na etap LLM zostaje 480 s.

## Pomiar kontrolny (jednorazowy, bez strojenia)

`runs/m6-holdout`: skany 0006–0010 (15 PDF) i 4 negatywy, OCR ensemble3, LLM
`mistralai/ministral-14b-2512` (OpenRouter, `mistral/eu`), 2 rdzenie, pusty cache: 447 s.

| Metryka | M4 (reguły, 1 OCR) | Teraz |
|---|---:|---:|
| historyczny exact F1 (ignore/strict) | 0,779 | **0,949** |
| proxy Discorda (ratio80) | – | 0,981 |

Klasyfikacja 19/19. Błędy: 2× `Odwolujacy[].nazwa`, 1× czynność, tytuły przelewów (exact).
Ten zbiór był już raz mierzony w M4, więc nie jest dziewiczy; nie stroimy pod te błędy.

Train po poprawkach (ciepły cache, `runs/review-fixes-warm`): diagnostyczny F1 1,0
(przed poprawkami 0,9998), oryginalne goldy 0,9775 (różnice to prefiks PL, opisany wcześniej).

## Wdrożenie

`setup_vm.sh` instaluje Pythona 3.13 przez uv do katalogu projektu (VM ma 3.12), zależności
i modele OCR. `RUN.md`: `./start.sh` z `/opt/solution`.

## Test na VM oceniającej (team20, 2 vCPU / 15 GB)

Dokładnie jak w VM.md: 150 PDF train (123 cyfrowe, 15 skanów, 12 negatywów) w `/opt/solution/input`,
pusty `output`, `./start.sh` (jako team20; z `sudo` do sprawdzenia przez właściciela konta),
zimny cache: **615 s**, kod wyjścia 0, 150 JSON, 138 odpowiedzi LLM, 36 stron OCR w trybie ensemble.
Wynik zgodny z lokalnym: diagnostyczny F1 1,0, oryginalne goldy exact 0,951 (prefiks PL).
Po teście `input/` i `output/` na VM wyczyszczono.

## Wybór modelu i ensemble (reguła ustalona przed pomiarem)

Reguła: na train (207 dok.) najmniejszy największy model, którego F1 proxy na oryginalnych goldach
jest najwyżej 0,003 niższy od najlepszego, a F1 skanów najwyżej 0,01 niższy; remis → mniej wywołań.
Hold-out tylko jednorazowo, dla zwycięzcy. Dodatkowo `scripts/augment_scans.py`: 45 nowych skanów
z cyfrowych dokumentów train (obrót ±2°, rozmycie, szum, JPEG 35–70, seed 2026), gold bez zmian;
zbiór do porównań OCR/LLM, nie do strojenia reguł.

| Wariant | Train orig | Train skany (diag) | Nowe skany (diag) | Wywołania |
|---|---:|---:|---:|---:|
| same reguły | 0,9657 | 0,942 | 0,9715 | 0 |
| 3B | 0,9717 | 0,990 | 0,9901 | 1× |
| 3B ×4 próbki (głosowanie) | 0,9733 | 0,990 | 0,9921 | 4× |
| 3B ×5 próbek (głosowanie) | 0,9736 | 0,990 | 0,9921 | 5× |
| **8B** | **0,9761** | **0,994** | **0,9980** | 1× |
| 8B ×3 próbki | 0,9764 | 0,990 | 0,9980 | 3× |
| 14B | 0,9775 | 1,000 | 0,9970 | 1× |

Zwycięzca: **ministral-8b-2512**, teraz domyślny. 14B jest idealny tylko na train, na którym
strojono prompty; na nowych skanach nie jest lepszy od 8B. Ensemble nie poprawiają wyniku
na tyle, by uzasadnić 3–4× więcej wywołań. Kod ensemble zostaje (`OPENROUTER_MODEL` z listą
modeli, `OPENROUTER_SAMPLES`), domyślnie wyłączony.

Hold-out z 8B (`runs/m7-holdout-8b`): exact 0,949, proxy 0,981, identycznie jak 14B.

Dodatkowo: sprawdzanie płci nazwisk przymiotnikowych (-ski/-ska, -cki/-cka) przy weryfikacji
osób z LLM (`evidence.py`), żeby rdzeń bez 3 liter nie przepuszczał „Anna Kowalski”.

## Runda odporności: przebudowa pod niewidziane dane (bez strojenia pod wartości)

Train jest „wyuczony” (reguły i prompty powstawały na nim), więc do wykrywania słabości użyto
zbiorów, których przy strojeniu nie oglądano, z tym samym goldem:

- `scripts/stress_reflow.py`: cyfrowe dokumenty train z akapitami złamanymi na nowo (szerokość
  70–110, seed). Tytuły, punkty list, linie „Etykieta:”, wiersze tabel i linia „Miejscowość, dnia”
  zostają osobno, jak w prawdziwych pismach. Seed 1–2 posłużyły do znalezienia błędów,
  3 do kontroli w trakcie, 4 i 5 do walidacji końcowej.
- `scripts/augment_scans.py --hard`: trudniejsze skany (150 dpi, mocniejszy szum, rozmycie,
  JPEG 25–50, obrót do 3°). `aug-hard` (seed 7) do znalezienia błędów, `aug-hard2` (seed 11)
  do walidacji.

Najważniejsza obserwacja: same reguły spadają po zmianie łamania linii z 0,992 do 0,979–0,982,
czyli są dopasowane do układu wierszy szablonów train. LLM odzyskuje większość, ale zostawały
ogólne klasy błędów. Poprawki (każda jako zasada dokumentu/języka, z testem):

| Klasa błędu | Poprawka |
|---|---|
| LLM wymyślał datę/miejscowość pisma bez nagłówka | `x-evidence: header-line`: wartość z LLM tylko z krótkiej linii zakończonej datą |
| Nazwa organizacji z doklejonym adresem | `organization`: obcięcie „ul./al./pl./os. … numer” i kodu pocztowego |
| Wartość z nazwy pliku/URL w stopce wydruku | odrzucana, gdy występuje tylko w tokenie pliku/URL |
| OCR gubi polskie znaki (Stępień → Stepień) | wyrównanie wierszy silników, dodanie wyłącznie diakrytyków widzianych przez inny silnik |
| OCR: „PLO2”, „PL.64” w rachunku | litery O/I/l → cyfry i zbędna kropka tylko w ciągu o kształcie rachunku |
| OCR: „wraz Z serwisem”, „sp. Z 0.0.” | „z”/„w” między małymi literami i w formie prawnej → małe |
| Tytuł ze skanu z jednym błędem („Pehnomocnictwo”) → pusty dokument | klasyfikacja: `similar` (podobieństwo ≥ 0,9, ta sama końcówka) w krótkiej linii |
| Słowa kluczowe złamane między liniami (SWZ) | reguły `first_match`/`flag` szukają w tekście z końcem linii jako spacją |
| Etykieta sekcji w środku sklejonej linii | końce sekcji stron rozpoznają „Etykieta:” także w środku linii |
| Ucięta nazwa (JDG „DUDEK-TECH” / „Jarosław Dudek”) | pełna wartość z reguły, gdy dopisek to ≤ 4 słowa z wielkiej litery, bez cyfr |
| Dopisek „prowadzący postępowanie” pod punktem 1) | `flags` w `entities`: flaga dla punktu listy, w którym stoi dopisek |
| I/l/1 w powtarzanym identyfikatorze | `x-spelling: majority`: pisownia z większości powtórzeń w dokumencie |
| Płeć nazwiska z LLM (Anna Kowalski) | sprawdzenie końcówek -ski/-ska w wystąpieniach w tekście |

### Wynik końcowy (zamrożony kod, ministral-8b-2512, proxy Discorda, referencja diagnostyczna)

| Zbiór | Rola | Przed rundą | Po |
|---|---|---:|---:|
| train (207) | znany | 0,9986 | **1,0000** |
| nowe skany (45) | porównawczy | 0,9980 | **1,0000** |
| łamanie linii seed 1 | znajdowanie błędów | 0,9958 | 0,9993 |
| łamanie linii seed 3 | kontrola | 0,9983 | 0,9995 |
| łamanie linii seed 4 | walidacja | – | **0,9995** |
| łamanie linii seed 5 | walidacja (ostatni) | – | **0,9998** |
| trudne skany `aug-hard` | znajdowanie błędów | 0,946 | 0,9906 |
| trudne skany `aug-hard2` | walidacja | – | **0,9949** |
| hold-out (15 skanów + 4 neg.), raz | potwierdzenie | 0,981 (exact 0,949) | 0,981 (exact **0,955**) |

Same reguły na seedzie 3: 0,9818; hybryda 0,9995. Hold-out na 2 rdzeniach: 443 s.
Pozostałe błędy to głównie nieczytelne fragmenty trudnych skanów (zniszczona linia tytułu,
„DI”/„D1” przy jednym wystąpieniu); ich „naprawa” wymagałaby reguł pod konkretne dane.

VM po rundzie odporności (kod końcowy, zimny cache, 150 PDF train): 631 s, kod wyjścia 0,
150 JSON, 138 odpowiedzi LLM, proxy na referencji diagnostycznej 1,0. `input/` i `output/` wyczyszczone.

## Modele < 8B (maksimum punktów za rozmiar modelu)

Analiza błędów 3B tylko na zbiorach roboczych (train, nowe skany, aug-hard, seed 3); walidacja na
seedach 4–6, aug-hard2 i świeżym aug-hard3 (seed 23). Ogólne poprawki:

- nazwisko w płci z dokumentu: mały model zmienia „Halina Przybylski” na „Przybylska”; wartość ma być
  jak w dokumencie, więc przy zgodnym imieniu i rdzeniu przywracana jest końcówka ze źródła;
- zapis dosłowny: gdy reguła ma wartość dosłownie z tekstu, a LLM tę samą w innym zapisie
  („Spółka z o.o.”, „-” zamiast półpauzy), wygrywa zapis ze źródła;
- `x-null-unless`: `imie_nazwisko` mocodawcy tylko przy `typ = osoba_fizyczna`;
- polityka `rules` dla `pelnomocnik.imie_nazwisko` („pierwszy wymieniony”) i `mocodawca.typ`
  (CEIDG/KRS); obie reguły bez błędów na trzech zbiorach bez LLM.

| Zbiór | 3B przed | 3B po | 8B po |
|---|---:|---:|---:|
| train | 0,9972 | 0,9991 | 1,0000 |
| łamanie linii seed 4 / 5 | 0,9968 / 0,9966 | 0,9983 / 0,9983 | 0,9998 / 0,9998 |
| łamanie linii seed 6 (świeży) | – | 0,9983 | 0,9998 |
| aug-hard2 / aug-hard3 (świeży) | 0,9936 / – | 0,9936 / 0,9896 | 0,9949 / 0,9941 |
| błędy łącznie (9 zbiorów) | – | 52 | 18 |
| hold-out exact / proxy | – | 0,946 / 0,974 | 0,955 / 0,981 |

Ensemble 3B×5 nie poprawia wyniku w istotnym stopniu (65 vs 69 błędów przed poprawkami).
Wybór 3B vs 8B zależy od nieopublikowanej wagi punktów za rozmiar modelu względem F1.

## Dostawca LLM do oceny

Decyzja: `ministral-8b-2512` bezpośrednio przez API Mistral AI w regionie UE
(`DOXFORGE_LLM=openai_compatible`, `https://api.eu.mistral.ai/v1/`). OpenRouter wyłączony; na VM `.env`
zawiera tylko zmienne Mistrala i OCR. Limit klucza z nagłówków: 750 zapytań/min, 1 mln tokenów/min;
klient dopasowuje odstęp automatycznie. Kod na VM sprawdzony sumami SHA-256 względem lokalnego (93 pliki).
