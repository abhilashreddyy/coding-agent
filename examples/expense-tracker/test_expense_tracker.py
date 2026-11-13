import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

import expense_tracker as et


class ValidateTests(unittest.TestCase):
    def test_valid_expense(self):
        clean, err = et.validate_expense({"amount": "12.345", "category": " Food ", "note": "lunch"})
        self.assertIsNone(err)
        self.assertEqual(clean, {"amount": round(12.345, 2), "category": "Food", "note": "lunch"})

    def test_rejects_bad_amount(self):
        for bad in ({"amount": "abc", "category": "x"}, {"amount": -5, "category": "x"}, {"amount": 0, "category": "x"}):
            _, err = et.validate_expense(bad)
            self.assertIsNotNone(err)

    def test_requires_category(self):
        _, err = et.validate_expense({"amount": 3, "category": "  "})
        self.assertEqual(err, "Category is required.")


class TotalsTests(unittest.TestCase):
    def test_totals_by_category(self):
        expenses = [
            {"amount": 10.0, "category": "Food"},
            {"amount": 2.5, "category": "Food"},
            {"amount": 7.0, "category": "Travel"},
        ]
        self.assertEqual(et.totals_by_category(expenses), {"Food": 12.5, "Travel": 7.0})


class StorageAndServerTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)  # start with no file
        self.server = et.make_server(0, self.path)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_add_and_list(self):
        body = json.dumps({"amount": 4.5, "category": "Food", "note": "coffee"}).encode()
        req = urllib.request.Request(self.url("/api/expenses"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 201)
            created = json.loads(r.read())
        self.assertEqual(created["id"], 1)

        with urllib.request.urlopen(self.url("/api/expenses")) as r:
            data = json.loads(r.read())
        self.assertEqual(len(data["expenses"]), 1)
        self.assertEqual(data["totals"], {"Food": 4.5})

        # persisted to the JSON file
        self.assertEqual(len(et.load_expenses(self.path)), 1)

    def test_invalid_post_returns_400(self):
        body = json.dumps({"amount": "x", "category": "Food"}).encode()
        req = urllib.request.Request(self.url("/api/expenses"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_index_page_served(self):
        with urllib.request.urlopen(self.url("/")) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"Expense Tracker", r.read())


if __name__ == "__main__":
    unittest.main()
