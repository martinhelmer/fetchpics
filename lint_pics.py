#!/usr/bin/env python3
import os
import sys
import csv
import re
import argparse
import subprocess
import json
import time
from pathlib import Path
from datetime import datetime
from statistics import stdev
from collections import Counter

# Same media extensions as fetchpics.py
PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2", ".dng", ".pef", ".srw", ".raf",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv",
    ".flv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg",
}

ALL_MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

COMMON_SCREEN_RESOLUTIONS = {
    (1080, 1920), (1920, 1080),
    (1440, 2560), (2560, 1440),
    (1440, 3040), (3040, 1440),
    (1080, 2340), (2340, 1080),
    (1125, 2436), (2436, 1125),
    (1080, 2280), (2280, 1080),
    (390, 844), (844, 390),
    (412, 915), (915, 412),
    (1284, 2778), (2778, 1284),
    (800, 600), (600, 800),
    (1024, 768), (768, 1024),
    (1366, 768), (768, 1366),
    (1600, 900), (900, 1600),
    (2560, 1600), (1600, 2560),
}

SUSPICIOUS_FILENAMES = {
    'thumb', 'thumbnail', 'icon', 'cache', '.nomedia',
    'thumbs.db', '.ds_store', 'desktop.ini', 'albumart',
    'folder.jpg', '_thumb', 'favicon'
}

SUSPICIOUS_DIRS = {
    '.cache', 'thumbnails', '.thumbnails', '__macosx',
    'android/data', '.android', 'whatsapp/.statuses'
}


KNOWN_APP_KEYWORDS = {
    'screenshot', 'whatsapp', 'instagram', 'snapchat',
    'tiktok', 'telegram', 'facebook', 'messenger', 'imessage'
}

FLAG_LEVELS = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}


def get_image_dimensions(filepath):
    """Get image dimensions via ImageMagick identify."""
    try:
        result = subprocess.run(
            ['identify', '-format', '%w %h', filepath],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) == 2:
                return (int(parts[0]), int(parts[1])), None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None, None


def get_video_info(filepath):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', filepath],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            duration = float(data.get('format', {}).get('duration', 0))
            streams = data.get('streams', [])
            has_audio = any(s.get('codec_type') == 'audio' for s in streams)
            return duration, has_audio
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, TypeError):
        pass
    return None, None


