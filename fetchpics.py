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

PHOTO_EXTENSIONS = set([
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".webp", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2", ".dng", ".pef", ".srw", ".raf",
])

VIDEO_EXTENSIONS = set([
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv",
    ".flv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg",
])

ALL_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


def open_db(db_path):
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


def file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_media(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in ALL_EXTENSIONS


def iter_media(directory):
    for root, _, files in os.walk(directory, followlinks=False):
        for fname in files:
            path = os.path.join(root, fname)
            if is_media(path):
                yield path


def is_dupe(db, path):
    """Returns (is_dupe, hash). Hash is None if dupe detected by size only."""
    size = os.path.getsize(path)
    rows = db.execute("SELECT hash FROM files WHERE size=?", (size,)).fetchall()
    if not rows:
        return False, None
    h = file_hash(path)
    for (existing_hash,) in rows:
        if existing_hash == h:
            return True, h
    return False, h


def register(db, path, precomputed_hash=None):
    size = os.path.getsize(path)
    h = precomputed_hash or file_hash(path)
    db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
    db.commit()


def cmd_init(args):
    directory = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(directory):
        print("Error: {0} is not a directory".format(directory), file=sys.stderr)
        sys.exit(1)

    db_path = os.path.abspath(os.path.expanduser(args.db))
    db = open_db(db_path)
    print("Initializing DB from {0} ...".format(directory))

    added = 0
    skipped = 0
    for path in iter_media(directory):
        size = os.path.getsize(path)
        h = file_hash(path)
        existing = db.execute("SELECT 1 FROM files WHERE hash=?", (h,)).fetchone()
        if existing:
            skipped += 1
        else:
            db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
            added += 1
        if (added + skipped) % 100 == 0:
            db.commit()
            print("  {0} files processed...".format(added + skipped), end="\r")
            sys.stdout.flush()

    db.commit()
    print("\nDone. Added {0} files, skipped {1} already known.".format(added, skipped))


def cmd_fetch(args):
    src = os.path.abspath(os.path.expanduser(args.src))
    dst = os.path.abspath(os.path.expanduser(args.dst))

    if not os.path.isdir(src):
        print("Error: {0} is not a directory".format(src), file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(dst):
        os.makedirs(dst)

    db_path = os.path.abspath(os.path.expanduser(args.db))
    db = open_db(db_path)

    imported = 0
    dupes = 0
    errors = 0

    for path in iter_media(src):
        try:
            dupe, h = is_dupe(db, path)
            if dupe:
                dupes += 1
                if args.delete:
                    os.unlink(path)
                print("  DUPE    {0}".format(os.path.basename(path)))
                continue

            if args.preserve_dirs:
                rel_path = os.path.relpath(path, src)
                dest_path = os.path.join(dst, rel_path)
                dest_dir = os.path.dirname(dest_path)
                if not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
            else:
                dest_path = os.path.join(dst, os.path.basename(path))

            if os.path.exists(dest_path):
                base, ext = os.path.splitext(os.path.basename(path))
                dest_dir = os.path.dirname(dest_path)
                counter = 1
                while os.path.exists(dest_path):
                    dest_path = os.path.join(dest_dir, "{0}_{1}{2}".format(base, counter, ext))
                    counter += 1

            shutil.copy2(path, dest_path)

            h = h or file_hash(path)
            size = os.path.getsize(path)
            db.execute("INSERT OR IGNORE INTO files (hash, size) VALUES (?, ?)", (h, size))
            db.commit()

            if args.delete:
                os.unlink(path)

            imported += 1
            print("  IMPORT  {0} -> {1}".format(os.path.basename(path), os.path.basename(dest_path)))

        except Exception as e:
            errors += 1
            print("  ERROR   {0}: {1}".format(os.path.basename(path), str(e)), file=sys.stderr)

    print("\nDone. Imported: {0}, Dupes skipped: {1}, Errors: {2}".format(imported, dupes, errors))


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
    p_fetch.add_argument(
        "--delete",
        action="store_true",
        help="Delete source files after successful import"
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

