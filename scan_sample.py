#!/usr/bin/env python3
"""
Sample scanning script (Windows WIA).

Shows the Windows scan dialog, acquires one image from the default
(or selected) scanner, and saves it to a file.

Usage:
  python scan_sample.py [output_path]
  python scan_sample.py --subprocess [output_path]   # run scan in subprocess (avoids "device busy" on 2nd scan)

  If output_path is omitted, saves to scan_output.png in the current directory.
"""

from __future__ import annotations

import gc
import subprocess
import sys
import time
from pathlib import Path


# WIA device type: 1 = Scanner (WiaDeviceTypeScanner)
WIA_DEVICE_TYPE_SCANNER = 1


def scan_to_file(output_path: str | Path) -> bool:
    """
    Show WIA acquire-image dialog, then save the result to output_path.

    Returns True if an image was acquired and saved, False if user cancelled
    or an error occurred.
    """
    try:
        import win32com.client
    except ImportError:
        print("Error: pywin32 is required. Run: pip install pywin32")
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dialog = None
    image = None
    try:
        dialog = win32com.client.Dispatch("WIA.CommonDialog")
        # ShowAcquireImage(DeviceType, Intent, Bias, FormatID, AlwaysSelectDevice, UseCommonUI, CancelError)
        # DeviceType=1 (Scanner); other params default → use common UI, no cancel error
        image = dialog.ShowAcquireImage(
            WIA_DEVICE_TYPE_SCANNER,
            0,  # UnspecifiedIntent
            0,  # MaximizeQuality (default)
            "{00000000-0000-0000-0000-000000000000}",  # default format
            False,  # AlwaysSelectDevice
            True,   # UseCommonUI
            False,  # CancelError → return Nothing instead of raising
        )
        if image is None:
            print("No image acquired (dialog cancelled or no device).")
            return False

        image.SaveFile(str(output_path.resolve()))
        print(f"Saved: {output_path.resolve()}")
        return True
    except Exception as e:
        print(f"Scan failed: {e}")
        return False
    finally:
        # Release WIA/COM references so the scanner is not left "busy" for the next scan
        image = None
        dialog = None
        gc.collect()
        time.sleep(0.3)


def main() -> None:
    if sys.platform != "win32":
        print("This script is for Windows only (WIA).")
        sys.exit(1)

    argv = [a for a in sys.argv[1:] if a]
    use_subprocess = "--subprocess" in argv
    if use_subprocess:
        argv.remove("--subprocess")
    out = Path(argv[0]) if argv else Path("scan_output.png")

    # Run in subprocess so COM/WIA is fully torn down after each scan (avoids "device busy")
    if use_subprocess:
        out_str = str(out.resolve())
        code = subprocess.run(
            [sys.executable, "-c", f"from scan_sample import scan_to_file; exit(0 if scan_to_file({out_str!r}) else 1)"],
            cwd=Path(__file__).resolve().parent,
        ).returncode
        sys.exit(code)
    else:
        ok = scan_to_file(out)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
