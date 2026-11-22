# Doprecyzowania UZP z dostarczonej rozmowy Discord

Źródłem są wypowiedzi wklejone przez użytkownika, z godzinami, bez dat poszczególnych wiadomości.
Sprawdzono również bieżące README i słownik powodów w publicznym repozytorium UZP.
Zachowujemy rozbieżności między źródłami, gdy ich kolejności nie da się jednoznacznie ustalić.

## Zmiany mające wpływ na rozwiązanie

- O 21:17 i 23:00 ustalono zachowanie kodu kraju rachunku, jeśli występuje w dokumencie,
  i usunięcie whitespace. Kodu kraju nie należy dopisywać do rachunku krajowego.
  UZP uznał brak prefiksu w części starych goldów za błąd. Poprawiono normalizator, schemat,
  instrukcje modelu i przywracanie jednoznacznej postaci źródłowej przy scalaniu.
  Ekstraktor obsługuje rachunek krajowy, formaty ze spacjami PL, DE, GB, FR, NL, BE, CH
  oraz ciągły zapis innych kodów. Grupowane rachunki pozostałych krajów wymagają rozszerzenia.
  Nie sprawdzamy checksum jako warunku akceptacji, ponieważ syntetyki bywają niepoprawne.
- O 11:26 i 13:26 podano próg zgodności 80% dla nazwy firmy pełnomocnika, tytułu przelewu,
  nazwy odwołującego/zamawiającego i tytułu zamówienia. Algorytm podobieństwa, szczegóły
  normalizacji i zakres niektórych pól nie zostały podane. Dotychczasowe exact F1 jest
  lokalnym pomiarem. Wartości 0,9793 i 0,9970 nie są pomiarami oficjalnego scorera.
- Ten sam opis definiuje nieprawidłową obecną wartość jako FP, a brak oczekiwanej wartości
  jako FN. Nasz scorer przypisuje błędnej wartości FP+FN. Do czasu otrzymania skryptu
  należy traktować lokalne wyniki i zalecenia dotyczące abstencji jako zależne od przyjętej metryki.
  Puste wartości po obu stronach opisano jako TN.
- O 13:26 potwierdzono dopasowanie list po treści. To wspiera parowanie bez zależności
  od indeksu elementu, nadal bez wskazania konkretnego algorytmu.
- O 12:47 opisano test jako syntetyczne dokumenty na podstawie prawdziwych spraw.
  O 15:49 dodano skany, a o 22:23 potwierdzono ocenę obu rodzajów.
  Wcześniejsze stwierdzenie, że test będzie obejmował rzeczywiste dokumenty, było zbyt mocne.
- O 15:55 potwierdzono mianownik imion i nazwisk. O 21:17 nakazano zachować nazwę firmy
  z dokumentu, nawet gdy różni się od CEIDG. Nie należy dowolnie poprawiać nazw podmiotów.
- O 22:44 dopuszczono lokalny mały NER. To dodatkowa możliwość dla osób i ról,
  wymagająca własnego pomiaru jakości, czasu i licencji.
- O 13:43 opis powodu 6 poprawiono na „Odpowiedzi/wyjaśnienia do SWZ”.
  Obecna konfiguracja już używa tej wartości. Kod 12 ma opis w aktualnym słowniku.
- O 13:50 i 22:32 potwierdzono dokładnie pusty obiekt JSON. O 22:19 potwierdzono
  nazwę wyjścia zgodną z PDF-em. Schemat wynikowy ma być ścisły, bez dodanych cytatów.
- O 11:23 i 11:28 dopuszczono płatne API z kluczem dla oceniających. Późniejsza wiadomość
  o 15:17 wykluczyła hosting LLM przez osobę prywatną. Wymagany hosting firmowy
  z polityką obejmującą zakres i miejsce przetwarzania. README wskazuje EU/EOG.
  Klucz można przekazać przez formularz zgłoszeniowy według wiadomości z 17:20.

## Limit czasu nadal wymaga rozstrzygnięcia

