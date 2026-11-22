# DoxForge: Odwołania2json

Zamiana dokumentów PDF sprawy odwoławczej KIO (odwołanie, pełnomocnictwo, potwierdzenie przelewu)
na JSON w formacie organizatora [HackNation 2026 / UZP](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026).
Analiza zadania i plan: [ANALIZA.md](ANALIZA.md).

**Stan: gotowe do oceny.** Działa CLI (`./start.sh`: `input/` → `output/`, JSON na każdy PDF, `{}` dla
dokumentu spoza klas) i webowy podgląd ekstrakcji (`./start_gui.sh`). LLM: `ministral-8b-2512`
(Apache-2.0) przez API Mistral AI w UE. Szczegóły pomiarów i decyzji:
[raport z przeglądu](docs/reviews/2026-11-22-claude-review.md).

| Pomiar (proxy metryki z Discorda, F1) | Wynik |
|---|---:|
| hold-out: 15 skanów + 4 negatywy, mierzony jednorazowo | 0,981 (exact 0,955) |
| inne łamanie linii, świeże seedy (layout spoza train) | 0,9998 |
| zdegradowane skany 150 dpi, świeży zbiór | 0,994 |
| 150 PDF na VM oceniającej (2 vCPU), zimny cache | 625 s |

Zbiór train służył do budowy reguł, więc jego wynik nie jest miarą generalizacji. Odporność
mierzymy na zbiorach pochodnych z tym samym goldem (`scripts/stress_reflow.py`,
`scripts/augment_scans.py`).

### Zgodność z wymaganiami zadania

| Wymaganie | Jak |
|---|---|
| Python 3.13+, `requirements.txt`, prosta komenda | `pip install -r requirements.txt`, `./start.sh` / `start.bat`; VM: `setup_vm.sh`, `RUN.md` |
| Tylko open source, CPU bez GPU, LLM po API w UE | OCR lokalnie (PP-OCR, Tesseract); Ministral 3 8B przez `api.eu.mistral.ai` |
| Rozpoznanie typu po treści, JSON zgodny z `oczekiwane-json` | klasyfikacja wzorcami z YAML; walidacja wyniku JSON Schema |
| Nowe typy bez zmiany kodu | jeden plik `config/doc_types/<typ>.yaml` albo rejestracja w GUI |
| Transparentność ekstrakcji | GUI podświetla źródło wartości; każda wartość z LLM weryfikowana z tekstem |
| Skany | ensemble OCR, budżet czasu 2640 s z przełączaniem na jeden silnik |

## Jak to działa

1. **Odczyt** (`doxforge/ingest.py`): warstwa tekstowa PDF (pypdfium2). Strony bez niej (skany) przechodzą
   przez ensemble OCR na CPU (`doxforge/ocr.py`): PP-OCRv6 Small, PP-OCRv5 Latin Mobile
   i Tesseract LSTM pol+eng, maksymalnie 4 pierwsze strony bez tekstu. Odczyty są dopasowywane
   przestrzennie, a głosowanie wybiera całe zaobserwowane wiersze. Wynik trafia do `.cache/ocr/`.
2. **Klasyfikacja** (`doxforge/classify.py`): suma wag wzorców z sekcji `classification` pliku klasy.
   Poniżej progu dokument nie należy do żadnej klasy i dostaje `{}`.
3. **Ekstrakcja** (`doxforge/extraction/`): reguły z sekcji `extraction`, potem złożenie JSON-a według
   schematu klasy. Pole bez pewnej wartości ma `null`.
4. **LLM** (`doxforge/llm/`, gdy włączony i klasa ma sekcję `llm`): model dostaje tekst, instrukcje klasy,
   słowniki, schemat (wymuszony JSON Schema) i wartości z reguł. Każdą wartość z modelu normalizujemy
   typem pola (`x-type`), sprawdzamy ze schematem, a pola `x-source: text` muszą występować w dokumencie
   (dopasowanie odporne na OCR). Scalanie pole po polu: polityka `rules` albo `llm` z sekcji `llm.policies`.
   Gdy LLM zawiedzie (sieć, limit, zły JSON), dokument dostaje wynik samych reguł.

