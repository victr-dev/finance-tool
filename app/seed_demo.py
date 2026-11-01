"""Fill a new database with a made-up bartender's last few months, for trying the app and for screenshots.

usage: python app/seed_demo.py <demo.db> [--today YYYY-MM-DD] [--force]
then:  FINANCE_DB=<demo.db> FINANCE_TODAY=<same date> python app/server.py --open
"""
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

ITEMS = [("Rent", "Needs", "Bill", 950, 1), ("Internet", "Needs", "Bill", 60, 8), ("Utilities", "Needs", "Bill", 120, 15),
         ("Car Insurance", "Needs", "Bill", 135, 20), ("Phone", "Needs", "Bill", 55, 12),
         ("Gym", "Needs", "Subscription", 30, 3), ("Groceries", "Needs", "Spending", 350, None),
         ("Gas", "Needs", "Spending", 160, None), ("Unexpected / Misc", "Needs", "Spending", 0, None),
         ("Streaming", "Wants", "Subscription", 16, 10), ("Music", "Wants", "Subscription", 11, 22),
         ("Dining Out", "Wants", "Spending", 120, None), ("Entertainment", "Wants", "Spending", 80, None)]
# name, APR, minimum, due day, balance
DEBTS = [("Visa", 27.99, 35, 7, 640.00), ("Store Card", 29.99, 40, 18, 1150.00), ("Mastercard", 24.49, 90, 12, 2300.00),
         ("Rewards Card", 22.99, 150, 25, 4800.00), ("Student Loan", 5.5, 120, 15, 9800.00),
         ("Auto Loan", 7.9, 410, 5, 16500.00)]
