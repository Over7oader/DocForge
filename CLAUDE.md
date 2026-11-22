# Handoff przeglądu Codex

Użytkownik zlecił przegląd jakości, generalizacji i zgodności z limitem 45 minut.
Przed kolejnymi zmianami przeczytaj `docs/reviews/2026-11-21-pipeline-cr.md`.

- Nie przerywano Twojego procesu `m5-train`; ma w pamięci kod sprzed poprawek.
- 0,9793 pochodzi z M4 (reguły/train), nie z Mistrala ani z niezależnego testu.
- Naprawiono fuzzy grounding identyfikatorów, scalanie list podmiotów, adaptację odstępu,
  liczbowy Retry-After i zapis niepoprawnego JSON do cache; nowe testy są w `tests/test_llm.py`.
- Użyj `python -m scripts.replay_cache --out runs/NOWA-NAZWA` do porównań bez API.
  Twój scratchpad `policy_compare.py` przy cache miss wywołuje API i konkuruje z przebiegiem.
- `python -m scripts.policy_cv runs/NOWA-NAZWA/replay.json` daje diagnostykę grupową,
  lecz nie usuwa wcześniejszego strojenia reguł/promptów na całym generatorze.
- Konfiguracji pól nie zmieniano dla podbicia train F1. Nie stroić na odłożonych skanach.
- Płatne API nie jest zabronione przez README UZP. Zweryfikuj faktyczne RPM/TPM i UE/EOG;
  przy 2/min nie ukończysz 150 wywołań w 45 min. Cache nie jest dowodem wydajności.

To wiadomość w plikach współdzielonego projektu, nie wysłany komunikat do aktywnej sesji Claude.

Nowszy pomiar i zmiany w trybie konkursowym opisuje
[raport o weryfikacji pól i budżecie API](docs/reviews/2026-11-21-evidence-budget.md).
Skrypty startowe stosują budżet 2640 s. Polecenie CLI bez tej opcji nadal zbiera wszystkie
odpowiedzi do eksperymentów. Wyniki 56 zapytań są symulacją na cache train.

Nowe doprecyzowania z wklejonej rozmowy Discord UZP opisuje
[raport z rozmowy](docs/reviews/2026-11-21-discord-clarifications.md).
Zmieniono IBAN na zachowanie kodu kraju ze źródła. Starsze goldy bez PL są błędne
według UZP. Nowy prompt i reguły zmieniają cache przelewów. Próg 80% dotyczy wybranych
długich tekstów, miary podobieństwa nie podano. Definicja FP/FN różni się od lokalnej.
Nie przedstawiać historycznych exact F1 jako oficjalnej metryki. Test opisano jako syntetyki
oparte na rzeczywistych sprawach, także skany. Limit pozostaje rozbieżny z README.

Ewaluator ma już opcję `score runs/NAZWA --metric discord-proxy`, z raportowanymi założeniami
ratio80 i FP-only dla błędnej obecnej wartości. Dokładne pola i zastrzeżenia są w raporcie
doprecyzowań. Snapshot 127 starych wyników M5 daje 0,9976 w tym proxy.
W nowym pełnym przebiegu reguł jest 95 rozbieżności wynikających wyłącznie z prefiksu PL
względem wadliwych historycznych goldów. Nie cofać poprawnej obsługi prefiksu dla wyniku train.

Przygotowano tryb DOXFORGE_LLM=openrouter. Lokalny .env jest przełączony na ten tryb,
klucz OPENROUTER_API_KEY czeka na uzupełnienie przez użytkownika. Dotychczasowe LLM_*
zachowano dla bezpośredniego Mistrala. Domyślny model to mistralai/ministral-8b-2512,
provider.only to mistral/eu, bez fallbacków, require_parameters=true, data_collection=deny.
Zwykła brama OpenRouter nie gwarantuje całej ścieżki EU/EOG, wymagania opisano w README.
Dostawcy i bramy regionalne mają osobne klucze cache. Nie łączyć wyników OpenRoutera ze
starymi odpowiedziami Mistrala jako jednego przebiegu jakościowego bez oznaczenia pochodzenia.

Klucz OpenRoutera działa, wykonano 207/207 dokumentów w 238,56 s przy ciepłym OCR
i 12 odpowiedziach z pilota. Koszt 183 nowych odpowiedzi wyniósł 0,056490159.
Wynik całego train proxy to 0,9665 po poprawce tabel, 95 rozbieżności samych prefiksów PL.
Bez obu pól rachunków proxy F1 wynosi 0,9884. Nie przedstawiać tej diagnostyki jako
wyniku całego systemu. Retrospektywny kontrolny zbiór 19 dokumentów dał proxy 0,9571.
Szczegóły w docs/reviews/2026-11-22-openrouter-live.md.

