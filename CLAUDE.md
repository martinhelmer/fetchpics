# fetchpics — Photo Consolidation Tools

## Project Goal
Consolidate ~100k photos from 20 backup sources into a single canonical collection, deduplicate, lint, tag, and import into PhotoPrism.

**Status:** 52k+ photos consolidated ✓ | Tools ready | Workflow in progress

---

## Workflow Overview

```
1. CONSOLIDATE  → fetchpics2 (lazy-hash dedup)                  ✓ 52k photos
2. LINT         → lint_pics (tag HIGH/MED/LOW candidates)       → NEXT
3. FIX EXIF     → exif_fixer (add photographer + dates)         → After lint review
4. ORGANIZE     → organize_by_date (sort into YYYY/MM/)         → After cleanup
5. IMPORT       → PhotoPrism (point at canonical-photos)        → Final step
```

---

## Tools & Usage

### `fetchpics2.py` — Consolidation with Dedup
Merges photos from multiple sources into `~/canonical-photos`, skipping duplicates via lazy-hash verification.

```bash
python3 fetchpics2.py --db library.db fetch /source/path ~/canonical-photos
```

**Output:** Database file tracks all files (path, size, hash) for safety.

---

### `lint_pics.py` — Identify Candidates for Deletion
Scans for junk: blank/blurry images, screenshots, near-solid colors, app thumbnails, etc.
Writes candidates to CSV and optionally tags in EXIF for PhotoPrism review.

```bash
# Scan and report (default: HIGH level to stdout)
python3 lint_pics.py ~/canonical-photos

# Tag all candidates (LOW/MED/HIGH) in EXIF Keywords
python3 lint_pics.py ~/canonical-photos --tag --min-flag LOW --output candidates.csv

# Delete marked files after visual review
python3 lint_pics.py ~/canonical-photos --delete --min-flag HIGH
```

**Output:** CSV file with columns: path, size_bytes, dimensions, flag_level, score, reasons.
**CSV fields:** Used by `review_lint.py` for interactive review.

---

### `exif_fixer.py` — Fix EXIF Data
Sets photographer name (based on camera model) and DateTimeOriginal (from filename/path/mtime).

```bash
# Preview photographer assignments
python3 exif_fixer.py report ~/canonical-photos

# Preview which photos can have dates detected
python3 exif_fixer.py report ~/canonical-photos

# Apply photographer assignments
python3 exif_fixer.py set-photographer ~/canonical-photos

# Apply date detection
python3 exif_fixer.py set-dates ~/canonical-photos
```

**Camera→Photographer Mapping:**
- Motorola Moto G Pure → Gabriel
- Samsung SM-F707W → Stephanie
- Samsung SM-G973W → Martin
- Samsung SAMSUNG-SM-G890A → Sebastian

**Date Detection Order:** DateTimeOriginal (if set) → filename patterns → path patterns (e.g. "Day7 - 12 Aug") → other EXIF dates → file mtime.

**Implementation:** Batch size 200 for low memory footprint; reads 200 files' EXIF at once, processes them, then next batch. Progress output every 200 files.

---

### `organize_by_date.py` — Sort by Date
Reorganizes photos into `YYYY/MM/` directory structure based on DateTimeOriginal.

```bash
python3 organize_by_date.py ~/canonical-photos
```

**Requirement:** Run exif_fixer first to ensure all files have DateTimeOriginal.

---

### `review_lint.py` — Interactive Review
Review CSV from `lint_pics` interactively, approve/reject candidates.

```bash
python3 review_lint.py candidates.csv
```

---

## Progress Log

Track consolidation status as work progresses. Update this after each major step.

| Date | Step | Status | Notes |
|------|------|--------|-------|
| 2026-06-09 | Consolidate (fetchpics2) | ✓ Complete | 52,000+ photos from all sources |
| 2026-07-20 | exif_fixer refactor | ✓ Complete | Batch size 200, read-as-you-go, progress bug fixed |
| TBD | Lint (lint_pics --tag) | Pending | Run `lint_pics --tag --min-flag LOW` and review in CSV |
| TBD | EXIF Fix (exif_fixer) | Pending | Set photographer + dates |
| TBD | Organize (organize_by_date) | Pending | Sort by date, ensure all DateTimeOriginal set first |
| TBD | PhotoPrism Review | Pending | Manual review in PhotoPrism UI |
| TBD | Delete & Import | Pending | Remove marked files, point PhotoPrism at canonical |

---

## Implementation Notes

### Memory & Performance
- **Batch size 200:** exif_fixer reads 200 files' EXIF per exiftool call, keeping memory low for 52k+ photos
- **Lazy-hash dedup:** fetchpics2 uses incremental hashing; skips full-file hash unless size/mtime match
- **Progress every 200:** Reduces stdout flushes while keeping feedback visible

### Safety
- All tools support `--delete` flag with confirmation prompt before destructive ops
- CSV output from lint_pics is read-only; deletion must be explicit via flag
- Database (library.db) is immutable once written; safe for audit trail

### Extensibility
- Camera→Photographer mapping in exif_fixer.py:21 is trivial to extend with new models
- Date patterns in exif_fixer.py:65-70 can be added for new filename conventions
- Heuristics in lint_pics.py:100+ are configurable via constants (thresholds at line 200+)

---

## Common Commands

```bash
# Preview what lint_pics will find (HIGH candidates only)
python3 lint_pics.py ~/canonical-photos --min-flag HIGH

# Full CSV export and EXIF tagging (all levels)
python3 lint_pics.py ~/canonical-photos --tag --min-flag LOW --output candidates.csv

# Dry-run: report photographer/date assignments without modifying files
python3 exif_fixer.py report ~/canonical-photos

# Apply both photographer and date fixes
python3 exif_fixer.py set-photographer ~/canonical-photos && \
python3 exif_fixer.py set-dates ~/canonical-photos

# Organize and check results
python3 organize_by_date.py ~/canonical-photos
```
