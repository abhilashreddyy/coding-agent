"""A small expense tracker web app using only the Python standard library.

Run:  python expense_tracker.py [port]
Then open http://localhost:8000 in a browser.
"""

import json
import os
import sys
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expenses.json")
_lock = threading.Lock()


# ---------- storage ----------

def load_expenses(path=DATA_FILE):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_expenses(expenses, path=DATA_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(expenses, f, indent=2)
    os.replace(tmp, path)


def add_expense(amount, category, note, path=DATA_FILE):
    """Validate and store one expense. Raises ValueError on bad input."""
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    category = (category or "").strip()
    if not category:
        raise ValueError("Category is required.")
    note = (note or "").strip()
    with _lock:
        expenses = load_expenses(path)
        next_id = max((e["id"] for e in expenses), default=0) + 1
        expense = {"id": next_id, "amount": amount, "category": category, "note": note}
        expenses.append(expense)
        save_expenses(expenses, path)
    return expense


def totals_by_category(expenses):
    totals = {}
    for e in expenses:
        totals[e["category"]] = round(totals.get(e["category"], 0) + e["amount"], 2)
    return dict(sorted(totals.items()))


# ---------- HTML ----------

def render_page(expenses, error=""):
    rows = "\n".join(
        f"<tr><td>{escape(str(e['id']))}</td><td>{e['amount']:.2f}</td>"
        f"<td>{escape(e['category'])}</td><td>{escape(e['note'])}</td></tr>"
        for e in expenses
    ) or '<tr><td colspan="4">No expenses yet.</td></tr>'
    totals = totals_by_category(expenses)
    total_rows = "\n".join(
        f"<tr><td>{escape(c)}</td><td>{t:.2f}</td></tr>" for c, t in totals.items()
    ) or '<tr><td colspan="2">No data.</td></tr>'
    grand = sum(e["amount"] for e in expenses)
    err_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Expense Tracker</title>
<style>
body {{ font-family: sans-serif; max-width: 800px; margin: 2em auto; padding: 0 1em; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; }}
th, td {{ border: 1px solid #ccc; padding: 0.4em; text-align: left; }}
th {{ background: #f0f0f0; }}
.error {{ color: #b00020; }}
</style>
</head>
<body>
<h1>Expense Tracker</h1>
{err_html}
<h2>Add expense</h2>
<form method="post" action="/add">
  <label>Amount <input name="amount" type="number" step="0.01" min="0.01" required></label>
  <label>Category <input name="category" required></label>
  <label>Note <input name="note"></label>
  <button type="submit">Add</button>
</form>
<h2>Totals per category</h2>
<table>
<tr><th>Category</th><th>Total</th></tr>
{total_rows}
<tr><th>All categories</th><th>{grand:.2f}</th></tr>
</table>
<h2>All expenses</h2>
<table>
<tr><th>ID</th><th>Amount</th><th>Category</th><th>Note</th></tr>
{rows}
</table>
</body>
</html>
"""


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    data_path = DATA_FILE

    def _send(self, status, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] != "/":
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        expenses = load_expenses(self.data_path)
        self._send(200, render_page(expenses))

    def do_POST(self):
        if self.path != "/add":
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        get = lambda k: form.get(k, [""])[0]
        try:
            add_expense(get("amount"), get("category"), get("note"), self.data_path)
        except ValueError as exc:
            expenses = load_expenses(self.data_path)
            self._send(400, render_page(expenses, str(exc)))
            return
        self._send(303, "", headers={"Location": "/"})

    def log_message(self, fmt, *args):
        pass


def make_server(port=8000, data_path=DATA_FILE):
    handler = type("BoundHandler", (Handler,), {"data_path": data_path})
    return HTTPServer(("127.0.0.1", port), handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = make_server(port)
    print(f"Expense tracker running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
