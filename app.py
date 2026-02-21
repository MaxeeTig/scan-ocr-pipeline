#!/usr/bin/env python3
"""
Phase 1: Local web app for scan → process → approve/rescan.

Run: uvicorn app:app --reload
Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

# Project root (where scan_sample.py and work/ live)
PROJECT_ROOT = Path(__file__).resolve().parent
WORK_DIR = PROJECT_ROOT / "work"
SCANS_DIR = WORK_DIR / "scans"
CLEANED_DIR = WORK_DIR / "cleaned"
TEXTS_DIR = WORK_DIR / "texts"
NEXT_INDEX_FILE = WORK_DIR / "next_index.txt"

# Spread index: 3-digit zero-padded
INDEX_DIGITS = 3


def _ensure_work_dirs() -> None:
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    TEXTS_DIR.mkdir(parents=True, exist_ok=True)


def _read_next_index() -> int:
    _ensure_work_dirs()
    if NEXT_INDEX_FILE.is_file():
        try:
            return int(NEXT_INDEX_FILE.read_text().strip())
        except ValueError:
            pass
    return 1


def _write_next_index(n: int) -> None:
    NEXT_INDEX_FILE.write_text(str(n), encoding="utf-8")


def _spread_filename(index: int) -> str:
    return f"image_{index:0{INDEX_DIGITS}d}.png"


def _run_scan(output_path: Path) -> tuple[bool, str]:
    """
    Run WIA scan in subprocess; save to output_path.
    Returns (True, "") if image was acquired, (False, error_message) otherwise.
    Removes existing file first so WIA is not asked to overwrite (can cause failures).
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        try:
            output_path.unlink()
        except OSError as e:
            return False, f"Cannot remove existing file for overwrite: {e}"
    path_str = str(output_path)
    cmd = [
        sys.executable,
        "-c",
        f"from scan_sample import scan_to_file; exit(0 if scan_to_file({path_str!r}) else 1)",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    err_msg = (result.stderr or "").strip() or (result.stdout or "").strip()
    if result.returncode != 0:
        return False, err_msg or "Scan cancelled or failed (no output from scanner)."
    return True, ""


app = FastAPI(title="Scan-OCR Pipeline", version="0.1.0")


class ScanBody(BaseModel):
    rescan_for_index: int | None = None


class ProcessBody(BaseModel):
    index: int
    to_grayscale: bool = True


class StateBody(BaseModel):
    next_scan_index: int


@app.on_event("startup")
def startup() -> None:
    _ensure_work_dirs()


@app.post("/api/scan")
def api_scan(body: ScanBody | None = None) -> dict:
    """Scan one spread. Use rescan_for_index to overwrite that spread's raw image."""
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail="Scanning is only supported on Windows (WIA).")
    body = body or ScanBody()
    if body.rescan_for_index is not None:
        index = body.rescan_for_index
        if index < 1:
            raise HTTPException(status_code=400, detail="rescan_for_index must be >= 1")
        out_path = SCANS_DIR / _spread_filename(index)
    else:
        index = _read_next_index()
        out_path = SCANS_DIR / _spread_filename(index)
        _write_next_index(index + 1)
    ok, err_msg = _run_scan(out_path)
    if not ok:
        raise HTTPException(status_code=422, detail=err_msg or "Scan cancelled or failed.")
    return {"ok": True, "path": str(out_path), "index": index}


@app.post("/api/process")
def api_process(body: ProcessBody) -> dict:
    """Process (clean + orient) the raw scan for the given spread index."""
    index = body.index
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    raw_path = SCANS_DIR / _spread_filename(index)
    if not raw_path.is_file():
        raise HTTPException(status_code=404, detail=f"No scan found for spread {index}. Scan first.")
    cleaned_path = CLEANED_DIR / _spread_filename(index)
    from image_processing import process_image
    if not process_image(raw_path, cleaned_path, to_grayscale=body.to_grayscale):
        raise HTTPException(status_code=500, detail="Image processing failed.")
    return {
        "path": str(cleaned_path),
        "url": f"/api/serve/cleaned/{_spread_filename(index)}",
        "index": index,
    }


@app.post("/api/process/rotate-180")
def api_process_rotate_180(body: ProcessBody) -> dict:
    """Rotate the cleaned image for this spread 180° in place. File is updated; use for visual feedback then Approve."""
    index = body.index
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    cleaned_path = CLEANED_DIR / _spread_filename(index)
    if not cleaned_path.is_file():
        raise HTTPException(status_code=404, detail=f"No cleaned image for spread {index}. Process first.")
    from image_processing import rotate_image_180
    if not rotate_image_180(cleaned_path):
        raise HTTPException(status_code=500, detail="Rotate 180° failed.")
    return {
        "url": f"/api/serve/cleaned/{_spread_filename(index)}",
        "index": index,
    }


