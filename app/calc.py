"""All the money math. Pure functions over a loaded snapshot of the database.

Mirrors the logic that was verified in the Excel version:
  balance = balance on the 'as of' date + money in after that date - money out after that date
  budget actuals ignore anything paid from a savings fund
  waterfall priority: Needs -> Debt minimums -> Wants -> Savings -> Extra debt
"""
import calendar
from datetime import date, timedelta

from db import get_settings, rows

SECTIONS = [("needs", "Needs"), ("debt", "Debt minimums"), ("wants", "Wants"), ("savings", "Savings"),
            ("extra", "Extra debt payoff")]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SIM_MONTHS = 180


# ------------------------------------------------------------------ date helpers
def d(s):
    return date.fromisoformat(s) if s else None


def month_start(x):
    return x.replace(day=1)


def month_end(x):
    return x.replace(day=calendar.monthrange(x.year, x.month)[1])


def add_months(x, n):
    y, m = divmod(x.month - 1 + n, 12)
    return date(x.year + y, m + 1, 1)


def ym(x):
    return f"{x.year:04d}-{x.month:02d}"


def parse_month(s):
    return date(int(s[:4]), int(s[5:7]), 1)


def months_between(a, b):
    return (b.year - a.year) * 12 + b.month - a.month


def due_in_month(day, ms):
    """Due day clamped to the month (31st -> 30th/28th)."""
    return ms.replace(day=min(int(day), month_end(ms).day))


def r2(x):
    return round(x + 0.0, 2)


