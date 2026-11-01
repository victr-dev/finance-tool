"""Local web server for the finance app. Standard library only.

python server.py          start the server (http://localhost:8765)
python server.py --open   start it if needed (restarting an outdated one) and open the browser
"""
import csv
import io
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
import zipfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import calc  # noqa: E402
import db  # noqa: E402

PORT = int(os.environ.get("FINANCE_PORT", "8765"))
URL = f"http://localhost:{PORT}/"
STATIC = os.path.join(HERE, "static")
LOG = os.path.join(os.path.dirname(db.DB_PATH), "server.log")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}
LOGS = ("shifts", "transactions", "income")
ARCHIVABLE = ("accounts", "funds", "debts")


def code_version():
    """Changes whenever any app file changes, so the launcher can restart an outdated server."""
    newest = 0.0
    for base, _, files in os.walk(HERE):
        if "__pycache__" in base or "tests" in base:
            continue
        for f in files:
            newest = max(newest, os.path.getmtime(os.path.join(base, f)))
    return str(int(newest))


VERSION = code_version()
LOCK = threading.RLock()
CON = None
_last_backup = 0.0


def today():
    t = os.environ.get("FINANCE_TODAY")
    return date.fromisoformat(t) if t else date.today()


def snapshot():
    return calc.Data(CON, today=today())


def maybe_backup(force=False):
    global _last_backup
    if force or time.time() - _last_backup > 600:
        try:
            path = db.backup(CON)
            _last_backup = time.time()
            return path
        except Exception:
            log(traceback.format_exc())
    return None


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


# ------------------------------------------------------------------ list endpoints
def lists():
    s = db.get_settings(CON)
    return {
        "settings": s, "today": today().isoformat(), "version": VERSION,
        "accounts": db.rows(CON, "SELECT * FROM accounts ORDER BY sort, id"),
        "funds": db.rows(CON, "SELECT * FROM funds ORDER BY sort, id"),
        "debts": db.rows(CON, "SELECT * FROM debts ORDER BY sort, id"),
        "budget_items": db.rows(CON, "SELECT * FROM budget_items ORDER BY sort, id"),
        "shift_types": db.rows(CON, "SELECT * FROM shift_types ORDER BY sort, id"),
        "income_types": db.rows(CON, "SELECT * FROM income_types ORDER BY sort, id"),
    }


def log_rows(table, month=None):
    data = snapshot()
    name = lambda t, i: data.names.get((t, i)) if t else None
    if month:
        a = calc.parse_month(month)
        inside = lambda x: calc.month_start(x["d"]) == a
    else:
        inside = lambda x: True
    out = []
    if table == "shifts":
        for s in data.shifts:
            if inside(s):
                dest = data.shift_dest(s)
                out.append({**{k: v for k, v in s.items() if k != "d"}, "type": data.type_name.get(s["type_id"]),
                            "weekday": calc.WEEKDAYS[s["d"].weekday()], "dest_name": name(*dest) if dest else None,
                            "per_hour": s["tips"] / s["hours"] if s["tips"] is not None and s["hours"] else None})
    elif table == "transactions":
        groups = {i["id"]: i["grp"] for i in data.items}
        for t in data.txs:
            if inside(t):
                grp = {"item": groups.get(t["cat_id"]), "debt": "Debt payment", "fund": "Savings",
                       "account": "Transfer"}.get(t["cat_type"], "Uncategorized")
                out.append({**{k: v for k, v in t.items() if k != "d"}, "cat_name": name(t["cat_type"], t["cat_id"]),
                            "src_name": name(t["src_type"], t["src_id"]), "group": grp})
    else:
        for i in data.incomes:
            if inside(i):
                dest = data.income_dest(i)
                t = data.income_types.get(i["type_id"])
                out.append({**{k: v for k, v in i.items() if k != "d"}, "type": t["name"] if t else None,
                            "dest_name": name(*dest) if dest else None})
    out.sort(key=lambda r: (r["date"], r["id"]), reverse=table != "shifts")
    return out


def bulk_shifts(body):
    start, end = date.fromisoformat(body["start"]), date.fromisoformat(body["end"])
    pattern = {int(k): v for k, v in body["pattern"].items() if v}   # weekday (0=Mon) -> shift type id
    if (end - start).days > 400:
        raise db.ValidationError("Pick a range shorter than a year.")
    existing = {(r["date"], r["type_id"]) for r in db.rows(CON, "SELECT date, type_id FROM shifts")}
    n = 0
    day = start
    while day <= end:
        t = pattern.get(day.weekday())
        if t and (day.isoformat(), int(t)) not in existing:
            db.insert(CON, "shifts", {"date": day.isoformat(), "type_id": t})
            n += 1
        day = date.fromordinal(day.toordinal() + 1)
    return {"added": n}


