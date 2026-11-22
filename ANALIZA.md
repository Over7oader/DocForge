Aktualizacja na podstawie dostarczonej rozmowy UZP z Discorda: [doprecyzowania](docs/reviews/2026-11-21-discord-clarifications.md). Starsze pomiary stosują lokalne exact F1 i historyczną konwencję IBAN.

# Odwołania2json: analiza zadania, mapa zależności i plan

Zadanie: HackNation 2026, UZP. Repo: https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026 (stan z 2026-11-21, commit `0b360a9`).

Aktualny przegląd kodu, wyniki porównań i blokery zgłoszenia: [CR 2026-11-21](docs/reviews/2026-11-21-pipeline-cr.md).
**0,9793 dotyczy M4/train bez LLM. Generalizacja i pełny limit 45 min nie zostały jeszcze potwierdzone.**

## CEL (GOAL)

**Uzyskać maksymalny F1 na ukrytym zbiorze. Discord UZP opisuje syntetyczne dokumenty oparte na prawdziwych sprawach, także skany. Unikać zapamiętywania wartości i nazw treningowych.**

Każdą zmianę oceniamy według trzech kryteriów, w tej kolejności:
1. F1 w walidacji krzyżowej po sprawach,
2. F1 na zbiorze kontrolnym (skany + nasze dokumenty spoza repo),
3. czas i rozmiar modelu.

Wynik na samym zbiorze treningowym nie jest argumentem. Reguła działająca tylko na szablonie generatora to dług, nie zysk.

## 0. TL;DR: najważniejsze decyzje

1. **Gemini odpada w gotowym rozwiązaniu.** Regulamin wymaga wyłącznie komponentów open source, w tym otwartych modeli, a LLM musi działać po API firmy, która przetwarza dane na serwerach w UE/EOG. Gemini jest zamknięty. Można go użyć najwyżej jako narzędzia deweloperskiego do audytu etykiet na danych syntetycznych. Bezpieczniej użyć do tego dużego otwartego modelu (patrz §5).
2. **W etapie 2 mniejszy LLM daje więcej punktów** (1–20 pkt, im mniej parametrów, tym lepiej). Dlatego architektura jest hybrydowa:
   - **Deterministyczna ekstrakcja** (regex, walidatory, normalizatory) dla ok. 60–70% pól. Te wartości są w tekście dosłownie i mają ścisły format: NIP, REGON, KRS, IBAN, kwota, daty, BZP, TED, e-mail, numer transakcji.
   - **Mały LLM** (3–8B) tylko dla pól semantycznych: powód czynności, wybór właściwej osoby lub podmiotu, mianownik nazwisk, tytuł, nazwa kancelarii.
   - **Pola `x-source: text` z LLM przechodzą grounding**; identyfikatory bez fuzzy fallback. Daty, kwoty i pola derived wymagają jeszcze pełnych dowodów źródłowych. Grounding nie dowodzi poprawnej roli podmiotu.
3. **Nie trenujemy/fine-tunujemy LLM-a.** Mamy 60 spraw syntetycznych z jednego generatora (`LAPFormalDoc`), a według doprecyzowania UZP test obejmie syntetyki na podstawie prawdziwych spraw. Fine-tuning nauczyłby model artefaktów generatora (dowody w §2). „Trening” ma tu sens tylko w trzech miejscach:
   - mały klasyfikator typu dokumentu, z klasą „inne” i progiem OOD,
   - dobór few-shotów i promptów,
   - kalibracja progów pewności.

   Wszystko pod walidacją krzyżową po sprawach i na odłożonym zbiorze skanów.
4. **Precyzja jest ważniejsza niż „wypełnianie na siłę”.** Dotychczasowy lokalny scorer liczy złą wartość jako FP+FN. Opis UZP z Discorda przypisuje ją do FP, a FN do braku wartości. Wymaga to potwierdzenia oficjalnym skryptem i aktualizacji ewaluacji. Gdy brakuje pewności lub dowodu w tekście, zwracamy `null`.
5. **Klasy dokumentów są definiowane w konfiguracji (YAML), nie w kodzie.** To 20 pkt w etapie 2 i wymóg „premiowany”: schemat, sygnały klasyfikacji, ekstraktory pól, normalizatory, walidatory i prompt per klasa.
6. **Najważniejsze ryzyko to OCR skanów.** Prawdziwe sprawy będą częściowo skanami. W repo jest 30 skanów (`dane/skany`) i to one są najlepszym przybliżeniem zbioru testowego. Trzeba je trzymać jako **hold-out** i nie stroić na nich reguł.

## 1. Wymagania twarde (checklista zgodności)

| # | Wymaganie | Źródło | Konsekwencja |
|---|---|---|---|
| R1 | Python **3.13+**, `requirements.txt` z przypiętymi wersjami | README §5 | Lokalnie jest 3.14.4, OK. Testować też na 3.13. |
| R2 | Wyłącznie open source, w tym LLM | PDF §5, README §5 | Ani Gemini, ani GPT, ani Mistral OCR (zamknięty). Modele z licencją Apache-2.0/MIT/Qwen/Llama. |
| R3 | On-prem **bez GPU**, LLM po API | PDF §5 | OCR lokalny na CPU, albo przez API, ale wtedy liczony jak LLM. |
| R4 | API LLM: firma z polityką prywatności, serwery w **UE/EOG**, dostarczony **klucz API** | README §6 | Np. `api.eu.mistral.ai`, Scaleway (fr-par), OVHcloud AI Endpoints, Nebius EU. Klucz z limitem wydatków. |
| R5 | Foldery `./input` i `./output` w katalogu głównym, drzewo spraw zachowane, **JSON nazwany jak plik wejściowy** (`x.pdf` → `x.json`) | README §3 | Np. `input/case1/foo.pdf` → `output/case1/foo.json`. |
| R6 | **Klasyfikacja po treści, nie po nazwie pliku** | README §3 | Nazwy plików w teście będą inne. Testować z losowymi nazwami. |
| R7 | Dokumenty „negatywne” → **pusty `.json`** | README §6 | Hard negatives: orzeczenie KIO, ogłoszenia BZP/TED, decyzje ministrów, broszura JPK. Format „pustego” jest niejasny (§11). |
| R8 | CLI lub wrapper (`start.bat`, `start.sh`); GUI jest punktowane dodatkowo | README §3 | CLI + prosty GUI (Gradio lub Streamlit) z podglądem dowodów. |
| R9 | **150 PDF w ≤ 45 min** (ok. 18 s/PDF) | README „Wyjątki” | Pomiar bez cache na docelowym kluczu. Współbieżność nie omija RPM/TPM; przy 2/min 150 wywołań przekroczy limit. OCR tylko tam, gdzie potrzebny. |
| R10 | Ocena: etap 1 = 100% F1; etap 2 = 50% F1 + 20 rozmiar LLM + 20 rozszerzalność + 10 dodatki | README §7 | Najpierw F1, potem mały model i konfigurowalność. |
| R11 | Link do GitHuba z działającym prototypem, tytuł, opcjonalnie dokumentacja z opisem LLM i własne testy jakości | README §4 | Raport z ewaluacji w repo (tabela F1 per pole). |

