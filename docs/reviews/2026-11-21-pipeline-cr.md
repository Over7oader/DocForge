# Przegląd DoxForge: jakość, generalizacja i limit czasu

Stan 2026-11-21. Cel: maksymalny F1 na nieznanych dokumentach, przy zachowaniu reguł
UZP. Nie da się zagwarantować F1=1 ani braku overfittingu na podstawie tego zbioru.
Nie wykonano fine-tuningu modelu. Aktualnie „uczenie” oznacza dobór reguł, promptów i polityk.

## Fakty z zapisanych pomiarów

| Pomiar | Zakres | ignore/strict F1 |
|---|---|---:|
| `runs/m4-train/report.json`, same reguły | 207 dokumentów roboczych | 0,9793 |
| ten sam raport, cyfrowe | 180 PDF, wszystkie 60 spraw | 0,9877 |
| ten sam raport, skany robocze | 15 PDF | 0,8621 |
| `runs/m4-holdout/report.json`, reguły | 15 skanów + 4 negatywy | 0,7786 |

0,9793 nie jest wynikiem Mistrala ani wynikiem na ukrytym teście. Puste negatywy nie dają
TP w naszym extraction F1, dlatego macierz klasyfikacji i kompletność plików trzeba raportować osobno.
Zapisane macierze pokazują poprawną klasyfikację 226/226 dokumentów (207+19), nie 211/211.
Dokładna metryka organizatora nadal nie jest opublikowana.

Przebieg `m5-train` w chwili przeglądu był niekompletny. W logu 23:11:38 klient wykrył
2 requests/min i odstęp 31,5 s. Później były jeszcze 429; nie należy deklarować, że
automatyczne dopasowanie wyeliminowało wszystkie błędy. Nie przerywałem istniejącego przebiegu.
Uruchomiony proces ma załadowany starszy kod; poprawki obejmą następne uruchomienie.

## Ustalenia CR

### P1: brak niezależnego dowodu generalizacji — otwarte

Wszystkie sprawy cyfrowe są w train. Wiele iteracji reguł i promptów widziało ten sam generator.
Losowanie nazw plików eliminuje tylko korzystanie z nazwy. Nie eliminuje zależności od szablonu.
Skany 0006–0010 były częścią wcześniejszego pomiaru wszystkich skanów, a później odłożono je
po obejrzeniu wyniku zbiorczego. To retrospektywny zbiór kontrolny, nie całkowicie dziewiczy holdout.

Wymagane: nowy, ręcznie oznaczony i zamrożony zestaw dokumentów spoza generatora, z innymi
szablonami i negatywami. Augmentacje dokumentów jednej sprawy dzielą z nią fold; także renderowane
skany i oryginały. Podział po sprawach nie usuwa wspólnego generatora. Nie stroić po kolejnych
wynikach holdoutu ani nie wybierać dziesiątek polityk per pole na tej samej próbce.

### P1: kolejność list mieszała podmioty — poprawione

`_merge_list` parowało elementy po indeksie, jeśli długości były równe. Odwrócona kolejność
zamawiających mogła połączyć nazwę firmy B z NIP-em firmy A preferowanym z reguł.
Teraz listy wieloelementowe używają jednoznacznych kotwic tożsamości (nazwy/identyfikatory).
Nierozpoznany element nie dziedziczy identyfikatora z pozycji na liście. Wspólny pełnomocnik
nie jest kotwicą tożsamości reprezentowanej firmy. Para jednoelementowa zachowuje dotychczasową politykę.
Dodano test odwrócenia kolejności i test nowego podmiotu bez identyfikatora.

### P1: fuzzy grounding akceptował złą cyfrę — poprawione

Próg 90% dopuszczał NIP różniący się jedną cyfrą i rachunek różniący się jedną cyfrą.
Dla NIP, REGON, KRS, PESEL, IBAN, e-maila i BZP wyłączono fuzzy fallback.
Normalizacja spacji, separatorów i polskich znaków pozostaje. Testy nie używają nazw i numerów
organizatora. Dopasowanie do tekstu nadal nie dowodzi poprawnej roli podmiotu; reguły i model
muszą rozróżnić np. rachunek nadawcy i odbiorcy. Grounding nazw osób nadal jest heurystyką,
a daty/kwoty i pola derived nie mają pełnego dowodu źródłowego.

### P1: limit czasu niepotwierdzony — częściowo poprawione

150 zapytań przy 2/min wymaga ok. 75 min; z marginesem 31,5 s jest to ponad 78 min.
Nawet pominięcie 50 przelewów zostawia ok. 50 min dla 100 zapytań. Cache treningowy nie rozwiązuje
tego problemu na ukrytych PDF-ach. Cztery wątki nie zwiększą limitu organizacji.

