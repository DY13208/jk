#!/usr/bin/env python3
"""Snapshot store + diff for product monitoring.
Usage:
  diff_sqlite.py ingest <products.json> [--db DB]         store one run (keyed by tool+run, product_id)
  diff_sqlite.py diff --tool crawl4ai [--db DB] [--a RUN --b RUN]   diff latest two runs (or given run ids)
  diff_sqlite.py runs [--db DB]
Detects: new products, disappeared products, sale-price changes, original-price changes (and name/url changes)."""
import sqlite3, json, sys, argparse, datetime, os
from pathlib import Path
from zoneinfo import ZoneInfo
DB = os.getenv('DB_PATH', str(Path(__file__).resolve().parent / 'data' / 'snapshots.db'))
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT, run_tag TEXT,
  started TEXT, ended TEXT, duration_s REAL, site_total INTEGER, extracted INTEGER, source_file TEXT,
  ingested_at TEXT, UNIQUE(tool, started));
CREATE TABLE IF NOT EXISTS snapshots(run_id INTEGER REFERENCES runs(run_id), product_id TEXT, variant_id TEXT,
  name TEXT, sale_price REAL, original_price REAL, currency TEXT, discount TEXT, url TEXT,
  PRIMARY KEY(run_id, product_id));
CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT, run_a INTEGER, run_b INTEGER,
  product_id TEXT, change_type TEXT, field TEXT, old_value TEXT, new_value TEXT, detected_at TEXT);
"""
def conn(db):
    c = sqlite3.connect(db); c.executescript(SCHEMA); return c

def ingest(c, path, *, document=None, commit=True, quiet=False):
    d = document if document is not None else json.load(open(path, encoding='utf-8')); m = d["meta"]; tool = m.get("tool")
    cur = c.execute("INSERT OR IGNORE INTO runs(tool,run_tag,started,ended,duration_s,site_total,extracted,source_file,ingested_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (tool, m.get("run_tag"), m.get("started"), m.get("ended"), m.get("duration_s"), m.get("site_total"),
         len(d["products"]), path, datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()))
    if cur.rowcount == 0:
        if not quiet: print("already ingested:", path)
        return c.execute('SELECT run_id FROM runs WHERE tool=? AND started=?', (tool, m.get('started'))).fetchone()[0]
    rid = cur.lastrowid
    c.executemany("INSERT OR REPLACE INTO snapshots(run_id,product_id,variant_id,name,sale_price,original_price,currency,discount,url) VALUES(?,?,?,?,?,?,?,?,?)",
        [(rid, p["product_id"], p.get("product_variant_id"), p.get("name"), p.get("sale_price"), p.get("original_price"),
          p.get("currency"), p.get("discount"), p.get("url")) for p in d["products"] if p.get("product_id")])
    if commit: c.commit()
    if not quiet: print(f"ingested run_id={rid} tool={tool} tag={m.get('run_tag')} products={len(d['products'])}")
    return rid

def load(c, rid):
    return {r[0]: r for r in c.execute("SELECT product_id,name,sale_price,original_price,url FROM snapshots WHERE run_id=?", (rid,))}

def diff(c, tool, a=None, b=None, record=True, quiet=False, commit=True):
    if a is None or b is None:
        ids = [r[0] for r in c.execute("SELECT run_id FROM runs WHERE tool=? ORDER BY run_id DESC LIMIT 2", (tool,))]
        if len(ids) < 2: print("need >=2 runs for", tool); return None
        b, a = ids[0], ids[1]
    A, B = load(c, a), load(c, b)
    res = {"tool": tool, "run_a": a, "run_b": b, "count_a": len(A), "count_b": len(B),
           "new": sorted(set(B) - set(A)), "disappeared": sorted(set(A) - set(B)), "price_changes": [], "other_changes": []}
    for pid in sorted(set(A) & set(B)):
        for i, f in ((2, "sale_price"), (3, "original_price")):
            if A[pid][i] != B[pid][i]:
                res["price_changes"].append({"product_id": pid, "name": B[pid][1], "field": f, "old": A[pid][i], "new": B[pid][i]})
        for i, f in ((1, "name"), (4, "url")):
            if A[pid][i] != B[pid][i]:
                res["other_changes"].append({"product_id": pid, "field": f, "old": A[pid][i], "new": B[pid][i]})
    if record:
        now = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        rows = [(tool, a, b, p, "new", None, None, B[p][1], now) for p in res["new"]] + \
               [(tool, a, b, p, "disappeared", None, A[p][1], None, now) for p in res["disappeared"]] + \
               [(tool, a, b, x["product_id"], "price_change", x["field"], str(x["old"]), str(x["new"]), now) for x in res["price_changes"]] + \
               [(tool, a, b, x["product_id"], "field_change", x["field"], str(x["old"]), str(x["new"]), now) for x in res["other_changes"]]
        c.executemany("INSERT INTO changes(tool,run_a,run_b,product_id,change_type,field,old_value,new_value,detected_at) VALUES(?,?,?,?,?,?,?,?,?)", rows)
        if commit: c.commit()
    if not quiet: print(json.dumps({**res, "summary": {k: len(res[k]) for k in ("new", "disappeared", "price_changes", "other_changes")}}, ensure_ascii=False, indent=1))
    return res

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("path", nargs="?")
    ap.add_argument("--db", default=DB); ap.add_argument("--tool"); ap.add_argument("--a", type=int); ap.add_argument("--b", type=int)
    ap.add_argument("--no-record", action="store_true")
    x = ap.parse_args(); c = conn(x.db)
    if x.cmd == "ingest": ingest(c, x.path)
    elif x.cmd == "diff": diff(c, x.tool, x.a, x.b, not x.no_record)
    elif x.cmd == "runs":
        for r in c.execute("SELECT run_id,tool,run_tag,started,duration_s,site_total,extracted FROM runs ORDER BY run_id"): print(r)
