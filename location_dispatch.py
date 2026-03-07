"""
location_dispatch.py
--------------------
Fully free, no API key needed.

Dispatches to different locations based on emergency TYPE and SEVERITY:

  AMBULANCE high   → Nearest A&E Hospital
  AMBULANCE medium → Nearest Polyclinic
  AMBULANCE low    → Nearest GP Clinic

  FIRE high        → Nearest SCDF Fire Station
  FIRE medium      → Nearest SCDF Fire Station (non-emergency contact)
  FIRE low         → Building management (no map)

  POLICE high      → Nearest Police Station
  POLICE medium    → Nearest Neighbourhood Police Centre (NPC)
  POLICE low       → Community safety (no map)

  SOCIAL high      → Nearest Hospital Social Work Dept
  SOCIAL medium    → Nearest PAP Community Foundation Centre
  SOCIAL low       → Nearest Senior Activity Centre
"""

import requests
import webbrowser
import math

# ══════════════════════════════════════════════════════════
# ✏️  EDIT THIS — your fixed device location
# ══════════════════════════════════════════════════════════
DEVICE_LOCATION = {
   "name":    "this place",
   "address": "11 Eunos Rd 8, Singapore 408601",
   "lat":     1.3199696867409512,    # Right-click your address on maps.google.com → copy lat
   "lng":    103.89233526275515,  # and paste here
   "floor":   "Level 2",
   "notes":   "Take lift to Level 2, turn left",
}

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════
SEARCH_RADIUS_M = 10000
MAX_CANDIDATES  = 5
OPEN_IN_BROWSER = True

# ══════════════════════════════════════════════════════════
# HARDCODED SINGAPORE LOCATIONS
# ══════════════════════════════════════════════════════════

SG_AE_HOSPITALS = [
    {"name": "Singapore General Hospital (SGH)",       "lat": 1.2739,  "lng": 103.8339},
    {"name": "Changi General Hospital (CGH)",          "lat": 1.3402,  "lng": 103.9496},
    {"name": "Tan Tock Seng Hospital (TTSH)",          "lat": 1.3196,  "lng": 103.8409},
    {"name": "National University Hospital (NUH)",     "lat": 1.2963,  "lng": 103.7834},
    {"name": "Khoo Teck Puat Hospital (KTPH)",         "lat": 1.4243,  "lng": 103.8198},
    {"name": "Ng Teng Fong General Hospital (NTFGH)",  "lat": 1.3334,  "lng": 103.7458},
    {"name": "Sengkang General Hospital (SKH)",        "lat": 1.3914,  "lng": 103.9006},
    {"name": "KK Women's & Children's Hospital (KKH)", "lat": 1.3063,  "lng": 103.8415},
]

SG_POLYCLINICS = [
    {"name": "Ang Mo Kio Polyclinic",     "lat": 1.36940, "lng": 103.84760},
    {"name": "Bedok Polyclinic",          "lat": 1.32450, "lng": 103.92870},
    {"name": "Bukit Batok Polyclinic",    "lat": 1.34900, "lng": 103.74900},
    {"name": "Bukit Merah Polyclinic",    "lat": 1.28410, "lng": 103.81640},
    {"name": "Choa Chu Kang Polyclinic",  "lat": 1.38540, "lng": 103.74540},
    {"name": "Clementi Polyclinic",       "lat": 1.31520, "lng": 103.76520},
    {"name": "Geylang Polyclinic",        "lat": 1.31860, "lng": 103.88230},
    {"name": "Hougang Polyclinic",        "lat": 1.37130, "lng": 103.89360},
    {"name": "Jurong Polyclinic",         "lat": 1.34380, "lng": 103.70630},
    {"name": "Kallang Polyclinic",        "lat": 1.31060, "lng": 103.86650},
    {"name": "Marine Parade Polyclinic",  "lat": 1.30270, "lng": 103.90720},
    {"name": "Outram Polyclinic",         "lat": 1.27970, "lng": 103.83540},
    {"name": "Pasir Ris Polyclinic",      "lat": 1.37250, "lng": 103.94900},
    {"name": "Punggol Polyclinic",        "lat": 1.40350, "lng": 103.90950},
    {"name": "Queenstown Polyclinic",     "lat": 1.29450, "lng": 103.80560},
    {"name": "Sembawang Polyclinic",      "lat": 1.44900, "lng": 103.81980},
    {"name": "Sengkang Polyclinic",       "lat": 1.39160, "lng": 103.89540},
    {"name": "Tampines Polyclinic",       "lat": 1.35280, "lng": 103.94340},
    {"name": "Toa Payoh Polyclinic",      "lat": 1.33230, "lng": 103.84660},
    {"name": "Woodlands Polyclinic",      "lat": 1.43700, "lng": 103.78650},
    {"name": "Yishun Polyclinic",         "lat": 1.42590, "lng": 103.83850},
]

