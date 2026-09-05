from flask import Flask, jsonify, send_from_directory, request
import base64
import math
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

try:
    from flask_cors import CORS
    _cors_ok = True
except ImportError:
    _cors_ok = False

try:
    import requests as req
    _requests_ok = True
except ImportError:
    _requests_ok = False

app = Flask(__name__)
if _cors_ok:
    CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Venues ────────────────────────────────────────────────────────────────────
VENUES = {
    "wankhede": {"lat": 18.9388, "lng": 72.8251, "name": "Wankhede Stadium",
                 "address": "D Rd, Churchgate, Mumbai 400020", "parking_capacity": 500},
    "dome":     {"lat": 18.9865, "lng": 72.8155, "name": "Dome @ NSCI SVP Stadium, Worli",
                 "address": "D Wing, NSCI SVP Stadium, Lala Lajpatrai Marg, Worli, Mumbai 400018", "parking_capacity": 800},
    # Centre verified against OSM (way "Dr. D.Y. Patil Cricket Stadium") — the
    # previous 19.0433/73.0278 sat ~200 m NE of the stadium, so the map centred
    # on an empty patch beside it and the venue looked absent.
    "dypatil":  {"lat": 19.04176, "lng": 73.02674, "name": "DY Patil Stadium",
                 "address": "Sector 7, Nerul, Navi Mumbai 400706", "parking_capacity": 3000},
    "nesco":    {"lat": 19.1493, "lng": 72.8542, "name": "NESCO / Bombay Exhibition Centre",
                 "address": "Western Express Hwy, Goregaon East, Mumbai 400063", "parking_capacity": 2500},
    "mmrda":    {"lat": 19.0674, "lng": 72.8613, "name": "MMRDA Ground, BKC",
                 "address": "G Block, Bandra Kurla Complex, Bandra East, Mumbai 400051", "parking_capacity": 2000},
    # Centre verified against OSM way "Mahalaxmi Racecourse" (18.98589/72.81850);
    # the old 18.9852/72.8198 pin sat 157 m off, which skewed the venue-radius
    # and road-proximity maths even though the footprint polygon was accurate.
    "mahalaxmi":{"lat": 18.98589, "lng": 72.81850, "name": "Mahalaxmi Racecourse (RWITC)",
                 "address": "Keshavrao Khadye Marg, Mahalaxmi, Mumbai 400034", "parking_capacity": 1000},
}

# ── Road parameters ───────────────────────────────────────────────────────────
FREE_FLOW_SPEEDS = {"arterial": 40, "sub_arterial": 30, "local": 20}
ROAD_CAPACITY    = {"arterial": 1800, "sub_arterial": 1200, "local": 600}
ROAD_WEIGHT      = {"arterial": 6,    "sub_arterial": 4,    "local": 2}
ROAD_SHARE       = {"arterial": 0.50, "sub_arterial": 0.35, "local": 0.15}

# ── Base transport splits ─────────────────────────────────────────────────────
BASE_TRANSPORT_SPLIT = {
    "wankhede": {"train_metro": 0.60, "car_cab": 0.25, "foot_auto": 0.15},
    "dome":     {"train_metro": 0.35, "car_cab": 0.45, "foot_auto": 0.20},
    "dypatil":  {"train_metro": 0.25, "car_cab": 0.60, "foot_auto": 0.15},
    # Ram Mandir + Goregaon stations abut NESCO → strong rail share.
    "nesco":    {"train_metro": 0.50, "car_cab": 0.35, "foot_auto": 0.15},
    # BKC is car-dominated (weak direct rail); metro + Bandra/Kurla feed the rest.
    "mmrda":    {"train_metro": 0.30, "car_cab": 0.55, "foot_auto": 0.15},
    # Mahalaxmi station adjoins the racecourse → very high rail/metro share.
    "mahalaxmi":{"train_metro": 0.55, "car_cab": 0.30, "foot_auto": 0.15},
}

# ── Genre arrival profiles ────────────────────────────────────────────────────
# Each entry: (minutes_before, time_factor)  positive = before event, negative = after
GENRE_PROFILES = {
    "western_superstar": [
        (240, 0.15), (180, 0.35), (120, 0.60), (90, 0.78),
        (60,  0.90), (30,  1.00), (0,   0.70), (-30, 0.85),
        (-60, 0.62), (-90, 0.38), (-120, 0.20),
    ],
    "indian_mainstream": [
        (240, 0.08), (180, 0.18), (120, 0.35), (90, 0.55),
        (60,  0.80), (30,  1.00), (0,   0.78), (-30, 0.90),
        (-60, 0.68), (-90, 0.42), (-120, 0.22),
    ],
    "bollywood_party": [
        (240, 0.05), (180, 0.12), (120, 0.22), (90, 0.38),
        (60,  0.62), (30,  0.88), (0,   1.00), (-30, 0.95),
        (-60, 0.78), (-90, 0.60), (-120, 0.72),   # post-midnight cab surge
    ],
    "sports": [
        (180, 0.25), (120, 0.45), (90, 0.65), (60, 0.85),
        (30,  1.00), (0,   0.75), (-30, 0.90), (-60, 0.70),
        (-90, 0.45), (-120, 0.25),
    ],
    "edm": [
        (240, 0.20), (180, 0.40), (120, 0.58), (90, 0.73),
        (60,  0.85), (30,  0.95), (0,   1.00), (-30, 0.80),
        (-60, 0.58), (-90, 0.38), (-120, 0.22),
    ],
}

# ── Artist origin modifiers ───────────────────────────────────────────────────
ARTIST_ORIGIN_MODIFIERS = {
    "western_superstar": {"car_mult_extra": 1.35, "out_of_city_default": 25, "profile": "western_superstar"},
    "western_midtier":   {"car_mult_extra": 1.15, "out_of_city_default": 8,  "profile": "western_superstar"},
    "indian_mainstream": {"car_mult_extra": 0.90, "out_of_city_default": 15, "profile": "indian_mainstream"},
    "indian_hiphop":     {"car_mult_extra": 0.85, "out_of_city_default": 5,  "profile": "indian_mainstream"},
    "bollywood_party":   {"car_mult_extra": 1.00, "out_of_city_default": 3,  "profile": "bollywood_party"},
    "edm_festival":      {"car_mult_extra": 1.20, "out_of_city_default": 10, "profile": "edm"},
    "sports":            {"car_mult_extra": 1.00, "out_of_city_default": 5,  "profile": "sports"},
}

# ── Nearest transit stations (for special train pedestrian marker) ─────────────
NEAREST_STATIONS = {
    "wankhede": {"name": "Churchgate",             "lat": 18.9339, "lng": 72.8270},
    "dome":     {"name": "Worli (Metro Line 3)",   "lat": 18.9935, "lng": 72.8176},
    "dypatil":  {"name": "Nerul Railway Station",  "lat": 19.0323, "lng": 73.0248},
    "nesco":    {"name": "Ram Mandir Station",     "lat": 19.1510, "lng": 72.8502},
    "mmrda":    {"name": "BKC Metro (Line 2B)",    "lat": 19.0607, "lng": 72.8547},
    "mahalaxmi":{"name": "Mahalaxmi Station",      "lat": 18.9825, "lng": 72.8242},
}

# ── Uber/Ola designated pickup corridor per venue ─────────────────────────────
UBER_ZONE_CORRIDOR = {
    "wankhede": "Veer Nariman Road",
    "dome":     "Dr Annie Besant Road",
    "dypatil":  "Palm Beach Road",
    "nesco":    "Western Express Highway Service Road",
    "mmrda":    "Bandra Kurla Complex Road",
    "mahalaxmi":"Keshavrao Khadye Marg",
}

# ── Road corridors ────────────────────────────────────────────────────────────
ROAD_CORRIDORS = {
    "wankhede": [
        {"road_name": "Marine Drive",        "road_type": "arterial",     "direction": "inbound",
         "points": [[18.9350,72.8230],[18.9310,72.8220],[18.9270,72.8210]]},
        {"road_name": "Veer Nariman Road",   "road_type": "sub_arterial", "direction": "inbound",
         "points": [[18.9380,72.8300],[18.9375,72.8350],[18.9370,72.8400]]},
        {"road_name": "Churchgate north",    "road_type": "sub_arterial", "direction": "inbound",
         "points": [[18.9420,72.8260],[18.9450,72.8270],[18.9480,72.8280],[18.9510,72.8290],[18.9540,72.8300]]},
        {"road_name": "DN Road/CST corridor","road_type": "arterial",     "direction": "inbound",
         "points": [[18.9400,72.8350],[18.9420,72.8400],[18.9440,72.8450]]},
        {"road_name": "Charni Road",         "road_type": "local",        "direction": "inbound",
         "points": [[18.9530,72.8190],[18.9560,72.8180],[18.9590,72.8170]]},
        {"road_name": "Grant Road",          "road_type": "local",        "direction": "inbound",
         "points": [[18.9640,72.8200],[18.9680,72.8210],[18.9720,72.8220]]},
    ],
    "dome": [   # repositioned to the real NSCI/SVP Dome (18.9865,72.8155) on actual roads
        {"road_name": "Dr Annie Besant Road",    "road_type": "arterial",     "direction": "inbound",
         "points": [[18.98740,72.81369],[18.99033,72.81397],[18.99350,72.81470],[18.99568,72.81593]]},
        {"road_name": "Lala Lajpatrai Marg",     "road_type": "sub_arterial", "direction": "inbound",
         "points": [[18.98017,72.81268],[18.98380,72.81486],[18.98654,72.81384],[18.98740,72.81369]]},
        {"road_name": "Worli Sea Face",          "road_type": "arterial",     "direction": "inbound",
         "points": [[18.98300,72.81230],[18.98620,72.81210],[18.98900,72.81230]]},
        {"road_name": "Worli Sea Link exit ramp","road_type": "arterial",     "direction": "inbound",
         "points": [[18.98300,72.81550],[18.98480,72.81440],[18.98650,72.81380]]},
        {"road_name": "Worli Naka",              "road_type": "sub_arterial", "direction": "inbound",
         "points": [[18.99450,72.81950],[18.99250,72.81700],[18.99020,72.81450]]},
        {"road_name": "Lal Bahadur Shastri Marg","road_type": "sub_arterial","direction": "inbound",
         "points": [[18.99600,72.81780],[18.99400,72.81600],[18.99150,72.81430]]},
    ],
    "dypatil": [
        {"road_name": "Thane-Belapur Road",   "road_type": "arterial",     "direction": "inbound",
         "points": [[19.0500,73.0200],[19.0470,73.0240],[19.0433,73.0278]]},
        {"road_name": "Palm Beach Road",      "road_type": "arterial",     "direction": "inbound",
         "points": [[19.0300,73.0100],[19.0350,73.0180],[19.0400,73.0240]]},
        {"road_name": "Vashi Bridge approach","road_type": "arterial",     "direction": "inbound",
         "points": [[19.0750,72.9980],[19.0650,73.0050],[19.0550,73.0150]]},
        {"road_name": "Nerul station road",   "road_type": "sub_arterial", "direction": "inbound",
         "points": [[19.0350,73.0300],[19.0380,73.0290],[19.0410,73.0285]]},
        {"road_name": "Sector 28 internal",   "road_type": "local",        "direction": "inbound",
         "points": [[19.0433,73.0320],[19.0433,73.0300],[19.0433,73.0278]]},
    ],
    "nesco": [
        {"road_name": "Western Express Highway", "road_type": "arterial",     "direction": "inbound",
         "points": [[19.1380,72.8530],[19.1440,72.8536],[19.1493,72.8542],[19.1560,72.8548]]},
        {"road_name": "WEH Service Road",        "road_type": "sub_arterial", "direction": "inbound",
         "points": [[19.1460,72.8522],[19.1490,72.8528],[19.1520,72.8536]]},
        {"road_name": "Goregaon Link Road",      "road_type": "sub_arterial", "direction": "inbound",
         "points": [[19.1525,72.8566],[19.1510,72.8554],[19.1495,72.8544]]},
        {"road_name": "Aarey Road",              "road_type": "local",        "direction": "inbound",
         "points": [[19.1545,72.8600],[19.1520,72.8570],[19.1500,72.8550]]},
    ],
    "mmrda": [
        {"road_name": "Bandra Kurla Complex Road","road_type": "arterial",    "direction": "inbound",
         "points": [[19.0607,72.8547],[19.0640,72.8580],[19.0674,72.8613]]},
        {"road_name": "Santacruz-Chembur Link Rd","road_type": "arterial",    "direction": "inbound",
         "points": [[19.0700,72.8500],[19.0686,72.8560],[19.0680,72.8610]]},
        {"road_name": "BKC - CST Link Road",      "road_type": "arterial",    "direction": "inbound",
         "points": [[19.0688,72.8607],[19.0720,72.8590],[19.0760,72.8570]]},
        {"road_name": "Kurla feeder (SCLR east)", "road_type": "sub_arterial","direction": "inbound",
         "points": [[19.0653,72.8794],[19.0668,72.8700],[19.0674,72.8625]]},
    ],
    "mahalaxmi": [
        {"road_name": "Keshavrao Khadye Marg",   "road_type": "arterial",     "direction": "inbound",
         "points": [[18.9790,72.8240],[18.9820,72.8242],[18.9870,72.8244],[18.9910,72.8240]]},
        {"road_name": "Dr E Moses Road",         "road_type": "sub_arterial", "direction": "inbound",
         "points": [[18.9800,72.8210],[18.9810,72.8195],[18.9820,72.8180]]},
        {"road_name": "Mahalaxmi Bridge / Jacob Circle","road_type": "arterial","direction": "inbound",
         "points": [[18.9770,72.8270],[18.9790,72.8250],[18.9810,72.8225]]},
        {"road_name": "Worli — Dr Annie Besant approach","road_type": "sub_arterial","direction": "inbound",
         "points": [[18.9910,72.8180],[18.9885,72.8190],[18.9860,72.8198]]},
    ],
}

# ── Out-of-city arrival corridors ─────────────────────────────────────────────
OUT_OF_CITY_CORRIDORS = {
    "wankhede_airport": {
        "road_name": "BKC → South Mumbai (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[19.0580,72.8360],[19.0200,72.8380],[18.9950,72.8320],[18.9640,72.8220],[18.9388,72.8251]],
    },
    "wankhede_cst": {
        "road_name": "CST / LT Intercity Arrivals (Out-of-city)", "road_type": "sub_arterial", "direction": "inbound",
        "points": [[18.9397,72.8354],[18.9420,72.8310],[18.9388,72.8251]],
    },
    "dome_airport": {
        "road_name": "Airport / BKC → Sea Link (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[19.0580,72.8360],[19.0320,72.8340],[19.0100,72.8280],[18.9920,72.8175],[18.9865,72.8155]],
    },
    "dypatil_pune": {
        "road_name": "Pune / Nashik Highway → Navi Mumbai (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[18.9650,73.1350],[19.0000,73.0900],[19.0250,73.0600],[19.0433,73.0278]],
    },
    "nesco_airport": {
        "road_name": "Airport / WEH North → Goregaon (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[19.2200,72.8580],[19.1900,72.8560],[19.1650,72.8550],[19.1493,72.8542]],
    },
    "mmrda_airport": {
        "road_name": "Airport / WEH → BKC (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[19.1100,72.8650],[19.0950,72.8550],[19.0800,72.8600],[19.0674,72.8613]],
    },
    "mahalaxmi_intercity": {
        "road_name": "Sea Link / Eastern Fwy → Mahalaxmi (Out-of-city)", "road_type": "arterial", "direction": "inbound",
        "points": [[19.0250,72.8200],[19.0050,72.8230],[18.9900,72.8210],[18.9852,72.8198]],
    },
}

OUT_OF_CITY_CORRIDOR_MAP = {
    "wankhede": {
        "western_superstar": ["wankhede_airport"],
        "western_midtier":   ["wankhede_airport"],
        "indian_mainstream": ["wankhede_cst"],
    },
    "dome": {
        "western_superstar": ["dome_airport"],
        "western_midtier":   ["dome_airport"],
        "indian_mainstream": [],
    },
    "dypatil": {
        "western_superstar": ["dypatil_pune"],
        "western_midtier":   ["dypatil_pune"],
        "indian_mainstream": ["dypatil_pune"],
        "sports": [],
    },
    "nesco": {
        "western_superstar": ["nesco_airport"],
        "western_midtier":   ["nesco_airport"],
        "indian_mainstream": [],
    },
    "mmrda": {
        "western_superstar": ["mmrda_airport"],
        "western_midtier":   ["mmrda_airport"],
        "indian_mainstream": ["mmrda_airport"],
    },
    "mahalaxmi": {
        "western_superstar": ["mahalaxmi_intercity"],
        "western_midtier":   ["mahalaxmi_intercity"],
        "indian_mainstream": [],
    },
}

# ── Hardcoded deployment points ───────────────────────────────────────────────
HARDCODED_DEPLOYMENTS = {
    "wankhede": {
        "min_crowd": 10000,
        "points": [
            {"type": "police",    "lat": 18.9339, "lng": 72.8270, "location_name": "Churchgate Station junction",
             "recommendation": "Deploy constables at platform exit; coordinate with railway police; manage pedestrian flow toward Marine Drive and Veer Nariman Road"},
            {"type": "police",    "lat": 18.9270, "lng": 72.8210, "location_name": "Marine Drive / Nariman Point split",
             "recommendation": "Manual signal override; restrict right-turn onto Marine Drive during peak dispersal window; direct cabs to holding zone"},
            {"type": "barricade", "lat": 18.9370, "lng": 72.8400, "location_name": "Veer Nariman Road choke point",
             "barricade_type": "Lane restriction",
             "restrict_lanes": "Reduce to single inbound lane; keep outbound clear for dispersal",
             "alternative_route": "Via Churchgate north corridor"},
        ],
    },
    "dome": {
        "min_crowd": 5000,
        "points": [
            {"type": "police",    "lat": 18.98700, "lng": 72.81430, "location_name": "NSCI Gate / Lala Lajpatrai Marg",
             "recommendation": "Marshal the venue access off Lala Lajpatrai Marg; separate the cab drop-off from the walk-in entry; no stopping on the carriageway"},
            {"type": "police",    "lat": 18.99050, "lng": 72.81440, "location_name": "Dr Annie Besant Rd / Worli Naka approach",
             "recommendation": "Manual signal at the Annie Besant Rd approach; meter inbound flow toward the venue; divert overflow via Worli Naka"},
            {"type": "barricade", "lat": 18.98450, "lng": 72.81490, "location_name": "Lala Lajpatrai Marg (Worli Sea Face side)",
             "barricade_type": "Lane restriction",
             "restrict_lanes": "Reduce to single inbound lane pre-event; reverse to outbound-only for the 60-min post-event window",
             "alternative_route": "Via Dr Annie Besant Road northbound"},
        ],
    },
    "dypatil": {
        "min_crowd": 15000,
        "points": [
            {"type": "police",    "lat": 19.0750, "lng": 72.9980, "location_name": "Vashi Bridge toll",
             "recommendation": "Open all toll lanes; suspend toll collection 30 min before to 2 hr after event; deploy 3 constables at merge"},
            {"type": "police",    "lat": 19.0433, "lng": 73.0150, "location_name": "Thane-Belapur / Palm Beach intersection",
             "recommendation": "Manual override; priority flow on Palm Beach Rd during dispersal; no U-turns"},
            {"type": "barricade", "lat": 19.0350, "lng": 73.0300, "location_name": "Nerul station exit",
             "barricade_type": "Lane restriction",
             "restrict_lanes": "Separate pedestrian and vehicle lanes; restrict parking within 200 m",
             "alternative_route": "Via Sector 28 internal road"},
        ],
    },
    "nesco": {
        "min_crowd": 6000,
        "points": [
            {"type": "police",    "lat": 19.1510, "lng": 72.8502, "location_name": "Ram Mandir station exit",
             "recommendation": "Coordinate with railway police; channel pedestrians onto WEH service road; prevent spillover onto highway shoulder"},
            {"type": "police",    "lat": 19.1493, "lng": 72.8530, "location_name": "WEH–NESCO service road merge",
             "recommendation": "Manual signal override on Western Express Highway slip; hold cabs in designated bay; no stopping on main carriageway"},
            {"type": "barricade", "lat": 19.1520, "lng": 72.8560, "location_name": "Goregaon Link Road approach",
             "barricade_type": "Lane restriction",
             "restrict_lanes": "Reduce to single inbound lane; keep outbound clear for dispersal",
             "alternative_route": "Via Aarey Road"},
        ],
    },
    "mmrda": {
        "min_crowd": 12000,
        "points": [
            {"type": "police",    "lat": 19.0607, "lng": 72.8547, "location_name": "BKC Metro / G-Block junction",
             "recommendation": "Marshals at metro exit; one-way pedestrian flow on BKC Road toward gates; no parking on G-Block service lanes"},
            {"type": "police",    "lat": 19.0686, "lng": 72.8590, "location_name": "SCLR / CST Link Road merge",
             "recommendation": "Priority flow on SCLR during dispersal; suspend right turns; 3 constables at the merge"},
            {"type": "barricade", "lat": 19.0668, "lng": 72.8700, "location_name": "BKC East (Kurla approach)",
             "barricade_type": "One-way flow",
             "restrict_lanes": "Inbound only pre-event; reverse to outbound-only for 60 min post-event",
             "alternative_route": "Via Bandra Kurla Complex Road west"},
        ],
    },
    "mahalaxmi": {
        "min_crowd": 8000,
        "points": [
            {"type": "police",    "lat": 18.9825, "lng": 72.8242, "location_name": "Mahalaxmi station / Keshavrao Khadye Marg",
             "recommendation": "Coordinate with railway police; manage level-crossing pedestrian surge; channel toward east gate"},
            {"type": "police",    "lat": 18.9785, "lng": 72.8255, "location_name": "Jacob Circle / Mahalaxmi Bridge",
             "recommendation": "Manual override at circle; priority flow on Dr E Moses Rd; restrict heavy vehicles during window"},
            {"type": "barricade", "lat": 18.9905, "lng": 72.8185, "location_name": "Science Centre metro approach",
             "barricade_type": "Lane restriction",
             "restrict_lanes": "Separate pedestrian and vehicle lanes on the metro feeder",
             "alternative_route": "Via Dr Annie Besant Road"},
        ],
    },
}

DIVERSION_SUGGESTIONS = {
    "wankhede": {
        "Marine Drive":          {"via": "Veer Nariman Road",           "time_saving_min": 8},
        "DN Road/CST corridor":  {"via": "Churchgate north",            "time_saving_min": 5},
        "Churchgate north":      {"via": "Grant Road → Marine Drive",   "time_saving_min": 6},
    },
    "dome": {
        "Dr Annie Besant Road":     {"via": "Worli Sea Face road",           "time_saving_min": 7},
        "Worli Sea Link exit ramp": {"via": "Annie Besant Road southbound",  "time_saving_min": 10},
    },
    "dypatil": {
        "Thane-Belapur Road":    {"via": "Palm Beach Road",             "time_saving_min": 9},
        "Vashi Bridge approach": {"via": "Nerul-Belapur Road (NH-4B)",  "time_saving_min": 12},
    },
    "nesco": {
        "Western Express Highway": {"via": "WEH Service Road",          "time_saving_min": 8},
        "Goregaon Link Road":      {"via": "Aarey Road",                "time_saving_min": 6},
    },
    "mmrda": {
        "Bandra Kurla Complex Road":  {"via": "Santacruz-Chembur Link Rd", "time_saving_min": 10},
        "BKC - CST Link Road":        {"via": "Bandra Kurla Complex Road", "time_saving_min": 7},
    },
    "mahalaxmi": {
        "Keshavrao Khadye Marg":   {"via": "Dr E Moses Road",           "time_saving_min": 7},
        "Mahalaxmi Bridge / Jacob Circle": {"via": "Dr Annie Besant Road","time_saving_min": 9},
    },
}

