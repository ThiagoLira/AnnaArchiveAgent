import subprocess
import csv
import sqlite3
import time
import os
import sys

def build_catalog():
    db_path = '/mnt/storage/annas_archive/metadata/catalog.db'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Fast SQLite settings for bulk insertion
    cur.execute('PRAGMA journal_mode = OFF;')
    cur.execute('PRAGMA synchronous = 0;')
    cur.execute('PRAGMA cache_size = 1000000;')
    cur.execute('PRAGMA locking_mode = EXCLUSIVE;')
    cur.execute('PRAGMA temp_store = MEMORY;')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        source_id INTEGER,
        title TEXT,
        author TEXT,
        series TEXT,
        publisher TEXT,
        year TEXT,
        isbn TEXT,
        filesize INTEGER,
        extension TEXT,
        md5 TEXT UNIQUE,
        locator TEXT
    );
    ''')
    conn.commit()

    # 1. Ingest LibGen Fiction
    fiction_path = '/mnt/storage/annas_archive/metadata/aa_derived_mirror_metadata_20260208/mariadb/allthethings.libgenrs_fiction.00000.dat.gz'
    if os.path.exists(fiction_path):
        print("\n" + "="*70)
        print(" [1/2] Ingesting LibGen Fiction (~3 million records)...")
        print("="*70)
        
        proc = subprocess.Popen(['pigz', '-dc', fiction_path], stdout=subprocess.PIPE, text=True, errors='replace', bufsize=1024*1024*16)
        reader = csv.reader(proc.stdout)
        header = next(reader)
        
        batch = []
        count = 0
        t0 = time.time()
        t_last = t0
        
        for row in reader:
            if len(row) >= 19:
                try:
                    sid = int(row[0])
                    md5 = row[1].lower().strip()
                    title = row[2].strip()
                    author = row[3].strip()
                    series = row[4].strip()
                    year = row[7].strip()
                    pub = row[8].strip()
                    isbn = row[10].strip()
                    ext = row[14].strip().lower()
                    sz = int(row[15]) if row[15].isdigit() else 0
                    locator = row[18].strip()
                    
                    batch.append(('libgen_rs_fic', sid, title, author, series, pub, year, isbn, sz, ext, md5, locator))
                    count += 1
                    
                    if len(batch) >= 100000:
                        cur.executemany('''
                            INSERT OR IGNORE INTO books (source, source_id, title, author, series, publisher, year, isbn, filesize, extension, md5, locator)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', batch)
                        conn.commit()
                        batch = []
                        
                        elapsed = time.time() - t0
                        rate = count / elapsed if elapsed > 0 else 0
                        print(f" -> Fiction: {count:,} rows inserted ({rate:,.0f} rows/sec) [{elapsed:.1f}s]", flush=True)
                except Exception:
                    continue
        
        if batch:
            cur.executemany('''
                INSERT OR IGNORE INTO books (source, source_id, title, author, series, publisher, year, isbn, filesize, extension, md5, locator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
        proc.wait()
        print(f"[+] Done LibGen Fiction: {count:,} rows inserted in {time.time()-t0:.1f}s")

    # 2. Ingest LibGen Non-Fiction
    nf_path = '/mnt/storage/annas_archive/metadata/aa_derived_mirror_metadata_20260208/mariadb/allthethings.libgenrs_updated.00000.dat.gz'
    if os.path.exists(nf_path):
        print("\n" + "="*70)
        print(" [2/2] Ingesting LibGen Non-Fiction (~4.5 million records)...")
        print("="*70)
        
        proc = subprocess.Popen(['pigz', '-dc', nf_path], stdout=subprocess.PIPE, text=True, errors='replace', bufsize=1024*1024*16)
        reader = csv.reader(proc.stdout)
        header = next(reader)
        
        batch = []
        count = 0
        t0 = time.time()
        
        for row in reader:
            if len(row) >= 41:
                try:
                    sid = int(row[0])
                    title = row[1].strip()
                    series = row[3].strip()
                    author = row[5].strip()
                    year = row[6].strip()
                    pub = row[8].strip()
                    isbn = row[16].strip()
                    sz = int(row[35]) if row[35].isdigit() else 0
                    ext = row[36].strip().lower()
                    md5 = row[37].lower().strip()
                    locator = row[40].strip()
                    
                    batch.append(('libgen_rs_non_fic', sid, title, author, series, pub, year, isbn, sz, ext, md5, locator))
                    count += 1
                    
                    if len(batch) >= 100000:
                        cur.executemany('''
                            INSERT OR IGNORE INTO books (source, source_id, title, author, series, publisher, year, isbn, filesize, extension, md5, locator)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', batch)
                        conn.commit()
                        batch = []
                        
                        elapsed = time.time() - t0
                        rate = count / elapsed if elapsed > 0 else 0
                        print(f" -> Non-Fiction: {count:,} rows inserted ({rate:,.0f} rows/sec) [{elapsed:.1f}s]", flush=True)
                except Exception:
                    continue
        
        if batch:
            cur.executemany('''
                INSERT OR IGNORE INTO books (source, source_id, title, author, series, publisher, year, isbn, filesize, extension, md5, locator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
        proc.wait()
        print(f"[+] Done LibGen Non-Fiction: {count:,} rows inserted in {time.time()-t0:.1f}s")

    # 3. Create Indexes
    print("\n" + "="*70)
    print(" Building Fast Lookup B-Tree Indexes...")
    print("="*70)
    t0 = time.time()
    cur.execute('CREATE INDEX IF NOT EXISTS idx_books_md5 ON books(md5);')
    print(" -> MD5 index built", flush=True)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);')
    print(" -> Title index built", flush=True)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_books_author ON books(author COLLATE NOCASE);')
    print(" -> Author index built", flush=True)
    conn.commit()
    print(f"[+] All indexes built in {time.time()-t0:.1f}s")

    cur.execute('SELECT COUNT(*) FROM books;')
    total = cur.fetchone()[0]
    print(f"\n[🎉] Catalog Database Ready! Total Books Indexed: {total:,}")
    conn.close()

if __name__ == '__main__':
    build_catalog()