W kliencie poprawiono rezerwację wysyłki przy zmianie limitu oraz wspólny cooldown `Retry-After`.
Wcześniej uśpione wątki miały zarezerwowane terminy według starego tempa, a `Retry-After` >30 s
obcinano do 30 s. Teraz respektowany jest pełny liczbowy nagłówek. Nadal brakuje sterowania TPM,
obsługi daty HTTP w `Retry-After`, koordynacji wielu procesów oraz deadline'u całej partii.
Nie jest to gwarancja ukończenia w 45 min. Długie retry mogą trwać wiele minut.

Warunek zgłoszenia: 150 PDF, puste cache LLM/OCR, docelowy klucz, miks i wariant samych skanów,
czas ścienny <2700 s, kompletność 150/150, liczba fallbacków, F1, rozkład opóźnień i retry.
Modele OCR pobrane przed uruchomieniem; dokumentować sprzęt. Własny cel operacyjny <35 min.
Teoretyczne minimum to >3,33 zapytania/min dla 150 wywołań bez czasu na resztę; cel z zapasem
to co najmniej 10/min, ale należy sprawdzić także TPM i rzeczywiste czasy, nie tylko RPM.

### P2: cache i narzędzia porównujące — częściowo poprawione

Niepoprawny JSON z modelu był zapisywany przed parsowaniem; zatruwał kolejne uruchomienia.
Teraz parsowanie poprzedza zapis. Istniejące uszkodzone wpisy wymagają osobnej kontroli.
Klucz cache zawiera body i model, ale nie endpoint ani faktyczną wersję pod aliasem `latest`.
Do finalnego benchmarku przypiąć wersję modelu i odseparować cache po dostawcy/wersji.
Nie zmieniono klucza cache podczas trwającego przebiegu, aby nie unieważnić jego odpowiedzi.

Scratchpad Claude'a `policy_compare.py` używa prawdziwego `LLMClient`: brak wpisu w cache
wywołuje API. Uruchomienie go obok bieżącego przebiegu konkuruje o ten sam limit.
Repozytoryjny `scripts/replay_cache.py` ma klienta, którego `_post` zawsze rzuca CacheMissError.
Raportuje braki i używa tylko train; nie dodaje po cichu wyników reguł dla brakującego LLM.

### P2: pozostałe luki specyfikacji — otwarte

- OCR czyta maksymalnie 4 strony bez tekstu; klasyfikator tylko pierwsze 3 strony, prompt tylko
  prefiks znakowy. Dane i podpisy na dalszych stronach mogą zniknąć. Potrzebny pomiar strategii
  wyboru stron przy stałym budżecie, zamiast założenia, że zawsze wystarczą pierwsze strony.
- Schemat jest sprawdzany po scaleniu, ale błędny wynik jest tylko logowany i zapisywany.
- Obecne parowanie list scorera jest dokładne dla małych list, a potem zachłanne; to nie
  zapowiedziany algorytm węgierski. Nie zakładać dokładności dla dużych konsorcjów.
- Brakuje porównania 3B/8B/14B na zamrożonym protokole, dowodów per pole, pełnego benchmarku
  wydajności i pakowania modeli OCR. To zadania do wykonania, nie gotowe funkcje.
- Szum etykiet IBAN (`PL` vs same cyfry), niepoprawne checksumy syntetyków i konflikt pierwszego
  zarzutu z opisem czynności są już opisane. Nie dodawać wyjątków po ID/nazwie dokumentu ani
  nie poprawiać oficjalnych goldów dla zwiększenia wyniku. Ustalić konwencję z mentorami.

## Eksperymenty w przeglądzie

Zamrożony punkt porównawczy: **76 odpowiedzi** (`runs/review-before/replay.json`),
72 cyfrowe + 4 skany, 46 grup spraw. To próbka wyznaczona dostępnością cache, nie losowy test.
Porównano cztery z góry określone polityki; bez wywołań API i bez zmiany promptów.

| Polityka | F1 na tej samej próbce |
|---|---:|
| same reguły | 0,9862 |
| obecna konfiguracja hybrydy | 0,9921 |
| wszystkie pola: reguły najpierw | 0,9869 |
| wszystkie pola: LLM najpierw | 0,9921 |

