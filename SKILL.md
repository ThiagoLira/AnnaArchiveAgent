---
name: offline-book-grabber
description: Search the local Anna's Archive/LibGen SQLite catalog (7.6M books), pick the best edition by MD5, and download it verified into the Calibre ingest queue. Agent-in-the-loop by design - the agent judges candidates, the tools only search and fetch.
---

# Offline & Swarm Book Grabber Skill

Agent-driven pipeline: **you** (the agent) query the catalog, judge which MD5 is
the right book and best edition, then fetch exactly that MD5. The fetch tool
verifies every download (md5 + format) before it reaches Calibre.

## Workflow (agent babysits every step)

```
1. SEARCH  — search_books.py "<title>" ["<author>"]
     └── prints candidates: MD5 | format | size | year | title — author | publisher
2. JUDGE   — you pick the best MD5 (see criteria below)
3. FETCH   — fetch_md5.py <md5>
     └── torrent-piece download, HTTP gateway fallback
     └── verifies md5-of-bytes == catalog md5 AND format sanity
     └── only verified files land in /mnt/storage/books-ingest/
4. CONFIRM — exit 0 = verified & ingested; on failure pick the next-best MD5 and retry
```

### Judging criteria (step 2)

- Only fetch works that are public domain, openly licensed, or that the user
  has the right to download. If that is unclear, ask the user before fetching.
- Title must actually be the requested book (catalog has junk/mislabeled rows —
  a "the Golden Bough" record once pointed at a Star Trek novel).
- Prefer EPUB over PDF; PDF over mobi/other.
- Plausible size: a full book EPUB is rarely <300 KB; huge PDFs (>20 MB) are
  page scans that download slowly and read badly — prefer a mid-size text PDF.
- Prefer a real publisher and a sane year over blank fields.
- If two candidates look equal, prefer the smaller torrent wait: EPUBs first.

## Commands

```bash
# Step 1: candidates for the agent to read (add --json for machine parsing)
python3 ~/.agents/skills/offline-book-grabber/search_books.py "Pride and Prejudice" "Austen"

# Step 3: fetch the MD5 you chose
python3 ~/.agents/skills/offline-book-grabber/fetch_md5.py <md5-chosen-in-step-2>
```

### Unattended fallback (no agent judgment)

`grab_book.py "<title>" ["<author>"]` chains search→fetch automatically
(EPUB-first, largest-first, tries 3 candidates). Same verification applies.
Use it only for bulk jobs where per-book judgment isn't worth it.

## Verification (never bypass)

Every download must pass, before ingest:
1. **md5 of downloaded bytes == catalog md5** — files are md5-addressed, so this
   proves the file is complete and correct.
2. **Format sanity** — EPUB: readable zip containing an OPF; PDF: `%PDF` header
   and `%%EOF` trailer; MOBI: `BOOKMOBI` header.

On 2026-09-02 a pre-verification version ingested 17 corrupt files
(truncated/offset-shifted torrent pieces, wrong file indices) whose sizes looked
right — file size alone proves nothing.

## Local Paths Reference
- **SQLite Catalog**: `/mnt/storage/annas_archive/metadata/catalog.db` (7,620,278 books; sources: libgen_rs_fic, libgen_rs_non_fic)
- **Torrent Manifests**: `/mnt/storage/annas_archive/manifests/` (22,132 container manifests)
- **Calibre Ingest Folder**: `/mnt/storage/books-ingest/` (CWA auto-ingests)
- **Calibre Library**: `/mnt/storage/books/`
