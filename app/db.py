"""SQLite storage: schema, connection, generic CRUD and reference handling."""
import json
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime


def data_dir():
    """Per-user folder outside the code, so personal data never sits in the repo."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Finance")


# FINANCE_DB points the app at another database (demo data, tests)
DB_PATH = os.path.abspath(os.environ.get("FINANCE_DB") or os.path.join(data_dir(), "finance.db"))
DEFAULT_BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, anchor_date TEXT, anchor_balance REAL DEFAULT 0,
  notes TEXT, sort INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS funds (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'Goal', target REAL, due_month INTEGER,
  target_date TEXT, monthly_amount REAL, held_at INTEGER, anchor_date TEXT, anchor_balance REAL DEFAULT 0,
  notes TEXT, sort INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS debts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, apr REAL DEFAULT 0, minimum REAL DEFAULT 0, due_day INTEGER,
  last_payment_month TEXT, extra_payoff INTEGER DEFAULT 1, anchor_date TEXT, anchor_balance REAL DEFAULT 0,
  notes TEXT, sort INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS budget_items (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, grp TEXT DEFAULT 'Needs', kind TEXT DEFAULT 'Bill', amount REAL DEFAULT 0,
  due_day INTEGER, start_month TEXT, end_month TEXT, notes TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS month_overrides (
  month TEXT NOT NULL, ref_type TEXT NOT NULL, ref_id INTEGER NOT NULL, amount REAL NOT NULL,
  PRIMARY KEY (month, ref_type, ref_id));
CREATE TABLE IF NOT EXISTS shift_types (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS income_types (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, counts INTEGER DEFAULT 1, dest_type TEXT, dest_id INTEGER,
  sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, type_id INTEGER, hours REAL, tips REAL,
  dest_type TEXT, dest_id INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT, amount REAL NOT NULL,
  cat_type TEXT, cat_id INTEGER, src_type TEXT, src_id INTEGER, unexpected INTEGER DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS income (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT, type_id INTEGER, amount REAL NOT NULL,
  dest_type TEXT, dest_id INTEGER, notes TEXT);
CREATE INDEX IF NOT EXISTS ix_shifts_date ON shifts(date);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_income_date ON income(date);
"""

DEFAULT_SETTINGS = {
    "goal": 3000, "tolerance": 50, "strategy": "Avalanche", "typical_shifts": 20,
    "start_month": date.today().strftime("%Y-%m"), "default_tips_account": None, "backup_dir": DEFAULT_BACKUP_DIR,
}

# column -> kind. "!" = required. Used to coerce and validate values coming from the browser.
TABLES = {
    "accounts": {"name": "text!", "type": "text", "anchor_date": "date", "anchor_balance": "num", "notes": "text",
                 "sort": "int", "archived": "bool"},
    "funds": {"name": "text!", "kind": "text", "target": "num", "due_month": "int", "target_date": "date",
              "monthly_amount": "num", "held_at": "int", "anchor_date": "date", "anchor_balance": "num",
              "notes": "text", "sort": "int", "archived": "bool"},
    "debts": {"name": "text!", "type": "text", "apr": "num", "minimum": "num", "due_day": "int",
              "last_payment_month": "month", "extra_payoff": "bool", "anchor_date": "date", "anchor_balance": "num",
              "notes": "text", "sort": "int", "archived": "bool"},
    "budget_items": {"name": "text!", "grp": "text", "kind": "text", "amount": "num", "due_day": "int",
                     "start_month": "month", "end_month": "month", "notes": "text", "sort": "int"},
    "shift_types": {"name": "text!", "sort": "int"},
    "income_types": {"name": "text!", "counts": "bool", "dest_type": "ref", "dest_id": "int", "sort": "int"},
    "shifts": {"date": "date!", "type_id": "int", "hours": "num", "tips": "num", "dest_type": "ref", "dest_id": "int",
               "notes": "text"},
    "transactions": {"date": "date!", "description": "text", "amount": "num!", "cat_type": "ref", "cat_id": "int",
                     "src_type": "ref", "src_id": "int", "unexpected": "bool", "notes": "text"},
    "income": {"date": "date!", "description": "text", "type_id": "int", "amount": "num!", "dest_type": "ref",
               "dest_id": "int", "notes": "text"},
}
REF_TYPES = {"account", "fund", "debt", "item"}
# which ref_type each master table is referred to by
REF_OF = {"accounts": "account", "funds": "fund", "debts": "debt", "budget_items": "item"}


