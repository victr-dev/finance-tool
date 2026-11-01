"""Checks the app's budgeting, pace and payoff math on a made-up household.

run: python -m unittest discover -s app/tests   (from the project folder)
"""
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calc  # noqa: E402
import db  # noqa: E402

OCT31 = "2026-10-31"
ITEMS = [("Rent", "Needs", "Bill", 950, 1), ("Internet", "Needs", "Bill", 60, 8), ("Utilities", "Needs", "Bill", 120, 15),
         ("Car Insurance", "Needs", "Bill", 135, 20), ("Phone", "Needs", "Bill", 55, 12),
         ("Gym", "Needs", "Subscription", 30, 3), ("Groceries", "Needs", "Spending", 350, None),
         ("Gas", "Needs", "Spending", 160, None), ("Unexpected / Misc", "Needs", "Spending", 0, None),
         ("Streaming", "Wants", "Subscription", 16, 10), ("Music", "Wants", "Subscription", 11, 22),
         ("Dining Out", "Wants", "Spending", 120, None), ("Entertainment", "Wants", "Spending", 80, None)]
DEBTS = [("Visa", 27.99, 35, 7, None, 1, 640.00), ("Store Card", 29.99, 40, 18, None, 1, 1150.00),
         ("Mastercard", 24.49, 90, 12, None, 1, 2300.00), ("Rewards Card", 22.99, 150, 25, None, 1, 4800.00),
         ("Laptop Installments", 0, 75, 28, "2027-04", 0, 450.00),
         ("Student Loan", 5.5, 120, 15, None, 1, 9800.00), ("Auto Loan", 7.9, 410, 5, None, 1, 16500.00)]
FUNDS = [("Emergency Fund", "Emergency", 5000, None, None, 900), ("Car Registration", "Sinking", 120, 9, None, 0),
         ("Gifts", "Sinking", 800, None, None, 0), ("Car Maintenance", "Sinking", 600, None, None, 0),
         ("Annual Gym Fee", "Sinking", 50, None, None, 0), ("Software License", "Sinking", 40, 8, None, 0),
         ("New Apartment", "Goal", 3000, None, "2027-03-01", 0), ("Beach Trip", "Goal", 2000, None, None, 0),
         ("Road Trip", "Goal", 1500, None, None, 0)]


def seed(con, start="2026-11"):
    ids = {}
    db.set_settings(con, {"start_month": start, "goal": 4000, "typical_shifts": 20})
    for n in ("Day", "Night"):
        ids[n] = db.insert(con, "shift_types", {"name": n})
    for n, t in (("Checking", "Checking"), ("Cash", "Cash")):
        ids[n] = db.insert(con, "accounts", {"name": n, "type": t, "anchor_date": OCT31, "anchor_balance": 0})
    db.set_settings(con, {"default_tips_account": ids["Checking"]})
    for n, g, k, a, due in ITEMS:
        ids[n] = db.insert(con, "budget_items", {"name": n, "grp": g, "kind": k, "amount": a, "due_day": due,
                                                 "start_month": "2026-11"})
    for n, apr, mn, due, last, extra, bal in DEBTS:
        ids[n] = db.insert(con, "debts", {"name": n, "apr": apr, "minimum": mn, "due_day": due, "last_payment_month": last,
                                          "extra_payoff": extra, "anchor_date": OCT31, "anchor_balance": bal})
    for n, k, tgt, dm, td, bal in FUNDS:
        ids[n] = db.insert(con, "funds", {"name": n, "kind": k, "target": tgt, "due_month": dm, "target_date": td,
                                          "anchor_date": OCT31, "anchor_balance": bal})
    ids["Paycheck"] = db.insert(con, "income_types", {"name": "Paycheck", "counts": 0, "dest_type": "fund",
                                                      "dest_id": ids["Emergency Fund"]})
    ids["Side Gig"] = db.insert(con, "income_types", {"name": "Side Gig", "counts": 1})
    return ids


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        self.con = db.connect(self.path)
        self.ids = seed(self.con)

    def tearDown(self):
        self.con.close()
        os.remove(self.path)

    def data(self, today):
        return calc.Data(self.con, today=today)


