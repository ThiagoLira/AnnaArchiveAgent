#!/usr/bin/env python3
"""
Search the local Anna's Archive/LibGen catalog and print candidates for the
AGENT to evaluate. Does not download anything.

Usage:
  search_books.py "<title>" ["<author>"] [--limit N] [--json]

The agent picks the best MD5 from this list and passes it to fetch_md5.py.
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_book import DB_PATH, MANIFESTS_ROOT, find_torrent_and_idx  # noqa: E402

FIELDS = ('source', 'source_id', 'title', 'author', 'series', 'publisher',
          'year', 'isbn', 'extension', 'filesize', 'md5')


def search(title, author="", limit=10):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cols = ', '.join(FIELDS)
    clauses = []
    if author:
        clauses.append(("title LIKE ? AND author LIKE ?",
                        [f'%{title}%', f'%{author}%']))
    clauses.append(("title LIKE ?", [f'%{title}%']))
    words = [w for w in title.replace(':', ' ').replace('-', ' ').split() if len(w) > 2]
    if len(words) >= 2:
        c = " AND ".join("title LIKE ?" for _ in words)
        p = [f'%{w}%' for w in words]
        if author:
            c += " AND author LIKE ?"
            p.append(f'%{author}%')
        clauses.append((c, p))

    seen, rows = set(), []
    try:
        for clause, params in clauses:
            cur.execute(f"SELECT {cols} FROM books WHERE {clause} "
                        f"ORDER BY CASE WHEN extension='epub' THEN 1 "
                        f"WHEN extension='pdf' THEN 2 ELSE 3 END, filesize DESC "
                        f"LIMIT ?", (*params, limit))
            for row in cur.fetchall():
                d = dict(zip(FIELDS, row))
                if d['md5'] not in seen:
                    seen.add(d['md5'])
                    rows.append(d)
            if len(rows) >= limit:
                break
        return rows[:limit]
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('title')
    ap.add_argument('author', nargs='?', default='')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    rows = search(args.title, args.author, args.limit)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    if not rows:
        print(f"No catalog matches for '{args.title}'"
              + (f" by '{args.author}'" if args.author else ""))
        sys.exit(1)
    for d in rows:
        print(f"MD5 {d['md5']} | {d['extension'].upper():5} | {d['filesize']:>12,}B "
              f"| {d['year'] or '????'} | {d['title'][:60]} — {d['author'][:40]}"
              + (f" | {d['publisher'][:25]}" if d['publisher'] else ""))


if __name__ == '__main__':
    main()