# ------------------------------------------------------------------ snapshot
class Data:
    def __init__(self, con, today=None):
        s = get_settings(con)
        self.today = today or date.today()
        self.goal = float(s.get("goal") or 0)
        self.tol = float(s.get("tolerance") or 0)
        self.strategy = s.get("strategy") if s.get("strategy") in ("Avalanche", "Snowball") else "Avalanche"
        self.typical = float(s.get("typical_shifts") or 20)
        self.start = parse_month(s.get("start_month") or ym(self.today))
        self.default_acct = s.get("default_tips_account")
        self.settings = s
        self.accounts = rows(con, "SELECT * FROM accounts ORDER BY sort, id")
        self.funds = rows(con, "SELECT * FROM funds ORDER BY sort, id")
        self.debts = rows(con, "SELECT * FROM debts ORDER BY sort, id")
        self.items = rows(con, "SELECT * FROM budget_items ORDER BY sort, id")
        self.shift_types = rows(con, "SELECT * FROM shift_types ORDER BY sort, id")
        self.income_types = {r["id"]: r for r in rows(con, "SELECT * FROM income_types ORDER BY sort, id")}
        self.overrides = {(r["month"], r["ref_type"], r["ref_id"]): r["amount"]
                          for r in rows(con, "SELECT * FROM month_overrides")}
        self.shifts = rows(con, "SELECT * FROM shifts ORDER BY date, id")
        self.txs = rows(con, "SELECT * FROM transactions ORDER BY date, id")
        self.incomes = rows(con, "SELECT * FROM income ORDER BY date, id")
        for x in self.shifts + self.txs + self.incomes:
            x["d"] = d(x["date"])
        for sh in self.shifts:
            sh["status"] = self.shift_status(sh)
        for inc in self.incomes:
            t = self.income_types.get(inc["type_id"])
            inc["counts"] = bool(t["counts"]) if t else True
        self.type_name = {t["id"]: t["name"] for t in self.shift_types}
        self.names = {("account", a["id"]): a["name"] for a in self.accounts}
        self.names.update({("fund", f["id"]): f["name"] for f in self.funds})
        self.names.update({("debt", x["id"]): x["name"] for x in self.debts})
        self.names.update({("item", i["id"]): i["name"] for i in self.items})
        self._events = None
        self._bal = {}

    # current planning month: this month, but never before tracking starts
    @property
    def cm(self):
        return max(month_start(self.today), self.start)

    def shift_status(self, sh):
        if sh["tips"] is not None:
            return "Worked"
        return "Scheduled" if sh["d"] >= self.today else "Needs tips"

    def default_dest(self):
        return ("account", self.default_acct) if self.default_acct else None

    def shift_dest(self, sh):
        if sh["dest_type"] and sh["dest_id"]:
            return (sh["dest_type"], sh["dest_id"])
        return self.default_dest()

    def income_dest(self, inc):
        if inc["dest_type"] and inc["dest_id"]:
            return (inc["dest_type"], inc["dest_id"])
        t = self.income_types.get(inc["type_id"])
        if t and t["dest_type"] and t["dest_id"]:
            return (t["dest_type"], t["dest_id"])
        return self.default_dest()

    def events(self):
        """Every money movement as (date, amount, to_ref, from_ref)."""
        if self._events is None:
            ev = []
            for t in self.txs:
                to = (t["cat_type"], t["cat_id"]) if t["cat_type"] else None
                fr = (t["src_type"], t["src_id"]) if t["src_type"] else None
                ev.append((t["d"], t["amount"], to, fr))
            for i in self.incomes:
                ev.append((i["d"], i["amount"], self.income_dest(i), None))
            for s in self.shifts:
                if s["tips"]:
                    ev.append((s["d"], s["tips"], self.shift_dest(s), None))
            self._events = ev
        return self._events

    # ---------------------------------------------------------- balances
    def balance(self, ref, before=None):
        """Balance of an account/fund (asset) or debt (amount owed).
        before=None -> current balance; before=date -> balance at the start of that day."""
        key = (ref, before)
        if key not in self._bal:
            self._bal[key] = self._balance(ref, before)
        return self._bal[key]

    def _balance(self, ref, before):
        rec = self.record(ref)
        anchor = d(rec["anchor_date"])
        bal = rec["anchor_balance"] or 0.0
        sign = -1 if ref[0] == "debt" else 1
        if before is not None and anchor is not None and before <= anchor + timedelta(days=1):
            return bal
        for when, amt, to, fr in self.events():
            if anchor is not None and when <= anchor:
                continue
            if before is not None and when >= before:
                continue
            if to == ref:
                bal += sign * amt
            if fr == ref:
                bal -= sign * amt
        return bal

    def record(self, ref):
        table = {"account": self.accounts, "fund": self.funds, "debt": self.debts, "item": self.items}[ref[0]]
        for r in table:
            if r["id"] == ref[1]:
                return r
        raise KeyError(ref)

    # ---------------------------------------------------------- income helpers
    def earned_between(self, a, b):
        tips = sum(s["tips"] or 0 for s in self.shifts if a <= s["d"] <= b)
        other = sum(i["amount"] for i in self.incomes if i["counts"] and a <= i["d"] <= b)
        return tips + other

    def worked(self):
        return [s for s in self.shifts if s["status"] == "Worked"]

    def averages(self):
        w = self.worked()
        avg_all = (sum(s["tips"] for s in w) / len(w)) if w else (self.goal / self.typical if self.typical else 0)
        by_type = {}
        for t in self.shift_types:
            ws = [s["tips"] for s in w if s["type_id"] == t["id"]]
            by_type[t["id"]] = sum(ws) / len(ws) if ws else avg_all
        return avg_all, by_type


# ------------------------------------------------------------------ month view
def bill_status(due, budget, actual, today):
    if due:
        if budget > 0 and actual >= budget - 0.005:
            return "✓ Paid"
        if actual > 0:
            return "Partly paid"
        if today > due:
            return "Overdue"
        if (due - today).days <= 5:
            return "Due soon"
        return "Upcoming"
    if actual > budget + 0.005:
        return "Over budget"
    if budget == 0:
        return "—"
    return f"{actual / budget:.0%} used"