class NovemberPlan(Base):
    def test_plan_total_and_leftover(self):
        mv = calc.month_view(self.data(date(2026, 10, 30)), "2026-11")
        self.assertAlmostEqual(mv["plan"]["planned"], 3894.28, places=2)
        self.assertEqual(mv["plan"]["over"], 0)
        self.assertAlmostEqual(mv["plan"]["pool"], 105.72, places=2)     # goal 4000 - planned
        extra = {ln["name"]: ln["budget"] for ln in mv["sections"][4]["lines"]}
        self.assertEqual(extra["Store Card"], 105.72)                   # highest APR gets the leftover
        self.assertEqual(extra["Visa"], 0)
        sav = {ln["name"]: ln["budget"] for ln in mv["sections"][3]["lines"]}
        self.assertEqual(sav["New Apartment"], 750)       # 3000 over Nov-Feb
        self.assertEqual(sav["Car Registration"], 12)     # 120 over 10 months until September
        self.assertEqual(sav["Software License"], 4.44)   # 40 over 9 months until August
        self.assertEqual(sav["Gifts"], 66.67)             # no due month: target / 12
        self.assertEqual(sav["Annual Gym Fee"], 4.17)

    def test_december_catch_up_and_ended_item(self):
        db.update(self.con, "budget_items", self.ids["Gym"], {"end_month": "2026-11"})
        db.insert(self.con, "budget_items", {"name": "Netflix", "grp": "Wants", "amount": 15, "start_month": "2026-12"})
        dec = calc.month_view(self.data(date(2026, 10, 30)), "2026-12")
        names = [ln["name"] for s in dec["sections"] for ln in s["lines"]]
        self.assertNotIn("Gym", names)
        self.assertIn("Netflix", names)
        sav = {ln["name"]: ln["budget"] for ln in dec["sections"][3]["lines"]}
        self.assertEqual(sav["Car Registration"], 13.33)
        self.assertEqual(sav["Software License"], 5)
        self.assertEqual(sav["New Apartment"], 1000)
        nov = calc.month_view(self.data(date(2026, 10, 30)), "2026-11")
        self.assertIn("Gym", [ln["name"] for s in nov["sections"] for ln in s["lines"]])

    def test_override_changes_budget(self):
        db.set_override(self.con, "2026-11", "item", self.ids["Utilities"], 152.50)
        mv = calc.month_view(self.data(date(2026, 10, 30)), "2026-11")
        ln = next(ln for ln in mv["sections"][0]["lines"] if ln["name"] == "Utilities")
        self.assertEqual(ln["budget"], 152.5)
        self.assertEqual(ln["default"], 120)
        self.assertAlmostEqual(mv["plan"]["planned"], 3926.78, places=2)
        self.assertAlmostEqual(mv["plan"]["pool"], 73.22, places=2)


class Payoff(Base):
    def test_minimums_only(self):
        p = calc.payoff_plans(self.data(date(2026, 10, 30)))
        a, s = p["Avalanche"], p["Snowball"]
        self.assertEqual(a["debt_free"], {"months": 48, "label": "Oct 2030"})
        self.assertEqual(s["debt_free"], {"months": 48, "label": "Oct 2030"})
        self.assertAlmostEqual(a["interest"], 7720.38, places=1)
        self.assertAlmostEqual(s["interest"], 7768.93, places=1)
        self.assertEqual(p["message"], "Avalanche saves you $49 in interest vs Snowball.")
        i = self.ids
        self.assertEqual(a["per_debt"][i["Store Card"]]["label"], "Mar 2028")   # avalanche: highest APR first
        self.assertEqual(s["per_debt"][i["Visa"]]["label"], "Sep 2027")         # snowball: smallest balance first
        self.assertEqual(a["per_debt"][i["Visa"]]["label"], "May 2028")
        self.assertEqual(a["per_debt"][i["Laptop Installments"]]["label"], "Apr 2027")
        self.assertEqual(a["per_debt"][i["Auto Loan"]]["label"], "Feb 2030")
        self.assertEqual(s["per_debt"][i["Auto Loan"]]["label"], "Sep 2030")
        self.assertEqual(a["per_debt"][i["Student Loan"]]["label"], "Oct 2030")

    def test_extra_payment_speeds_payoff(self):
        base = calc.payoff_plans(self.data(date(2026, 10, 30)))["Avalanche"]
        more = calc.payoff_plans(self.data(date(2026, 10, 30)), extra=300)["Avalanche"]
        self.assertLess(more["debt_free"]["months"], base["debt_free"]["months"])
        self.assertLess(more["interest"], base["interest"])


