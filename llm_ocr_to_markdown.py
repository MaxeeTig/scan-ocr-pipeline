#!/usr/bin/env python3
"""
LLM-based OCR: extract text from a scanned image using a vision model and save as Markdown.

Uses OpenRouter (https://openrouter.ai) with a vision-capable model (default: gpt-4o-mini).
Optimized for bilingual documents: English (main) and Russian (translations/comments).

Before sending the image to the model, it is preprocessed so the LLM sees horizontal text:
  - EXIF orientation is applied (scanner/camera rotation).
  - If Tesseract is installed, orientation detection (OSD) rotates the image so text is
    horizontal. This improves quality for portrait scans with vertically oriented lines.

Requires:
  - OPENROUTER_API_KEY in environment or .env
  - pip install openai python-dotenv
  - pip install pillow  (recommended: normalizes image format so provider accepts it)
  - Tesseract on PATH (optional): enables OSD-based rotation for better portrait/vertical-line scans

Usage:
  python llm_ocr_to_markdown.py image.png [output.md]
  python llm_ocr_to_markdown.py scan_output.png
  python llm_ocr_to_markdown.py scan_output.png document.md
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"

# Prompt for bilingual (EN + RU) document → clean Markdown
OCR_SYSTEM_PROMPT = """You are an OCR assistant. Extract all text from the image exactly as written.
- Output valid Markdown: use headings for titles, keep paragraphs and lists.
- Preserve both English and Russian text; do not translate or omit any language.
- Keep layout and structure (paragraphs, line breaks, bullet points, styles - e.g italic or bold where applicable).
- Do not add commentary, explanations, or text that is not in the image.
- Output only the extracted document in Markdown."""

OCR_USER_PROMPT = """Extract all text from this scanned document and format it as Markdown.
The document is mostly in English with some Russian (translations or comments). Preserve both languages and the structure. Output only the Markdown content."""


def _normalize_image_to_png(
    image_path: Path,
    max_size_kb: int = 4096,
    fix_orientation: bool = True,
) -> tuple[bytes, str] | None:
    """
    Open image with PIL and re-encode as PNG so content matches image/png.
    Handles misnamed files (e.g. .png that is actually BMP) and avoids "Invalid image data".

    When fix_orientation is True (default):
      - Applies EXIF orientation so the image is upright (scanner/camera rotation).
      - If Tesseract is installed (pytesseract), runs orientation detection (OSD) and
        rotates the image so text is horizontal. This greatly improves LLM-OCR quality
        for portrait scans with vertically oriented lines.

    Returns (png_bytes, "image/png") or None if PIL is not available.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    path = Path(image_path)
    with Image.open(path) as img:
        # 1) Apply EXIF orientation so we work with the intended viewing orientation
        if fix_orientation:
            img = ImageOps.exif_transpose(img)

        # 2) Optional: use Tesseract OSD to rotate so text is horizontal (fixes portrait + vertical lines)
        if fix_orientation:
            try:
                import pytesseract
                osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
                if osd.get("rotate", 0) != 0:
                    img = img.rotate(osd["rotate"], expand=True, resample=Image.Resampling.BICUBIC)
            except Exception:
                pass  # Tesseract not installed or OSD failed; continue with EXIF-only

        # Convert to RGB for consistency; grayscale stays as L for smaller PNG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Optional: resize if image is huge to stay under provider limits
        w, h = img.size
        if max_size_kb and (w > 4096 or h > 4096):
            ratio = min(4096 / w, 4096 / h)
            new_size = (int(w * ratio), int(h * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        raw = buf.getvalue()

        # If still very large, re-encode as JPEG for smaller payload (many providers accept both)
        if max_size_kb and len(raw) > max_size_kb * 1024 and img.mode == "RGB":
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            raw = buf.getvalue()
            return (raw, "image/jpeg")
        return (raw, "image/png")


def image_to_base64_data_url(image_path: Path) -> str:
    """Read image file and return a data URL (e.g. data:image/png;base64,...)."""
    path = Path(image_path)
    # Prefer normalized image so content type always matches (fixes .png that is actually BMP, etc.)
    normalized = _normalize_image_to_png(path)
    if normalized is not None:
        raw, mime = normalized
        b64 = base64.b64encode(raw).decode("utf-8")
        return f"data:{mime};base64,{b64}"
    # Fallback: raw bytes by extension (may fail if extension doesn't match content)
    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix, "image/png")
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def llm_ocr_to_text(image_path: Path, model: str = DEFAULT_MODEL) -> str:
    """
    Send image to OpenRouter vision model and return extracted text (Markdown).
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY not set. Set it in the environment or in a .env file."
        )

    data_url = image_to_base64_data_url(image_path)
    client = OpenAI(base_url=OPENROUTER_BASE, api_key=api_key)

    messages = [
        {"role": "system", "content": OCR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_USER_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    text = completion.choices[0].message.content or ""
    return text.strip()


def llm_image_to_markdown(
    image_path: str | Path,
    output_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> Path:
    """
    Run LLM-based OCR on the image and save the result as a Markdown file.

    Returns the path of the written .md file.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    if output_path is None:
        output_path = image_path.with_suffix(".md")
    else:
        output_path = Path(output_path)

    text = llm_ocr_to_text(image_path, model=model)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python llm_ocr_to_markdown.py <image> [output.md]")
        print("  e.g.  python llm_ocr_to_markdown.py scan_output.png")
        print("  e.g.  python llm_ocr_to_markdown.py scan_output.png document.md")
        print()
        print("Requires OPENROUTER_API_KEY in environment or .env")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    try:
        out = llm_image_to_markdown(image_path, output_path)
        print(f"Saved: {out.resolve()}")
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    except ValueError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"LLM OCR failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