def item_active(item, ms):
    s = item["start_month"]
    e = item["end_month"]
    return (not s or parse_month(s) <= ms) and (not e or parse_month(e) >= ms)


def fund_suggestion(f, ms, bal):
    """Monthly amount for a fund given its balance at the start of month ms."""
    target = f["target"] or 0
    if f["monthly_amount"] is not None:
        s = f["monthly_amount"]
    elif f["kind"] == "Sinking":
        if not f["due_month"]:
            s = target / 12
        else:
            s = max(0, target - bal) / max(1, (int(f["due_month"]) - ms.month) % 12)
    elif f["kind"] == "Goal" and f["target_date"]:
        td = d(f["target_date"])
        s = 0 if td < ms else max(0, target - bal) / max(1, months_between(ms, td))
    else:
        s = 0
    return r2(s)


def strategy_ranks(data, strategy=None):
    """Order (1 = first) for debts that can take extra payments, by current balance."""
    strategy = strategy or data.strategy
    elig = [x for x in data.debts if not x["archived"] and x["extra_payoff"] and data.balance(("debt", x["id"])) > 0]
    bal = {x["id"]: data.balance(("debt", x["id"])) for x in elig}
    if strategy == "Snowball":
        key = lambda x: (bal[x["id"]], -(x["apr"] or 0))
    else:
        key = lambda x: (-(x["apr"] or 0), bal[x["id"]])
    return {x["id"]: i + 1 for i, x in enumerate(sorted(elig, key=key))}


