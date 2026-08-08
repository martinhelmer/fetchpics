#!/usr/bin/env python3
"""
organize_by_date — organize photos/videos into YYYY/MM/ directory structure

Uses exiftool to read metadata, filenames, and paths to determine capture date,
then organizes files into a YYYY/MM/ folder hierarchy.

Usage:
  organize_by_date scan <src> <dst>         Show detected dates for all files (batched output)
  organize_by_date organize <src> <dst>     Move files to YYYY/MM/ structure in dst
  organize_by_date organize --copy <src> <dst>  Copy files instead of moving

Options:
  --copy              Copy files instead of moving them (organize only)

Date detection (in priority order):
  1. EXIF DateTimeOriginal or DateTime
  2. Filename patterns: YYYYMMDD, YYYY-MM-DD, YYYY_MM_DD, YYYYMM, YYYY-MM, YYYY_MM
  3. Path patterns: YYYY/MM or YYYY-MM in directory names
  4. File modification time (fallback)
"""

import argparse
import os
import shutil
import sys
import re
import subprocess
from datetime import datetime

PHOTO_EXTENSIONS = set([
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2", ".dng", ".pef", ".srw", ".raf",
])

VIDEO_EXTENSIONS = set([
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv",
    ".flv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg", ".m2v",
])

ALL_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def is_media(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in ALL_EXTENSIONS


def iter_media(directory):
    for root, _, files in os.walk(directory, followlinks=False):
        for fname in files:
            path = os.path.join(root, fname)
            if is_media(path):
                yield path


_exif_cache = {}

def batch_read_exif_dates(paths):
    """Batch-read EXIF dates in chunks of 100. Results cached in _exif_cache."""
    uncached = [p for p in paths if p not in _exif_cache]
    if not uncached:
        return

    for i in range(0, len(uncached), 100):
        chunk = uncached[i:i+100]
        try:
            cmd = ["exiftool", "-fast", "-DateTimeOriginal", "-DateTime", "-csv"] + chunk
            output = subprocess.check_output(cmd, stderr=subprocess.PIPE).decode('utf-8', errors='ignore')

            lines = output.strip().split('\n')
            if len(lines) > 1:
                for idx, line in enumerate(lines[1:]):
                    if idx < len(chunk):
                        parts = line.split(',')
                        path = chunk[idx]
                        date_str = (parts[1] if len(parts) > 1 and parts[1] else
                                    parts[2] if len(parts) > 2 and parts[2] else None)
                        _exif_cache[path] = _parse_exif_date(date_str) if date_str else None
        except (OSError, subprocess.CalledProcessError):
            for p in chunk:
                _exif_cache[p] = None

def _parse_exif_date(value):
    """Parse EXIF date string. Returns (year, month) or None."""
    for fmt in ["%Y:%m:%d", "%Y-%m-%d", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(value[:10], fmt[:10])
            return (dt.year, dt.month)
        except (ValueError, IndexError):
            pass
    return None

def read_exif_date(path):
    """Return cached EXIF date. Call batch_read_exif_dates() first to populate cache."""
    return _exif_cache.get(path)


def parse_filename_date(filename):
    """
    Extract date from filename patterns.
    Handles: YYYYMMDD, YYYY-MM-DD, YYYY_MM_DD, YYYYMM, YYYY-MM, etc.
    Returns (year, month) or None.
    """
    name_without_ext = os.path.splitext(filename)[0]

    # Pattern: YYYYMMDD
    match = re.search(r'\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b', name_without_ext)
    if match:
        return (int(match.group(1)), int(match.group(2)))

    # Pattern: YYYY-MM-DD or YYYY_MM_DD or similar
    match = re.search(r'\b(20\d{2})[-_](0[1-9]|1[0-2])[-_](0[1-9]|[12]\d|3[01])\b', name_without_ext)
    if match:
        return (int(match.group(1)), int(match.group(2)))

    # Pattern: YYYYMM
    match = re.search(r'\b(20\d{2})(0[1-9]|1[0-2])\b', name_without_ext)
    if match:
        return (int(match.group(1)), int(match.group(2)))

    # Pattern: YYYY-MM or YYYY_MM
    match = re.search(r'\b(20\d{2})[-_](0[1-9]|1[0-2])\b', name_without_ext)
    if match:
        return (int(match.group(1)), int(match.group(2)))

    return None


def parse_path_date(full_path):
    """
    Extract date from directory path patterns.
    Looks for YYYY, YYYY/MM, YYYY-MM, etc. in path components.
    Returns (year, month) or just (year, None).
    """
    parts = full_path.replace('\\', '/').split('/')

    for i, part in enumerate(parts):
        # Try YYYYMM pattern
        match = re.search(r'\b(20\d{2})(0[1-9]|1[0-2])\b', part)
        if match:
            return (int(match.group(1)), int(match.group(2)))

        # Try YYYY pattern
        match = re.search(r'\b(20\d{2})\b', part)
        if match:
            year = int(match.group(1))
            # Check next path component for month
            if i + 1 < len(parts):
                month_match = re.search(r'\b(0[1-9]|1[0-2])\b', parts[i + 1])
                if month_match:
                    return (year, int(month_match.group(1)))
            return (year, None)

    return None


def detect_date(path):
    """
    Detect capture date from EXIF, filename, path, or file mtime.
    Returns (year, month) tuple or None if detection fails.
    """
    # Try EXIF first (most reliable for photos)
    result = read_exif_date(path)
    if result:
        return result

    # Try filename patterns
    filename = os.path.basename(path)
    result = parse_filename_date(filename)
    if result:
        return result

    # Try path patterns
    result = parse_path_date(path)
    if result and result[1]:  # Need month for organizing
        return result

    # Fall back to file modification time
    try:
        mtime = os.path.getmtime(path)
        dt = datetime.fromtimestamp(mtime)
        return (dt.year, dt.month)
    except Exception:
        pass

    return None


def cmd_scan(args):
    """Scan directory and show detected dates with target paths."""
    src = os.path.abspath(os.path.expanduser(args.src))
    dst = os.path.abspath(os.path.expanduser(args.dst))

    if not os.path.isdir(src):
        print("Error: {0} is not a directory".format(src), file=sys.stderr)
        sys.exit(1)

    detected = 0
    failed = 0

    print("Scanning {0}...".format(src))
    media_paths = list(iter_media(src))
    print("Total files: {0}".format(len(media_paths)))

    batch_size = 100
    total_batches = (len(media_paths) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(media_paths), batch_size):
        batch = media_paths[batch_idx:batch_idx+batch_size]
        batch_num = batch_idx // batch_size + 1

        print("\nBatch {0}/{1}: caching EXIF...".format(batch_num, total_batches), end="")
        sys.stdout.flush()
        batch_read_exif_dates(batch)
        print(" scanning...")
        sys.stdout.flush()

        for path in batch:
            date_info = detect_date(path)
            if date_info:
                year, month = date_info
                print("  {0:04d}-{1:02d}  {2}".format(year, month, os.path.basename(path)))
                detected += 1
            else:
                print("  ????-??  {0}".format(os.path.basename(path)))
                failed += 1
            sys.stdout.flush()

    print("\nTotal: Detected {0} files, Failed on {1}.".format(detected, failed))


def cmd_organize(args):
    """Move or copy files into YYYY/MM/ structure, processing in batches of 100."""
    src = os.path.abspath(os.path.expanduser(args.src))
    dst = os.path.abspath(os.path.expanduser(args.dst))

    if not os.path.isdir(src):
        print("Error: {0} is not a directory".format(src), file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(dst):
        os.makedirs(dst)

    copy_mode = args.copy
    organized = 0
    failed = 0

    media_paths = list(iter_media(src))
    print("Total files: {0}".format(len(media_paths)))

    batch_size = 100
    total_batches = (len(media_paths) + batch_size - 1) // batch_size

    try:
        for batch_idx in range(0, len(media_paths), batch_size):
            batch = media_paths[batch_idx:batch_idx+batch_size]
            batch_num = batch_idx // batch_size + 1

            print("\nBatch {0}/{1}: caching EXIF...".format(batch_num, total_batches), end="")
            sys.stdout.flush()
            batch_read_exif_dates(batch)
            print(" organizing...")
            sys.stdout.flush()

            for path in batch:
                try:
                    date_info = detect_date(path)
                    if not date_info or not date_info[1]:
                        print("  SKIP    {0} (no date detected)".format(os.path.basename(path)))
                        failed += 1
                        continue

                    year, month = date_info
                    dest_dir = os.path.join(dst, "{0:04d}".format(year), "{0:02d}".format(month))
                    if not os.path.exists(dest_dir):
                        os.makedirs(dest_dir)

                    dest_path = os.path.join(dest_dir, os.path.basename(path))

                    # Skip if already in correct position
                    if os.path.normpath(path) == os.path.normpath(dest_path):
                        organized += 1
                        print("  {0:04d}-{1:02d}  {2} (already organized)".format(year, month, os.path.basename(path)))
                        continue

                    # Handle collisions
                    if os.path.exists(dest_path):
                        base, ext = os.path.splitext(os.path.basename(path))
                        counter = 1
                        while os.path.exists(dest_path):
                            dest_path = os.path.join(dest_dir, "{0}_{1}{2}".format(base, counter, ext))
                            counter += 1

                    if copy_mode:
                        copy_ok = False
                        try:
                            shutil.copy2(path, dest_path)
                            copy_ok = True
                        except OSError:
                            try:
                                shutil.copy(path, dest_path)
                                copy_ok = True
                            except OSError:
                                pass
                        if not copy_ok:
                            raise OSError("Failed to copy {0} to {1}".format(path, dest_path))
                    else:
                        shutil.move(path, dest_path)

                    organized += 1
                    print("  {0:04d}-{1:02d}  {2}".format(year, month, os.path.basename(path)))

                except Exception as e:
                    failed += 1
                    print("  ERROR   {0}: {1}".format(os.path.basename(path), str(e)), file=sys.stderr)
                sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n\nInterrupted! Restart to resume from the next batch.", file=sys.stderr)
        sys.exit(130)

    mode = "Copied" if copy_mode else "Moved"
    print("\n{0}: {1} files, Failed: {2}".format(mode, organized, failed))


def main():
    parser = argparse.ArgumentParser(
        description="organize_by_date — organize media into YYYY/MM/ hierarchy"
    )
    subparsers = parser.add_subparsers(dest="command")

    p_scan = subparsers.add_parser("scan", help="Scan and show detected dates")
    p_scan.add_argument("src", help="Source directory")
    p_scan.add_argument("dst", help="Target destination directory")

    p_organize = subparsers.add_parser("organize", help="Organize files into YYYY/MM/ structure")
    p_organize.add_argument("src", help="Source directory")
    p_organize.add_argument("dst", help="Destination directory")
    p_organize.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of moving"
    )

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "organize":
        cmd_organize(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
