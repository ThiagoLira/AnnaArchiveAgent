#!/usr/bin/env python3
"""
Download EXACTLY one file by its catalog MD5, verify it (md5 + format), and
place it in the Calibre ingest queue. The agent chooses the MD5 via
search_books.py; this tool never substitutes a different edition.

Usage:
  fetch_md5.py <md5> [--dest DIR]

Exit 0 only when a verified file was ingested.
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_book import (DB_PATH, INGEST_DIR, download_via_gateway,  # noqa: E402
                       download_via_torrent, find_torrent_and_idx, validate_book)


def lookup_md5(md5):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT source, source_id, title, author, year, extension, filesize, md5 "
            "FROM books WHERE md5 = ? COLLATE NOCASE", (md5,)).fetchone()
    finally:
        conn.close()
    return row


def fetch_md5(md5, dest_dir=INGEST_DIR):
    row = lookup_md5(md5)
    if not row:
        print(f"[-] MD5 {md5} not in catalog.")
        return False
    source, source_id, title, author, year, ext, size, md5 = row
    print(f"[*] {md5} = '{title}' by {author} ({year}) [{ext.upper()}, {size:,}B]")

    clean_author = re.sub(r'[\\/:;,\?"*|<>~]', '', author or '').strip()
    clean_title = re.sub(r'[\\/:;,\?"*|<>~]', '', title or '').strip()
    final_name = f"{clean_author} - {clean_title}.{ext}" if clean_author else f"{clean_title}.{ext}"

    os.makedirs(dest_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix='fetch_')
    try:
        got = None
        tpath, idx, relpath = find_torrent_and_idx(source, md5, source_id)
        if tpath:
            print(f"[*] Torrent: {os.path.basename(tpath)} file #{idx} ({relpath})")
            got = download_via_torrent(tpath, idx, relpath, md5, temp_dir)
        else:
            print(f"[~] No local torrent manifest for MD5 {md5}")
        if not got:
            print("[*] Falling back to HTTP gateways...")
            got = download_via_gateway(md5, temp_dir)
        if not got:
            print(f"[-] Could not download {md5} from swarm or gateways.")
            return False

        ok, reason = validate_book(got, ext, md5, size)
        if not ok:
            print(f"[✘] REJECTED (not ingested): {reason}")
            return False

        final_path = os.path.join(dest_dir, final_name)
        shutil.copy2(got, final_path)
        print(f"[✔] Verified & ingested: {final_path} ({os.path.getsize(final_path):,} bytes) — {reason}")
        return True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('md5')
    ap.add_argument('--dest', default=INGEST_DIR)
    args = ap.parse_args()
    if not re.fullmatch(r'[0-9a-fA-F]{32}', args.md5):
        print("Argument must be a 32-hex-char MD5.")
        sys.exit(2)
    sys.exit(0 if fetch_md5(args.md5, args.dest) else 1)
