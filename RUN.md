# Uruchomienie

Z katalogu `/opt/solution` (dokumenty w `input/`, wyniki w `output/`):

```bash
./start.sh
```

Komenda nie wymaga interakcji i kończy się sama, najpóźniej po 44 minutach (budżet 2640 s;
dokumenty nieukończone w budżecie dostają `{}`).

## Usługi zewnętrzne

- LLM `mistralai/ministral-8b-2512` (Apache-2.0) przez OpenRouter, wyłącznie u dostawcy
  `mistral/eu`, bez fallbacków i z `data_collection=deny`. Klucz jest w `/opt/solution/.env`
  (`OPENROUTER_API_KEY`).
- Bez dostępu do API rozwiązanie działa dalej na samych regułach (wynik reguł jest zapisywany przed
  wywołaniem LLM). Pełne wyłączenie LLM: `DOXFORGE_LLM=none ./start.sh`.
- OCR działa lokalnie na CPU (PP-OCRv6, PP-OCRv5, Tesseract); modele są już pobrane.

Instalacja (już wykonana na tym serwerze): `./setup_vm.sh`.
