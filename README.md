# Scan → OCR → Markdown pipeline (Windows)

Semi-automatic pipeline: **scan document → OCR → store as Markdown** (spellcheck optional, later).

## 1. Can you access the scanner?

**From this environment:** No — we can’t physically access your scanner.  
**From your PC:** Yes — when you run the scripts on your Windows machine, they use:

- **WIA** (Windows Image Acquisition) to list and control scanners.
- **win32print** to list printers.

So the scanner is accessed by the code that runs locally on your computer.

---

## 2. List scanning and printing devices

**Script:** `list_devices.py`  
**Framework:** Python + **pywin32** (WIA for scanners, `win32print` for printers).

### Setup

```powershell
cd d:\Documents\Docs_Personal\Development\scan-ocr-pipeline
pip install -r requirements.txt
```

### Run

```powershell
python list_devices.py
```

You’ll see:

- **Printers** (local + network)
- **Scanners / imaging devices** (WIA)

---

## 3. Sample scanning

**Script:** `scan_sample.py`

Opens the Windows WIA scan dialog so you can acquire one image from your scanner (e.g. Panasonic) and save it to a file.

```powershell
python scan_sample.py              # saves to scan_output.png
python scan_sample.py my_scan.png  # saves to my_scan.png
```

The scanner must be on and the driver installed (WIA). If the default format doesn’t match the extension, try `.bmp` (e.g. `scan_sample.py out.bmp`).

---

## 4. OCR → Markdown

**Script:** `ocr_to_markdown.py`

Runs Tesseract OCR on a scanned image and saves the text as a Markdown (`.md`) file.

**Tesseract must be installed** (one-time):

```powershell
winget install UB-Mannheim.TesseractOCR
```

Or download from [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract). Ensure `tesseract` is on your PATH.

**Install Python deps** (if not already):

```powershell
pip install -r requirements.txt
```

**Run:**

```powershell
python ocr_to_markdown.py scan_output.png
# → creates scan_output.md next to the image

python ocr_to_markdown.py scan_output.png my_doc.md
# → writes to my_doc.md
```

For other languages, edit the `lang` argument in the script (default `eng`; e.g. `rus`, `eng+rus`).

### LLM-based OCR (alternative)

**Script:** `llm_ocr_to_markdown.py`

Uses a vision model via OpenRouter (default: `openai/gpt-4o-mini`) for higher-quality OCR, especially for bilingual (English + Russian) documents.

Images are **preprocessed** before being sent to the model: EXIF orientation is applied, and if Tesseract is installed, orientation detection (OSD) rotates the image so text is horizontal. This improves results for portrait scans or pages where lines are oriented vertically.

**Setup:** Set `OPENROUTER_API_KEY` in your environment or in a `.env` file in the project folder. Optional: install Tesseract (see above) for OSD-based rotation. Then:

```powershell
pip install -r requirements.txt
python llm_ocr_to_markdown.py scan_output.png
# → creates scan_output.md

python llm_ocr_to_markdown.py scan_output.png my_doc.md
# → writes to my_doc.md
```

---

## 5. Pipeline target (step by step)

| Step | What | Status |
|------|------|--------|
| 1 | List scanners/printers | ✅ Done (`list_devices.py`) |
| 2 | Scan a page (WIA) → save image | ✅ Done (`scan_sample.py`) |
| 3 | OCR image → text | ✅ Done (`ocr_to_markdown.py` or `llm_ocr_to_markdown.py`) |
| 4 | Save text as Markdown | ✅ Done (same script) |
| 5 | Optional: spellcheck | Later |

**Full flow:** `list_devices.py` → `scan_sample.py` → `ocr_to_markdown.py` → you have a `.md` file.

---

## 6. Web app (Phase 1 – completed)

**Script:** `app.py`

Local web UI for the book-scan pipeline. **Phase 1 is complete.**

- **Scan** a spread (WIA dialog) → saved as `work/scans/image_001.png`, `image_002.png`, … (sequential).
- **Set next spread number** to restart from 1 or continue from any index (files stay `image_nnn.png`).
- **Process** → EXIF + OSD orientation, optional grayscale; output in `work/cleaned/image_nnn.png`.
- **Rotate 180°** and **Deskew** (slight skew correction) on the cleaned image; preview updates and file is saved in place.
- **Approve** or **Rescan** (rescan replaces the current spread’s file). Scan errors show the scanner/subprocess message in the UI.

```powershell
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open **http://127.0.0.1:8000**. Use **Scan** → **Process** → optionally **Rotate 180°** / **Deskew** → **Approve** or **Rescan**.

---

## Requirements

- **Windows** (WIA and win32print are Windows-only).
- **Python 3.8+**.
- **Tesseract OCR** on PATH (see above).