def check_heuristics(filepath):
    reasons = []
    score = 0
    path_obj = Path(filepath)
    filename = path_obj.name
    file_size = os.path.getsize(filepath)
    ext = path_obj.suffix.lower()
    cached_dims = None
    cached_duration = None

    # FAST CHECKS (early exit if score >= 3)

    # H1: Small file size
    if ext in ALL_MEDIA_EXTENSIONS and file_size < 50 * 1024:
        score += 1
        reasons.append('H1_small_size')

    # H3: Suspicious filename/path
    filename_lower = filename.lower()
    path_str = str(filepath).lower()

    if any(pattern in filename_lower for pattern in SUSPICIOUS_FILENAMES):
        score += 1
        reasons.append('H3_bad_filename')

    if any(pattern in path_str for pattern in SUSPICIOUS_DIRS):
        score += 1
        reasons.append('H3_bad_path')

    if filename.startswith('.'):
        score += 1
        reasons.append('H3_hidden_file')

    # Early exit: if we already have HIGH confidence from fast checks
    if score >= 3:
        return 'HIGH', score, reasons, cached_dims, cached_duration

    # H10: Suspicious filename patterns
    if re.match(r'^[a-f0-9]{32,}$', filename.split('.')[0]):
        score += 1
        reasons.append('H10_hex_name')

    if any(pattern in filename_lower for pattern in ['(1)', '(2)', ' copy', '_copy']):
        score += 1
        reasons.append('H10_duplicate_marker')

    # H11: Implausible file dates
    mtime = os.path.getmtime(filepath)
    if mtime < 631152000 or mtime > time.time() + 86400:
        score += 1
        reasons.append('H11_bad_date')

    # Early exit: if we already have HIGH confidence
    if score >= 3:
        return 'HIGH', score, reasons, cached_dims, cached_duration

    # IMAGE-SPECIFIC HEURISTICS (still relatively fast)
    if ext.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}:
        dims, mode = get_image_dimensions(filepath)
        cached_dims = dims

        # H2: Small dimensions
        if dims and (dims[0] < 200 or dims[1] < 200):
            score += 1
            reasons.append('H2_small_dims')

        # H7: Screenshot dimensions
        if dims and dims in COMMON_SCREEN_RESOLUTIONS:
            score += 1
            reasons.append('H7_screenshot_dims')

        # H9: Suspicious format
        if ext in {'.gif', '.webp', '.bmp'}:
            score += 1
            reasons.append(f'H9_is_{ext[1:]}')

        # Early exit: if we already have HIGH confidence before expensive EXIF checks
        if score >= 3:
            return 'HIGH', score, reasons, cached_dims, cached_duration

        # H8: Square aspect ratio (simple dimension check)
        if dims and dims[0] == dims[1] and dims[0] < 512:
            score += 1
            reasons.append('H8_square_icon')

    # Early exit before expensive video checks (ffprobe)
    if score >= 3:
        return 'HIGH', score, reasons, cached_dims, cached_duration

    # VIDEO-SPECIFIC HEURISTICS (expensive - ffprobe subprocess)
    if ext.lower() in {'.mp4', '.mov', '.avi', '.mkv', '.webm'}:
        duration, has_audio = get_video_info(filepath)
        cached_duration = duration

        if duration is not None:
            # H12: Short duration
            if duration < 2:
                score += 1
                reasons.append('H12_short_duration')

            # H13: No audio
            if not has_audio:
                score += 1
                reasons.append('H13_no_audio')

    # Determine flag level
    flag_map = {3: 'HIGH', 2: 'MEDIUM', 1: 'LOW', 0: 'SKIP'}
    flag = flag_map.get(min(score, 3), 'SKIP')

    return flag, score, reasons, cached_dims, cached_duration


def get_dimensions_str(filepath, ext, cached_dims=None, cached_duration=None):
    if ext.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}:
        dims = cached_dims or get_image_dimensions(filepath)[0]
        return f"{dims[0]}x{dims[1]}" if dims else "n/a"
    elif ext.lower() in {'.mp4', '.mov', '.avi', '.mkv', '.webm'}:
        duration = cached_duration or get_video_info(filepath)[0]
        return f"duration={duration:.1f}s" if duration is not None else "n/a"
    return "n/a"