def month_view(data, month=None):
    today = data.today
    ms = parse_month(month) if month else data.cm
    me = month_end(ms)
    days = me.day
    key = ym(ms)
    goal = data.goal

    earned = data.earned_between(ms, me)
    elapsed = 0 if today < ms else (days if today > me else (today - ms).days + 1)
    lo = max(ms, today)
    sched = [s for s in data.shifts if lo <= s["d"] <= me and s["status"] == "Scheduled"]
    avg_all, avg_type = data.averages()
    if today > me:
        method = "Month complete"
    elif sched:
        method = "Your scheduled shifts"
    elif today < ms or earned == 0:
        method = "No data yet"
    else:
        method = "Daily pace so far"
    if method == "Month complete":
        proj = earned
    elif sched:
        proj = earned + sum(avg_type.get(s["type_id"], avg_all) for s in sched)
    elif method == "No data yet":
        proj = earned
    else:
        proj = earned / elapsed * days
    worked = [s for s in data.shifts if ms <= s["d"] <= me and s["status"] == "Worked"]
    tips_m = sum(s["tips"] for s in worked)
    hours_m = sum(s["hours"] or 0 for s in worked)
    wage = sum(i["amount"] for i in data.incomes if not i["counts"] and ms <= i["d"] <= me)

    pace = {
        "goal": goal, "earned": earned, "still_needed": max(0, goal - earned),
        "pct_goal": min(1, earned / goal) if goal else 0, "pct_month": elapsed / days,
        "projected": proj, "method": method, "scheduled_left": len(sched),
        "need_per_shift": (max(0, goal - earned) / len(sched)) if sched else (0 if earned >= goal else None),
        "shifts_worked": len(worked), "avg_per_shift": tips_m / len(worked) if worked else None,
        "avg_per_hour": tips_m / hours_m if hours_m else None, "hourly_wage": wage,
        "estimate_per_shift": None if data.worked() else avg_all,   # set while there's no tip history yet
        "status": pace_status(method, proj, goal, data.tol),
    }

    # this week (Mon-Sun), clipped to the month
    week = None
    if ms <= today <= me:
        wk0 = today - timedelta(days=today.weekday())
        wf, wl = max(wk0, ms), min(wk0 + timedelta(days=6), me)
        share = goal * ((wl - wf).days + 1) / days
        w_earned = data.earned_between(wf, wl)
        left = [s for s in data.shifts if today <= s["d"] <= wl and s["status"] == "Scheduled"]
        need = max(0, share - w_earned)
        week = {"start": wk0.isoformat(), "full_target": goal * 7 / days, "target": share, "earned": w_earned,
                "shifts_left": len(left), "left": need,
                "need_per_shift": need / len(left) if left else (0 if w_earned >= share else None),
                "on_track": w_earned >= share}

    weeks = []
    wk = ms - timedelta(days=ms.weekday())
    while wk <= me:
        a, b = max(wk, ms), min(wk + timedelta(days=6), me)
        tgt = goal * ((b - a).days + 1) / days
        e = data.earned_between(a, b)
        if a > today:
            st = "Upcoming"
        elif b >= today:
            st = "✓ Hit" if e >= tgt else "In progress"
        else:
            st = "✓ Hit" if e >= tgt else "✗ Short"
        weeks.append({"start": a.isoformat(), "end": b.isoformat(), "days": (b - a).days + 1, "target": tgt,
                      "earned": e, "worked": sum(1 for s in data.shifts if a <= s["d"] <= b and s["status"] == "Worked"),
                      "diff": (e - tgt) if a <= today else None, "status": st})
        wk += timedelta(days=7)

    def spent(ref_type, ref_id):
        return sum(t["amount"] for t in data.txs if t["cat_type"] == ref_type and t["cat_id"] == ref_id
                   and ms <= t["d"] <= me and t["src_type"] != "fund")

    def line(section, ref_type, rec, due, default):
        ov_type = "extra" if section == "extra" else ref_type   # extra payoff has its own "this month" amount
        ov = data.overrides.get((key, ov_type, rec["id"]))
        budget = ov if ov is not None else default
        return {"section": section, "ref_type": ref_type, "ov_type": ov_type, "ref_id": rec["id"], "name": rec["name"],
                "due": due.isoformat() if due else None, "default": r2(default), "override": ov,
                "budget": r2(budget)}

    lines = []
    for grp, sec in (("Needs", "needs"), ("Wants", "wants")):
        for it in data.items:
            if it["grp"] == grp and item_active(it, ms):
                due = due_in_month(it["due_day"], ms) if it["due_day"] else None
                ln = line(sec, "item", it, due, it["amount"] or 0)
                ln["kind"] = it["kind"]
                ln["actual"] = spent("item", it["id"])
                ln["status"] = bill_status(due, ln["budget"], ln["actual"], today)
                lines.append(ln)

    debts = [x for x in data.debts if not x["archived"]]
    start_bal = {x["id"]: data.balance(("debt", x["id"]), before=ms) for x in debts}
    min_line = {}
    for x in debts:
        lp = parse_month(x["last_payment_month"]) if x["last_payment_month"] else None
        live = start_bal[x["id"]] > 0.005 and (lp is None or lp >= ms)
        due = due_in_month(x["due_day"], ms) if x["due_day"] else None
        ln = line("debt", "debt", x, due, (x["minimum"] or 0) if live else 0)
        paid = spent("debt", x["id"])
        ln["paid_total"] = paid
        ln["actual"] = min(ln["budget"], paid)
        ln["status"] = "Finished" if ln["budget"] == 0 else bill_status(due, ln["budget"], ln["actual"], today)
        min_line[x["id"]] = ln
        if ln["budget"] or paid:
            lines.append(ln)

    for f in data.funds:
        if f["archived"]:
            continue
        bal0 = data.balance(("fund", f["id"]), before=ms)
        if f["target_date"]:
            due = d(f["target_date"])
        elif f["due_month"]:
            dm = int(f["due_month"])
            due = date(ms.year + (1 if dm < ms.month else 0), dm, 1)
        else:
            due = None
        ln = line("savings", "fund", f, due, fund_suggestion(f, ms, bal0))
        ln["kind"] = f["kind"]
        ln["actual"] = spent("fund", f["id"])
        b, a = ln["budget"], ln["actual"]
        ln["status"] = (("✓ Added" if a > 0 else "—") if b == 0 else
                        "✓ Saved" if a >= b - 0.005 else "Partly saved" if a > 0 else "Not yet")
        lines.append(ln)

    planned = sum(ln["budget"] for ln in lines)
    pool = max(0, goal - planned)
    ranks = strategy_ranks(data)
    remaining = pool
    for x in sorted(debts, key=lambda x: ranks.get(x["id"], 999)):
        if x["id"] not in ranks:
            continue
        room = max(0, start_bal[x["id"]] - min_line[x["id"]]["budget"])
        ln = line("extra", "debt", x, None, min(room, remaining))
        remaining = max(0, remaining - room)
        ln["actual"] = max(0, min_line[x["id"]]["paid_total"] - min_line[x["id"]]["actual"])
        b, a = ln["budget"], ln["actual"]
        ln["status"] = (("✓ Extra paid" if a > 0 else "—") if b == 0 else
                        "✓ Paid" if a >= b - 0.005 else "Partly paid" if a > 0 else "Not yet")
        ln["order"] = ranks[x["id"]]
        lines.append(ln)

    order = {s: i for i, (s, _) in enumerate(SECTIONS)}
    lines.sort(key=lambda ln: order[ln["section"]])  # stable: keeps list order inside a section
    cum = 0.0
    at_risk_now = at_risk_proj = 0
    for ln in lines:
        b = ln["budget"]
        ln["left"] = r2(b - ln["actual"])
        ln["covered_now"] = r2(min(b, max(0, earned - cum)))
        ln["covered_proj"] = r2(min(b, max(0, proj - cum)))
        cum += b
        if b > 0:
            at_risk_now += ln["covered_now"] < b - 0.005
            at_risk_proj += ln["covered_proj"] < b - 0.005

    sections = []
    for s, label in SECTIONS:
        ls = [ln for ln in lines if ln["section"] == s]
        sections.append({"key": s, "label": label, "lines": ls,
                         "total": {k: r2(sum(ln[k] for ln in ls)) for k in
                                   ("default", "budget", "actual", "left", "covered_now", "covered_proj")}})
    total = {k: r2(sum(sec["total"][k] for sec in sections)) for k in sections[0]["total"]}

    in_month = [t for t in data.txs if ms <= t["d"] <= me]
    unexp = [t for t in in_month if t["unexpected"]]
    plan = {
        "goal": goal, "planned": r2(planned), "over": r2(max(0, planned - goal)), "pool": r2(pool),
        "spent": total["actual"], "unexpected": sum(t["amount"] for t in unexp), "unexpected_count": len(unexp),
        "from_savings": sum(t["amount"] for t in in_month if t["src_type"] == "fund"),
        "uncategorized": sum(t["amount"] for t in in_month if not t["cat_type"]),
        "projected_minus_plan": r2(proj - total["budget"]),
        "not_covered_now": at_risk_now, "at_risk_projected": at_risk_proj, "strategy": data.strategy,
    }
    return {"month": key, "month_start": ms.isoformat(), "month_end": me.isoformat(), "today": today.isoformat(),
            "is_current": ms == month_start(today), "pace": pace, "week": week, "weeks": weeks,
            "plan": plan, "sections": sections, "total": total}