@app.post("/api/process/deskew")
def api_process_deskew(body: ProcessBody) -> dict:
    """Deskew the cleaned image (correct slight rotation). File is updated; preview updates for visual feedback."""
    index = body.index
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    cleaned_path = CLEANED_DIR / _spread_filename(index)
    if not cleaned_path.is_file():
        raise HTTPException(status_code=404, detail=f"No cleaned image for spread {index}. Process first.")
    from image_processing import deskew_image
    if not deskew_image(cleaned_path):
        raise HTTPException(status_code=500, detail="Deskew failed.")
    return {
        "url": f"/api/serve/cleaned/{_spread_filename(index)}",
        "index": index,
    }


@app.post("/api/process/crop-borders")
def api_process_crop_borders(body: ProcessBody) -> dict:
    """Crop the cleaned image to content bounds (trim borders) in place. File is updated; preview updates for visual feedback."""
    index = body.index
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    cleaned_path = CLEANED_DIR / _spread_filename(index)
    if not cleaned_path.is_file():
        raise HTTPException(status_code=404, detail=f"No cleaned image for spread {index}. Process first.")
    from image_processing import crop_to_content
    if not crop_to_content(cleaned_path):
        raise HTTPException(status_code=500, detail="Crop borders failed (no content found or error).")
    return {
        "url": f"/api/serve/cleaned/{_spread_filename(index)}",
        "index": index,
    }


@app.post("/api/ocr")
def api_ocr(body: ProcessBody) -> dict:
    """Run LLM-based OCR on the cleaned image for the given spread index and save as Markdown."""
    index = body.index
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    cleaned_path = CLEANED_DIR / _spread_filename(index)
    if not cleaned_path.is_file():
        raise HTTPException(status_code=404, detail=f"No cleaned image for spread {index}. Process first.")
    output_path = TEXTS_DIR / f"image_{index:0{INDEX_DIGITS}d}.md"
    try:
        from llm_ocr_to_markdown import llm_image_to_markdown
        llm_image_to_markdown(cleaned_path, output_path)
    except ValueError as e:
        # API key missing or similar configuration error
        raise HTTPException(status_code=500, detail=f"OCR configuration error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR failed: {e}")
    return {
        "path": str(output_path),
        "index": index,
    }


@app.get("/api/serve/scans/{filename}")
def serve_scans(filename: str) -> FileResponse:
    if not filename.startswith("image_") or not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = SCANS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/serve/cleaned/{filename}")
def serve_cleaned(filename: str) -> FileResponse:
    if not filename.startswith("image_") or not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = CLEANED_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cleaned/list")
def api_cleaned_list() -> dict:
    """Return sorted list of spread indices that have cleaned images (for Compare tab)."""
    _ensure_work_dirs()
    indices: list[int] = []
    for p in CLEANED_DIR.glob("image_*.png"):
        try:
            # image_001.png -> 1
            num = int(p.stem.split("_", 1)[1])
            if num >= 1:
                indices.append(num)
        except (ValueError, IndexError):
            continue
    indices.sort()
    return {"indices": indices}


@app.get("/api/texts/{index}", response_class=PlainTextResponse, response_model=None)
def api_texts(index: int):
    """Return markdown content for the given spread index. 404 if no file."""
    if index < 1:
        raise HTTPException(status_code=400, detail="index must be >= 1")
    path = TEXTS_DIR / f"image_{index:0{INDEX_DIGITS}d}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No OCR result for this spread.")
    return PlainTextResponse(content=path.read_text(encoding="utf-8"))


@app.get("/api/state")
def api_state() -> dict:
    """Return next scan index (for UI to show 'Next spread will be N')."""
    return {"next_scan_index": _read_next_index()}


