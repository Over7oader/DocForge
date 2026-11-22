# Koncepcja ekstrakcji lokalnej na CPU

Celem etapu koncepcyjnego jest zastąpienie zewnętrznej ekstrakcji LLM przy zachowaniu
mierzalnej jakości. Aktualny pipeline nadal korzysta z OpenRoutera. Poniższe modele
są kandydatami do pomiaru, a nie komponentami obecnego rozwiązania konkursowego.

## Zmiana architektury

Zachowujemy klasyfikator dokumentów, OCR CPU, typowane ekstraktory identyfikatorów,
schematy YAML, słowniki czynności oraz walidację. Lokalny encoder proponuje spany osób,
organizacji, ról i zdarzeń. Dekoder wiąże wartości z właściwym podmiotem i sprawdza
ograniczenia schematu. Morfologia normalizuje imiona i nazwiska po wykryciu osoby.
Wartości liczbowe są kopiowane ze spanów źródłowych i normalizowane znanymi typami.

Ta kolejność ogranicza dopisywanie wartości. Wykrycie osoby nie oznacza rozpoznania jej roli.
Potrzebne są osobne relacje mocodawca–podpisujący–pełnomocnik i lider–partner.
Powód odwołania wymaga wyboru pierwszej zaskarżonej czynności oraz rozróżnienia czynności
od zaniechania. Błędy OCR pozostają wspólnym ograniczeniem obu architektur.

## Kandydaci

- Morfeusz 2 jest analizatorem i generatorem morfologicznym, z programem i danymi na
  licencji BSD-2-Clause. Rozpoznaje m.in. odmianę imion i nazwisk. Dokumentacja licencji
  jest dostępna u autorów. [Licencja](https://morfeusz.sgjp.pl/doc/license/en)
- GLiNER multi-v2.1 jest wielojęzycznym modelem ekstrakcji na Apache-2.0. Dokumentacja
  biblioteki opisuje backend ONNX Runtime na CPU. Wielkość parametrów należy potwierdzić
  na rzeczywistym checkpointcie, ponieważ tabela autorów i oznaczenie rozmiaru na Hubie
  różnią się. To kandydat do wydobywania spanów, nie pełny parser danych sprawy.
  [Karta modelu](https://huggingface.co/urchade/gliner_multi-v2.1),
  [backend CPU](https://github.com/urchade/GLiNER/blob/main/docs/instalation.md)
- GLiNER2.5 multi-v1 to wielojęzyczny encoder deklarowany przez autorów jako 287M,
  obsługujący ekstrakcję rekordów, relacji i klasyfikację. Model Hub potwierdził licencję
  Apache-2.0 i rewizję 7b005274e82da37dcf1f5fd9d6a2ef413c6a004c podczas tego przeglądu.
  Dokumentacja wymienia Python 3.10+ i testy na 3.10–3.12, dlatego nasz wymagany
  Python 3.13 potrzebuje osobnej próby uruchomienia.
  [Repozytorium](https://github.com/fastino-ai/GLiNER2),
  [model](https://huggingface.co/fastino/gliner2.5-multi-v1)

## Wykonana próba morfologii

W odizolowanym środowisku Python 3.13.18 zainstalowano morfeusz2==1.99.15.
Nie zmieniono zależności uruchomieniowych pipeline'u.

scripts/morphology_probe.py poprawnie przekształcił trzy jednoznaczne przykłady:
Janowi Kowalskiemu, Annę Malinowską i Janowi Lisowi. Dla niejednoznacznego
Stanisława Nowaka oraz nieobsłużonej kombinacji Emmy Noether zwrócił brak wyniku.
Wybór lematu korzysta z etykiet imię/nazwisko i wymaga jednoznacznego odczytu każdego członu.
Tysiąc normalizacji przykładu dwuwyrazowego zajęło około 0,043 s, po inicjalizacji analizatora.
Ten pomiar nie obejmuje NER, OCR ani wykrywania ról i nie jest wynikiem F1 całego systemu.

Reprodukcja:
```bash
uv venv --python .venv/bin/python runs/cpu-research/venv
uv pip install --python runs/cpu-research/venv/bin/python -r requirements-cpu-research.txt
runs/cpu-research/venv/bin/python scripts/morphology_probe.py
```

## Kryteria dalszego pomiaru

Najpierw należy zamrozić lokalny i chmurowy wariant oraz wspólny zestaw z etykietami.
Podział musi grupować sprawę, jej skan, augmentacje i pochodzenie szablonu. Zbiór syntetyczny
jednego generatora nie wystarcza do potwierdzenia generalizacji. Każdą korektę historycznych
etykiet trzeba oznaczyć źródłem i niezależnie od predykcji.

Porównanie powinno obejmować F1 wszystkich pól, osoby i relacje osobno, czas 150 PDF,
szczytowe RAM, rozmiar artefaktów oraz dostępność Python 3.13 bez GPU i internetu.
Dopiero po tym pomiarze można zdecydować o zastąpieniu LLM. Mały encoder i szybka morfologia
nie stanowią jeszcze dowodu, że osiągną jakość pełnego modelu na polskich dokumentach.