def pace_status(method, proj, goal, tol):
    done = method == "Month complete"
    if method == "No data yet":
        return {"tone": "info", "text": "Add scheduled shifts to see your pace"}
    if proj >= goal + tol:
        return {"tone": "good", "text": f"{'Finished OVER goal by' if done else 'On pace to go OVER by'} ${proj - goal:,.0f}"}
    if proj >= goal - tol:
        return {"tone": "good", "text": "You hit your goal" if done else "On pace to HIT your goal"}
    return {"tone": "bad", "text": f"{'Finished UNDER goal by' if done else 'On pace to come in UNDER by'} ${goal - proj:,.0f}"}


# ------------------------------------------------------------------ balances overview
def overview(data, extra=0.0):
    cm = data.cm
    accounts = []
    fund_bal = {f["id"]: data.balance(("fund", f["id"])) for f in data.funds}
    for a in data.accounts:
        own = data.balance(("account", a["id"]))
        held = [f for f in data.funds if f["held_at"] == a["id"] and not f["archived"]]
        accounts.append({**a, "own_balance": own, "funds": [{"id": f["id"], "name": f["name"], "balance": fund_bal[f["id"]]}
                                                            for f in held],
                         "balance": own + sum(fund_bal[f["id"]] for f in held)})
    acct_name = {a["id"]: a["name"] for a in data.accounts}
    funds = []
    for f in data.funds:
        bal = fund_bal[f["id"]]
        tgt = f["target"] or 0
        funds.append({**f, "balance": bal, "progress": (max(0, min(1, bal / tgt)) if tgt else None),
                      "still_needed": max(0, tgt - bal), "suggested": fund_suggestion(f, cm, bal),
                      "held_at_name": acct_name.get(f["held_at"])})
    payoff = payoff_plans(data, extra)
    debts = []
    ranks_a, ranks_s = strategy_ranks(data, "Avalanche"), strategy_ranks(data, "Snowball")
    for x in data.debts:
        bal = data.balance(("debt", x["id"]))
        debts.append({**x, "balance": bal, "interest_month": max(0, bal) * (x["apr"] or 0) / 100 / 12,
                      "room": max(0, bal - (x["minimum"] or 0)),
                      "order_avalanche": ranks_a.get(x["id"]), "order_snowball": ranks_s.get(x["id"]),
                      "payoff_avalanche": payoff["Avalanche"]["per_debt"].get(x["id"]),
                      "payoff_snowball": payoff["Snowball"]["per_debt"].get(x["id"])})
    live = lambda r: not r["archived"]
    acct_total = sum(a["own_balance"] for a in accounts if live(a))
    fund_total = sum(f["balance"] for f in funds if live(f))
    debt_total = sum(max(0, x["balance"]) for x in debts if live(x))
    needs = sum(i["amount"] or 0 for i in data.items if i["grp"] == "Needs" and item_active(i, cm))
    mins = sum(x["minimum"] or 0 for x in debts if live(x) and x["balance"] > 0)
    emergency = sum(f["balance"] for f in funds if live(f) and f["kind"] == "Emergency")
    return {
        "accounts": accounts, "funds": funds, "debts": debts, "payoff": payoff,
        "totals": {"accounts": acct_total, "funds": fund_total, "debts": debt_total,
                   "net_worth": acct_total + fund_total - debt_total, "minimums": mins,
                   "interest_month": sum(x["interest_month"] for x in debts if live(x)),
                   "suggested_savings": sum(f["suggested"] for f in funds if live(f)),
                   "emergency_months": emergency / (needs + mins) if needs + mins else 0},
    }