Nowszy fragment przekazany przez użytkownika: na pytanie o jeden PDF zawierający pismo
z doklejonym załącznikiem (np. pełnomocnictwo + odpis KRS) UZP | Dominik Olczak o 00:03
odpowiedział, że podczas oceny nie będzie takiego przypadku. Nie oznacza to, że dokumenty
będą jednostronicowe. Nie jest potrzebne rozdzielanie jednego PDF na wiele wyników JSON.
Uwagi uczestników o instalacji Tesseract nie ustanawiają wymagań środowiska organizatora.
Obecne `tesserocr==2.13.0` z dostępnym kołem Python 3.13 zawiera biblioteki natywne;
instalację i działanie na naszym Linuxie sprawdzono, a wagi pol+eng zajmują 26,11 MiB.

Wiadomość z 12:47 o braku limitu została uzupełniona później. O 21:13 podano,
że samo ograniczenie zapytań nie daje kary, ale aplikacja może nie zdążyć.
O 22:32 liczba plików i czas były jeszcze nieustalone.
Sprawdzone podczas tej aktualizacji README wciąż mówi o 150 PDF i 45 minutach.
Pozostawiono budżet skryptów startowych 2640 sekund jako dotychczasowe ustawienie.
Rozmowa nie dowodzi zniesienia limitu. Wysoki RPM pozwala ukończyć więcej ulepszeń
przed końcem dostępnego czasu.

## Konsekwencje dla pomiarów

Historyczne wyniki zachowujemy z ich konwencją. Nie modyfikowano oficjalnych goldów.
Zachowywanie prefiksu może obniżyć exact F1 względem błędnych etykiet treningowych.
Zmiana promptu i wartości wstępnych przelewów zmienia klucze cache, więc niepełny replay
nie może służyć do bezpośredniego porównania nowych i starych wariantów.
Próg 80% z nieznaną miarą nie uzasadnia osłabienia grounding identyfikatorów.
Następna aktualizacja ewaluatora powinna raportować jawnie nazwany wariant zgodny z opisem
Discorda obok historycznego exact, a pełną zgodność zadeklarować po otrzymaniu skryptu UZP.

Źródła publiczne sprawdzone podczas aktualizacji:
- [README UZP](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026)
- [Słownik powodów](https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/blob/main/oczekiwane-json/czynnosc_powod_id_dict.md)

## Wdrożony wariant ewaluatora

Polecenie `python -m doxforge score runs/NAZWA --metric discord-proxy` wybiera wariant
`ignore/discord_ratio80_proxy`. Dopasowanie elementów list używa tego samego kryterium
porównania pól co wybrana metryka. Wariant historyczny zachowuje wcześniejsze zachowanie.
Wynik ma jawne założenia o rapidfuzz.fuzz.ratio, whitespace, wielkości liter i progu 80.
To lokalne przybliżenie oczekujące potwierdzenia skryptem UZP.

Pomiar po wdrożeniu:
- Stare reguły na 207 dokumentach M4, stare goldy i stary zapis IBAN dały 0,9793 exact
  oraz 0,9825 w proxy Discorda.
- Bieżące reguły na 207 dokumentach dały 0,9357 exact oraz 0,9597 w proxy. W 95 polach
  przewidywany rachunek różnił się od starego goldu wyłącznie dodanym, obecnym w źródle PL.
  Historyczne etykiety zachowano bez zmian. Poza rachunkami największe straty dotyczyły
  podpisujących pełnomocnictwo, wielopodmiotowych zamawiających i wskazania lidera.
- Snapshot 127 ukończonych dokumentów M5 dał 0,9929 exact i 0,9976 w proxy.
  To niekompletny przebieg procesu uruchomionego przed ostatnimi zmianami kodu,
  promptu i IBAN. Nie porównywać tych 127 plików z pełnymi 207 jako poprawy modelu.
- Testy obejmują ograniczenie fuzzy do wskazanych pól, parowanie odwróconych list
  po treści oraz osobne naliczanie błędów. 133 testy przeszły.

Raporty zapisano w `runs/discord-metric-review/`, w tym
`m4-train-comparison.json`, `discord-metric-review-comparison.json` i `m5-train-comparison.json`.
