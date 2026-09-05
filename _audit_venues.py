# -*- coding: utf-8 -*-
"""Audit every Mumbai venue's hardcoded geodata against OpenStreetMap."""
import sys, time, math, json
sys.stdout.reconfigure(encoding="utf-8")
import app as A

MUMBAI = ["wankhede", "dome", "dypatil", "nesco", "mmrda", "mahalaxmi"]
issues = []


def note(venue, sev, what, detail):
    issues.append((sev, venue, what, detail))
    print(f"  [{sev}] {what}: {detail}")


def poly_centre(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


for vid in MUMBAI:
    v = A.VENUES[vid]
    vlat, vlng = v["lat"], v["lng"]
    print(f"\n=== {v['name']} ({vid}) ===")

    # 1. Venue centre vs OSM stadium/venue polygon
    els = A._overpass(
        f'(way["leisure"~"^(stadium|sports_centre|racetrack|pitch)$"](around:900,{vlat},{vlng});'
        f'way["building"~"^(stadium|sports_hall)$"](around:900,{vlat},{vlng});'
        f'way["amenity"="events_venue"](around:900,{vlat},{vlng}););out geom tags;')
    if els is None:
        note(vid, "SKIP", "venue lookup", "Overpass unreachable")
    elif not els:
        note(vid, "WARN", "venue polygon", "no stadium/venue polygon in OSM within 900 m")
    else:
        best = max(
            ((A._polygon_area_m2([[p["lat"], p["lon"]] for p in (e.get("geometry") or [])]),
              e) for e in els if len(e.get("geometry") or []) >= 3),
            default=(0, None))
        if best[1]:
            pts = [[p["lat"], p["lon"]] for p in best[1]["geometry"]]
            c = poly_centre(pts)
            off = A.haversine_km(vlat, vlng, c[0], c[1]) * 1000
            nm = best[1].get("tags", {}).get("name", "?")
            sev = "ERROR" if off > 250 else ("WARN" if off > 120 else "ok")
            print(f"  [{sev}] centre vs OSM '{nm[:34]}': off by {off:.0f} m "
                  f"(osm {c[0]:.5f},{c[1]:.5f}) area {best[0]:,.0f} m²")
            if sev != "ok":
                issues.append((sev, vid, "venue centre", f"{off:.0f} m from OSM '{nm}'"))
            # footprint sanity
            fp = A.VENUE_FOOTPRINTS.get(vid) or []
            if len(fp) >= 3:
                ours = A._polygon_area_m2(fp)
                ratio = ours / best[0] if best[0] else 0
                fc = poly_centre(fp)
                foff = A.haversine_km(fc[0], fc[1], c[0], c[1]) * 1000
                s2 = "ERROR" if (ratio < 0.3 or ratio > 3.0 or foff > 250) else "ok"
                print(f"  [{s2}] footprint: ours {ours:,.0f} m² vs OSM {best[0]:,.0f} m² "
                      f"(x{ratio:.2f}), centre off {foff:.0f} m")
                if s2 != "ok":
                    issues.append((s2, vid, "footprint",
                                   f"area x{ratio:.2f} of OSM, centre off {foff:.0f} m"))

    # 2. Nearest station vs OSM
    st = A.NEAREST_STATIONS.get(vid)
    if st:
        els = A._overpass(
            f'(node["railway"="station"](around:3500,{vlat},{vlng});'
            f'node["public_transport"="station"](around:3500,{vlat},{vlng});'
            f'node["station"="subway"](around:3500,{vlat},{vlng});'
            f'way["railway"="station"](around:3500,{vlat},{vlng}););out center tags;')
        if els is None:
            note(vid, "SKIP", "station lookup", "Overpass unreachable")
        else:
            cands = []
            for e in els:
                la = e.get("lat") or (e.get("center") or {}).get("lat")
                lo = e.get("lon") or (e.get("center") or {}).get("lon")
                if la is None:
                    continue
                cands.append((A.haversine_km(st["lat"], st["lng"], la, lo) * 1000,
                              e.get("tags", {}).get("name", "?"), la, lo))
            if cands:
                cands.sort()
                d, nm, la, lo = cands[0]
                sev = "ERROR" if d > 400 else ("WARN" if d > 180 else "ok")
                print(f"  [{sev}] station '{st['name'][:28]}' vs OSM '{nm[:28]}': {d:.0f} m apart")
                if sev != "ok":
                    issues.append((sev, vid, "station coords",
                                   f"'{st['name']}' is {d:.0f} m from OSM '{nm}'"))
            else:
                note(vid, "WARN", "station", "no OSM station found within 3.5 km")

    # 3. Parking lots vs OSM amenity=parking
    lots = A.PARKING_FACILITIES.get(vid, [])
    els = A._overpass(
        f'(way["amenity"="parking"](around:2500,{vlat},{vlng});'
        f'node["amenity"="parking"](around:2500,{vlat},{vlng}););out center tags;')
    if els is None:
        note(vid, "SKIP", "parking lookup", "Overpass unreachable")
    else:
        osm_p = []
        for e in els:
            la = e.get("lat") or (e.get("center") or {}).get("lat")
            lo = e.get("lon") or (e.get("center") or {}).get("lon")
            if la is not None:
                osm_p.append((la, lo, e.get("tags", {}).get("name", "")))
        for l in lots:
            if not osm_p:
                break
            d, nm = min(((A.haversine_km(l["lat"], l["lng"], p[0], p[1]) * 1000, p[2])
                         for p in osm_p))
            sev = "WARN" if d > 250 else "ok"
            print(f"  [{sev}] parking '{l['name'][:32]}': nearest OSM parking {d:.0f} m"
                  + (f" ('{nm[:24]}')" if nm else ""))
            if sev != "ok":
                issues.append((sev, vid, "parking coords",
                               f"'{l['name']}' has no OSM parking within {d:.0f} m"))

    # 4. Gates should sit on/near the venue footprint
    fp = A.VENUE_FOOTPRINTS.get(vid) or []
    if len(fp) >= 3:
        for g in A.get_venue_gates(vid, "default"):
            dmin = min(A.haversine_km(g["lat"], g["lng"], p[0], p[1]) * 1000 for p in fp)
            sev = "ERROR" if dmin > 220 else ("WARN" if dmin > 120 else "ok")
            if sev != "ok":
                print(f"  [{sev}] gate '{g['name'][:34]}': {dmin:.0f} m from venue outline")
                issues.append((sev, vid, "gate coords",
                               f"'{g['name'][:40]}' is {dmin:.0f} m off the outline"))

    # 5. Corridor names should exist in OSM near the venue
    els = A._overpass(f'way["highway"]["name"](around:1600,{vlat},{vlng});out tags;')
    if els is None:
        note(vid, "SKIP", "corridor lookup", "Overpass unreachable")
    else:
        names = {e["tags"]["name"].lower() for e in els if e.get("tags", {}).get("name")}
        for c in A.ROAD_CORRIDORS.get(vid, []):
            n = c["road_name"].lower()
            hit = any(n in o or o in n for o in names)
            if not hit:
                print(f"  [WARN] corridor '{c['road_name']}' not found in OSM within 1.6 km")
                issues.append(("WARN", vid, "corridor name",
                               f"'{c['road_name']}' not in OSM near venue"))
    time.sleep(1)

print("\n\n================ SUMMARY ================")
for sev in ("ERROR", "WARN", "SKIP"):
    rows = [i for i in issues if i[0] == sev]
    print(f"\n{sev}: {len(rows)}")
    for _, v, what, det in rows:
        print(f"   {v:10s} {what:16s} {det}")
json.dump([{"sev": s, "venue": v, "what": w, "detail": d} for s, v, w, d in issues],
          open("_audit_result.json", "w", encoding="utf-8"), indent=1)
