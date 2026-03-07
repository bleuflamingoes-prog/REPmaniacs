"""
location_dispatch.py
--------------------
Fully free, no API key needed.

When audio OR video triggers an emergency:
  1. Reads the hardcoded device location
  2. Searches for the 3 types of places that can dispatch in Singapore:
       - SCDF Fire Stations
       - SCDF Fire Posts
       - Public Hospitals with A&E
  3. Gets ACTUAL driving distance/time for all candidates via OSRM
  4. Picks the one with the shortest driving time (not straight-line)
  5. Opens Google Maps with the route pre-loaded in browser

INSTALL:
    pip install requests

USAGE:
    from location_dispatch import trigger_dispatch
    trigger_dispatch("video")   # or "audio"
"""

import requests
import webbrowser
import math

# ══════════════════════════════════════════════════════════
# ✏️  EDIT THIS — your fixed device location
# ══════════════════════════════════════════════════════════

DEVICE_LOCATION = {
    "name":    "Sungei Gedong Camp",
    "address": "Sungei Gedong Camp, Lim Chu Kang, Singapore",
    "lat":     1.4205,
    "lng":     103.7649,
    "floor":   "",
    "notes":   "Main entrance",
}

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════

SEARCH_RADIUS_M  = 10000  # Search within 10km (wider = more candidates)
MAX_CANDIDATES   = 5      # Top N by straight-line before checking road distance
OPEN_IN_BROWSER  = True   # Auto-open Google Maps route in browser


# ══════════════════════════════════════════════════════════
# KNOWN SINGAPORE A&E HOSPITALS (hardcoded as fallback)
# These are the PUBLIC hospitals with 24hr A&E in Singapore
# that are authorised to dispatch ambulances
# ══════════════════════════════════════════════════════════

SG_AE_HOSPITALS = [
    {"name": "Singapore General Hospital (SGH)",       "lat": 1.27940, "lng": 103.83490},
    {"name": "Changi General Hospital (CGH)",          "lat": 1.34057, "lng": 103.94920},
    {"name": "Tan Tock Seng Hospital (TTSH)",          "lat": 1.32130, "lng": 103.84580},
    {"name": "National University Hospital (NUH)",     "lat": 1.29450, "lng": 103.78280},
    {"name": "Khoo Teck Puat Hospital (KTPH)",         "lat": 1.42430, "lng": 103.83850},
    {"name": "Ng Teng Fong General Hospital (NTFGH)",  "lat": 1.33390, "lng": 103.74630},
    {"name": "Sengkang General Hospital (SKH)",        "lat": 1.39450, "lng": 103.89350},
    {"name": "KK Women's & Children's Hospital (KKH)", "lat": 1.30700, "lng": 103.84560},
]


# ══════════════════════════════════════════════════════════
# STEP 1 — Find nearby SCDF stations via OpenStreetMap
# ══════════════════════════════════════════════════════════

def find_scdf_stations(lat: float, lng: float) -> list[dict]:
    """
    Queries OpenStreetMap (Overpass API) for SCDF fire stations
    and fire posts near the device location.
    Returns list sorted by straight-line distance.
    """
    print("[Dispatch] Searching for nearby SCDF stations and fire posts...")

    query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      way["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      node["amenity"="fire_station"]["operator"="SCDF"](around:{SEARCH_RADIUS_M},{lat},{lng});
      node["emergency"="fire_hydrant"]["operator"="SCDF"](around:{SEARCH_RADIUS_M},{lat},{lng});
    );
    out center {MAX_CANDIDATES * 3};
    """

    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=15
        )
        data = response.json()
        elements = data.get("elements", [])

        stations = []
        seen = set()
        for el in elements:
            s_lat = el.get("lat") or el.get("center", {}).get("lat")
            s_lng = el.get("lon") or el.get("center", {}).get("lon")
            if not s_lat or not s_lng:
                continue

            tags  = el.get("tags", {})
            name  = tags.get("name") or tags.get("operator") or "SCDF Station"
            key   = f"{round(s_lat,4)},{round(s_lng,4)}"
            if key in seen:
                continue
            seen.add(key)

            dist = _haversine_km(lat, lng, s_lat, s_lng)
            stations.append({
                "name":      name,
                "type":      "SCDF Station",
                "lat":       s_lat,
                "lng":       s_lng,
                "dist_km":   round(dist, 2),
            })

        stations.sort(key=lambda x: x["dist_km"])
        print(f"[Dispatch] Found {len(stations)} SCDF station(s) from OpenStreetMap.")
        return stations[:MAX_CANDIDATES]

    except Exception as e:
        print(f"[Dispatch] Overpass API error: {e}")
        return []


def find_nearest_ae_hospitals(lat: float, lng: float) -> list[dict]:
    """
    Returns the closest A&E hospitals from the hardcoded list,
    sorted by straight-line distance.
    """
    hospitals = []
    for h in SG_AE_HOSPITALS:
        dist = _haversine_km(lat, lng, h["lat"], h["lng"])
        hospitals.append({
            "name":    h["name"],
            "type":    "A&E Hospital",
            "lat":     h["lat"],
            "lng":     h["lng"],
            "dist_km": round(dist, 2),
        })
    hospitals.sort(key=lambda x: x["dist_km"])
    return hospitals[:MAX_CANDIDATES]


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Straight-line distance between two coordinates in km."""
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ══════════════════════════════════════════════════════════
# STEP 2 — Get ACTUAL driving time/distance via OSRM (free)
#           This fixes the "canal / bridge" problem —
#           straight-line nearest is NOT always fastest by road
# ══════════════════════════════════════════════════════════