Organizatorzy zapowiedzieli zmiany w ocenie i punktacji LLM („stay tuned”). Trzeba **obserwować repo** (`git pull` codziennie) i kanały mentorów.

## 2. Wnioski z danych (zmierzone na repo)

**Skład zbioru:** 60 spraw (`przyklad_01..60`) z 3 dokumentami każda, 30 skanów (10 odwołań, 10 pełnomocnictw, 10 potwierdzeń) z etykietami, 12 negatywów w `negatywne/` i 5 negatywów wplecionych w sprawy (`przyklad_04, 10, 24, 37`).

**Profil PDF-ów:**

| Typ | n | Średnio stron | Warstwa tekstu |
|---|---|---|---|
| odwołanie | 60 | 5,1 | tak (180 z 190 PDF-ów pochodzi z generatora `LAPFormalDoc`) |
| pełnomocnictwo | 60 | 1,0 | tak |
| potwierdzenie przelewu | 60 | 1,0 | tak |
| skany (×3 typy) | 30 | 5,2 / 1 / 1 | **brak, potrzebny OCR**: obraz 200 dpi, JPEG, szum, plamy, pieczątki |
| negatywy | 12 | 10,3 | 3 bez warstwy tekstu |

**Ekstrakcyjność etykiet:** sprawdziłem, czy wartość z JSON-a występuje dosłownie w tekście PDF (po usunięciu spacji i myślników).

- Dosłownie w tekście (100%): `nazwa`, `nip`, `regon`, `krs` obu stron, `pelnomocnik.*`, `tytul`, `numer_postepowania`, `numer_bzp`, IBAN-y, `tytul` i `numer_transakcji` przelewu, nazwiska w pełnomocnictwie.
- Wymagają normalizacji:
  - wszystkie daty (słownie „1 sierpnia 2026”, `01-08-2026`, `12.07.2026` → ISO),
  - `numer_ted` (`2026/S154-999639` → `999639-2026`; dosłownie tylko 14 z 47),
  - `kwota` (`17,00 PLN` → `17.0`),
  - NIP z myślnikami (`999-295-65-50` → `9992956550`),
  - IBAN według Discorda UZP (23:00): usuwamy whitespace, zachowujemy PL lub inny kod kraju, jeśli wydrukowano go w źródle.
  - BZP (`2026/BZP 00767819/01` → `2026/BZP00767819/01`).
- Wymagają rozumienia: `czynnosc_powod_id`, `czynnosc_opis`, `czynnosc_dzien`, `przez-pelnomocnika`, `prowadzacy_postepowanie`, `typ` mocodawcy.

**Pułapki i konwencje etykiet** (każda to osobna reguła w konfiguracji i osobny test):

1. **`data` i `miejscowosc` odwołania są `null` w 15 z 60 przypadków**, zawsze obie naraz: brak nagłówka „X, dnia …”. Nie wolno brać daty z pełnomocnictwa ani z przelewu.
2. **`czynnosc_powod_id`:** specyfikacja mówi „wyłącznie pierwszy zarzut”. W syntetykach treść „Zarzut nr 1” to **losowy wypełniacz** niezwiązany z etykietą (np. `przyklad_14`: zarzut o „zaniechaniu wezwania”, etykieta 9 = unieważnienie). Etykietę koduje zdanie „Zamawiający w dniu <data> <czynność>”. W skanach (bliżej realnych) jest to pierwszy punkt listy „wnoszę odwołanie wobec: 1. …”. Model nauczony na syntetykach nauczy się tylko jednego szablonu, a to **główne ryzyko overfittingu**. Reguła: powód i data to „pierwsza zaskarżona czynność” z sekcji wstępnej lub petitum.
3. Rozkład `czynnosc_powod_id`: 4–10 przykładów na klasę, a klasa 12 ma tylko 1 przykład. Klasyfikacja regułami lub uczenie powodu na tak małej próbie jest niewiarygodne. Lepiej opisy klas w prompcie plus słownik synonimów w YAML.
4. **`pelnomocnik`:** `imie_nazwisko` to **pierwsza** z wymienionych osób. `nazwa` to kancelaria z „adres do korespondencji”. `mail` to e-mail z bloku Odwołującego. `przez-pelnomocnika` = 0 tylko raz na 70 przypadków.
5. **Nazwiska:** etykieta to forma z dokumentu bez tytułu („adw.”, „r. pr.”), nawet z niezgodnością rodzaju („Jolanta Zajkowski”). W realnych pełnomocnictwach często jest celownik lub dopełniacz („udzielam pełnomocnictwa Panu Janowi Kowalskiemu”), a wymagany jest mianownik. Do tego posłuży LLM, z opcjonalną weryfikacją Morfeuszem 2.
6. **`pesel` w etykietach jest stringiem** (`"68091502944"`), choć przykład w specyfikacji pokazuje liczbę. Trzymamy się etykiet: string, bo liczba gubi zera wiodące.
7. **Sumy kontrolne:** NIP w etykietach przechodzi checksum w 93 na 141 przypadków, IBAN w 61 na 138. Dane syntetyczne mają „999…” i niepoprawne sumy, więc **checksum to sygnał miękki** (podbija pewność), **nie filtr**. Na danych realnych będzie mocniejszy.
8. `Zamawiajacy` bywa podwójny (4 przypadki): wtedy `prowadzacy_postepowanie` = 1 lub 0, w przeciwnym razie `null`.
9. `mocodawca` bywa podwójny (konsorcjum, 7 przypadków). `typ` = `osoba_fizyczna` w 5 przypadkach i wtedy `imie_nazwisko` jest wypełnione.
10. W przelewie `numer_transakcji` jest `null` w 6 z 10 skanów (brak w dokumencie), a `tytul` jest kopiowany 1:1 z łamaniem linii zamienionym na spację.
11. **Hard negatives:** orzeczenie KIO (`KIO_KIO 2617-21.pdf`) zawiera „Odwołujący”, „Zamawiający”, NIP-y i daty. Ogłoszenia BZP/TED zawierają numery BZP/TED i tytuł zamówienia. Sam keyword matching ich nie odrzuci.

