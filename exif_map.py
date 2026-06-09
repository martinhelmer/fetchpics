#!/usr/bin/env python3
"""
exif_map — identify camera hardware and map to photographers
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict


def iter_media(directory):
    """Iterate over all media files recursively."""
    for root, _, files in os.walk(directory, followlinks=False):
        for fname in files:
            path = os.path.join(root, fname)
            _, ext = os.path.splitext(path)
            if ext.lower() in ('.jpg', '.jpeg', '.png', '.heic', '.gif', '.bmp', '.webp', '.tiff', '.mp4', '.mov'):
                yield path


def get_camera_info(path):
    """Get Make and Model from file."""
    try:
        result = subprocess.run(
            ['exiftool', '-json', '-Make', '-Model', path],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)[0]
        make = data.get('Make', 'unknown')
        model = data.get('Model', 'unknown')
        return (make, model)
    except Exception:
        return ('unknown', 'unknown')


def scan_hardware(directory):
    """Scan directory and identify unique hardware."""
    hardware_map = defaultdict(list)
    total = 0

    for path in iter_media(directory):
        total += 1
        make, model = get_camera_info(path)
        key = f"{make} {model}"
        hardware_map[key].append(os.path.relpath(path, directory))

    # Sort by count
    devices = sorted(hardware_map.items(), key=lambda x: -len(x[1]))

    print(f"Found {total} media files across {len(devices)} unique devices:\n")

    for i, (device, files) in enumerate(devices, 1):
        count = len(files)
        pct = 100 * count / total
        print(f"{i}. {device}")
        print(f"   Files: {count} ({pct:.1f}%)")
        print(f"   Sample: {files[0]}")
        print()

    return {device: count for device, (_, files) in [(d, (None, f)) for d, f in devices] for count in [len(files)]}


def main():
    parser = argparse.ArgumentParser(description="Map camera hardware in photo library")
    parser.add_argument("src_dir", help="Source directory to scan")
    args = parser.parse_args()

    src_dir = os.path.abspath(os.path.expanduser(args.src_dir))
    if not os.path.isdir(src_dir):
        print(f"Error: {src_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    scan_hardware(src_dir)


if __name__ == "__main__":
    main()