def get_driving_route(origin_lat, origin_lng, dest_lat, dest_lng) -> dict | None:
    """
    Uses OSRM (free, no key) to get real driving distance and time.
    Origin = dispatch location, Dest = device location.
    """
    try:
        url = (
            f"http://router.project-osrm.org/route/v1/driving/"
            f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
            f"?overview=false&steps=false"
        )
        r = requests.get(url, timeout=10)
        data = r.json()

        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            return {
                "duration_min": round(route["duration"] / 60, 1),
                "distance_km":  round(route["distance"] / 1000, 2),
            }
    except Exception as e:
        print(f"[Dispatch] OSRM error for route: {e}")
    return None


# ══════════════════════════════════════════════════════════
# STEP 3 — Build Google Maps URL (no key — just opens browser)
# ══════════════════════════════════════════════════════════

def build_maps_url(station: dict, device: dict) -> str:
    """
    Directions FROM the dispatch location TO the device.
    Opens in browser as a normal Google Maps link — no API key needed.
    """
    return (
        f"https://www.google.com/maps/dir/"
        f"{station['lat']},{station['lng']}/"
        f"{device['lat']},{device['lng']}"
    )


# ══════════════════════════════════════════════════════════
# MAIN — call this from goal3_camera.py or main.py
# ══════════════════════════════════════════════════════════

def trigger_dispatch(source: str) -> None:
    """
    Full dispatch pipeline.

    1. Collects all SCDF stations + A&E hospitals nearby
    2. Gets ACTUAL driving time for every candidate (not straight-line)
    3. Picks the one with shortest driving time
    4. Opens Google Maps with the winning route

    source: "audio", "video", or "manual"
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
    print(f"  Address  : {device['address']}")
    print(f"  Unit     : {device['floor']}")
    print(f"  Notes    : {device['notes']}")
    print("=" * 58)

    # Gather all candidates — SCDF stations + A&E hospitals
    scdf_stations = find_scdf_stations(lat, lng)
    ae_hospitals  = find_nearest_ae_hospitals(lat, lng)
    all_candidates = scdf_stations + ae_hospitals

    if not all_candidates:
        fallback = f"https://www.google.com/maps/search/SCDF+fire+station/@{lat},{lng},14z"
        print(f"\n  ⚠  No candidates found. Opening Maps search...")
        if OPEN_IN_BROWSER:
            webbrowser.open(fallback)
        return

    # Get ACTUAL driving time for every candidate
    print(f"[Dispatch] Checking driving routes for {len(all_candidates)} candidates...")
    routed = []
    for candidate in all_candidates:
        route = get_driving_route(candidate["lat"], candidate["lng"], lat, lng)
        if route:
            candidate["duration_min"] = route["duration_min"]
            candidate["distance_km"]  = route["distance_km"]
            routed.append(candidate)
        else:
            # OSRM failed for this one — use straight-line as fallback
            candidate["duration_min"] = None
            candidate["distance_km"]  = candidate["dist_km"]
            routed.append(candidate)

    # Sort by DRIVING TIME (not straight-line — fixes the canal/bridge problem)
    routed.sort(key=lambda x: (x["duration_min"] is None, x["duration_min"] or x["dist_km"]))
    best = routed[0]
    maps_url = build_maps_url(best, device)

    # Print winning dispatch
    print(f"\n  ✅  FASTEST DISPATCH LOCATION")
    print(f"     Type      : {best['type']}")
    print(f"     Name      : {best['name']}")
    print(f"     Drive Time: {best['duration_min']} min" if best["duration_min"] else "     Drive Time: N/A")
    print(f"     Road Dist : {best['distance_km']} km")
    print(f"     Maps      : {maps_url}")

    # Print all other options
    if len(routed) > 1:
        print(f"\n  📋 OTHER NEARBY OPTIONS")
        for s in routed[1:6]:  # show up to 5 others
            time_str = f"{s['duration_min']} min" if s["duration_min"] else "N/A"
            print(f"     • [{s['type']}] {s['name']} — {time_str} ({s['distance_km']} km)")

    print("\n" + "=" * 58)

    # Open Google Maps in browser
    if OPEN_IN_BROWSER:
        print("  Opening route in Google Maps...")
        webbrowser.open(maps_url)


# ── Quick test: python location_dispatch.py ───────────────
if __name__ == "__main__":
    trigger_dispatch("manual")