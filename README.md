# AnnaArchiveAgent

Offline book lookup and selective download built on Anna's Archive metadata and
torrent manifests. The end result is a local SQLite catalog (~7.6M LibGen
records) that an agent searches, plus a fetcher that downloads only the single
file it needs from the right container torrent. It then checks the file's MD5
and format and drops it into a Calibre ingest folder.

## Requirements

- `aria2c`, `pigz`, `python3` (stdlib only)
- About 180 GB free under `/mnt/storage` (the paths are set at the top of each script)

## Reproduce

### 1. Download the manifests and metadata

```bash
./start_ingestion.sh
```

- **Phase 1:** downloads the ~22.6k container `.torrent` manifests listed in
  `manifest_urls_aria2.txt` into `/mnt/storage/annas_archive/manifests/`
  (~350 MB).
- **Phase 2:** downloads only the needed tables from
  `aa_derived_mirror_metadata_20260208.torrent` (~67.5 GB) into
  `/mnt/storage/annas_archive/metadata/`:
  - `aarecords_all_md5` (files 188-189)
  - `computed_all_md5s`, `isbndb`, `libgenli_*`, `libgenrs_*` (files 806-891)
  - `torrents_json`, `zlib_*` (files 904-912)

### 2. Build the SQLite catalog

```bash
python3 build_catalog_fast.py
```

Streams the LibGen fiction and non-fiction MariaDB dumps into
`/mnt/storage/annas_archive/metadata/catalog.db` (`books` table, unique on md5).

### 3. Search and fetch

```bash
python3 search_books.py "Pride and Prejudice" "Austen"  # list candidates (md5, format, size, ...)
python3 fetch_md5.py <md5>                              # download exactly that file
python3 grab_book.py "<title>" ["<author>"]             # search, then fetch the best match
```

`fetch_md5.py` first tries to download just the file's pieces from the container
torrent (`aria2c --select-file`). If that fails, it falls back to an HTTP
gateway. A file is kept only if the MD5 of its bytes matches the catalog MD5 and
it looks like a valid EPUB, PDF or MOBI. Kept files go to `/mnt/storage/books-ingest/`.

## Intended use

This repo is for public-domain works, openly licensed works, and works you
otherwise have the right to download. It contains no books, only code, a list of
manifest URLs and a metadata torrent. The catalogs and swarms it reads from mix
those works with copyrighted ones, so whether a download is legal depends on the
work and on your jurisdiction. You are responsible for what you fetch.

## Agent skill

`SKILL.md` is an agent skill that runs step 3 with the agent choosing the
candidate. Install it by copying `SKILL.md`, `search_books.py`, `fetch_md5.py`
and `grab_book.py` into `~/.agents/skills/offline-book-grabber/`.

## Files

| File | Role |
|---|---|
| `start_ingestion.sh` | Step 1: downloads the manifests and metadata |
| `manifest_urls_aria2.txt` | aria2 input list of every container manifest URL |
| `aa_derived_mirror_metadata_20260208.torrent` | Metadata dump torrent (2026-02-08 snapshot) |
| `build_catalog_fast.py` | Step 2: builds `catalog.db` |
| `grab_book.py` | Shared library and end-to-end CLI (search, torrent/gateway download, verify) |
| `search_books.py` / `fetch_md5.py` | The two agent-facing tools |
| `SKILL.md` | Agent skill definition |
