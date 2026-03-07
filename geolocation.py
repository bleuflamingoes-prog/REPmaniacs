"""
location_dispatch.py
--------------------
Fully free, no API key needed.

When audio OR video triggers an emergency:
  1. Reads the hardcoded device location
  2. Queries OpenStreetMap (Overpass API) for nearest SCDF / ambulance stations
  3. Gets shortest driving route via OSRM (free routing engine)
  4. Prints full dispatch info + opens Google Maps route in browser

INSTALL:
    pip install requests

USAGE (from goal3_camera.py or main.py):
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
    "name":    "Home",
    "address": "123 Bedok North Ave 1, #04-56, Singapore 460123",
    "lat":     1.32450,    # Right-click your address on maps.google.com → copy lat
    "lng":     103.92870,  # and paste here
    "floor":   "Level 4, Unit 56",
    "notes":   "Take lift to Level 4, turn left",
}

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════

SEARCH_RADIUS_M  = 5000   # Search for stations within 5km
MAX_RESULTS      = 5      # How many nearby stations to find
OPEN_IN_BROWSER  = True   # Auto-open Google Maps route in browser


# ══════════════════════════════════════════════════════════
# STEP 1 — Find nearest SCDF / ambulance stations
#           Uses Overpass API (free OpenStreetMap data)
# ══════════════════════════════════════════════════════════

def find_nearest_stations(lat: float, lng: float) -> list[dict]:
    """
    Queries OpenStreetMap for nearby emergency stations.
    Returns list of stations sorted by straight-line distance.
    """
    print("[Dispatch] Searching for nearest SCDF / ambulance stations...")

    # Overpass query — finds fire stations, ambulance stations, SCDF posts
    query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      way["amenity"="fire_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      node["emergency"="ambulance_station"](around:{SEARCH_RADIUS_M},{lat},{lng});
      node["operator"="SCDF"](around:{SEARCH_RADIUS_M},{lat},{lng});
    );
    out center {MAX_RESULTS};
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
        for el in elements:
            # Get coordinates (nodes have lat/lon directly, ways use center)
            s_lat = el.get("lat") or el.get("center", {}).get("lat")
            s_lng = el.get("lon") or el.get("center", {}).get("lon")
            if not s_lat or not s_lng:
                continue

            tags = el.get("tags", {})
            name = (
                tags.get("name") or
                tags.get("operator") or
                tags.get("amenity", "").replace("_", " ").title()
            )

            dist = _haversine_km(lat, lng, s_lat, s_lng)
            stations.append({
                "name":     name,
                "lat":      s_lat,
                "lng":      s_lng,
                "dist_km":  round(dist, 2),
                "tags":     tags,
            })

        # Sort by straight-line distance
        stations.sort(key=lambda x: x["dist_km"])

        if stations:
            print(f"[Dispatch] Found {len(stations)} station(s) nearby.")
        else:
            print("[Dispatch] No stations found in OpenStreetMap within radius.")

        return stations

    except Exception as e:
        print(f"[Dispatch] Overpass API error: {e}")
        return []


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
# STEP 2 — Get shortest driving route via OSRM (free)
# ══════════════════════════════════════════════════════════

def get_route(origin_lat, origin_lng, dest_lat, dest_lng) -> dict | None:
    """
    Uses OSRM public API to get shortest driving route.
    Returns duration (minutes), distance (km), and route summary.
    """
    try:
        # OSRM format: /route/v1/driving/lng,lat;lng,lat
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
        print(f"[Dispatch] OSRM routing error: {e}")
    return None


# ══════════════════════════════════════════════════════════
# STEP 3 — Build Google Maps URL (no key needed — just opens browser)
# ══════════════════════════════════════════════════════════

def build_maps_url(station: dict, device: dict) -> str:
    """
    Builds a Google Maps directions URL from the station to the device.
    Opens in browser — no API key required, just a regular Maps link.

    Format: maps.google.com/dir/STATION_LAT,LNG/DEVICE_LAT,LNG
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
    Full dispatch pipeline. Call this when alert fires.

    source: "audio", "video", or "manual"
    """
    device = DEVICE_LOCATION
    lat, lng = device["lat"], device["lng"]

    source_label = {
        "audio":  "AUDIO — mic/speech trigger",
        "video":  "VIDEO — pose/fall detection",
        "manual": "MANUAL — button pressed",
    }.get(source, source.upper())

    print("\n" + "=" * 58)
    print("  🚨  EMERGENCY DISPATCH TRIGGERED")
    print(f"  Source   : {source_label}")
    print(f"  Location : {device['address']}")
    print(f"  Unit     : {device['floor']}")
    print(f"  Notes    : {device['notes']}")
    print("=" * 58)

    # Find nearest stations
    stations = find_nearest_stations(lat, lng)

    if not stations:
        # No stations found — still give a useful Maps link
        fallback_url = f"https://www.google.com/maps/search/SCDF+fire+station/@{lat},{lng},14z"
        print(f"\n  ⚠  Could not find stations automatically.")
        print(f"  Opening nearest SCDF search on Google Maps:")
        print(f"  {fallback_url}")
        if OPEN_IN_BROWSER:
            webbrowser.open(fallback_url)
        return

    # Get routing info for the closest station
    nearest = stations[0]
    route = get_route(nearest["lat"], nearest["lng"], lat, lng)
    maps_url = build_maps_url(nearest, device)

    # Print dispatch summary
    print(f"\n  📍 NEAREST STATION")
    print(f"     Name     : {nearest['name']}")
    print(f"     Distance : {nearest['dist_km']} km (straight line)")
    if route:
        print(f"     Drive    : {route['duration_min']} min ({route['distance_km']} km by road)")
    print(f"     Maps     : {maps_url}")

    # Print all other nearby stations too
    if len(stations) > 1:
        print(f"\n  📋 OTHER NEARBY STATIONS")
        for s in stations[1:]:
            print(f"     • {s['name']} — {s['dist_km']} km away")

    print("\n" + "=" * 58)

    # Open Google Maps route in browser automatically
    if OPEN_IN_BROWSER:
        print("  Opening route in Google Maps...")
        webbrowser.open(maps_url)


# ── Quick test: python location_dispatch.py ───────────────
if __name__ == "__main__":
    trigger_dispatch("manual")