## 3. Architektura docelowa

```
input/**.pdf
   │
   ▼
[1] Ingest: pypdfium2/pdfplumber → tekst + bboxy per strona
   │   └─ brak warstwy tekstu / śmieciowy tekst? → [1b] OCR CPU (Tesseract 5 `pol` lub RapidOCR/PP-OCR), deskew/denoise
   ▼
[2] Klasyfikator typu (config-driven)
   │   a) reguły/słowa kluczowe z YAML (score)  b) TF-IDF char-ngram + LogReg (z klasą "inne")
   │   c) niepewne → mały LLM z opisami klas z YAML
   │   └─ "inne" → pusty JSON
   ▼
[3] Ekstrakcja pól wg YAML danej klasy
   │   a) ekstraktory deterministyczne (regex z etykietą kontekstu, np. "NIP" w bloku "Odwołujący")
   │   b) LLM (structured output / JSON schema) TYLKO dla pól oznaczonych `llm: true`
   │      + kandydaci z (a) podani w prompcie jako podpowiedź
   ▼
[4] Normalizacja + walidacja (python-stdnum: NIP/REGON/PESEL/IBAN; daty; formaty BZP/TED)
   │   grounding: każda wartość musi mieć dowód (span) w tekście, inaczej null
   │   reguły spójności między polami (opis ↔ id, data ≤ data pisma, konsorcjum ↔ lider)
   ▼
[5] Złożenie JSON wg schematu klasy + plik dowodów (`output/.../x.evidence.json` lub `--explain`)
   ▼
output/**.json   (+ raport, GUI z podświetleniem źródeł)
```

### Konfiguracja klasy (szkic `config/doc_types/przelew.yaml`)

```yaml
name: przelew
output_root: przelew_bankowy
classify:
  keywords: {strong: ["potwierdzenie przelewu", "rachunek beneficjenta", "tytułem"], negative: ["wyrok", "sygn. akt"]}
fields:
  z_rachunku:   {type: iban, near: ["z rachunku", "rachunek obciążany", "rachunek zleceniodawcy"], normalize: iban_digits}
  na_rachunek:  {type: iban, near: ["rachunek beneficjenta", "rachunek odbiorcy", "rachunek kontrahenta"], normalize: iban_digits}
  kwota:        {type: amount, near: ["kwota"], normalize: decimal}
  data_operacji:{type: date, near: ["data wykonania", "data transakcji", "zlecona data wykonania"], normalize: iso_date}
  numer_transakcji: {type: string, near: ["numer referencyjny", "nr ref"], nullable: true}
  tytul:        {type: text, near: ["tytułem", "opis", "szczegóły płatności"], llm: fallback}
```

Nowy typ dokumentu dodaje się jako nowy YAML z JSON Schema i polami, bez zmian w kodzie. To spełnia wymóg „premiowany” i daje 20 pkt w etapie 2. W dokumentacji warto pokazać demo: dodanie np. „przystąpienia do postępowania” jednym plikiem.

### Stack (wszystko open source)

| Warstwa | Wybór | Dlaczego |
|---|---|---|
| PDF | `pypdfium2` (szybki), `pdfplumber` (bboxy, układ dwukolumnowy) | Tekst z układem; layout ma znaczenie dla tabelek „etykieta: wartość”. |
| OCR | **Tesseract 5 + `pol`** jako baza (najszybszy na CPU, dobry na wielokolumnowych stronach), **RapidOCR** (modele PP-OCR na ONNX) jako alternatywa lub ensemble na trudnych skanach | Wybór po benchmarku na 30 skanach: CER na polskich znakach i czas/stronę. Benchmarki z sieci są ogólne, nie dla polskiego. |
| Klasyfikator | scikit-learn (TF-IDF + LogisticRegression, kalibracja) + reguły YAML | Interpretowalny, CPU, mały model w repo. |
| LLM client | `openai` SDK (endpoint zgodny z OpenAI) + `pydantic` (JSON schema) | Jeden kod dla Mistral/Scaleway/OVH; model i endpoint w konfiguracji. |
| Grounding (opcjonalnie) | wzorzec z Google **LangExtract** (każda ekstrakcja ma `char_interval`, brak spanu = odrzuć) albo własny fuzzy-match (`rapidfuzz`) | Transparentność jest premiowana. LangExtract obsługuje endpointy zgodne z OpenAI, ale wymuszenie schematu działa pewnie tylko u wybranych dostawców; własny matcher jest prostszy. |
| Walidacja | `python-stdnum` (pl.nip, pl.regon, pl.pesel, iban), `dateparser` lub własny parser polskich miesięcy | Gotowe sumy kontrolne zamiast własnych. |
| Morfologia | `morfeusz2` (opcjonalnie) | Weryfikacja mianownika nazwisk bez LLM. |
| GUI | Gradio | Upload PDF → JSON + podświetlone dowody; łatwe 10 pkt. |

## 4. Mapa zależności i kamienie milowe

```
M0 Setup/kontrakt ──► M1 Ewaluator(F1) ──┬──► M3 Klasyfikator ──┐
        │                                │                      ├──► M5 LLM-hybryda ──► M6 Odporność/real-data ──► M7 Wydajność/pakowanie ──► M8 Dokumentacja/GUI
        └──► M2 Ingest+OCR ──────────────┴──► M4 Ekstrakcja deterministyczna ──┘
```

Ścieżka krytyczna: **M1 → M4 → M5 → M6**. Ewaluator musi powstać pierwszy, bo bez niego każda zmiana to zgadywanie.