SG_POLICE_STATIONS = [
    {"name": "Ang Mo Kio Police Division HQ",  "lat": 1.37020, "lng": 103.84530},
    {"name": "Bedok Police Division HQ",       "lat": 1.32450, "lng": 103.92560},
    {"name": "Bishan Police Division HQ",      "lat": 1.35160, "lng": 103.84850},
    {"name": "Bukit Merah East NPC",           "lat": 1.28480, "lng": 103.82350},
    {"name": "Clementi Police Division HQ",    "lat": 1.31380, "lng": 103.76540},
    {"name": "Jurong Police Division HQ",      "lat": 1.34270, "lng": 103.69860},
    {"name": "Woodlands Police Division HQ",   "lat": 1.43640, "lng": 103.78640},
    {"name": "Yishun Police Division HQ",      "lat": 1.42620, "lng": 103.83520},
    {"name": "Tampines Police Division HQ",    "lat": 1.35290, "lng": 103.94360},
    {"name": "Central Police Division HQ",     "lat": 1.29560, "lng": 103.85230},
]

SG_NPCS = [
    {"name": "Bedok North NPC",            "lat": 1.33450, "lng": 103.93120},
    {"name": "Bukit Timah NPC",            "lat": 1.33450, "lng": 103.77540},
    {"name": "Clementi NPC",               "lat": 1.31520, "lng": 103.76520},
    {"name": "Geylang NPC",                "lat": 1.31530, "lng": 103.87820},
    {"name": "Hougang NPC",                "lat": 1.37290, "lng": 103.89230},
    {"name": "Jurong East NPC",            "lat": 1.33380, "lng": 103.74210},
    {"name": "Pasir Ris NPC",              "lat": 1.37310, "lng": 103.94850},
    {"name": "Punggol NPC",                "lat": 1.40290, "lng": 103.90620},
    {"name": "Queenstown NPC",             "lat": 1.29450, "lng": 103.80180},
    {"name": "Sengkang NPC",               "lat": 1.39520, "lng": 103.89540},
    {"name": "Tampines NPC",               "lat": 1.35450, "lng": 103.94120},
    {"name": "Woodlands NPC",              "lat": 1.43560, "lng": 103.78540},
    {"name": "Yishun NPC",                 "lat": 1.42290, "lng": 103.83620},
]

SG_SENIOR_ACTIVITY_CENTRES = [
    {"name": "Bedok Senior Activity Centre",       "lat": 1.32640, "lng": 103.93020},
    {"name": "Tampines Senior Activity Centre",    "lat": 1.35450, "lng": 103.94560},
    {"name": "Ang Mo Kio Senior Activity Centre",  "lat": 1.36940, "lng": 103.84560},
    {"name": "Woodlands Senior Activity Centre",   "lat": 1.43650, "lng": 103.78650},
    {"name": "Jurong Senior Activity Centre",      "lat": 1.34270, "lng": 103.69860},
    {"name": "Yishun Senior Activity Centre",      "lat": 1.42590, "lng": 103.83520},
    {"name": "Hougang Senior Activity Centre",     "lat": 1.37130, "lng": 103.89360},
    {"name": "Sengkang Senior Activity Centre",    "lat": 1.39450, "lng": 103.89540},
]

SG_PAPCP_CENTRES = [
    {"name": "PAP Community Foundation Bedok",      "lat": 1.32450, "lng": 103.92870},
    {"name": "PAP Community Foundation Tampines",   "lat": 1.35280, "lng": 103.94340},
    {"name": "PAP Community Foundation Ang Mo Kio", "lat": 1.36940, "lng": 103.84760},
    {"name": "PAP Community Foundation Woodlands",  "lat": 1.43700, "lng": 103.78650},
    {"name": "PAP Community Foundation Jurong",     "lat": 1.34380, "lng": 103.70630},
    {"name": "PAP Community Foundation Yishun",     "lat": 1.42590, "lng": 103.83850},
]