PARKING_OVERFLOW = {
    "wankhede": [
        {"name": "Azad Maidan open ground", "capacity": 300, "walk_distance": "12 min walk",
         "lat": 18.9428, "lng": 72.8326},
    ],
    "dome": [
        {"name": "Worli Fort open ground",      "capacity": 500, "walk_distance": "8 min walk",
         "lat": 18.9945, "lng": 72.8150},
        {"name": "Nehru Planetarium parking",   "capacity": 200, "walk_distance": "15 min walk + shuttle",
         "lat": 18.9757, "lng": 72.8123},
    ],
    "dypatil": [
        {"name": "Sector 15 open ground, CBD Belapur", "capacity": 2000, "walk_distance": "Free shuttle, ~10 min",
         "lat": 19.0230, "lng": 73.0280},
        {"name": "Nerul Railway Station parking",      "capacity": 800,  "walk_distance": "5 min auto / shuttle",
         "lat": 19.0323, "lng": 73.0248},
    ],
    "nesco": [
        {"name": "NESCO multi-level car park", "capacity": 2000, "walk_distance": "On-site, 3 min walk",
         "lat": 19.1500, "lng": 72.8552},
        {"name": "Oberoi Mall parking (Goregaon)", "capacity": 1200, "walk_distance": "10 min auto / shuttle",
         "lat": 19.1738, "lng": 72.8606},
    ],
    "mmrda": [
        {"name": "BKC G-Block open lots", "capacity": 1500, "walk_distance": "On-site, 5 min walk",
         "lat": 19.0660, "lng": 72.8640},
        {"name": "MCA Club / Jio World parking", "capacity": 1000, "walk_distance": "12 min walk",
         "lat": 19.0625, "lng": 72.8628},
    ],
    "mahalaxmi": [
        {"name": "RWITC east apron (near Gate 1 / Mahalaxmi Stn)", "capacity": 1000, "walk_distance": "On-site, 3 min walk",
         "lat": 18.9843, "lng": 72.8235},
        {"name": "Jacob Circle / Dr E Moses Rd lot", "capacity": 400, "walk_distance": "10 min walk + shuttle",
         "lat": 18.9786, "lng": 72.8254},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Parking facilities & Uber/Ola pickup zones  (traffic generators / bottlenecks)
# ─────────────────────────────────────────────────────────────────────────────
# capacity        = car spaces (parking) ; throughput_cph = cars/hr the lot mouth
#                   can process (queue forms on the access road when arrivals exceed it).
# type            = venue_lot | paid_structure | overflow_ground
PARKING_FACILITIES = {
    "wankhede": [
        {"id":"wk_venue","name":"Wankhede venue parking (Vinoo Mankad Rd)","type":"venue_lot",
         "lat":18.94040,"lng":72.82540,"capacity":500,"throughput_cph":300,
         "access_road":"Vinoo Mankad Road","walk_min":4,"price":0},
        {"id":"wk_garware","name":"Garware Club paid parking","type":"paid_structure",
         "lat":18.93720,"lng":72.82540,"capacity":350,"throughput_cph":210,
         "access_road":"Maharshi Karve Road","walk_min":6,"price":300},
        {"id":"wk_azad","name":"Azad Maidan overflow ground","type":"overflow_ground",
         "lat":18.94280,"lng":72.83260,"capacity":300,"throughput_cph":170,
         "access_road":"Mahapalika Marg","walk_min":12,"price":150},
    ],
    "dome": [
        {"id":"dm_venue","name":"NSCI on-site parking","type":"venue_lot",
         "lat":18.98610,"lng":72.81440,"capacity":300,"throughput_cph":180,
         "access_road":"Lala Lajpatrai Marg","walk_min":3,"price":0},
        {"id":"dm_worli","name":"Worli paid lot (Annie Besant Rd)","type":"paid_structure",
         "lat":18.98800,"lng":72.81380,"capacity":250,"throughput_cph":150,
         "access_road":"Dr Annie Besant Road","walk_min":7,"price":250},
    ],
    "dypatil": [
        {"id":"dy_venue","name":"DY Patil stadium parking","type":"venue_lot",
         "lat":19.04260,"lng":73.02520,"capacity":3000,"throughput_cph":1200,
         "access_road":"DY Patil Service Road","walk_min":5,"price":100},
        {"id":"dy_cbd","name":"Sector 15 CBD Belapur overflow (shuttle)","type":"overflow_ground",
         "lat":19.02300,"lng":73.02800,"capacity":2000,"throughput_cph":600,
         "access_road":"Palm Beach Road","walk_min":10,"price":0},
    ],
    "nesco": [
        {"id":"ns_venue","name":"NESCO multi-level car park","type":"paid_structure",
         "lat":19.15000,"lng":72.85520,"capacity":2000,"throughput_cph":900,
         "access_road":"WEH Service Road","walk_min":4,"price":200},
        {"id":"ns_oberoi","name":"Oberoi Mall overflow","type":"overflow_ground",
         "lat":19.17379,"lng":72.86060,"capacity":1200,"throughput_cph":500,
         "access_road":"Western Express Highway","walk_min":12,"price":150},
    ],
    "mmrda": [
        {"id":"mm_gblock","name":"BKC G-Block open lots","type":"venue_lot",
         "lat":19.06600,"lng":72.86400,"capacity":1500,"throughput_cph":700,
         "access_road":"Bandra Kurla Complex Road","walk_min":5,"price":300},
        {"id":"mm_jio","name":"Jio World / MCA paid parking","type":"paid_structure",
         "lat":19.06250,"lng":72.86280,"capacity":1000,"throughput_cph":500,
         "access_road":"Bandra Kurla Complex Road","walk_min":12,"price":400},
    ],
    "mahalaxmi": [
        {"id":"mx_apron","name":"RWITC east apron parking","type":"venue_lot",
         "lat":18.98430,"lng":72.82350,"capacity":1000,"throughput_cph":500,
         "access_road":"Keshavrao Khadye Marg","walk_min":3,"price":200},
        {"id":"mx_jacob","name":"Jacob Circle / E Moses Rd lot","type":"overflow_ground",
         "lat":18.97860,"lng":72.82540,"capacity":400,"throughput_cph":220,
         "access_road":"Dr E Moses Road","walk_min":10,"price":150},
    ],
}

# capacity_pph = people/hr the zone can pick up smoothly (bays × turnover × occupancy).
PICKUP_ZONES = {
    "wankhede": [
        {"id":"wk_marine","name":"Marine Drive Uber/Ola zone","type":"uber_ola",
         "lat":18.93960,"lng":72.82420,"capacity_pph":2400,"serves":"West Gates","access_road":"Marine Drive"},
        {"id":"wk_droad","name":"D Road taxi / auto stand","type":"taxi",
         "lat":18.93860,"lng":72.82748,"capacity_pph":1400,"serves":"East Gates","access_road":"Maharshi Karve Road"},
    ],
    "dome": [
        {"id":"dm_pickup_west","name":"Lala Lajpatrai Marg Uber/Ola zone","type":"uber_ola",
         "lat":18.98620,"lng":72.81440,"capacity_pph":1500,"serves":"Entrance C (West)","access_road":"Lala Lajpatrai Marg"},
        {"id":"dm_pickup_east","name":"Dr Annie Besant Rd Uber/Ola zone","type":"uber_ola",
         "lat":18.98700,"lng":72.81610,"capacity_pph":1500,"serves":"Entrance A (East)","access_road":"Dr Annie Besant Road"},
    ],
    "dypatil": [
        {"id":"dy_palm","name":"Palm Beach Rd Uber/Ola zone","type":"uber_ola",
         "lat":19.04180,"lng":73.02860,"capacity_pph":3500,"serves":"East Gate","access_road":"Palm Beach Road"},
    ],
    "nesco": [
        {"id":"ns_weh","name":"WEH service road Uber/Ola zone","type":"uber_ola",
         "lat":19.14960,"lng":72.85290,"capacity_pph":2600,"serves":"West Entry","access_road":"WEH Service Road"},
    ],
    "mmrda": [
        {"id":"mm_pickup","name":"BKC designated Uber/Ola zone","type":"uber_ola",
         "lat":19.06520,"lng":72.86110,"capacity_pph":4000,"serves":"Main Entry (West)","access_road":"Bandra Kurla Complex Road"},
    ],
    "mahalaxmi": [
        {"id":"mx_pickup","name":"Mahalaxmi Uber/Ola zone (Keshavrao Khadye Marg)","type":"uber_ola",
         "lat":18.98070,"lng":72.82150,"capacity_pph":2400,"serves":"Main Entry (South)","access_road":"Keshavrao Khadye Marg"},
    ],
}

# Share of car_cab travellers who use app-cabs/taxi/auto (rest drive a private car
# and need a parking space). Higher in affluent / poor-rail-access areas (BKC).
# Share of car_cab travellers who arrive by app-cab/taxi/auto (drop-and-go) rather
# than driving a private car they must park. Central Mumbai venues have almost no
# event parking and festivals suspend it outright → cab/auto dominates; Navi-Mumbai
# DY Patil is car-oriented with a large lot, so more people actually park.
# (Calibrated against Lollapalooza 2025: parking was banned, transit/cab carried it.)
CAB_SHARE = {
    "wankhede":0.70, "dome":0.72, "dypatil":0.40,
    "nesco":0.48, "mmrda":0.58, "mahalaxmi":0.80,
}
PARKING_OCCUPANCY = 2.6   # people per private car

# VENUES[...]["parking_capacity"] decides when overflow lots are opened, so it
# must equal the parking the model actually has. The two had drifted apart
# (dome declared 800 vs 550 mapped; nesco 2500 vs 3200; mmrda 2000 vs 2500),
# so derive it from PARKING_FACILITIES — one source of truth, cannot diverge.
for _vid, _lots in PARKING_FACILITIES.items():
    if _vid in VENUES:
        VENUES[_vid]["parking_capacity"] = sum(l["capacity"] for l in _lots)


# ─────────────────────────────────────────────────────────────────────────────
# Historical learning data
# ─────────────────────────────────────────────────────────────────────────────

# Day-of-week traffic multipliers for Mumbai  (source: MMRDA surveys + Google Maps
# "Typical traffic" data). Wednesday = 1.18 is the normalisation anchor.
DOW_TRAFFIC = {
    "Monday": 1.08, "Tuesday": 1.12, "Wednesday": 1.18,
    "Thursday": 1.20, "Friday": 1.30, "Saturday": 0.88, "Sunday": 0.72,
}

# Hourly background ECI per venue on a typical weekday (index = hour 0-23).
# Reflects non-event base congestion — captures AM/PM peaks, sea-link bottleneck,
# Vashi Bridge queue etc.  Derived from Google Maps "typical traffic" layers.
HOURLY_BG_ECI = {
    "wankhede": [   # Churchgate / Marine Drive — severe twin peaks
        0.10, 0.07, 0.06, 0.05, 0.07, 0.15,   # 00-05
        0.30, 0.55, 0.73, 0.72, 0.56, 0.48,   # 06-11
        0.52, 0.55, 0.50, 0.52, 0.66, 0.83,   # 12-17
        0.88, 0.83, 0.73, 0.60, 0.46, 0.28,   # 18-23
    ],
    "dome": [       # Worli / Bandra-Worli Sea Link — sea link adds persistent burden
        0.12, 0.09, 0.07, 0.06, 0.08, 0.18,   # 00-05
        0.32, 0.58, 0.76, 0.73, 0.61, 0.55,   # 06-11
        0.59, 0.61, 0.56, 0.59, 0.71, 0.86,   # 12-17
        0.90, 0.87, 0.78, 0.65, 0.50, 0.32,   # 18-23
    ],
    "dypatil": [    # Navi Mumbai / Vashi Bridge — moderate with sharp bridge bottleneck
        0.08, 0.06, 0.05, 0.04, 0.06, 0.12,   # 00-05
        0.22, 0.45, 0.68, 0.71, 0.55, 0.46,   # 06-11
        0.49, 0.51, 0.46, 0.51, 0.63, 0.79,   # 12-17
        0.83, 0.79, 0.66, 0.53, 0.39, 0.22,   # 18-23
    ],
    "nesco": [      # Goregaon / Western Express Highway — chronic WEH twin peaks
        0.12, 0.09, 0.07, 0.06, 0.09, 0.20,   # 00-05
        0.38, 0.64, 0.82, 0.78, 0.62, 0.54,   # 06-11
        0.57, 0.59, 0.54, 0.58, 0.72, 0.86,   # 12-17
        0.90, 0.84, 0.72, 0.58, 0.44, 0.26,   # 18-23
    ],
    "mmrda": [      # BKC business district — severe weekday office peaks
        0.14, 0.10, 0.08, 0.07, 0.10, 0.22,   # 00-05
        0.42, 0.70, 0.88, 0.82, 0.66, 0.60,   # 06-11
        0.62, 0.64, 0.60, 0.64, 0.76, 0.90,   # 12-17
        0.92, 0.85, 0.72, 0.58, 0.44, 0.28,   # 18-23
    ],
    "mahalaxmi": [  # Mahalaxmi / Worli — dense central corridor, strong PM peak
        0.13, 0.10, 0.08, 0.06, 0.09, 0.19,   # 00-05
        0.34, 0.60, 0.79, 0.75, 0.60, 0.53,   # 06-11
        0.57, 0.59, 0.54, 0.58, 0.71, 0.85,   # 12-17
        0.89, 0.85, 0.74, 0.60, 0.46, 0.28,   # 18-23
    ],
}

# Per-corridor historical baselines computed from all observed events in
# event_history.json (recency-weighted mean, 90th-percentile, and max).
# These give the algorithm corridor-level calibration beyond the venue average.
CORRIDOR_HISTORY = {
    "wankhede": {
        "Marine Drive":          {"mean": 0.773, "p90": 0.910, "events": 7, "max_observed": 0.92},
        "Churchgate north":      {"mean": 0.776, "p90": 0.888, "events": 7, "max_observed": 0.90},
        "DN Road/CST corridor":  {"mean": 0.690, "p90": 0.820, "events": 7, "max_observed": 0.85},
        "Veer Nariman Road":     {"mean": 0.656, "p90": 0.762, "events": 7, "max_observed": 0.78},
        "Charni Road":           {"mean": 0.451, "p90": 0.550, "events": 7, "max_observed": 0.58},
        "Grant Road":            {"mean": 0.403, "p90": 0.502, "events": 7, "max_observed": 0.52},
    },
    "dome": {
        "Dr Annie Besant Road":     {"mean": 0.915, "p90": 0.960, "events": 4, "max_observed": 0.97},
        "Worli Sea Link exit ramp": {"mean": 0.865, "p90": 0.940, "events": 4, "max_observed": 0.95},
        "Worli Sea Face":           {"mean": 0.843, "p90": 0.913, "events": 4, "max_observed": 0.92},
        "Worli Naka":               {"mean": 0.843, "p90": 0.888, "events": 4, "max_observed": 0.90},
        "Lal Bahadur Shastri Marg": {"mean": 0.695, "p90": 0.773, "events": 4, "max_observed": 0.78},
        "Haji Ali approach":        {"mean": 0.643, "p90": 0.713, "events": 4, "max_observed": 0.72},
    },
    "dypatil": {
        "Thane-Belapur Road":    {"mean": 0.876, "p90": 0.944, "events": 7, "max_observed": 0.96},
        "Palm Beach Road":       {"mean": 0.821, "p90": 0.912, "events": 7, "max_observed": 0.92},
        "Vashi Bridge approach": {"mean": 0.806, "p90": 0.922, "events": 7, "max_observed": 0.94},
        "Nerul station road":    {"mean": 0.754, "p90": 0.810, "events": 7, "max_observed": 0.82},
        "Sector 28 internal":    {"mean": 0.604, "p90": 0.673, "events": 7, "max_observed": 0.68},
    },
    # New venues have no event_history.json yet — baselines below are seeded from
    # the road's chronic background + comparable-venue analogues (3 nominal events).
    "nesco": {
        "Western Express Highway": {"mean": 0.872, "p90": 0.940, "events": 3, "max_observed": 0.95},
        "WEH Service Road":        {"mean": 0.760, "p90": 0.850, "events": 3, "max_observed": 0.88},
        "Goregaon Link Road":      {"mean": 0.704, "p90": 0.790, "events": 3, "max_observed": 0.82},
        "Aarey Road":              {"mean": 0.560, "p90": 0.650, "events": 3, "max_observed": 0.68},
    },
    "mmrda": {
        "Bandra Kurla Complex Road":  {"mean": 0.885, "p90": 0.950, "events": 3, "max_observed": 0.96},
        "Santacruz-Chembur Link Rd":  {"mean": 0.842, "p90": 0.920, "events": 3, "max_observed": 0.94},
        "BKC - CST Link Road":        {"mean": 0.808, "p90": 0.890, "events": 3, "max_observed": 0.91},
        "Kurla feeder (SCLR east)":   {"mean": 0.730, "p90": 0.820, "events": 3, "max_observed": 0.85},
    },
    "mahalaxmi": {
        "Keshavrao Khadye Marg":   {"mean": 0.848, "p90": 0.920, "events": 3, "max_observed": 0.93},
        "Mahalaxmi Bridge / Jacob Circle": {"mean": 0.820, "p90": 0.900, "events": 3, "max_observed": 0.92},
        "Dr E Moses Road":         {"mean": 0.742, "p90": 0.830, "events": 3, "max_observed": 0.86},
        "Worli — Dr Annie Besant approach": {"mean": 0.690, "p90": 0.770, "events": 3, "max_observed": 0.80},
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Foot-traffic data  — gates, pedestrian corridors, choke-points
# ─────────────────────────────────────────────────────────────────────────────

# Real building/ground footprints (simplified OSM polygons, [lat,lng] rings).
# Drawn as the venue outline on the map so scale & gate positions are obvious.
VENUE_FOOTPRINTS = {
    "wankhede": [[18.93701,72.82506],[18.93703,72.82497],[18.93704,72.82485],[18.93869,72.82483],[18.94002,72.82475],[18.94008,72.82503],[18.93976,72.82513],[18.94002,72.82635],[18.93933,72.82651],[18.93899,72.82657],[18.93841,72.82668],[18.93812,72.82671],[18.93793,72.82582],[18.93797,72.82529],[18.93804,72.82507],[18.93701,72.82506]],
    "dome": [[18.98685,72.81482],[18.98723,72.81572],[18.987,72.81567],[18.98715,72.81589],[18.98628,72.81628],[18.98634,72.81605],[18.98624,72.81603],[18.98612,72.81627],[18.98597,72.81538],[18.986,72.81528],[18.9859,72.81524],[18.98586,72.81513],[18.9866,72.8148],[18.98661,72.81501],[18.9867,72.81503],[18.98685,72.81482]],
    # Outer bowl only. This was previously the outer ring AND an inner ring
    # concatenated into ONE sequence (a donut drawn as a single polygon): it
    # self-intersected, rendered as a seamed ring in Leaflet, and made the
    # shoelace area return outer-minus-inner (15,935 m² instead of 34,953 m²).
    # Bearing sweep is now 360° (a simple ring) rather than 582°.
    "dypatil": [[19.04134,73.026],[19.04206,73.02565],[19.04274,73.02594],[19.04302,73.02639],[19.04299,73.02715],[19.04251,73.02771],[19.0418,73.02779],[19.0412,73.02729],[19.04134,73.026]],
    "nesco": [[19.15202,72.85614],[19.15217,72.85465],[19.15127,72.85463],[19.15129,72.85408],[19.15162,72.85359],[19.15164,72.85259],[19.14874,72.85242],[19.14714,72.85219],[19.14695,72.85343],[19.14639,72.85508],[19.14676,72.85518],[19.14674,72.85569],[19.15198,72.85614],[19.15202,72.85614]],
    "mmrda": [[19.06603,72.86037],[19.0659,72.8611],[19.06625,72.86112],[19.06636,72.86116],[19.06646,72.86121],[19.06653,72.86128],[19.06714,72.86222],[19.06794,72.86166],[19.06803,72.86163],[19.06813,72.86167],[19.06888,72.86152],[19.06884,72.86115],[19.0684,72.86109],[19.06769,72.86094],[19.06627,72.8604],[19.06603,72.86037]],
    "mahalaxmi": [[18.979,72.81585],[18.98023,72.82142],[18.98036,72.82172],[18.98292,72.8238],[18.98374,72.8243],[18.98442,72.82444],[18.98495,72.8244],[18.98993,72.82139],[18.9895,72.82017],[18.99137,72.82058],[18.99134,72.8205],[18.98943,72.81705],[18.98923,72.81637],[18.98896,72.8163],[18.98881,72.81629],[18.98857,72.81637],[18.98839,72.81643],[18.9882,72.81654],[18.98799,72.81658],[18.98556,72.8173],[18.98546,72.81737],[18.98421,72.8153],[18.98383,72.81533],[18.98269,72.81508],[18.98255,72.81508],[18.979,72.81585]],
}

# Venue entry/exit gates with realistic lane capacities.
# capacity_pph = max pedestrians per hour under normal scanning/turnstile conditions.
# Gate positions sit on the real stadium perimeter (OSM building footprints) and
# are named after the actual stands / gate numbers at each venue.
VENUE_GATES = {
    # Wankhede footprint: W≈72.8248 (Marine Drive side) · E≈72.8267 (D Road/Maharshi
    # Karve Rd side) · N≈18.9400 (Vinoo Mankad Rd) · S≈18.9370.
    "wankhede": [
        {"id":"gate_east","name":"Gates 5–7 — Sunil Gavaskar / Divecha / MCA (D Road, East)",
         "lat":18.93895,"lng":72.82650,"type":"entry","capacity_pph":6000,
         "lanes":8,"in_share":0.45,"out_share":0.30,
         "notes":"Faces Maharshi Karve Rd; main inflow from Churchgate & Marine Lines stations"},
        {"id":"gate_west","name":"Gates 1 & 4 — Grand Stand / Sachin Tendulkar (Marine Drive, West)",
         "lat":18.93910,"lng":72.82450,"type":"entry_exit","capacity_pph":3500,
         "lanes":5,"in_share":0.22,"out_share":0.22,
         "notes":"Marine Drive cab drop & walk-in; shared entry/exit"},
        {"id":"gate_north","name":"Vinoo Mankad Gate — North (Main entrance)",
         "lat":18.94000,"lng":72.82560,"type":"entry","capacity_pph":4500,
         "lanes":6,"in_share":0.28,"out_share":0.10,
         "notes":"Main gate on Vinoo Mankad Rd; VIP, players, general — fastest scanning"},
        {"id":"gate_south","name":"Gates 2–3 — Garware / Vijay Merchant (South Exit)",
         "lat":18.93705,"lng":72.82580,"type":"exit_primary","capacity_pph":7000,
         "lanes":10,"in_share":0.05,"out_share":0.38,
         "notes":"Primary post-match dispersal toward Churchgate; opens at innings break"},
    ],
    # DOME / SVP Indoor Stadium footprint (NSCI, Worli): W≈72.8148 · E≈72.8163 ·
    # N≈18.9872 · S≈18.9857. Per the venue's own architectural layout (TWKTK
    # Emergence drawing): two balanced public entrances A (East) + C (West), a
    # north production/talent entry behind the stage, and a south emergency exit.
    "dome": [
        {"id":"gate_c","name":"Entrance C — West (Lala Lajpatrai Marg side)",
         "lat":18.98640,"lng":72.81475,"type":"entry","capacity_pph":4500,
         "lanes":6,"in_share":0.46,"out_share":0.32,
         "notes":"Public entry from Lala Lajpatrai Marg; primary cab/Uber drop side"},
        {"id":"gate_a","name":"Entrance A — East (NSCI campus side)",
         "lat":18.98640,"lng":72.81615,"type":"entry","capacity_pph":4500,
         "lanes":6,"in_share":0.42,"out_share":0.30,
         "notes":"Public entry from inside the NSCI campus / Dr Annie Besant Rd approach"},
        {"id":"gate_north","name":"Production / Talent Entry — North (Stage + Party Bus)",
         "lat":18.98710,"lng":72.81545,"type":"entry_exit","capacity_pph":1500,
         "lanes":3,"in_share":0.07,"out_share":0.10,
         "notes":"Artist, crew, party-bus access behind the stage; VIP/platinum-table guests"},
        {"id":"gate_exit","name":"Emergency Exit — South",
         "lat":18.98585,"lng":72.81550,"type":"exit_primary","capacity_pph":6000,
         "lanes":9,"in_share":0.05,"out_share":0.28,
         "notes":"Egress-only south exit per fire-safety layout"},
    ],
    # DY Patil footprint: W≈73.0257 · E≈73.0278 · N≈19.0431 · S≈19.0404.
    # Gates 6–9 face Nerul station (W/SW); gates 2–5 face Juinagar station (N).
    # in_share and out_share must EACH total 1.0 across the gate set. out_share
    # previously summed to 1.10, over-allocating egress demand by 10% at every
    # DY Patil event; re-normalised below keeping the same relative weighting.
    "dypatil": [
        {"id":"gate_nerul","name":"Gates 6–9 — Nerul side (West)",
         "lat":19.04130,"lng":73.02590,"type":"entry","capacity_pph":7000,
         "lanes":10,"in_share":0.40,"out_share":0.23,
         "notes":"Closest to Nerul Station (5–10 min walk); largest general inflow"},
        {"id":"gate_juinagar","name":"Gates 2–5 — Juinagar side (North)",
         "lat":19.04290,"lng":73.02650,"type":"entry","capacity_pph":6000,
         "lanes":8,"in_share":0.28,"out_share":0.18,
         "notes":"Closest to Juinagar Station (7–8 min walk)"},
        {"id":"gate_east","name":"East Gate — Palm Beach Rd (Uber/Ola zone)",
         "lat":19.04180,"lng":73.02780,"type":"entry_exit","capacity_pph":5000,
         "lanes":7,"in_share":0.27,"out_share":0.18,
         "notes":"Cab/Uber drop on Palm Beach Rd; ticket scan outside gate"},
        {"id":"gate_south","name":"South Gate — Mass-Dispersal Exit",
         "lat":19.04125,"lng":73.02690,"type":"exit_primary","capacity_pph":9000,
         "lanes":12,"in_share":0.05,"out_share":0.41,
         "notes":"Primary post-event exit; opens 15 min before event ends"},
    ],
    # NESCO / BEC footprint: W≈72.8522 · E≈72.8561 · N≈19.1522 · S≈19.1464.
    # Multi-hall complex: two rail-fed entries (Gate 1 NE near Goregaon East; West
    # entry off the WEH service road near Ram Mandir) + hall entry + emergency exit.
    "nesco": [
        {"id":"gate_main","name":"Gate 1 — Main Entry (NE, Goregaon East)",
         "lat":19.15200,"lng":72.85600,"type":"entry","capacity_pph":5000,
         "lanes":7,"in_share":0.44,"out_share":0.28,
         "notes":"OSM-named Gate 1 — primary entrance; closest to Goregaon East station & metro"},
        {"id":"gate_west","name":"West Entry (WEH Service Rd / Ram Mandir)",
         "lat":19.14850,"lng":72.85290,"type":"entry","capacity_pph":4000,
         "lanes":6,"in_share":0.38,"out_share":0.24,
         "notes":"Off WEH service road; closest to Ram Mandir station (adjacent)"},
        {"id":"gate_hall","name":"Hall 4–5 South Entry","type":"entry_exit",
         "lat":19.14700,"lng":72.85440,"capacity_pph":3000,
         "lanes":5,"in_share":0.13,"out_share":0.18,
         "notes":"Serves pillarless Hall 4 & Hall 5; on-site parking side"},
        {"id":"gate_exit","name":"Emergency Exit — South","type":"exit_primary",
         "lat":19.14660,"lng":72.85510,"capacity_pph":5000,
         "lanes":8,"in_share":0.05,"out_share":0.30,
         "notes":"Egress-only post-event; disperses back to WEH service road"},
    ],
    # MMRDA Ground BKC footprint: W≈72.8604 · E≈72.8622 · N≈19.0689 · S≈19.0659.
    # OPEN FESTIVAL GROUND → one dominant MAIN ENTRY (frisk/scan bottleneck) on the
    # BKC-road/west side (per OSM gate cluster) + VIP + emergency exits (egress-only).
    "mmrda": [
        {"id":"gate_main","name":"MAIN ENTRY — BKC Road (West)",
         "lat":19.06560,"lng":72.86045,"type":"entry","capacity_pph":7000,
         "lanes":12,"in_share":0.72,"out_share":0.38,
         "notes":"Single general-admission entry — all ingress funnels here (frisk + scan); from BKC metro & Bandra"},
        {"id":"gate_vip","name":"VIP / Guest Entry (SW)","type":"entry_exit",
         "lat":19.06640,"lng":72.86075,"capacity_pph":2500,
         "lanes":4,"in_share":0.18,"out_share":0.12,
         "notes":"VIP, artist, media; pre-scanned fast lane"},
        {"id":"gate_em_n","name":"Emergency Exit — North (G-Block)","type":"exit_primary",
         "lat":19.06860,"lng":72.86130,"capacity_pph":6000,
         "lanes":9,"in_share":0.06,"out_share":0.28,
         "notes":"Egress-only; opens at headliner end — disperses to G-Block roads"},
        {"id":"gate_em_e","name":"Emergency Exit — East (Kurla side)","type":"exit_primary",
         "lat":19.06760,"lng":72.86200,"capacity_pph":5000,
         "lanes":7,"in_share":0.04,"out_share":0.22,
         "notes":"Egress-only; disperses toward SCLR / Kurla"},
    ],
    # Mahalaxmi Racecourse footprint (oval): W≈72.8151 · E≈72.8244 · N≈18.9914 · S≈18.9790.
    # Per the Lollapalooza site map: ONE MAIN ENTRY at the SOUTH on Keshavrao Khadye
    # Marg (OSM Gate 3/4) + a VIP/Step-Up entry near the station + emergency exits.
    "mahalaxmi": [
        {"id":"gate_main","name":"MAIN ENTRY — Keshavrao Khadye Marg (South)",
         "lat":18.97990,"lng":72.82010,"type":"entry","capacity_pph":7000,
         "lanes":12,"in_share":0.72,"out_share":0.38,
         "notes":"Lollapalooza/NH7 main gate (OSM Gate 3/4) — all GA ingress funnels here off Keshavrao Khadye Marg"},
        {"id":"gate_vip","name":"VIP / Step-Up Entry (East, near Mahalaxmi Stn)","type":"entry_exit",
         "lat":18.98300,"lng":72.82400,"capacity_pph":2500,
         "lanes":4,"in_share":0.18,"out_share":0.12,
         "notes":"Step-Up/VIP & guest entry; closest to Mahalaxmi station"},
        {"id":"gate_em_n","name":"Emergency Exit — North (Metro side)","type":"exit_primary",
         "lat":18.99000,"lng":72.82150,"capacity_pph":5000,
         "lanes":8,"in_share":0.06,"out_share":0.28,
         "notes":"Egress-only; disperses toward Science Centre / Atre Chowk metro"},
        {"id":"gate_em_w","name":"Emergency Exit — West (Infield)","type":"exit_primary",
         "lat":18.98400,"lng":72.81520,"capacity_pph":4500,
         "lanes":7,"in_share":0.04,"out_share":0.22,
         "notes":"Egress-only; infield dispersal toward Worli / Dr E Moses Rd"},
    ],
}

# Event-specific gate setups. VENUE_GATES above is the base ("default") setup; per
# venue we add the OTHER real configurations used for different event types
# (researched from past events). Selecting a layout swaps which gates are open as
# entries vs exits, their capacity and demand share — because, e.g., a cricket match
# admits by stand while a standing concert funnels everyone to one GA gate.
EVENT_LAYOUTS = {
    "wankhede": {
        "_default_label": "Cricket — stand-wise entry (IPL / international)",
        "concert": {
            "label": "Concert (GA + reserved)",
            "desc": "Vinoo Mankad (North) becomes the single GA main entry; Marine Drive gates are VIP/Golden-Circle; South is exit-only.",
            "gates": [
                {"id":"gate_north","name":"Vinoo Mankad Gate — MAIN GA Entry (North)","lat":18.94000,"lng":72.82560,
                 "type":"entry","capacity_pph":7000,"lanes":12,"in_share":0.55,"out_share":0.25,
                 "notes":"Concert GA funnels here off Vinoo Mankad Rd — frisk + scan bottleneck"},
                # in_share across this layout must total 1.0 — it summed to 1.05,
                # over-allocating ingress by 5% at every Wankhede concert.
                {"id":"gate_east","name":"Gates 5–7 — Reserved Stands (D Road, East)","lat":18.93895,"lng":72.82650,
                 "type":"entry","capacity_pph":5000,"lanes":7,"in_share":0.25,"out_share":0.25,
                 "notes":"Reserved-seating entry from the Churchgate side"},
                {"id":"gate_west","name":"Gates 1 & 4 — VIP / Golden Circle (Marine Drive)","lat":18.93910,"lng":72.82450,
                 "type":"entry_exit","capacity_pph":3000,"lanes":5,"in_share":0.15,"out_share":0.20,
                 "notes":"VIP / Golden Circle entry from Marine Drive"},
                {"id":"gate_south","name":"Gates 2–3 — South Exit","lat":18.93705,"lng":72.82580,
                 "type":"exit_primary","capacity_pph":7000,"lanes":10,"in_share":0.05,"out_share":0.30,
                 "notes":"Primary post-show dispersal toward Churchgate"},
            ],
        },
    },
    "dome": {
        "_default_label": "Standing concert — A+C dual entry (Floor + GA Stands)",
        "seated": {
            "label": "Seated show (comedy / awards / theatre)",
            "desc": "Both public entrances (A east, C west) operate; tier-seated audiences arrive more spread out; production/VIP north entry handles platinum-table guests.",
            "gates": [
                {"id":"gate_c","name":"Entrance C — West (Floor + GA Stands)","lat":18.98640,"lng":72.81475,
                 "type":"entry","capacity_pph":4000,"lanes":6,"in_share":0.42,"out_share":0.28,
                 "notes":"Floor + GA-Stands tier access"},
                {"id":"gate_a","name":"Entrance A — East (Floor + GA Stands)","lat":18.98640,"lng":72.81615,
                 "type":"entry","capacity_pph":4000,"lanes":6,"in_share":0.40,"out_share":0.28,
                 "notes":"Floor + GA-Stands tier access"},
                {"id":"gate_north","name":"VIP / Platinum-Table Entry — North","lat":18.98710,"lng":72.81545,
                 "type":"entry_exit","capacity_pph":1500,"lanes":3,"in_share":0.13,"out_share":0.14,
                 "notes":"VIP, platinum-table guests, talent & crew"},
                {"id":"gate_exit","name":"Emergency Exit — South","lat":18.98585,"lng":72.81550,
                 "type":"exit_primary","capacity_pph":6000,"lanes":9,"in_share":0.05,"out_share":0.30,
                 "notes":"Egress-only south exit"},
            ],
        },
    },
    "dypatil": {
        "_default_label": "Concert — wristband gates (Coldplay-style)",
        "cricket": {
            "label": "Cricket / Football — stand-wise entry",
            "desc": "All four gates open as balanced stand-wise entries (gate printed on the ticket); South opens for general stands too.",
            "gates": [
                {"id":"gate_nerul","name":"Gates 6–9 — Nerul side (West)","lat":19.04130,"lng":73.02590,
                 "type":"entry","capacity_pph":7000,"lanes":10,"in_share":0.30,"out_share":0.25,
                 "notes":"Nerul-station stands"},
                {"id":"gate_juinagar","name":"Gates 2–5 — Juinagar side (North)","lat":19.04290,"lng":73.02650,
                 "type":"entry","capacity_pph":6000,"lanes":8,"in_share":0.25,"out_share":0.22,
                 "notes":"Juinagar-station stands"},
                {"id":"gate_east","name":"East Gate — Members / VIP (Palm Beach)","lat":19.04180,"lng":73.02780,
                 "type":"entry","capacity_pph":5000,"lanes":7,"in_share":0.25,"out_share":0.18,
                 "notes":"Members / VIP / corporate boxes"},
                {"id":"gate_south","name":"South Gate — General stands + Exit","lat":19.04125,"lng":73.02690,
                 "type":"entry_exit","capacity_pph":9000,"lanes":12,"in_share":0.20,"out_share":0.35,
                 "notes":"General-stand entry; primary mass exit post-match"},
            ],
        },
    },
    "mahalaxmi": {
        "_default_label": "Festival — single main entry (Lollapalooza / NH7)",
        "dual_entry": {
            "label": "Large festival — dual entry (relieve main)",
            "desc": "A second GA/Step-Up entry near Mahalaxmi station opens to relieve the south main gate at peak ingress.",
            "gates": [
                {"id":"gate_main","name":"MAIN ENTRY — Keshavrao Khadye Marg (South)","lat":18.97990,"lng":72.82010,
                 "type":"entry","capacity_pph":7000,"lanes":12,"in_share":0.52,"out_share":0.32,
                 "notes":"Primary GA entry (OSM Gate 1/3/4)"},
                {"id":"gate_vip","name":"East Entry — Mahalaxmi Station (2nd GA)","lat":18.98300,"lng":72.82400,
                 "type":"entry","capacity_pph":4500,"lanes":7,"in_share":0.40,"out_share":0.18,
                 "notes":"Second GA/Step-Up entry opened to relieve the main gate"},
                {"id":"gate_em_n","name":"Emergency Exit — North","lat":18.99000,"lng":72.82150,
                 "type":"exit_primary","capacity_pph":5000,"lanes":8,"in_share":0.05,"out_share":0.28,
                 "notes":"Egress-only"},
                {"id":"gate_em_w","name":"Emergency Exit — West (Infield)","lat":18.98400,"lng":72.81520,
                 "type":"exit_primary","capacity_pph":4500,"lanes":7,"in_share":0.03,"out_share":0.22,
                 "notes":"Egress-only"},
            ],
        },
    },
    "mmrda": {
        "_default_label": "Concert — single main entry",
        "expo": {
            "label": "Exhibition / Expo (multi-gate)",
            "desc": "Trade-expo setup: several gates open as balanced entries; steady all-day flow rather than a fixed-start peak.",
            "gates": [
                {"id":"gate_main","name":"Gate A — West (BKC Road, main hall)","lat":19.06560,"lng":72.86045,
                 "type":"entry","capacity_pph":5000,"lanes":8,"in_share":0.35,"out_share":0.28,"notes":"Main hall entry"},
                {"id":"gate_vip","name":"Gate B — SW (Delegate registration)","lat":19.06640,"lng":72.86075,
                 "type":"entry_exit","capacity_pph":3500,"lanes":5,"in_share":0.25,"out_share":0.22,"notes":"Delegate / exhibitor registration"},
                {"id":"gate_em_n","name":"Gate C — North (G-Block, Hall 2)","lat":19.06860,"lng":72.86130,
                 "type":"entry","capacity_pph":4000,"lanes":6,"in_share":0.25,"out_share":0.25,"notes":"Hall 2 / G-Block visitors"},
                {"id":"gate_em_e","name":"Gate D — East (cargo + visitor)","lat":19.06760,"lng":72.86200,
                 "type":"entry_exit","capacity_pph":4000,"lanes":6,"in_share":0.15,"out_share":0.25,"notes":"Cargo + secondary visitor gate"},
            ],
        },
    },
    "nesco": {
        "_default_label": "Concert / show (two rail-fed entries)",
        "expo": {
            "label": "Exhibition (Halls 1–5, multi-gate)",
            "desc": "Trade-fair setup: each hall has its own gate; flow spread across the day and across all gates.",
            "gates": [
                {"id":"gate_main","name":"Gate 1 — Halls 1–2 (NE)","lat":19.15200,"lng":72.85600,
                 "type":"entry","capacity_pph":5000,"lanes":7,"in_share":0.32,"out_share":0.26,"notes":"Halls 1–2"},
                {"id":"gate_west","name":"Gate 2 — Halls 2–3 (WEH side)","lat":19.14850,"lng":72.85290,
                 "type":"entry","capacity_pph":4000,"lanes":6,"in_share":0.28,"out_share":0.24,"notes":"WEH service rd / Ram Mandir"},
                {"id":"gate_hall","name":"Gate 3 — Halls 4–5 (South)","lat":19.14700,"lng":72.85440,
                 "type":"entry_exit","capacity_pph":4000,"lanes":6,"in_share":0.25,"out_share":0.24,"notes":"Pillarless Halls 4–5"},
                {"id":"gate_exit","name":"Gate 4 — Service / Visitor (South)","lat":19.14660,"lng":72.85510,
                 "type":"entry_exit","capacity_pph":4000,"lanes":6,"in_share":0.15,"out_share":0.26,"notes":"Visitor + service gate"},
            ],
        },
    },
}


def get_venue_gates(venue_id, layout="default"):
    """Return the gate list for a venue under a given event layout (falls back to base)."""
    if layout and layout != "default":
        lay = EVENT_LAYOUTS.get(venue_id, {}).get(layout)
        if lay and lay.get("gates"):
            return lay["gates"]
    return VENUE_GATES.get(venue_id, [])


def get_available_layouts(venue_id):
    """List the selectable gate layouts for a venue (default + event-specific)."""
    ev = EVENT_LAYOUTS.get(venue_id, {})
    out = [{"key": "default", "label": ev.get("_default_label", "Default gate setup"), "desc": ""}]
    for k, v in ev.items():
        if k.startswith("_"):
            continue
        out.append({"key": k, "label": v["label"], "desc": v.get("desc", "")})
    return out

# Pedestrian corridors — key walking routes between transit nodes and venue gates.
# Walking routes traced along the real street grid (OSM road centrelines) from the
# nearest transit nodes / drop-offs to the venue gates. All vertices lie on land.
PEDESTRIAN_CORRIDORS = {
    "wankhede": [
        # North up Maharshi Karve Rd (the "D Road" east face) from Churchgate.
        {"id":"churchgate_east","name":"Churchgate Station → East Gates (5–7)",
         "from":"Churchgate Stn","to":"East Gates",
         "points":[[18.93372,72.82746],[18.93516,72.82748],[18.93718,72.82746],
                   [18.93890,72.82742],[18.93895,72.82650]],
         "width_m":6,"capacity_pph":4500,"walk_time_min":8,
         "bottleneck":"Footpath on Maharshi Karve Rd (east of the rail); FOB crossing to the gate"},
        # West of the Western Railway from Marine Lines to the Vinoo Mankad gate.
        {"id":"marinelines_north","name":"Marine Lines Station → Vinoo Mankad Gate",
         "from":"Marine Lines Stn","to":"North Gate",
         "points":[[18.94567,72.82379],[18.94380,72.82540],[18.94180,72.82555],
                   [18.94000,72.82560]],
         "width_m":5,"capacity_pph":3500,"walk_time_min":9,
         "bottleneck":"Cross the Marine Lines FOB, then south on the road east of the rail"},
        # Short crossing east off Marine Drive (NSC Bose Rd) to the west gates.
        {"id":"marinedrive_west","name":"Marine Drive cab drop → West Gates (1 & 4)",
         "from":"Marine Drive drop","to":"West Gates",
         "points":[[18.93975,72.82410],[18.93927,72.82425],[18.93910,72.82450]],
         "width_m":7,"capacity_pph":3000,"walk_time_min":4,
         "bottleneck":"Unsignalised crossing of Marine Drive carriageway"},
    ],
    "dome": [
        # Short walk in off Lala Lajpatrai Marg (the complex's public access road).
        {"id":"lala_c","name":"Lala Lajpatrai Marg drop → Entrance C (West)",
         "from":"Lala Lajpatrai Marg","to":"Entrance C",
         "points":[[18.98533,72.81440],[18.98559,72.81456],[18.98610,72.81475],
                   [18.98640,72.81475]],
         "width_m":6,"capacity_pph":3000,"walk_time_min":3,
         "bottleneck":"Frisk + scan line at Entrance C"},
        # Walk-in down Dr Annie Besant Rd, crossing into the NSCI campus to Entrance A.
        {"id":"anniebesant_a","name":"Worli Naka / Annie Besant Rd → Entrance A (East)",
         "from":"Worli Naka / Annie Besant Rd","to":"Entrance A",
         "points":[[18.99063,72.81402],[18.98913,72.81385],[18.98766,72.81376],
                   [18.98700,72.81560],[18.98640,72.81615]],
         "width_m":5,"capacity_pph":3000,"walk_time_min":10,
         "bottleneck":"Walk through NSCI campus from Annie Besant Rd to the east-side Entrance A"},
    ],
    "dypatil": [
        # ~1 km along the service road from Nerul station to the west (Nerul-side) gates.
        {"id":"nerul_gates","name":"Nerul Station → Gates 6–9 (auto/walk)",
         "from":"Nerul Rly Station","to":"Nerul Gates",
         "points":[[19.03414,73.01968],[19.03406,73.02124],[19.03453,73.02171],
                   [19.03630,73.02349],[19.03738,73.02457],[19.03901,73.02537],
                   [19.04130,73.02590]],
         "width_m":5,"capacity_pph":4000,"walk_time_min":14,
         "bottleneck":"~1 km along DY Patil service road — most use autos/shuttles"},
        # ~1.7 km from Juinagar station to the north (Juinagar-side) gates.
        {"id":"juinagar_gates","name":"Juinagar Station → Gates 2–5 (auto/walk)",
         "from":"Juinagar Rly Station","to":"Juinagar Gates",
         "points":[[19.05518,73.01629],[19.05176,73.01680],[19.04735,73.01849],
                   [19.04692,73.02338],[19.04500,73.02520],[19.04290,73.02650]],
         "width_m":5,"capacity_pph":3500,"walk_time_min":18,
         "bottleneck":"~1.7 km; limited footpath on Adi Shankaracharya Marg"},
        # Short walk west off Palm Beach Rd Uber/Ola zone to the east gate.
        {"id":"palmbeach_east","name":"Palm Beach Rd Uber zone → East Gate",
         "from":"Palm Beach Rd drop","to":"East Gate",
         "points":[[19.04180,73.02920],[19.04185,73.02850],[19.04180,73.02780]],
         "width_m":8,"capacity_pph":5000,"walk_time_min":4,
         "bottleneck":"Shared pedestrian/vehicle lane during drop-off peak"},
    ],
    "nesco": [
        {"id":"goregaon_main","name":"Goregaon East Station/Metro → Gate 1 (Main)",
         "from":"Goregaon East Stn","to":"Gate 1 (Main)",
         "points":[[19.15245,72.85660],[19.15220,72.85625],[19.15205,72.85605],[19.15200,72.85600]],
         "width_m":5,"capacity_pph":3500,"walk_time_min":5,
         "bottleneck":"Skywalk from Goregaon East funnels into the Gate 1 frisk lanes"},
        {"id":"rammandir_west","name":"Ram Mandir Station → West Entry (WEH Service Rd)",
         "from":"Ram Mandir Stn","to":"West Entry",
         "points":[[19.15102,72.85015],[19.15045,72.85200],[19.14930,72.85275],[19.14850,72.85290]],
         "width_m":5,"capacity_pph":3000,"walk_time_min":6,
         "bottleneck":"Footbridge to WEH service road funnels into a single ramp"},
    ],
    "mmrda": [
        # All GA ingress funnels to the single main entry on the BKC-road (west) side.
        {"id":"bkcmetro_main","name":"BKC Metro / Bandra → MAIN ENTRY (BKC Road)",
         "from":"BKC Metro","to":"Main Entry",
         "points":[[19.06066,72.85468],[19.06280,72.85680],[19.06460,72.85900],[19.06560,72.86045]],
         "width_m":6,"capacity_pph":4500,"walk_time_min":11,
         "bottleneck":"~900 m along BKC Road, then a single frisk/scan line at the main gate"},
        {"id":"kurla_main","name":"Kurla / SCLR drop → MAIN ENTRY",
         "from":"Kurla / SCLR drop","to":"Main Entry",
         "points":[[19.06700,72.86700],[19.06640,72.86400],[19.06590,72.86150],[19.06560,72.86045]],
         "width_m":5,"capacity_pph":3000,"walk_time_min":12,
         "bottleneck":"Cabs from SCLR drop short; long walk around to the single main gate"},
    ],
    "mahalaxmi": [
        # Per the Lollapalooza map: everyone funnels to the south Main Entry on
        # Keshavrao Khadye Marg — a long walk down the racecourse wall from the station.
        {"id":"mahalaxmi_main","name":"Mahalaxmi Station → MAIN ENTRY (Keshavrao Khadye Marg)",
         "from":"Mahalaxmi Stn","to":"Main Entry (South)",
         "points":[[18.98252,72.82422],[18.98140,72.82260],[18.98050,72.82110],[18.97990,72.82010]],
         "width_m":6,"capacity_pph":4000,"walk_time_min":8,
         "bottleneck":"~600 m down Keshavrao Khadye Marg, then a single frisk/scan line"},
        {"id":"jacob_main","name":"Jacob Circle / E Moses Rd → MAIN ENTRY",
         "from":"Jacob Circle drop","to":"Main Entry (South)",
         "points":[[18.97860,72.82540],[18.97955,72.82120],[18.97985,72.82050],[18.97990,72.82010]],
         "width_m":5,"capacity_pph":3000,"walk_time_min":6,
         "bottleneck":"Cab drop at Jacob Circle; converges with station crowd at the gate"},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat, dlng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_time_factor(minutes_before, profile="sports"):
    table = GENRE_PROFILES.get(profile, GENRE_PROFILES["sports"])
    if minutes_before >= table[0][0]:  return table[0][1]
    if minutes_before <= table[-1][0]: return table[-1][1]
    for i in range(len(table) - 1):
        m1, f1 = table[i]; m2, f2 = table[i + 1]
        if m2 <= minutes_before <= m1:
            t = (minutes_before - m2) / (m1 - m2)
            return round(f2 + t * (f1 - f2), 4)
    return 0.25


def get_transport_split(venue_id, ticket_price):
    base  = dict(BASE_TRANSPORT_SPLIT[venue_id])
    train = base["train_metro"]; car = base["car_cab"]; foot = base["foot_auto"]
    if ticket_price < 500:
        shift = 0.10; car = max(0, car - shift); train = min(1, train + shift*0.5); foot = min(1, foot + shift*0.5)
    elif ticket_price <= 2000:
        pass
    elif ticket_price <= 5000:
        shift = 0.10; train = max(0, train - shift); car = min(1, car + shift)
    else:
        shift = 0.15; train = max(0, train - shift); car = min(1, car + shift)
    total = train + car + foot
    return {"train_metro": round(train/total, 3), "car_cab": round(car/total, 3), "foot_auto": round(foot/total, 3)}


def get_resale_modifier(original_price, effective_price):
    if not original_price or not effective_price or original_price <= 0:
        return {"ratio": 1.0, "signal": "unknown", "car_modifier": 1.0, "label": ""}
    ratio = effective_price / original_price

    # Step 1: ratio-based tier
    if ratio < 0.50:
        signal, car_modifier = "severe_collapse",   0.65
    elif ratio < 0.75:
        signal, car_modifier = "moderate_collapse", 0.80
    elif ratio < 1.0:
        signal, car_modifier = "minor_collapse",    0.92
    elif ratio < 1.5:
        signal, car_modifier = "normal",            1.00
    elif ratio < 3.0:
        signal, car_modifier = "premium",           1.20
    else:
        signal, car_modifier = "extreme_premium",   1.50

    # Step 2: cap signal by absolute effective price — a ₹1,500 crowd is not
    # ultra-affluent regardless of how much the ratio exceeded face value.
    # extreme_premium requires effective ≥ ₹10,000 (think Coldplay ₹45k tickets)
    # premium        requires effective ≥ ₹2,500  (otherwise it's just a normal market)
    if signal == "extreme_premium" and effective_price < 10000:
        signal, car_modifier = "premium", 1.20
    if signal == "premium" and effective_price < 2500:
        signal, car_modifier = "normal",  1.00

    labels = {
        "severe_collapse":   f"Severe collapse ({ratio:.2f}×) — crowd demographic shifted significantly lower than face value",
        "moderate_collapse": f"Moderate collapse ({ratio:.2f}×) — crowd shifted ~1 income bracket lower",
        "minor_collapse":    f"Minor softness ({ratio:.2f}×) — slight downward demographic shift",
        "normal":            f"Normal market ({ratio:.2f}×)",
        "premium":           f"Premium market ({ratio:.2f}×) — wealthier crowd, higher car/cab ratio expected",
        "extreme_premium":   f"Extreme premium ({ratio:.2f}×) — ultra-premium crowd, expect chauffeur/hotel cabs from airport corridors",
    }
    return {"ratio": round(ratio,2), "signal": signal, "car_modifier": car_modifier,
            "label": labels[signal]}


def eci_to_los(eci):
    if eci < 0.20: return "A"
    if eci < 0.40: return "B"
    if eci < 0.60: return "C"
    if eci < 0.75: return "D"
    if eci < 0.90: return "E"
    return "F"


def eci_to_colour(eci):
    return {"A":"#1D9E75","B":"#84CC16","C":"#EAB308","D":"#F97316","E":"#E24B4A","F":"#7F1D1D"}[eci_to_los(eci)]


def load_json(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path): return {}
    with open(path, encoding="utf-8") as fh: return json.load(fh)


def calculate_historical_baseline(venue_id, event_type, crowd_size=None):
    """
    Historical severity anchor for this venue+type.
    - When crowd_size is given, past events are weighted by crowd proximity so a
      5k club night doesn't inherit the baseline of a 60k stadium show.
    - The result is shrunk toward the 0.70 prior by n/(n+3): one lone past event
      moves the anchor only 25% of the way, ten events ~77%.
    """
    PRIOR = 0.70
    history = load_json("event_history.json")
    matches = [e for e in history.get(venue_id, []) if e.get("event_type") == event_type]
    if not matches:
        return (PRIOR, 0)
    if crowd_size:
        pairs = []
        for e in matches:
            ec = e.get("crowd", crowd_size)
            w  = max(0.15, 1.0 - abs(ec - crowd_size) / max(crowd_size, ec, 1))
            pairs.append((w, e["severity_score"]))
        mean = sum(w*s for w, s in pairs) / sum(w for w, _ in pairs)
    else:
        mean = sum(e["severity_score"] for e in matches) / len(matches)
    n = len(matches)
    conf = n / (n + 3.0)
    return (round(conf * mean + (1 - conf) * PRIOR, 3), n)


def get_active_disruptions(venue_id):
    data  = load_json("disruptions.json")
    today = datetime.now().strftime("%Y-%m-%d")
    return [d for d in data.get("disruptions", [])
            if d.get("affected_venue") == venue_id
            and d.get("start_date","0000-01-01") <= today <= d.get("end_date","9999-12-31")]


def add_time_minutes(time_str, delta_minutes):
    try:
        t = datetime.strptime(time_str, "%H:%M") + timedelta(minutes=int(delta_minutes))
        return t.strftime("%H:%M")
    except Exception:
        return time_str


def get_corridor_historical_eci(venue_id, corridor_name, event_type="sports"):
    """
    Return (mean_eci, n_events) for a specific corridor from CORRIDOR_HISTORY.
    Falls back to the venue-level event_history.json average when the corridor
    is not in the pre-computed table (e.g. unnamed OSM roads).
    """
    ch = CORRIDOR_HISTORY.get(venue_id, {}).get(corridor_name)
    if ch:
        return ch["mean"], ch["events"]
    return calculate_historical_baseline(venue_id, event_type)


def find_similar_events(venue_id, event_type, crowd_size, artist_origin, n_results=2):
    """
    Find the N most similar past events by event_type, artist_origin, and crowd_size.
    Returns a list of summary dicts for UI display.
    Scoring: event-type match 2×, artist match 1.5×, crowd proximity linear.
    """
    history = load_json("event_history.json")
    events  = history.get(venue_id, [])
    scored  = []
    for e in events:
        type_s   = 2.0 if e.get("event_type")    == event_type    else 0.6
        artist_s = 1.5 if e.get("artist_origin") == artist_origin else 0.8
        ec       = e.get("crowd", crowd_size)
        crowd_s  = max(0.1, 1.0 - abs(ec - crowd_size) / max(crowd_size, ec, 1))
        scored.append((type_s * artist_s * crowd_s, e))
    scored.sort(key=lambda x: -x[0])
    result = []
    for _, e in scored[:n_results]:
        result.append({
            "event_name":      e["event_name"],
            "date":            e["date"],
            "day_of_week":     e.get("day_of_week", ""),
            "severity_score":  e["severity_score"],
            "worst_corridors": e.get("worst_corridors", []),
            "crowd":           e.get("crowd"),
            "weather":         e.get("weather", "clear"),
            "out_of_city_pct": e.get("out_of_city_percentage", 0),
        })
    return result


def _gate_solutions(pci, gate, is_egress):
    """Return actionable solutions for a gate at a given PCI level."""
    solutions = []
    gname = gate["name"]
    if pci >= 0.90:
        solutions += [
            "🚨 EMERGENCY: Temporarily halt entry, open all emergency exits",
            "📢 PA: direct overflow to adjacent gates or holding zones",
            "👮 Police cordon — prevent crush; form orderly queues outside",
            "🔀 Close gate for 5 min, reopen in controlled batches of 200",
        ]
    elif pci >= 0.75:
        solutions += [
            "⚠ Activate timed-entry system — stagger by seating section",
            "🚧 One-way pedestrian corridor — separate entry and exit streams",
            "👮 Deploy additional marshals: 1 per 150 crowd members at this gate",
        ]
        if not is_egress:
            solutions.append("🎟 Open secondary/VIP gate to absorb overflow")
    elif pci >= 0.60:
        solutions += [
            "🔀 Serpentine queue barriers to organise flow and prevent surging",
            "📢 PA announcement: redirect 30% of arrivals to alternate gate",
            "👮 Station 1 marshal every 50 m on approach corridor",
        ]
    elif pci >= 0.40:
        solutions += [
            "✓ Standard marshaling — monitor every 10 min for density build-up",
            "📢 Pre-event PA reminder of gate assignments by ticket section",
        ]
    else:
        solutions.append("✓ Normal flow — no intervention required")
    return solutions


# Fraction of the whole crowd that pushes through the gates in the single busiest
# hour at the peak (tf=1.0). Fixed-start shows surge hardest; sports stagger most.
# Source: observed gate-throughput curves (Coldplay/Lollapalooza BKC, IPL Wankhede).
PEAK_HOUR_FRACTION = {
    "western_superstar": 0.64, "indian_mainstream": 0.58,
    "bollywood_party":   0.60, "edm":               0.60,
    "sports":            0.50,
}


# Station gate-line throughput (people/hour through ticket gates + stairs).
# Demo-grade estimates: suburban termini are big, single metro stations small.
STATION_GATELINE_CAPACITY = {
    "wankhede":  22000,   # Churchgate — terminus, many gate arrays
    "dome":       9000,   # Worli Metro L3 — single underground station
    "dypatil":   14000,   # Nerul — large suburban interchange
    "nesco":      8000,   # Ram Mandir — small suburban station
    "mmrda":     10000,   # BKC Metro L2B
    "mahalaxmi": 10000,   # Mahalaxmi suburban
}


def _gate_queue(share, cap, crowd_size, peak_frac, genre_profile, minutes_before,
                egress_tail, egress_crowd=None):
    """
    Deterministic fluid queue at one gate: step the arrival curve in 5-min
    slices, accumulate arrivals beyond capacity, drain when arrivals dip below.
    Returns (queue_people, wait_min). Unlike a flat 'util - 0.85' heuristic,
    an oversaturated gate's queue GROWS for as long as demand exceeds capacity.
    egress_crowd: stagger-adjusted surge population (crowd × EGRESS_BULK_FRACTION).
    """
    step  = 5.0
    queue = 0.0
    if minutes_before >= 0:   # ingress: from doors-open (T-300) to now
        t = 300.0
        while t > minutes_before:
            rate  = crowd_size * peak_frac * get_time_factor(t, genre_profile) * share
            queue = max(0.0, queue + (rate - cap) * (step / 60.0))
            t -= step
    else:                     # egress: from T-0 to now
        surge = (egress_crowd if egress_crowd is not None else crowd_size) \
                * min(1.0, peak_frac * 1.45) * share
        t = 0.0
        target = abs(minutes_before)
        while t < target:
            rate  = surge * max(0.0, 1.0 - (t - 10) / egress_tail)
            queue = max(0.0, queue + (rate - cap) * (step / 60.0))
            t += step
    wait = queue / cap * 60.0 if cap > 0 else 0.0
    return round(queue), round(min(wait, 240))


# Share of the crowd still inside at the final whistle/last song — the bulk that
# exits in ONE post-event pulse. All-day festivals leak attendees for hours
# (Lolla real dispersal ≈ 90 min vs the 180 a single-pulse model predicts);
# single-set shows hold nearly everyone to the end.
EGRESS_BULK_FRACTION = {
    "edm": 0.62, "bollywood_party": 0.78, "western_superstar": 0.85,
    "indian_mainstream": 0.85, "sports": 0.90,
}


def egress_tail_minutes(people, capacity_per_hour, cap_max=180.0):
    """
    How long the egress pulse lasts, scaled to crowd vs exit capacity.
    Physical floor: time to push the surge through at 100% throughput,
    padded ×1.15 + 10 min. Callers pass the STAGGER-ADJUSTED surge (crowd ×
    EGRESS_BULK_FRACTION), so most traffic lands right after the event and
    the tail is short. Backtest-calibrated: Coldplay 50k ≈ 130 min (real ~2h),
    Lolla 60k×0.62 ≈ 95 min (real ~90).
    """
    if capacity_per_hour <= 0:
        return 45.0
    physical = people / capacity_per_hour * 60.0
    return max(30.0, min(cap_max, physical * 1.15 + 10.0))


def compute_foot_traffic(venue_id, crowd_size, minutes_before,
                          transport_split, genre_profile, layout="default",
                          exit_capacity_factor=1.0):
    """
    Compute Pedestrian Congestion Index (PCI, 0-1) for every gate and walking
    corridor at the venue.  PCI uses the same 0–1 scale and LOS grades as ECI.

    Model (calibrated to real gate throughput, not just walk-up traffic):
    - EVERY attendee passes through a gate, regardless of arrival mode — car/cab
      arrivals are dropped at the venue kerb and still funnel through turnstiles.
      So gate throughput is driven by the whole crowd, not only foot+train pax.
    - The crowd is time-concentrated: at a fixed-start show 64–70% of attendees
      pass the gates in the busiest hour. `tf` shapes that surge over time.
    - Egress is sharper still: most of the crowd leaves within ~30–40 min, so
      exit gates routinely hit LOS E/F immediately after the event ends.
    - Approach CORRIDORS only carry the walking population (foot + last-mile
      train/metro walk); kerb-dropped cab/car arrivals bypass the long corridors.
    """
    gates    = get_venue_gates(venue_id, layout)
    pedcorrs = PEDESTRIAN_CORRIDORS.get(venue_id, [])
    if not gates:
        return {"gates": [], "corridors": [], "total_pedestrian_flow_pph": 0,
                "is_egress": False, "walking_population": 0}

    is_egress = minutes_before < 0
    tf        = get_time_factor(minutes_before, genre_profile)
    peak_frac = PEAK_HOUR_FRACTION.get(genre_profile, 0.62)

    # Gate throughput rate (pedestrians/hour through the perimeter at this moment).
    # Entire crowd, concentrated into the peak window.
    total_exit_cap = round((sum(g["capacity_pph"] for g in gates) or 1) * exit_capacity_factor)
    egress_bulk    = EGRESS_BULK_FRACTION.get(genre_profile, 0.85)
    egress_crowd   = crowd_size * egress_bulk          # the one-pulse surge
    egress_tail    = egress_tail_minutes(egress_crowd, total_exit_cap)
    if is_egress:
        # Post-event surge: the heaviest traffic is the first minutes after the
        # show ends; early leavers (1 − bulk) are excluded from the pulse, so
        # the tail is short and front-loaded.
        mins_after   = abs(minutes_before)
        egress_curve = max(0.0, 1.0 - (mins_after - 10) / egress_tail)
        pax_per_hour = egress_crowd * min(1.0, peak_frac * 1.45) * egress_curve
    else:
        pax_per_hour = crowd_size * peak_frac * tf

    # Walking population that actually uses the approach corridors.
    walk_fraction = transport_split["foot_auto"] + transport_split["train_metro"] * 0.75
    walking_pph   = pax_per_hour * walk_fraction

    # Demand share = fraction of the crowd that actually approaches each gate,
    # driven by the transit/parking it serves (NOT by its capacity). A small gate
    # facing a busy station is a worse bottleneck than a big gate facing a car park.
    # Pure-entry gates carry a trickle on egress; the exit ramp a trickle on entry.
    def _demand(g):
        if is_egress:
            base = g.get("out_share")
            if base is None:
                base = 0.05 if g["type"] == "entry" else g["capacity_pph"]
            return base
        else:
            base = g.get("in_share")
            if base is None:
                base = 0.04 if g["type"] == "exit_primary" else g["capacity_pph"]
            return base

    demand_total = sum(_demand(g) for g in gates) or 1

    gate_results = []
    for gate in gates:
        cap      = gate["capacity_pph"]
        share    = _demand(gate) / demand_total
        flow     = pax_per_hour * share
        raw_util = flow / cap                  # may exceed 1.0 when oversaturated
        pci      = round(min(1.0, raw_util), 4)
        los  = eci_to_los(pci)
        col  = eci_to_colour(pci)
        # Fluid-queue model: queue accumulates while arrivals exceed capacity.
        queue_ppl, wait = _gate_queue(share, cap, crowd_size, peak_frac,
                                      genre_profile, minutes_before, egress_tail,
                                      egress_crowd=egress_crowd)

        gate_results.append({
            "queue_people":  queue_ppl,
            "id":            gate["id"],
            "name":          gate["name"],
            "lat":           gate["lat"],
            "lng":           gate["lng"],
            "type":          gate["type"],
            "lanes":         gate.get("lanes", 4),
            "pci":           pci,
            "los_grade":     los,
            "colour":        col,
            "flow_pph":      round(flow),
            "capacity_pph":  cap,
            "utilisation_pct": round(raw_util * 100),   # uncapped — can read >100%
            "est_wait_min":  wait,
            "notes":         gate.get("notes", ""),
            "solutions":     _gate_solutions(pci, gate, is_egress),
        })

    # Approach-corridor PCIs — demand-driven, not capacity-proportional.
    # (Capacity-proportional allocation gives every corridor an identical PCI —
    # flow/cap cancels — hiding exactly the station corridor that overloads.)
    # Station-fed corridors carry the train/metro walkers; the rest carry local
    # walk-ups. Within each group, split by capacity.
    _is_station = lambda pc: bool(re.search(r"station|stn|metro|railway",
                                            (pc.get("from", "") + " " + pc["name"]).lower()))
    station_corrs = [pc for pc in pedcorrs if _is_station(pc)]
    local_corrs   = [pc for pc in pedcorrs if not _is_station(pc)]
    train_walk_pph = pax_per_hour * transport_split["train_metro"] * 0.75
    local_walk_pph = max(0.0, walking_pph - train_walk_pph)
    if not station_corrs:                     # no station route mapped — fall back
        station_corrs, local_corrs = [], pedcorrs
        local_walk_pph = walking_pph
    station_cap = sum(pc["capacity_pph"] for pc in station_corrs) or 1
    local_cap   = sum(pc["capacity_pph"] for pc in local_corrs) or 1

    corr_results = []
    for pc in pedcorrs:
        if pc in station_corrs:
            corr_flow = train_walk_pph * pc["capacity_pph"] / station_cap
        else:
            corr_flow = local_walk_pph * pc["capacity_pph"] / local_cap
        corr_pci   = round(min(1.0, corr_flow / pc["capacity_pph"]), 4)
        corr_results.append({
            "id":            pc["id"],
            "name":          pc["name"],
            "from":          pc["from"],
            "to":            pc["to"],
            "points":        pc["points"],
            "pci":           corr_pci,
            "los_grade":     eci_to_los(corr_pci),
            "colour":        eci_to_colour(corr_pci),
            "capacity_pph":  pc["capacity_pph"],
            "flow_pph":      round(corr_flow),
            "walk_time_min": pc["walk_time_min"],
            "bottleneck":    pc.get("bottleneck", ""),
        })

    # ── Station-side load (the Elphinstone-type risk) ─────────────────────────
    # Post-event, every train/metro attendee converges on ONE station gate-line.
    # The venue's gates can be clear while the station stairs are at crush load.
    station_block = None
    st = NEAREST_STATIONS.get(venue_id)
    st_cap = STATION_GATELINE_CAPACITY.get(venue_id)
    if st and st_cap:
        st_share = transport_split["train_metro"] * 0.75      # rail pax who walk it
        st_flow  = pax_per_hour * st_share
        st_load  = st_flow / st_cap
        st_queue, st_wait = (_gate_queue(st_share, st_cap, crowd_size, peak_frac,
                                         genre_profile, minutes_before, egress_tail,
                                         egress_crowd=egress_crowd)
                             if is_egress else (0, 0))
        g, colr, lab = _pp_status(min(1.0, st_load))
        recs = []
        if is_egress and st_load >= 0.8:
            recs = ["Staggered egress: hold-and-release crowd blocks toward the station",
                    "Open ALL ticket gates / AFC arrays; suspend ticket checking if queue builds",
                    "RPF/GRP personnel on platforms + foot-over-bridges before the surge lands",
                    "Announce extra services / next-train times inside the venue to slow the rush"]
        elif st_load >= 0.8:
            recs = ["Meter station exit toward the venue corridor; keep FOB one-directional"]
        station_block = {
            "name": st["name"], "lat": st["lat"], "lng": st["lng"],
            "capacity_pph": st_cap, "flow_pph": round(st_flow),
            "load_pct": round(st_load * 100),
            "queue_people": st_queue, "est_wait_min": st_wait,
            "grade": g, "colour": colr, "label": lab,
            "direction": "venue → station" if is_egress else "station → venue",
            "recommendations": recs,
        }

    return {
        "gates":                   gate_results,
        "corridors":               corr_results,
        "station":                 station_block,
        "total_pedestrian_flow_pph": round(pax_per_hour),
        "is_egress":               is_egress,
        "walking_population":      round(crowd_size * walk_fraction),
        "egress_tail_min":         round(egress_tail),
        "total_exit_capacity_pph": total_exit_cap,
    }


def compute_parking_plan(venue_id, crowd_size, transport_split, event_time, genre_profile):
    """
    Deterministic lot-by-lot parking allocation — the 'Parking Plan' annexure
    the Traffic NOC requires. Drivers fill the nearest lot first (by walk time),
    spilling to the next when a lot hits 90%. Fill-by clock times come from the
    cumulative arrival curve; marshal counts and exit-clearance from lot
    capacity/throughput. Demand side is model-calibrated; lot capacities are
    desk estimates until site-verified (flagged in the output).
    """
    lots = PARKING_FACILITIES.get(venue_id, [])
    if not lots:
        return None
    car_cab      = transport_split["car_cab"]
    cab_share    = CAB_SHARE.get(venue_id, 0.5)
    total_cars   = round(crowd_size * car_cab * (1.0 - cab_share) / PARKING_OCCUPANCY)
    total_cap    = sum(l["capacity"] for l in lots)

    # Nearest-first allocation to 90% of each lot (10% held for VIP/ops/error).
    remaining = total_cars
    plan_lots = []
    cum_alloc = 0
    for l in sorted(lots, key=lambda x: x["walk_min"]):
        usable = int(l["capacity"] * 0.90)
        alloc  = min(remaining, usable)
        remaining -= alloc
        cum_alloc += alloc
        # Fill-by: when cumulative arrivals reach this lot's cumulative allocation.
        fill_by = None
        if alloc > 0 and total_cars > 0:
            target = cum_alloc / total_cars
            t = 300
            while t >= 0:
                if _occupancy_fraction(t) >= min(0.97, target):
                    fill_by = add_time_minutes(event_time, -t)
                    break
                t -= 5
            if fill_by is None:
                fill_by = event_time      # fills only by showtime
        plan_lots.append({
            "name": l["name"], "type": l["type"],
            "capacity": l["capacity"], "allocated": alloc,
            "vip_ops_reserve": l["capacity"] - usable,
            "entry_route": l["access_road"], "walk_min": l["walk_min"],
            "fill_by": fill_by,
            "marshals": max(2, -(-alloc // 150)) if alloc > 0 else 0,   # ceil(alloc/150)
            "exit_clear_min": round(alloc / l["throughput_cph"] * 60) if alloc > 0 else 0,
            "price": l["price"],
        })

    shortfall = remaining
    overflow_recs = []
    if shortfall > 0:
        overflow_recs = [
            f"Demand exceeds mapped supply by ~{shortfall} vehicles — mandate pre-booked parking on the ticket page",
            "Arrange a park-and-ride ground 2–4 km out with shuttle service (10-min headway)",
            "Push rail/metro hard in pre-event comms; consider a parking surcharge to shift mode",
            "Publish tow-away zones on approach roads 48h ahead (with Traffic Police sign-off)",
        ]
    elif total_cars > total_cap * 0.75:
        overflow_recs = [
            "Supply is tight — station variable-message signage showing live lot status on approach roads",
            "Pre-assign lots by ticket tier/gate on the ticket page to prevent cross-venue circling",
        ]

    return {
        "total_cars_expected": total_cars,
        "total_capacity": total_cap,
        "utilisation_pct": round(total_cars / total_cap * 100) if total_cap else 0,
        "lots": plan_lots,
        "shortfall": shortfall,
        "overflow_recommendations": overflow_recs,
        "total_marshals": sum(p["marshals"] for p in plan_lots),
        "verification_note": ("Lot capacities are desk estimates pending site verification "
                              "(venue ops / BMC parking authority / physical survey)."),
    }


def _pp_status(load):
    """Map a 0..1 load to (grade, colour, label)."""
    if load >= 0.95: return "F", "#7F1D1D", "Gridlock"
    if load >= 0.80: return "E", "#E24B4A", "Severe"
    if load >= 0.60: return "D", "#F97316", "Heavy"
    if load >= 0.40: return "C", "#EAB308", "Moderate"
    if load >= 0.20: return "B", "#84CC16", "Light"
    return "A", "#1D9E75", "Free"


def _parking_solutions(fill, access, is_egress):
    s = []
    if fill >= 1.0:
        s.append("🅿️ Lot FULL — divert arrivals to overflow ground & display 'FULL' on approach VMS")
    if access >= 0.95:
        s += ["🚨 Entry queue spilling onto road — open all entry lanes, suspend ticketing (pay-on-exit)",
              "👮 Marshal to wave-through; stage a holding lane off the carriageway"]
    elif access >= 0.75:
        s += ["⚠ Open additional entry lane; pre-sell/pre-pay parking to cut transaction time",
              "🔀 Split inbound flow: cars to lot, cabs to drop-and-go kerb"]
    elif access >= 0.45:
        s.append("✓ Add a marshal at the lot mouth to keep the access lane moving")
    if is_egress and access >= 0.6:
        s.append("🔁 Convert entry lanes to exit-only; meter outflow to avoid road gridlock")
    if not s:
        s.append("✓ Within capacity — routine management")
    return s


def _pickup_solutions(load, is_egress):
    s = []
    if load >= 0.95:
        s += ["🚨 Cab pileup CRITICAL — hold drivers in a remote queue, release in batches by booking",
              "📢 Stagger crowd release & PA cab-zone wait time; push attendees to metro/train",
              "👮 Strict one-way cab loop; no on-street waiting; 4+ marshals on bays"]
    elif load >= 0.75:
        s += ["⚠ Expand pickup bays; geo-fence a driver staging lot 300–500 m away",
              "📲 Zone-code matching (lettered bays) to cut driver search time"]
    elif load >= 0.45:
        s.append("✓ Add 2 marshals to load bays; keep the lane one-way")
    else:
        s.append("✓ Normal cab flow — no intervention required")
    if is_egress and load >= 0.6:
        s.append("🚂 Promote rail/metro as faster exit; cap surge pricing with operator")
    return s


def compute_parking_pickup(venue_id, crowd_size, minutes_before,
                           transport_split, genre_profile):
    """
    Analyse how parking lots and Uber/Ola pickup zones load up and feed congestion
    onto the surrounding roads.

    - car_cab attendees split into private cars (need a parking space) and
      app-cabs/taxis (drop-off pre-event, pickup surge post-event).
    - Parking lots: fill% over the event + an access-queue index (arrival rate vs
      the lot-mouth throughput). A saturated mouth spills queues onto its road.
    - Pickup zones: post-event everyone leaving by cab converges in a ~40-min burst
      → the classic cab pileup. Congestion = demand rate vs zone throughput.
    """
    lots  = PARKING_FACILITIES.get(venue_id, [])
    zones = PICKUP_ZONES.get(venue_id, [])
    is_egress = minutes_before < 0
    tf        = get_time_factor(minutes_before, genre_profile)
    peak      = PEAK_HOUR_FRACTION.get(genre_profile, 0.6)
    car_cab   = transport_split["car_cab"]
    cab_share = CAB_SHARE.get(venue_id, 0.5)

    drive_people = crowd_size * car_cab * (1.0 - cab_share)
    cab_people   = crowd_size * car_cab * cab_share
    total_cars   = drive_people / PARKING_OCCUPANCY

    # Egress tails scale with what each system must drain:
    # parking = cars vs lot-mouth throughput; pickups = cab pax vs kerb capacity.
    # Cab pickups get a higher cap — the classic post-show pileup (Lolla 2025
    # saw 90+ min waits) drains far slower than car parks.
    total_lot_tput = sum(l["throughput_cph"] for l in lots) or 1
    total_zone_cap = sum(z["capacity_pph"] for z in zones) or 1
    egress_bulk = EGRESS_BULK_FRACTION.get(genre_profile, 0.85)
    tail_park   = egress_tail_minutes(total_cars * egress_bulk, total_lot_tput)
    tail_pickup = egress_tail_minutes(cab_people * egress_bulk, total_zone_cap, cap_max=240.0)

    ecurve_park = ecurve_pickup = 0.0
    if is_egress:
        ma            = abs(minutes_before)
        ecurve_park   = max(0.0, 1.0 - (ma - 5) / tail_park)
        ecurve_pickup = max(0.0, 1.0 - (ma - 5) / tail_pickup)

    # ── Parking lots ──────────────────────────────────────────────────────────
    total_cap = sum(l["capacity"] for l in lots) or 1
    parking = []
    for l in lots:
        share = l["capacity"] / total_cap
        cars  = total_cars * share
        fill  = cars / l["capacity"]
        if is_egress:
            rate = cars * egress_bulk * 1.35 * ecurve_park   # cars/hr trying to exit (front-loaded)
        else:
            rate = cars * peak * tf                # cars/hr arriving in peak window
        access = rate / max(1, l["throughput_cph"])
        # Ingress congestion ≈ worse of "lot filling up" and "queue at the mouth";
        # egress is dominated by the exit-lane queue.
        load = access if is_egress else max(fill, access)
        g, c, lab = _pp_status(min(1.0, load))
        wait = min(75, round(max(0.0, access - 0.85) * 28)) if access > 0.85 else 0
        parking.append({
            "id": l["id"], "name": l["name"], "type": l["type"],
            "lat": l["lat"], "lng": l["lng"],
            "capacity": l["capacity"], "cars_assigned": round(cars),
            "fill_pct": round(fill * 100), "access_load_pct": round(access * 100),
            "status": g, "colour": c, "label": lab, "wait_min": wait,
            "access_road": l["access_road"], "walk_min": l["walk_min"], "price": l["price"],
            "load": round(min(1.0, load), 3),
            "solutions": _parking_solutions(fill, access, is_egress),
        })

    # ── Pickup zones (Uber / Ola / taxi) ────────────────────────────────────────
    total_zcap = sum(z["capacity_pph"] for z in zones) or 1
    pickup = []
    for z in zones:
        share = z["capacity_pph"] / total_zcap
        if is_egress:
            demand = cab_people * egress_bulk * 1.05 * ecurve_pickup * share  # post-event pickup surge (front-loaded)
        else:
            demand = cab_people * peak * tf * 0.40 * share  # pre-event drop-off (faster)
        load = demand / z["capacity_pph"]
        g, c, lab = _pp_status(min(1.0, load))
        wait = min(120, round(max(0.0, load - 0.8) * 30)) if load > 0.8 else 0
        pickup.append({
            "id": z["id"], "name": z["name"], "type": z["type"],
            "lat": z["lat"], "lng": z["lng"],
            "capacity_pph": z["capacity_pph"], "demand_pph": round(demand),
            "load_pct": round(load * 100), "status": g, "colour": c, "label": lab,
            "wait_min": wait, "serves": z["serves"], "access_road": z["access_road"],
            "load": round(min(1.0, load), 3),
            "solutions": _pickup_solutions(load, is_egress),
        })

    return {
        "parking": parking, "pickup_zones": pickup, "is_egress": is_egress,
        "cars_total": round(total_cars), "cab_people": round(cab_people),
        "parking_capacity_total": total_cap,
        "egress_tail_park_min": round(tail_park),
        "egress_tail_pickup_min": round(tail_pickup),
    }


# ── Internal crowd-density safety model ──────────────────────────────────────
# Motivated by the Klangkuenstler @ NSCI Dome death (Jun 2026): gates can be
# LOS B while the floor in front of the stage is at crush density. Thresholds
# per Fruin LoS + NDMA "Managing Crowds" guide + UK Green Guide:
#   < 2.0 /m²  comfortable (typical event-assessment level)
#   2–3.5      busy, involuntary contact begins
#   3.5–4.7    high — approaching Green Guide standing max (4.7/m²)
#   ≥ 5.0      NDMA injury threshold — dangerous
#   ≥ 7.0      fatality-risk density, crowd behaves as fluid
#
# Standing/audience floor areas (m²) for the default concert configuration.
# Demo-grade estimates from satellite footprints — calibrate per event layout.
VENUE_FLOOR_AREAS = {
    "wankhede":  14000,   # cricket outfield (concerts only; matches are seated)
    "dome":       3500,   # indoor arena floor
    "dypatil":   10000,   # pitch standing area (Coldplay config)
    "nesco":     20000,   # exhibition grounds concert arena
    "mmrda":     35000,   # BKC ground event arena
    "mahalaxmi": 50000,   # racecourse festival arena (Lolla config)
}

# Cumulative share of the crowd already inside the venue, by minutes-to-start.
_OCCUPANCY_CURVE = [(300, 0.02), (180, 0.10), (120, 0.30), (60, 0.55),
                    (30, 0.75), (15, 0.88), (0, 0.97)]


def _occupancy_fraction(minutes_before, egress_tail=60.0):
    if minutes_before < 0:                       # egress: linear drain over the tail
        return 0.97 * max(0.0, 1.0 - abs(minutes_before) / max(1.0, egress_tail))
    if minutes_before >= _OCCUPANCY_CURVE[0][0]:
        return _OCCUPANCY_CURVE[0][1]
    for (m1, f1), (m2, f2) in zip(_OCCUPANCY_CURVE, _OCCUPANCY_CURVE[1:]):
        if m2 <= minutes_before <= m1:
            t = (minutes_before - m2) / (m1 - m2)
            return f2 + t * (f1 - f2)
    return 0.97


def compute_internal_density(venue_id, crowd_size, minutes_before, event_type, egress_tail=60.0,
                             floor_area_override=None):
    """
    People/m² on the venue floor, averaged and at the front-of-stage zone
    (crowds pack ~3× the average density against the barrier). Only standing
    shows are modelled; seated sport manages density via seat allocation.
    floor_area_override: measured area from an uploaded floor plan, replacing
    the desk estimate.
    """
    floor = floor_area_override or VENUE_FLOOR_AREAS.get(venue_id)
    standing = event_type == "concert"
    if not floor or not standing:
        return {"applicable": False,
                "note": "Seated/allocated event — floor density managed by seating; "
                        "monitor concourses and gates at ingress/egress instead."}

    occ    = _occupancy_fraction(minutes_before, egress_tail)
    inside = crowd_size * occ
    avg    = inside / floor
    front  = min(9.5, avg * 3.0)   # front-of-stage packing factor

    if front >= 7.0:
        level, colour = "critical", "#7F1D1D"
        label = f"CRITICAL — {front:.1f}/m² at front-of-stage: fatality-risk density (≥7/m²)"
        recs  = ["PAUSE performance until front zone thins (artist announcement)",
                 "Open all emergency egress; pull barriers at pit flanks",
                 "Stop ALL further entry to the floor immediately",
                 "Medical teams forward to pit barrier; prepare extraction lanes"]
    elif front >= 4.7:
        level, colour = "danger", "#E24B4A"
        label = (f"DANGER — {front:.1f}/m² at front-of-stage exceeds Green Guide standing max "
                 f"(4.7/m²), at/near NDMA injury threshold (5/m²)")
        recs  = ["Halt further entry to the front-of-stage zone (one-in-one-out)",
                 "Deploy density spotters on elevated platforms with show-pause authority",
                 "Push free water to the pit; announce rear-floor space on screens",
                 "Brief artist/DJ team on slow-down + pause protocol"]
    elif front >= 3.5:
        level, colour = "high", "#F97316"
        label = f"HIGH — {front:.1f}/m² at front-of-stage approaching Green Guide standing max (4.7/m²)"
        recs  = ["Add mid-pit barrier breaks to segment the front crowd",
                 "Position spotters with CCTV/drone view of the pit",
                 "Meter floor entry; direct latecomers to rear/side zones"]
    elif front >= 2.0:
        level, colour = "busy", "#EAB308"
        label = f"Busy — {front:.1f}/m² at front-of-stage; involuntary contact begins"
        recs  = ["Normal monitoring; keep floor-entry lanes staffed"]
    else:
        level, colour = "comfortable", "#1D9E75"
        label = f"Comfortable — {front:.1f}/m² at front-of-stage"
        recs  = []

    return {
        "applicable": True,
        "floor_area_m2": floor,
        "occupancy_pct": round(occ * 100),
        "people_inside": round(inside),
        "avg_density": round(avg, 2),
        "front_stage_density": round(front, 2),
        "level": level, "colour": colour, "label": label,
        "recommendations": recs,
        "thresholds": "Fruin LoS · NDMA Managing Crowds (injury ≥5/m², fatal ≥7/m²) · UK Green Guide standing 4.7/m²",
    }


# ── Extended feeder corridors ─────────────────────────────────────────────────
# The OSM fetch only covers ~1 km around the venue, but real event queues
# stretch far up the approach highways (Coldplay: 8-10 km on Sion-Panvel).
# Each feeder is a long polyline ordered FAR END → VENUE; the jam occupies the
# last `jam_km` of it with an ECI gradient (worst at the venue, fading to
# background at the jam front). Geometries are approximate shoreline/highway
# traces — verify against OSM before printing in a customer report.
EXTENDED_FEEDERS = {
    "dome": [
        {"road_name": "Coastal Road (from Marine Drive)", "road_type": "arterial",
         "points": [[18.9430, 72.8230], [18.9545, 72.8115], [18.9560, 72.8050],
                    [18.9640, 72.7930], [18.9705, 72.7960], [18.9790, 72.8060],
                    [18.9860, 72.8110], [18.9880, 72.8150]]},
        {"road_name": "Bandra–Worli Sea Link approach", "road_type": "arterial",
         "points": [[19.0450, 72.8200], [19.0250, 72.8130], [18.9990, 72.8145],
                    [18.9930, 72.8155], [18.9880, 72.8160]]},
    ],
    "mahalaxmi": [
        {"road_name": "Coastal Road (from Marine Drive)", "road_type": "arterial",
         "points": [[18.9430, 72.8230], [18.9545, 72.8115], [18.9560, 72.8050],
                    [18.9640, 72.7930], [18.9705, 72.7960], [18.9790, 72.8060],
                    [18.9825, 72.8140]]},
        {"road_name": "Sea Link → Annie Besant Rd (from Bandra)", "road_type": "arterial",
         "points": [[19.0450, 72.8200], [19.0250, 72.8130], [18.9950, 72.8140],
                    [18.9900, 72.8165], [18.9845, 72.8175]]},
    ],
    # wankhede: no extended feeders. Marine Drive only reaches ~1.5 km (already
    # inside the 1 km OSM layer) and the Eastern Freeway is not a real Wankhede
    # approach — it rendered as a disconnected line. Wankhede is rail-dominated
    # (Churchgate/Marine Lines), so long car-queue feeders are the wrong model.
    "dypatil": [
        {"road_name": "Sion-Panvel Hwy (from Vashi/Mankhurd)", "road_type": "arterial",
         "points": [[19.0630, 72.9930], [19.0620, 73.0050], [19.0665, 73.0125],
                    [19.0680, 73.0160], [19.0600, 73.0210], [19.0520, 73.0250],
                    [19.0450, 73.0265]]},
        {"road_name": "Sion-Panvel Hwy (from Kharghar/Belapur)", "road_type": "arterial",
         "points": [[19.0330, 73.0620], [19.0380, 73.0480], [19.0420, 73.0350],
                    [19.0435, 73.0295]]},
        {"road_name": "Palm Beach Road (from Vashi/Airoli)", "road_type": "arterial",
         "points": [[19.0900, 72.9920], [19.0770, 72.9980], [19.0600, 73.0050],
                    [19.0450, 73.0150], [19.0400, 73.0230], [19.0420, 73.0260]]},
    ],
    "nesco": [
        {"road_name": "Western Express Hwy (from Andheri)", "road_type": "arterial",
         "points": [[19.1150, 72.8630], [19.1250, 72.8600], [19.1350, 72.8570],
                    [19.1440, 72.8550], [19.1495, 72.8545]]},
        {"road_name": "Western Express Hwy (from Borivali)", "road_type": "arterial",
         "points": [[19.1850, 72.8500], [19.1700, 72.8510], [19.1600, 72.8520],
                    [19.1520, 72.8540]]},
    ],
    "mmrda": [
        {"road_name": "WEH → BKC connector (from Kalanagar)", "road_type": "arterial",
         "points": [[19.0550, 72.8400], [19.0600, 72.8480], [19.0640, 72.8550],
                    [19.0674, 72.8613]]},
        {"road_name": "Sion → BKC (from Eastern Exp Hwy)", "road_type": "arterial",
         "points": [[19.0400, 72.8680], [19.0500, 72.8660], [19.0600, 72.8630],
                    [19.0665, 72.8615]]},
    ],
}


def _km_between(p1, p2):
    lat1, lng1, lat2, lng2 = map(math.radians, (p1[0], p1[1], p2[0], p2[1]))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


# Real OSM geometry for the feeders — name-regex + bbox per route. The
# hand-drawn EXTENDED_FEEDERS polylines above are only the fallback when
# Overpass is unreachable (sparse hand points cut across the sea).
FEEDER_OSM_QUERIES = {
    "dome": [
        {"road_name": "Coastal Road (from Marine Drive)",
         "regex": "Coastal Road", "bbox": (18.93, 72.78, 19.00, 72.83)},
        {"road_name": "Bandra–Worli Sea Link approach",
         "regex": "Sea Link", "bbox": (18.98, 72.80, 19.06, 72.85)},
    ],
    "mahalaxmi": [
        {"road_name": "Coastal Road (from Marine Drive)",
         "regex": "Coastal Road", "bbox": (18.93, 72.78, 18.995, 72.83)},
        {"road_name": "Sea Link → Annie Besant Rd (from Bandra)",
         "regex": "Sea Link|Annie Besant", "bbox": (18.97, 72.80, 19.06, 72.85)},
    ],
    # wankhede intentionally omitted — see EXTENDED_FEEDERS note above.
    "dypatil": [
        {"road_name": "Sion-Panvel Highway",
         "regex": "Sion.{0,3}Panvel", "bbox": (19.02, 72.98, 19.09, 73.08)},
        {"road_name": "Palm Beach Road",
         "regex": "Palm Beach", "bbox": (19.00, 72.98, 19.10, 73.03)},
    ],
    "nesco": [
        {"road_name": "Western Express Highway",
         "regex": "Western Express", "bbox": (19.10, 72.83, 19.19, 72.88)},
    ],
    "mmrda": [
        {"road_name": "Western Express Hwy (Kalanagar)",
         "regex": "Western Express", "bbox": (19.04, 72.83, 19.08, 72.87)},
        {"road_name": "BKC connector / CST Road",
         "regex": "Bandra.{0,3}Kurla|CST Road", "bbox": (19.05, 72.85, 19.08, 72.885)},
    ],
}
FEEDER_CACHE_TTL_HRS = 24 * 30       # road geometry barely changes
FEEDER_FAIL_COOLDOWN_S = 1800        # after a failed fetch, don't retry for 30 min
_feeder_fail_ts = {}                 # venue -> ts of last failed fetch
_feeder_inflight = set()             # venues currently being warmed in background
_feeder_lock = threading.Lock()


def _warm_feeder_cache(venue_id):
    """Background worker: fetch feeder geometry and write the cache."""
    try:
        fetch_feeder_geometry(venue_id, _blocking=True)
    except Exception:
        pass
    finally:
        with _feeder_lock:
            _feeder_inflight.discard(venue_id)


def fetch_feeder_geometry(venue_id, _blocking=False):
    """
    Real polylines for each feeder route from Overpass, cached to disk.
    Returns [{road_name, ways: [[[lat,lng],...], ...]}, ...] or [] if not cached.
    Request path never blocks (`_blocking=False`): a cache miss kicks off a
    background warm and returns []. Only the warmer passes _blocking=True.
    A failed fetch sets a 30-min cooldown.
    """
    if venue_id not in VENUES:
        return []
    cache_path = os.path.join(BASE_DIR, f"feeders_cache_{venue_id}.json")
    if os.path.exists(cache_path):
        age_hrs = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hrs < FEEDER_CACHE_TTL_HRS:
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
    if not _requests_ok:
        return []
    if time.time() - _feeder_fail_ts.get(venue_id, 0) < FEEDER_FAIL_COOLDOWN_S:
        return []

    # NEVER block a prediction on this. Feeder lines are optional enrichment;
    # Overpass can take ~2 min across mirrors when it is struggling. Warm the
    # cache in the background and return nothing for THIS request — the lines
    # appear on the next prediction once cached.
    if not _blocking:
        with _feeder_lock:
            if venue_id in _feeder_inflight:
                return []
            _feeder_inflight.add(venue_id)
        threading.Thread(target=_warm_feeder_cache, args=(venue_id,),
                         daemon=True, name=f"feeders-{venue_id}").start()
        return []

    queries = FEEDER_OSM_QUERIES.get(venue_id, [])
    results = []
    for q in queries:
        s, w, n, e = q["bbox"]
        query = (
            '[out:json][timeout:30];'
            f'way["highway"~"^(motorway|trunk|primary|secondary)$"]'
            f'["name"~"{q["regex"]}",i]({s},{w},{n},{e});'
            'out geom;'
        )
        # The public Overpass servers 504 under load — try mirrors in order.
        elements = None
        for mirror in (OVERPASS_URL,
                       "https://overpass.kumi.systems/api/interpreter",
                       "https://overpass.private.coffee/api/interpreter"):
            try:
                r = req.post(mirror, data={"data": query}, timeout=40,
                             headers={"User-Agent": "EventTrafficPlatform/1.0"})
                if r.status_code == 200:
                    elements = r.json().get("elements", [])
                    break
            except Exception:
                continue
        if elements is None:
            continue
        ways = []
        for el in elements:
            pts = [[g["lat"], g["lon"]] for g in el.get("geometry", [])]
            # drop stubs/ramps: need a few vertices and >120 m of length
            if len(pts) >= 3:
                length = sum(_km_between(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
                if length >= 0.12:
                    ways.append(pts)
        if ways:
            results.append({"road_name": q["road_name"], "ways": ways})

    # ALL-OR-NOTHING: a half-fetched set (one route missing) renders as a lone
    # arbitrary line and looks broken. Only cache/return complete sets — a
    # failed fetch means no feeders this request, retried on the next one.
    if queries and len(results) == len(queries):
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, separators=(",", ":"))
        except Exception:
            pass
        return results
    _feeder_fail_ts[venue_id] = time.time()
    return []


def compute_extended_feeders(venue_id, crowd_size, minutes_before, transport_split,
                              near_eci, genre_profile, egress_tail=90.0):
    """
    Long-range queue prediction on the approach highways. Jam length is
    calibrated to Coldplay 2025 (50k, mostly-car, ECI~0.8 → 8-10 km on the
    Sion-Panvel) and scales with crowd, car share, near-venue ECI and phase.
    Returns per-feeder segment lists with an ECI gradient for rendering.
    """
    feeders = EXTENDED_FEEDERS.get(venue_id, [])
    if not feeders:
        return []

    # Phase intensity: how hard the feeder is being loaded right now.
    if minutes_before >= 0:
        phase = get_time_factor(minutes_before, genre_profile)
    else:
        ma    = abs(minutes_before)
        phase = max(0.0, 1.0 - (ma - 10) / max(1.0, egress_tail))

    car_scale = transport_split["car_cab"] / 0.5          # 0.5 = reference car share
    sev       = max(0.0, (near_eci - 0.30) / 0.70)        # 0 below ECI .3, 1 at ECI 1
    jam_km    = min(15.0, max(0.4, (crowd_size / 50000.0) * 10.0 * sev * car_scale * phase))

    BG = 0.25   # arterial background
    vlat, vlng = VENUES[venue_id]["lat"], VENUES[venue_id]["lng"]

    def _grade(d_km):
        if d_km <= jam_km:
            eci = BG + (near_eci - BG) * max(0.15, 1.0 - d_km / max(jam_km, 0.1))
        else:
            eci = BG
        return round(min(1.0, eci), 3)

    # Real OSM geometry ONLY — hand-drawn traces cut across the sea and read as
    # broken. If Overpass has no complete set for this venue, draw nothing;
    # the next request retries the fetch.
    geo = fetch_feeder_geometry(venue_id)
    if not geo:
        return []
    results = []
    for f in geo:
        segments, max_d = [], 0.0
        for way in f["ways"]:
            mid = way[len(way) // 2]
            d   = _km_between([vlat, vlng], mid)
            max_d = max(max_d, d)
            eci = _grade(d)
            segments.append({"points": way, "eci": eci,
                             "los_grade": eci_to_los(eci), "colour": eci_to_colour(eci),
                             "km_from_venue": round(d, 1)})
        results.append({"road_name": f["road_name"], "road_type": "arterial",
                        "total_km": round(max_d, 1),
                        "jam_km": round(min(jam_km, max_d), 1),
                        "segments": segments, "geometry": "osm"})
    return results


# ── Floor-plan intelligence ───────────────────────────────────────────────────
# An uploaded venue floor plan (photo/PDF) is analysed once by Claude vision and
# stored as floorplan_<venue>.json. Predictions then use the REAL layout:
# extracted floor area + exit count refine the density/egress models, and each
# marked zone (washrooms, bars, VIP crossings, stage front…) gets a local
# crowding estimate — the places that get cramped even when gates read fine.

def _floorplan_path(venue_id):
    if venue_id not in VENUES:
        raise ValueError(f"unknown venue: {venue_id!r}")
    return os.path.join(BASE_DIR, f"floorplan_{venue_id}.json")


def load_floorplan(venue_id):
    try:
        p = _floorplan_path(venue_id)
    except ValueError:
        return None
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _density_grade(d):
    """Fruin/NDMA grading for a local density value (people/m²)."""
    if d >= 7.0: return "critical",    "#7F1D1D"
    if d >= 4.7: return "danger",      "#E24B4A"
    if d >= 3.5: return "high",        "#F97316"
    if d >= 2.0: return "busy",        "#EAB308"
    return "comfortable", "#1D9E75"


# Local packing multiplier vs venue-average density, per zone type and phase.
# Phases: ingress (T-30), show (peak occupancy), egress (first 20 min after).
# Calibration heuristics: bars/washrooms draw standing queues in a small
# footprint; stage front packs ~3× average; exits stack hard on egress.
ZONE_PHASE_MULT = {
    "stage_front": {"ingress": 2.0, "show": 3.0, "egress": 1.2},
    "entrance":    {"ingress": 3.2, "show": 0.5, "egress": 1.2},
    "exit":        {"ingress": 0.6, "show": 0.4, "egress": 3.5},
    "washroom":    {"ingress": 1.3, "show": 2.2, "egress": 1.6},
    "bar":         {"ingress": 1.5, "show": 2.5, "egress": 0.8},
    "food":        {"ingress": 1.4, "show": 2.0, "egress": 0.7},
    "vip_crossing":{"ingress": 1.6, "show": 1.8, "egress": 2.2},
    "lounge":      {"ingress": 0.6, "show": 0.8, "egress": 0.7},
    "vip":         {"ingress": 0.7, "show": 0.9, "egress": 1.0},
    "backstage":   {"ingress": 0.2, "show": 0.3, "egress": 0.3},
    "medical":     {"ingress": 0.3, "show": 0.5, "egress": 0.8},
}
_ZONE_MITIGATIONS = {
    "washroom":    "Add portable units on the far side; one-way queue lane; steward at peak",
    "bar":         "Split into more, smaller counters; pre-mixed serves; queue rails angled AWAY from walkways",
    "food":        "Move stalls off the main circulation route; spread along the perimeter",
    "stage_front": "Mid-pit barrier breaks; density spotters with show-pause authority",
    "exit":        "Open ALL exits 10 min before the finale; staff wave-through, no re-checking",
    "entrance":    "More frisking lanes pre-peak; separate bag/no-bag lanes",
    "vip_crossing":"Time-separate VIP movements from GA flow, or bridge/barrier the crossing",
}


def compute_floorplan_hotspots(crowd_size, event_type, floorplan, floor_area):
    """
    Per-zone local density across the 3 phases; returns each zone at its WORST
    phase. Local density = venue-average density (crowd × occupancy ÷ floor
    area) × zone packing multiplier, capped at 9.5 p/m².
    """
    if not floorplan or event_type != "concert" or not floor_area:
        return []
    analysis = floorplan.get("analysis", {})
    PHASE_OCC = {"ingress": 0.75, "show": 0.97, "egress": 0.97}

    # Build the zone list: stage front always modelled; entrances/exits from
    # the extraction; other zones from the zones array.
    zone_items = [("stage_front", "Front-of-stage pit", None)]
    for e in analysis.get("entrances", []) or []:
        zone_items.append(("entrance", f"Entrance: {e.get('label','?')}"
                           + (f" ({e['serves']})" if e.get("serves") else ""), e))
    for e in analysis.get("exits", []) or []:
        zone_items.append(("exit", f"Exit: {e.get('label','?')}", e))
    for z in analysis.get("zones", []) or []:
        zt = (z.get("type") or "").lower()
        if zt in ZONE_PHASE_MULT and zt not in ("entrance", "exit"):
            zone_items.append((zt, f"{zt.replace('_',' ').title()}: {z.get('label','')}"
                               .rstrip(': '), z))
    for c in analysis.get("vip_ga_crossings", []) or []:
        zone_items.append(("vip_crossing", f"VIP×GA crossing: {c}", None))

    hotspots = []
    for ztype, label, meta in zone_items:
        mults = ZONE_PHASE_MULT.get(ztype)
        if not mults:
            continue
        worst_phase, worst_d = None, -1.0
        for phase, occ in PHASE_OCC.items():
            avg = crowd_size * occ / floor_area
            d = min(9.5, avg * mults[phase])
            if d > worst_d:
                worst_phase, worst_d = phase, d
        level, colour = _density_grade(worst_d)
        hotspots.append({
            "zone_type": ztype, "label": label[:80],
            "worst_phase": worst_phase,
            "density": round(worst_d, 2),
            "level": level, "colour": colour,
            "location_hint": (meta or {}).get("location_hint", "") if isinstance(meta, dict) else "",
            "mitigation": _ZONE_MITIGATIONS.get(ztype, ""),
        })
    hotspots.sort(key=lambda h: -h["density"])
    return hotspots


def compute_staff_ops(venue_id, crowd_size, minutes_before, foot, parking_pickup, event_time):
    """
    Translate the crowd/gate prediction into an operational staffing plan for the
    people RUNNING the event — ticket office, helpdesk/wayfinding, cleaning,
    medical, water/welfare. Returns role cards (headcount + timeline + tasks) and
    positioned map stations. Everything scales with crowd size, gate congestion,
    and whether it is ingress or egress.
    """
    gates     = foot.get("gates", [])
    is_egress = bool(foot.get("is_egress"))
    vlat, vlng = VENUES[venue_id]["lat"], VENUES[venue_id]["lng"]

    entries = [g for g in gates if g["type"] in ("entry", "entry_exit")]
    exits   = [g for g in gates if g["type"] in ("exit_primary", "entry_exit")]
    busiest = max(gates, key=lambda g: g.get("utilisation_pct", 0)) if gates else None

    def t(delta):  # event-relative time label
        return add_time_minutes(event_time, delta)

    # ── Ticket scanning / box office ────────────────────────────────────────────
    # ~700 clean scans per lane-hour; open enough lanes to clear the peak inflow.
    lanes = {}
    total_scanners = 0
    for g in entries:
        n = max(2, math.ceil(g.get("flow_pph", 0) / 700))
        lanes[g["id"]] = n
        total_scanners += n
    box_office = max(2, round(crowd_size / 9000))
    worst_wait = busiest.get("est_wait_min", 0) if busiest else 0

    # ── Helpdesk / wayfinding / assistance ──────────────────────────────────────
    marshals    = max(8, round(crowd_size / 220))
    info_booths = len(entries) + 1
    helpdesk    = info_booths * 2 + max(2, round(crowd_size / 12000))

    # ── Cleaning & sanitation ───────────────────────────────────────────────────
    clean_event = max(6, round(crowd_size / 1300))
    clean_sweep = clean_event * 2
    restroom_cadence = 20 if crowd_size >= 25000 else 30

    # ── Medical / first-aid ─────────────────────────────────────────────────────
    med_posts = max(2, round(crowd_size / 10000)) + (1 if busiest and busiest.get("utilisation_pct", 0) >= 90 else 0)
    med_resp  = med_posts * 3
    ambulances = max(1, round(crowd_size / 25000))

    # ── Water / welfare ─────────────────────────────────────────────────────────
    water_points = max(2, round(crowd_size / 8000))

    roles = [
        {"role": "Ticket scanning / box office", "icon": "🎟", "for": "Ticket office",
         "headcount": total_scanners + box_office,
         "detail": f"{total_scanners} scan-lane staff + {box_office} box-office/will-call",
         "deploy_by": t(-120), "peak": t(-30), "stand_down": t(30),
         "tasks": [
             f"Open scan lanes per gate: " + ", ".join(f"{g['name'].split('—')[0].strip()}×{lanes[g['id']]}" for g in entries[:4]),
             f"Busiest gate ({busiest['name'].split('—')[0].strip() if busiest else '—'}) — expect ~{worst_wait} min queue at peak; pre-stage a serpentine line",
             "Pre-event: prioritise will-call/box-office before gates open; close on-site sales at T-30",
             "Re-entry not permitted — brief staff to direct re-entry queries to helpdesk",
         ]},
        {"role": "Helpdesk / wayfinding & assistance", "icon": "🛈", "for": "Helpdesk / assistance",
         "headcount": marshals + helpdesk,
         "detail": f"{marshals} wayfinding marshals + {helpdesk} helpdesk/accessibility staff across {info_booths} booths",
         "deploy_by": t(-120), "peak": t(15 if is_egress else -30), "stand_down": t(90),
         "tasks": [
             f"Place info booths at every entry + 1 central; concentrate marshals at {busiest['name'].split('—')[0].strip() if busiest else 'the main gate'} (most wrong-gate arrivals)",
             "Hand out gate/section maps; staff lost-&-found and accessibility/wheelchair assist",
             "Egress: flip marshals to exit-side wayfinding — point crowds to station, cab zone, and parking",
         ]},
        {"role": "Cleaning & sanitation", "icon": "🧹", "for": "Cleaners",
         "headcount": clean_event,
         "detail": f"{clean_event} during-event crew → {clean_sweep} for the post-event sweep",
         "deploy_by": t(-60), "peak": t(20), "stand_down": t(150),
         "tasks": [
             f"Service restrooms every ~{restroom_cadence} min; keep bins by food/bar zones from below half-full",
             "Spot-clean spills on high-density approach corridors during the event",
             f"POST-EVENT SWEEP from {t(0)}: {clean_sweep} crew focus on exit gates ("
             + ", ".join(g['name'].split('—')[0].strip() for g in exits[:3]) + ") + food courts",
         ]},
        {"role": "Medical / first-aid", "icon": "➕", "for": "Medical",
         "headcount": med_resp,
         "detail": f"{med_posts} first-aid posts · {med_resp} responders · {ambulances} ambulance(s) on standby",
         "deploy_by": t(-90), "peak": t(-30), "stand_down": t(120),
         "tasks": [
             f"Site one post at the busiest gate ({busiest['name'].split('—')[0].strip() if busiest else '—'}) + posts central",
             "Watch for heat/crush at peak density; keep a clear ambulance lane to the primary exit",
         ]},
        {"role": "Water & welfare", "icon": "💧", "for": "Welfare",
         "headcount": water_points * 2,
         "detail": f"{water_points} water/welfare points (2 staff each)",
         "deploy_by": t(-90), "peak": t(-30), "stand_down": t(90),
         "tasks": ["Position free-water points near the longest queues and the standing area",
                   "Welfare/cool-down zone near the busiest gate"]},
    ]

    # ── Map stations ────────────────────────────────────────────────────────────
    stations = []
    for g in entries:
        stations.append({"type": "ticket", "icon": "🎟",
                         "label": f"{lanes[g['id']]} scan lanes",
                         "detail": f"{g['name']} — staff {lanes[g['id']]} lanes; peak {t(-30)}",
                         "lat": g["lat"], "lng": g["lng"]})
    if busiest:
        stations.append({"type": "info", "icon": "🛈", "label": "Info / Helpdesk (main)",
                         "detail": f"Busiest gate — marshals + wayfinding + lost & found", "lat": busiest["lat"], "lng": busiest["lng"]})
        stations.append({"type": "firstaid", "icon": "➕", "label": "First-aid post",
                         "detail": f"{med_resp} responders · ambulance lane to exit", "lat": busiest["lat"] + 0.0004, "lng": busiest["lng"]})
    stations.append({"type": "info", "icon": "🛈", "label": "Info / Lost & Found (central)",
                     "detail": "Central helpdesk, accessibility & lost-and-found", "lat": vlat, "lng": vlng})
    stations.append({"type": "firstaid", "icon": "➕", "label": "Medical centre",
                     "detail": f"{med_posts} posts · {ambulances} ambulance(s)", "lat": vlat + 0.0005, "lng": vlng})
    for i, g in enumerate(exits[:3]):
        stations.append({"type": "cleaning", "icon": "🧹", "label": "Cleaning crew (sweep)",
                         "detail": f"Post-event sweep base — exit {g['name'].split('—')[0].strip()}", "lat": g["lat"], "lng": g["lng"]})
    for i, g in enumerate(gates[:max(2, water_points)]):
        # Nudge welfare points a little toward the venue interior (off perimeter rails/roads).
        wlat = round(g["lat"] + (vlat - g["lat"]) * 0.18, 5)
        wlng = round(g["lng"] + (vlng - g["lng"]) * 0.18, 5)
        stations.append({"type": "water", "icon": "💧", "label": "Water / welfare point",
                         "detail": "Free water + cool-down", "lat": wlat, "lng": wlng})

    phase = "egress (post-event)" if is_egress else "ingress (pre-event)"
    total_staff = sum(r["headcount"] for r in roles)
    summary = (f"~{total_staff} ground staff for {crowd_size:,} attendees · phase: {phase} · "
               f"{total_scanners} scan-lanes · {marshals} marshals · {clean_event}→{clean_sweep} cleaning · {med_resp} medics")

    return {"roles": roles, "stations": stations, "is_egress": is_egress,
            "total_staff": total_staff, "summary": summary}


def compute_corridor_eci(corridor, crowd_size, minutes_before, transport_split,
                          car_multiplier, disruptions, capacity_bonus=1.0, genre_profile="sports"):
    road_type  = corridor["road_type"]
    ffs        = FREE_FLOW_SPEEDS[road_type]
    capacity   = ROAD_CAPACITY[road_type] * capacity_bonus
    road_share = ROAD_SHARE[road_type]
    tf         = get_time_factor(minutes_before, genre_profile)

    car_fraction  = transport_split["car_cab"]
    num_cars      = (crowd_size * car_fraction) / 2.5
    effective_cars = num_cars * car_multiplier if road_type == "arterial" else num_cars
    vehicles_per_hour = (effective_cars / 1.5) * road_share * 1.8 * tf

    v_c_ratio          = min(1.0, vehicles_per_hour / capacity)
    current_speed      = ffs * max(0.05, 1.0 - 0.85 * v_c_ratio)
    speed_ratio_inv    = 1.0 - (current_speed / ffs)
    delay_ratio        = min(1.0, (ffs / current_speed) - 1.0)

    eci = round(min(1.0, 0.4*speed_ratio_inv + 0.4*v_c_ratio + 0.2*delay_ratio), 4)

    disruption_info = None
    for d in disruptions:
        if d.get("affected_corridor") == corridor["road_name"]:
            sev  = d["severity"]
            mult = 1.2 if sev < 0.3 else (1.5 if sev < 0.6 else 1.9)
            eci  = round(min(1.0, eci * mult), 4)
            disruption_info = {"id": d["id"], "description": d["description"], "severity": sev}
            break

    return eci, round(vehicles_per_hour), round(current_speed, 1), disruption_info


def apply_transit_augmentation(corridors_data, augmentation, venue_id, minutes_before, crowd_size):
    special_train  = augmentation.get("special_train",  False)
    extended_metro = augmentation.get("extended_metro", False)
    shuttle_bus    = augmentation.get("shuttle_bus",    False)
    uber_zone      = augmentation.get("uber_zone",      False)

    uber_corridor = UBER_ZONE_CORRIDOR.get(venue_id, "")
    active_names  = []
    result        = []

    # Relief scales with the share of the crowd the mode can actually carry —
    # a flat discount over-credits transit at mega-events (backtest: Coldplay
    # 50k with 2 special rakes ≈ 7% of crowd, not a 35% road-traffic cut).
    _cs = max(crowd_size, 1)
    special_relief = min(0.35, 2.0 * 3500.0  / _cs)   # ~2 rakes
    metro_relief   = min(0.30, 2.0 * 6000.0  / _cs)   # extended headways all night
    shuttle_relief = min(0.15, 2.0 * 1500.0  / _cs)   # bus fleet
    for c in corridors_data:
        c   = dict(c)
        eci = c["eci"]
        if special_train:                        eci *= (1.0 - special_relief)
        if extended_metro and minutes_before < 0: eci *= (1.0 - metro_relief)
        if shuttle_bus:                           eci *= (1.0 - shuttle_relief)
        if uber_zone:
            eci = min(1.0, eci * 1.4) if c["road_name"] == uber_corridor else eci * 0.80
        eci = round(min(1.0, eci), 4)
        c["eci"]              = eci
        c["los_grade"]        = eci_to_los(eci)
        c["colour"]           = eci_to_colour(eci)
        c["clearance_minutes"] = round(15 * (1 + eci * 3) * (crowd_size / 10000 * 0.15))
        result.append(c)

    if special_train:                          active_names.append("Special train")
    if extended_metro:                         active_names.append("Metro extended")
    if shuttle_bus:                            active_names.append("Shuttle bus")
    if uber_zone:                              active_names.append("Uber/Ola zone")
    if augmentation.get("heavy_vehicle_ban"):  active_names.append("HV ban")

    reduction = 0
    if special_train:                         reduction += round(special_relief * 100)
    if extended_metro and minutes_before < 0: reduction += round(metro_relief * 100)
    if shuttle_bus:                           reduction += round(shuttle_relief * 100)
    if uber_zone:                             reduction += 8
    if augmentation.get("heavy_vehicle_ban"): reduction += 5
    reduction = min(reduction, 65)

    summary = ""
    if active_names:
        summary = f"Transit augmentation active: {' + '.join(active_names)} — road traffic reduced by est. {reduction}%"

    return result, active_names, reduction, summary


def build_deployment_indicators(venue_id, corridors_data, crowd_size, event_time):
    indicators = []
    hc         = HARDCODED_DEPLOYMENTS.get(venue_id, {})
    min_crowd  = hc.get("min_crowd", 999_999)

    if crowd_size >= min_crowd:
        for pt in hc.get("points", []):
            local_eci = 0.5
            for c in corridors_data:
                for p in c["points"]:
                    if haversine_km(p[0], p[1], pt["lat"], pt["lng"]) < 2.0:
                        local_eci = max(local_eci, c["eci"]); break

            personnel_count = math.ceil(local_eci * 4)
            personnel_str   = f"{personnel_count} constables + 1 inspector" if local_eci > 0.75 else f"{personnel_count} constables"
            priority        = "HIGH" if local_eci >= 0.75 else ("MEDIUM" if local_eci >= 0.6 else "LOW")
            deploy_by       = add_time_minutes(event_time, -90)
            peak_start      = add_time_minutes(event_time, -30)
            peak_end        = add_time_minutes(event_time,  45)
            clearance       = add_time_minutes(event_time, int(15*(1+local_eci*3)*(crowd_size/10000*0.15)))

            ind = {"type": pt["type"], "lat": pt["lat"], "lng": pt["lng"],
                   "location_name": pt["location_name"], "priority": priority,
                   "deploy_by": deploy_by, "personnel": personnel_str,
                   "peak_window": f"{peak_start}–{peak_end}", "clearance": clearance,
                   "recommendation": pt.get("recommendation", "")}
            if pt["type"] == "barricade":
                ind.update({"barricade_type": pt.get("barricade_type","Lane restriction"),
                             "restrict_lanes": pt.get("restrict_lanes",""),
                             "alternative_route": pt.get("alternative_route","")})
            indicators.append(ind)

    diversions = DIVERSION_SUGGESTIONS.get(venue_id, {})
    for c in corridors_data:
        if c["eci"] >= 0.75 and c["road_name"] in diversions:
            d = diversions[c["road_name"]]
            indicators.append({"type": "diversion", "lat": c["points"][0][0], "lng": c["points"][0][1],
                                "location_name": f"Diversion — {c['road_name']}",
                                "diverted_from": c["road_name"], "diversion_via": d["via"],
                                "time_saving": f"{d['time_saving_min']} min vs congested route",
                                "activation": add_time_minutes(event_time, -60),
                                "priority": "HIGH" if c["eci"] >= 0.9 else "MEDIUM"})

    venue_parking = VENUES[venue_id]["parking_capacity"]
    max_vph       = max((c.get("vehicles_per_hour", 0) for c in corridors_data), default=0)
    approx_cars   = max_vph * 1.5 / 0.5
    if approx_cars > venue_parking * 0.70:
        open_by = add_time_minutes(event_time, -120)
        for pof in PARKING_OVERFLOW.get(venue_id, []):
            indicators.append({"type": "parking", "lat": pof["lat"], "lng": pof["lng"],
                                "location_name": pof["name"], "capacity": pof["capacity"],
                                "open_by": open_by, "walk_distance": pof["walk_distance"], "priority": "MEDIUM"})
    return indicators


def _waze_severity(alert_type):
    if "CLOSED" in alert_type:       return 0.9
    if "CONSTRUCTION" in alert_type: return 0.4
    if "JAM" in alert_type:          return 0.5
    return 0.3


# ── Waze live-alert cache (60 s TTL — keeps predict() fast on rapid calls) ────
_waze_cache = {}   # venue_id -> (alerts_list, fetched_at_timestamp)
WAZE_CACHE_TTL = 60


def _fetch_waze_alerts(venue_id):
    """
    Fetch live Waze alerts near venue with a 60-second in-memory cache.
    Returns a list of alert dicts; never raises — returns [] on any failure.
    """
    now = time.time()
    if venue_id in _waze_cache:
        cached_alerts, ts = _waze_cache[venue_id]
        if now - ts < WAZE_CACHE_TTL:
            return cached_alerts

    venue = VENUES.get(venue_id, {})
    lat, lng = venue.get("lat", 0), venue.get("lng", 0)
    radius   = 0.05

    if not _requests_ok:
        return []

    try:
        waze_url = (
            f"https://www.waze.com/live-map/api/georss"
            f"?top={lat+radius:.4f}&bottom={lat-radius:.4f}"
            f"&left={lng-radius:.4f}&right={lng+radius:.4f}"
            f"&types=alerts,traffic"
        )
        r = req.get(waze_url, timeout=4,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; EventTrafficBot/1.0)"})
        if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
            _waze_cache[venue_id] = ([], now)
            return []

        raw    = r.json()
        TYPES  = {"ROAD_CLOSED", "ROAD_CLOSED_HAZARD", "HAZARD_ON_ROAD_CONSTRUCTION", "JAM"}
        alerts = []
        for alert in raw.get("alerts", []):
            atype = alert.get("type", "")
            if atype in TYPES or any(t in atype for t in TYPES):
                loc = alert.get("location", {})
                alerts.append({
                    "type":        atype,
                    "street":      (alert.get("street") or "").strip(),
                    "description": alert.get("reportDescription") or atype.replace("_", " ").title(),
                    "lat":         loc.get("y", lat),
                    "lng":         loc.get("x", lng),
                })

        _waze_cache[venue_id] = (alerts, now)
        return alerts

    except Exception:
        _waze_cache[venue_id] = ([], now)
        return []


def _apply_live_alerts(roads, alerts):
    """
    Mutate road dicts in-place based on live Waze alerts.
    Returns (closure_list, jams_count).
    closure_list entries: {road_name, description, lat, lng}
    """
    closures = []
    jams     = 0

    for alert in alerts:
        street = alert["street"].lower()
        if not street:
            continue

        s_words = [w for w in street.split() if len(w) >= 4]

        for road in roads:
            rname = (road.get("road_name") or "").lower()
            if not rname or rname == "unnamed road":
                continue

            r_words = [w for w in rname.split() if len(w) >= 4]
            match   = (any(w in rname for w in s_words) or
                       any(w in street for w in r_words))
            if not match:
                continue

            atype = alert["type"]

            if "CLOSED" in atype:
                road["eci"]          = 1.0
                road["los_grade"]    = "F"
                road["colour"]       = "#7F1D1D"
                road["live_closure"] = True
                road["live_alert"]   = f"🚨 Road closed — {alert['description']}"
                key = road["road_name"]
                if not any(lc["road_name"] == key for lc in closures):
                    closures.append({
                        "road_name":   road["road_name"],
                        "description": alert["description"],
                        "lat":         alert["lat"],
                        "lng":         alert["lng"],
                    })

            elif "JAM" in atype or "CONSTRUCTION" in atype:
                road["eci"]       = round(min(1.0, road["eci"] * 1.35), 4)
                road["los_grade"] = eci_to_los(road["eci"])
                road["colour"]    = eci_to_colour(road["eci"])
                road["live_jam"]  = True
                road["live_alert"] = f"⚠ Live jam — {alert['description']}"
                jams += 1

    return closures, jams


# ─────────────────────────────────────────────────────────────────────────────
# OSM road fetching & ECI mapping
# ─────────────────────────────────────────────────────────────────────────────

OVERPASS_URL       = "https://overpass-api.de/api/interpreter"
ROAD_CACHE_TTL_HRS = 72   # re-fetch every 3 days (roads rarely change)

# Map OSM highway tag → our road_type
OSM_ROAD_TYPE = {
    "motorway": "arterial",  "motorway_link": "arterial",
    "trunk":    "arterial",  "trunk_link":    "arterial",
    "primary":  "arterial",  "primary_link":  "arterial",
    "secondary":    "sub_arterial", "secondary_link":  "sub_arterial",
    "tertiary":     "sub_arterial", "tertiary_link":   "sub_arterial",
    "unclassified": "local",
    "residential":  "local",
    "service":      "local",
    "living_street":"local",
    "road":         "local",
}

# OSM classes never drawn on the congestion map: parking aisles/driveways
# (service), residential lanes, and roads that aren't open. They remain in the
# response data — only the map layer skips them. Named corridors and disrupted
# roads override this (see `event_affected`).
_UNDRAWN_HIGHWAYS = {"service", "residential", "living_street", "construction",
                     "track", "path", "footway", "pedestrian"}

# Pixel weight per OSM type (slightly finer than our 3-tier system)
OSM_WEIGHT = {
    "motorway": 8, "trunk": 7, "primary": 6,
    "secondary": 5, "tertiary": 4,
    "unclassified": 3, "residential": 2,
    "service": 1, "living_street": 1,
}

# Background ECI per road type (Mumbai always congested even without an event)
BACKGROUND_ECI = {"arterial": 0.28, "sub_arterial": 0.18, "local": 0.10}


def _road_cache_path(venue_id):
    if venue_id not in VENUES:          # never let a request-supplied id form a path
        raise ValueError(f"unknown venue: {venue_id!r}")
    return os.path.join(BASE_DIR, f"roads_cache_{venue_id}.json")


def fetch_osm_roads(venue_id, force_refresh=False):
    """
    Fetch all driveable road geometries within ~1 km of the venue from
    OpenStreetMap via the Overpass API.  Results are cached to disk.
    Returns {"roads": [...], "fetched_at": timestamp} or {"error": ..., "roads": []}.
    """
    cache_path = _road_cache_path(venue_id)

    # ── Serve from cache if fresh ─────────────────────────────────────────────
    if not force_refresh and os.path.exists(cache_path):
        age_hrs = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hrs < ROAD_CACHE_TTL_HRS:
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass   # corrupted cache – re-fetch

    if not _requests_ok:
        return {"error": "requests not installed", "roads": []}

    venue    = VENUES[venue_id]
    lat, lng = venue["lat"], venue["lng"]

    # ── Overpass query: all driveable ways in a ~1 km radius ─────────────────
    query = f"""
[out:json][timeout:40];
(
  way["highway"]["highway"!~"footway|cycleway|path|steps|pedestrian|track|bridleway"]
     (around:1100,{lat},{lng});
);
out geom;
"""

    try:
        r = req.post(OVERPASS_URL, data={"data": query}, timeout=45,
                     headers={"User-Agent": "EventTrafficPlatform/1.0 (mumbai traffic research)"})
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return {"error": str(e), "roads": []}

    # ── Parse elements ────────────────────────────────────────────────────────
    roads = []
    seen_names = {}   # deduplicate long named roads that repeat many small segments

    for elem in raw.get("elements", []):
        if elem.get("type") != "way":
            continue
        tags     = elem.get("tags", {})
        highway  = tags.get("highway", "road")
        name     = (tags.get("name") or tags.get("name:en") or
                    tags.get("ref")  or "")
        geometry = elem.get("geometry", [])

        if len(geometry) < 2:
            continue

        road_type = OSM_ROAD_TYPE.get(highway, "local")

        # Clip geometry to the query radius (Overpass returns full ways)
        points = []
        for g in geometry:
            if haversine_km(g["lat"], g["lon"], lat, lng) <= 1.15:
                points.append([round(g["lat"], 6), round(g["lon"], 6)])

        if len(points) < 2:
            continue

        roads.append({
            "osm_id":    elem["id"],
            "name":      name,
            "highway":   highway,
            "road_type": road_type,
            "oneway":    tags.get("oneway") == "yes",
            "lanes":     int(tags.get("lanes", 1)),
            "points":    points,
        })

    result = {"roads": roads, "fetched_at": time.time(),
              "venue_id": venue_id, "count": len(roads)}

    # ── Cache to disk ─────────────────────────────────────────────────────────
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, separators=(",", ":"))
    except Exception:
        pass

    return result


def _named_corridor_eci_map(venue_id, crowd_size, minutes_before,
                             transport_split, car_multiplier, disruptions,
                             hist_baseline, capacity_bonus, genre_profile,
                             event_type="sports", event_hour=19, day_of_week="Friday",
                             dow_factor=None, crowd_hist_ratio=1.0):
    """
    Pre-compute ECI for each named corridor so OSM roads can inherit by name.
    Uses a 3-way blend: 60% formula (DOW-adjusted) + 25% corridor history + 15% background.
    dow_factor/crowd_hist_ratio: pass the event-calibrated values from predict()
    so this path stays consistent with the named-corridor path.
    """
    bg_curve   = HOURLY_BG_ECI.get(venue_id, HOURLY_BG_ECI["wankhede"])
    bg_eci     = bg_curve[event_hour % 24]
    if dow_factor is None:   # legacy fallback
        dow_factor = DOW_TRAFFIC.get(day_of_week, 1.18) / 1.18

    mapping = {}
    for corridor in ROAD_CORRIDORS.get(venue_id, []):
        eci, vph, speed, dis_info = compute_corridor_eci(
            corridor, crowd_size, minutes_before,
            transport_split, car_multiplier, disruptions,
            capacity_bonus=capacity_bonus, genre_profile=genre_profile,
        )
        ch_mean, _ = get_corridor_historical_eci(venue_id, corridor["road_name"], event_type)
        blended = round(min(1.0,
            0.60 * min(1.0, eci * dow_factor) +
            0.25 * ch_mean * crowd_hist_ratio +
            0.15 * bg_eci
        ), 4)
        mapping[corridor["road_name"].lower()] = {
            "eci": blended, "vph": vph, "speed": speed,
            "clearance_minutes": round(15 * (1 + blended * 3) * (crowd_size / 10000 * 0.15)),
        }
    return mapping


def compute_all_roads_eci(osm_data, venue_id, crowd_size, minutes_before,
                           transport_split, car_multiplier, disruptions,
                           hist_baseline, capacity_bonus, genre_profile,
                           augmentation, event_hour=19, day_of_week="Friday",
                           event_type="sports", parking_hotspots=None,
                           dow_factor=None, crowd_hist_ratio=1.0):
    """
    Assign an ECI score to every OSM road segment:
      • Named corridors that match our key arterials inherit their detailed ECI.
      • All other roads get a distance-weighted ECI from the formula.
      • Background congestion ensures non-zero ECI even far from venue.
    Then apply transit augmentation multipliers.
    """
    venue    = VENUES[venue_id]
    vlat, vlng = venue["lat"], venue["lng"]
    roads    = osm_data.get("roads", [])
    uber_corr = UBER_ZONE_CORRIDOR.get(venue_id, "")

    # Effective venue radius: distance from the centre out to the footprint edge.
    # Big venues (Mahalaxmi racecourse, JLN Stadium, DY Patil) span hundreds of
    # metres, so a road hugging the perimeter is far from the CENTRE while being
    # right at the venue. Proximity rules must measure from the edge, not the pin.
    _fp = VENUE_FOOTPRINTS.get(venue_id) or []
    venue_radius_km = (max(haversine_km(p[0], p[1], vlat, vlng) for p in _fp)
                       if len(_fp) >= 3 else 0.12)

    aug_special = augmentation.get("special_train",  False)
    aug_metro   = augmentation.get("extended_metro", False)
    aug_shuttle = augmentation.get("shuttle_bus",    False)
    aug_uber    = augmentation.get("uber_zone",      False)

    # Named corridor ECI lookup (keyed by lowercased corridor name)
    named_eci = _named_corridor_eci_map(
        venue_id, crowd_size, minutes_before,
        transport_split, car_multiplier, disruptions,
        hist_baseline, capacity_bonus, genre_profile,
        event_type=event_type, event_hour=event_hour, day_of_week=day_of_week,
        dow_factor=dow_factor, crowd_hist_ratio=crowd_hist_ratio,
    )

    # Time-of-day aware background ECI (replaces flat BACKGROUND_ECI for unmatched roads)
    bg_curve   = HOURLY_BG_ECI.get(venue_id, HOURLY_BG_ECI["wankhede"])
    bg_hourly  = bg_curve[event_hour % 24]
    if dow_factor is None:   # legacy fallback when predict() didn't pass one
        dow_factor = DOW_TRAFFIC.get(day_of_week, 1.18) / 1.18
    # Road-type scale: arterials carry more of the background congestion burden
    # Local lanes carry far less of the area-wide background than arterials —
    # a side street shouldn't glow like the main road at peak hour.
    _rt_bg_scale = {"arterial": 1.00, "sub_arterial": 0.60, "local": 0.32}

    # Pre-compute one representative ECI per road_type (distance = 0)
    type_base_eci = {}
    for rt in ("arterial", "sub_arterial", "local"):
        fake = {"road_name": "_base_", "road_type": rt, "direction": "inbound"}
        eci_val, _, _, _ = compute_corridor_eci(
            fake, crowd_size, minutes_before,
            transport_split, car_multiplier, disruptions,
            capacity_bonus=capacity_bonus, genre_profile=genre_profile,
        )
        type_base_eci[rt] = round(min(1.0, 0.6 * eci_val + 0.4 * hist_baseline), 4)

    result = []
    for road in roads:
        name      = road["name"]
        road_type = road["road_type"]
        highway   = road["highway"]
        points    = road["points"]
        if len(points) < 2:
            continue

        # ── Try name match against known corridors ────────────────────────────
        matched = None
        if name:
            nl = name.lower()
            for cname, cdata in named_eci.items():
                # Match if any long word (≥5 chars) from corridor name appears in road name
                if any(word in nl for word in cname.split() if len(word) >= 5):
                    matched = cdata
                    break
                # Or exact corridor substring in road name
                if cname in nl or nl in cname:
                    matched = cdata
                    break

        event_weight, road_bg = 1.0, 0.0   # named corridors are event routes by definition
        _mid  = points[len(points) // 2]
        _dist = haversine_km(_mid[0], _mid[1], vlat, vlng)
        if matched:
            eci = matched["eci"]
            vph = matched["vph"]
            spd = matched["speed"]
            clr = matched["clearance_minutes"]
        else:
            # ── Distance-weighted formula ECI (time-of-day aware) ─────────────
            mid, dist = _mid, _dist
            # Event influence: full at venue, zero at 1.1 km
            ew    = max(0.0, 1.0 - (dist / 1.1))
            event_weight = ew
            # Time-of-day background for this road type
            bg    = max(BACKGROUND_ECI[road_type],
                        bg_hourly * _rt_bg_scale.get(road_type, 0.75))
            road_bg = bg
            # Formula ECI scaled by DOW (Friday brings more cars even far from venue)
            base  = min(1.0, type_base_eci[road_type] * dow_factor)
            eci   = round(min(1.0, max(bg * (1.0 - ew), base * ew + bg * (1.0 - ew))), 4)
            vph   = 0
            spd   = round(FREE_FLOW_SPEEDS[road_type] * max(0.05, 1.0 - 0.85 * eci), 1)
            clr   = round(15 * (1 + eci * 3) * (crowd_size / 10000 * 0.15))

        # ── Apply transit augmentation ────────────────────────────────────────
        if aug_special:                         eci = round(eci * 0.65, 4)
        if aug_metro and minutes_before < 0:    eci = round(eci * 0.70, 4)
        if aug_shuttle:                         eci = round(eci * 0.85, 4)
        if aug_uber:
            eci = round(min(1.0, eci * 1.4), 4) if name == uber_corr else round(eci * 0.80, 4)
        eci = min(1.0, eci)

        # ── Disruption multipliers ────────────────────────────────────────────
        # Match only roads with a real name (≥5 chars) — an empty name must never
        # match, since "" is a substring of every corridor string.
        disruption_info = None
        nm = (name or "").strip().lower()
        if len(nm) >= 5:
            for d in disruptions:
                ac = (d.get("affected_corridor") or "").strip().lower()
                if ac and (ac in nm or nm in ac):
                    sev  = d["severity"]
                    mult = 1.2 if sev < 0.3 else (1.5 if sev < 0.6 else 1.9)
                    eci  = round(min(1.0, eci * mult), 4)
                    disruption_info = {"id": d["id"], "description": d["description"]}
                    break

        # ── Parking-lot / pickup-zone proximity bump ──────────────────────────
        # A saturated lot mouth or a cab pileup spills queues onto nearby roads.
        if parking_hotspots:
            mid = points[len(points) // 2]
            bump = 0.0
            for hs in parking_hotspots:
                dkm = haversine_km(mid[0], mid[1], hs["lat"], hs["lng"])
                if dkm < 0.30 and hs["load"] > 0.4:
                    prox = 1.0 - (dkm / 0.30)
                    bump = max(bump, hs["load"] * prox * 0.55)
            if bump > 0:
                eci = round(min(1.0, eci + bump), 4)

        result.append({
            "osm_id":              road["osm_id"],
            "road_name":           name or "Unnamed road",
            "highway":             highway,
            "road_type":           road_type,
            "eci":                 round(eci, 4),
            "los_grade":           eci_to_los(eci),
            "colour":              eci_to_colour(eci),
            "weight":              OSM_WEIGHT.get(highway, 2),
            "predicted_speed_kmph": spd,
            "vehicles_per_hour":   vph,
            "clearance_minutes":   clr,
            "points":              points,
            "disruption":          disruption_info,
            "out_of_city":         False,
            # How much the EVENT raises this road above its own ordinary
            # background level. Named corridors and disrupted roads always
            # count; everything else must clear a real margin, so the map
            # stops outlining streets carrying nothing but normal traffic.
            # Also gate by road class × distance: event traffic on a residential
            # lane 800 m out is not actionable (nobody barricades it), and
            # drawing hundreds of them buries the venue and the real corridors.
            "event_weight":        round(event_weight, 3),
            "event_delta":         round(max(0.0, eci - road_bg), 4),
            "km_from_venue":       round(_dist, 3),
            "km_from_edge":        round(max(0.0, _dist - venue_radius_km), 3),
            # Class filter: service alleys, driveways, residential lanes and
            # construction stubs are never traffic-management targets, and we
            # only score them with a background heuristic — colouring them
            # implies precision we do not have. They stay in the data but are
            # not drawn, UNLESS they are a named corridor or carry a disruption
            # (so a venue's own named access road can never disappear).
            "event_affected":      bool(
                matched or disruption_info
                or (highway not in _UNDRAWN_HIGHWAYS
                    and max(0.0, _dist - venue_radius_km)
                        <= {"arterial": 1.20, "sub_arterial": 0.50,
                            "local": 0.20}.get(road_type, 0.50))),
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# API routes
# ─────────────────────────────────────────────────────────────────────────────

def _int_param(name, default, lo, hi):
    """Parse an int query param safely; bad input falls back to default, clamped to [lo, hi]."""
    try:
        v = int(float(request.args.get(name, default)))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


@app.route("/api/predict", methods=["GET"])
def predict():
    venue_id       = request.args.get("venue", "wankhede")
    crowd_size     = _int_param("crowd", 33000, 100, 200000)
    minutes_before = _int_param("minutes", 60, -360, 720)
    ticket_price   = _int_param("ticket_price", 1500, 0, 1000000)
    event_type     = request.args.get("event_type", "cricket")
    event_time     = request.args.get("event_time", "19:30")
    artist_origin  = request.args.get("artist_origin", "sports")
    gate_layout    = request.args.get("layout", "default")
    original_price = _int_param("original_price", ticket_price, 0, 1000000)
    effective_price= _int_param("effective_price", ticket_price, 0, 1000000)
    out_of_city_pct= _int_param("out_of_city_pct",
                         ARTIST_ORIGIN_MODIFIERS.get(artist_origin, {}).get("out_of_city_default", 5), 0, 100)

    augmentation = {
        "special_train":     request.args.get("special_train",     "false").lower() == "true",
        "extended_metro":    request.args.get("extended_metro",    "false").lower() == "true",
        "shuttle_bus":       request.args.get("shuttle_bus",       "false").lower() == "true",
        "uber_zone":         request.args.get("uber_zone",         "false").lower() == "true",
        "heavy_vehicle_ban": request.args.get("heavy_vehicle_ban", "false").lower() == "true",
    }

    venue = VENUES.get(venue_id)
    if not venue: return jsonify({"error": "Venue not found"}), 404

    # ── Parse event hour and day-of-week for historical learning ──────────────
    try:
        event_hour = int(event_time.split(":")[0])
    except Exception:
        event_hour = 19

    event_date_str = request.args.get("event_date", "")
    try:
        dow = datetime.strptime(event_date_str, "%Y-%m-%d").strftime("%A")
    except Exception:
        dow = datetime.now().strftime("%A")

    # ── Weather multiplier ─────────────────────────────────────────────────────
    # Mumbai rain is the strongest single traffic variable: light rain slows
    # arterials ~15%, heavy monsoon rain 30-40% (waterlogging, stalled cars,
    # people abandoning trains for cabs). Calibrated vs MMRDA 26/7-style deltas.
    weather = request.args.get("weather", "clear").lower()
    WEATHER_FACTORS = {
        "clear":      {"eci_mult": 1.00, "car_shift": 0.00, "label": "Clear"},
        "light_rain": {"eci_mult": 1.15, "car_shift": 0.05, "label": "Light rain"},
        "heavy_rain": {"eci_mult": 1.35, "car_shift": 0.10, "label": "Heavy rain / monsoon"},
    }
    wf = WEATHER_FACTORS.get(weather, WEATHER_FACTORS["clear"])

    bg_eci = HOURLY_BG_ECI.get(venue_id, HOURLY_BG_ECI["wankhede"])[event_hour % 24]
    # EVENT-day factor, not background-day factor. Weekend direction depends on
    # the venue's district: DY Patil sits on the Sion-Panvel/Expressway leisure
    # corridor (weekend = Coldplay gridlock), while BKC/Churchgate are business
    # districts that EMPTY on weekends (IPL Sunday flows fine). The boost also
    # scales with crowd — a 6k club night doesn't move weekend traffic.
    WEEKEND_SENSITIVITY = {"dypatil": 1.0, "nesco": 0.5, "mahalaxmi": 0.3,
                           "dome": 0.3, "wankhede": 0.15, "mmrda": 0.0}
    EVENT_DOW = {"Friday": 1.06, "Saturday": 1.12, "Sunday": 1.08}
    base_dow  = EVENT_DOW.get(dow, 0.98)
    if base_dow > 1.0:
        # Interpolate from "district empties on weekends" (0.85 — CBD venues
        # like BKC/Churchgate) up to the full leisure-corridor boost (suburban
        # highway venues), scaled by crowd — small shows don't move weekends.
        sens     = WEEKEND_SENSITIVITY.get(venue_id, 0.5)
        crowd_sc = min(1.0, crowd_size / 40000.0)
        base_dow = 0.85 + (base_dow - 0.85) * sens * crowd_sc
    dow_factor = round(base_dow * wf["eci_mult"], 4)   # weather folded in

    origin_mod    = ARTIST_ORIGIN_MODIFIERS.get(artist_origin, ARTIST_ORIGIN_MODIFIERS["sports"])
    genre_profile = origin_mod["profile"]
    resale_info   = get_resale_modifier(original_price, effective_price)

    # Base transport split from effective price
    transport_split = get_transport_split(venue_id, effective_price)

    # Rain pushes walkers/train riders into cabs
    if wf["car_shift"] > 0:
        shift = min(wf["car_shift"], transport_split["foot_auto"] * 0.6)
        transport_split["car_cab"]   = round(transport_split["car_cab"] + shift, 3)
        transport_split["foot_auto"] = round(transport_split["foot_auto"] - shift, 3)
        tot = sum(transport_split.values())
        transport_split = {k: round(v/tot, 3) for k, v in transport_split.items()}

    # Special train shifts car->rail, but only as many people as the trains can
    # actually carry (~2 rakes ≈ 3,500 pax). Backtest fix: the old flat 30% shift
    # made Coldplay@DYPatil predict LOS C against real gridlock.
    if augmentation["special_train"]:
        boost = min(0.30, transport_split["car_cab"] * 0.50,
                    3500.0 / max(crowd_size, 1))
        transport_split["train_metro"] = round(transport_split["train_metro"] + boost, 3)
        transport_split["car_cab"]     = round(transport_split["car_cab"]     - boost, 3)
        tot = sum(transport_split.values())
        transport_split = {k: round(v/tot, 3) for k,v in transport_split.items()}

    baseline_car   = BASE_TRANSPORT_SPLIT[venue_id]["car_cab"]
    car_multiplier = round(
        (transport_split["car_cab"] / baseline_car)
        * origin_mod["car_mult_extra"]
        * resale_info["car_modifier"], 3
    )

    hist_baseline, past_events_count = calculate_historical_baseline(venue_id, event_type, crowd_size)
    # Corridor-history events skew big (Lolla-scale at Mahalaxmi) — scale their
    # contribution by this event's crowd vs the venue's typical major-event crowd,
    # so a 42k weekday show doesn't inherit 60k-festival corridor severity.
    TYPICAL_EVENT_CROWD = {"wankhede": 33000, "dome": 8000, "dypatil": 50000,
                           "nesco": 15000, "mmrda": 40000, "mahalaxmi": 60000}
    crowd_hist_ratio = max(0.55, min(1.15,
        crowd_size / TYPICAL_EVENT_CROWD.get(venue_id, 30000)))
    disruptions_active = get_active_disruptions(venue_id)
    capacity_bonus     = 1.15 if augmentation["heavy_vehicle_ban"] else 1.0
    # Venue corridor-capacity correction. Backtest: three real DY Patil mega
    # events (ISL 0.85, Kabaddi 0.83, Coldplay 0.89 severity) all saturated
    # harder than the raw formula — its highway capacities are effectively
    # lower on event nights (tolls, merges, no shoulder discipline).
    VENUE_CAPACITY_FACTOR = {"dypatil": 0.78}
    capacity_bonus *= VENUE_CAPACITY_FACTOR.get(venue_id, 1.0)

    # Main corridors — 3-way blend: formula (DOW-adj) + corridor history + background
    corridors_data = []
    for corridor in ROAD_CORRIDORS.get(venue_id, []):
        eci, vph, speed, disruption_info = compute_corridor_eci(
            corridor, crowd_size, minutes_before,
            transport_split, car_multiplier, disruptions_active,
            capacity_bonus=capacity_bonus, genre_profile=genre_profile,
        )
        ch_mean, _ = get_corridor_historical_eci(venue_id, corridor["road_name"], event_type)
        blended = round(min(1.0,
            0.60 * min(1.0, eci * dow_factor) +
            0.25 * ch_mean * crowd_hist_ratio +
            0.15 * bg_eci
        ), 4)
        corridors_data.append({
            "road_name": corridor["road_name"], "road_type": corridor["road_type"],
            "direction": corridor["direction"], "eci": blended,
            "los_grade": eci_to_los(blended), "predicted_speed_kmph": speed,
            "vehicles_per_hour": vph,
            "clearance_minutes": round(15*(1+blended*3)*(crowd_size/10000*0.15)),
            "colour": eci_to_colour(blended), "weight": ROAD_WEIGHT[corridor["road_type"]],
            "points": corridor["points"], "disruption": disruption_info, "out_of_city": False,
        })

    # Out-of-city corridors
    if out_of_city_pct > 10:
        for key in OUT_OF_CITY_CORRIDOR_MAP.get(venue_id, {}).get(artist_origin, []):
            oc = OUT_OF_CITY_CORRIDORS.get(key)
            if not oc: continue
            oc_tf  = get_time_factor(minutes_before, genre_profile)
            oc_eci = round(min(1.0, 0.4 * (out_of_city_pct / 40) * (1 + 0.3 * oc_tf)), 4)
            oc_ffs = FREE_FLOW_SPEEDS[oc["road_type"]]
            corridors_data.append({
                "road_name": oc["road_name"], "road_type": oc["road_type"],
                "direction": oc["direction"], "eci": oc_eci,
                "los_grade": eci_to_los(oc_eci),
                "predicted_speed_kmph": round(oc_ffs * max(0.05, 1 - 0.85*oc_eci), 1),
                "vehicles_per_hour": max(0, round(crowd_size * 0.01 * out_of_city_pct / 2.5)),
                "clearance_minutes": round(15*(1+oc_eci*3)*(crowd_size/10000*0.15)),
                "colour": eci_to_colour(oc_eci), "weight": 4,
                "points": oc["points"], "disruption": None,
                "out_of_city": True, "label": "Out-of-city arrival corridor",
            })

    # Apply transit augmentation
    corridors_data, active_aug, road_reduction_pct, aug_summary = apply_transit_augmentation(
        corridors_data, augmentation, venue_id, minutes_before, crowd_size
    )

    worst          = max(corridors_data, key=lambda c: c["eci"])
    predicted_peak = add_time_minutes(event_time, -30)
    clearance_time = add_time_minutes(event_time, worst["clearance_minutes"])

    deployment_indicators = build_deployment_indicators(venue_id, corridors_data, crowd_size, event_time)

    # Special train pedestrian marker
    if augmentation["special_train"]:
        station = NEAREST_STATIONS.get(venue_id)
        if station:
            deployment_indicators.append({
                "type": "pedestrian", "lat": station["lat"], "lng": station["lng"],
                "location_name": station["name"],
                "note": "Special event train service — high pedestrian flow expected from this station",
                "priority": "MEDIUM",
            })

    # ── Floor-plan calibration (if one was uploaded for this venue) ───────────
    fplan = load_floorplan(venue_id)
    fp_analysis   = (fplan or {}).get("analysis", {})
    fp_floor_area = fp_analysis.get("floor_area_m2_est") or None
    exit_capacity_factor = 1.0
    if fplan:
        modeled_exits   = len(get_venue_gates(venue_id, gate_layout))
        extracted_exits = len(fp_analysis.get("exits") or []) + len(fp_analysis.get("entrances") or [])
        if modeled_exits and extracted_exits:
            exit_capacity_factor = max(0.7, min(1.3, extracted_exits / modeled_exits))

    # ── Foot traffic + parking/pickup + staff ops (computed up-front so they
    #    can feed the road ECI and each other) ─────────────────────────────────
    foot_traffic = compute_foot_traffic(
        venue_id, crowd_size, minutes_before, transport_split, genre_profile,
        layout=gate_layout, exit_capacity_factor=exit_capacity_factor
    )
    parking_pickup = compute_parking_pickup(
        venue_id, crowd_size, minutes_before, transport_split, genre_profile
    )
    staff_ops = compute_staff_ops(
        venue_id, crowd_size, minutes_before, foot_traffic, parking_pickup, event_time
    )
    internal_density = compute_internal_density(
        venue_id, crowd_size, minutes_before, event_type,
        egress_tail=foot_traffic.get("egress_tail_min", 60),
        floor_area_override=fp_floor_area
    )
    floorplan_block = None
    if fplan:
        used_area = fp_floor_area or VENUE_FLOOR_AREAS.get(venue_id)
        floorplan_block = {
            "active": True,
            "uploaded_at": fplan.get("uploaded_at"),
            "filename": fplan.get("filename"),
            "confidence": fp_analysis.get("confidence"),
            "hotspots": compute_floorplan_hotspots(crowd_size, event_type, fplan, used_area),
            "pinch_points": fp_analysis.get("pinch_points") or [],
            "comparison": {
                "floor_area_modeled_m2": VENUE_FLOOR_AREAS.get(venue_id),
                "floor_area_extracted_m2": fp_floor_area,
                "gates_modeled": len(get_venue_gates(venue_id, gate_layout)),
                "access_points_extracted": len(fp_analysis.get("entrances") or []) + len(fp_analysis.get("exits") or []),
                "exit_capacity_factor_applied": round(exit_capacity_factor, 2),
                "zones_extracted": len(fp_analysis.get("zones") or []),
                "note": ("Floor area from plan replaces the desk estimate; exit capacity scaled by "
                         "extracted access-point count; washroom/bar/VIP zones are NEW intelligence "
                         "not present in the base model."),
            },
        }
    parking_plan = compute_parking_plan(
        venue_id, crowd_size, transport_split, event_time, genre_profile
    )
    # Long-range queues on the approach highways (beyond the 1 km OSM circle)
    extended_feeders = compute_extended_feeders(
        venue_id, crowd_size, minutes_before, transport_split,
        worst["eci"], genre_profile,
        egress_tail=foot_traffic.get("egress_tail_min", 90),
    )
    # Plain-language egress advisory — the single fact every organizer/attendee
    # needs: the worst traffic is right after the event, and for how long.
    _tail = foot_traffic.get("egress_tail_min", 60)
    _bulk = round(EGRESS_BULK_FRACTION.get(genre_profile, 0.85) * 100)
    egress_advisory = {
        "peak_window_min": min(30, max(15, round(_tail * 0.35))),
        "clear_by_min": _tail,
        "bulk_pct": _bulk,
        "text": (f"Heaviest traffic hits in the first {min(30, max(15, round(_tail * 0.35)))} minutes "
                 f"after the event ends (~{_bulk}% of the crowd exits in one pulse); "
                 f"venue surroundings clear in roughly {_tail} minutes."),
    }
    parking_hotspots = [
        {"lat": p["lat"], "lng": p["lng"], "load": p["load"]}
        for p in parking_pickup["parking"]
    ] + [
        {"lat": z["lat"], "lng": z["lng"], "load": z["load"]}
        for z in parking_pickup["pickup_zones"]
    ]

    # ── OSM roads: real curved geometry + ECI for every road ─────────────────
    all_roads    = []
    osm_status   = "ok"
    osm_road_count = 0
    try:
        osm_data = fetch_osm_roads(venue_id)
        if osm_data.get("roads"):
            all_roads = compute_all_roads_eci(
                osm_data, venue_id, crowd_size, minutes_before,
                transport_split, car_multiplier, disruptions_active,
                hist_baseline, capacity_bonus, genre_profile, augmentation,
                event_hour=event_hour, day_of_week=dow, event_type=event_type,
                parking_hotspots=parking_hotspots,
                dow_factor=dow_factor, crowd_hist_ratio=crowd_hist_ratio,
            )
            osm_road_count = len(all_roads)
        elif osm_data.get("error"):
            osm_status = osm_data["error"]
    except Exception as e:
        osm_status = str(e)

    # Fall back to hardcoded corridors if OSM fetch failed
    draw_roads = all_roads if all_roads else corridors_data

    # ── Live Waze alerts: mutate ECI in-place ────────────────────────────────
    live_closures      = []
    live_jams_applied  = 0
    live_alerts_count  = 0
    waze_alerts = _fetch_waze_alerts(venue_id)
    live_alerts_count  = len(waze_alerts)
    if waze_alerts:
        c1, j1 = _apply_live_alerts(draw_roads,    waze_alerts)
        c2, j2 = _apply_live_alerts(corridors_data, waze_alerts)
        # Merge closures from both lists (deduplicated by road_name)
        seen = set()
        for lc in c1 + c2:
            if lc["road_name"] not in seen:
                live_closures.append(lc)
                seen.add(lc["road_name"])
        live_jams_applied = j1 + j2
        # Re-derive worst corridor after live modifications
        worst         = max(corridors_data, key=lambda c: c["eci"])
        clearance_time = add_time_minutes(event_time, worst["clearance_minutes"])

    return jsonify({
        "venue": venue["name"], "event_type": event_type,
        "artist_origin": artist_origin, "crowd_size": crowd_size,
        "minutes_before": minutes_before,
        "ticket_price": effective_price, "original_ticket_price": original_price,
        "resale_info": resale_info,
        "historical_baseline": hist_baseline, "past_events_used": past_events_count,
        "weather": {"condition": weather, "label": wf["label"], "eci_mult": wf["eci_mult"]},
        "active_disruptions": len(disruptions_active), "disruptions_detail": disruptions_active,
        "transport_split": transport_split, "car_multiplier": car_multiplier,
        "out_of_city_pct": out_of_city_pct,
        "worst_eci": worst["eci"], "worst_corridor": worst["road_name"],
        "los_grade": worst["los_grade"],
        "predicted_peak_time": predicted_peak, "clearance_time": clearance_time,
        "corridors": corridors_data,        # named corridors (deployment logic)
        "all_roads": draw_roads,            # all OSM roads with ECI (map drawing)
        "osm_road_count": osm_road_count,
        "osm_status": osm_status,
        "live_road_closures": live_closures,
        "live_jams_applied": live_jams_applied,
        "live_alerts_count": live_alerts_count,
        "live_data_time": datetime.now().strftime("%H:%M:%S"),
        "deployment_indicators": deployment_indicators,
        "transit_augmentation": {
            "active": augmentation, "active_names": active_aug,
            "road_reduction_pct": road_reduction_pct, "summary": aug_summary,
        },
        "crowd_profile": {
            "artist_origin": artist_origin, "genre_profile": genre_profile,
            "resale_signal": resale_info["signal"], "resale_label": resale_info["label"],
            "resale_ratio": resale_info["ratio"], "out_of_city_pct": out_of_city_pct,
        },
        # ── Venue footprint outline (OSM building polygon) ────────────────────
        "venue_footprint": VENUE_FOOTPRINTS.get(venue_id, []),
        # ── Foot traffic ─────────────────────────────────────────────────────
        "foot_traffic": foot_traffic,
        # ── Event-specific gate layouts ───────────────────────────────────────
        "available_layouts": get_available_layouts(venue_id),
        "active_layout": gate_layout,
        # ── Parking & Uber/Ola pickup ─────────────────────────────────────────
        "parking_pickup": parking_pickup,
        # ── Staff operations plan (ticket office, helpdesk, cleaning, medical) ─
        "staff_ops": staff_ops,
        "internal_density": internal_density,
        "parking_plan": parking_plan,
        "egress_advisory": egress_advisory,
        "floorplan": floorplan_block,
        "extended_feeders": extended_feeders,
        # ── Historical learning model ─────────────────────────────────────────
        "historical_model": {
            "day_of_week":         dow,
            "dow_multiplier":      DOW_TRAFFIC.get(dow, 1.18),
            "event_hour":          event_hour,
            "background_eci":      round(bg_eci, 3),
            "past_events_count":   past_events_count,
            "learning_confidence": (
                "high"   if past_events_count >= 5 else
                "medium" if past_events_count >= 3 else "low"
            ),
            "similar_events":      find_similar_events(venue_id, event_type, crowd_size, artist_origin),
            "corridor_baselines":  CORRIDOR_HISTORY.get(venue_id, {}),
        },
    })


@app.route("/api/live-status", methods=["GET"])
def live_status():
    """Lightweight endpoint for frontend polling — returns current Waze alert counts."""
    venue_id = request.args.get("venue", "wankhede")
    if venue_id not in VENUES:
        return jsonify({"error": "Venue not found"}), 404
    alerts   = _fetch_waze_alerts(venue_id)
    closures = sum(1 for a in alerts if "CLOSED" in a["type"])
    jams     = sum(1 for a in alerts if "JAM"    in a["type"])
    return jsonify({
        "venue":     venue_id,
        "closures":  closures,
        "jams":      jams,
        "total":     len(alerts),
        "alerts":    alerts,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/history", methods=["GET"])
def history():
    venue_id = request.args.get("venue", "wankhede")
    data     = load_json("event_history.json")
    return jsonify({"venue": venue_id, "events": data.get(venue_id, [])})


@app.route("/api/disruptions", methods=["GET"])
def disruptions():
    venue_id = request.args.get("venue")
    data     = load_json("disruptions.json")
    all_d    = data.get("disruptions", [])
    if venue_id:
        all_d = [d for d in all_d if d.get("affected_venue") == venue_id]
    return jsonify({"disruptions": all_d})


@app.route("/api/disruptions/live", methods=["GET"])
def disruptions_live():
    venue_id = request.args.get("venue", "wankhede")
    venue    = VENUES.get(venue_id)
    if not venue: return jsonify({"error": "Venue not found"}), 404

    lat, lng, radius = venue["lat"], venue["lng"], 0.05
    results = {"waze": [], "manual": get_active_disruptions(venue_id), "police_notices": [], "waze_error": None}

    if _requests_ok:
        # ── Waze live alerts ──────────────────────────────────────────────────
        try:
            waze_url = (
                f"https://www.waze.com/live-map/api/georss"
                f"?top={lat+radius:.4f}&bottom={lat-radius:.4f}"
                f"&left={lng-radius:.4f}&right={lng+radius:.4f}"
                f"&types=alerts,traffic"
            )
            r = req.get(waze_url, timeout=6,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; EventTrafficBot/1.0)"})
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                data = r.json()
                RELEVANT = {"ROAD_CLOSED", "ROAD_CLOSED_HAZARD", "HAZARD_ON_ROAD_CONSTRUCTION", "JAM"}
                for alert in data.get("alerts", []):
                    atype = alert.get("type", "")
                    if atype in RELEVANT or any(rt in atype for rt in RELEVANT):
                        loc = alert.get("location", {})
                        results["waze"].append({
                            "source": "waze", "lat": loc.get("y", lat), "lng": loc.get("x", lng),
                            "type": atype, "street": alert.get("street", "Unknown road"),
                            "description": alert.get("reportDescription") or atype.replace("_"," ").title(),
                            "severity": _waze_severity(atype),
                        })
        except Exception as e:
            results["waze_error"] = str(e)

        # ── Mumbai Traffic Police notice scrape ───────────────────────────────
        try:
            venue_roads = [c["road_name"] for c in ROAD_CORRIDORS.get(venue_id, [])]
            pr = req.get("https://trafficpolicemumbai.maharashtra.gov.in/en/notices/",
                         timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            if pr.status_code == 200:
                for road in venue_roads:
                    if road.lower() in pr.text.lower():
                        results["police_notices"].append({
                            "source": "mumbai_traffic_police", "road": road,
                            "url": "https://trafficpolicemumbai.maharashtra.gov.in/en/notices/",
                            "note": f"Active notice mentioning {road} on Mumbai Traffic Police website",
                        })
        except Exception:
            pass
    else:
        results["waze_error"] = "requests library not installed — run: pip install requests"

    # Apply live Waze ECI impacts
    waze_impacts = []
    for w in results["waze"]:
        if "CLOSED" in w["type"]:
            waze_impacts.append({"corridor": w["street"], "eci_floor": 1.0, "capacity_factor": 0.0})
        elif "CONSTRUCTION" in w["type"]:
            waze_impacts.append({"corridor": w["street"], "eci_floor": 0.5, "capacity_factor": 0.6})
        elif "JAM" in w["type"]:
            waze_impacts.append({"corridor": w["street"], "eci_floor": 0.5, "capacity_factor": 1.0})

    return jsonify({
        "venue": venue_id, "live_count": len(results["waze"]),
        "live_disruptions": results["waze"], "manual_disruptions": results["manual"],
        "police_notices": results["police_notices"], "waze_error": results["waze_error"],
        "eci_impacts": waze_impacts,
        "total": len(results["waze"]) + len(results["manual"]),
    })


_weather_cache = {}              # (venue, date) -> (payload, fetched_at_ts)
WEATHER_CACHE_TTL = 1800         # 30 min


@app.route("/api/weather", methods=["GET"])
def venue_weather():
    """
    Point weather forecast for a venue's exact coordinates via Open-Meteo
    (free, keyless). Mumbai monsoon cells are hyper-local — DY Patil can be
    dry while Worli floods — so each venue gets its own forecast, not a
    city-wide one. Returns the model's weather bucket for the event hour.
    """
    venue_id = request.args.get("venue", "wankhede")
    venue    = VENUES.get(venue_id)
    if not venue:
        return jsonify({"error": "Venue not found"}), 404
    date_str = request.args.get("date", "") or datetime.now().strftime("%Y-%m-%d")
    try:
        hour = max(0, min(23, int(request.args.get("hour", "19"))))
    except ValueError:
        hour = 19

    cache_key = (venue_id, date_str, hour)
    if cache_key in _weather_cache:
        payload, ts = _weather_cache[cache_key]
        if time.time() - ts < WEATHER_CACHE_TTL:
            return jsonify({**payload, "cached": True})

    if not _requests_ok:
        return jsonify({"error": "requests not installed", "condition": None})

    # Open-Meteo covers ~16 days out; beyond that we can't forecast.
    try:
        days_out = (datetime.strptime(date_str, "%Y-%m-%d").date() - datetime.now().date()).days
    except ValueError:
        return jsonify({"error": "bad date format (YYYY-MM-DD)", "condition": None}), 400
    if days_out < 0 or days_out > 15:
        return jsonify({"condition": None, "reason": f"event is {days_out} days out — forecast only covers 0-15 days",
                        "venue": venue_id})

    try:
        r = req.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": venue["lat"], "longitude": venue["lng"],
                "hourly": "precipitation,precipitation_probability",
                "start_date": date_str, "end_date": date_str,
                "timezone": "Asia/Kolkata",
            },
            timeout=8,
        )
        r.raise_for_status()
        h = r.json().get("hourly", {})
        # Consider the event hour plus the two hours around it (arrival window).
        idxs   = [i for i in (hour - 1, hour, hour + 1) if 0 <= i < len(h.get("precipitation", []))]
        precip = max((h["precipitation"][i] or 0) for i in idxs) if idxs else 0.0
        prob   = max((h.get("precipitation_probability", [0]*24)[i] or 0) for i in idxs) if idxs else 0

        # Bucket: >=4 mm/h = heavy monsoon rain; >=0.4 mm/h (or >60% chance of
        # some rain) = light; else clear.
        if precip >= 4.0:
            condition = "heavy_rain"
        elif precip >= 0.4 or (precip > 0 and prob >= 60):
            condition = "light_rain"
        else:
            condition = "clear"

        payload = {
            "venue": venue_id, "date": date_str, "hour": hour,
            "condition": condition,
            "precip_mm_per_hr": round(precip, 2),
            "probability_pct": prob,
            "source": "open-meteo.com (venue-point forecast)",
            "cached": False,
        }
        _weather_cache[cache_key] = (payload, time.time())
        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": f"weather fetch failed: {str(e)[:120]}", "condition": None})


# ═════════════════════════════════════════════════════════════════════════════
# VENUE AUTO-DISCOVERY — build a full venue profile from bare coordinates.
#
# Everything geometric (venue outline, floor area, approach corridors, feeder
# highways, stations, parking) is pulled from OpenStreetMap so a new venue —
# in any city — needs no hand-authored data. What CANNOT come from a map is
# the behavioural calibration: background congestion by hour, transport mode
# split, cab share. Those are CITY-level and live in CITY_PROFILES below.
# ═════════════════════════════════════════════════════════════════════════════

CITY_PROFILES = {
    "mumbai": {
        "label": "Mumbai (MMR)",
        "centre": (19.0760, 72.8777), "radius_km": 60,
        # Suburban rail is the backbone; car ownership per capita is low and
        # road space is severely constrained (island geography).
        "transport_split": {"train_metro": 0.45, "car_cab": 0.40, "foot_auto": 0.15},
        "cab_share": 0.60,          # of car_cab trips that are cab/auto (not self-driven)
        "parking_occupancy": 2.6,
        "hist_prior": 0.70,
        # Typical weekday background ECI by hour (0-23) — arterial average.
        "bg_eci": [0.11, 0.08, 0.06, 0.05, 0.08, 0.16,
                   0.31, 0.56, 0.74, 0.72, 0.58, 0.51,
                   0.55, 0.58, 0.53, 0.55, 0.68, 0.84,
                   0.88, 0.83, 0.72, 0.58, 0.44, 0.26],
        "dow": {"Monday": 1.08, "Tuesday": 1.12, "Wednesday": 1.18,
                "Thursday": 1.20, "Friday": 1.30, "Saturday": 0.88, "Sunday": 0.72},
        "weekend_sensitivity": 0.4,
        "notes": "Calibrated on 10 backtested Mumbai events (2023-26).",
    },
    "delhi": {
        "label": "Delhi NCR",
        "centre": (28.6139, 77.2090), "radius_km": 70,
        # Delhi Metro carries a large share (~11M daily transit trips citywide,
        # ~50% public-transport mode share) BUT private-vehicle use is far
        # higher than Mumbai and rising (+21% private modes 2005-2019); two-
        # wheelers are ~26% of trips. For ticketed events expect car-dominant
        # arrivals with strong metro support at metro-adjacent venues.
        "transport_split": {"train_metro": 0.35, "car_cab": 0.50, "foot_auto": 0.15},
        "cab_share": 0.45,          # more self-driving than Mumbai
        "parking_occupancy": 2.8,   # larger family groups / more 4-seat use
        "hist_prior": 0.68,
        # Delhi peaks are broader and flatter than Mumbai's twin spikes —
        # a grid road network spreads load, but the PM peak runs longer.
        "bg_eci": [0.10, 0.07, 0.05, 0.05, 0.07, 0.14,
                   0.26, 0.45, 0.66, 0.70, 0.60, 0.53,
                   0.54, 0.56, 0.54, 0.58, 0.70, 0.82,
                   0.85, 0.80, 0.70, 0.55, 0.38, 0.22],
        "dow": {"Monday": 1.06, "Tuesday": 1.10, "Wednesday": 1.14,
                "Thursday": 1.16, "Friday": 1.28, "Saturday": 0.92, "Sunday": 0.75},
        "weekend_sensitivity": 0.5,
        "notes": "Seeded from Delhi travel-demand studies; NOT yet event-backtested.",
    },
}
DEFAULT_CITY = "mumbai"


def _overpass(query, timeout=45, attempts=1):
    """
    Run an Overpass query against mirrors in order.
    Returns a list of elements (possibly empty = genuinely nothing there), or
    None = every mirror failed. Callers MUST distinguish the two: reporting
    "nothing found" for a failed request is how you ship a wrong map.
    """
    if not _requests_ok:
        return None
    mirrors = [OVERPASS_URL,
               "https://overpass.kumi.systems/api/interpreter",
               "https://overpass.private.coffee/api/interpreter"]
    for attempt in range(attempts):
        for mirror in mirrors:
            try:
                r = req.post(mirror, data={"data": f"[out:json][timeout:40];{query}"},
                             timeout=timeout, headers={"User-Agent": "EventTrafficPlatform/1.0"})
                if r.status_code == 200:
                    return r.json().get("elements", [])
            except Exception:
                continue
        if attempt + 1 < attempts:
            time.sleep(2)
    return None


def _polygon_area_m2(pts):
    """Shoelace area of a lat/lng ring, projected to metres locally."""
    if len(pts) < 3:
        return 0.0
    lat0 = sum(p[0] for p in pts) / len(pts)
    mx = [(p[1] - pts[0][1]) * 111320.0 * math.cos(math.radians(lat0)) for p in pts]
    my = [(p[0] - pts[0][0]) * 110540.0 for p in pts]
    n = len(pts)
    return abs(sum(mx[i] * my[(i + 1) % n] - mx[(i + 1) % n] * my[i] for i in range(n))) / 2.0


def _bearing_label(from_pt, to_pt):
    """Compass label for the approach direction (where traffic comes FROM)."""
    dy = from_pt[0] - to_pt[0]
    dx = (from_pt[1] - to_pt[1]) * math.cos(math.radians(to_pt[0]))
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["north", "north-east", "east", "south-east",
            "south", "south-west", "west", "north-west"][int((ang + 22.5) % 360 // 45)]


def nearest_city_profile(lat, lng):
    """Pick the calibration profile whose centre is closest and in range."""
    best, best_d = None, 1e9
    for key, p in CITY_PROFILES.items():
        d = _km_between([lat, lng], list(p["centre"]))
        if d < p["radius_km"] and d < best_d:
            best, best_d = key, d
    return best or DEFAULT_CITY, round(best_d, 1) if best else None


def _merge_profile(new, base):
    """
    Fill sections that FAILED this run from a previous profile. Overpass fails
    different subqueries on different runs, so re-running discovery tops up the
    gaps instead of overwriting good data with a transient failure.
    """
    if not base:
        return new
    filled = []
    def take(field, *keys):
        if new["confidence"].get(field) == "unknown" and base.get(keys[0]):
            for k in keys:
                new[k] = base.get(k)
            new["confidence"][field] = base.get("confidence", {}).get(field, "high")
            filled.append(field)
    take("venue_outline", "footprint", "footprint_area_m2", "floor_area_m2")
    take("corridors", "corridors")
    take("station", "station", "station_dist_km")
    take("parking", "parking")
    if not new.get("gates") and base.get("gates"):
        new["gates"] = base["gates"]
        new["confidence"]["gates"] = base.get("confidence", {}).get("gates", "low")
        filled.append("gates")
    if not new.get("feeders") and base.get("feeders"):
        new["feeders"] = base["feeders"]
        new["confidence"]["feeders"] = "high"
        filled.append("feeders")
    if filled:
        new["warnings"] = [w for w in new["warnings"] if "FAILED" not in w]
        new["warnings"].insert(0, "Kept from the previous successful scan (this run's lookup "
                                  f"failed): {', '.join(filled)}.")
        new["merged_from_previous"] = filled
    return new


def discover_venue(lat, lng, name_hint="", slug=None, base=None):
    """
    Build a complete venue profile from coordinates using OpenStreetMap.
    Returns a dict ready for register_venue_profile(), with a per-field
    `confidence` map so the UI/report can show what was verified vs assumed.
    `base`: a previous profile whose data fills any section that fails now.
    """
    prof_key, dist_km = nearest_city_profile(lat, lng)
    city = CITY_PROFILES[prof_key]
    conf, warn = {}, []

    # ── 1. The venue itself: outline + floor area ────────────────────────────
    name, footprint, area, pitch_area = name_hint or "Venue", [], None, None
    els = _overpass(
        f'(way["leisure"~"^(stadium|sports_centre|pitch)$"](around:450,{lat},{lng});'
        f'way["building"~"^(stadium|sports_hall)$"](around:450,{lat},{lng});'
        f'way["amenity"="events_venue"](around:450,{lat},{lng}););out geom tags;')
    if els:
        best = None
        for e in els:
            t = e.get("tags", {})
            pts = [[p["lat"], p["lon"]] for p in (e.get("geometry") or [])]
            if len(pts) < 3:
                continue
            a = _polygon_area_m2(pts)
            if best is None or a > best[0]:                 # outline = largest polygon
                best = (a, pts, t.get("name"))
            if t.get("leisure") == "pitch":                 # performance floor = the pitch
                pitch_area = max(pitch_area or 0, a)
        if best:
            area, footprint = best[0], best[1]
            name = best[2] or name_hint or "Venue"
            conf["venue_outline"] = "high"
    if not footprint:
        if els is None:
            conf["venue_outline"] = "unknown"
            warn.append("Venue lookup FAILED (OpenStreetMap unreachable) — re-run discovery.")
        else:
            conf["venue_outline"] = "none"
            warn.append("No stadium/venue polygon in OSM — floor area and outline unavailable.")

    # ── 2. Named approach corridors (real OSM geometry) ─────────────────────
    corridors = []
    els = _overpass(                       # critical for the model — worth a retry
        f'way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]["name"]'
        f'(around:1300,{lat},{lng});out geom tags;', attempts=2)
    if els:
        groups = {}
        for e in els:
            t = e.get("tags", {})
            nm, hw = t.get("name"), t.get("highway")
            g = [[p["lat"], p["lon"]] for p in e.get("geometry", [])]
            if not nm or len(g) < 2:
                continue
            rt = OSM_ROAD_TYPE.get(hw, "local")
            key = (nm, rt)
            groups.setdefault(key, []).extend(g)
        ranked = []
        for (nm, rt), pts in groups.items():
            d = min(_km_between([lat, lng], p) for p in pts)
            far = max(pts, key=lambda p: _km_between([lat, lng], p))
            ranked.append((d, nm, rt, pts, far))
        ranked.sort(key=lambda x: ({"arterial": 0, "sub_arterial": 1, "local": 2}[x[2]], x[0]))
        for d, nm, rt, pts, far in ranked[:8]:
            pts = sorted(pts, key=lambda p: -_km_between([lat, lng], p))   # far → venue
            corridors.append({"road_name": nm, "road_type": rt,
                              "direction": "inbound",
                              "approach_from": _bearing_label(far, [lat, lng]),
                              "points": pts[:80]})
    if corridors:
        conf["corridors"] = "high" if len(corridors) >= 3 else "low"
    elif els is None:
        conf["corridors"] = "unknown"
        warn.append("Road lookup FAILED (OpenStreetMap unreachable) — corridors are missing, "
                    "not absent. Re-run discovery before trusting this profile.")
    else:
        conf["corridors"] = "none"
        warn.append("No named roads within 1.3 km — check the coordinates.")

    # ── 3. Nearest rail / metro station (broad query: OSM tagging varies) ────
    station, station_dist = None, None
    els = _overpass(
        f'(node["railway"="station"](around:3000,{lat},{lng});'
        f'node["railway"="halt"](around:3000,{lat},{lng});'
        f'node["public_transport"="station"]["train"="yes"](around:3000,{lat},{lng});'
        f'node["station"="subway"](around:3000,{lat},{lng});'
        f'way["railway"="station"](around:3000,{lat},{lng}););out center tags;')
    if els:
        cand = []
        for e in els:
            elat = e.get("lat") or (e.get("center") or {}).get("lat")
            elng = e.get("lon") or (e.get("center") or {}).get("lon")
            if elat is None:
                continue
            cand.append((_km_between([lat, lng], [elat, elng]),
                         e.get("tags", {}).get("name", "Station"), elat, elng))
        if cand:
            cand.sort()
            d, nm, slat, slng = cand[0]
            station = {"name": nm, "lat": slat, "lng": slng}
            station_dist = round(d, 2)
            conf["station"] = "high"
    if not station:
        if els is None:
            conf["station"] = "unknown"
            warn.append("Station lookup FAILED (OpenStreetMap unreachable) — re-run discovery.")
        else:
            conf["station"] = "none"
            warn.append("No rail/metro station within 3 km — station-crush model disabled.")

    # ── 4. Parking (capacity tag, else area ÷ 28 m² per space) ──────────────
    lots = []
    els = _overpass(
        f'(way["amenity"="parking"](around:1500,{lat},{lng});'
        f'node["amenity"="parking"](around:1500,{lat},{lng}););out geom tags;')
    if els:
        for i, e in enumerate(els):
            t = e.get("tags", {})
            g = [[p["lat"], p["lon"]] for p in e.get("geometry", [])]
            if g:
                plat = sum(p[0] for p in g) / len(g); plng = sum(p[1] for p in g) / len(g)
            else:
                plat, plng = e.get("lat"), e.get("lon")
            if plat is None:
                continue
            cap = t.get("capacity")
            cap = int(cap) if str(cap).isdigit() else (
                int(_polygon_area_m2(g) / 28) if len(g) >= 3 else 0)
            if cap < 25:
                continue
            d_km = _km_between([lat, lng], [plat, plng])
            lots.append({
                "id": f"auto_p{i}", "name": t.get("name") or f"Parking ({cap} spaces)",
                "type": "paid_structure" if t.get("parking") == "multi-storey" else "venue_lot",
                "lat": plat, "lng": plng, "capacity": cap,
                "throughput_cph": max(120, int(cap * 0.55)),
                "access_road": "(auto-detected)",
                "walk_min": max(2, round(d_km * 12)), "price": 0,
            })
        lots.sort(key=lambda l: l["walk_min"])
        lots = lots[:6]
    if lots:
        conf["parking"] = "medium"
    elif els is None:
        conf["parking"] = "unknown"
        warn.append("Parking lookup FAILED (OpenStreetMap unreachable) — re-run discovery.")
    else:
        conf["parking"] = "none"
        warn.append("No mapped parking near the venue — upload a parking plan for an accurate annexure.")

    # ── 5. Gates: OSM entrance nodes, else synthesised on the outline ────────
    gates = []
    els = _overpass(f'node["entrance"](around:400,{lat},{lng});out;')
    ent = [e for e in (els or []) if e.get("lat")]
    if len(ent) >= 2:
        for i, e in enumerate(ent[:8]):
            gates.append({"id": f"auto_g{i}",
                          "name": e.get("tags", {}).get("name") or f"Entrance {i+1} (OSM)",
                          "lat": e["lat"], "lng": e["lon"], "type": "entry_exit",
                          "capacity_pph": 4000, "lanes": 6,
                          "notes": "Auto-detected from OSM entrance node"})
        conf["gates"] = "medium"
    elif footprint:
        # Place gates on the outline nearest the top approach corridors.
        for i, c in enumerate(corridors[:4]):
            road_pt = c["points"][-1]
            near = min(footprint, key=lambda p: _km_between(p, road_pt))
            gates.append({"id": f"auto_g{i}",
                          "name": f"Gate facing {c['road_name'][:30]}",
                          "lat": near[0], "lng": near[1], "type": "entry_exit",
                          "capacity_pph": 4000, "lanes": 6,
                          "notes": f"Estimated — nearest outline point to {c['road_name']}"})
        conf["gates"] = "low"
        warn.append("Gates are ESTIMATED from the venue outline — upload a floor plan to correct them.")
    if gates:
        share = round(1.0 / len(gates), 3)
        for g in gates:
            g["in_share"] = share; g["out_share"] = share
    else:
        conf["gates"] = "none"
        warn.append("No gates determined (needs either OSM entrance nodes, or a venue outline "
                    "plus corridors) — upload a floor plan to supply them.")

    # ── 6. Extended feeder highways (long-range queues) ─────────────────────
    feeders = []
    els = _overpass(
        f'way["highway"~"^(motorway|trunk|primary)$"]["name"](around:6000,{lat},{lng});'
        f'out geom tags;', timeout=60)
    if els:
        groups = {}
        for e in els:
            nm = e.get("tags", {}).get("name")
            g = [[p["lat"], p["lon"]] for p in e.get("geometry", [])]
            if nm and len(g) >= 3:
                groups.setdefault(nm, []).append(g)
        scored = []
        for nm, ways in groups.items():
            reach = max(max(_km_between([lat, lng], p) for p in w) for w in ways)
            if reach >= 1.8:                     # must actually extend outward
                scored.append((reach, nm, ways))
        scored.sort(reverse=True)
        for reach, nm, ways in scored[:3]:
            feeders.append({"road_name": nm, "ways": ways})
        conf["feeders"] = "high" if feeders else "none"
    if not feeders:
        conf["feeders"] = "none"

    # Performance floor: prefer the actual pitch polygon. Falling back to a
    # fraction of the whole footprint badly over-reads for stadiums (the outline
    # includes stands, concourses and often the surrounding sports complex), so
    # the fallback is conservative and flagged low confidence.
    if pitch_area:
        est_floor = round(pitch_area)
        conf["floor_area"] = "high"
    elif area:
        est_floor = round(area * 0.22)
        conf["floor_area"] = "low"
        warn.append("Performance-floor area is a rough fraction of the venue outline — "
                    "upload a floor plan for an accurate crowd-density model.")
    else:
        est_floor = None
        conf["floor_area"] = "none"
    if base and not name_hint and base.get("name") and name == "Venue":
        name = base["name"]

    profile = {
        "slug": slug or re.sub(r"[^a-z0-9]+", "_", (name or "venue").lower()).strip("_")[:40],
        "name": name, "lat": lat, "lng": lng,
        "city": prof_key, "city_label": city["label"], "km_from_city_centre": dist_km,
        "discovered_at": datetime.now().isoformat(timespec="seconds"),
        "footprint": footprint,
        "footprint_area_m2": round(area) if area else None,
        "floor_area_m2": est_floor,
        "corridors": corridors, "station": station, "station_dist_km": station_dist,
        "parking": lots, "gates": gates, "feeders": feeders,
        "confidence": conf, "warnings": warn,
        "auto": True,
    }
    profile = _merge_profile(profile, base)

    # Gates may now be synthesisable using an outline/corridors recovered from base.
    if not profile["gates"] and profile.get("footprint") and profile.get("corridors"):
        for i, c in enumerate(profile["corridors"][:4]):
            road_pt = c["points"][-1]
            near = min(profile["footprint"], key=lambda p: _km_between(p, road_pt))
            profile["gates"].append({
                "id": f"auto_g{i}", "name": f"Gate facing {c['road_name'][:30]}",
                "lat": near[0], "lng": near[1], "type": "entry_exit",
                "capacity_pph": 4000, "lanes": 6,
                "notes": f"Estimated — nearest outline point to {c['road_name']}"})
        if profile["gates"]:
            share = round(1.0 / len(profile["gates"]), 3)
            for g in profile["gates"]:
                g["in_share"] = share; g["out_share"] = share
            profile["confidence"]["gates"] = "low"
            profile["warnings"] = [w for w in profile["warnings"] if "No gates determined" not in w]
            profile["warnings"].append("Gates are ESTIMATED from the venue outline — "
                                       "upload a floor plan to correct them.")
    return profile


def _venue_profile_path(slug):
    safe = re.sub(r"[^a-z0-9_]", "", slug.lower())[:40]
    if not safe:
        raise ValueError("bad slug")
    return os.path.join(BASE_DIR, f"venue_{safe}.json")


def register_venue_profile(p):
    """Inject a discovered profile into the module dicts the model already reads."""
    vid  = p["slug"]
    city = CITY_PROFILES.get(p.get("city"), CITY_PROFILES[DEFAULT_CITY])

    VENUES[vid] = {
        "lat": p["lat"], "lng": p["lng"], "name": p["name"],
        "address": p.get("address") or f"{p['lat']:.4f}, {p['lng']:.4f} · {p.get('city_label','')}",
        "parking_capacity": sum(l.get("capacity", 0) for l in p.get("parking", [])),
        "auto": True,
    }
    ROAD_CORRIDORS[vid]       = [{k: c[k] for k in ("road_name", "road_type", "direction", "points")}
                                 for c in p.get("corridors", [])]
    BASE_TRANSPORT_SPLIT[vid] = dict(city["transport_split"])
    CAB_SHARE[vid]            = city["cab_share"]
    HOURLY_BG_ECI[vid]        = list(city["bg_eci"])
    PEDESTRIAN_CORRIDORS[vid] = []
    PARKING_FACILITIES[vid]   = p.get("parking", [])
    PICKUP_ZONES[vid]         = [{
        "id": f"{vid}_pickup", "name": "Primary cab pickup (auto-sited)",
        "type": "uber_ola",
        "lat": p["lat"], "lng": p["lng"], "capacity_pph": 1800,
        "serves": "All gates", "access_road": (p["corridors"][0]["road_name"]
                                               if p.get("corridors") else "main approach"),
    }]
    if p.get("gates"):
        VENUE_GATES[vid] = p["gates"]
    if p.get("footprint"):
        VENUE_FOOTPRINTS[vid] = p["footprint"]
    if p.get("floor_area_m2"):
        VENUE_FLOOR_AREAS[vid] = p["floor_area_m2"]
    if p.get("station"):
        NEAREST_STATIONS[vid] = p["station"]
        STATION_GATELINE_CAPACITY[vid] = 10000
    if p.get("feeders"):
        # Pre-seed the feeder geometry cache so no Overpass call is needed later.
        try:
            with open(os.path.join(BASE_DIR, f"feeders_cache_{vid}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(p["feeders"], fh, separators=(",", ":"))
        except Exception:
            pass
        EXTENDED_FEEDERS[vid]   = [{"road_name": f["road_name"], "road_type": "arterial",
                                    "points": f["ways"][0]} for f in p["feeders"]]
        FEEDER_OSM_QUERIES[vid] = [{"road_name": f["road_name"], "regex": re.escape(f["road_name"]),
                                    "bbox": (p["lat"] - .06, p["lng"] - .06,
                                             p["lat"] + .06, p["lng"] + .06)}
                                   for f in p["feeders"]]
    VENUE_CITY[vid] = p.get("city", DEFAULT_CITY)
    return vid


VENUE_CITY = {v: DEFAULT_CITY for v in VENUES}    # venue → calibration city


def load_discovered_venues():
    """Register every venue_*.json profile at startup."""
    n = 0
    for fn in os.listdir(BASE_DIR):
        if fn.startswith("venue_") and fn.endswith(".json"):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding="utf-8") as fh:
                    register_venue_profile(json.load(fh))
                n += 1
            except Exception:
                continue
    return n


@app.route("/api/venue/discover", methods=["GET"])
def venue_discover():
    """Preview a venue profile built from coordinates. Nothing is saved."""
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except ValueError:
        return jsonify({"success": False, "error": "lat and lng are required numbers"}), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"success": False, "error": "coordinates out of range"}), 400
    # Use an already-saved profile at these coordinates as a base, so a re-run
    # tops up whatever failed last time instead of discarding good data.
    base = None
    for fn in os.listdir(BASE_DIR):
        if fn.startswith("venue_") and fn.endswith(".json"):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding="utf-8") as fh:
                    cand = json.load(fh)
                if _km_between([lat, lng], [cand["lat"], cand["lng"]]) < 0.25:
                    base = cand
                    break
            except Exception:
                continue
    p = discover_venue(lat, lng, request.args.get("name", "").strip(), base=base)
    if not p["corridors"] and not p["footprint"]:
        return jsonify({"success": False,
                        "error": "Nothing found at these coordinates — check them, "
                                 "or OpenStreetMap may be temporarily unavailable.",
                        "profile": p}), 200
    return jsonify({"success": True, "profile": p, "summary": {
        "name": p["name"], "city": p["city_label"],
        "footprint_area_m2": p["footprint_area_m2"], "floor_area_m2": p["floor_area_m2"],
        "corridors": len(p["corridors"]), "gates": len(p["gates"]),
        "parking_lots": len(p["parking"]), "feeders": len(p["feeders"]),
        "station": (p["station"] or {}).get("name"), "station_dist_km": p["station_dist_km"],
        "confidence": p["confidence"], "warnings": p["warnings"],
    }})


@app.route("/api/venue/save", methods=["POST"])
def venue_save():
    """Persist a discovered profile and register it for immediate use."""
    body = request.get_json(silent=True) or {}
    p = body.get("profile")
    if not p or "slug" not in p:
        return jsonify({"success": False, "error": "profile (from /api/venue/discover) required"}), 400
    if body.get("name"):
        p["name"] = body["name"]
    try:
        path = _venue_profile_path(p["slug"])
    except ValueError:
        return jsonify({"success": False, "error": "invalid venue slug"}), 400
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(p, fh, indent=1)
    vid = register_venue_profile(p)
    return jsonify({"success": True, "venue_id": vid, "name": p["name"]})


@app.route("/api/venues", methods=["GET"])
def list_venues():
    """All venues the model can predict for — built-in and discovered."""
    return jsonify({"venues": [
        {"id": k, "name": v["name"], "lat": v["lat"], "lng": v["lng"],
         "auto": bool(v.get("auto")), "city": VENUE_CITY.get(k, DEFAULT_CITY)}
        for k, v in VENUES.items()]})


@app.route("/api/venue/<slug>", methods=["DELETE"])
def venue_delete(slug):
    try:
        path = _venue_profile_path(slug)
    except ValueError:
        return jsonify({"success": False, "error": "invalid slug"}), 400
    if os.path.exists(path):
        os.remove(path)
        VENUES.pop(slug, None)
        return jsonify({"success": True, "removed": slug,
                        "note": "Restart the server to fully unload it."})
    return jsonify({"success": False, "error": "not found"}), 404


@app.route("/api/floorplan", methods=["GET", "POST", "DELETE"])
def floorplan():
    """
    Venue floor-plan intelligence.
    POST  multipart 'file' (image or PDF) + ?venue= → Claude vision extracts
          entrances/exits/VIP/GA/backstage/lounge/washrooms/bars/stage into
          floorplan_<venue>.json; every later /api/predict uses it.
    GET   → stored analysis. DELETE → remove it.
    """
    venue_id = request.args.get("venue", "wankhede")
    if venue_id not in VENUES:
        return jsonify({"error": "Venue not found"}), 404

    if request.method == "GET":
        fp = load_floorplan(venue_id)
        return jsonify(fp or {"active": False})

    if request.method == "DELETE":
        p = _floorplan_path(venue_id)
        if os.path.exists(p):
            os.remove(p)
        return jsonify({"removed": True})

    # ── POST: analyse an upload ───────────────────────────────────────────────
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "error": "No file uploaded (field name: 'file')"}), 400

    raw = f.read()
    if len(raw) > 15 * 1024 * 1024:
        return jsonify({"success": False, "error": "File too large (max 15 MB)"}), 400

    name = f.filename.lower()
    if name.endswith(".pdf"):
        try:
            import fitz                      # PyMuPDF
        except ImportError:
            return jsonify({"success": False,
                            "error": "PDF support needs PyMuPDF — run: pip install pymupdf "
                                     "(or upload a screenshot/photo of the plan instead)"}), 400
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            pix = doc[0].get_pixmap(dpi=150)
            raw, media_type = pix.tobytes("png"), "image/png"
        except Exception as e:
            return jsonify({"success": False, "error": f"Could not render PDF: {str(e)[:120]}"}), 400
    elif name.endswith((".png",)):
        media_type = "image/png"
    elif name.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
    elif name.endswith((".webp",)):
        media_type = "image/webp"
    else:
        return jsonify({"success": False, "error": "Upload a PNG/JPG/WEBP image or a PDF"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"success": False, "error": "ANTHROPIC_API_KEY env var not set"}), 500
    try:
        import anthropic
    except ImportError:
        return jsonify({"success": False, "error": "anthropic package not installed"}), 500

    schema_prompt = (
        "You are analysing a venue floor plan / site layout / parking plan for event-crowd "
        f"safety modelling. The venue is {VENUES[venue_id]['name']}, Mumbai. "
        "Extract everything visible and return ONLY a valid JSON object, no markdown:\n"
        '{"plan_type":"floor_plan"|"parking_plan"|"site_layout"|"other",'
        '"floor_area_m2_est":number|null,'
        '"stage_position":string|null,'
        '"entrances":[{"label":string,"width_m_est":number|null,"serves":"GA"|"VIP"|"staff"|"mixed"|null,"location_hint":string}],'
        '"exits":[{"label":string,"width_m_est":number|null,"serves":string|null,"location_hint":string}],'
        '"zones":[{"type":"vip"|"ga"|"backstage"|"lounge"|"washroom"|"bar"|"food"|"medical"|"mixing"|"parking"|"other",'
        '"label":string,"relative_size":"small"|"medium"|"large","location_hint":string}],'
        '"vip_ga_crossings":[string],'
        '"pinch_points":[string],'
        '"parking":{"lots":[{"label":string,"capacity_est":number|null}]}|null,'
        '"notes":string,"confidence":"high"|"medium"|"low"}\n'
        "Rules: estimate floor_area_m2_est from any scale bar or known references (null if impossible). "
        "List EVERY entrance/exit/gate you can identify. vip_ga_crossings = points where VIP routes "
        "cross general-admission flow. pinch_points = corridors/doorways that look narrow relative "
        "to the areas they connect. Do not invent features that are not visible."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=2500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(raw).decode()}},
                {"type": "text", "text": schema_prompt},
            ]}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            return jsonify({"success": False, "error": "Could not parse analysis", "raw": text[:400]})
        analysis = json.loads(m.group())
    except Exception as e:
        msg = str(e)
        friendly = ("⏱ API rate limit — wait a minute and retry" if "rate" in msg.lower()
                    else f"Analysis failed: {msg[:150]}")
        return jsonify({"success": False, "error": friendly}), 200

    record = {"active": True, "venue": venue_id, "filename": f.filename,
              "uploaded_at": datetime.now().isoformat(timespec="seconds"),
              "analysis": analysis}
    with open(_floorplan_path(venue_id), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)

    return jsonify({"success": True, "venue": venue_id,
                    "summary": {
                        "plan_type":  analysis.get("plan_type"),
                        "entrances":  len(analysis.get("entrances") or []),
                        "exits":      len(analysis.get("exits") or []),
                        "zones":      len(analysis.get("zones") or []),
                        "crossings":  len(analysis.get("vip_ga_crossings") or []),
                        "pinch_points": analysis.get("pinch_points") or [],
                        "floor_area_m2_est": analysis.get("floor_area_m2_est"),
                        "confidence": analysis.get("confidence"),
                    },
                    "analysis": analysis})


# In-memory cache for /api/research — avoids re-hitting the Anthropic API
# (and burning rate-limit budget) for the same query within an hour.
_research_cache = {}             # key -> (payload_dict, fetched_at_ts)
RESEARCH_CACHE_TTL = 3600        # 1 hour


@app.route("/api/research", methods=["GET"])
def research():
    event_name = request.args.get("event", "").strip()
    venue_id   = request.args.get("venue", "wankhede")
    date       = request.args.get("date", "")
    force      = request.args.get("force", "false").lower() == "true"

    if not event_name:
        return jsonify({"success": False, "error": "event parameter required"}), 400

    # ── Cache hit? ─────────────────────────────────────────────────────────────
    cache_key = f"{event_name.lower()}|{venue_id}|{date}"
    if not force and cache_key in _research_cache:
        payload, ts = _research_cache[cache_key]
        if time.time() - ts < RESEARCH_CACHE_TTL:
            cached = dict(payload)
            cached["cached"] = True
            cached["cache_age_s"] = int(time.time() - ts)
            return jsonify(cached)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"success": False,
                        "error": "ANTHROPIC_API_KEY env var not set. Set it and restart the server."}), 500

    try:
        import anthropic
    except ImportError:
        return jsonify({"success": False, "error": "anthropic package not installed — run: pip install anthropic"}), 500

    venue_name = VENUES.get(venue_id, {}).get("name", "Mumbai")

    system_prompt = (
        "You are a traffic prediction research assistant for Mumbai event venues. "
        "Research the given event and return ONLY a valid JSON object — no markdown, no extra text.\n\n"
        "VENUE — pick one of these 6 keys (or null if unsure): "
        "'wankhede' (Wankhede Stadium, Churchgate), 'dome' (Dome @ NSCI SVP Stadium, Worli), "
        "'dypatil' (DY Patil Stadium, Nerul), 'nesco' (NESCO / Bombay Exhibition Centre, Goregaon), "
        "'mmrda' (MMRDA Ground, BKC), 'mahalaxmi' (Mahalaxmi Racecourse / RWITC).\n\n"
        "EVENT_TYPE — 'cricket' (IPL/international match), 'football' (ISL/friendly), "
        "'concert' (any music — DJ, band, EDM, Bollywood, Western), 'other' (exhibition, comedy, awards, etc).\n\n"
        "EVENT_DATE / EVENT_TIME — return the show's actual date (YYYY-MM-DD) and doors-open / show-start "
        "time (HH:MM, 24-hour). null if unknown.\n\n"
        "RESALE PRICES — this is important and most often wrong. Actually search reseller sites: "
        "Viagogo India, StubHub India, Twickets, BookMyShow resale, Insider.in waitlist, Paytm Insider, "
        "and informal channels (Twitter/X listings, Instagram resale accounts, OLX, Reddit r/mumbai). "
        "Report a representative average price from actual current listings (₹). If no listings exist, "
        "set resale_ticket_avg to null and resale_signal to 'unknown' or 'normal' — do NOT invent a number. "
        "Compute resale_ratio = resale_ticket_avg / face_value_ticket_avg only when both are real.\n\n"
        "Schema:\n"
        '{"event_name":string,"venue":"wankhede"|"dome"|"dypatil"|"nesco"|"mmrda"|"mahalaxmi"|null,'
        '"event_type":"cricket"|"football"|"concert"|"other",'
        '"event_date":string|null,"event_time":string|null,'
        '"artist_origin":"western_superstar"|"western_midtier"|"indian_mainstream"|"indian_hiphop"|"bollywood_party"|"edm_festival"|"sports",'
        '"expected_crowd":number,"face_value_ticket_avg":number,"resale_ticket_avg":number|null,'
        '"resale_ratio":number|null,"resale_signal":"extreme_premium"|"premium"|"normal"|"moderate_collapse"|"severe_collapse"|"unknown",'
        '"resale_evidence":string,'
        '"transit_augmentation":{"special_train":boolean,"special_train_route":string|null,"extended_metro":boolean,"shuttle_bus":boolean,"uber_zone":boolean,"heavy_vehicle_ban":boolean},'
        '"out_of_city_percentage":number,"genre_arrival_profile":"western_superstar"|"indian_mainstream"|"bollywood_party"|"sports"|"edm",'
        '"weather_forecast":string|null,"confidence":"high"|"medium"|"low","notes":string}'
    )

    user_msg = (
        f"Research this event: '{event_name}'"
        + (f" on {date}" if date else "")
        + f". The dashboard is currently set to '{venue_name}' as a hint, "
        "but determine the ACTUAL venue, event type, date, and start time from your web search and "
        "fill the corresponding fields — override the hint if the real venue is different. "
        "Use multiple web searches if needed and SPEND search budget on resale prices: "
        "check Viagogo India, StubHub India, Twickets, BookMyShow resale, Paytm Insider, "
        "Instagram resale accounts, Twitter ticket listings, OLX. "
        "In 'resale_evidence' put a one-line note saying where you found resale data (which sites, how many "
        "listings, the price range you observed) — or 'no resale listings found' if there are none. "
        "Also find expected attendance, face-value ticket price, any special-train/metro announcements, "
        "weather forecast, and traffic advisories. Return the JSON object only."
    )

    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2200,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )

        result_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                result_text += block.text

        match = re.search(r'\{[\s\S]*\}', result_text)
        if not match:
            return jsonify({"success": False, "error": "Could not parse JSON from research response",
                            "raw": result_text[:500]})

        data = json.loads(match.group())
        payload = {"success": True, "data": data,
                   "confidence": data.get("confidence", "medium"),
                   "cached": False}
        # Cache successful results for 1 hour to keep API usage low (bounded)
        if len(_research_cache) >= 200:
            oldest = min(_research_cache, key=lambda k: _research_cache[k][1])
            del _research_cache[oldest]
        _research_cache[cache_key] = (payload, time.time())
        return jsonify(payload)

    except Exception as e:
        # Make rate-limit and overload errors user-friendly instead of leaking raw API text
        msg = str(e)
        low = msg.lower()
        if "rate" in low and "limit" in low:
            friendly = ("⏱ Anthropic API rate limit hit. Wait ~60 s and try again — "
                        "or set `&force=false` (default) to reuse the last result.")
        elif "overload" in low or "529" in msg:
            friendly = "⚠ Anthropic API temporarily overloaded. Try again in a minute."
        elif "401" in msg or "authentication" in low or "invalid" in low and "api" in low:
            friendly = "🔑 ANTHROPIC_API_KEY is missing or invalid — check the env var."
        elif "timeout" in low or "timed out" in low:
            friendly = "⏱ Research call timed out. Try again, or simplify the query."
        else:
            friendly = f"Research failed: {msg[:160]}"
        return jsonify({"success": False, "error": friendly, "detail": msg[:300]}), 200


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


# Register any auto-discovered venues saved as venue_*.json (runs on import so
# it also applies under waitress/gunicorn, not just `python app.py`).
try:
    _n_auto = load_discovered_venues()
    if _n_auto:
        print(f" * Registered {_n_auto} auto-discovered venue(s)")
except Exception as _e:
    print(f" * Could not load discovered venues: {_e}")


if __name__ == "__main__":
    # SECURITY: debug=True enables the Werkzeug interactive debugger, which is
    # remote code execution for anyone who can reach the port. Keep host on
    # 127.0.0.1 for development; NEVER expose this publicly. For deployment use:
    #   waitress-serve --port=5000 app:app
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug)