| Kamień | Zakres | Zależy od | Kryterium zakończenia (exit) |
|---|---|---|---|
| **M0** | Repo, Python 3.13, struktura `input/` `output/` `config/`, `start.sh` i `start.bat`, CI (pytest, ruff, mypy), loader YAML + JSON Schema per klasa | brak | `start.sh` na pustym `input/` kończy się bez błędu. Schemat waliduje wszystkie 150 etykiet. |
| **M1** | **Ewaluator**: spłaszczenie JSON do ścieżek liści, dopasowanie elementów list algorytmem węgierskim, normalizacja, micro-F1 i F1 per pole/typ, 2 warianty liczenia `null` (§7), raport różnic | M0 | Etykiety vs etykiety dają F1 = 1. Raport wskazuje pole i plik. |
| **M2** | Ingest: tekst + layout, wykrycie braku warstwy tekstu, OCR, pre-processing skanów (deskew, binaryzacja, usuwanie plam), cache wyników | M0 | CER/WER OCR na 30 skanach zmierzony, wybrany silnik, ≤ 3 s/stronę na CPU. |
| **M3** | Klasyfikator (reguły + LogReg + próg OOD) z klasą „inne” | M1, M2 | 100% na sprawach, 0 fałszywych klasyfikacji negatywów, test z **losowymi nazwami plików**. Leave-one-case-out. |
| **M4** | Ekstraktory deterministyczne dla pól formatowych + normalizatory + walidatory | M1, M2 | F1 per pole ≥ 0,98 na cyfrowych, raport na skanach. |
| **M5** | LLM dla pól semantycznych: prompt per klasa z YAML, structured output, few-shot dobierane z train, grounding, reguły spójności, abstencja | M3, M4 | Wzrost F1 vs M4 na CV. Porównanie 3 rozmiarów modeli (3B / 8B / 14B–24B) w tabeli F1 vs koszt. |
| **M6** | Odporność: test na skanach (hold-out), augmentacje (§6), odporność na zmiany layoutu, nazwy w przypadkach zależnych, dokumenty wielostronicowe, PDF z kilkoma dokumentami | M5 | Spadek F1 skany vs cyfrowe < 10 p.p. Brak regresji na CV. |
| **M7** | Wydajność i pakowanie: async, retry, timeouty, limity, budżet tokenów (tylko pierwsze 3–4 strony odwołania), `requirements.txt` z wersjami, klucz API przez `.env` | M5 | 150 PDF (miks z ok. 30% skanów) w < 20 min na laptopie bez GPU, czyli z ponad 2× zapasem. |
| **M8** | README (instalacja, konfiguracja, dodawanie klasy), opis LLM, raport testów jakości, GUI, diagram architektury | M5–M7 | Świeży klon + `pip install -r requirements.txt` + `start` działa. |

## 5. Strategia LLM

**Kandydaci** (otwarte wagi, API w UE; dostępność trzeba zweryfikować w dniu wyboru, bo katalogi zmieniają się co miesiąc):

| Model | Parametry | Licencja | Gdzie (UE) | Rola |
|---|---|---|---|---|
| Ministral 3 3B | 3B | Apache-2.0 | Mistral `api.eu.mistral.ai` | Najtańszy w punktacji. Sprawdzić, czy wystarczy przy grounding + regułach. |
| **Ministral 3 8B** | 8B | Apache-2.0 | Mistral EU | **Kandydat główny**: balans F1 i punktów. |
| Ministral 3 14B / Mistral Small 3.2 24B | 14–24B | Apache-2.0 | Mistral EU, Scaleway, OVH | Górny punkt odniesienia. |
| Qwen3.x (np. 8B, 27B, 35B-A3B MoE) | różne | Apache-2.0 | OVHcloud, Scaleway | Alternatywa. Przy MoE niejasne, czy liczą parametry aktywne czy całkowite: zapytać. |
| Bielik 11B v3 | 11B | Apache-2.0 | hosting PL/UE (Cyfronet, CloudFerro…) do weryfikacji | Polski model, mocny w fleksji. Sprawdzić, czy jest stabilne API z polityką prywatności UE. |
| Mistral Large 3 / Qwen-duży | duży | otwarte | Mistral EU, Scaleway | **Tylko narzędzie dev**: audytor etykiet i „nauczyciel” do generowania danych testowych, nie w finalnej aplikacji. |

**Zasady:**

- Model, endpoint i limity w konfiguracji. Klucz z env (`LLM_API_KEY`), nigdy w repo. Dla organizatora osobny klucz z limitem wydatków.
- `temperature=0`, structured output (JSON schema), jeśli dostawca to wspiera. W przeciwnym razie parsowanie przez `pydantic` + 1 retry z komunikatem błędu walidacji.
- **Do LLM idzie tylko to, czego regex nie rozwiąże.** Mniej tokenów to szybciej, taniej i mniej halucynacji.
- Prompt dostaje: opis pola z YAML (treść komentarzy z `oczekiwane-json`), słownik powodów 1–12 z synonimami, kandydatów z ekstraktorów i tekst pierwszych N stron.
- Odpowiedź LLM zawiera **cytat dowodu** dla każdego pola. Kod sprawdza, czy cytat jest w tekście (fuzzy ≥ 90 dla OCR). Brak dowodu = `null`.
- „Pętla z LLM” (pomysł z Gemini) ma sens w dwóch wersjach:
  1. **Dev, offline:** duży otwarty model przechodzi po wszystkich 150 parach PDF/etykieta i zgłasza podejrzane etykiety oraz niespójności. Wynik: lista do ręcznej weryfikacji i wiedza o konwencjach, nie automatyczna zmiana etykiet.
  2. **Runtime:** *self-consistency* tylko dla pól o niskiej pewności. Drugie zapytanie z innym ułożeniem kontekstu; przy niezgodności `null` albo wartość z dowodem. Ogranicza to koszt i czas (R9).

## 6. „Trening” i reguły przeciw overfittingowi

**Co trenujemy:** wyłącznie klasyfikator typu dokumentu oraz progi pewności i abstencji. Opcjonalnie dobór few-shotów. **Nie** fine-tunujemy LLM-a: 60 próbek z jednego generatora, test realny, a hosting fine-tune'u w UE jako „open source” jest wątpliwy.

**Reguły przygotowania danych:**

