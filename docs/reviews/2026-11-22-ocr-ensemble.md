# Ensemble trzech lekkich OCR na CPU

## Zmiana

Domyślny `DOXFORGE_OCR=ensemble3` łączy trzy modele:

| Model | Rozpoznawanie | Detekcja |
|---|---:|---:|
| PP-OCRv6 Small | 20,25 MiB | 9,47 MiB |
| PP-OCRv5 Latin Mobile | 7,54 MiB | 4,60 MiB |
| Tesseract LSTM, tessdata_best pol+eng | 26,11 MiB | w silniku |

PP-OCR używa wspólnego pliku klasyfikatora 0,56 MiB; suma aktywnych wag ok. 68,5 MiB.
To trzy modele i dwie rodziny architektur, nie trzy niezależne rodziny.
Nie używamy GPU, zewnętrznego OCR API ani fine-tuningu. Strony i wyniki OCR pozostają lokalne.
Jedno wywołanie istniejącego LLM wykonano osobno do kontroli ekstrakcji odzyskanej pierwszej strony.

Tesseract 5.5.4 działa przez `tesserocr==2.13.0`, którego koło zawiera biblioteki natywne.
Wagi pol+eng są przypięte do rewizji `e12c65a915945e4c28e237a9b52bc4a8f39a0cec`
w oficjalnym `tessdata_best`; oba pliki są sprawdzane SHA-256 i publikowane atomowo.
RapidOCR 3.10.0 pobiera i sprawdza swoje przypięte modele ONNX.

Przed pomiarem należy wykonać:

```bash
pip install -r requirements.txt
python -m scripts.prepare_ocr
```

W `.env` i `.env.example` ustawiono ensemble oraz `DOXFORGE_OCR_THREADS=1`.
Trzy silniki działają współbieżnie wewnątrz jednej strony; strony są szeregowane.
Ograniczenie dotyczy wątków ONNX, a Tesseract otrzymuje domyślnie `OMP_THREAD_LIMIT=1`.
Istniejące jawne ustawienie OpenMP użytkownika ma pierwszeństwo.
`DOXFORGE_OCR=single` przywraca stary PP-OCRv6 i jego cache.

## Łączenie wyników

- Każdy model wykonuje własną detekcję i rozpoznawanie tej samej strony w 200 DPI.
- Zachowujemy tekst, położenie i pewność. Korygujemy współrzędne o przekrzywienie strony.
- Tesseract zachowuje własne przypisanie słów do wierszy z TSV, aby wysokość liter i znaków
  diakrytycznych nie rozbijała jednego zdania na przypadkowe linie.
- Dopasowanie wierszy wymaga bliskości w pionie i nakładania w poziomie. Jeden silnik daje
  najwyżej jeden głos w grupie; powielone obserwacje nie zwiększają liczby głosów.
- Wybieramy cały zaobserwowany wiersz. Nie głosujemy osobno po cyfrach i nie tworzymy
  nowych identyfikatorów z fragmentów różnych odczytów.
- Teksty z różnymi cyframi nie głosują jako zgodne. Zbliżone odczyty mogą zgadzać się po
  normalizacji whitespace i wielkości liter, przy podobieństwie co najmniej 0,9.
- Pewność różnych modeli nie jest skalibrowana. Przy braku zgodności wiarygodny model główny
  ma margines pierwszeństwa; samodzielny odczyt pomocniczy musi mieć wyższą jakość.
- Przy zgodzie i zbliżonej pewności preferujemy PP-OCRv6, następnie Tesseract, następnie
  PP-OCRv5 Latin, ze względu na pokrycie polskich znaków. Nie używamy nazw lub wartości ze zbioru.

Cache ma osobny klucz dla ensemble, ustawień i wersji algorytmu. Awaria jednego silnika
jest logowana; dostępne odczyty są zachowane, ale nie są cachowane jako pełny ensemble.
Zapisy cache OCR i wyników są atomowe.

## Pomiar bez strojenia na holdoucie

`runs/ocr-ensemble-benchmark/report.json`: cztery niezależne obrazy syntetyczne i pierwsze
strony 15 znanych skanów train. Modele były już pobrane; wyniki OCR obliczano od nowa.
Użyto jednego wątku ONNX na silnik. Nie odczytywano holdoutu. Progi nie były wybierane
na podstawie maksymalizacji F1 tego pomiaru. To znane dane robocze, bez dowodu generalizacji.

15 rzeczywistych stron: 135,69 s łącznie, średnia 9,05 s, maksimum 12,47 s, 0 awarii silników.
To pomiar OCR pierwszych stron, bez PDF-renderingu, ekstrakcji LLM, czasu przygotowania modeli
i pozostałych stron. Nie jest pomiarem pełnego przebiegu 150 PDF.

