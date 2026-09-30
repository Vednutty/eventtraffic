"""
Third-party traffic data for backtests (Mappls today; TomTom/HERE fit the same shape).

What each provider must answer: "what did traffic on road R actually look like on
date D between times T1–T2?" — measured speeds or delay for THAT day, not a
typical-day average. Mappls' public routing API offers live traffic and typical
(predictive) ETAs only; date-specific history has to come from a data export
agreed with Mappls. Until then this reads whatever has been pasted into
verified_events.json under provider_data.<name>:

    "provider_data": {
      "mappls": {
        "retrieved": "2026-10-01", "product": "<export name from Mappls>",
        "roads": {
          "Thane-Belapur Road": {"window": "18:00-21:00", "avg_speed_kmph": 9,
                                  "freeflow_speed_kmph": 45, "delay_min": 55}
        }
      }
    }

Road keys are the model's corridor / feeder names (same as observed.roads[].model_names).
"""
import os

PROVIDERS = {
    # Any ONE of these credential sets is enough: a static key, or an OAuth
    # client pair (exchanged for short-lived tokens — preferred once hosted).
    "mappls": {"env_options": (("MAPPLS_STATIC_KEY",),
                               ("MAPPLS_CLIENT_ID", "MAPPLS_CLIENT_SECRET")),
               "signup": "https://developer.mappls.com"},
}


def status(name):
    cfg = PROVIDERS[name]
    found = [opt for opt in cfg["env_options"] if all(os.environ.get(v) for v in opt)]
    if found:
        return (f"API keys set via {' + '.join(found[0])} "
                "(live/typical traffic only — date-specific history needs an export)")
    wanted = " or ".join(" + ".join(opt) for opt in cfg["env_options"])
    return f"no API keys (set {wanted})"


def road_data(event, name, model_road):
    """Stored provider measurement for one road on the event day, or None."""
    return (event.get("provider_data", {}).get(name, {})
                 .get("roads", {}).get(model_road))


def congestion_ratio(obs):
    """Measured speed as a share of free flow, inverted: 0 = free, 1 = standstill."""
    if not obs or not obs.get("avg_speed_kmph") or not obs.get("freeflow_speed_kmph"):
        return None
    return round(1 - obs["avg_speed_kmph"] / obs["freeflow_speed_kmph"], 2)
