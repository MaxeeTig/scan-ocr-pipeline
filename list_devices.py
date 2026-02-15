#!/usr/bin/env python3
"""
List scanning and printing devices on Windows.

Uses:
- WIA (Windows Image Acquisition) for scanners/cameras
- win32print for printers

Run on Windows with: python list_devices.py
"""

from __future__ import annotations

import sys


def list_printers() -> list[dict]:
    """List all printers via Windows Print Spooler API."""
    try:
        import win32print
    except ImportError:
        return []  # pywin32 not installed

    result = []
    try:
        # Level 1 returns (flags, description, name, comment) per printer
        printers = win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
            None,
            1,
        )
        for p in printers:
            flags, desc, name, comment = p
            result.append({
                "name": name,
                "description": desc or "",
                "comment": comment or "",
            })
    except Exception as e:
        result.append({"error": str(e)})
    return result


def list_scanners() -> list[dict]:
    """List WIA imaging devices (scanners, cameras) via COM."""
    try:
        import win32com.client
    except ImportError:
        return []

    result = []
    try:
        # WIA Automation: DeviceManager.DeviceInfos (scripting-friendly collection)
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        dev_infos = dm.DeviceInfos
        n = dev_infos.Count
        for i in range(1, n + 1):
            try:
                di = dev_infos.Item(i)
                result.append(_device_info_to_dict(di))
            except Exception as e:
                result.append({"index": i, "error": str(e)})
    except Exception as e:
        result.append({"error": str(e)})
    return result


def _device_info_to_dict(dev_info) -> dict:
    """Extract name, id, and type from WIA IWiaPropertyStorage."""
    out = {}
    try:
        out["name"] = _get_prop(dev_info, "Name") or _get_prop(dev_info, 2) or "Unknown"
    except Exception:
        out["name"] = "Unknown"
    try:
        out["id"] = _get_prop(dev_info, "Device ID") or _get_prop(dev_info, 3) or ""
    except Exception:
        out["id"] = ""
    try:
        out["description"] = _get_prop(dev_info, "Description") or _get_prop(dev_info, 4) or ""
    except Exception:
        out["description"] = ""
    return out


def _get_prop(dev_info, prop_name_or_id):
    try:
        props = dev_info.Properties
        if isinstance(prop_name_or_id, int):
            return props(prop_name_or_id).Value
        return props(prop_name_or_id).Value
    except Exception:
        return None


def main() -> None:
    if sys.platform != "win32":
        print("This script is for Windows only (WIA, win32print).")
        sys.exit(1)

    print("=" * 60)
    print("PRINTERS")
    print("=" * 60)
    printers = list_printers()
    if not printers:
        print("(none found or win32print not available)")
    else:
        for i, p in enumerate(printers, 1):
            if "error" in p:
                print(f"  [{i}] Error: {p['error']}")
            else:
                print(f"  [{i}] {p['name']}")
                if p.get("description"):
                    print(f"       {p['description']}")
                if p.get("comment"):
                    print(f"       Comment: {p['comment']}")

    print()
    print("=" * 60)
    print("SCANNERS / IMAGING DEVICES (WIA)")
    print("=" * 60)
    scanners = list_scanners()
    if not scanners:
        print("(no WIA devices found — scanner may use a different driver or be offline)")
    else:
        for i, s in enumerate(scanners, 1):
            if "error" in s:
                print(f"  [{i}] Error: {s['error']}")
            else:
                print(f"  [{i}] {s.get('name', 'Unknown')}")
                if s.get("description"):
                    print(f"       {s['description']}")
                if s.get("id"):
                    print(f"       ID: {s['id']}")

    print()
    return None


if __name__ == "__main__":
    main()
