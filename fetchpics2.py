#!/usr/bin/env python3
"""
fetchpics2 — photo/video consolidation with size-first, lazy-hash duplicate detection

Design:
  - The DB stores one row per known file: (path, size, hash). `hash` is NULL until
    a size collision forces us to compute it ("lazy hashing").
  - reindex does ZERO hashing — it only stat()s files. Fast even for 100k+ files.
    It is incremental and safe to re-run whenever the canonical dir changes.
  - fetch hashes lazily: only when a source file's size matches something already
    known. Hashes computed this way are cached back into the DB.
  - A file is declared a duplicate ONLY after a confirmed hash match against a
    canonical file that provably exists on disk right now. Size alone is NEVER
    enough to delete a source. This is the deletion-safety invariant.

Usage:
  fetchpics2 --db DB reindex <dir>               Record contents of dir (no hashing)
  fetchpics2 --db DB fetch <src> <dst>           Copy non-duplicate files src -> dst
  fetchpics2 --db DB fetch --preserve-dirs ...   Preserve source dir structure
  fetchpics2 --db DB fetch --delete ...          Delete source files after verified import
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
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            hash TEXT
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


def paths_overlap(a, b):
    """True if a and b are the same dir, or one is nested inside the other."""
    a = os.path.realpath(a)
    b = os.path.realpath(b)
    if a == b:
        return True
    return a.startswith(b + os.sep) or b.startswith(a + os.sep)


def find_confirmed_dupe(db, size, h_src):
    """
    Look for a known file with this size whose content matches h_src.

    Returns the canonical path of a confirmed, currently-existing duplicate, or
    None. Along the way it lazily fills in missing hashes, prunes rows whose file
    has vanished, and re-checks rows whose on-disk size has drifted from the DB.
    A match is only returned for a file that exists on disk with the expected size.
    """
    rows = db.execute(
        "SELECT path, hash FROM files WHERE size=?", (size,)
    ).fetchall()
    for cpath, chash in rows:
        if not os.path.exists(cpath):
            # Canonical copy is gone — DB drifted. Drop the stale row.
            db.execute("DELETE FROM files WHERE path=?", (cpath,))
            continue
        if os.path.getsize(cpath) != size:
            # File changed since it was recorded; any cached hash is unreliable.
            db.execute("DELETE FROM files WHERE path=?", (cpath,))
            continue
        if chash is None:
            chash = file_hash(cpath)
            db.execute("UPDATE files SET hash=? WHERE path=?", (chash, cpath))
        if chash == h_src:
            return cpath
    return None


def cmd_reindex(args):
    directory = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(directory):
        print("Error: {0} is not a directory".format(directory), file=sys.stderr)
        sys.exit(1)

    db_path = os.path.abspath(os.path.expanduser(args.db))
    db = open_db(db_path)
    print("Reindexing DB from {0} (size-only, no hashing) ...".format(directory))

    added = 0
    for path in iter_media(directory):
        try:
            size = os.path.getsize(path)
        except OSError as e:
            print("  SKIP    {0}: {1}".format(path, e), file=sys.stderr)
            continue
        # OR IGNORE: re-running init must not clobber an already-computed hash.
        db.execute(
            "INSERT OR IGNORE INTO files (path, size, hash) VALUES (?, ?, NULL)",
            (path, size),
        )
        added += 1
        if added % 500 == 0:
            db.commit()
            print("  {0} files recorded...".format(added), end="\r")
            sys.stdout.flush()

    db.commit()
    print("\nDone. Recorded {0} files.".format(added))


def cmd_fetch(args):
    src = os.path.abspath(os.path.expanduser(args.src))
    dst = os.path.abspath(os.path.expanduser(args.dst))

    if not os.path.isdir(src):
        print("Error: {0} is not a directory".format(src), file=sys.stderr)
        sys.exit(1)

    # Catastrophic-mistake guard: never let a source overlap the destination,
    # otherwise --delete could wipe the very library we're consolidating into.
    if paths_overlap(src, dst):
        print(
            "Error: src and dst overlap ({0} <-> {1}). Refusing to run.".format(src, dst),
            file=sys.stderr,
        )
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
            size = os.path.getsize(path)

            # Size-first dedup: only hash when something of this size is known.
            h_src = None
            dupe_of = None
            rows_exist = db.execute(
                "SELECT 1 FROM files WHERE size=? LIMIT 1", (size,)
            ).fetchone()
            if rows_exist:
                h_src = file_hash(path)
                dupe_of = find_confirmed_dupe(db, size, h_src)

            if dupe_of is not None:
                # dupe_of was confirmed to exist with matching size+hash above.
                dupes += 1
                if args.delete:
                    os.unlink(path)
                db.commit()
                print("  DUPE    {0}  (== {1})".format(
                    os.path.basename(path), dupe_of))
                continue

            # Not a duplicate -> import.
            if args.preserve_dirs:
                rel_path = os.path.relpath(path, src)
                dest_path = os.path.join(dst, rel_path)
                dest_dir = os.path.dirname(dest_path)
                if not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
            else:
                dest_path = os.path.join(dst, os.path.basename(path))

            # Never overwrite an existing destination file: rename on collision.
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(os.path.basename(path))
                dest_dir = os.path.dirname(dest_path)
                counter = 1
                while os.path.exists(dest_path):
                    dest_path = os.path.join(
                        dest_dir, "{0}_{1}{2}".format(base, counter, ext))
                    counter += 1

            shutil.copy2(path, dest_path)

            # Verify the copy before trusting it / deleting the source.
            if os.path.getsize(dest_path) != size:
                raise IOError(
                    "copy size mismatch: {0} != {1}".format(
                        os.path.getsize(dest_path), size))

            # Register the new canonical location. Store h_src if we already
            # computed it (size collision); otherwise leave hash NULL (lazy).
            db.execute(
                "INSERT OR REPLACE INTO files (path, size, hash) VALUES (?, ?, ?)",
                (dest_path, size, h_src),
            )
            db.commit()

            if args.delete:
                os.unlink(path)

            imported += 1
            print("  IMPORT  {0} -> {1}".format(
                os.path.basename(path), os.path.basename(dest_path)))

        except Exception as e:
            errors += 1
            print("  ERROR   {0}: {1}".format(
                os.path.basename(path), str(e)), file=sys.stderr)

    print("\nDone. Imported: {0}, Dupes skipped: {1}, Errors: {2}".format(
        imported, dupes, errors))


def main():
    parser = argparse.ArgumentParser(
        description="fetchpics2 — photo/video consolidation with lazy-hash dedup"
    )
    parser.add_argument("--db", required=True, help="Path to database file")
    subparsers = parser.add_subparsers(dest="command")

    p_reindex = subparsers.add_parser(
        "reindex", help="Record/refresh a library's contents (no hashing; re-runnable)")
    p_reindex.add_argument("dir", help="Directory to scan")

    p_fetch = subparsers.add_parser("fetch", help="Import non-duplicate files")
    p_fetch.add_argument("src", help="Source directory (backup)")
    p_fetch.add_argument("dst", help="Destination directory (canonical library)")
    p_fetch.add_argument(
        "--preserve-dirs",
        action="store_true",
        help="Preserve source directory structure in destination",
    )
    p_fetch.add_argument(
        "--delete",
        action="store_true",
        help="Delete source files after a verified import/dupe match",
    )

    args = parser.parse_args()

    if args.command == "reindex":
        cmd_reindex(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