class ValidationError(Exception):
    pass


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
    con.commit()
    return con


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def get_settings(con):
    return {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM settings")}


def set_settings(con, values):
    for k, v in values.items():
        if k not in DEFAULT_SETTINGS:
            raise ValidationError(f"Unknown setting: {k}")
        if k == "strategy" and v not in ("Avalanche", "Snowball"):
            raise ValidationError("Strategy must be Avalanche or Snowball.")
        if k in ("goal", "tolerance", "typical_shifts") and not isinstance(v, (int, float)):
            raise ValidationError(f"{k.replace('_', ' ').capitalize()} must be a number.")
        if k == "start_month":
            _coerce("month!", v, "start month")
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
    con.commit()


def _coerce(kind, value, col):
    required = kind.endswith("!")
    kind = kind.rstrip("!")
    if value in (None, ""):
        if required:
            raise ValidationError(f"{col.replace('_', ' ').capitalize()} is required.")
        return None
    try:
        if kind == "text":
            return str(value).strip() or None
        if kind == "num":
            return round(float(str(value).replace("$", "").replace(",", "")), 6)
        if kind == "int":
            return int(float(value))
        if kind == "bool":
            return 1 if value in (True, 1, "1", "true", "on", "Yes") else 0
        if kind == "date":
            return date.fromisoformat(str(value)[:10]).isoformat()
        if kind == "month":
            s = str(value)[:7]
            datetime.strptime(s, "%Y-%m")
            return s
        if kind == "ref":
            if value not in REF_TYPES:
                raise ValueError
            return value
    except (ValueError, TypeError):
        raise ValidationError(f"'{value}' isn't a valid {col.replace('_', ' ')}.")
    return value


def clean(table, data, partial=False):
    spec = TABLES[table]
    out = {}
    for col, kind in spec.items():
        if col in data:
            out[col] = _coerce(kind, data[col], col)
        elif not partial and kind.endswith("!"):
            raise ValidationError(f"{col.replace('_', ' ').capitalize()} is required.")
    return out


def insert(con, table, data):
    vals = clean(table, data)
    cols = list(vals)
    cur = con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      [vals[c] for c in cols])
    con.commit()
    return cur.lastrowid


def update(con, table, rid, data):
    vals = clean(table, data, partial=True)
    if not vals:
        return
    con.execute(f"UPDATE {table} SET {', '.join(f'{c}=?' for c in vals)} WHERE id=?", [*vals.values(), rid])
    con.commit()


def ref_counts(con, table, rid):
    """How many logged entries point at this record (used before deleting)."""
    if table in REF_OF:
        t = REF_OF[table]
        n = con.execute("SELECT COUNT(*) FROM transactions WHERE (cat_type=? AND cat_id=?) OR (src_type=? AND src_id=?)",
                        (t, rid, t, rid)).fetchone()[0]
        n += con.execute("SELECT COUNT(*) FROM income WHERE dest_type=? AND dest_id=?", (t, rid)).fetchone()[0]
        n += con.execute("SELECT COUNT(*) FROM shifts WHERE dest_type=? AND dest_id=?", (t, rid)).fetchone()[0]
        return n
    if table == "shift_types":
        return con.execute("SELECT COUNT(*) FROM shifts WHERE type_id=?", (rid,)).fetchone()[0]
    if table == "income_types":
        return con.execute("SELECT COUNT(*) FROM income WHERE type_id=?", (rid,)).fetchone()[0]
    return 0


def delete(con, table, rid):
    """Hard delete. Logged entries that pointed here become uncategorized / fall back to defaults."""
    if table in REF_OF:
        t = REF_OF[table]
        con.execute("UPDATE transactions SET cat_type=NULL, cat_id=NULL WHERE cat_type=? AND cat_id=?", (t, rid))
        con.execute("UPDATE transactions SET src_type=NULL, src_id=NULL WHERE src_type=? AND src_id=?", (t, rid))
        con.execute("UPDATE income SET dest_type=NULL, dest_id=NULL WHERE dest_type=? AND dest_id=?", (t, rid))
        con.execute("UPDATE shifts SET dest_type=NULL, dest_id=NULL WHERE dest_type=? AND dest_id=?", (t, rid))
        con.execute("UPDATE income_types SET dest_type=NULL, dest_id=NULL WHERE dest_type=? AND dest_id=?", (t, rid))
        con.execute("DELETE FROM month_overrides WHERE ref_type=? AND ref_id=?", (t, rid))
        if table == "accounts":
            con.execute("UPDATE funds SET held_at=NULL WHERE held_at=?", (rid,))
            s = get_settings(con)
            if s.get("default_tips_account") == rid:
                set_settings(con, {"default_tips_account": None})
    elif table in ("shift_types", "income_types") and ref_counts(con, table, rid):
        raise ValidationError("This type is used by logged entries. Rename it instead of deleting it.")
    con.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    con.commit()


def set_override(con, month, ref_type, ref_id, amount):
    if amount in (None, ""):
        con.execute("DELETE FROM month_overrides WHERE month=? AND ref_type=? AND ref_id=?", (month, ref_type, ref_id))
    else:
        con.execute("INSERT OR REPLACE INTO month_overrides VALUES (?, ?, ?, ?)",
                    (month, ref_type, int(ref_id), float(amount)))
    con.commit()


def backup(con, folder=None, keep=30):
    """Copy the database to the backup folder (one file per day, newest 30 kept)."""
    folder = folder or get_settings(con).get("backup_dir") or DEFAULT_BACKUP_DIR
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"finance-{date.today().isoformat()}.db")
    tmp = target + ".tmp"
    dst = sqlite3.connect(tmp)
    with dst:
        con.backup(dst)
    dst.close()
    shutil.move(tmp, target)
    old = sorted(f for f in os.listdir(folder) if f.startswith("finance-") and f.endswith(".db"))
    for f in old[:-keep]:
        os.remove(os.path.join(folder, f))
    return target
