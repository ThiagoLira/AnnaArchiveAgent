#!/usr/bin/env python3
"""
Offline & Swarm Book Grabber for Pi Agent
Searches local 7.6M SQLite catalog and downloads via local BitTorrent container
manifests, with an HTTP gateway fallback.

Every download is verified before it reaches the Calibre ingest queue:
  1. md5 of the downloaded bytes must equal the catalog md5 (files are md5-addressed)
  2. format sanity check (epub = readable zip with OPF, pdf = %PDF..%%EOF, mobi header)
A file that fails verification is discarded and the next catalog candidate is tried.
"""

import hashlib
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

DB_PATH = '/mnt/storage/annas_archive/metadata/catalog.db'
MANIFESTS_ROOT = '/mnt/storage/annas_archive/manifests'
INGEST_DIR = '/mnt/storage/books-ingest'

TORRENT_TIMEOUT = 900        # overall seconds allowed for one torrent attempt
BT_STOP_TIMEOUT = 300        # give up if swarm sends nothing for this long
MAX_CANDIDATES = 3           # catalog matches to try before giving up

# Some hosts have an IPv6 blackhole: AAAA-first resolution stalls ~12s per request.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
socket.getaddrinfo = _ipv4_getaddrinfo

# libgen mirrors routinely serve self-signed certs; integrity comes from the md5 check.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX))
_OPENER.addheaders = [('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64)')]


