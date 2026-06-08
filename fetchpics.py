#!/usr/bin/env python3
"""
fetchpics — photo/video import tool with duplicate detection

Usage:
  fetchpics init <dir>              Initialize DB with contents of dir
  fetchpics fetch <src> <dst>       Copy non-duplicate files from src to dst
  fetchpics fetch --preserve-dirs <src> <dst>  Copy while preserving directory structure
"""

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
from pathlib import Path

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2", ".dng", ".pef", ".srw", ".raf",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv",
    ".flv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg",
}

ALL_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def open_db(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS files (
            hash TEXT PRIMARY KEY,
            size INTEGER NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_size ON files(size)")
    db.commit()
    return db


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_media(path: Path) -> bool:
    return path.suffix.lower() in ALL_EXTENSIONS


def iter_media(directory: Path):
    for root, _, files in os.walk(directory, followlinks=False):
        for fname in files:
            p = Path(root) / fname
            if is_media(p):
                yield p


def is_dupe(db: sqlite3.Connection, path: Path) -> tuple[bool, str | None]:
    """Returns (is_dupe, hash). Hash is None if dupe detected by size only."""
    size = path.stat().st_size
    rows = db.execute("SELECT hash FROM files WHERE size=?", (size,)).fetchall()
    if not rows:
        return False, None
    # Size match — need to check hash
    h = file_hash(path)
    for (existing_hash,) in rows:
        if existing_hash == h:
            return True, h
    return False, h


def register(db: sqlite3.Connection, path: Path, precomputed_hash: str | None = None):
    size = path.stat().st_size
    h = precomputed_hash or file_hash(path)
    db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
    db.commit()


def cmd_init(args):
    directory = Path(args.dir).expanduser().resolve()
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.db).expanduser().resolve()
    db = open_db(db_path)
    print(f"Initializing DB from {directory} ...")

    added = 0
    skipped = 0
    for path in iter_media(directory):
        size = path.stat().st_size
        h = file_hash(path)
        existing = db.execute("SELECT 1 FROM files WHERE hash=?", (h,)).fetchone()
        if existing:
            skipped += 1
        else:
            db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
            added += 1
        if (added + skipped) % 100 == 0:
            db.commit()
            print(f"  {added + skipped} files processed...", end="\r")

    db.commit()
    print(f"\nDone. Added {added} files, skipped {skipped} already known.")


def cmd_fetch(args):
    src = Path(args.src).expanduser().resolve()
    dst = Path(args.dst).expanduser().resolve()

    if not src.is_dir():
        print(f"Error: {src} is not a directory", file=sys.stderr)
        sys.exit(1)

    dst.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db).expanduser().resolve()
    db = open_db(db_path)

    imported = 0
    dupes = 0
    errors = 0

    for path in iter_media(src):
        try:
            dupe, h = is_dupe(db, path)
            if dupe:
                dupes += 1
                print(f"  DUPE    {path.name}")
                continue

            # Not a dupe — copy to destination
            if args.preserve_dirs:
                rel_path = path.relative_to(src)
                dest_path = dst / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                dest_path = dst / path.name

            # Handle filename collision (different file, same name)
            if dest_path.exists():
                stem = path.stem
                suffix = path.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = dest_path.parent / f"{stem}_{counter}{suffix}"
                    counter += 1

            shutil.copy2(path, dest_path)

            # Register in DB — use precomputed hash if we have it
            h = h or file_hash(path)
            size = path.stat().st_size
            db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
            db.commit()

            imported += 1
            print(f"  IMPORT  {path.name} → {dest_path.name}")

        except Exception as e:
            errors += 1
            print(f"  ERROR   {path.name}: {e}", file=sys.stderr)

    print(f"\nDone. Imported: {imported}, Dupes skipped: {dupes}, Errors: {errors}")


def main():
    parser = argparse.ArgumentParser(
        description="fetchpics — photo/video import with duplicate detection"
    )
    parser.add_argument("--db", required=True, help="Path to database file")
    subparsers = parser.add_subparsers(dest="command")

    p_init = subparsers.add_parser("init", help="Initialize DB from existing library")
    p_init.add_argument("dir", help="Directory to scan")

    p_fetch = subparsers.add_parser("fetch", help="Import non-duplicate files")
    p_fetch.add_argument("src", help="Source directory (backup)")
    p_fetch.add_argument("dst", help="Destination directory (inbox)")
    p_fetch.add_argument(
        "--preserve-dirs",
        action="store_true",
        help="Preserve source directory structure in destination"
    )

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

