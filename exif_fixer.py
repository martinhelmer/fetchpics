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


def iter_media(directories):
    """Iterate over all media files recursively from one or more directories."""
    if isinstance(directories, str):
        directories = [directories]
    for directory in directories:
        for root, _, files in os.walk(directory, followlinks=False):
            for fname in files:
                path = os.path.join(root, fname)
                _, ext = os.path.splitext(path)
                if ext.lower() in PHOTO_EXTENSIONS:
                    yield path


def iter_media_batches(directories, batch_size=200):
    """Yields batches of (path, exif_data) tuples; reads and processes 200 at a time."""
    paths = list(iter_media(directories))
    for i in range(0, len(paths), batch_size):
        batch = paths[i:i+batch_size]
        exif_cache = read_exif_batch(batch)
        for path in batch:
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
    """Read EXIF data for a batch of files. Returns dict: path -> exif_data."""
    if not paths:
        return {}

    cache = {}
    try:
        result = subprocess.run(
            ['exiftool', '-json', '-DateTimeOriginal', '-CreateDate', '-ModifyDate',
             '-FileModifyDate', '-Make', '-Model'] + list(paths),
            capture_output=True, text=True, check=True, timeout=300
        )
        data = json.loads(result.stdout)
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
    except subprocess.TimeoutExpired:
        print(f"  Warning: exiftool batch timeout on {len(paths)} files", file=sys.stderr)
        raise
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"  Error: exiftool batch failed on {len(paths)} files: {e}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"  Error: unexpected failure reading EXIF batch: {e}", file=sys.stderr)
        raise

    return cache


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
        subprocess.run(args, capture_output=True, check=True, timeout=30)
        return True
    except subprocess.TimeoutExpired:
        return False
    except subprocess.CalledProcessError:
        return False


def set_photographers(src_dirs):
    """Set photographer EXIF field on all photos based on camera model."""
    stats = defaultdict(lambda: {'updated': 0, 'skipped': 0, 'error': 0})
    total = 0

    for path, exif_data in iter_media_batches(src_dirs):
        make = exif_data.get('Make', '')
        model = exif_data.get('Model', '')
        photographer = get_photographer(make, model)

        if not photographer:
            stats['unknown']['skipped'] += 1
            total += 1
            continue

        if set_exif_fields(path, photographer=photographer):
            stats[photographer]['updated'] += 1
        else:
            stats[photographer]['error'] += 1

        total += 1
        if total % 200 == 0:
            print(f"  Processed {total} files...", end='\r')
            sys.stdout.flush()

    print(f"\nProcessed {total} files\n")
    for photographer, counts in sorted(stats.items()):
        print(f"{photographer}:")
        print(f"  Updated: {counts['updated']}")
        if counts['error'] > 0:
            print(f"  Errors: {counts['error']}")


def set_dates(src_dirs):
    """Set DateTimeOriginal on all photos from filename/path/other EXIF tags."""
    stats = {'updated': 0, 'skipped': 0, 'error': 0, 'already_set': 0}
    total = 0

    for path, exif_dates in iter_media_batches(src_dirs):
        if exif_dates.get('DateTimeOriginal'):
            stats['already_set'] += 1
            total += 1
            continue

        datetime_str = detect_datetime_with_cache(path, exif_dates)
        if not datetime_str:
            stats['skipped'] += 1
            total += 1
            continue

        if set_exif_fields(path, datetime_str=datetime_str):
            stats['updated'] += 1
        else:
            stats['error'] += 1

        total += 1
        if total % 200 == 0:
            print(f"  Processed {total} files...", end='\r')
            sys.stdout.flush()

    print(f"\nProcessed {total} files\n")
    print(f"Updated: {stats['updated']}")
    print(f"Already set: {stats['already_set']}")
    print(f"Skipped (no date found): {stats['skipped']}")
    if stats['error'] > 0:
        print(f"Errors: {stats['error']}")


def report_photographers(src_dirs):
    """Preview photographer assignments."""
    assignments = defaultdict(int)
    unknown = 0

    for path, exif_data in iter_media_batches(src_dirs):
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


def report_dates(src_dirs):
    """Preview date detection for files missing DateTimeOriginal."""
    has_date = 0
    can_detect = 0
    cannot_detect = 0

    for path, exif_dates in iter_media_batches(src_dirs):
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
    if total == 0:
        print("  No media files found")
        return
    print(f"  Already have DateTimeOriginal: {has_date} ({100*has_date/total:.1f}%)")
    print(f"  Can detect from path/filename: {can_detect} ({100*can_detect/total:.1f}%)")
    print(f"  Cannot detect: {cannot_detect} ({100*cannot_detect/total:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Fix EXIF data: add photographer & dates")
    subparsers = parser.add_subparsers(dest="command")

    sp1 = subparsers.add_parser("set-photographer", help="Set photographer based on camera model")
    sp1.add_argument("src_dir", nargs='+', help="Source directory or directories")

    sp2 = subparsers.add_parser("set-dates", help="Set DateTimeOriginal from filename/path/other tags")
    sp2.add_argument("src_dir", nargs='+', help="Source directory or directories")

    sp3 = subparsers.add_parser("report", help="Preview all assignments")
    sp3.add_argument("src_dir", nargs='+', help="Source directory or directories")

    args = parser.parse_args()

    # Expand and validate all directories
    src_dirs = []
    for src_arg in args.src_dir:
        src_dir = os.path.abspath(os.path.expanduser(src_arg))
        if not os.path.isdir(src_dir):
            print(f"Error: {src_dir} is not a directory", file=sys.stderr)
            sys.exit(1)
        src_dirs.append(src_dir)

    if args.command == "set-photographer":
        set_photographers(src_dirs)
    elif args.command == "set-dates":
        set_dates(src_dirs)
    elif args.command == "report":
        print("=== Photographer Assignments ===\n")
        report_photographers(src_dirs)
        print("\n=== Date Detection ===\n")
        report_dates(src_dirs)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
