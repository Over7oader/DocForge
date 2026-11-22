# Uruchomienie

Z katalogu `/opt/solution` (dokumenty w `input/`, wyniki w `output/`):

```bash
./start.sh
```

Komenda nie wymaga interakcji i kończy się sama, najpóźniej po 44 minutach (budżet 2640 s;
dokumenty nieukończone w budżecie dostają `{}`).

## Usługi zewnętrzne

- LLM `ministral-8b-2512` (Ministral 3 8B, Apache-2.0) bezpośrednio przez API Mistral AI w regionie UE
  (`https://api.eu.mistral.ai/v1/`, firma z siedzibą we Francji, przetwarzanie w UE). Klucz jest
  w `/opt/solution/.env` (`LLM_API_KEY`). Limit klucza: 750 zapytań/min.
- Bez dostępu do API rozwiązanie działa dalej na samych regułach (wynik reguł jest zapisywany przed
  wywołaniem LLM). Pełne wyłączenie LLM: `DOXFORGE_LLM=none ./start.sh`.
- OCR działa lokalnie na CPU (PP-OCRv6, PP-OCRv5, Tesseract); modele są już pobrane.

Instalacja (już wykonana na tym serwerze): `./setup_vm.sh`.

## Interfejs webowy (podgląd ekstrakcji)

Działa stale pod `http://20.215.210.103:8080` (start po restarcie: crontab `@reboot`). Uruchamiany
z osobnej kopii `/opt/solution/gui`, więc typy rejestrowane w GUI nie zmieniają konfiguracji
używanej przez `./start.sh`. Ręczne uruchomienie: `cd /opt/solution/gui && ./start_gui.sh`.