Dokumenty są przetwarzane równolegle (`PIPELINE_WORKERS`). PDFium i strony OCR są szeregowane;
wewnątrz jednej strony trzy niezależne silniki OCR działają współbieżnie z ograniczoną liczbą wątków.
Błąd jednego dokumentu zapisuje `{}` i traceback w logu, reszta partii idzie dalej.

## Uruchomienie

Wymagany Python 3.13+. Wagi OCR należy pobrać przed mierzonym przebiegiem (łącznie ok. 69 MiB).
Koła `tesserocr` dla obsługiwanych platform zawierają Tesseract i Leptonicę; nie wymagają osobnego CLI.
Instalacja ze źródeł wymaga tych bibliotek systemowych.

```bash
pip install -r requirements.txt
cp .env.example .env  # uzupełnij LLM_API_KEY albo ustaw DOXFORGE_LLM=none (tylko reguły)
python -m scripts.prepare_ocr  # pobierz i sprawdź wagi przed startem pomiaru
./start.sh            # Windows: start.bat
```

### Interfejs webowy (GUI)

Webowy podgląd ekstrakcji (Streamlit) uruchamiany lokalnie:

```bash
pip install -r requirements.txt -r requirements-web.txt
./start_gui.sh            # albo: streamlit run app.py; domyślnie port 8080, dostęp z sieci
```