Na cyfrowych same reguły miały 0,9939, hybryda 0,9918. Na czterech skanach hybryda
poprawiła 0,7778 do 1,0000; **cztery skany nie uzasadniają deklaracji idealnej jakości**.
Dla przelewów w tej próbce reguły dały 1,0000, hybryda 0,9808. Bez niezależnej walidacji
nie zmieniono konfiguracji pól dla podbicia wyniku.

Replay po poprawkach na wspólnych 76 dokumentach zachował wszystkie TP/FP/FN każdej polityki.
Poprawki usuwają wykazane testami błędy; nie deklarujemy poprawy F1 na tej próbce.
Powtórzony pełny przebieg samych reguł (`runs/review-rules`) zachował F1 0,9793 na 207/207,
bez brakujących wyników. To pomiar z ciepłym cache OCR, nie benchmark limitu 45 min.
Końcowa weryfikacja: 108 testów przeszło; ruff, kontrola formatowania i mypy bez błędów.

`scripts/policy_cv.py` wybiera globalną politykę na czterech foldach i ocenia na piątym,
grupując dokumenty sprawy oraz zachowawczo skany o tym samym numerze. W diagnostyce
wybór padł we wszystkich foldach na konfigurację hybrydową, F1 OOF 0,9921.
95% przedział case-bootstrap różnicy hybryda minus reguły: **[-0,0050; +0,0199]**.
Obejmuje brak poprawy. Reguły/prompty już widziały te dane, więc OOF nie jest niezależną
walidacją całego systemu. Kolejne snapshoty cache mają inną próbkę i nie są porównaniem before/after.

```bash
.venv/bin/python -m scripts.replay_cache --out runs/replay-NOWA-NAZWA
.venv/bin/python -m scripts.policy_cv runs/replay-NOWA-NAZWA/replay.json
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy doxforge
```

## Płatny Mistral i zgodność

W opublikowanych zasadach UZP nie ma wymogu bezpłatnego API ani zakazu płatnego planu.
To pozwala użyć płatnego API z tym samym otwartym modelem; parametry modelu pozostają te same.
Wymagane są Python 3.13+, open source, lokalne uruchomienie bez GPU, API z odpowiednią
polityką prywatności i serwerami UE/EOG, klucz dla organizatora oraz limit 45 min.
Nie kupować abonamentu czatu w założeniu, że zwiększy limity API.

Aktualna dokumentacja Mistrala opisuje aktywację billing/pay-as-you-go, kredyty i limity organizacji.
Starsze materiały używają nazwy Scale. Faktyczny limit w panelu API jest rozstrzygający;
opłacenie dostępu samo nie dowodzi podniesienia RPM/TPM. W razie potrzeby wnioskować do supportu.
Warto przypiąć `ministral-8b-2512` (Apache-2.0), po sprawdzeniu dostępności w regionie.
Alias `latest` nie zapewnia powtarzalności.

Uwaga do lokalizacji: dokumentacja regionalna nazywa endpoint „EU”, ale tabela wspomina EU/EFTA.
EFTA obejmuje też Szwajcarię spoza EOG. Dla ścisłego wymogu UZP trzeba uzyskać potwierdzenie
zakresu UE/EOG od dostawcy/mentora; sama nazwa endpointu nie zastępuje polityki przetwarzania.
Nie kierować dokumentów na globalny endpoint w celu obejścia limitów.

Źródła zweryfikowane podczas przeglądu:

- [Wymagania UZP, §5–7 i limit czasu](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)
- [Mistral: usage, limity, billing i kontakt w sprawie wyższych limitów](https://docs.mistral.ai/admin/billing-usage/usage-limits)
- [Mistral: regional inference](https://docs.mistral.ai/inference/regional-inference)
- [Ministral 3 8B: wersja i licencja](https://docs.mistral.ai/models/ministral-8b-latest)

## Kolejność dalszej pracy

1. Dokończyć obecny przebieg; odnotować cache, fallbacki i retry. Nie uruchamiać drugiego klienta API
   na tym samym limicie. Całkowity wynik M5 dopiero po 207/207.
2. Zweryfikować billing/limity klucza i zgodność regionu; przygotować finalny klucz z budżetem.
3. Zamrozić zewnętrzny test z innymi układami, osobami i negatywami. Dobór polityk wyłącznie
   na roboczych foldach po sprawach, mała liczba wariantów, pełny dziennik eksperymentów.
4. Porównać 3B/8B/14B i strategię stron na wspólnych danych. Wybrać mniejszy model tylko przy
   potwierdzonej jakości; nie zakładać, że większy lub drugi prompt automatycznie pomoże.
5. Jeden pomiar zamrożonego systemu na zewnętrznym teście i pełny cold-cache benchmark 150 PDF.
