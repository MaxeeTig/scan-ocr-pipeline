#!/usr/bin/env python3
"""
OCR an image to text and save as Markdown (Step 3 + 4).

Uses Tesseract via pytesseract. Requires Tesseract installed on the system:
  winget install UB-Mannheim.TesseractOCR
  or https://github.com/tesseract-ocr/tesseract

Usage:
  python ocr_to_markdown.py image.png [output.md]
  python ocr_to_markdown.py scan_output.png
  python ocr_to_markdown.py scan_output.png document.md

If output path is omitted, writes to <image_stem>.md in the same folder as the image.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ocr_image_to_text(image_path: Path, lang: str = "eng") -> str:
    """Run Tesseract OCR on an image and return the extracted text."""
    import pytesseract
    from PIL import Image

    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang=lang).strip()


def image_to_markdown(
    image_path: str | Path,
    output_path: str | Path | None = None,
    lang: str = "eng",
) -> Path:
    """
    OCR the image and save the text as a Markdown file.

    Returns the path of the written .md file.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    if output_path is None:
        output_path = image_path.with_suffix(".md")
    else:
        output_path = Path(output_path)

    text = ocr_image_to_text(image_path, lang=lang)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python ocr_to_markdown.py <image> [output.md]")
        print("  e.g.  python ocr_to_markdown.py scan_output.png")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    try:
        out = image_to_markdown(image_path, output_path)
        print(f"Saved: {out.resolve()}")
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"OCR failed: {e}")
        if "tesseract" in str(e).lower():
            print("Ensure Tesseract is installed and on PATH (e.g. winget install UB-Mannheim.TesseractOCR)")
        sys.exit(1)


if __name__ == "__main__":
    main()