def tag_exif(filepath, tag_value):
    """Write removal candidate tag to EXIF Keywords field via exiftool."""
    try:
        subprocess.run(
            ['exiftool', '-overwrite_original', f'-Keywords={tag_value}', filepath],
            capture_output=True, check=True, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def filter_candidates_by_flag(candidates, min_flag):
    """Filter candidate dicts by flag_level threshold."""
    if min_flag not in FLAG_LEVELS:
        raise ValueError(f"Invalid min_flag '{min_flag}'. Must be one of: {', '.join(FLAG_LEVELS.keys())}")
    min_level = FLAG_LEVELS[min_flag]
    return [c for c in candidates if FLAG_LEVELS.get(c['flag_level'], -1) >= min_level]


def lint_collection(root_path, output_file, min_flag='HIGH'):
    if min_flag not in FLAG_LEVELS:
        raise ValueError(f"Invalid min_flag '{min_flag}'. Must be one of: {', '.join(FLAG_LEVELS.keys())}")
    min_level = FLAG_LEVELS[min_flag]

    candidates = []
    total_files = 0
    skipped = 0
    non_media = 0

    print(f"Scanning {root_path}...\n")
    for dirpath, dirnames, filenames in os.walk(root_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            ext = Path(filename).suffix.lower()

            # Skip files that aren't media (same as fetchpics.py)
            if ext not in ALL_MEDIA_EXTENSIONS:
                non_media += 1
                continue

            total_files += 1

            # Progress indicator every 1000 files
            if total_files % 1000 == 0:
                print(f"  Scanned {total_files} files, {len(candidates)} candidates found...", flush=True)

            try:
                flag, score, reasons, cached_dims, cached_duration = check_heuristics(filepath)

                if flag == 'SKIP':
                    skipped += 1
                    continue

                dimensions = get_dimensions_str(filepath, ext, cached_dims, cached_duration)
                file_size = os.path.getsize(filepath)
                rel_path = os.path.relpath(filepath, root_path)
                reasons_str = '; '.join(reasons)

                candidate = {
                    'path': rel_path,
                    'size_bytes': file_size,
                    'dimensions': dimensions,
                    'flag_level': flag,
                    'score': score,
                    'reasons': reasons_str
                }
                candidates.append(candidate)

                # Print immediately if meets display threshold
                if FLAG_LEVELS[flag] >= min_level:
                    print(f"[{flag}] {rel_path}")
                    print(f"      size={file_size} dims={dimensions} reasons={reasons_str}")

            except Exception as e:
                print(f"Error processing {filepath}: {e}", file=sys.stderr)

    # Write CSV with ALL candidates
    with open(output_file, 'w', newline='', encoding='utf-8', errors='replace') as f:
        writer = csv.DictWriter(f, fieldnames=['path', 'size_bytes', 'dimensions', 'flag_level', 'score', 'reasons'])
        writer.writeheader()
        writer.writerows(candidates)

    # Final summary
    print(f"\n=== Lint Complete ===")
    print(f"Media files scanned: {total_files}")
    print(f"Non-media files skipped: {non_media}")
    print(f"Score 0 (clean): {skipped}")
    print(f"Candidates found: {len(candidates)}")
    print(f"Full CSV written to: {output_file}")

    # Return candidates meeting min_flag threshold
    return [c for c in candidates if FLAG_LEVELS[c['flag_level']] >= min_level]


def main():
    parser = argparse.ArgumentParser(description='Lint photo/video collection for unwanted files')
    parser.add_argument('path', nargs='?', help='Path to photo collection')
    parser.add_argument('--output', default='lint_report.csv', help='CSV output file')
    parser.add_argument('--min-flag', choices=['LOW', 'MEDIUM', 'HIGH'], default='HIGH',
                        help='Minimum flag level to display/delete/tag (default: HIGH)')
    parser.add_argument('--delete', action='store_true', help='Delete candidates at min-flag level after confirmation')
    parser.add_argument('--tag', action='store_true', help='Tag candidates at min-flag level in EXIF Keywords')
    parser.add_argument('--delete-from-report', action='store_true', help='Delete candidates from CSV report (based on min-flag)')
    parser.add_argument('--label-from-report', action='store_true', help='Tag candidates from CSV report (based on min-flag)')

    args = parser.parse_args()

    # Validate arguments
    if args.delete_from_report or args.label_from_report:
        # Report-based modes need both path and report
        if not args.path:
            print(f"Error: path argument required for --delete-from-report or --label-from-report", file=sys.stderr)
            sys.exit(1)
        if not os.path.isdir(args.path):
            print(f"Error: {args.path} is not a directory", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(args.output):
            print(f"Error: report file {args.output} not found", file=sys.stderr)
            sys.exit(1)
    else:
        # Scan modes need path
        if not args.path:
            parser.print_help()
            sys.exit(1)
        if not os.path.isdir(args.path):
            print(f"Error: {args.path} is not a directory", file=sys.stderr)
            sys.exit(1)

    candidates = []
    all_candidates = []

    # Load from report for report-based modes
    if args.delete_from_report:
        with open(args.output, 'r', newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            candidates = filter_candidates_by_flag(list(reader), args.min_flag)

        if not candidates:
            print(f"No candidates found in {args.output} matching {args.min_flag}")
            sys.exit(0)

        print(f"\n=== Delete from Report ===")
        print(f"Will delete {len(candidates)} files (min-flag={args.min_flag}):")
        for c in candidates[:10]:
            print(f"  {c['path']}")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")

        response = input("\nProceed with deletion? (yes/no): ").strip().lower()
        if response == 'yes':
            deleted = 0
            for c in candidates:
                filepath = os.path.join(args.path, c['path'])
                try:
                    os.remove(filepath)
                    deleted += 1
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"Failed to delete {c['path']}: {e}", file=sys.stderr)
            print(f"\nDeleted {deleted}/{len(candidates)} files")
        else:
            print("Deletion cancelled")
        return

    if args.label_from_report:
        with open(args.output, 'r', newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            all_candidates = filter_candidates_by_flag(list(reader), args.min_flag)

        if not all_candidates:
            print(f"No candidates found in {args.output} matching {args.min_flag}")
            sys.exit(0)

        print(f"\n=== Label from Report ===")
        print(f"Will tag {len(all_candidates)} candidates in EXIF Keywords (min-flag={args.min_flag}):")

        level_counts = Counter(c['flag_level'] for c in all_candidates)
        for level in ['HIGH', 'MEDIUM', 'LOW']:
            if level in level_counts:
                print(f"  {level}: {level_counts[level]}")

        response = input("\nProceed with tagging? (yes/no): ").strip().lower()
        if response == 'yes':
            tagged = 0
            failed = 0
            for c in all_candidates:
                filepath = os.path.join(args.path, c['path'])
                tag = f"LINT_{c['flag_level']}"
                if tag_exif(filepath, tag):
                    tagged += 1
                else:
                    failed += 1

            print(f"\nTagged {tagged}/{len(all_candidates)} files in EXIF Keywords")
            if failed > 0:
                print(f"Failed: {failed}")
        else:
            print("Tagging cancelled")
        return

    if args.tag:
        # Fresh scan to get all levels in CSV
        lint_collection(args.path, args.output, min_flag='LOW')

        # Read candidates from CSV and filter by min_flag
        with open(args.output, 'r', newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            all_candidates = filter_candidates_by_flag(list(reader), args.min_flag)
    else:
        # Fresh scan for preview/delete mode
        candidates = lint_collection(args.path, args.output, args.min_flag)

    if args.tag:
        print(f"\n=== Tagging Mode ===")
        print(f"Will tag {len(all_candidates)} candidates in EXIF Keywords (min-flag={args.min_flag}):")

        # Count by level
        level_counts = Counter(c['flag_level'] for c in all_candidates)
        for level in ['HIGH', 'MEDIUM', 'LOW']:
            if level in level_counts:
                print(f"  {level}: {level_counts[level]}")

        response = input("\nProceed with tagging? (yes/no): ").strip().lower()
        if response == 'yes':
            tagged = 0
            failed = 0
            for c in all_candidates:
                filepath = os.path.join(args.path, c['path'])
                tag = f"LINT_{c['flag_level']}"
                if tag_exif(filepath, tag):
                    tagged += 1
                else:
                    failed += 1
                    print(f"Failed to tag {c['path']}", file=sys.stderr)

            print(f"\nTagged {tagged}/{len(all_candidates)} files in EXIF Keywords")
            if failed > 0:
                print(f"Failed: {failed}")
        else:
            print("Tagging cancelled")

    elif args.delete and candidates:
        print(f"\n=== Deletion Preview ===")
        print(f"The following {len(candidates)} files (min-flag={args.min_flag}) will be deleted:")
        for c in candidates[:10]:
            print(f"  {c['path']}")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")

        response = input("\nProceed with deletion? (yes/no): ").strip().lower()
        if response == 'yes':
            deleted = 0
            for c in candidates:
                filepath = os.path.join(args.path, c['path'])
                try:
                    os.remove(filepath)
                    deleted += 1
                except Exception as e:
                    print(f"Failed to delete {filepath}: {e}", file=sys.stderr)
            print(f"\nDeleted {deleted}/{len(candidates)} files")
        else:
            print("Deletion cancelled")


if __name__ == '__main__':
    main()