Co daje (cała logika w `webapp/`, rdzeń pipeline'u bez zmian):

- **Podgląd przejrzystości** — wrzuć PDF (jeden lub całą sprawę), uruchom ekstrakcję w przeglądarce;
  dokument po lewej, pola po prawej. Kliknięcie pola podświetla miejsce w tekście, skąd pochodzi
  wartość (strona + fragment). Wartości znormalizowane/wywnioskowane dostają etykietę zamiast
  fałszywego podświetlenia.
- **Pewność ensemble** — kolor pola według zgodności modeli (🟢 pełna, 🟡 większość, 🔴 rozbieżność,
  ⚪ brak sygnału, 🔧 wartość z reguł). Sygnał pojawia się w trybie OpenRouter, gdy skonfigurowano
  kilka modeli (`OPENROUTER_MODEL` po przecinku) lub `OPENROUTER_SAMPLES>1`; przy jednym modelu
  (domyślny Mistral) pole pokazuje brak sygnału.
- **Rejestracja typu** — dodaj nowy typ dokumentu z przeglądarki: Mode B (podajesz JSON Schema)
  lub Mode A (LLM proponuje schemat z PDF). Zapisuje jeden plik `config/doc_types/<nazwa>.yaml`;
  typ jest rozpoznawany klasyfikatorem LLM przy kolejnym wgraniu. Dokument spoza typów nadal daje `{}`.

Z `DOXFORGE_LLM=none` GUI działa w trybie regułowym (bez pewności ensemble i Mode A).

Na maszynie oceniającej (Ubuntu 24.04 z Pythonem 3.12, 2 vCPU) projekt leży w `/opt/solution`.
`./setup_vm.sh` instaluje tam Pythona 3.13 przez uv, zależności i modele OCR. Komenda oceny
jest w `RUN.md`. W trybie z budżetem OCR przechodzi na sam PP-OCRv6 dla stron, które w trybie
ensemble nie zmieściłyby się w czasie (ensemble 16,4 s/stronę, pojedynczy 6,0 s/stronę na 2 vCPU).

### Ensemble OCR

`DOXFORGE_OCR=ensemble3` jest domyślny i ustawiony w `.env.example`.
`DOXFORGE_OCR=single` przywraca wcześniejszy PP-OCRv6 i jego cache.
`DOXFORGE_OCR_THREADS=1` ogranicza wątki ONNX na silnik. Tesseract ma limit czasu 20 s na stronę;
strony są przetwarzane kolejno, aby trzy silniki nie mnożyły liczby aktywnych kopii modeli.

Modele rozpoznawania: PP-OCRv6 Small 20,25 MiB, PP-OCRv5 Latin Mobile 7,54 MiB,
Tesseract `tessdata_best` pol+eng 26,11 MiB. Dwa modele PP-OCR mają różne detektory (9,47 i 4,60 MiB);
wspólny plik klasyfikatora zajmuje 0,56 MiB. Jest to ensemble trzech modeli, z dwiema rodzinami
architektur: PP-OCR i Tesseract. Nie są to trzy niezależne rodziny.

Głosowanie uwzględnia geometrię wiersza, zgodność tekstów i pewność. Różne cyfry nie są uznawane
za zgodę; nie składamy nowych identyfikatorów z fragmentów wyników. Pewność różnych silników
nie jest skalibrowana, więc przy braku zgodności zachowujemy wiarygodny odczyt główny.
Przy zbliżonej pewności i zgodzie preferujemy modele z pełną obsługą polskich znaków.
Wagi Tesseract są przypięte do rewizji i sprawdzane SHA-256; RapidOCR sprawdza własne wagi.
Cache ensemble jest oddzielny od pojedynczego OCR. Wynik po awarii silnika jest użyteczny
w bieżącym przebiegu, lecz nie trafia do cache jako pełny ensemble.

Pomiar bez LLM: `python -m scripts.benchmark_ocr --out runs/ocr-NOWY --train-run runs/typed-14b`.
Porównuje 4 niezależne obrazy syntetyczne i pierwsze strony znanych skanów train;
nie odczytuje etykiet ani holdoutu. Szczegóły i ograniczenia pomiaru:
[raport OCR](docs/reviews/2026-11-22-ocr-ensemble.md).

Źródła modeli i interfejsów: [RapidOCR](https://rapidai.github.io/RapidOCRDocs/main/en/model_list/),
[tesserocr](https://github.com/sirfz/tesserocr),
[tessdata_best](https://github.com/tesseract-ocr/tessdata_best).

### LLM (opcjonalny)

Domyślnie (`DOXFORGE_LLM=openai_compatible`) model `ministral-8b-2512` jest wywoływany
bezpośrednio przez API Mistral AI w regionie UE (`LLM_BASE_URL=https://api.eu.mistral.ai/v1/`,
`LLM_MODEL`, `LLM_API_KEY` w `.env`). Klient sam dopasowuje odstęp między zapytaniami do limitu
z nagłówka `x-ratelimit-limit-req-minute` (użyty klucz: 750/min). Uruchom `./start.sh` lub `start.bat`.

Alternatywa, wyłączona: OpenRouter (`DOXFORGE_LLM=openrouter`, `OPENROUTER_*` w `.env.example`).

Żądania OpenRouter zawierają `provider.only`, `allow_fallbacks=false`,
`require_parameters=true` oraz `data_collection=deny`. Model musi obsługiwać JSON Schema.
Domyślny endpoint `mistral/eu` był dostępny w katalogu OpenRouter podczas konfiguracji.
Zmiana dostawcy lub regionu ma osobny cache. Historyczny cache bezpośredniego Mistrala EU pozostaje dostępny.

**Lokalizacja przetwarzania przed oceną UZP wymaga weryfikacji.** Endpoint
`mistral/eu` określa upstream, a standardowa brama OpenRouter nie gwarantuje UE/EOG
dla całej ścieżki. Opcjonalne `OPENROUTER_BASE_URL=https://eu.openrouter.ai/api/v1/`
wymaga uprawnień Business/Enterprise. Dokumentacja tej bramy opisuje Europę obejmującą
również UK i Szwajcarię. Należy potwierdzić zakres EU/EOG z dostawcą i organizatorem.
[Routing OpenRouter](https://openrouter.ai/docs/guides/routing/provider-selection),
[opis granic regionu](https://openrouter.ai/blog/announcements/us-in-region-routing/).

HTTP 402 oznacza problem z kredytami, 401 z kluczem, a 404 może oznaczać brak endpointu
spełniającego filtry lub region. Przy 429 klient ponawia z respektowaniem Retry-After.
W trybie konkursowym zachowuje ukończone wyniki reguł po wyczerpaniu budżetu.
Dla ograniczenia tempa ustaw np. `OPENROUTER_MIN_INTERVAL_S=6` dla ok. 10 zapytań/min.


Konfiguracja w `.env` (plik poza gitem; zmienne środowiskowe mają pierwszeństwo):

| Zmienna | Znaczenie |
|---|---|
| `DOXFORGE_LLM` | `openrouter`, `openai_compatible` albo `none` (tylko reguły); wymagana |
| `OPENROUTER_API_KEY` | klucz OpenRouter, używany wyłącznie przy `DOXFORGE_LLM=openrouter` |
| `OPENROUTER_BASE_URL` | domyślnie `https://openrouter.ai/api/v1/`; dostępna też brama regionalna |
| `OPENROUTER_MODEL` | domyślnie `mistralai/ministral-8b-2512`; kilka modeli po przecinku = ensemble z głosowaniem |
| `OPENROUTER_SAMPLES` | próbki na model w ensemble (domyślnie 1, deterministycznie) |
| `OPENROUTER_PROVIDERS` | endpointy rozdzielone przecinkami, domyślnie `mistral/eu` |
| `OPENROUTER_MAX_TOKENS` | limit długości odpowiedzi, domyślnie 4096 |
| `OPENROUTER_MIN_INTERVAL_S` | odstęp między wywołaniami wszystkich wątków, domyślnie 1,2 s |
| `LLM_BASE_URL` | adres API zgodnego z OpenAI, np. `https://api.eu.mistral.ai/v1/` (Mistral, serwery w UE) |
| `LLM_MODEL` | model z otwartymi wagami, np. `ministral-8b-latest` (Ministral 3 8B, Apache-2.0) |
| `LLM_API_KEY` | klucz API |

Bez `DOXFORGE_LLM` albo z brakującą zmienną wybranej opcji aplikacja nie startuje. Zmiana dostawcy
(Scaleway, OVHcloud, vLLM) albo modelu to tylko zmiana `.env`. Odpowiedzi LLM trafiają do `.cache/llm/`.

Aplikacja czyta `./input/<sprawa>/<plik>.pdf` i zapisuje `./output/<sprawa>/<plik>.json`
(nazwa JSON-a taka jak nazwa PDF-a). Dokument spoza skonfigurowanych klas dostaje `{}`.

## Klasy dokumentów (konfiguracja zamiast kodu)

Każda klasa to jeden plik `config/doc_types/<klasa>.yaml`:

- `name`, `description`: nazwa i opis klasy;
- `vocabularies`: słowniki wartości (np. powody odwołania 1–12);
- `schema`: JSON Schema wyniku. Każde pole ma `x-source`:
  - `text`: wartość występuje w dokumencie dosłownie,
  - `normalized`: wartość sprowadzona do formatu, np. data ISO,
  - `derived`: wartość ustalana z treści.

  Opcjonalne adnotacje pola: `x-type` (typ i normalizacja), `x-evidence: header-line` (wartość z LLM
  tylko z linii „Miejscowość, dnia …”), `x-spelling: majority` (pisownia identyfikatora z OCR według
  większości powtórzeń w dokumencie), `x-null-when-singleton`.

- `classification`: próg `min_score` i wzorce z wagami (`zone: title` to pierwsze linie, `body` to pierwsze strony);
  `similar: <słowo>` dopuszcza jeden błąd OCR w krótkiej linii tytułu;
- `extraction`: nazwane sekcje tekstu (`sections`) i reguły dla pól (`fields`, klucz to ścieżka pola,
  np. `Odwolujacy[].nip`). Lista reguł oznacza kolejne próby: wygrywa pierwsza niepusta.

Wzorce piszemy dla tekstu „złożonego”: małe litery, bez polskich znaków (`ł` -> `l`). Dzięki temu pasują
też do OCR, który gubi ogonki.

| Reguła (`kind`) | Co zwraca |
|---|---|
| `labeled` | wartość przy etykiecie: ta sama linia, następna albo okno znaków; `all: true` daje listę |
| `first` | pierwszą (albo `occurrence`-tą) wartość typu w zakresie; `all: true` daje listę |
| `regex` | grupę `value` wyrażenia regularnego |
| `first_line` | pierwszą niepustą linię sekcji (np. nazwę pod „Odwołujący:”) |
| `quoted` | tekst w cudzysłowie za etykietą (np. tytuł zamówienia po „pn.”) |
| `persons` | osoby z tytułem zawodowym (adw., r. pr., radca prawny), bez tytułu |
| `signatories` | osoby podpisane pod linią kropek |
| `flag` | jedną z dwóch wartości zależnie od obecności wzorca |
| `first_match` | wartość przypadku, którego wzorzec występuje (`pick: earliest` wybiera najwcześniejszy) |
| `vocabulary` | opis ze słownika dla wartości innego pola |

Typy wartości (`type`): `nip`, `regon`, `krs`, `pesel`, `iban`, `amount`, `date`, `email`, `bzp`, `ted`,
`token`, `text`, `name`, `organization`. Normalizują do formatu organizatora, np. daty słowne do ISO, rachunek do 26 cyfr.

`extraction.entities` przypisuje identyfikatory, podpisujących i flagi (`flags`, np. „prowadzący
postępowanie” w punkcie listy) do właściwego podmiotu według bloków dokumentu.

Nową klasę dodaje się nowym plikiem YAML. Błędna konfiguracja (niepoprawny schemat, powtórzona nazwa,
reguła dla nieistniejącego pola, nieznana sekcja albo słownik, zły regex) zatrzymuje aplikację przy starcie.

## Ewaluacja

```bash
pip install -r requirements-dev.txt
./scripts/fetch_dataset.sh                                   # zbiór organizatora -> data/ (poza gitem)
python -m doxforge prepare-eval --split train --out runs/r1  # input/ + gold/, losowe nazwy plików
DOXFORGE_LLM=none python -m doxforge run --input runs/r1/input --output runs/r1/output  # albo z LLM z .env
python -m doxforge score runs/r1                             # tabela F1 + runs/r1/report.json
```

- **Podział zbioru** (`config/evaluation.yaml`):
  - `train`: 60 spraw cyfrowych, skany 0001–0005 (15) i 12 negatywów;
  - `holdout`: skany 0006–0010 (15) i 4 negatywy, na których nie stroimy reguł.

  Test pilnuje, żeby żaden plik kontrolny nie miał kopii binarnej w części `train`.
  Podział skanów wykonano po pierwszym pomiarze zbiorczym; nie zastępuje nowego testu spoza generatora.
  Wszystkie sprawy cyfrowe były używane do rozwoju reguł/promptów.
- **Losowe nazwy plików** (domyślnie, `--keep-names` je wyłącza): klasyfikacja nie może zależeć od nazwy pliku.
- **Warianty F1**: organizator nie opisał dokładnej metody, więc raportujemy 4 warianty.
  - `ignore`: pary null/null pomijane; `as_value`: poprawny null to TP.
  - `strict`: dokładna równość; `lenient`: bez różnic w spacjach i wielkości liter.

  Wariant główny to `ignore/strict`. Zła wartość liczy się jako FP+FN, a `null` tylko jako FN.
  Elementy list są parowane niezależnie od kolejności.

### Testy odporności (bez nowych etykiet)

```bash
python -m scripts.stress_reflow --source runs/R --out runs/reflow-5 --seed 5 --reference GOLD  # inne łamanie linii
python -m scripts.augment_scans --source runs/R --out runs/aug-hard --count 30 --seed 11 --hard  # trudne skany
python -m scripts.measure_run --source runs/aug-hard --out runs/aug-hard-8b --reference GOLD
```

Gold się nie zmienia, więc oba zbiory mierzą odporność na układ i jakość skanu. Poprawki szuka się
na jednym seedzie, a sprawdza na świeżym; nie stroi się reguł pod pojedyncze wartości.
Wyniki: `docs/reviews/2026-11-22-claude-review.md`.

## Jakość kodu

```bash
python -m ruff format --check . && python -m ruff check . && python -m mypy doxforge && python -m pytest
```

Testy z markerem `dataset` sprawdzają zbiór organizatora:

- skład zbioru i brak przecieku między train i holdout;
- zgodność etykiet ze schematami, z listą znanych odstępstw;
- obecność wartości tekstowych w PDF;
- spójność odwołania z pełnomocnictwem.

Bez pobranego zbioru te testy są pomijane.

## Porównania bez wywołań API

```bash
python -m scripts.replay_cache --out runs/replay-NOWA-NAZWA
python -m scripts.policy_cv runs/replay-NOWA-NAZWA/replay.json
```

Replay porównuje cztery stałe polityki na wspólnej części train z dostępną odpowiedzią LLM.
Raportuje braki cache i nigdy nie wysyła zapytań. CV grupuje sprawy, ale wcześniejsze strojenie
na całym zbiorze oznacza, że wynik nadal jest diagnostyką rozwojową.

Przy limicie obserwowanego klucza 2 zapytania/min 150 wywołań nie mieści się w 45 min.
Płatne API jest dopuszczalne według opublikowanych wymagań; trzeba sprawdzić jego faktyczne
RPM/TPM, wersję otwartego modelu i zakres UE/EOG. Wymagany jest pełny benchmark bez cache
na docelowym kluczu. Szczegóły i źródła: [CR](docs/reviews/2026-11-21-pipeline-cr.md).

Skrypty startowe stosują budżet 2640 s do całego OCR i ekstrakcji, także bez LLM.
Najpierw tworzą poprawny JSON `{}` dla każdego wejścia, następnie atomowo zapisują ukończone
wyniki reguł. Pozostały czas przeznaczają na LLM, zaczynając od skanów i dokumentów z brakami.
Po wyczerpaniu czasu zatrzymują proces roboczy; ukończone wyniki pozostają, a nieprzetworzone
dokumenty mają `{}`. Komplet plików nie oznacza kompletnej ekstrakcji. Długi OCR może zużyć
budżet przed etapem LLM; pełny pomiar 150 skanów nadal jest wymagany.
Opcję można przekazać wprost przez `python -m doxforge run --budget-seconds 2640`.
Wersja CLI bez tej opcji zachowuje dotychczasowy tryb zbierania wszystkich odpowiedzi.
Opis algorytmu, pomiary i zakres testów zawiera [raport eksperymentu](docs/reviews/2026-11-21-evidence-budget.md).

Doprecyzowania z Discorda UZP zmieniają format rachunków na zapis bez whitespace
z zachowanym kodem kraju ze źródła. Historyczne goldy często usuwały PL błędnie.
Podane wcześniej exact F1 nie odtwarza zapowiedzianego progu 80% dla wybranych tekstów.
Szczegóły i nierozstrzygnięte kwestie są w [raporcie z rozmowy](docs/reviews/2026-11-21-discord-clarifications.md).

Wariant opisany na Discordzie można wybrać poleceniem
`python -m doxforge score runs/NAZWA --metric discord-proxy`.
Ma nazwę `ignore/discord_ratio80_proxy`. Używa progu 80 w `rapidfuzz.fuzz.ratio`
po ujednoliceniu whitespace i wielkości liter, wyłącznie dla pól wskazanych przez UZP.
Błędna obecna wartość daje FP, brak oczekiwanej wartości FN. Raport opisuje te założenia.
Miara podobieństwa i agregacja wymagają potwierdzenia oficjalnym skryptem.
Dotychczasowy wariant pozostaje dostępny przez `--metric historical`.

Klient zapisuje w logach model, dostawcę, ID żądania, tokeny wejścia/wyjścia i koszt
z usage odpowiedzi API. Nie zapisuje w tych wpisach klucza ani treści dokumentu.
Raport przebiegu można wygenerować przez `python -m scripts.audit_run runs/NAZWA`.
Osobny katalog cache do pomiaru od zera ustawia `DOXFORGE_CACHE_DIR=/absolutna/sciezka`.
[Pomiar działającego OpenRoutera](docs/reviews/2026-11-22-openrouter-live.md) opisuje próbki,
czas, koszt, znane błędy etykiet i ograniczenia wniosków.

Eksperymentalna reguła `select_entity: true` może wskazywać podmiot wymaganej roli,
np. nazwę po etykiecie lidera konsorcjum. Działa wyłącznie po udanej ekstrakcji wartości
w polu listy. Schemat może też oznaczyć `x-null-when-singleton: true` na polu,
które ma być puste przy jednoelementowej liście, np. fladze lidera pojedynczego zamawiającego.
Oba mechanizmy korzystają z konfiguracji klasy i nie wymagają nazw ani numerów z datasetu.

[Koncepcja CPU i próba morfologii](docs/reviews/2026-11-22-cpu-concept.md) opisuje
oddzielny eksperyment. Zależności z requirements-cpu-research.txt są opcjonalne.