class OctoberScenario(Base):
    """A month already in progress: tips, side income and spending logged (today = Oct 29, 2026)."""

    def setUp(self):
        super().setUp()
        i, c = self.ids, self.con
        db.set_settings(c, {"start_month": "2026-10"})
        for t, k in (("debts", "Visa"), ("debts", "Rewards Card"), ("accounts", "Checking"), ("accounts", "Cash"),
                     ("funds", "Emergency Fund"), ("funds", "New Apartment")):
            db.update(c, t, i[k], {"anchor_date": "2026-09-30"})
        shifts = [(2, "Night", 7, 280), (3, "Night", 8, 345), (7, "Day", 6, 135), (9, "Night", 7, 290),
                  (10, "Night", 8, 330), (14, "Day", 6, 120), (16, "Night", 7, 275), (17, "Night", 8, 360),
                  (21, "Day", 6, 140), (23, "Night", 7, 300), (24, "Night", 8, 335), (28, "Day", 6, 130),
                  (30, "Night", None, None), (31, "Night", None, None)]
        for dd, t, h, tip in shifts:
            db.insert(c, "shifts", {"date": f"2026-10-{dd:02d}", "type_id": i[t], "hours": h, "tips": tip})
        db.insert(c, "income", {"date": "2026-10-02", "description": "catering gig", "type_id": i["Side Gig"],
                                "amount": 250})
        db.insert(c, "income", {"date": "2026-10-09", "description": "paycheck", "type_id": i["Paycheck"],
                                "amount": 120})
        tx = [(1, "Rent", "item", "Rent", 950, "account", "Checking", 0),
              (3, "Grocery store", "item", "Groceries", 92.15, "debt", "Rewards Card", 0),
              (5, "Car payment", "debt", "Auto Loan", 410, "account", "Checking", 0),
              (7, "Visa payment", "debt", "Visa", 400, "account", "Checking", 0),
              (12, "Gas station", "item", "Gas", 48, "account", "Checking", 0),
              (15, "Flat tire", "item", "Unexpected / Misc", 140, "fund", "Emergency Fund", 1),
              (18, "Tacos", "item", "Dining Out", 45, "debt", "Store Card", 1),
              (20, "Apartment savings", "fund", "New Apartment", 250, "account", "Checking", 0),
              (22, "Cash deposit", "account", "Checking", 600, "account", "Cash", 0)]
        for dd, desc, ct, cn, amt, st, sn, unexp in tx:
            db.insert(c, "transactions", {"date": f"2026-10-{dd:02d}", "description": desc, "amount": amt,
                                          "cat_type": ct, "cat_id": i[cn], "src_type": st, "src_id": i[sn],
                                          "unexpected": unexp})
        self.d = self.data(date(2026, 10, 29))
        self.mv = calc.month_view(self.d, "2026-10")

    def test_pace(self):
        p = self.mv["pace"]
        self.assertAlmostEqual(p["earned"], 3290)          # tips + side gig; the paycheck doesn't count
        self.assertAlmostEqual(p["projected"], 3918.75)    # + 2 nights at the night average
        self.assertEqual(p["status"]["text"], "On pace to come in UNDER by $81")
        self.assertAlmostEqual(p["need_per_shift"], 355)
        w = self.mv["week"]
        self.assertAlmostEqual(w["target"], 774.19, places=2)   # Oct 26-31: 6 of 31 days
        self.assertAlmostEqual(w["need_per_shift"], 322.10, places=2)

    def test_balances(self):
        i, b = self.ids, self.d.balance
        self.assertAlmostEqual(b(("debt", i["Visa"])), 240)
        self.assertAlmostEqual(b(("debt", i["Rewards Card"])), 4892.15)
        self.assertAlmostEqual(b(("fund", i["Emergency Fund"])), 880)
        self.assertAlmostEqual(b(("account", i["Checking"])), 1832)
        self.assertAlmostEqual(b(("fund", i["New Apartment"])), 250)

    def test_extra_debt_allocation(self):
        self.assertAlmostEqual(self.mv["plan"]["planned"], 1655.75, places=2)
        extra = {ln["name"]: ln["budget"] for ln in self.mv["sections"][4]["lines"]}
        self.assertEqual(extra["Store Card"], 1110)     # room = balance - minimum
        self.assertEqual(extra["Visa"], 605)
        self.assertEqual(extra["Mastercard"], 629.25)   # what's left of the pool
        self.assertEqual(extra["Rewards Card"], 0)

    def test_budget_actuals_skip_savings_spending(self):
        needs = {ln["name"]: ln for ln in self.mv["sections"][0]["lines"]}
        self.assertNotIn("Unexpected / Misc", needs)  # items start in November
        self.assertEqual(self.mv["plan"]["from_savings"], 140)
        self.assertEqual(self.mv["plan"]["unexpected_count"], 2)


class Insights(Base):
    def test_month_within_tolerance_counts_as_hit(self):
        for dd, tip in ((3, 1990), (10, 1970)):   # 3960: $40 short of 4000, inside the $50 cushion
            db.insert(self.con, "shifts", {"date": f"2026-11-{dd:02d}", "type_id": self.ids["Night"], "tips": tip})
        d = self.data(date(2026, 12, 5))
        nov = next(m for m in calc.insights(d)["by_month"] if m["month"] == "2026-11")
        self.assertEqual(nov["hit"], "✓ Yes")
        self.assertEqual(calc.month_view(d, "2026-11")["pace"]["status"]["text"], "You hit your goal")


if __name__ == "__main__":
    unittest.main()
