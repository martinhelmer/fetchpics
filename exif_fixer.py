#!/usr/bin/env python3
"""
exif_fixer — fix EXIF data: add photographer based on camera, add date from path/filename

Usage:
  exif_fixer set-photographer <src_dir>    Set photographer for all files based on camera
  exif_fixer set-dates <src_dir>           Set DateTimeOriginal from filename/path/other tags
  exif_fixer report <src_dir>              Show assignments preview
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import defaultdict


CAMERA_TO_PHOTOGRAPHER = {
    ('motorola', 'moto g pure'): 'Gabriel',
    ('samsung', 'SM-F707W'): 'Stephanie',
    ('samsung', 'SM-G973W'): 'Martin',
    ('samsung', 'SAMSUNG-SM-G890A'): 'Sebastian',
}


PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.gif', '.bmp', '.webp', '.tiff', '.tif'}


def iter_media(directory):
    """Iterate over all media files recursively."""
    for root, _, files in os.walk(directory, followlinks=False):
        for fname in files:
            path = os.path.join(root, fname)
            _, ext = os.path.splitext(path)
            if ext.lower() in PHOTO_EXTENSIONS:
                yield path


def iter_media_with_exif(directory):
    """Yields (path, exif_data) tuples; batch-reads EXIF once for efficiency."""
    paths = list(iter_media(directory))
    exif_cache = read_exif_batch(paths)
    for path in paths:
        yield path, exif_cache.get(path, {})


def get_photographer(make, model):
    """Map camera to photographer name."""
    key = (make.lower(), model)
    if key in CAMERA_TO_PHOTOGRAPHER:
        return CAMERA_TO_PHOTOGRAPHER[key]
    return None


def parse_datetime_from_filename(filename):
    """Extract datetime from filename patterns: YYYYMMDD_HHMMSS, IMG_YYYYMMDD_HHMMSS, etc."""
    name = os.path.splitext(filename)[0]

    patterns = [
        r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
        r'IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})',
    ]

    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            groups = match.groups()
            try:
                return f"{groups[0]}:{groups[1]}:{groups[2]} {groups[3]}:{groups[4]}:{groups[5]}"
            except (IndexError, ValueError):
                pass

    return None


def parse_datetime_from_path(full_path):
    """Extract datetime from path patterns: Day7 - 12 Aug, 2025/08/12, etc."""
    path_parts = full_path.replace('\\', '/').split('/')

    for part in path_parts:
        match = re.search(r'Day\d+\s*[-–]\s*(\d{2})\s+(\w+)', part)
        if match:
            day, month_name = match.groups()
            month_map = {
                'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
                'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
                'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
            }
            month = month_map.get(month_name.lower())
            if month:
                year = str(datetime.datetime.now().year)
                return f"{year}:{month}:{day} 12:00:00"

    return None


def read_exif_batch(paths):
    """Read EXIF data for multiple files in one exiftool call. Returns dict: path -> exif_data."""
    if not paths:
        return {}
    try:
        result = subprocess.run(
            ['exiftool', '-json', '-DateTimeOriginal', '-CreateDate', '-ModifyDate',
             '-FileModifyDate', '-Make', '-Model'] + list(paths),
            capture_output=True, text=True, check=True, timeout=60
        )
        data = json.loads(result.stdout)
        cache = {}
        for entry in data:
            path = entry.get('SourceFile')
            if path:
                cache[path] = {
                    'DateTimeOriginal': entry.get('DateTimeOriginal'),
                    'CreateDate': entry.get('CreateDate'),
                    'ModifyDate': entry.get('ModifyDate'),
                    'FileModifyDate': entry.get('FileModifyDate'),
                    'Make': entry.get('Make', '').lower(),
                    'Model': entry.get('Model', ''),
                }
        return cache
    except Exception:
        return {}


def detect_datetime_with_cache(path, exif_dates):
    """Detect datetime using pre-read EXIF data to avoid re-reading."""
    if exif_dates.get('DateTimeOriginal'):
        return exif_dates['DateTimeOriginal']

    datetime_from_filename = parse_datetime_from_filename(os.path.basename(path))
    if datetime_from_filename:
        return datetime_from_filename

    datetime_from_path = parse_datetime_from_path(path)
    if datetime_from_path:
        return datetime_from_path

    if exif_dates.get('CreateDate'):
        return exif_dates['CreateDate']

    if exif_dates.get('ModifyDate'):
        return exif_dates['ModifyDate']

    try:
        mtime = os.path.getmtime(path)
        dt = datetime.datetime.fromtimestamp(mtime)
        return dt.strftime('%Y:%m:%d %H:%M:%S')
    except Exception:
        pass

    return None


def set_exif_fields(path, photographer=None, datetime_str=None):
    """Set Artist and/or DateTimeOriginal EXIF fields in one call."""
    if not photographer and not datetime_str:
        return True
    try:
        args = ['exiftool', '-overwrite_original']
        if photographer:
            args.append(f'-Artist={photographer}')
        if datetime_str:
            args.append(f'-DateTimeOriginal={datetime_str}')
        args.append(path)
        subprocess.run(args, capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def set_photographers(src_dir):
    """Set photographer EXIF field on all photos based on camera model."""
    stats = defaultdict(lambda: {'updated': 0, 'skipped': 0, 'error': 0})

    for i, (path, exif_data) in enumerate(iter_media_with_exif(src_dir), 1):
        make = exif_data.get('Make', '')
        model = exif_data.get('Model', '')
        photographer = get_photographer(make, model)

        if not photographer:
            stats['unknown']['skipped'] += 1
            continue

        if set_exif_fields(path, photographer=photographer):
            stats[photographer]['updated'] += 1
        else:
            stats[photographer]['error'] += 1

        if i % 50 == 0:
            print(f"  Processed {i} files...", end='\r')
            sys.stdout.flush()

    print(f"\nProcessed {i} files\n")
    for photographer, counts in sorted(stats.items()):
        print(f"{photographer}:")
        print(f"  Updated: {counts['updated']}")
        if counts['error'] > 0:
            print(f"  Errors: {counts['error']}")


def set_dates(src_dir):
    """Set DateTimeOriginal on all photos from filename/path/other EXIF tags."""
    stats = {'updated': 0, 'skipped': 0, 'error': 0, 'already_set': 0}

    for i, (path, exif_dates) in enumerate(iter_media_with_exif(src_dir), 1):
        if exif_dates.get('DateTimeOriginal'):
            stats['already_set'] += 1
            continue

        datetime_str = detect_datetime_with_cache(path, exif_dates)
        if not datetime_str:
            stats['skipped'] += 1
            continue

        if set_exif_fields(path, datetime_str=datetime_str):
            stats['updated'] += 1
        else:
            stats['error'] += 1

        if i % 50 == 0:
            print(f"  Processed {i} files...", end='\r')
            sys.stdout.flush()

    print(f"\nProcessed {i} files\n")
    print(f"Updated: {stats['updated']}")
    print(f"Already set: {stats['already_set']}")
    print(f"Skipped (no date found): {stats['skipped']}")
    if stats['error'] > 0:
        print(f"Errors: {stats['error']}")


def report_photographers(src_dir):
    """Preview photographer assignments."""
    assignments = defaultdict(int)
    unknown = 0

    for path, exif_data in iter_media_with_exif(src_dir):
        make = exif_data.get('Make', '')
        model = exif_data.get('Model', '')
        photographer = get_photographer(make, model)

        if photographer:
            assignments[photographer] += 1
        else:
            unknown += 1

    print("Photographer assignments:\n")
    for photographer, count in sorted(assignments.items(), key=lambda x: -x[1]):
        print(f"  {photographer}: {count} files")
    if unknown:
        print(f"  Unknown: {unknown} files")


def report_dates(src_dir):
    """Preview date detection for files missing DateTimeOriginal."""
    has_date = 0
    can_detect = 0
    cannot_detect = 0

    for path, exif_dates in iter_media_with_exif(src_dir):
        if exif_dates.get('DateTimeOriginal'):
            has_date += 1
            continue

        datetime_str = detect_datetime_with_cache(path, exif_dates)
        if datetime_str:
            can_detect += 1
        else:
            cannot_detect += 1

    total = has_date + can_detect + cannot_detect
    print(f"Date detection preview for {total} files:\n")
    print(f"  Already have DateTimeOriginal: {has_date} ({100*has_date/total:.1f}%)")
    print(f"  Can detect from path/filename: {can_detect} ({100*can_detect/total:.1f}%)")
    print(f"  Cannot detect: {cannot_detect} ({100*cannot_detect/total:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Fix EXIF data: add photographer & dates")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("set-photographer", help="Set photographer based on camera model")
    subparsers.add_parser("set-dates", help="Set DateTimeOriginal from filename/path/other tags")
    subparsers.add_parser("report", help="Preview all assignments")

    parser.add_argument("src_dir", help="Source directory")

    args = parser.parse_args()

    src_dir = os.path.abspath(os.path.expanduser(args.src_dir))
    if not os.path.isdir(src_dir):
        print(f"Error: {src_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    if args.command == "set-photographer":
        set_photographers(src_dir)
    elif args.command == "set-dates":
        set_dates(src_dir)
    elif args.command == "report":
        print("=== Photographer Assignments ===\n")
        report_photographers(src_dir)
        print("\n=== Date Detection ===\n")
        report_dates(src_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