1. **Podział po sprawach** (`przyklad_XX` = grupa), nigdy po plikach: dokumenty tej samej sprawy dzielą nazwy i numery. Używamy `GroupKFold(k=5)` i leave-one-case-out dla reguł.
2. **Zbiór kontrolny:** obecnie skany 0006–0010 (15 PDF) + 4 negatywy. Wydzielono je po wcześniejszym wyniku zbiorczym wszystkich skanów, więc nie jest to całkowicie niezależny holdout. Nie stroimy pod kolejne wyniki. Potrzebny nowy zamrożony test spoza generatora. Historia: §16 i CR.
3. **Odchudzenie artefaktów generatora:** cechy klasyfikatora nie mogą zależeć od nazw banków, firm „Opalit”/„Belmoria”, numerów „999…” ani nagłówka `LAPFormalDoc`. Ablacja: usuwamy podejrzaną cechę i patrzymy, czy F1 nie spada.
4. **Losowe nazwy plików** w każdym przebiegu ewaluacji (R6).
5. **Augmentacje do testów odporności** (nie do treningu LLM):
   - render cyfrowych PDF-ów do obrazu + szum, rotacja ±2°, JPEG, plamy → OCR. Daje „skany” z etykietami za darmo, 180 sztuk zamiast 30;
   - permutacja kolejności bloków (Zamawiający przed Odwołującym), zmiana etykiet („NIP:” → „nr NIP”, „Numer identyfikacji podatkowej”);
   - formy zależne nazwisk („Panu Janowi Kowalskiemu”);
   - formaty dat i kwot („10.000,00 zł”, „10 000 PLN”, „dnia 1.08.2026 r.”).
6. **Realne dokumenty jako test jakościowy (bez etykiet lub z ręcznymi etykietami):** publiczne ogłoszenia BZP/TED (negatywy i źródło formatów numerów), orzeczenia KIO z bazy UZP (hard negatives; cytowane w nich fragmenty odwołań pokazują realny język petitum). Warto ręcznie oetykietować 5–10 realnych lub realistycznych odwołań: to najlepszy predyktor wyniku na teście.
7. **Każda reguła regex ma test jednostkowy z przykładem spoza syntetyków.** Regex pod jeden szablon generatora to overfitting.
8. **Dziennik eksperymentów** (CSV lub MLflow-lite): wersja configu, model, F1 CV, F1 hold-out, czas, koszt. Decyzje podejmujemy na CV, a hold-out tylko potwierdza.

## 7. Ewaluacja: własny scorer

Organizator nie opisał dokładnego liczenia F1. Budujemy scorer odporny na interpretacje i optymalizujemy pod najbardziej prawdopodobną:

- spłaszczenie do par `(ścieżka, wartość)`; listy (`Odwolujacy`, `Zamawiajacy`, `mocodawca`, `pelnomocnicy`, `podpisujacy`) dopasowane algorytmem węgierskim po podobieństwie;
- porównanie po normalizacji (trim, wielokrotne spacje, typ: `17.0` == `17.00`) **oraz** wariant ścisły (exact string);
- `null`: wariant A (pary z `null` po obu stronach ignorowane, zwykle przy F1 ekstrakcji) i wariant B (`null`=`null` liczone jako TP);
- dokument negatywny: poprawny pusty wynik = TP klasyfikacji, wypełniony = same FP;
- raport: F1 micro/macro, per pole, per typ, per źródło (cyfrowe vs skan), top-20 błędów z diffem.

**Polityka abstencji wynikająca z F1:** pole wypełniamy, gdy `P(poprawne) > ~0,5`. Wtedy oczekiwany zysk z wypełnienia jest dodatni. Progi kalibrujemy per pole na CV.

## 8. Transparentność (premiowana)

- Dla każdego pola: wartość, źródło (`regex:nip_near_label` / `llm:<model>` / `rule:derived`), strona, cytat/span, pewność, wyniki walidatorów (checksum OK/FAIL).
- Zapis jako osobny plik (`output/.../x.evidence.json` lub katalog `output_explain/`), żeby **nie psuć formatu ocenianego JSON-a**. Najpierw potwierdzić z mentorami, że dodatkowe pliki w `output/` nie przeszkadzają; bezpieczniej trzymać je poza `output/`.
- GUI: PDF obok JSON-a, kliknięcie pola podświetla miejsce w dokumencie.

## 9. Budżet czasu (R9)

150 PDF, z czego ok. 50 odwołań (ok. 5 stron), ok. 50 pełnomocnictw i ok. 50 przelewów, plus negatywy.

- Tekst cyfrowy: < 0,2 s/plik.
- OCR, gdy wszystko byłoby skanem: ok. 250 stron × 1–3 s ≈ 4–12 min na CPU. Zrównoleglenie na rdzenie (`ProcessPoolExecutor`) i OCR tylko pierwszych 3–4 stron odwołania, bo dane są „z reguły na pierwszych trzech stronach”. Wyjątek: szukanie czynności, jeśli nie ma jej na początku.
- LLM: ok. 100–150 wywołań × 2–6 s, przy 6 równoległych ≈ 1–3 min.
- Cel: < 20 min na całość. Twardy timeout per plik; po przekroczeniu zapis wyniku częściowego (deterministyczne pola) zamiast braku pliku.

## 10. Ryzyka

| Ryzyko | Wpływ | Mitygacja |
|---|---|---|
| Domain shift syntetyczne → realne | wysoki | hold-out skanów, augmentacje, ręczne etykiety realnych dokumentów, reguły ogólne, LLM do semantyki |
| Jakość OCR (pieczątki, plamy, kursywa) | wysoki | pre-processing, benchmark silników, fuzzy grounding, walidatory checksum do korekty OCR (np. `O`↔`0`) |
| Niejasna metryka F1 / format pustego JSON-a | średni | scorer wielowariantowy, pytanie do mentorów |
| Zmiana zasad punktacji LLM | średni | model w konfiguracji, raport F1 vs rozmiar dla 3 modeli, decyzja na końcu |
| Limit lub awaria API podczas oceny | wysoki | retry/backoff, fallback do wyniku deterministycznego, test z wyłączonym API (F1 „bez LLM” też raportujemy) |
| Jeden PDF zawiera kilka dokumentów (odwołanie + pełnomocnictwo + przelew) | średni | PDF opisany jako „składający się na odwołanie” jest niejednoznaczny: wykrywać granice dokumentów per strona. Zapytać, czy wynik ma być jeden JSON na plik. |

