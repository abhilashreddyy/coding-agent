#!/usr/bin/env python3
"""Small expense tracker web app using only the Python standard library.

Data is stored in expenses.json next to this file.
Run with: python3 expense_tracker.py [port]
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "expenses.json")
_lock = threading.Lock()


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


def validate_expense(data):
    """Return (clean_expense, error_message). error_message is None when valid."""
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return None, "Amount must be a number."
    if amount != amount or amount in (float("inf"), float("-inf")) or amount <= 0:
        return None, "Amount must be a positive number."
    category = str(data.get("category", "")).strip()
    if not category:
        return None, "Category is required."
    note = str(data.get("note", "")).strip()
    return {"amount": round(amount, 2), "category": category, "note": note}, None


def add_expense(expense, path=DATA_FILE):
    with _lock:
        expenses = load_expenses(path)
        expense = dict(expense)
        expense["id"] = max((e.get("id", 0) for e in expenses), default=0) + 1
        expenses.append(expense)
        save_expenses(expenses, path)
        return expense


def totals_by_category(expenses):
    totals = {}
    for e in expenses:
        totals[e["category"]] = round(totals.get(e["category"], 0) + e["amount"], 2)
    return dict(sorted(totals.items()))


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Expense Tracker</title>
<style>
body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; }
h1 { margin-bottom: .2rem; }
fieldset { border: 1px solid #ccc; border-radius: 6px; padding: 1rem; margin-bottom: 1.5rem; }
input { padding: .4rem; margin: .2rem .4rem .2rem 0; }
button { padding: .4rem 1rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
th, td { text-align: left; padding: .4rem; border-bottom: 1px solid #eee; }
td.num, th.num { text-align: right; }
#error { color: #b00020; min-height: 1.2rem; }
</style>
</head>
<body>
<h1>Expense Tracker</h1>

<fieldset>
<legend>Add expense</legend>
<form id="form">
<input id="amount" type="number" step="0.01" min="0.01" placeholder="Amount" required>
<input id="category" type="text" placeholder="Category" required>
<input id="note" type="text" placeholder="Note (optional)">
<button type="submit">Add</button>
</form>
<div id="error"></div>
</fieldset>

<h2>Totals by category</h2>
<table>
<thead><tr><th>Category</th><th class="num">Total</th></tr></thead>
<tbody id="totals"></tbody>
</table>

<h2>All expenses</h2>
<table>
<thead><tr><th>Category</th><th class="num">Amount</th><th>Note</th></tr></thead>
<tbody id="expenses"></tbody>
</table>

<script>
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function refresh() {
  const res = await fetch('/api/expenses');
  const data = await res.json();
  document.getElementById('totals').innerHTML = Object.entries(data.totals)
    .map(([c, t]) => `<tr><td>${esc(c)}</td><td class="num">${t.toFixed(2)}</td></tr>`).join('')
    || '<tr><td colspan="2">No expenses yet.</td></tr>';
  document.getElementById('expenses').innerHTML = data.expenses.slice().reverse()
    .map(e => `<tr><td>${esc(e.category)}</td><td class="num">${e.amount.toFixed(2)}</td><td>${esc(e.note)}</td></tr>`).join('')
    || '<tr><td colspan="3">No expenses yet.</td></tr>';
}
document.getElementById('form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const body = {
    amount: document.getElementById('amount').value,
    category: document.getElementById('category').value,
    note: document.getElementById('note').value,
  };
  const res = await fetch('/api/expenses', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const err = document.getElementById('error');
  if (!res.ok) {
    err.textContent = (await res.json()).error || 'Could not add expense.';
    return;
  }
  err.textContent = '';
  ev.target.reset();
  document.getElementById('category').focus();
  refresh();
});
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    data_file = DATA_FILE

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/expenses":
            with _lock:
                expenses = load_expenses(self.data_file)
            self._send_json(200, {"expenses": expenses, "totals": totals_by_category(expenses)})
        else:
            self._send_json(404, {"error": "Not found."})

    def do_POST(self):
        if urlparse(self.path).path != "/api/expenses":
            self._send_json(404, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "Invalid JSON body."})
            return
        expense, error = validate_expense(data)
        if error:
            self._send_json(400, {"error": error})
            return
        saved = add_expense(expense, self.data_file)
        self._send_json(201, saved)

    def log_message(self, fmt, *args):
        pass


def make_server(port=8000, data_file=DATA_FILE):
    handler = type("BoundHandler", (Handler,), {"data_file": data_file})
    return HTTPServer(("127.0.0.1", port), handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = make_server(port)
    print(f"Expense tracker running at http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