Na czterech prostych obrazach syntetycznych średni CER po normalizacji wielkości liter,
Unicode NFC i whitespace wyniósł:

| Odczyt | CER |
|---|---:|
| PP-OCRv6 | 0,00405 |
| PP-OCRv5 Latin | 0,04207 |
| Tesseract | 0 |
| Ensemble | 0,00405 |

Ensemble nie poprawił PP-OCRv6 na tych prostych syntetykach; Tesseract sam odczytał je dokładniej.
Ta mała próbka nie uzasadnia zmiany całego systemu na Tesseract ani deklaracji idealnej jakości.

Osobna diagnostyka `rules-first-page.json` porównuje identyczne reguły na tych samych
15 pierwszych stronach: wcześniejszy cache PP-OCRv6 vs nowy ensemble, oryginalne goldy.
Oba warianty używają wyłącznie pierwszej strony, więc nie jest to porównanie pełnych PDF.

| Wariant | TP | FP | FN | historyczny exact F1 |
|---|---:|---:|---:|---:|
| Pojedynczy OCR + reguły | 127 | 9 | 27 | 0,8759 |
| Ensemble + te same reguły | 134 | 9 | 20 | 0,9024 |

W pomiarze odzyskano 7 TP bez zwiększenia FP. Nie jest to wynik hybrydy z LLM ani oficjalna
metryka organizatora. Nie wolno porównywać 0,9024 z wcześniejszym 0,9982 dla innego zakresu.

Problemowa pierwsza strona skanu odwołania 0005 odzyskała:

- `numer_postepowania = ZP/12/2026`;
- `numer_bzp = 2026/BZP00879135/03`;
- OPZ jako pierwszą zaskarżoną czynność, `czynnosc_powod_id = 11`;
- datę zaskarżonej czynności `2026-03-23`.

Tesseract odczytał linię z numerami, PP-OCRv5 i Tesseract odczytały OPZ. Reguły i pojedyncze
kontrolne wywołanie skonfigurowanego LLM na odzyskanej pierwszej stronie zwróciły te wartości.
Wynik kontroli: `runs/ocr-ensemble-benchmark/recovered-page-extraction.json`.
Nie jest to ponowne przeliczenie F1 całego systemu ani pełna ocena całego sześciostronicowego PDF.

Dodatkowy smoke test całej ścieżki PDF -> OCR -> reguły -> JSON, z pustym cache treści OCR
i wyłączonym LLM: `runs/ocr-ensemble-smoke`. Przetworzono cztery strony tego PDF, bez awarii
silników; wynik końcowy ma oba poprawne numery i OPZ id 11. Same wywołania OCR zajęły
łącznie ok. 64,7 s (od 7,0 do 20,6 s/stronę), co pokazuje zmienność wydajności względem
krótszego pomiaru pierwszych stron. Wagi były już zainstalowane. To jeden PDF, nie 150.

## Limit czasu

Znaleziono i naprawiono lukę: dotychczas etap lokalnego OCR był wykonywany poza procesem,
który można zatrzymać po deadline. Obecnie budżet 2640 s obejmuje lokalny OCR i LLM,
także przy `DOXFORGE_LLM=none`. Rodzic tworzy poprawne puste JSON-y dla wszystkich wejść;
proces roboczy atomowo zastępuje je ukończonymi wynikami i jest zatrzymywany po budżecie.
Niedokończone dokumenty pozostają `{}`. To zachowanie awaryjne, nie poprawna ekstrakcja.

Przy średniej 9,05 s/stronę 150 pojedynczych stron to orientacyjnie 22,6 min samego OCR,
a 600 stron to ponad 90 min. To ekstrapolacja, nie benchmark. Pełne czytanie czterech stron
każdego z 150 skanów nie jest potwierdzone w limicie 45 min. Deadline chroni czas, ale nie
gwarantuje kompletnej ekstrakcji. Dalsze prace wymagają strategii wyboru stron i pełnego
pomiaru miksu 150 PDF oraz wariantu samych skanów. Nie stroić tych decyzji na odłożonym zbiorze.

Kontrole: 172 testy, ruff i mypy przeszły. Nowe testy obejmują sprzeczne cyfry, geometrię
wierszy, odzyskiwanie tekstu, polskie znaki, powielone głosy, awarię silnika, cache częściowego
ensemble i zatrzymanie długiego etapu lokalnego z zachowaniem poprawnych plików JSON.

## Źródła

- [Modele RapidOCR](https://rapidai.github.io/RapidOCRDocs/main/en/model_list/)
- [tesserocr: dołączone biblioteki i zasady używania API](https://github.com/sirfz/tesserocr)
- [Oficjalne wagi Tesseract best](https://github.com/tesseract-ocr/tessdata_best)
- [Format TSV i wiersze Tesseract](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
