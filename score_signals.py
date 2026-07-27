#!/usr/bin/env python3
"""
Trayo composite signal scorer.

Ranks accounts by *stacked* buying signals from Trayo MCP event output instead of
firing on single ones. Pain + a fresh, ICP-fit buyer inside a recency window = hot.
Pain alone = warm. No signal = hold.

Usage:  python3 score_signals.py events.json
No dependencies — Python 3 standard library only.
"""
import json, sys, math
from datetime import date
from collections import defaultdict

# ---- tunable model (every number here is defensible; that's the point) -------
REFERENCE_DATE = date(2026, 7, 26)   # "today" for recency decay
HALF_LIFE_DAYS = 45                  # a signal's weight halves every 45 days
COMPOSITE_MULTIPLIER = 1.6           # boost when buyer + why-now co-occur in window
COMPOSITE_WINDOW_DAYS = 75           # max gap between the buyer and why-now signal

# base weight by signal type: structure you can write logic against ranks higher.
TYPE_WEIGHT = {"job_change": 3.0, "jobs": 2.0, "news": 1.0}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}

# a signal_key identifies a BUYER (the "who") vs a why-now (the "why").
BUYER_SIGNAL_KEYS = {
    "ops-executive-joiner", "knowledge-ai-owner-joiner", "sales-manager-move",
    "vp-sales-joiner", "consulting-coo-hire", "knowledge-leader-hire",
}

# ICP-FIT WEIGHT — keyed to the stakeholderCriteria (delivery/ops/knowledge owners
# who control post-meeting workflow). A sales-leader joiner is a real buyer signal
# but a poor fit for THIS product, so it's discounted. This is what stops the model
# from surfacing the wrong human just because their move is more recent.
BUYER_FIT = {
    "ops-executive-joiner":       1.0,
    "knowledge-ai-owner-joiner":  1.0,
    "consulting-coo-hire":        1.0,
    "knowledge-leader-hire":      0.9,
    "vp-sales-joiner":            0.5,
    "sales-manager-move":         0.4,
}
DEFAULT_BUYER_FIT = 0.6
# ------------------------------------------------------------------------------

def normalized_date(ev):
    """jobs eventDate is the discovery date (useless); postedAt is the truth.
    news/job_change eventDate is real. This is the recency-fidelity fix."""
    raw = ev.get("postedAt") if ev["signalType"] == "jobs" else ev.get("eventDate")
    raw = raw or ev.get("eventDate")
    return date.fromisoformat(raw[:10])

def days_ago(d):
    return (REFERENCE_DATE - d).days

def recency(d):
    return math.pow(0.5, max(days_ago(d), 0) / HALF_LIFE_DAYS)

def best_confidence(ev):
    ws = [CONFIDENCE_WEIGHT.get(s.get("confidence", "low"), 0.3)
          for s in ev["matchedSignals"]]
    return max(ws) if ws else 0.3

def is_buyer(ev):
    return (ev["signalType"] == "job_change"
            or any(s["signal_key"] in BUYER_SIGNAL_KEYS for s in ev["matchedSignals"]))

def buyer_fit(ev):
    """max ICP-fit across the event's buyer signals; 1.0 for non-buyer events."""
    fits = [BUYER_FIT.get(s["signal_key"], DEFAULT_BUYER_FIT)
            for s in ev["matchedSignals"] if s["signal_key"] in BUYER_SIGNAL_KEYS]
    return max(fits) if fits else 1.0

def event_score(ev):
    base = TYPE_WEIGHT.get(ev["signalType"], 1.0) * best_confidence(ev) * recency(normalized_date(ev))
    return base * (buyer_fit(ev) if ev["_buyer"] else 1.0)

def score_accounts(events):
    by_acct = defaultdict(list)
    for ev in events:
        ev["_date"]  = normalized_date(ev)
        ev["_buyer"] = is_buyer(ev)
        ev["_fit"]   = buyer_fit(ev)
        ev["_score"] = event_score(ev)
        by_acct[ev["account"]].append(ev)

    out = []
    for acct, evs in by_acct.items():
        buyers   = [e for e in evs if e["_buyer"]]
        why_nows = [e for e in evs if not e["_buyer"]]
        base = sum(e["_score"] for e in evs)

        composite = any(
            abs((b["_date"] - w["_date"]).days) <= COMPOSITE_WINDOW_DAYS
            for b in buyers for w in why_nows
        )
        score = base * (COMPOSITE_MULTIPLIER if composite else 1.0)
        tier = "HOT" if composite else ("WARM" if (buyers or why_nows) else "HOLD")

        # outreach target: best-FIT buyer (fit dominates, score breaks ties),
        # else the strongest why-now's stakeholder.
        if buyers:
            target = max(buyers, key=lambda e: (e["_fit"], e["_score"]))["stakeholder"]
        elif why_nows:
            target = max(why_nows, key=lambda e: e["_score"])["stakeholder"]
        else:
            target = {"name": None, "title": None}

        stack = sorted({s["signal_key"] for e in evs for s in e["matchedSignals"]})
        out.append({
            "account": acct, "tier": tier, "score": round(score, 2),
            "events": len(evs), "buyer_signals": len(buyers),
            "target": target, "stack": stack,
            "top_evidence": max(evs, key=lambda e: e["_score"]),
        })
    return sorted(out, key=lambda r: r["score"], reverse=True)

def why_now_line(row):
    t = row["target"]
    who = t.get("name") or t.get("title") or "the delivery/ops owner"
    ev = row["top_evidence"]
    if row["tier"] == "HOT":
        return (f"New ICP-fit buyer ({who}) landed in-window while the account is "
                f"actively investing — e.g. \"{ev['title']}\". Call today.")
    if row["tier"] == "WARM":
        return (f"Active strain (\"{ev['title']}\") but no fresh buyer yet — "
                f"nurture {who}, don't burn it on a cold open.")
    return "No qualifying signal. Hold."

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "events.json"
    events = json.load(open(path))
    rows = score_accounts(events)
    print(f"\n{'='*80}\nTRAYO COMPOSITE SIGNAL RANKING  —  {len(events)} events, "
          f"{len(rows)} accounts  (as of {REFERENCE_DATE})\n{'='*80}")
    for i, r in enumerate(rows, 1):
        tgt = r["target"].get("name") or r["target"].get("title") or "—"
        print(f"\n{i}. {r['account']:<14} [{r['tier']}]  score {r['score']:>6}   "
              f"{r['events']} events / {r['buyer_signals']} buyer")
        print(f"   target : {tgt}")
        print(f"   stack  : {', '.join(r['stack'])}")
        print(f"   why-now: {why_now_line(r)}")
    print(f"\n{'-'*80}\nHOLD (no signal after deepBackfill): Credera, Point B\n{'-'*80}")

if __name__ == "__main__":
    main()
