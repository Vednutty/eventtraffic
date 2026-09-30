"""
Backtest the model against verified_events.json and write backtest/report.md.

    python3 backtest/run_backtest.py

Out-of-sample by construction: while predicting an event, that event is hidden
from event_history.json, live Waze alerts are switched off (they describe
TODAY, not the event day), and disruptions are filtered by the event's date.
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import app                                   # noqa: E402
import providers                             # noqa: E402
from bands import BAND_LOS, los_distance     # noqa: E402

PHASES = [(120, "T-120"), (60, "T-60"), (0, "start"), (-30, "T+30"), (-60, "T+60")]
# Only used when no sourced crowd exists — always flagged in the report.
ASSUMED_CROWD = {"mahalaxmi": 32000, "mmrda": 30000, "dome": 6000,
                 "dypatil": 45000, "wankhede": 33000, "nesco": 10000}

_current = {}
_real_load_json = app.load_json
_disruption_file = _real_load_json("disruptions.json").get("disruptions", [])


def _load_json_hiding_current(filename):
    data = _real_load_json(filename)
    if filename == "event_history.json" and _current:
        data = {v: [e for e in evs if not (v == _current["venue_id"] and e["date"] == _current["date"])]
                for v, evs in data.items()}
    return data


def _disruptions_on_event_day(venue_id):
    day = _current["date"]
    return [d for d in _disruption_file
            if d.get("affected_venue") == venue_id
            and d.get("start_date", "0000-01-01") <= day <= d.get("end_date", "9999-12-31")]


app.load_json = _load_json_hiding_current
app.get_active_disruptions = _disruptions_on_event_day
app._fetch_waze_alerts = lambda venue_id: []


def predict(client, e, crowd, minutes):
    q = {"venue": e["venue_id"], "crowd": crowd, "minutes": minutes,
         "event_type": e["event_type"], "artist_origin": e["artist_origin"],
         "event_time": e["start_time"] or "19:30", "event_date": e["date"],
         "heavy_vehicle_ban": str(e["measures"].get("heavy_vehicle_ban", False)).lower(),
         "weather": "clear"}
    r = client.get("/api/predict", query_string=q)
    if r.status_code != 200:
        raise RuntimeError(f"{e['id']} {minutes}: HTTP {r.status_code}")
    return r.get_json()


def road_view(pred, model_name):
    """Predicted ECI and queue for one named road (corridor or extended feeder)."""
    for c in pred["corridors"]:
        if c["road_name"] == model_name:
            return {"eci": c["eci"], "los": c["los_grade"], "jam_km": None}
    for f in pred.get("extended_feeders") or []:
        if f["road_name"] == model_name:
            eci = max((s["eci"] for s in f["segments"]), default=None)
            return {"eci": eci, "los": app.eci_to_los(eci) if eci is not None else None,
                    "jam_km": f["jam_km"]}
    return None


def run_event(client, e):
    lo, hi = e["crowd"]["low"], e["crowd"]["high"]
    crowd_assumed = not (lo and hi)
    crowd = ASSUMED_CROWD[e["venue_id"]] if crowd_assumed else (lo + hi) // 2
    _current.update(venue_id=e["venue_id"], date=e["date"])
    phases = {label: predict(client, e, crowd, m) for m, label in PHASES}
    _current.clear()

    worst = max(phases.items(), key=lambda kv: kv[1]["worst_eci"] or 0)
    result = {"id": e["id"], "name": e["name"], "venue": e["venue_id"], "date": e["date"],
              "crowd": crowd, "crowd_assumed": crowd_assumed,
              "start_time_unsourced": e.get("start_time_unsourced", False),
              "status": e["observed"]["status"],
              "predicted": {label: {"los": p["los_grade"], "worst_corridor": p["worst_corridor"],
                                    "worst_eci": p["worst_eci"]} for label, p in phases.items()},
              "peak_phase": worst[0], "peak_los": worst[1]["los_grade"]}

    obs = e["observed"]
    if obs["status"] != "reported":
        return result

    band = obs["severity_band"]
    result["severity"] = {"reported_band": band, "band_los": BAND_LOS[band],
                          "predicted_peak_los": result["peak_los"],
                          "grades_off": los_distance(result["peak_los"], band)}
    roads = []
    for road in obs["roads"]:
        best = None
        for label, p in phases.items():
            for name in road["model_names"]:
                v = road_view(p, name)
                if v and v["eci"] is not None and (best is None or v["eci"] > best["eci"]):
                    best = dict(v, phase=label, model_name=name)
        mappls = providers.road_data(e, "mappls", best["model_name"]) if best else None
        roads.append({
            "as_reported": road["as_reported"],
            "reported_delay_min": road.get("delay_min"),
            "reported_queue_km": road.get("queue_km"),
            "model": best,
            "hotspot_hit": bool(best and best["los"] in ("E", "F")),
            "mappls": mappls,
            "mappls_congestion": providers.congestion_ratio(mappls),
        })
    result["roads"] = roads
    return result


def fmt_queue(q):
    return "—" if q is None else (f"{q[0]}–{q[1]} km" if isinstance(q, list) else f"{q} km")


def write_report(results, path):
    scored = [r for r in results if r["status"] == "reported"]
    hits = [rd["hotspot_hit"] for r in scored for rd in r["roads"]]
    exact = [r for r in scored if r["severity"]["grades_off"] == 0]
    within = [r for r in scored if r["severity"]["grades_off"] is not None and r["severity"]["grades_off"] <= 1]

    L = [f"# EventTraffic backtest — {datetime.now():%Y-%m-%d %H:%M}", "",
         "Out-of-sample: each event is hidden from event_history.json while it is predicted; "
         "live Waze alerts off; disruptions filtered to the event date; weather assumed clear.", "",
         f"**Mappls:** {providers.status('mappls')}", "",
         "## Scorecard (events with reported congestion only)", "",
         f"- Events scored: **{len(scored)}** of {len(results)} verified "
         f"({len(results) - len(scored)} have only a pre-event advisory — see bottom)",
         f"- Hotspot hit rate (reported road predicted LOS E/F): **{sum(hits)}/{len(hits)}**",
         f"- Severity exact grade: **{len(exact)}/{len(scored)}** · within one grade: **{len(within)}/{len(scored)}**",
         "", "> Sample is far too small to quote as an accuracy figure. It shows the harness works "
         "and where ground truth is missing.", ""]

    for r in scored:
        flags = []
        if r["crowd_assumed"]:
            flags.append(f"crowd ASSUMED {r['crowd']:,}")
        if r["start_time_unsourced"]:
            flags.append("start time unsourced")
        s = r["severity"]
        L += [f"### {r['name']} — {r['date']} ({r['venue']})", "",
              f"Crowd {r['crowd']:,}" + (f" · ⚠ {', '.join(flags)}" if flags else ""), "",
              f"Severity: reported **{s['reported_band']}** (≈ LOS {s['band_los']}) · "
              f"model peak **LOS {s['predicted_peak_los']}** at {r['peak_phase']} · "
              f"{s['grades_off']} grade(s) off", "",
              "| Reported road | Reported | Model road | Model LOS (phase) | Model queue | Hit | Mappls same-day |",
              "|---|---|---|---|---|---|---|"]
        for rd in r["roads"]:
            rep = ", ".join(x for x in [f"{rd['reported_delay_min']} min delay" if rd["reported_delay_min"] else "",
                                        fmt_queue(rd["reported_queue_km"]) if rd["reported_queue_km"] else ""] if x) or "congested"
            m = rd["model"]
            mappls = ("no data" if rd["mappls"] is None else
                      f"{rd['mappls'].get('avg_speed_kmph')} km/h ({rd['mappls_congestion']})")
            L.append(f"| {rd['as_reported']} | {rep} | {m['model_name'] if m else 'not modelled'} | "
                     f"{(m['los'] + ' (' + m['phase'] + ')') if m else '—'} | {fmt_queue(m['jam_km']) if m else '—'} | "
                     f"{'✅' if rd['hotspot_hit'] else '❌'} | {mappls} |")
        L.append("")

    L += ["## Advisory-only events (predictions shown, not scored)", "",
          "| Event | Date | Venue | Crowd | T-60 | Start | T+30 | Worst corridor |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r["status"] == "reported":
            continue
        p = r["predicted"]
        crowd = f"{r['crowd']:,}" + (" (assumed)" if r["crowd_assumed"] else "")
        L.append(f"| {r['name']} | {r['date']} | {r['venue']} | {crowd} | {p['T-60']['los']} | "
                 f"{p['start']['los']} | {p['T+30']['los']} | {p[r['peak_phase']]['worst_corridor']} |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def main():
    with open(os.path.join(HERE, "verified_events.json"), encoding="utf-8") as fh:
        events = [e for e in json.load(fh)["events"] if e.get("date") and e.get("start_time")]
    client = app.app.test_client()
    results = []
    for e in events:
        print(f"  {e['id']}", flush=True)
        results.append(run_event(client, e))
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, ensure_ascii=False)
    write_report(results, os.path.join(HERE, "report.md"))
    print("wrote backtest/report.md and backtest/results.json")


if __name__ == "__main__":
    main()