# ------------------------------------------------------------------ payoff simulation
def simulate(data, strategy, extra=0.0):
    debts = [x for x in data.debts if not x["archived"]]
    bal = [max(0.0, data.balance(("debt", x["id"]))) for x in debts]
    rate = [(x["apr"] or 0) / 100 / 12 for x in debts]
    mins = [x["minimum"] or 0 for x in debts]
    ranks = strategy_ranks(data, strategy)
    rank = [ranks.get(x["id"], 90 + i) for i, x in enumerate(debts)]
    budget = sum(m for m, b in zip(mins, bal) if b > 0) + (extra or 0)
    n = len(debts)
    above = [0] * n          # months each debt still has a balance
    total_above = 0
    interest = 0.0
    for _ in range(SIM_MONTHS):
        interest += sum(b * r for b, r in zip(bal, rate))
        grown = [b * (1 + r) for b, r in zip(bal, rate)]
        paid = [min(m, g) for m, g in zip(mins, grown)]
        after = [g - p for g, p in zip(grown, paid)]
        pool = max(0, budget - sum(paid))
        new = []
        for i in range(n):
            ahead = sum(after[j] for j in range(n) if rank[j] < rank[i])
            new.append(max(0.0, round(after[i] - min(after[i], max(0, pool - ahead)), 2)))
        bal = new
        for i in range(n):
            above[i] += bal[i] > 0.005
        total_above += sum(bal) > 0.005
    cm = data.cm

    def when(months):
        if months == 0:
            return {"months": 0, "label": "Paid off"}
        if months > SIM_MONTHS:
            return {"months": months, "label": "15+ years"}
        return {"months": months, "label": add_months(cm, months - 1).strftime("%b %Y")}

    start = [max(0.0, data.balance(("debt", x["id"]))) for x in debts]
    per = {x["id"]: when(0 if start[i] <= 0 else above[i] + 1) for i, x in enumerate(debts)}
    overall = when(0 if sum(start) <= 0 else total_above + 1)
    return {"per_debt": per, "debt_free": overall, "interest": interest, "budget": budget}


