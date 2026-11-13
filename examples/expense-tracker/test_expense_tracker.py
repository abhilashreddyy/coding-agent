import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

import expense_tracker as et


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "expenses.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_add_and_load(self):
        e = et.add_expense("12.5", "Food", "lunch", self.path)
        self.assertEqual(e["id"], 1)
        self.assertEqual(e["amount"], 12.5)
        stored = et.load_expenses(self.path)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["category"], "Food")

    def test_ids_increase(self):
        et.add_expense("1", "A", "", self.path)
        e2 = et.add_expense("2", "B", "", self.path)
        self.assertEqual(e2["id"], 2)

    def test_rejects_bad_amount(self):
        for bad in ["abc", "0", "-5", ""]:
            with self.assertRaises(ValueError):
                et.add_expense(bad, "Food", "", self.path)

    def test_rejects_empty_category(self):
        with self.assertRaises(ValueError):
            et.add_expense("5", "   ", "", self.path)

    def test_totals_by_category(self):
        expenses = [
            {"id": 1, "amount": 10.0, "category": "Food", "note": ""},
            {"id": 2, "amount": 2.5, "category": "Food", "note": ""},
            {"id": 3, "amount": 7.0, "category": "Rent", "note": ""},
        ]
        self.assertEqual(et.totals_by_category(expenses), {"Food": 12.5, "Rent": 7.0})


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "expenses.json")
        self.server = et.make_server(0, self.path)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def post(self, data):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(self.base + "/add", data=body, method="POST")
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_page_lists_and_totals(self):
        self.assertEqual(self.post({"amount": "3.00", "category": "Food", "note": "apple"})[0], 303)
        self.post({"amount": "4.00", "category": "Food", "note": "bread"})
        with urllib.request.urlopen(self.base + "/") as r:
            page = r.read().decode()
        self.assertIn("apple", page)
        self.assertIn("bread", page)
        self.assertIn("7.00", page)  # Food total

    def test_invalid_post_shows_error(self):
        status, body = self.post({"amount": "-1", "category": "Food"})
        self.assertEqual(status, 400)
        self.assertIn("greater than zero", body)
        self.assertEqual(et.load_expenses(self.path), [])

    def test_escapes_html(self):
        self.post({"amount": "1", "category": "<b>x</b>", "note": ""})
        with urllib.request.urlopen(self.base + "/") as r:
            page = r.read().decode()
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_303(self, req, fp, code, msg, headers):
        return fp


if __name__ == "__main__":
    unittest.main()
