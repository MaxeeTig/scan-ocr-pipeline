#!/usr/bin/env python3
"""
Image preprocessing for the scan pipeline: EXIF orientation, OSD rotation, format normalization.

Reuses logic from llm_ocr_to_markdown for consistency. Used by both the CLI OCR script
and the web app (Phase 1 process step).
"""

from __future__ import annotations

from pathlib import Path


def process_image(
    input_path: str | Path,
    output_path: str | Path,
    fix_orientation: bool = True,
    max_size_px: int = 4096,
    rotate_180: bool = False,
    to_grayscale: bool = False,
) -> bool:
    """
    Load image from input_path, apply EXIF + OSD orientation, optional 180° flip,
    optional grayscale, normalize format, and save to output_path as PNG.

    - rotate_180: if True, rotate image 180° (for when OSD misses upside-down text).
    - to_grayscale: if True, convert to grayscale to reduce size and colors.

    Returns True if successful, False if PIL is not available or processing failed.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False

    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.is_file():
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(input_path) as img:
            if fix_orientation:
                img = ImageOps.exif_transpose(img)
            if fix_orientation:
                try:
                    import pytesseract
                    osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
                    if osd.get("rotate", 0) != 0:
                        img = img.rotate(osd["rotate"], expand=True, resample=Image.Resampling.BICUBIC)
                except Exception:
                    pass

            if rotate_180:
                img = img.rotate(180, expand=False, resample=Image.Resampling.BICUBIC)

            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            if to_grayscale:
                img = img.convert("L")

            w, h = img.size
            if max_size_px and (w > max_size_px or h > max_size_px):
                ratio = min(max_size_px / w, max_size_px / h)
                new_size = (int(w * ratio), int(h * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            img.save(output_path, format="PNG", optimize=True)
        return True
    except Exception:
        return False


def rotate_image_180(image_path: str | Path) -> bool:
    """
    Rotate the image at image_path 180° in place (overwrites the file).
    Returns True if successful.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    path = Path(image_path)
    if not path.is_file():
        return False
    try:
        with Image.open(path) as img:
            out = img.rotate(180, expand=False, resample=Image.Resampling.BICUBIC)
        out.save(path, format="PNG", optimize=True)
        return True
    except Exception:
        return False


def _projection_variance(im) -> float:
    """Horizontal projection (row sums) variance; higher when text lines are horizontal."""
    if im.mode != "L":
        im = im.convert("L")
    w, h = im.size
    row_sums = []
    for y in range(h):
        row_sums.append(sum(im.getpixel((x, y)) for x in range(0, w, max(1, w // 200))))
    if len(row_sums) < 2:
        return 0.0
    mean = sum(row_sums) / len(row_sums)
    return sum((s - mean) ** 2 for s in row_sums) / len(row_sums)


def deskew_image(
    image_path: str | Path,
    angle_range: float = 4.0,
    angle_step: float = 0.5,
    min_angle: float = 0.2,
    max_size_analyze: int = 600,
) -> bool:
    """
    Detect skew (small rotation) and correct it in place. Uses projection-profile method:
    try angles in [-angle_range, +angle_range], pick the one that maximizes horizontal
    projection variance (text lines horizontal = stronger variance). Rotate image by
    that angle and overwrite the file.

    - angle_range: search ± this many degrees (default 4).
    - angle_step: step in degrees (default 0.5).
    - min_angle: do not rotate if |best_angle| < this (avoid unnecessary changes).
    - max_size_analyze: downsample to this max dimension for angle search (faster).

    Returns True if successful.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    path = Path(image_path)
    if not path.is_file():
        return False
    try:
        with Image.open(path) as img:
            orig = img.convert("L")
        w, h = orig.size
        if max(w, h) > max_size_analyze:
            r = max_size_analyze / max(w, h)
            small = orig.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS)
        else:
            small = orig
        best_angle = 0.0
        best_var = _projection_variance(small)
        a = -angle_range
        while a <= angle_range:
            rotated = small.rotate(a, expand=False, resample=Image.Resampling.BICUBIC)
            v = _projection_variance(rotated)
            if v > best_var:
                best_var = v
                best_angle = a
            a += angle_step
        if abs(best_angle) < min_angle:
            return True
        with Image.open(path) as img:
            out = img.rotate(best_angle, expand=True, resample=Image.Resampling.BICUBIC)
        out.save(path, format="PNG", optimize=True)
        return True
    except Exception:
        return False