Dalsze zmiany: select_entity w regule jawnej etykiety lidera, x-null-when-singleton
w schemacie oraz pełna walidacja schematu liścia zamiast samego typu i enum.
Poprawiono wyszukiwanie rachunków z prefiksem kraju złamanych między liniami.
Osobny scripts/source_reference.py tworzy diagnostyczną kopię referencji
na podstawie formatu rachunku w PDF, bez odczytu predykcji i bez nadpisywania goldów.
Nie przedstawiać tej kopii jako oryginalnego zbioru oceniającego.
Koncepcja CPU jest w docs/reviews/2026-11-22-cpu-concept.md.

Na jawne zlecenie użytkownika wdrożono ensemble OCR: PP-OCRv6 Small, PP-OCRv5 Latin Mobile
i Tesseract LSTM tessdata_best pol+eng. Aktywny `.env`: DOXFORGE_OCR=ensemble3,
DOXFORGE_OCR_THREADS=1. Przed pomiarem: `python -m scripts.prepare_ocr`.
Pełny opis, źródła, pomiary i ograniczenia: docs/reviews/2026-11-22-ocr-ensemble.md.
Modeli nie douczano. Łączymy całe odczytane wiersze według geometrii i zgodności, bez
syntezy cyfr. Cache ensemble jest osobny; odczyty po awarii silnika nie zatruwają cache.
Na pierwszych stronach 15 znanych skanów train reguły: 127 TP/9 FP/27 FN -> 134 TP/9 FP/20 FN.
Nie porównywać tego F1 z wynikiem pełnej hybrydy; holdoutu nie czytano.
Skan 0005: odzyskano oba numery i OPZ; jedno kontrolne wywołanie LLM na pierwszej stronie
też zwróciło poprawne wartości. Pełnego F1 hybrydy nie przeliczono.
Budżet 2640 s obejmuje teraz także OCR, również bez LLM. Niedokończone dokumenty mają `{}`,
więc komplet plików nie dowodzi kompletności ekstrakcji. 150 skanów po 4 strony nadal może
wyczerpać czas; średnio 9,05 s na znaną pierwszą stronę nie daje dowodu dla 150 pełnych PDF.
Kontrole: 172 testy, ruff, mypy. Dotychczasowego szerszego celu nie wznawiano automatycznie.

Przegląd Claude po zmianach Codexa: [raport](docs/reviews/2026-11-22-claude-review.md).
Wybrano ministral-8b-2512 (reguła przed pomiarem, nowe skany z augment_scans, hold-out 0,949 jak 14B); ensemble nie pomogły.
Poprawiono ownership (podpisujący bez nagłówka, identyfikatory pod listą nazw), TSV Tesseracta,
grounding cyfr identyfikatorów i numerów, izolację błędów w budżecie, brak opencv w requirements.
Dodano OCRPace: na 2 vCPU ensemble 16,4 s/str., pojedynczy 6,0 s/str.; przy braku czasu strony idą
przez PP-OCRv6. Jednorazowy pomiar m6-holdout: exact F1 0,949 (M4: 0,779), proxy 0,981; nie stroić.
VM oceniająca: team20@20.215.210.103 (klucz SSH), projekt w /opt/solution, setup_vm.sh, RUN.md.
Runda odporności (2026-11-22, Claude): stress_reflow.py (łamanie linii, seedy 1-5) i augment_scans.py
--hard (trudne skany). Ogólne poprawki opisane w docs/reviews/2026-11-22-claude-review.md (x-evidence,
x-spelling, flags, similar, diakrytyki z innego silnika, naprawy OCR rachunków). Walidacja na świeżych
seedach 4/5: 0,9995/0,9998, aug-hard2 0,9949, hold-out exact 0,955 (proxy 0,981). Nie stroić pod
pojedyncze wartości; nowe poprawki sprawdzać na świeżym seedzie.
Decyzja użytkownika (2026-11-22 rano): zostaje ministral-8b-2512, bezpośrednio Mistral EU
(DOXFORGE_LLM=openai_compatible, api.eu.mistral.ai); OpenRouter wyłączony (na VM .env bez OPENROUTER_*).
Klucz Mistrala ma 750 req/min i 1 mln tokenów/min. Kod na VM weryfikowany sumami SHA-256 (93 pliki).
