import copy
import json
import os
import unittest
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from config import Watch
from transitous import TransitousError, find_trip, trip_status

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "test", "fixtures")
TZ = ZoneInfo("Europe/Berlin")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FakeGet:
    def __init__(self, value):
        self.value = value
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


WATCH = Watch(stop_id="test_musterdorf", line="42", departure="07:05", target="Beispielstadt, Markt")


class FindTripTest(unittest.TestCase):
    def test_find_trip_matches_line_and_minute(self):
        get = FakeGet(fixture("stoptimes.json"))
        trip = find_trip(get, WATCH, date(2026, 11, 20), TZ)
        self.assertEqual(trip, {"tripId": "T42-0705", "mode": "BUS", "line": "42", "stopId": "test_musterdorf"})
        query = parse_qs(urlsplit(get.urls[0]).query)
        self.assertEqual(query["stopId"], ["test_musterdorf"])
        self.assertEqual(query["time"], ["2026-11-20T05:50:00Z"])
        self.assertTrue(get.urls[0].startswith("https://api.transitous.org/api/v5/stoptimes?"))

    def test_find_trip_with_other_departure(self):
        watch = Watch(stop_id="test_musterdorf", line="42", departure=None, target="x")
        trip = find_trip(FakeGet(fixture("stoptimes.json")), watch, date(2026, 11, 20), TZ, departure="07:35")
        self.assertEqual(trip["tripId"], "T42-0735")

    def test_find_trip_none_when_absent(self):
        watch = Watch(stop_id="test_musterdorf", line="42", departure="08:00", target="x")
        self.assertIsNone(find_trip(FakeGet(fixture("stoptimes.json")), watch, date(2026, 11, 20), TZ))

    def test_find_trip_other_day_is_none(self):
        self.assertIsNone(find_trip(FakeGet(fixture("stoptimes.json")), WATCH, date(2026, 11, 21), TZ))

    def test_skips_incomplete_entries(self):
        data = fixture("stoptimes.json")
        data["stopTimes"].insert(0, {"routeShortName": "42", "tripId": "kaputt", "place": {"name": "Musterdorf"}})
        data["stopTimes"].insert(0, {"routeShortName": "42"})
        trip = find_trip(FakeGet(data), WATCH, date(2026, 11, 20), TZ)
        self.assertEqual(trip["tripId"], "T42-0705")

    def test_remembers_platform_stop_id(self):
        data = fixture("stoptimes.json")
        data["stopTimes"][1]["place"]["stopId"] = "test_musterdorf_steig2"
        trip = find_trip(FakeGet(data), WATCH, date(2026, 11, 20), TZ)
        self.assertEqual(trip["stopId"], "test_musterdorf_steig2")

    def test_bad_payload(self):
        for payload in ({}, [], {"stopTimes": "x"}, {"stopTimes": ["x"]}):
            with self.assertRaises(TransitousError, msg=payload):
                find_trip(FakeGet(payload), WATCH, date(2026, 11, 20), TZ)


class TripStatusTest(unittest.TestCase):
    def test_trip_status_delay(self):
        get = FakeGet(fixture("trip.json"))
        s = trip_status(get, "T42-0705", "test_musterdorf", "Beispielstadt, Markt", TZ)
        self.assertEqual(s["to"]["expected"] - s["to"]["scheduled"], timedelta(minutes=3))
        self.assertEqual(s["from"]["expected"] - s["from"]["scheduled"], timedelta(minutes=2))
        self.assertEqual((s["from"]["name"], s["to"]["name"]), ("Musterdorf", "Beispielstadt, Markt"))
        self.assertEqual(s["to"]["scheduled"].astimezone(TZ).strftime("%H:%M"), "07:40")
        self.assertEqual((s["mode"], s["line"], s["realTime"], s["cancelled"]), ("BUS", "42", True, False))
        self.assertIn("tripId=T42-0705", get.urls[0])

    def test_trip_status_target_missing(self):
        with self.assertRaisesRegex(TransitousError, "target_not_on_trip"):
            trip_status(FakeGet(fixture("trip.json")), "T", "test_musterdorf", "Gibtsnicht", TZ)

    def test_target_before_origin_does_not_count(self):
        with self.assertRaisesRegex(TransitousError, "target_not_on_trip"):
            trip_status(FakeGet(fixture("trip.json")), "T", "test_musterdorf", "Anfang", TZ)

    def test_target_as_last_stop(self):
        s = trip_status(FakeGet(fixture("trip.json")), "T", "test_musterdorf", "Beispielstadt, Bahnhof", TZ)
        self.assertEqual(s["to"]["expected"] - s["to"]["scheduled"], timedelta(minutes=3))

    def test_trip_status_cancelled(self):
        data = fixture("trip.json")
        data["legs"][0]["intermediateStops"][1]["cancelled"] = True
        s = trip_status(FakeGet(data), "T", "test_musterdorf", "Beispielstadt, Markt", TZ)
        self.assertTrue(s["cancelled"])
        data = fixture("trip.json")
        data["legs"][0]["cancelled"] = True
        self.assertTrue(trip_status(FakeGet(data), "T", "test_musterdorf", "Beispielstadt, Markt", TZ)["cancelled"])

    def test_bad_payload(self):
        broken = fixture("trip.json")
        del broken["legs"][0]["intermediateStops"][1]["scheduledArrival"]
        for payload in ({}, {"legs": []}, {"legs": [{}]}, broken):
            with self.assertRaises(TransitousError):
                trip_status(FakeGet(payload), "T", "test_musterdorf", "Beispielstadt, Markt", TZ)

    def test_network_error_passes_through(self):
        with self.assertRaises(TransitousError):
            trip_status(FakeGet(TransitousError("unreachable")), "T", "a", "b", TZ)


if __name__ == "__main__":
    unittest.main()
