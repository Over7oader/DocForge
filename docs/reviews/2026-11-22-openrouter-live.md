# Pomiar OpenRouter i ekstrakcji po konfiguracji klucza

Model w pomiarze to mistralai/ministral-8b-2512. Żądania używają wyłącznie endpointu
mistral/eu, z allow_fallbacks=false, require_parameters=true i data_collection=deny.
Serwer przyjął zapytanie testowe i zwrócił JSON zgodny ze schematem.
OpenRouter raportował dostawcę Mistral. Lokalizacja całej ścieżki przez bramę globalną
nadal wymaga potwierdzenia przed oceną UZP.

## Pomiar 207 dokumentów

Przebieg openrouter-train zakończył się kompletem 207 JSON-ów w 238,56 s.
Cache OCR był ciepły, 12 odpowiedzi modelu pochodziło z wcześniejszej próby.
Dla 183 nowych odpowiedzi API raportowało 408444 tokeny wejścia, 53383 tokeny wyjścia
i koszt 0,056490159. Było 0 odpowiedzi HTTP 429, 0 awaryjnych przejść na reguły,
0 awarii dokumentów i 0 ostrzeżeń walidacji schematu.
Osobny pilot 13 dokumentów kosztował 0,003528822 dla 12 odpowiedzi API.

Na pełnych danych wynik proxy Discorda wyniósł 0,9663.
Po poprawce odczytu kolumny numeru transakcji i ponownym przebiegu z cache wynik
openrouter-final wynosi 0,9665, a exact F1 0,9412. Dodatkowe zapytanie kosztowało 0,000156981.
W 95 polach rachunek różni się od starego goldu wyłącznie prefiksem PL.
Audyt osobno sprawdza obecność rachunku z prefiksem w tekście źródłowym.
Goldy zachowano bez zmian. F1 z pominięciem obu pól rachunków wynosi 0,9884.
Ta diagnostyka obejmuje mniej pól i nie jest wynikiem całościowym.

## Pozostałe próby

Na 19 dokumentach retrospektywnego zbioru kontrolnego hybryda osiągnęła 0,9571
w proxy Discorda i 0,9167 exact. Same reguły na tych samych plikach dają 0,8284
w proxy. Było 15 wywołań API o łącznym koszcie 0,00615483.
Ocena została wykonana po zmianie algorytmu odczytu tabel, bez dalszego strojenia na tych wynikach.

Cztery własne PDF-y, w tym jeden skan i jeden negatyw, dały 1,0000 w obu metrykach.
Trzy wywołania kosztowały 0,0002424015. Te PDF-y były wcześniej używane przy rozwoju
i regresji. Wynik nie stanowi niezależnego dowodu generalizacji.

## Poprawka odczytu tabel

Przy układzie „Kwota / Data / Numer referencyjny” pierwszy znaleziony token z cyframi
może należeć do kwoty. Wykryto konkretny błąd zwracania 500 z wartości 7 500,00.
LabeledRule odczytuje teraz komórkę pod właściwą kolumną, jeśli nagłówek i wiersz mają
zgodną liczbę komórek rozdzielonych większym odstępem lub tabulatorem.
Wartość w tej samej linii ma pierwszeństwo przed interpretacją tabeli.
Regresje obejmują zmianę kolejności kolumn, tabulatory, brak numeru i układ etykieta–wartość.

Na pełnych 207 dokumentach same reguły po tej zmianie zyskały 1 TP i straciły 1 FP.
Zmiana nie wymaga nazw plików, konkretnych nazw podmiotów ani wartości z datasetu.
Może zawieść przy nierównej liczbie komórek, zawijaniu kolumn lub zagubionych odstępach OCR.

## Reprodukcja i raporty

Runy i raporty znajdują się w runs/openrouter-pilot, runs/openrouter-train,
runs/openrouter-final, runs/openrouter-control oraz runs/openrouter-challenge.
Każdy audit.json zawiera surowe wyniki wobec etykiet, diagnostykę rachunków,
zużycie API i liczniki błędów. Snapshoty zapisują konfigurację bez klucza API
oraz hashe kodu i plików YAML. Koszt w usage jest wartością naliczoną przez API,
bez oszacowania opłat zakupu kredytów lub przewalutowania.

Do ponownego audytu służy polecenie python -m scripts.audit_run runs/NAZWA.
DOXFORGE_CACHE_DIR pozwala ustawić osobny katalog cache OCR i LLM.
Brak tej zmiennej zachowuje domyślny .cache.

Wszystkie wyniki proxy zakładają rapidfuzz.fuzz.ratio >=80 na wybranych polach,
zgodnie z lokalną interpretacją rozmowy UZP. Oficjalny skrypt nadal nie jest dostępny.

W procesie potomnym trybu konkursowego wykryto brak konfiguracji logowania INFO.
Dodano przekazanie poziomu logowania z procesu głównego. Czas i kompletność trwającego
pomiaru cold150 pozostają mierzone przez proces nadrzędny, natomiast jego koszt nie
zostanie odtworzony z logu rozpoczętego przed poprawką. Audyt oznacza brak kosztu jako null.
Liczba poprawnych zapisów w nowym cache pozwala zweryfikować liczbę zakończonych odpowiedzi.


## Zakończony test konkursowy 150 PDF

Przebieg openrouter-cold150 uruchomiono przez start.sh z osobnym pustym katalogiem
cache OCR i LLM. Wagi OCR były wcześniej zainstalowane. Komplet 150/150 plików
powstał w 360,12 s. Dane obejmują 123 dokumenty cyfrowe, 15 skanów i 12 negatywów.
Lokalny etap zakończył się po ok. 190 s, następnie ukończono 138 odpowiedzi LLM.
W cache zapisano 138 pełnych odpowiedzi. Log nie zawiera ostrzeżeń ponowień,
fallbacków, błędów dokumentów ani walidacji schematu.

Proxy Discorda na starych etykietach wynosi 0,9669, bez obu pól rachunków 0,9888.
W 67 polach rozbieżność dotyczy wyłącznie prefiksu PL. Koszt tego przebiegu
jest nieznany w audycie, ponieważ uruchomiono go przed poprawką logowania potomka.
Oznaczono go null. Czas i kompletność zmierzono w procesie nadrzędnym.
Nie wykonywano próby 150 samych skanów. Obsługę logowania potomka sprawdzono
osobno przez start.sh po poprawce.