@app.put("/api/state")
def api_set_state(body: StateBody) -> dict:
    """Set the next spread number. Use to restart from 1 or continue from a position (e.g. 5). Next Scan will save as image_NNN.png."""
    n = body.next_scan_index
    if n < 1:
        raise HTTPException(status_code=400, detail="next_scan_index must be >= 1")
    _write_next_index(n)
    return {"next_scan_index": _read_next_index()}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scan pipeline – Phase 2</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 1rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; }
    .tab-bar { display: flex; gap: 0; margin-bottom: 1rem; border-bottom: 1px solid #ccc; }
    .tab-bar button { padding: 0.5rem 1rem; border: 1px solid #ccc; border-bottom: none; background: #f5f5f5; cursor: pointer; margin-right: 2px; }
    .tab-bar button.active { background: #fff; font-weight: 600; margin-bottom: -1px; padding-bottom: calc(0.5rem + 1px); }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .step { margin: 1rem 0; }
    button { padding: 0.5rem 1rem; margin-right: 0.5rem; cursor: pointer; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    #spreadInfo { font-weight: 600; margin-bottom: 0.5rem; }
    #imageBox { margin: 1rem 0; min-height: 200px; }
    #imageBox img { max-width: 100%; border: 1px solid #ccc; }
    .error { color: #c00; }
    .success { color: #060; }
    .setIndex .hint { color: #666; font-size: 0.9rem; margin-left: 0.5rem; }
    .compare-toolbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
    .compare-toolbar #compareSpreadLabel { font-weight: 600; min-width: 8rem; }
    .compare-layout { display: flex; gap: 1rem; min-height: 60vh; }
    .compare-pane { flex: 1; min-width: 0; overflow: auto; }
    .compare-pane img { max-width: 100%; border: 1px solid #ccc; display: block; }
    .compare-md { padding: 0.5rem; border: 1px solid #ccc; background: #fafafa; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; word-break: break-word; max-width: 100%; width: 100%; box-sizing: border-box; display: block; }
    .compare-md, .compare-md * { word-wrap: break-word; overflow-wrap: break-word; word-break: break-word; hyphens: auto; }
    .compare-md p, .compare-md li, .compare-md div, .compare-md span { overflow-wrap: break-word; word-break: break-word; max-width: 100%; white-space: normal; }
    .compare-md h1, .compare-md h2, .compare-md h3, .compare-md h4, .compare-md h5, .compare-md h6 { margin-top: 0.5em; margin-bottom: 0.25em; overflow-wrap: break-word; word-break: break-word; white-space: normal; }
    .compare-md p { margin: 0.5em 0; }
    .compare-md ul, .compare-md ol { margin: 0.5em 0; padding-left: 1.5em; }
    .compare-md pre, .compare-md code { white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; word-break: break-word; max-width: 100%; }
    .compare-md-wrap { display: flex; flex-direction: column; height: 100%; min-height: 0; }
    .compare-md-wrap .compare-ocr-row { margin-bottom: 0.5rem; flex-shrink: 0; }
  </style>
</head>
<body>
  <h1>Scan pipeline</h1>
  <nav class="tab-bar">
    <button type="button" class="tab-btn active" data-tab="scan">Scan</button>
    <button type="button" class="tab-btn" data-tab="compare">Compare (OCR)</button>
  </nav>

  <div id="tab-scan" class="tab-panel active">
  <p id="spreadInfo">Loading…</p>

  <div class="step setIndex">
    <label for="nextIndexInput">Next spread number:</label>
    <input type="number" id="nextIndexInput" min="1" value="1" style="width: 4rem; margin: 0 0.25rem;">
    <button id="btnSetIndex">Set</button>
    <span class="hint">(Restart from 1 or continue from N; next Scan will save as image_NNN.png)</span>
  </div>

  <div class="step">
    <div style="margin-bottom: 0.5rem; font-weight: 600;">Pipeline (auto-run after scan):</div>
    <label style="display: block; margin: 0.25rem 0;"><input type="checkbox" id="optGrayscale"> Convert to grayscale</label>
    <label style="display: block; margin: 0.25rem 0;"><input type="checkbox" id="optProcess"> Run process after scan</label>
    <label style="display: block; margin: 0.25rem 0;"><input type="checkbox" id="optRotate180"> Run rotate 180° after scan</label>
    <label style="display: block; margin: 0.25rem 0;"><input type="checkbox" id="optDeskew"> Run deskew after scan</label>
    <label style="display: block; margin: 0.25rem 0;"><input type="checkbox" id="optCropBorders"> Run crop borders after scan</label>
  </div>
  <div class="step">
    <button id="btnScan">Scan</button>
    <button id="btnProcess" disabled>Process</button>
    <button id="btnRotate180" disabled>Rotate 180°</button>
    <button id="btnDeskew" disabled>Deskew</button>
    <button id="btnCropBorders" disabled>Crop borders</button>
    <button id="btnOCR" disabled>Run OCR</button>
    <button id="btnBatchOCR" disabled>Batch OCR (current → end)</button>
    <button id="btnStopBatch" disabled>Stop batch</button>
    <button id="btnRescan" disabled>Rescan</button>
    <button id="btnApprove" disabled>Approve</button>
  </div>

  <div id="imageBox"></div>
  <p id="message"></p>
  </div>

  <div id="tab-compare" class="tab-panel">
    <div class="compare-toolbar">
      <button type="button" id="btnComparePrev" disabled>Previous</button>
      <span id="compareSpreadLabel">Spread 0 of 0</span>
      <button type="button" id="btnCompareNext" disabled>Next</button>
      <label style="margin-left: 1rem;"><input type="checkbox" id="compareChecked"> Checked</label>
    </div>
    <div class="compare-layout">
      <div class="compare-pane compare-image-pane">
        <div id="compareImageBox"></div>
      </div>
      <div class="compare-pane compare-md-pane">
        <div class="compare-md-wrap">
          <div class="compare-ocr-row">
            <button type="button" id="btnCompareOCR" disabled>Run OCR</button>
            <button type="button" id="btnCompareBatchOCR" disabled>Batch OCR (current → end)</button>
            <button type="button" id="btnCompareStopBatch" disabled>Stop batch</button>
          </div>
          <div id="compareMdBox" class="compare-md"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    // Tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + tab).classList.add('active');
        if (tab === 'compare') initCompareTab();
      });
    });

    let currentIndex = null;
    const spreadInfo = document.getElementById('spreadInfo');
    const imageBox = document.getElementById('imageBox');
    const message = document.getElementById('message');
    const btnScan = document.getElementById('btnScan');
    const btnProcess = document.getElementById('btnProcess');
    const btnRescan = document.getElementById('btnRescan');
    const btnApprove = document.getElementById('btnApprove');
    const nextIndexInput = document.getElementById('nextIndexInput');
    const btnSetIndex = document.getElementById('btnSetIndex');
    const btnRotate180 = document.getElementById('btnRotate180');
    const btnDeskew = document.getElementById('btnDeskew');
    const btnCropBorders = document.getElementById('btnCropBorders');
    const btnOCR = document.getElementById('btnOCR');
    const btnBatchOCR = document.getElementById('btnBatchOCR');
    const btnStopBatch = document.getElementById('btnStopBatch');
    const optGrayscale = document.getElementById('optGrayscale');
    const optProcess = document.getElementById('optProcess');
    const optRotate180 = document.getElementById('optRotate180');
    const optDeskew = document.getElementById('optDeskew');
    const optCropBorders = document.getElementById('optCropBorders');

    let batchRunning = false;
    let batchStopRequested = false;

    // localStorage keys
    const STORAGE_KEYS = {
      grayscale: 'scanPipeline_grayscale',
      process: 'scanPipeline_process',
      rotate180: 'scanPipeline_rotate180',
      deskew: 'scanPipeline_deskew',
      cropBorders: 'scanPipeline_crop',
      compareChecked: 'scanPipeline_compare_checked'
    };

    // Load checkbox states from localStorage
    function loadCheckboxStates() {
      optGrayscale.checked = localStorage.getItem(STORAGE_KEYS.grayscale) !== 'false';
      optProcess.checked = localStorage.getItem(STORAGE_KEYS.process) === 'true';
      optRotate180.checked = localStorage.getItem(STORAGE_KEYS.rotate180) === 'true';
      optDeskew.checked = localStorage.getItem(STORAGE_KEYS.deskew) === 'true';
      optCropBorders.checked = localStorage.getItem(STORAGE_KEYS.cropBorders) === 'true';
    }

    // Save checkbox state to localStorage
    function saveCheckboxState(key, checked) {
      localStorage.setItem(key, checked ? 'true' : 'false');
    }

    // Set up change listeners for all checkboxes
    optGrayscale.addEventListener('change', () => saveCheckboxState(STORAGE_KEYS.grayscale, optGrayscale.checked));
    optProcess.addEventListener('change', () => saveCheckboxState(STORAGE_KEYS.process, optProcess.checked));
    optRotate180.addEventListener('change', () => saveCheckboxState(STORAGE_KEYS.rotate180, optRotate180.checked));
    optDeskew.addEventListener('change', () => saveCheckboxState(STORAGE_KEYS.deskew, optDeskew.checked));
    optCropBorders.addEventListener('change', () => saveCheckboxState(STORAGE_KEYS.cropBorders, optCropBorders.checked));

    // Run pipeline steps automatically after scan
    async function runPipeline(index) {
      const shouldProcess = optProcess.checked || optRotate180.checked || optDeskew.checked || optCropBorders.checked;
      
      if (!shouldProcess) {
        setMessage('Scanned as spread ' + index + '. Click Process to clean the image.', false);
        btnProcess.disabled = false;
        btnRescan.disabled = false;
        return;
      }

      // Disable all action buttons during pipeline
      btnProcess.disabled = true;
      btnRotate180.disabled = true;
      btnDeskew.disabled = true;
      btnCropBorders.disabled = true;
      btnOCR.disabled = true;
      btnBatchOCR.disabled = true;
      btnStopBatch.disabled = true;
      btnRescan.disabled = true;
      btnApprove.disabled = true;

      let cleanedUrl = null;
      let pipelineError = null;

      // Step 1: Process (if Process checkbox OR any later step is checked)
      if (shouldProcess) {
        setMessage('Processing…', false);
        try {
          const data = await api('POST', '/api/process', {
            index: index,
            to_grayscale: optGrayscale.checked
          });
          cleanedUrl = data.url + '?t=' + Date.now();
          setSpread(index, cleanedUrl);
          setMessage('Processed. Running pipeline steps…', false);
        } catch (e) {
          pipelineError = e.message;
          setMessage('Process failed: ' + e.message, true);
          // Re-enable buttons on error
          btnProcess.disabled = false;
          btnRescan.disabled = false;
          return;
        }
      }

      // Step 2: Rotate 180° (if checkbox is set)
      if (!pipelineError && optRotate180.checked && cleanedUrl) {
        setMessage('Rotating 180°…', false);
        try {
          const data = await api('POST', '/api/process/rotate-180', { index: index });
          cleanedUrl = data.url + '?t=' + Date.now();
          const img = document.getElementById('cleanedImg');
          if (img) img.src = cleanedUrl;
          setMessage('Rotated 180°. Continuing pipeline…', false);
        } catch (e) {
          pipelineError = e.message;
          setMessage('Rotate 180° failed: ' + e.message, true);
        }
      }

      // Step 3: Deskew (if checkbox is set)
      if (!pipelineError && optDeskew.checked && cleanedUrl) {
        setMessage('Deskewing…', false);
        try {
          const data = await api('POST', '/api/process/deskew', { index: index });
          cleanedUrl = data.url + '?t=' + Date.now();
          const img = document.getElementById('cleanedImg');
          if (img) img.src = cleanedUrl;
          setMessage('Deskewed. Continuing pipeline…', false);
        } catch (e) {
          pipelineError = e.message;
          setMessage('Deskew failed: ' + e.message, true);
        }
      }

      // Step 4: Crop borders (if checkbox is set)
      if (!pipelineError && optCropBorders.checked && cleanedUrl) {
        setMessage('Cropping borders…', false);
        try {
          const data = await api('POST', '/api/process/crop-borders', { index: index });
          cleanedUrl = data.url + '?t=' + Date.now();
          const img = document.getElementById('cleanedImg');
          if (img) img.src = cleanedUrl;
          setMessage('Cropped borders. Pipeline complete.', false);
        } catch (e) {
          pipelineError = e.message;
          setMessage('Crop borders failed: ' + e.message, true);
        }
      }

      // Re-enable buttons based on current state
      btnProcess.disabled = false;
      btnRescan.disabled = false;
      if (cleanedUrl) {
        btnRotate180.disabled = false;
        btnDeskew.disabled = false;
        btnCropBorders.disabled = false;
        if (!batchRunning) {
          btnOCR.disabled = false;
          btnBatchOCR.disabled = false;
          btnStopBatch.disabled = true;
        }
        if (!pipelineError) {
          btnApprove.disabled = false;
          setMessage('Pipeline complete for spread ' + index + '. Approve or adjust further if needed.', false);
        }
      }
    }

    function setMessage(text, isError) {
      message.textContent = text;
      message.className = isError ? 'error' : 'success';
    }

    function setSpread(index, cleanedUrl) {
      currentIndex = index;
      spreadInfo.textContent = 'Spread ' + index;
      btnRotate180.disabled = !cleanedUrl;
      btnDeskew.disabled = !cleanedUrl;
      btnCropBorders.disabled = !cleanedUrl;
      btnOCR.disabled = batchRunning || !cleanedUrl;
      btnBatchOCR.disabled = batchRunning || !cleanedUrl;
      btnStopBatch.disabled = !batchRunning;
      if (cleanedUrl) {
        imageBox.innerHTML = '<img id="cleanedImg" src="' + cleanedUrl + '" alt="Cleaned spread ' + index + '">';
      } else {
        imageBox.innerHTML = '';
      }
    }

    async function api(method, path, body) {
      const opts = { method };
      if (body) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || r.statusText || 'Request failed');
      return data;
    }

    btnScan.addEventListener('click', async () => {
      btnScan.disabled = true;
      setMessage('Opening scan dialog…', false);
      try {
        const data = await api('POST', '/api/scan', {});
        setSpread(data.index, null);
        const st = await api('GET', '/api/state', null);
        nextIndexInput.value = st.next_scan_index;
        updateSpreadInfo(st.next_scan_index);
        // Run pipeline automatically after scan completes
        await runPipeline(data.index);
      } catch (e) {
        setMessage(e.message, true);
        btnProcess.disabled = false;
        btnRescan.disabled = false;
      }
      btnScan.disabled = false;
    });

    btnProcess.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnProcess.disabled = true;
      setMessage('Processing…', false);
      try {
        const data = await api('POST', '/api/process', {
          index: currentIndex,
          to_grayscale: document.getElementById('optGrayscale').checked
        });
        setSpread(data.index, data.url + '?t=' + Date.now());
        setMessage('Cleaned image ready. Rotate 180° if upside down, then Approve or Rescan.', false);
        btnApprove.disabled = false;
      } catch (e) {
        setMessage(e.message, true);
      }
      btnProcess.disabled = false;
    });

    btnRotate180.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnRotate180.disabled = true;
      setMessage('Rotating…', false);
      try {
        const data = await api('POST', '/api/process/rotate-180', { index: currentIndex });
        const img = document.getElementById('cleanedImg');
        if (img) img.src = data.url + '?t=' + Date.now();
        setMessage('Image rotated 180°. Approve or rotate again if needed.', false);
      } catch (e) {
        setMessage(e.message, true);
      }
      btnRotate180.disabled = false;
    });

    btnDeskew.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnDeskew.disabled = true;
      setMessage('Deskewing…', false);
      try {
        const data = await api('POST', '/api/process/deskew', { index: currentIndex });
        const img = document.getElementById('cleanedImg');
        if (img) img.src = data.url + '?t=' + Date.now();
        setMessage('Deskew applied. Approve or adjust further if needed.', false);
      } catch (e) {
        setMessage(e.message, true);
      }
      btnDeskew.disabled = false;
    });

    btnCropBorders.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnCropBorders.disabled = true;
      setMessage('Cropping borders…', false);
      try {
        const data = await api('POST', '/api/process/crop-borders', { index: currentIndex });
        const img = document.getElementById('cleanedImg');
        if (img) img.src = data.url + '?t=' + Date.now();
        setMessage('Borders cropped. Approve or adjust further if needed.', false);
      } catch (e) {
        setMessage(e.message, true);
      }
      btnCropBorders.disabled = false;
    });

    btnOCR.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnOCR.disabled = true;
      setMessage('Running OCR…', false);
      try {
        await api('POST', '/api/ocr', { index: currentIndex });
        setMessage('OCR complete. Switching to Compare tab…', false);
        btnOCR.disabled = false;
        btnBatchOCR.disabled = false;
        // Switch to Compare tab
        const compareTabBtn = document.querySelector('.tab-btn[data-tab="compare"]');
        if (compareTabBtn) {
          compareTabBtn.click();
          // Wait a bit for tab to initialize, then find and load the current spread
          setTimeout(async () => {
            if (compareIndices === null) {
              try {
                const data = await api('GET', '/api/cleaned/list', null);
                compareIndices = data.indices || [];
              } catch (e) {
                compareIndices = [];
              }
            }
            if (compareIndices.length > 0) {
              const pos = compareIndices.indexOf(currentIndex);
              if (pos >= 0) {
                comparePosition = pos;
                await loadCompareSpread(currentIndex);
                updateCompareToolbar();
              } else {
                // If current index not in list, refresh list and try again
                const data = await api('GET', '/api/cleaned/list', null);
                compareIndices = data.indices || [];
                if (compareIndices.length > 0) {
                  const pos = compareIndices.indexOf(currentIndex);
                  if (pos >= 0) {
                    comparePosition = pos;
                    await loadCompareSpread(currentIndex);
                    updateCompareToolbar();
                  }
                }
              }
            }
          }, 100);
        }
      } catch (e) {
        setMessage(e.message, true);
        btnOCR.disabled = false;
        btnBatchOCR.disabled = false;
      }
    });

    btnBatchOCR.addEventListener('click', async () => {
      if (currentIndex == null) return;
      let indices = [];
      try {
        const data = await api('GET', '/api/cleaned/list', null);
        indices = data.indices || [];
      } catch (e) {
        setMessage('Could not load cleaned list: ' + e.message, true);
        return;
      }
      const indicesToRun = indices.filter(i => i >= currentIndex);
      if (indicesToRun.length === 0) {
        setMessage('No spreads to the right with cleaned images.', false);
        return;
      }
      batchStopRequested = false;
      batchRunning = true;
      btnOCR.disabled = true;
      btnBatchOCR.disabled = true;
      btnStopBatch.disabled = false;
      let stopped = false;
      let lastIndex = null;
      for (let i = 0; i < indicesToRun.length; i++) {
        const idx = indicesToRun[i];
        lastIndex = idx;
        setMessage('OCR spread ' + idx + ' (' + (i + 1) + '/' + indicesToRun.length + ')…', false);
        try {
          await api('POST', '/api/ocr', { index: idx });
        } catch (e) {
          setMessage('OCR failed at spread ' + idx + ': ' + e.message, true);
          stopped = true;
          break;
        }
        if (batchStopRequested) {
          setMessage('Batch stopped after spread ' + idx + '.', false);
          stopped = true;
          break;
        }
      }
      batchRunning = false;
      batchStopRequested = false;
      btnStopBatch.disabled = true;
      const img = document.getElementById('cleanedImg');
      const hasCleaned = !!img && !!img.src;
      btnOCR.disabled = !hasCleaned;
      btnBatchOCR.disabled = !hasCleaned;
      if (!stopped) {
        setMessage('Batch complete. Switching to Compare tab…', false);
        const compareTabBtn = document.querySelector('.tab-btn[data-tab="compare"]');
        if (compareTabBtn) {
          compareTabBtn.click();
          setTimeout(async () => {
            if (compareIndices === null) {
              try {
                const data = await api('GET', '/api/cleaned/list', null);
                compareIndices = data.indices || [];
              } catch (e) {
                compareIndices = [];
              }
            }
            if (compareIndices.length > 0) {
              const pos = lastIndex != null ? compareIndices.indexOf(lastIndex) : 0;
              comparePosition = pos >= 0 ? pos : compareIndices.length - 1;
              await loadCompareSpread(compareIndices[comparePosition]);
              updateCompareToolbar();
            }
          }, 100);
        }
      }
    });

    btnStopBatch.addEventListener('click', () => {
      batchStopRequested = true;
    });

    btnRescan.addEventListener('click', async () => {
      if (currentIndex == null) return;
      btnRescan.disabled = true;
      setMessage('Opening scan dialog to replace spread ' + currentIndex + '…', false);
      try {
        const data = await api('POST', '/api/scan', { rescan_for_index: currentIndex });
        setSpread(data.index, null);
        setMessage('Rescanned. Click Process to clean again.', false);
        btnProcess.disabled = false;
        btnApprove.disabled = true;
      } catch (e) {
        setMessage(e.message, true);
      }
      btnRescan.disabled = false;
    });

    btnApprove.addEventListener('click', () => {
      setMessage('Spread ' + currentIndex + ' approved. (Phase 2 will add Run OCR.)', false);
    });

    function updateSpreadInfo(nextN) {
      if (currentIndex !== null) {
        spreadInfo.textContent = 'Spread ' + currentIndex + (nextN ? ' (next new scan will be ' + nextN + ')' : '');
      } else {
        spreadInfo.textContent = nextN ? 'Next spread will be ' + nextN + '. Click Scan to capture.' : 'No spread yet. Click Scan to capture the first spread.';
      }
    }

    btnSetIndex.addEventListener('click', async () => {
      const n = parseInt(nextIndexInput.value, 10);
      if (isNaN(n) || n < 1) {
        setMessage('Enter a number >= 1.', true);
        return;
      }
      try {
        const st = await api('PUT', '/api/state', { next_scan_index: n });
        nextIndexInput.value = st.next_scan_index;
        updateSpreadInfo(st.next_scan_index);
        setMessage('Next spread number set to ' + st.next_scan_index + '.', false);
      } catch (e) {
        setMessage(e.message, true);
      }
    });

    // --- Compare (OCR) tab ---
    let compareIndices = null;
    let comparePosition = null;
    let compareBatchRunning = false;
    const btnComparePrev = document.getElementById('btnComparePrev');
    const btnCompareNext = document.getElementById('btnCompareNext');
    const btnCompareOCR = document.getElementById('btnCompareOCR');
    const btnCompareBatchOCR = document.getElementById('btnCompareBatchOCR');
    const btnCompareStopBatch = document.getElementById('btnCompareStopBatch');
    const compareSpreadLabel = document.getElementById('compareSpreadLabel');
    const compareImageBox = document.getElementById('compareImageBox');
    const compareMdBox = document.getElementById('compareMdBox');
    const compareCheckedBox = document.getElementById('compareChecked');

    function getCompareCheckedSet() {
      try {
        const raw = localStorage.getItem(STORAGE_KEYS.compareChecked);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
      } catch (e) { return []; }
    }

    function saveCompareCheckedSet(ids) {
      localStorage.setItem(STORAGE_KEYS.compareChecked, JSON.stringify(ids));
    }

    function updateCompareCheckedCheckbox() {
      if (!compareIndices || compareIndices.length === 0) return;
      const currentId = compareIndices[comparePosition];
      compareCheckedBox.checked = getCompareCheckedSet().indexOf(currentId) >= 0;
    }

    function padIndex(n) {
      return String(n).padStart(3, '0');
    }

    async function loadCompareSpread(index) {
      const filename = 'image_' + padIndex(index) + '.png';
      compareImageBox.innerHTML = '<img src="/api/serve/cleaned/' + filename + '?t=' + Date.now() + '" alt="Spread ' + index + '">';
      try {
        const r = await fetch('/api/texts/' + index);
        const text = await r.text();
        if (r.ok) {
          compareMdBox.textContent = text;
          compareMdBox.classList.remove('no-ocr');
        } else {
          compareMdBox.textContent = 'No OCR result for this spread.';
          compareMdBox.classList.add('no-ocr');
        }
      } catch (e) {
        compareMdBox.textContent = 'No OCR result for this spread.';
        compareMdBox.classList.add('no-ocr');
      }
    }

    function updateCompareToolbar() {
      if (!compareIndices || compareIndices.length === 0) {
        btnCompareOCR.disabled = true;
        btnCompareBatchOCR.disabled = true;
        btnCompareStopBatch.disabled = true;
        return;
      }
      const n = compareIndices[comparePosition];
      const m = compareIndices.length;
      compareSpreadLabel.textContent = 'Spread ' + n + ' of ' + m;
      btnComparePrev.disabled = comparePosition === 0;
      btnCompareNext.disabled = comparePosition === compareIndices.length - 1;
      if (compareBatchRunning) {
        btnCompareOCR.disabled = true;
        btnCompareBatchOCR.disabled = true;
        btnCompareStopBatch.disabled = false;
      } else {
        btnCompareOCR.disabled = false;
        btnCompareBatchOCR.disabled = false;
        btnCompareStopBatch.disabled = true;
      }
      updateCompareCheckedCheckbox();
    }

    async function initCompareTab() {
      if (compareIndices === null) {
        try {
          const data = await api('GET', '/api/cleaned/list', null);
          compareIndices = data.indices || [];
        } catch (e) {
          compareIndices = [];
        }
      }
      if (compareIndices.length === 0) {
        compareSpreadLabel.textContent = 'No spreads';
        compareImageBox.innerHTML = '<p>No cleaned images. Use Scan tab first.</p>';
        compareMdBox.textContent = '';
        btnComparePrev.disabled = true;
        btnCompareNext.disabled = true;
        btnCompareOCR.disabled = true;
        btnCompareBatchOCR.disabled = true;
        btnCompareStopBatch.disabled = true;
        return;
      }
      if (comparePosition === null) {
        const checkedSet = getCompareCheckedSet();
        let firstUnchecked = null;
        for (let i = 0; i < compareIndices.length; i++) {
          if (checkedSet.indexOf(compareIndices[i]) < 0) {
            firstUnchecked = i;
            break;
          }
        }
        comparePosition = firstUnchecked !== null ? firstUnchecked : 0;
      }
      await loadCompareSpread(compareIndices[comparePosition]);
      updateCompareToolbar();
    }

    btnComparePrev.addEventListener('click', () => {
      if (compareIndices === null || comparePosition <= 0) return;
      comparePosition--;
      loadCompareSpread(compareIndices[comparePosition]);
      updateCompareToolbar();
    });

    btnCompareNext.addEventListener('click', () => {
      if (compareIndices === null || comparePosition >= compareIndices.length - 1) return;
      comparePosition++;
      loadCompareSpread(compareIndices[comparePosition]);
      updateCompareToolbar();
    });

    compareCheckedBox.addEventListener('change', () => {
      if (compareIndices === null || compareIndices.length === 0) return;
      const currentId = compareIndices[comparePosition];
      const set = getCompareCheckedSet();
      const idx = set.indexOf(currentId);
      if (compareCheckedBox.checked) {
        if (idx < 0) set.push(currentId);
      } else {
        if (idx >= 0) set.splice(idx, 1);
      }
      saveCompareCheckedSet(set);
    });

    btnCompareOCR.addEventListener('click', async () => {
      if (compareIndices === null || comparePosition === null || compareIndices.length === 0) return;
      const currentSpreadIndex = compareIndices[comparePosition];
      btnCompareOCR.disabled = true;
      compareMdBox.textContent = 'Running OCR…';
      compareMdBox.classList.add('no-ocr');
      try {
        await api('POST', '/api/ocr', { index: currentSpreadIndex });
        // Reload the markdown for the current spread
        await loadCompareSpread(currentSpreadIndex);
      } catch (e) {
        compareMdBox.textContent = 'OCR failed: ' + e.message;
        compareMdBox.classList.add('no-ocr');
      }
      btnCompareOCR.disabled = false;
    });

    btnCompareBatchOCR.addEventListener('click', async () => {
      if (compareIndices === null || comparePosition === null || compareIndices.length === 0) return;
      const currentSpreadIndex = compareIndices[comparePosition];
      let indices = [];
      try {
        const data = await api('GET', '/api/cleaned/list', null);
        indices = data.indices || [];
      } catch (e) {
        compareMdBox.textContent = 'Could not load cleaned list: ' + e.message;
        compareMdBox.classList.add('no-ocr');
        return;
      }
      const pos = indices.indexOf(currentSpreadIndex);
      if (pos < 0) {
        compareMdBox.textContent = 'No cleaned image for current spread.';
        compareMdBox.classList.add('no-ocr');
        return;
      }
      const indicesToRun = indices.slice(pos);
      batchStopRequested = false;
      compareBatchRunning = true;
      updateCompareToolbar();
      let stopped = false;
      const currentViewIndex = compareIndices[comparePosition];
      for (let i = 0; i < indicesToRun.length; i++) {
        const idx = indicesToRun[i];
        compareMdBox.textContent = 'OCR spread ' + idx + ' (' + (i + 1) + '/' + indicesToRun.length + ')…';
        compareMdBox.classList.add('no-ocr');
        try {
          await api('POST', '/api/ocr', { index: idx });
        } catch (e) {
          const isMissing = e.message && e.message.indexOf('No cleaned image') >= 0;
          compareMdBox.textContent = isMissing ? 'No cleaned image for spread ' + idx + '.' : 'OCR failed at spread ' + idx + ': ' + e.message;
          compareMdBox.classList.add('no-ocr');
          stopped = true;
          break;
        }
        if (batchStopRequested) {
          compareMdBox.textContent = 'Batch stopped after spread ' + idx + '.';
          compareMdBox.classList.add('no-ocr');
          stopped = true;
          break;
        }
      }
      compareBatchRunning = false;
      batchStopRequested = false;
      updateCompareToolbar();
      await loadCompareSpread(currentViewIndex);
      updateCompareToolbar();
    });

    btnCompareStopBatch.addEventListener('click', () => {
      batchStopRequested = true;
    });

    (async function init() {
      // Load checkbox states from localStorage
      loadCheckboxStates();
      
      try {
        const st = await api('GET', '/api/state', null);
        nextIndexInput.value = st.next_scan_index;
        updateSpreadInfo(st.next_scan_index);
      } catch (e) {
        spreadInfo.textContent = 'No spread yet. Click Scan to capture the first spread.';
      }
    })();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
