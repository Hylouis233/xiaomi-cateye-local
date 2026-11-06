"""Persist received device events separately from Frigate object detections."""
import os
import sqlite3
import time
from pathlib import Path

DB = Path('/config/.storage/cateye_local/events.sqlite3')
LABELS = {'1': '门铃响了', '3': '有人经过', '4': '有人停留', 'viewer': '实时查看'}

def connect():
    c = sqlite3.connect(DB, timeout=5)
    os.chmod(DB, 0o600)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at REAL, kind TEXT, label TEXT, source TEXT)')
    return c

def record(kind, at=None, source='mijia_event'):
    at = float(at or time.time())
    kind = str(kind)
    key = f'{source}-{kind}-{round(at * 1000)}'
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)', (key, at, kind, LABELS.get(kind, '设备事件'), source))
        c.execute('DELETE FROM events WHERE at < ?', (time.time() - 90 * 86400,))
    return key

def read(after, before):
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM events WHERE at>=? AND at<? ORDER BY at DESC LIMIT 200', (after, before))]