## 11. Pytania do mentorów (zadać na starcie)

1. Jak liczony jest F1: per pole czy per dokument, exact czy po normalizacji, jak traktowane są `null`-e i listy?
2. „Pusty plik `.json`” dla negatywu: `{}` czy plik 0-bajtowy?
3. Czy jeden PDF w teście może zawierać kilka dokumentów? Jaki wynik wtedy?
4. Rozmiar modelu przy MoE: liczą parametry całkowite czy aktywne? Jak liczony jest OCR przez API?
5. Czy dodatkowe pliki (np. `*.evidence.json`) mogą leżeć w `output/`?
6. `czynnosc_powod_id`: „pierwszy zarzut” czy „pierwsza zaskarżona czynność ze wstępu”? Syntetyki wskazują na to drugie.
7. Pole `pesel`: string czy liczba? W etykietach jest string, w specyfikacji liczba.
8. Numer rachunku z prefiksem `PL` z wydruku: zostawić czy usunąć? Etykiety generatora go usuwają (48 przypadków), dwa skany go zachowują.

## 12. Audyt datasetu: gdzie naprawdę jest „szum” (wykonany 2026-11-21)

Skrypty audytu leżą w scratchpadzie sesji. Przy starcie projektu przeniesiemy je do `tools/audit/` jako testy danych.

**12.1 Etykiety dokumentów cyfrowych (60 spraw, 180 JSON-ów)**

- Schemat i formaty: wszystkie klucze zgodne ze specyfikacją, daty w poprawnym ISO, `czynnosc_opis` zgodny 1:1 z `czynnosc_powod_id` we wszystkich 60 odwołaniach, `pesel` zawsze string, `kwota` zawsze liczba.
- Obecność w tekście: każda wartość tekstowa występuje w PDF-ie. 10 flag okazało się fałszywymi alarmami:
  - kwoty z kropką (`17.00 PLN`) zamiast przecinka;
  - 3 odwołania, w których numer BZP w tekście dotyczy **innego, wcześniejszego postępowania** (np. „ogłoszenie nr 2025/BZP 00998063, które zostało unieważnione…”). Etykieta `null` jest tam poprawna.
- Semantyka:
  - `czynnosc_dzien` i powód zgadzają się z pierwszą zaskarżoną czynnością;
  - w `przyklad_49` i `58` zdanie mówi o „wyborze oferty”, ale pierwszy zarzut dotyczy „zaniechania unieważnienia” (id 10) i etykieta jest poprawna. To dowód, że liczy się **pierwszy zarzut / pierwsza czynność z listy**, a nie pierwsze zdanie z datą;
  - `data_operacji` przelewu to zawsze **najwcześniejsza** data na potwierdzeniu (data wykonania), a nie „data wygenerowania”;
  - pełnomocnik to pierwsza osoba z listy;
  - mocodawca zgadza się z Odwołującym (NIP i nazwa), a pełnomocnik z odwołania jest na liście pełnomocników.
- **Wniosek: w etykietach cyfrowych nie znalazłem błędów.** Wyniku nie blokują złe etykiety, tylko **dystraktory w dokumentach**.

**12.2 Skany (30 dokumentów, OCR: RapidOCR PP-OCRv5 latin, CPU)**

- Po dopasowaniu rozmytym 27 z 30 etykiet ma pełne pokrycie w OCR. Słabe dopasowania wynikają wyłącznie z błędów OCR: `Poznań`→`Poznan`, `ł`→`t` (`Odwotujacy`), `ą`→`a`, `kpmunalnych`.
- Sprawdzone wizualnie (`odwolania/0005`): etykieta poprawna.
- Powód i data czynności w 10 skanach odwołań zgadzają się z pierwszym punktem listy „wnoszę odwołanie wobec: 1. …”.
- **Wniosek: etykiety skanów są poprawne. Ryzyko leży w OCR:** polskie znaki, kolejność linii na przekrzywionym skanie, prześwit tekstu z drugiej strony kartki, pieczątka „WPŁYNĘŁO dnia 19.02.2026”.

**12.3 Prawdziwy „szum”, który obniża wynik (reguły do wdrożenia i testy regresyjne)**

| # | Dystraktor | Przykład | Reguła |
|---|---|---|---|
| D1 | Numer BZP/TED **innego** postępowania w treści | `przyklad_41, 50, 53` | Brać numer z bloku „dotyczy” lub nagłówka, nie pierwszy z dokumentu. |
| D2 | Wiele dat w odwołaniu (otwarcie ofert, wezwania, publikacje, harmonogram) | `przyklad_40, 49` | Data czynności = data przy **pierwszej zaskarżonej czynności**. |
| D3 | Pieczątka wpływu z datą | skany | Pieczątka nie jest datą pisma. Data pisma pochodzi z linii „Miejscowość, dnia …”; brak takiej linii = `null`. |
| D4 | Blok „Wykonawca wybrany” z pełnymi danymi rejestrowymi | `przyklad_49` | Odwołujący to tylko podmiot przy etykiecie „Odwołujący”; przy konsorcjum lider. |
| D5 | Kilka osób w pełnomocnictwie, tytuły „adw.”, „r. pr.”, „radca prawny” | wszędzie | Pierwsza osoba, bez tytułu, w mianowniku. |
| D6 | Dwie lub trzy daty na potwierdzeniu przelewu | 58 z 60 | Data wykonania lub transakcji, nie wygenerowania lub wystawienia. |
| D7 | Kilka e-maili (Zamawiający, stopka kancelarii `biuro@…`, pełnomocnik) | skany | `mail` = e-mail w bloku Odwołującego lub pełnomocnika, nie ze stopki. |
| D8 | Prześwit tekstu z odwrotu kartki, plamy, rotacja | skany | Deskew, filtr pewności OCR, grupowanie linii po środku ramki. |
| D9 | Nagłówek pisma po bloku „dotyczy” zamiast na górze | `przyklad_49` | Szukać linii „X, dnia …” na całej pierwszej stronie, nie tylko w pierwszych liniach. |
| D10 | Dokumenty negatywne podobne do klas (orzeczenie KIO, ogłoszenia BZP/TED) | `negatywne/` | Klasyfikator z klasą „inne” i sygnałami negatywnymi („WYROK”, „Sygn. akt”, „Ogłoszenie o zamówieniu”). |