def export_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in list(db.TABLES) + ["month_overrides", "settings"]:
            rs = db.rows(CON, f"SELECT * FROM {table}")
            if table in LOGS:
                rs = log_rows(table)
            s = io.StringIO()
            if rs:
                w = csv.DictWriter(s, fieldnames=list(rs[0].keys()))
                w.writeheader()
                w.writerows(rs)
            z.writestr(f"{table}.csv", s.getvalue())
    return buf.getvalue()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json", headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        try:
            if not parts or parts[0] != "api":
                return self.static(u.path) if method == "GET" else self.send(404, {"error": "Not found"})
            with LOCK:
                result = self.api(method, parts[1:], q)
            if isinstance(result, tuple):
                return self.send(*result)
            self.send(200, result)
        except db.ValidationError as e:
            self.send(400, {"error": str(e)})
        except Exception as e:  # keep the app alive and show the message in the browser
            log(traceback.format_exc())
            self.send(500, {"error": f"Something went wrong: {e}"})

    def api(self, method, p, q):
        key = p[0] if p else ""
        if method == "GET":
            if key == "ping":
                return {"ok": True, "version": VERSION}
            if key == "lists":
                return lists()
            if key == "home":
                return calc.home(snapshot())
            if key == "month":
                return calc.month_view(snapshot(), q.get("m"))
            if key == "overview":
                return calc.overview(snapshot(), float(q.get("extra") or 0))
            if key == "insights":
                return calc.insights(snapshot())
            if key == "export":
                name = f"finance-export-{today().isoformat()}.zip"
                return (200, export_zip(), "application/zip", {"Content-Disposition": f'attachment; filename="{name}"'})
            if key == "t" and len(p) >= 2 and p[1] in db.TABLES:
                if p[1] in LOGS:
                    return log_rows(p[1], q.get("month"))
                return db.rows(CON, f"SELECT * FROM {p[1]} ORDER BY sort, id")
        else:
            if key == "shutdown" and method == "POST":
                threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
                return {"ok": True}
            result = self.mutate(method, key, p, q)
            if result is not None:
                maybe_backup()
                return result
        return (404, {"error": "Not found"})

    def mutate(self, method, key, p, q):
        b = self.body()
        if key == "t" and len(p) >= 2 and p[1] in db.TABLES:
            table = p[1]
            if method == "POST":
                return {"id": db.insert(CON, table, b)}
            rid = int(p[2])
            if method == "PUT":
                db.update(CON, table, rid, b)
                return {"ok": True}
            if method == "DELETE":
                mode = q.get("mode", "check")
                if mode == "check":
                    return {"refs": db.ref_counts(CON, table, rid)}
                if mode == "archive" and table in ARCHIVABLE:
                    db.update(CON, table, rid, {"archived": 1})
                elif mode == "end" and table == "budget_items":
                    db.update(CON, table, rid, {"end_month": q["end"]})
                else:
                    db.delete(CON, table, rid)
                return {"ok": True}
        if key == "override" and method == "PUT":
            if b.get("ref_type") not in ("item", "debt", "fund", "extra"):
                raise db.ValidationError("Unknown line type.")
            db.set_override(CON, b["month"], b["ref_type"], b["ref_id"], b.get("amount"))
            return {"ok": True}
        if key == "settings" and method == "PUT":
            db.set_settings(CON, b)
            return {"ok": True}
        if key == "shifts-bulk" and method == "POST":
            return bulk_shifts(b)
        if key == "backup" and method == "POST":
            return {"path": maybe_backup(force=True)}
        return None

    def static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/").removeprefix("static/")
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        with open(full, "rb") as fh:
            self.send(200, fh.read(), MIME.get(os.path.splitext(full)[1], "application/octet-stream"))


# ------------------------------------------------------------------ startup
def running_version():
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=1.5) as r:
            return json.loads(r.read())["version"]
    except Exception:
        return None


def stop_running():
    try:
        req = urllib.request.Request(URL + "api/shutdown", data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
    for _ in range(40):
        if running_version() is None:
            return
        time.sleep(0.1)


def serve(open_browser=False):
    global CON
    CON = db.connect()
    maybe_backup(force=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"started v{VERSION} on {URL}")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(URL)).start()
    httpd.serve_forever()


def main():
    if sys.stdout is None or sys.stderr is None:   # started with pythonw: no console
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8")
    want_open = "--open" in sys.argv
    current = running_version()
    if current == VERSION:
        if want_open:
            webbrowser.open(URL)
        return
    if current is not None:
        stop_running()
    serve(open_browser=want_open)


if __name__ == "__main__":
    main()
