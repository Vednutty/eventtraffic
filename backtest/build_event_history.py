"""
Regenerate ../event_history.json from verified_events.json.

Only events whose observed.status is 'reported' go in: an advisory says what
police expected, and training the historical baseline on expectations would
just feed the model its own assumptions back.

    python3 backtest/build_event_history.py
"""
import json
import os

from bands import BAND_SCORE

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "verified_events.json")
DEST = os.path.join(HERE, "..", "event_history.json")


def to_history_entry(e):
    obs = e["observed"]
    entry = {
        "date":           e["date"],
        "event_name":     e["name"],
        "event_type":     e["event_type"],
        "artist_origin":  e["artist_origin"],
        "day_of_week":    e["day_of_week"],
        "start_time":     e["start_time"],
        "severity_score": BAND_SCORE[obs["severity_band"]],
        "severity_basis": f"{obs['severity_band']} — {obs['band_basis']}",
        "worst_corridors": [r["model_names"][0] for r in obs["roads"]],
        "verified":       True,
        "sources":        obs["sources"],
    }
    # Omit crowd entirely when unknown: the baseline code does
    # e.get("crowd", crowd_size), and an explicit null would crash the maths.
    lo, hi = e["crowd"]["low"], e["crowd"]["high"]
    if lo and hi:
        entry["crowd"] = (lo + hi) // 2
    return entry


def main():
    with open(SRC, encoding="utf-8") as fh:
        events = json.load(fh)["events"]
    history, skipped = {}, []
    for e in events:
        if e["observed"]["status"] != "reported" or not e.get("date"):
            skipped.append(e["id"])
            continue
        history.setdefault(e["venue_id"], []).append(to_history_entry(e))
    for venue in history.values():
        venue.sort(key=lambda x: x["date"])
    with open(DEST, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=1, ensure_ascii=False)
    n = sum(len(v) for v in history.values())
    print(f"wrote {n} verified events to event_history.json "
          f"({', '.join(f'{k}: {len(v)}' for k, v in history.items())})")
    print(f"skipped {len(skipped)} advisory-only/undated events: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