**12.4 OCR: wnioski pomiarowe**

- RapidOCR mobile (PP-OCRv5 latin) na tym CPU (8 wątków): 5–12 s na stronę, czyli ok. 2,5 min na 10 odwołań po 4 strony. Model detekcji „server” jest ok. 10× wolniejszy, a w testowanym przypadku nic nie zyskał.
- Tekst jest rozpoznawany, ale **polskie znaki są gubione**. Potrzebne:
  - porównanie z Tesseract `pol` (ma słownik języka polskiego; brak w systemie, wymaga `apt install tesseract-ocr-pol`);
  - korekta po OCR: słownik miejscowości TERYT dla `miejscowosc`; dla nazw i tytułów LLM z instrukcją „popraw tylko znaki diakrytyczne”, sprawdzaną odległością edycji ≤ 10%.
- Modele RapidOCR pobierają się z modelscope.cn. Pliki `.onnx` trzeba **dołączyć lub pobrać podczas instalacji** z sumą SHA256, żeby uruchomienie u organizatora nie zależało od sieci.
- Alternatywa: Ministral 3 8B/14B ma wejście obrazowe. Gdy OCR ma niską pewność, ten sam model może czytać obraz strony, więc **nie dochodzi żaden dodatkowy model do punktacji** (do potwierdzenia: liczenie OCR przez API).

**12.5 Szum w etykietach: niejednoznaczna konwencja rachunku (wykryte przy walidacji schematem, M1)**

- 48 z 60 cyfrowych potwierdzeń drukuje rachunek jako `PL65 1632 …`, a etykiety mają **same 26 cyfr**. W skanach `potwierdzenia/0001` i `0006` wydruk też ma `PL`, ale etykieta **zachowała prefiks** (`PL62217000089152501756617062`). Pozostałe 8 skanów nie drukuje `PL`.
- Skany wyglądają na etykietowane ręcznie, czyli tak jak będzie etykietowany zbiór testowy. To ryzyko: w teście konwencją może być „przepisz tak, jak wydrukowano”.
- Decyzja tymczasowa: zachować prefiks kraju ze źródła i usunąć whitespace, zgodnie z Discordem UZP. Starsze goldy pozostawić jako historyczne dane z wykazanym błędem. **Wyjaśnienie otrzymane w rozmowie UZP o 21:17 i 23:00:** zachować prefiks ze źródła, usunąć whitespace. Starsze goldy bez prefiksu są błędne.
- Oba przypadki są w `KNOWN_LABEL_NOISE` w `tests/test_dataset.py`. Każde nowe odstępstwo po aktualizacji zbioru wyłoży test.
- Uwaga do metryki: w wariancie `ignore` poprawnie pusty negatyw nie daje TP. Klasyfikacja negatywów chroni więc tylko przed FP. Jeśli organizator liczy też trafność klasyfikacji, zobaczymy to w wariancie z macierzą klasyfikacji w raporcie.

## 13. Wybór otwartego modelu

Publiczne benchmarki nie porównują modeli 3–14B na ekstrakcji z polskich dokumentów. Decyzja zapadnie **na naszym benchmarku** (CV po sprawach + skany + dokumenty spoza repo), jako tabela F1 vs parametry vs czas.

| Kandydat | Za | Przeciw |
|---|---|---|
| **Ministral 3 8B** (Apache-2.0) | Natywne API UE (`api.eu.mistral.ai`), JSON schema, **wejście obrazowe** (zapas dla skanów), wg raportu Ministral 3 przewyższa większą Gemmę 12B | Polski słabszy niż w Bieliku |
| Ministral 3 3B / 14B | Ta sama rodzina i API, łatwe porównanie rozmiarów | 3B może nie udźwignąć powodu czynności i mianownika |
| **Bielik 11B v3** (Apache-2.0) | Najlepszy polski model w swojej klasie (65,9 na Open PL LLM Leaderboard), dobra fleksja | Niepewne komercyjne API w UE z polityką prywatności: zweryfikować dostawcę |
| Qwen3.5 9B / 27B | Bardzo dobre trzymanie schematu JSON (benchmark SOB), dostępny w OVHcloud i Scaleway | Polski słabszy; MoE (35B-A3B) ma niejasne liczenie parametrów |
| Gemma 4 E4B / 12B | Małe, dobre w schematach | Sprawdzić, czy licencja spełnia „open source” w rozumieniu organizatora |
| PLLuM | Polski, rządowy | Wykluczony z leaderboardu (trenowany na częściach zadań), niepewne API |

**Rekomendacja startowa:** Ministral 3 8B jako model główny, z Ministral 3 3B i Bielikiem 11B jako punktami porównania.

**Zasada ze świeżych badań:** w JSON schema **nie dawać `minItems`** dla list (pełnomocnicy, podpisujący). Otwarte modele dopisują wtedy wymyślone elementy, żeby dobić do minimum (arXiv 2610.30997).

## 14. Testy odporności poza repo (zanim uwierzymy w wynik)

1. **Zbiór kontrolny:** skany 0006–0010 (15 PDF) + 4 negatywy; to retrospektywny podział (§16), nie dowód niezależnej generalizacji. Nowy test spoza generatora musi być zamrożony przed wyborem rozwiązania.
2. **Augmentacja „skanowa”:** 180 cyfrowych PDF-ów renderujemy do obrazu i dodajemy szum, rotację ±2°, kompresję JPEG, pieczątkę z datą i prześwit tekstu odwrotu. Etykiety zostają bez zmian, więc mamy 180 skanów z etykietami.
3. **Augmentacja treści (generator szablonów):** zamiana kolejności bloków, synonimy etykiet („NIP” / „Numer identyfikacji podatkowej”), formy zależne nazwisk, inne formaty dat i kwot, konsorcja, dwóch zamawiających, brak nagłówka z datą.
4. **Dokumenty realne bez etykiet albo z ręcznymi etykietami:**
   - publiczne ogłoszenia BZP/TED: negatywy i realne formaty numerów;
   - orzeczenia KIO z wyszukiwarki UZP: negatywy i realny język petitum;
   - 5–10 realistycznych odwołań i pełnomocnictw napisanych ręcznie według wzorów kancelarii oraz potwierdzenia z prawdziwych banków (PKO, mBank, ING, Pekao) na zanonimizowanych danych.
