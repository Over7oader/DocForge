"""Download/check pinned local OCR models before the timed run. No document input or API key."""

from doxforge.ocr import load_ocr_settings, prepare_models


def main() -> None:
    prepare_models()
    print(f"OCR models ready: {load_ocr_settings().mode}")


if __name__ == "__main__":
    main()