def search_catalog(title_query, author_query="", limit=MAX_CANDIDATES):
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Catalog DB not found at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    words = [w for w in re.split(r'[\s\-_\.,;:]+', title_query.strip()) if len(w) > 2]

    # Prefer epub, then pdf; within a format prefer LARGER files — tiny
    # "editions" are overwhelmingly junk/mislabeled uploads (86KB Maps of
    # Meaning, 200KB Golden Bough that was actually a Star Trek novel...).
    order = ("ORDER BY CASE WHEN extension='epub' THEN 1 "
             "WHEN extension='pdf' THEN 2 ELSE 3 END, filesize DESC")

    queries = []
    # Strategy 1: Title AND Author
    if author_query:
        queries.append((f"title LIKE ? AND author LIKE ?",
                        [f'%{title_query}%', f'%{author_query}%']))
    # Strategy 2: Exact phrase title
    queries.append(("title LIKE ?", [f'%{title_query}%']))
    # Strategy 3: Multi-word match
    if len(words) >= 2:
        clause = " AND ".join(["title LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words]
        if author_query:
            clause += " AND author LIKE ?"
            params.append(f"%{author_query}%")
        queries.append((clause, params))

    try:
        for clause, params in queries:
            cur.execute(f'''
                SELECT source, source_id, title, author, year, extension, filesize, md5
                FROM books WHERE {clause} {order} LIMIT ?
            ''', (*params, limit))
            results = cur.fetchall()
            if results:
                return results
        return []
    finally:
        conn.close()


def validate_book(path, ext, expected_md5, expected_size):
    """Return (ok, reason). md5 is authoritative; format check catches
    upstream records whose bytes are intact but aren't the advertised book."""
    size = os.path.getsize(path)
    if expected_size and size != expected_size:
        return False, f"size {size:,} != catalog {expected_size:,} (incomplete download)"

    h = hashlib.md5()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    if h.hexdigest().lower() != expected_md5.lower():
        return False, f"md5 mismatch: got {h.hexdigest()} (corrupt/partial download)"

    ext = ext.lower()
    try:
        if ext == 'epub':
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad:
                    return False, f"epub zip corrupt at {bad}"
                if not any(n.lower().endswith('.opf') for n in z.namelist()):
                    return False, "epub has no OPF (not an epub)"
        elif ext == 'pdf':
            with open(path, 'rb') as fh:
                head = fh.read(1024)
                fh.seek(max(0, size - 2048))
                tail = fh.read()
            if not head.lstrip().startswith(b'%PDF-'):
                return False, "missing %PDF header (not a pdf)"
            if b'%%EOF' not in tail:
                return False, "missing %%EOF trailer (truncated pdf)"
        elif ext in ('mobi', 'azw', 'azw3'):
            with open(path, 'rb') as fh:
                fh.seek(60)
                if fh.read(8) not in (b'BOOKMOBI', b'TEXtREAd'):
                    return False, "bad mobi/palm header"
        else:
            with open(path, 'rb') as fh:
                if fh.read(4096).count(b'\x00') > 4000:
                    return False, "file starts with zeros (preallocated, never filled)"
    except (zipfile.BadZipFile, OSError) as e:
        return False, str(e)
    return True, "verified (md5 + format)"


def find_torrent_and_idx(source, md5, source_id):
    """Locate the container .torrent holding this md5 and the file's index/path."""
    if 'fic' in source and 'non' not in source:
        subdirs = ['libgen_rs_fic', 'libgen_li_fic', 'libgen_rs_non_fic', 'libgen_li_non_fic']
    else:
        subdirs = ['libgen_rs_non_fic', 'libgen_li_non_fic', 'libgen_rs_fic', 'libgen_li_fic']

    for sub in subdirs:
        sdir = os.path.join(MANIFESTS_ROOT, sub)
        if not os.path.exists(sdir):
            continue
        chunk = ((source_id - 1) // 1000 + 1) * 1000
        for c in [chunk, chunk - 1000, chunk + 1000]:
            prefix = 'r_' if 'non_fic' in sub else 'f_'
            tpath = os.path.join(sdir, f'{prefix}{c}.torrent')
            if not os.path.exists(tpath):
                continue
            with open(tpath, 'rb') as fh:
                if md5.encode() not in fh.read():
                    continue
            out = subprocess.check_output(['aria2c', '--show-files', tpath], text=True)
            for line in out.splitlines():
                if md5.lower() in line.lower():
                    m = re.match(r'^\s*(\d+)\|(\S.*)$', line)
                    if m:
                        return tpath, int(m.group(1)), m.group(2).strip()
    return None, None, None


def download_via_torrent(tpath, idx, relpath, md5, temp_dir):
    """Run aria2 on one file of a container torrent. Returns path or None."""
    try:
        proc = subprocess.run([
            'aria2c',
            f'--select-file={idx}',
            f'--dir={temp_dir}',
            '--seed-time=0',
            f'--bt-stop-timeout={BT_STOP_TIMEOUT}',
            '--summary-interval=30',
            '--console-log-level=warn',
            tpath,
        ], capture_output=True, text=True, timeout=TORRENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"  [!] Torrent attempt exceeded {TORRENT_TIMEOUT}s, aborting")
        return None

    # Find OUR file by md5 in its name — --select-file still creates/pads
    # neighboring files that share torrent pieces, never grab those.
    found = None
    for root, _, files in os.walk(temp_dir):
        for f in files:
            if md5.lower() in f.lower() and not f.endswith('.aria2'):
                found = os.path.join(root, f)
                break
        if found:
            break
    if not found:
        print(f"  [!] aria2 exited rc={proc.returncode}; file for {md5} not in output dir")
        return None

    # A leftover .aria2 control file means the download is incomplete.
    if os.path.exists(found + '.aria2') or proc.returncode != 0:
        print(f"  [!] Incomplete torrent download (rc={proc.returncode}, "
              f"control file {'present' if os.path.exists(found + '.aria2') else 'absent'})")
        return None
    return found


def download_via_gateway(md5, temp_dir):
    """HTTP fallback through libgen gateways. Returns path or None."""
    pages = [
        f'https://libgen.li/ads.php?md5={md5}',
        f'http://library.lol/main/{md5}',
        f'http://library.lol/fiction/{md5}',
    ]
    for page_url in pages:
        try:
            html = _OPENER.open(page_url, timeout=30).read().decode('utf-8', 'replace')
        except Exception as e:
            print(f"  [~] gateway {page_url.split('/')[2]}: {e}")
            continue
        links = re.findall(r'href=["\']([^"\']+)["\']', html)
        candidates = [l for l in links
                      if 'get.php?md5=' in l or (md5.lower() in l.lower() and l.startswith('http'))]
        for link in candidates:
            if not link.startswith('http'):
                base = page_url.rsplit('/', 1)[0] if 'library.lol' in page_url \
                    else f"https://{page_url.split('/')[2]}"
                link = f"{base}/{link.lstrip('/')}"
            try:
                resp = _OPENER.open(link, timeout=60)
                out = os.path.join(temp_dir, f'{md5}.gateway')
                with open(out, 'wb') as fh:
                    shutil.copyfileobj(resp, fh, 1 << 20)
                if os.path.getsize(out) > 10000:
                    return out
                os.unlink(out)
            except Exception as e:
                print(f"  [~] direct link failed: {e}")
    return None


def grab_book(title, author="", dest_dir=INGEST_DIR):
    os.makedirs(dest_dir, exist_ok=True)
    print(f"\n[*] Searching local catalog for: '{title}'" + (f" by '{author}'" if author else ""))

    results = search_catalog(title, author)
    if not results:
        print(f"[-] No matches found in catalog for '{title}'.")
        return False

    for source, source_id, b_title, b_author, b_year, ext, size, md5 in results:
        print(f"[+] Candidate: '{b_title}' by {b_author} ({b_year}) "
              f"[{ext.upper()}, {size:,}B] | MD5: {md5}")

        clean_author = re.sub(r'[\\/:;,\?"*|<>~]', '', b_author or '').strip()
        clean_title = re.sub(r'[\\/:;,\?"*|<>~]', '', b_title or '').strip()
        final_name = f"{clean_author} - {clean_title}.{ext}" if clean_author else f"{clean_title}.{ext}"

        temp_dir = tempfile.mkdtemp(prefix='grab_')
        try:
            got = None
            tpath, idx, relpath = find_torrent_and_idx(source, md5, source_id)
            if tpath:
                print(f"[*] Torrent: {os.path.basename(tpath)} file #{idx} ({relpath})")
                got = download_via_torrent(tpath, idx, relpath, md5, temp_dir)
            else:
                print(f"[~] No local torrent manifest for MD5 {md5}")

            if not got:
                print(f"[*] Falling back to HTTP gateways...")
                got = download_via_gateway(md5, temp_dir)

            if not got:
                print(f"[-] Could not download candidate {md5}, trying next...")
                continue

            ok, reason = validate_book(got, ext, md5, size)
            if not ok:
                print(f"[✘] REJECTED (not ingested): {reason}")
                continue

            final_path = os.path.join(dest_dir, final_name)
            shutil.copy2(got, final_path)
            print(f"[✔] Verified & ingested: {final_path} ({os.path.getsize(final_path):,} bytes) — {reason}")
            return True
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"[-] All {len(results)} candidate(s) for '{title}' failed download or verification.")
    return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: grab_book.py <title> [author]")
        sys.exit(1)

    ok = grab_book(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    sys.exit(0 if ok else 1)