def payoff_plans(data, extra=0.0):
    res = {s: simulate(data, s, extra) for s in ("Avalanche", "Snowball")}
    a, s = res["Avalanche"]["interest"], res["Snowball"]["interest"]
    if abs(a - s) < 1:
        msg = "Both strategies cost about the same in interest."
    elif a < s:
        msg = f"Avalanche saves you ${s - a:,.0f} in interest vs Snowball."
    else:
        msg = f"Snowball saves you ${a - s:,.0f} in interest vs Avalanche."
    res["message"] = msg
    res["extra"] = extra
    return res


# ------------------------------------------------------------------ home
def upcoming_bills(data, days=14):
    today = data.today
    out = []
    cands = [("item", i, i["amount"] or 0, i["due_day"]) for i in data.items if i["due_day"]]
    cands += [("debt", x, x["minimum"] or 0, x["due_day"]) for x in data.debts
              if x["due_day"] and not x["archived"] and data.balance(("debt", x["id"])) > 0]

    def paid_in(ref, when):
        a, b = month_start(when), month_end(when)
        return sum(t["amount"] for t in data.txs if (t["cat_type"], t["cat_id"]) == ref and a <= t["d"] <= b)

    for typ, rec, amt, day in cands:
        ref = (typ, rec["id"])
        this = due_in_month(day, month_start(today))
        if this >= today or (paid_in(ref, this) == 0 and this >= data.start):
            due = this
        else:
            due = due_in_month(day, add_months(today, 1))
        ms = month_start(due)
        if typ == "item":
            active = item_active(rec, ms)
        else:
            lp = rec["last_payment_month"]
            active = lp is None or parse_month(lp) >= ms
        if not active or due > today + timedelta(days=days) or due < data.start:
            continue
        ov = data.overrides.get((ym(ms), typ, rec["id"]))
        paid = paid_in(ref, due)
        if paid > 0:
            st, tone = "✓ Paid", "good"
        elif due < today:
            st, tone = "Overdue", "bad"
        elif due == today:
            st, tone = "Due today", "warn"
        else:
            n = (due - today).days
            st, tone = f"In {n} day{'s' if n != 1 else ''}", "warn" if n <= 3 else "info"
        out.append({"ref_type": typ, "ref_id": rec["id"], "name": rec["name"], "due": due.isoformat(),
                    "amount": ov if ov is not None else amt, "status": st, "tone": tone})
    out.sort(key=lambda b: (b["due"], b["name"]))
    return out


