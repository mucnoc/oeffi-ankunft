"""Abfragen bei Transitous (MOTIS 2, https://transitous.org/api/).

Transitous wird von Freiwilligen betrieben. Bedingungen: offener Code, nicht kommerziell, wenig Last, User-Agent mit
Kontakt, sichtbarer Link auf https://transitous.org/sources/. Dieser Dienst nutzt nur v5/stoptimes und v5/trip und
fragt höchstens alle 10 Minuten je Fahrt (tracker.py).
"""
import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from urllib.parse import urlencode

BASE = "https://api.transitous.org/api/"
USER_AGENT = "oeffi-ankunft/0.1 (+https://github.com/mucnoc/oeffi-ankunft)"
TIMEOUT = 20
SEARCH_BEFORE = timedelta(minutes=15)


class TransitousError(Exception):
    pass


def http_get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            body = answer.read()
    except urllib.error.HTTPError as e:
        raise TransitousError("status %d" % e.code) from e
    except (OSError, HTTPException, socket.timeout) as e:
        raise TransitousError("unreachable (%s)" % type(e).__name__) from e
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TransitousError("bad_json") from e


def _time(value):
    """ISO-Zeit der API (UTC, „…Z“) als aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_trip(get, watch, day, tz, departure=None):
    """Fahrt mit Linie watch.line und geplanter Abfahrt departure (sonst watch.departure; Ortszeit, am Tag day) an watch.stop_id:
    {"tripId", "mode", "line", "stopId" (Haltestelle/Steig der Abfahrt)} oder None."""
    hour, minute = (int(x) for x in (departure or watch.departure).split(":"))
    planned = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    url = BASE + "v5/stoptimes?" + urlencode({"stopId": watch.stop_id, "time": _utc(planned - SEARCH_BEFORE), "n": 20})
    data = get(url)
    stop_times = data.get("stopTimes") if isinstance(data, dict) else None
    if not isinstance(stop_times, list) or not all(isinstance(st, dict) for st in stop_times):
        raise TransitousError("bad_payload (stopTimes)")
    for st in stop_times:
        line = st.get("routeShortName") or st.get("displayName")
        if line != watch.line:
            continue
        try:
            place = st["place"]
            if _time(place["scheduledDeparture"]) == planned:
                # Die Abfahrt steht oft an einem Steig mit eigener ID unter der konfigurierten Station
                return {"tripId": st["tripId"], "mode": st.get("mode"), "line": line,
                        "stopId": place.get("stopId") or watch.stop_id}
        except (KeyError, TypeError, AttributeError, ValueError):
            continue  # unvollständiger Eintrag: die anderen zählen trotzdem
    return None


def _stop(place, scheduled_key, key):
    return {"name": place["name"], "scheduled": _time(place[scheduled_key]), "expected": _time(place.get(key) or place[scheduled_key])}


def trip_status(get, trip_id, stop_id, target, tz):
    """Abfahrt an stop_id und Ankunft an der ersten Haltestelle namens target danach.
    {"mode", "line", "from": {"name", "scheduled", "expected"}, "to": {…}, "realTime", "cancelled"}"""
    data = get(BASE + "v5/trip?" + urlencode({"tripId": trip_id}))
    try:
        leg = data["legs"][0]
        stops = [leg["from"]] + list(leg.get("intermediateStops") or []) + [leg["to"]]
        origin = next((i for i, s in enumerate(stops) if s.get("stopId") == stop_id), None)
        if origin is None:
            raise TransitousError("origin_not_on_trip")
        dest = next((i for i in range(origin + 1, len(stops)) if stops[i].get("name") == target), None)
        if dest is None:
            raise TransitousError("target_not_on_trip")
        o, t = stops[origin], stops[dest]
        return {
            "mode": leg.get("mode"),
            "line": leg.get("routeShortName") or leg.get("displayName"),
            "from": _stop(o, "scheduledDeparture", "departure"),
            "to": _stop(t, "scheduledArrival", "arrival"),
            "realTime": bool(leg.get("realTime")),
            "cancelled": bool(leg.get("cancelled") or leg.get("tripCancelled") or o.get("cancelled") or t.get("cancelled")),
        }
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        raise TransitousError("bad_payload (%s)" % type(e).__name__) from e
