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
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# Project root (where scan_sample.py and work/ live)
PROJECT_ROOT = Path(__file__).resolve().parent
WORK_DIR = PROJECT_ROOT / "work"
SCANS_DIR = WORK_DIR / "scans"
CLEANED_DIR = WORK_DIR / "cleaned"
NEXT_INDEX_FILE = WORK_DIR / "next_index.txt"

# Spread index: 3-digit zero-padded
INDEX_DIGITS = 3


def _ensure_work_dirs() -> None:
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    CLEANED_DIR.mkdir(parents=True, exist_ok=True)


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
  <title>Scan pipeline – Phase 1</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 900px; margin: 1rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; }
    .step { margin: 1rem 0; }
    button { padding: 0.5rem 1rem; margin-right: 0.5rem; cursor: pointer; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    #spreadInfo { font-weight: 600; margin-bottom: 0.5rem; }
    #imageBox { margin: 1rem 0; min-height: 200px; }
    #imageBox img { max-width: 100%; border: 1px solid #ccc; }
    .error { color: #c00; }
    .success { color: #060; }
    .setIndex .hint { color: #666; font-size: 0.9rem; margin-left: 0.5rem; }
  </style>
</head>
<body>
  <h1>Scan pipeline – Phase 1</h1>
  <p id="spreadInfo">Loading…</p>

  <div class="step setIndex">
    <label for="nextIndexInput">Next spread number:</label>
    <input type="number" id="nextIndexInput" min="1" value="1" style="width: 4rem; margin: 0 0.25rem;">
    <button id="btnSetIndex">Set</button>
    <span class="hint">(Restart from 1 or continue from N; next Scan will save as image_NNN.png)</span>
  </div>

  <div class="step">
    <label><input type="checkbox" id="optGrayscale" checked> Convert to grayscale</label>
  </div>
  <div class="step">
    <button id="btnScan">Scan</button>
    <button id="btnProcess" disabled>Process</button>
    <button id="btnRotate180" disabled>Rotate 180°</button>
    <button id="btnDeskew" disabled>Deskew</button>
    <button id="btnCropBorders" disabled>Crop borders</button>
    <button id="btnRescan" disabled>Rescan</button>
    <button id="btnApprove" disabled>Approve</button>
  </div>

  <div id="imageBox"></div>
  <p id="message"></p>

  <script>
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
        setMessage('Scanned as spread ' + data.index + '. Click Process to clean the image.', false);
        btnProcess.disabled = false;
        btnRescan.disabled = false;
      } catch (e) {
        setMessage(e.message, true);
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

    (async function init() {
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
