#!/usr/bin/env python3
import argparse
import csv
import os
import subprocess
import sys


def open_file(filepath):
    """Open file with default viewer."""
    try:
        subprocess.Popen(['xdg-open', filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def delete_file(path):
    """Delete file, return True if successful."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  ✗ {path}: {e}", file=sys.stderr)
        return False


def prompt_delete():
    """Prompt user for delete decision, return True/False."""
    while True:
        response = input("\nDelete? (y/n): ").strip().lower()
        if response in ('y', 'yes'):
            return True
        if response in ('n', 'no'):
            return False
        print("Please enter 'y' or 'n'")


def load_candidates(csv_file, flag_level=None):
    """Load CSV, optionally filter by flag_level. Return list of rows."""
    with open(csv_file, 'r') as f:
        candidates = list(csv.DictReader(f))
    if flag_level:
        return [c for c in candidates if c['flag_level'].upper() == flag_level.upper()]
    return candidates


def process_candidates(csv_file, collection_root, auto_delete=False):
    """Process candidates: interactive review or auto-delete. Return stats dict."""
    candidates = load_candidates(csv_file, auto_delete if auto_delete else None)
    stats = {'deleted': 0, 'kept': 0, 'skipped': 0}

    mode_label = f"{auto_delete} confidence" if auto_delete else "all"
    print(f"{'Auto-deleting' if auto_delete else 'Reviewing'} {len(candidates)} {mode_label} files from {csv_file}\n")

    for i, candidate in enumerate(candidates, 1):
        rel_path = candidate['path']
        full_path = os.path.join(collection_root, rel_path)

        result = delete_file(full_path) if auto_delete else None
        if result is None:
            stats['skipped'] += 1
            if not auto_delete:
                print(f"\n[{i}/{len(candidates)}] MISSING: {rel_path}")
            continue

        if auto_delete:
            if result:
                stats['deleted'] += 1
                print(f"  ✓ {rel_path}")
        else:
            print(f"\n[{i}/{len(candidates)}] {rel_path}")
            print(f"  Size: {candidate['size_bytes']} bytes")
            print(f"  Dims: {candidate['dimensions']}")
            print(f"  Score: {candidate['score']} ({candidate['flag_level']})")
            print(f"  Reasons: {candidate['reasons']}")
            print("\nOpening file...")
            open_file(full_path)

            if prompt_delete():
                if delete_file(full_path):
                    stats['deleted'] += 1
                    print(f"  ✓ Deleted")
            else:
                stats['kept'] += 1
                print(f"  - Kept")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Review and delete flagged files from lint report")
    parser.add_argument("csv_file", help="CSV file with flagged candidates")
    parser.add_argument("collection_root", help="Root directory containing original files")
    parser.add_argument("--delete", choices=['HIGH', 'MED', 'LOW'], help="Auto-delete files at this level")

    args = parser.parse_args()

    if not os.path.isfile(args.csv_file):
        print(f"Error: {args.csv_file} not found", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(args.collection_root):
        print(f"Error: {args.collection_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    stats = process_candidates(args.csv_file, args.collection_root, args.delete)

    print(f"\n=== Complete ===")
    print(f"Deleted: {stats['deleted']}")
    print(f"Kept: {stats['kept']}")
    print(f"Skipped: {stats['skipped']}")


if __name__ == '__main__':
    main()