def home(data):
    mv = month_view(data)
    ov = overview(data)
    return {"month": mv["month"], "today": data.today.isoformat(), "pace": mv["pace"], "week": mv["week"],
            "plan": mv["plan"], "bills": upcoming_bills(data), "totals": ov["totals"],
            "debts": [{k: x[k] for k in ("id", "name", "balance", "apr", "minimum", "payoff_avalanche",
                                         "payoff_snowball")} for x in ov["debts"] if not x["archived"]],
            "funds": [{k: f[k] for k in ("id", "name", "kind", "balance", "target", "progress", "held_at_name")}
                      for f in ov["funds"] if not f["archived"]],
            "debt_free": ov["payoff"][data.strategy]["debt_free"], "strategy": data.strategy}


# ------------------------------------------------------------------ insights
def insights(data):
    today = data.today
    w = data.worked()
    hrs = [s for s in w if s["hours"]]

    def stats(shifts):
        tips = sum(s["tips"] for s in shifts)
        h = [s for s in shifts if s["hours"]]
        return {"shifts": len(shifts), "tips": tips, "avg": tips / len(shifts) if shifts else None,
                "per_hour": sum(s["tips"] for s in h) / sum(s["hours"] for s in h) if h else None}

    best = max(w, key=lambda s: s["tips"], default=None)
    all_time = stats(w)
    all_time["best"] = ({"date": best["date"], "tips": best["tips"], "type": data.type_name.get(best["type_id"])}
                        if best else None)
    all_time["other_income"] = sum(i["amount"] for i in data.incomes if i["counts"])
    all_time["hourly_wage"] = sum(i["amount"] for i in data.incomes if not i["counts"])
    last4 = stats([s for s in w if today - timedelta(days=27) <= s["d"] <= today])
    last4["per_week"] = last4["tips"] / 4

    by_type = [{"name": t["name"], **stats([s for s in w if s["type_id"] == t["id"]])} for t in data.shift_types]
    by_day = []
    for i, name in enumerate(WEEKDAYS):
        ds = [s for s in w if s["d"].weekday() == i]
        by_day.append({"day": name, **stats(ds),
                       "types": [{"name": t["name"], **stats([s for s in ds if s["type_id"] == t["id"]])}
                                 for t in data.shift_types]})

    dates = [x["d"] for x in data.shifts + data.txs + data.incomes]
    first = min([data.start] + [month_start(x) for x in dates if x])
    by_month = []
    m = first
    while m <= data.cm:
        me = month_end(m)
        tips = sum(s["tips"] or 0 for s in data.shifts if m <= s["d"] <= me)
        other = sum(i["amount"] for i in data.incomes if i["counts"] and m <= i["d"] <= me)
        nw = sum(1 for s in w if m <= s["d"] <= me)
        spent = sum(t["amount"] for t in data.txs if m <= t["d"] <= me and t["cat_type"] == "item"
                    and t["src_type"] != "fund")
        total = tips + other
        if m > month_start(today):
            st = ""
        elif m == month_start(today):
            st = "In progress"
        else:
            st = "✓ Yes" if total >= data.goal - data.tol else "✗ No"   # same tolerance as the pace banner
        by_month.append({"month": ym(m), "tips": tips, "other": other, "total": total, "goal": data.goal,
                         "diff": total - data.goal if m <= month_start(today) else None, "shifts": nw,
                         "avg": tips / nw if nw else None, "spent": spent, "hit": st})
        m = add_months(m, 1)

    weeks = []
    wk0 = today - timedelta(days=today.weekday())
    for k in range(11, -1, -1):
        a = wk0 - timedelta(days=7 * k)
        b = a + timedelta(days=6)
        weeks.append({"start": a.isoformat(), "earned": data.earned_between(a, b),
                      "target": data.goal * 7 / 30.4375})  # goal spread over an average-length month
    return {"all_time": all_time, "last4": last4, "by_type": by_type, "by_day": by_day, "by_month": by_month,
            "weeks": weeks, "goal": data.goal}