5. **Test odporności na nazwy plików:** losowe nazwy w każdym przebiegu.
6. **Analiza błędów po każdej iteracji:** każdy błąd przypisujemy do jednej kategorii (OCR / klasyfikacja / ekstrakcja / normalizacja / etykieta). Poprawiamy klasę problemu i dodajemy test regresyjny.

## 16. Wyniki M2 + M4 (2026-11-21): reguły bez LLM

**Co działa:**
- odczyt warstwy tekstu, a dla skanów OCR na CPU;
- klasyfikator regułowy z YAML (część M3);
- ekstrakcja 10 rodzajami reguł z YAML.

Pomiar: wariant główny `ignore/strict`, losowe nazwy plików.

| Zbiór | Dokumenty | F1 | P | R |
|---|---|---|---|---|
| train, cyfrowe (60 spraw) | 180 | **0,988** | 0,991 | 0,984 |
| train, skany 0001–0005 (analiza błędów) | 15 | 0,862 | 0,919 | 0,812 |
| **kontrolny, skany 0006–0010 + 4 negatywy (podział retrospektywny)** | 19 | **0,779** | 0,872 | 0,703 |

- Holdout per klasa: odwołanie 0,870, przelew 0,760, pełnomocnictwo 0,580.
- Klasyfikacja: 226/226 poprawnie na train i zbiorze kontrolnym (207 + 19), w tym 16 negatywów, wśród nich postanowienie KIO i ogłoszenia TED/BZP.

**Wnioski:**
1. **Przesunięcie domeny jest realne.** Cyfrowe syntetyki dają 0,99, skany kontrolne 0,78. Zbiór testowy organizatora to realne sprawy, więc bliżej mu do skanów. Dalsze strojenie reguł na syntetykach nie ma sensu.
2. **Największe straty na skanach:**
   - pełnomocnictwa: osoby bez tytułu zawodowego, podpisy elektroniczne, różne układy;
   - błędy OCR w polach tekstowych (tytuły, nazwy, tytuł przelewu z utraconymi ogonkami);
   - etykiety złamane na dwie linie w dwukolumnowych potwierdzeniach przelewu („Numer / transakcji”);
   - kilku zamawiających naraz.

   To dokładnie zakres M5: mały LLM dla pól semantycznych i poprawy znaków po OCR, sprawdzany z tekstem źródłowym.
3. **OCR:** zmiana modelu z PP-OCRv5 latin na **PP-OCRv6 small** podniosła F1 skanów z 0,693 do 0,799 przy tym samym czasie, potem reguły odporne na OCR dały 0,862. Czas: 5,4 s na stronę (69 stron w 372 s, CPU 8 wątków). Najgorszy przypadek, 150 samych skanów (50 odwołań × 4 strony + 100 × 1), to ok. 27 min, poniżej limitu 45 min. Przy LLM potrzebne będzie zrównoleglenie (M7).
4. **Bez OCR** całość jest szybka: 207 dokumentów w 2,6 s.
5. **Holdout skanów jest mały** (15 dokumentów), więc różnica 0,86 vs 0,78 to po części szum. Potrzebny jest większy zbiór kontrolny (§14: cyfrowe PDF-y przerobione na „skany”, własne realistyczne dokumenty).

**Zmiana protokołu (wyniku nie wolno opisywać jako dziewiczego holdoutu):** po pierwszym pomiarze na wszystkich 30 skanach (F1 0,686, sam wynik zbiorczy) skany podzielono. 0001–0005 trafiły do części roboczej (analiza błędów OCR), 0006–0010 zostały holdoutem. Wynik 0,779 to pierwszy i jedyny pomiar tej połowy po zmianach.

## 15. Źródła

- Zadanie (PDF) i repo: https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026
- Mistral, modele (Ministral 3 3B/8B/14B, Apache-2.0): https://docs.mistral.ai/getting-started/models/models_overview/
- Mistral, inferencja regionalna UE (`api.eu.mistral.ai`): https://docs.mistral.ai/inference/regional-inference
- OVHcloud AI Endpoints, katalog: https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/
- Scaleway Generative APIs, modele: https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models
- LangExtract (grounding ekstrakcji do spanów): https://github.com/google/langextract
- Benchmark KV-extraction z LLM 2–8B na zaszumionym OCR (rankingi modeli odwracają się między czystym tekstem a OCR; więcej few-shotów nie pomaga stabilnie): https://arxiv.org/abs/2609.17538
- PP-OCRv6 (małe modele OCR, szybsze na CPU): https://arxiv.org/abs/2606.13108
- Porównanie silników OCR (Tesseract szybki i dobry przy wielu kolumnach; PaddleOCR/RapidOCR lepsze na zaszumionych skanach): https://withinnoise.dev/blog/posts/ocr-benchmark/ , https://invoicedataextraction.com/blog/python-ocr-library-comparison-invoices
- Bielik / PLLuM, status API (źródła sprzeczne, zweryfikować u wydawców): https://www.forum-ai.pl/polski-model-jezykowy-bielik-efektywnosc-ai/ , https://pasqualepillitteri.it/en/news/32726/european-open-llms-compared-languages-licenses
- Bielik 11B v3 (raport techniczny, 65,93 Open PL LLM Leaderboard): https://arxiv.org/pdf/2601.11579 ; Bielik v2, wykluczenie PLLuM z leaderboardu: https://arxiv.org/pdf/2505.02410
- Ministral 3 (raport): https://arxiv.org/pdf/2601.08584
- Structured Output Benchmark (Gemma-4-31B, Qwen3.5-35B, Ministral-3-14B): https://arxiv.org/html/2604.25359v1
- „Padding to k”: `minItems` w schemacie powoduje wymyślanie elementów list: https://arxiv.org/pdf/2610.30997

Weryfikacja pól i wybór dokumentów przy ograniczonym budżecie API są obecnie zaimplementowane.
Wyniki na wspólnych próbkach, pomiar CPU i ograniczenia opisuje
[raport eksperymentu](docs/reviews/2026-11-21-evidence-budget.md).