# ══════════════════════════════════════════════════════════
# LOCATION LOOKUP HELPERS
# ══════════════════════════════════════════════════════════

def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

def _nearest_from_list(lat: float, lng: float, places: list, place_type: str) -> list:
    """Sort a hardcoded list by straight-line distance."""
    results = []
    for p in places:
        dist = _haversine_km(lat, lng, p["lat"], p["lng"])
        results.append({**p, "type": place_type, "dist_km": round(dist, 2)})
    results.sort(key=lambda x: x["dist_km"])
    return results[:MAX_CANDIDATES]

def find_scdf_stations(lat: float, lng: float) -> list:
    """Query OpenStreetMap for nearby SCDF fire stations."""
    print("[Dispatch] Searching for nearby SCDF stations...")
    query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      way["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
    );
    out center {MAX_CANDIDATES * 3};
    """
    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query}, timeout=15
        )
        elements = response.json().get("elements", [])
        stations = []
        seen = set()
        for el in elements:
            s_lat = el.get("lat") or el.get("center", {}).get("lat")
            s_lng = el.get("lon") or el.get("center", {}).get("lon")
            if not s_lat or not s_lng:
                continue
            tags = el.get("tags", {})
            name = tags.get("name") or "SCDF Station"
            key  = f"{round(s_lat,4)},{round(s_lng,4)}"
            if key in seen:
                continue
            seen.add(key)
            dist = _haversine_km(lat, lng, s_lat, s_lng)
            stations.append({"name": name, "type": "SCDF Station",
                              "lat": s_lat, "lng": s_lng, "dist_km": round(dist, 2)})
        stations.sort(key=lambda x: x["dist_km"])
        print(f"[Dispatch] Found {len(stations)} SCDF station(s).")
        return stations[:MAX_CANDIDATES]
    except Exception as e:
        print(f"[Dispatch] Overpass error: {e}")
        return []

def get_driving_route(origin_lat, origin_lng, dest_lat, dest_lng):
    try:
        url = (f"http://router.project-osrm.org/route/v1/driving/"
               f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
               f"?overview=false&steps=false")
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "duration_min": round(route["duration"] / 60, 1),
                "distance_km":  round(route["distance"] / 1000, 2),
            }
    except Exception as e:
        print(f"[Dispatch] OSRM error: {e}")
    return None

def build_maps_url(station: dict, device: dict) -> str:
    return (f"https://www.google.com/maps/dir/"
            f"{station['lat']},{station['lng']}/"
            f"{device['lat']},{device['lng']}")

def _pick_and_open(candidates: list, device: dict, label: str):
    """Get driving times, pick fastest, open Google Maps."""
    if not candidates:
        print(f"  ⚠️  No {label} found nearby.")
        return

    print(f"[Dispatch] Checking driving routes for {len(candidates)} {label}(s)...")
    routed = []
    for c in candidates:
        route = get_driving_route(c["lat"], c["lng"], device["lat"], device["lng"])
        if route:
            c["duration_min"] = route["duration_min"]
            c["distance_km"]  = route["distance_km"]
        else:
            c["duration_min"] = None
        routed.append(c)

    routed.sort(key=lambda x: (x["duration_min"] is None, x["duration_min"] or x["dist_km"]))
    best = routed[0]
    maps_url = build_maps_url(best, device)

    print(f"\n  ✅  FASTEST {label.upper()}")
    print(f"     Name      : {best['name']}")
    print(f"     Drive Time: {best['duration_min']} min" if best["duration_min"] else "     Drive Time: N/A")
    print(f"     Road Dist : {best['distance_km']} km")
    print(f"     Maps      : {maps_url}")

    if len(routed) > 1:
        print(f"\n  📋 OTHER NEARBY OPTIONS")
        for s in routed[1:4]:
            t = f"{s['duration_min']} min" if s["duration_min"] else "N/A"
            print(f"     • {s['name']} — {t} ({s.get('distance_km', s.get('dist_km'))} km)")

    if OPEN_IN_BROWSER:
        print(f"  🗺️  Opening Google Maps...")
        webbrowser.open(maps_url)

# ══════════════════════════════════════════════════════════
# MAIN DISPATCH — called from classifier.py
# ══════════════════════════════════════════════════════════

def trigger_dispatch(source: str, team: str = "ambulance", urgency: str = "high") -> None:
    """
    Dispatches to the right type of location based on team + urgency.

    AMBULANCE high   → A&E Hospital
    AMBULANCE medium → Polyclinic
    AMBULANCE low    → GP Clinic (OSM search)

    FIRE high/medium → SCDF Fire Station
    FIRE low         → No map (building management)

    POLICE high      → Police Station
    POLICE medium    → Neighbourhood Police Centre
    POLICE low       → No map (community safety)

    SOCIAL high      → A&E Hospital (social work dept)
    SOCIAL medium    → PAP Community Foundation Centre
    SOCIAL low       → Senior Activity Centre
    """
    device = DEVICE_LOCATION
    lat, lng = device["lat"], device["lng"]

    source_label = {
        "audio":  "AUDIO — mic / speech trigger",
        "video":  "VIDEO — pose / fall detection",
        "manual": "MANUAL — button pressed",
    }.get(source, source.upper())

    print("\n" + "=" * 58)
    print("  🚨  EMERGENCY DISPATCH TRIGGERED")
    print(f"  Source   : {source_label}")
    print(f"  Team     : {team.upper()}  |  Urgency: {urgency.upper()}")
    print(f"  Address  : {device['address']}")
    print(f"  Notes    : {device['notes']}")
    print("=" * 58)

    # ── AMBULANCE ─────────────────────────────────────────
    if team == "ambulance":
        if urgency == "high":
            candidates = _nearest_from_list(lat, lng, SG_AE_HOSPITALS, "A&E Hospital")
            _pick_and_open(candidates, device, "A&E Hospital")
        elif urgency == "medium":
            candidates = _nearest_from_list(lat, lng, SG_POLYCLINICS, "Polyclinic")
            _pick_and_open(candidates, device, "Polyclinic")
        else:
            print("  🏥 LOW — Directing to nearest GP clinic")
            url = f"https://www.google.com/maps/search/GP+clinic/@{lat},{lng},14z"
            if OPEN_IN_BROWSER:
                webbrowser.open(url)

    # ── FIRE ──────────────────────────────────────────────
    elif team == "fire":
        if urgency in ("high", "medium"):
            candidates = find_scdf_stations(lat, lng)
            if not candidates:
                print("  ⚠️  No SCDF stations found via OSM — searching Google Maps...")
                url = f"https://www.google.com/maps/search/SCDF+fire+station/@{lat},{lng},14z"
                if OPEN_IN_BROWSER:
                    webbrowser.open(url)
            else:
                _pick_and_open(candidates, device, "SCDF Station")
        else:
            print("  🏢 LOW — Alerting building management. No map needed.")

    # ── POLICE ────────────────────────────────────────────
    elif team == "police":
        if urgency == "high":
            candidates = _nearest_from_list(lat, lng, SG_POLICE_STATIONS, "Police Station")
            _pick_and_open(candidates, device, "Police Station")
        elif urgency == "medium":
            candidates = _nearest_from_list(lat, lng, SG_NPCS, "Neighbourhood Police Centre")
            _pick_and_open(candidates, device, "Neighbourhood Police Centre")
        else:
            print("  👮 LOW — Flagging to community safety officer. No map needed.")

    # ── SOCIAL WORK ───────────────────────────────────────
    elif team == "social_work":
        if urgency == "high":
            candidates = _nearest_from_list(lat, lng, SG_AE_HOSPITALS, "Hospital Social Work")
            _pick_and_open(candidates, device, "Hospital Social Work Dept")
        elif urgency == "medium":
            candidates = _nearest_from_list(lat, lng, SG_PAPCP_CENTRES, "PAP Community Foundation")
            _pick_and_open(candidates, device, "PAP Community Foundation")
        else:
            candidates = _nearest_from_list(lat, lng, SG_SENIOR_ACTIVITY_CENTRES, "Senior Activity Centre")
            _pick_and_open(candidates, device, "Senior Activity Centre")

    # ── FALLBACK ──────────────────────────────────────────
    else:
        if urgency == "high":
            print("  ⚠️  Unknown team but HIGH urgency — defaulting to A&E Hospital")
            candidates = _nearest_from_list(lat, lng, SG_AE_HOSPITALS, "A&E Hospital")
            _pick_and_open(candidates, device, "A&E Hospital")

    print("=" * 58 + "\n")


# ── Quick test ────────────────────────────────────────────
if __name__ == "__main__":
    trigger_dispatch("manual", team="ambulance", urgency="high")