# name, kind, target, due month, balance, monthly transfer
FUNDS = [("Emergency Fund", "Emergency", 5000, None, 1200, 0), ("Car Registration", "Sinking", 120, 9, 24, 12),
         ("Gifts", "Sinking", 800, 12, 180, 60), ("Car Maintenance", "Sinking", 600, None, 150, 50),
         ("Annual Gym Fee", "Sinking", 50, 3, 20, 5), ("New Apartment", "Goal", 3000, None, 300, 400),
         ("Beach Trip", "Goal", 2000, None, 0, 0)]


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def seed(con, today, rnd):
    start = add_months(today.replace(day=1), -3)
    anchor = (start - timedelta(days=1)).isoformat()
    ym = start.strftime("%Y-%m")
    ids = {}
    ins = lambda table, rec: db.insert(con, table, rec)  # noqa: E731

    for n in ("Day", "Night"):
        ids[n] = ins("shift_types", {"name": n})
    for n, t, bal in (("Checking", "Checking", 850), ("Cash", "Cash", 260), ("High-Yield Savings", "Savings", 0)):
        ids[n] = ins("accounts", {"name": n, "type": t, "anchor_date": anchor, "anchor_balance": bal})
    for n, g, k, a, due in ITEMS:
        ids[n] = ins("budget_items", {"name": n, "grp": g, "kind": k, "amount": a, "due_day": due, "start_month": ym})
    for n, apr, mn, due, bal in DEBTS:
        ids[n] = ins("debts", {"name": n, "type": "Loan" if "Loan" in n else "Credit card", "apr": apr, "minimum": mn,
                               "due_day": due, "anchor_date": anchor, "anchor_balance": bal})
    ids["Laptop Installments"] = ins("debts", {"name": "Laptop Installments", "type": "Pay later", "apr": 0,
                                               "minimum": 75, "due_day": 28, "extra_payoff": 0, "anchor_date": anchor,
                                               "anchor_balance": 450,
                                               "last_payment_month": add_months(start, 5).strftime("%Y-%m")})
    for n, k, tgt, dm, bal, _ in FUNDS:
        rec = {"name": n, "kind": k, "target": tgt, "due_month": dm, "anchor_date": anchor, "anchor_balance": bal}
        if n == "New Apartment":
            rec["target_date"] = add_months(start, 9).isoformat()
        if n == "Emergency Fund":
            rec["held_at"] = ids["High-Yield Savings"]
        ids[n] = ins("funds", rec)
    ids["Paycheck"] = ins("income_types", {"name": "Paycheck", "counts": 0, "dest_type": "fund",
                                           "dest_id": ids["Emergency Fund"]})
    ids["Side Gig"] = ins("income_types", {"name": "Side Gig", "counts": 1})
    ids["Gift"] = ins("income_types", {"name": "Gift", "counts": 1})
    db.set_settings(con, {"goal": 4000, "typical_shifts": 18, "start_month": ym, "default_tips_account": ids["Checking"]})

    def tx(d, desc, amount, cat, cat_name, src, src_name, unexpected=0):
        ins("transactions", {"date": d.isoformat(), "description": desc, "amount": round(amount, 2),
                             "cat_type": cat, "cat_id": ids[cat_name], "src_type": src, "src_id": ids[src_name],
                             "unexpected": unexpected})

    last_day = add_months(today, 1) - timedelta(days=1)
    d = start
    while d <= last_day:
        past = d < today
        wd = d.weekday()
        kind = {1: "Day", 3: "Night", 4: "Night", 5: "Night"}.get(wd)
        if wd == 6 and rnd.random() < 0.5:
            kind = "Day"
        if kind:
            tips = None
            if past:
                mean = 100 if kind == "Day" else (250 if wd in (4, 5) else 200)
                tips = max(40, round(rnd.gauss(mean, mean * 0.18) / 5) * 5)
            ins("shifts", {"date": d.isoformat(), "type_id": ids[kind], "hours": 6 if kind == "Day" else 8,
                           "tips": tips})
        if past:
            for n, _, _, amount, due in ITEMS:
                if due == d.day:
                    tx(d, n, amount + (rnd.uniform(-12, 18) if n == "Utilities" else 0), "item", n,
                       "debt" if n in ("Streaming", "Music") else "account", "Visa" if n in ("Streaming", "Music")
                       else "Checking")
            for n, _, mn, due, _ in DEBTS:
                if due == d.day:
                    tx(d, f"{n} payment", mn, "debt", n, "account", "Checking")
            if d.day == 28 and d < add_months(start, 6):
                tx(d, "Laptop installment", 75, "debt", "Laptop Installments", "account", "Checking")
            if d.day == 20:
                tx(d, "Extra payment", 250, "debt", "Store Card", "account", "Checking")
            if d.day == 2:
                for n, _, _, _, _, monthly in FUNDS:
                    if monthly:
                        tx(d, "Savings transfer", monthly, "fund", n, "account", "Checking")
            if wd == 6:
                tx(d, "Grocery store", rnd.uniform(65, 105), "item", "Groceries", "account", "Checking")
            if wd in (0, 4) and rnd.random() < 0.55:
                tx(d, "Gas station", rnd.uniform(32, 48), "item", "Gas", "account", "Checking")
            if wd in (2, 6) and rnd.random() < 0.35:
                tx(d, rnd.choice(["Taco place", "Pizza", "Brunch", "Sushi"]), rnd.uniform(22, 55), "item",
                   "Dining Out", "account", "Checking")
            if d.day == 16:
                tx(d, rnd.choice(["Concert tickets", "Movies", "Bowling", "Record store"]), rnd.uniform(25, 70), "item",
                   "Entertainment", "account", "Checking")
            if d.day == 11 and d.month % 2:
                tx(d, "Cash deposit", 100, "account", "Checking", "account", "Cash")
            if wd == 4 and (d - start).days // 7 % 2 == 0:
                ins("income", {"date": d.isoformat(), "description": "Paycheck", "type_id": ids["Paycheck"],
                               "amount": round(rnd.uniform(130, 165), 2)})
            if d.day == 13:
                ins("income", {"date": d.isoformat(), "description": "Catering gig", "type_id": ids["Side Gig"],
                               "amount": rnd.choice([150, 180, 220, 250])})
        d += timedelta(days=1)

    tx(add_months(start, 1).replace(day=9), "Car battery", 165, "item", "Unexpected / Misc", "fund", "Emergency Fund", 1)
    tx(add_months(start, 2).replace(day=23), "Vet visit", 120, "item", "Unexpected / Misc", "account", "Checking", 1)
    ins("income", {"date": add_months(start, 1).replace(day=24).isoformat(), "description": "Birthday",
                   "type_id": ids["Gift"], "amount": 100})
    db.set_override(con, today.strftime("%Y-%m"), "item", ids["Utilities"], 145)
    return ids


def main():
    argv = sys.argv[1:]
    today = date.today()
    if "--today" in argv:
        i = argv.index("--today")
        today = date.fromisoformat(argv[i + 1])
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    path = os.path.abspath(args[0])
    if path == os.path.abspath(os.path.join(db.data_dir(), "finance.db")):
        sys.exit("That's the real database. Pick another file for demo data.")
    if os.path.exists(path):
        if "--force" not in sys.argv:
            sys.exit(f"{path} already exists. Re-run with --force to replace it.")
        os.remove(path)
    con = db.connect(path)
    db.set_settings(con, {"backup_dir": os.path.join(os.path.dirname(path), "backups")})
    seed(con, today, random.Random(7))
    con.close()
    print(f"Demo data written to {path} (today = {today}).")


if __name__ == "__main__":
    main()
