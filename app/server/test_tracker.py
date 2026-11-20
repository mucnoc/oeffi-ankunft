import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Watch
from tracker import Tracker
from transitous import TransitousError

TZ = ZoneInfo("Europe/Berlin")
WATCH = Watch(stop_id="test_musterdorf", line="42", departure="07:05", target="Beispielstadt, Markt", title="Pendelweg")


def z(day, hhmm):
    """Ortszeit am 2026-11-<day> als UTC-String der API."""
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 11, day, h, m, tzinfo=TZ).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self, day, hhmm):
        self.set(day, hhmm)

    def set(self, day, hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        self.t = datetime(2026, 11, day, h, m, tzinfo=TZ).timestamp()

    def __call__(self):
        return self.t


class FakeTransitous:
    """Antwortet wie Transitous für eine Fahrt der Linie 42 ab Musterdorf 07:05, am Markt 07:40."""

    def __init__(self, clock, arr_expected="07:43", dep_expected="07:07", real_time=True, cancelled=False, exists=True):
        self.clock = clock
        self.arr_expected, self.dep_expected = arr_expected, dep_expected
        self.real_time, self.cancelled, self.exists = real_time, cancelled, exists
        self.fail = 0
        self.calls = []  # (Zeit, "search"|"trip")

    def day(self):
        return datetime.fromtimestamp(self.clock(), TZ).day

    def __call__(self, url):
        kind = "search" if "stoptimes" in url else "trip"
        self.calls.append((self.clock(), kind))
        if self.fail:
            self.fail -= 1
            raise TransitousError("unreachable (timeout)")
        d = self.day()
        if kind == "search":
            times = [{"mode": "BUS", "routeShortName": "42", "tripId": "T%d" % d,
                      "place": {"stopId": "test_musterdorf_steig1", "scheduledDeparture": z(d, "07:05")}}] if self.exists else []
            return {"stopTimes": times}
        return {"legs": [{"mode": "BUS", "routeShortName": "42", "realTime": self.real_time, "cancelled": self.cancelled,
                          "from": {"name": "Musterdorf", "stopId": "test_musterdorf_steig1", "scheduledDeparture": z(d, "07:05"),
                                   "departure": z(d, self.dep_expected)},
                          "intermediateStops": [],
                          "to": {"name": "Beispielstadt, Markt", "stopId": "s", "scheduledArrival": z(d, "07:40"),
                                 "arrival": z(d, self.arr_expected)}}]}

    def count(self, kind):
        return sum(1 for _, k in self.calls if k == kind)


def run(tracker, clock, day, start, end):
    """tick() minütlich von start bis end (einschließlich)."""
    h, m = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    while (h, m) <= (eh, em):
        clock.set(day, "%02d:%02d" % (h, m))
        tracker.tick()
        m += 1
        if m == 60:
            h, m = h + 1, 0


class TrackerTest(unittest.TestCase):
    def make(self, day=20, hhmm="06:00", **kw):
        clock = Clock(day, hhmm)
        api = FakeTransitous(clock, **kw)
        return Tracker([WATCH], api, clock=clock, tz=TZ, log=lambda line: None), api, clock

    def test_no_requests_outside_window_or_weekend(self):
        t, api, clock = self.make(20, "06:00")
        run(t, clock, 20, "06:00", "06:29")
        run(t, clock, 21, "06:30", "08:00")  # Samstag
        self.assertEqual(api.calls, [])
        self.assertEqual(t.snapshot()["trips"], [])

    def test_polls_every_ten_minutes(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "07:30")
        self.assertEqual(api.count("search"), 1)
        self.assertEqual(api.count("trip"), 7)
        times = [c for c, k in api.calls if k == "trip"]
        self.assertTrue(all(b - a >= 600 for a, b in zip(times, times[1:])))

    def test_underway_snapshot(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "07:20")
        trip = t.snapshot()["trips"][0]
        self.assertEqual(trip["state"], "underway")
        self.assertEqual((trip["from"], trip["to"]), ({"name": "Musterdorf", "scheduled": "07:05", "expected": "07:07"},
                                                      {"name": "Beispielstadt, Markt", "scheduled": "07:40", "expected": "07:43"}))
        self.assertEqual((trip["delay"], trip["departDelay"], trip["checked"], trip["title"]), (3, 2, "07:20", "Pendelweg"))
        self.assertEqual((trip["mode"], trip["line"], trip["error"]), ("BUS", "42", None))

    def test_planned_before_departure(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "06:31")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "planned")

    def test_arrived_then_stops(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "07:50")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "arrived")
        self.assertEqual(t.snapshot()["trips"][0]["delay"], 3)
        before = len(api.calls)
        run(t, clock, 20, "07:51", "11:59")
        self.assertEqual(len(api.calls), before)
        self.assertEqual(len(t.snapshot()["trips"]), 1)
        clock.set(20, "12:00")
        t.tick()
        self.assertEqual(t.snapshot()["trips"], [])

    def test_no_realtime_after_schedule(self):
        t, api, clock = self.make(real_time=False, arr_expected="07:40", dep_expected="07:05")
        run(t, clock, 20, "06:30", "07:50")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "no_realtime")
        run(t, clock, 20, "07:51", "08:40")
        n = len(api.calls)
        run(t, clock, 20, "08:41", "11:00")
        self.assertEqual(len(api.calls), n)
        self.assertEqual(t.snapshot()["trips"][0]["state"], "no_realtime")

    def test_cancelled_is_final(self):
        t, api, clock = self.make(cancelled=True)
        run(t, clock, 20, "06:30", "07:30")
        self.assertEqual(api.count("trip"), 1)
        self.assertEqual(t.snapshot()["trips"][0]["state"], "cancelled")

    def test_unreachable_after_three_failures(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "06:30")
        api.fail = 2
        run(t, clock, 20, "06:31", "06:50")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "planned")
        api.fail = 1
        run(t, clock, 20, "06:51", "07:00")
        trip = t.snapshot()["trips"][0]
        self.assertEqual(trip["state"], "unreachable")
        self.assertTrue(trip["error"].startswith("unreachable"))
        self.assertEqual(trip["to"]["expected"], "07:43")  # letzter guter Stand bleibt
        run(t, clock, 20, "07:01", "07:10")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "underway")

    def test_search_failures_show_unreachable(self):
        t, api, clock = self.make()
        api.fail = 3
        run(t, clock, 20, "06:30", "06:50")
        trip = t.snapshot()["trips"][0]
        self.assertEqual((trip["state"], trip["to"]["scheduled"], trip["from"]["name"]), ("unreachable", None, None))

    def test_none_hides_and_gives_up(self):
        t, api, clock = self.make(exists=False)
        run(t, clock, 20, "06:30", "08:20")
        self.assertEqual(t.snapshot()["trips"], [])
        n = api.count("search")
        self.assertLessEqual(n, 11)
        run(t, clock, 20, "08:21", "11:00")
        self.assertEqual(api.count("search"), n)
        self.assertEqual(api.count("trip"), 0)

    def test_new_day_resets(self):
        t, api, clock = self.make()
        run(t, clock, 20, "06:30", "07:50")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "arrived")
        run(t, clock, 23, "06:00", "06:29")  # Montag vor dem Fenster
        self.assertEqual(t.snapshot()["trips"], [])
        run(t, clock, 23, "06:30", "06:31")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "planned")
        self.assertEqual([k for c, k in api.calls if c >= Clock(23, "00:00")()], ["search", "trip"])

    def test_unexpected_error_shows_only_its_type(self):
        clock = Clock(20, "06:30")

        def broken(url):
            raise ValueError("interne Einzelheit /pfad")
        t = Tracker([WATCH], broken, clock=clock, tz=TZ, log=lambda line: None)
        run(t, clock, 20, "06:30", "06:50")
        self.assertEqual(t.snapshot()["trips"][0]["error"], "ValueError")

    def test_tick_survives_unexpected_error(self):
        clock = Clock(20, "06:30")

        def broken(url):
            raise ValueError("kaputt")
        t = Tracker([WATCH], broken, clock=clock, tz=TZ, log=lambda line: None)
        t.tick()  # darf nicht werfen
        self.assertEqual(t.snapshot()["trips"], [])


class GrowingDelayTest(unittest.TestCase):
    def test_arrived_only_after_a_check_past_expected_arrival(self):
        clock = Clock(20, "06:00")
        api = FakeTransitous(clock, arr_expected="07:43")
        t = Tracker([WATCH], api, clock=clock, tz=TZ, log=lambda line: None)
        run(t, clock, 20, "06:30", "07:44")  # letzte Abfrage 07:40 sagte 07:43
        self.assertEqual(t.snapshot()["trips"][0]["state"], "underway")
        api.arr_expected = "07:53"  # der Bus verspätet sich weiter
        run(t, clock, 20, "07:45", "07:55")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "underway")
        run(t, clock, 20, "07:56", "08:00")  # Abfrage 08:00 liegt nach 07:53
        trip = t.snapshot()["trips"][0]
        self.assertEqual((trip["state"], trip["delay"]), ("arrived", 13))


RETURN = Watch(stop_id="test_markt", line="42", departure=None, target="Musterdorf", title="A",
               show_from="11:00", show_until="20:00", departures=("11:50", "12:10", "13:50"),
               earliest_url="http://kalender.test/api/ende?person=a", earliest_gap=10)
ARRIVALS = {"11:50": "12:25", "12:10": "12:45", "13:50": "14:25"}


class FakeReturn:
    """Ende (end) und Rückfahrten der Linie 42 ab dem Markt; Ankunft je 2 Minuten später als geplant."""

    def __init__(self, clock, end="11:30"):
        self.clock, self.end = clock, end
        self.date_shift = 0
        self.fail_end = 0
        self.cancel = set()
        self.skip = False
        self.no_trips = False
        self.fail_trip = False
        self.twin = False  # zweiter Bus derselben Linie und Minute in die andere Richtung, steht zuerst
        self.calls = []

    def now(self):
        return datetime.fromtimestamp(self.clock(), TZ)

    def __call__(self, url):
        d = self.now().day
        if url.startswith("http://kalender.test/"):
            self.calls.append((self.clock(), "end"))
            if self.fail_end:
                self.fail_end -= 1
                raise TransitousError("unreachable (URLError)")
            if self.skip:
                return {"date": "2026-11-%02d" % d, "end": None, "skip": True, "reason": "Sonderfall"}
            return {"date": "2026-11-%02d" % (d + self.date_shift), "end": self.end}
        if "stoptimes" in url:
            self.calls.append((self.clock(), "search"))
            twin = [{"mode": "BUS", "routeShortName": "42", "tripId": "X" + dep,
                     "place": {"stopId": "test_markt", "scheduledDeparture": z(d, dep)}} for dep in ARRIVALS] if self.twin else []
            return {"stopTimes": [] if self.no_trips else twin + [
                {"mode": "BUS", "routeShortName": "42", "tripId": "T" + dep,
                 "place": {"stopId": "test_markt", "scheduledDeparture": z(d, dep)}} for dep in ARRIVALS]}
        self.calls.append((self.clock(), "trip"))
        if self.fail_trip:
            raise TransitousError("target_not_on_trip")
        if "tripId=X" in url:
            dep = url.rsplit("X", 1)[-1].replace("%3A", ":")
            return {"legs": [{"mode": "BUS", "routeShortName": "42", "realTime": True, "cancelled": False,
                              "from": {"name": "Beispielstadt, Markt", "stopId": "test_markt",
                                       "scheduledDeparture": z(d, dep), "departure": z(d, dep)},
                              "intermediateStops": [],
                              "to": {"name": "Anderswo", "stopId": "x", "scheduledArrival": z(d, dep),
                                     "arrival": z(d, dep)}}]}
        dep = url.rsplit("T", 1)[-1].replace("%3A", ":")
        arr = ARRIVALS[dep]
        h, m = (int(x) for x in arr.split(":"))
        return {"legs": [{"mode": "BUS", "routeShortName": "42", "realTime": True, "cancelled": dep in self.cancel,
                          "from": {"name": "Beispielstadt, Markt", "stopId": "test_markt",
                                   "scheduledDeparture": z(d, dep), "departure": z(d, dep)},
                          "intermediateStops": [],
                          "to": {"name": "Musterdorf", "stopId": "m", "scheduledArrival": z(d, arr),
                                 "arrival": z(d, "%02d:%02d" % (h, m + 2))}}]}

    def kinds(self, since=0, until=1e12):
        return [k for t, k in self.calls if since <= t <= until]


class ReturnTripTest(unittest.TestCase):
    def make(self, end="11:30"):
        clock = Clock(20, "10:59")
        api = FakeReturn(clock, end)
        return Tracker([RETURN], api, clock=clock, tz=TZ, log=lambda line: None), api, clock

    def trip(self, t):
        trips = t.snapshot()["trips"]
        return trips[0] if trips else None

    def test_five_lessons_take_the_1245_bus(self):
        t, api, clock = self.make("11:30")
        run(t, clock, 20, "11:00", "11:00")
        trip = self.trip(t)
        self.assertEqual((trip["title"], trip["from"]["scheduled"], trip["to"]["scheduled"], trip["state"]),
                         ("A", "11:50", "12:25", "planned"))
        run(t, clock, 20, "11:01", "12:35")
        self.assertEqual((self.trip(t)["state"], self.trip(t)["delay"]), ("arrived", 2))

    def test_seven_lessons_take_the_1445_bus(self):
        t, api, clock = self.make("13:35")
        run(t, clock, 20, "11:00", "11:00")
        self.assertEqual(self.trip(t)["from"]["scheduled"], "13:50")

    def test_two_buses_same_minute_takes_the_one_to_the_target(self):
        t, api, clock = self.make("13:35")
        api.twin = True
        run(t, clock, 20, "11:00", "11:00")
        trip = self.trip(t)
        self.assertIsNotNone(trip)
        self.assertEqual((trip["from"]["scheduled"], trip["to"]["name"], trip["to"]["scheduled"], trip["error"]),
                         ("13:50", "Musterdorf", "14:25", None))

    def test_gap_skips_a_bus_too_close(self):
        t, api, clock = self.make("11:45")  # 11:50 ist zu knapp, 12:10 passt
        run(t, clock, 20, "11:00", "11:00")
        self.assertEqual(self.trip(t)["from"]["scheduled"], "12:10")

    def test_no_transitous_until_shortly_before_departure(self):
        t, api, clock = self.make("13:35")
        run(t, clock, 20, "11:00", "14:35")
        transitous = [(c, k) for c, k in api.calls if k != "end"]
        self.assertEqual([k for c, k in transitous if c < Clock(20, "13:20")()], ["search", "trip"])
        self.assertEqual(self.trip(t)["state"], "arrived")

    def test_end_changes_before_departure_switches_bus(self):
        t, api, clock = self.make("13:35")
        run(t, clock, 20, "11:00", "11:00")
        api.end = "11:30"  # früheres Ende
        run(t, clock, 20, "11:01", "11:15")
        self.assertEqual(self.trip(t)["from"]["scheduled"], "11:50")

    def test_choice_is_frozen_after_departure(self):
        t, api, clock = self.make("11:30")
        run(t, clock, 20, "11:00", "11:55")
        api.end = "13:35"
        n_end = api.kinds().count("end")
        run(t, clock, 20, "11:56", "12:45")
        self.assertEqual(self.trip(t)["from"]["scheduled"], "11:50")
        self.assertEqual(api.kinds().count("end"), n_end)

    def test_no_school_today_hides_and_asks_transitous_nothing(self):
        t, api, clock = self.make(None)
        run(t, clock, 20, "11:00", "12:05")
        self.assertIsNone(self.trip(t))
        self.assertEqual(set(api.kinds()), {"end"})
        api.end = "11:30"  # Quelle liefert spät doch noch ein Ende: der Bus nach diesem Ende zählt, auch wenn er schon fuhr
        run(t, clock, 20, "12:06", "12:35")
        self.assertEqual((self.trip(t)["from"]["scheduled"], self.trip(t)["state"]), ("11:50", "arrived"))

    def test_end_after_last_bus_or_other_date_hides(self):
        t, api, clock = self.make("14:05")
        run(t, clock, 20, "11:00", "11:35")
        self.assertIsNone(self.trip(t))
        self.assertEqual(set(api.kinds()), {"end"})
        t, api, clock = self.make("11:30")
        api.date_shift = 1
        run(t, clock, 20, "11:00", "11:35")
        self.assertIsNone(self.trip(t))

    def test_end_source_unreachable_counts_as_failure(self):
        t, api, clock = self.make("11:30")
        api.fail_end = 3
        run(t, clock, 20, "11:00", "11:25")
        trip = self.trip(t)
        self.assertEqual((trip["state"], trip["to"]["name"]), ("unreachable", "Musterdorf"))


class ReturnEdgeTest(unittest.TestCase):
    def make(self, end):
        clock = Clock(20, "10:59")
        api = FakeReturn(clock, end)
        return Tracker([RETURN], api, clock=clock, tz=TZ, log=lambda line: None), api, clock

    def test_cancelled_bus_before_departure_still_allows_new_choice(self):
        t, api, clock = self.make("13:35")
        api.cancel = {"13:50"}
        run(t, clock, 20, "11:00", "11:00")
        self.assertEqual(t.snapshot()["trips"][0]["state"], "cancelled")
        api.end = "11:30"  # früher Ende: anderer Bus
        run(t, clock, 20, "11:01", "11:10")
        self.assertEqual(t.snapshot()["trips"][0]["from"]["scheduled"], "11:50")

    def test_short_null_end_keeps_choice(self):
        t, api, clock = self.make("11:30")
        run(t, clock, 20, "11:00", "11:00")
        api.end = None  # Quelle startet neu
        run(t, clock, 20, "11:01", "11:10")
        self.assertEqual(t.snapshot()["trips"][0]["from"]["scheduled"], "11:50")
        self.assertEqual(api.kinds().count("search"), 1)

    def test_invalid_end_is_ignored(self):
        t, api, clock = self.make("25:99")
        run(t, clock, 20, "11:00", "11:30")
        self.assertEqual(t.snapshot()["trips"], [])
        self.assertEqual(set(api.kinds()), {"end"})

    def test_search_without_trip_waits_for_lead(self):
        t, api, clock = self.make("13:35")
        api.no_trips = True
        run(t, clock, 20, "11:00", "13:00")
        self.assertEqual(api.kinds(until=Clock(20, "13:19")()).count("search"), 1)

    def test_persistent_failure_stops_after_give_up(self):
        t, api, clock = self.make("11:30")
        api.fail_trip = True
        run(t, clock, 20, "11:00", "20:00")
        late = [k for c, k in api.calls if k != "end" and c > Clock(20, "13:00")()]
        self.assertEqual(late, [])


RETURN_B = Watch(stop_id="test_markt", line="42", departure=None, target="Musterdorf", title="B",
                 show_from="11:00", show_until="20:00", departures=("11:50", "12:10", "13:50"),
                 earliest_url="http://kalender.test/api/ende?person=b", earliest_gap=10)
RETURN_A = Watch(**dict(RETURN.__dict__, show_from="11:00"))


class Messages:
    def __init__(self, ok=True):
        self.ok = ok
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return self.ok


class CancelNotifyTest(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def make(self, watches, end="11:30", ok=True, clock_at="10:59"):
        clock = Clock(20, clock_at)
        api = FakeReturn(clock, end)
        msgs = Messages(ok)
        t = Tracker(watches, api, clock=clock, tz=TZ, log=lambda line: None, notify=msgs, state_dir=self.dir)
        return t, api, clock, msgs

    def test_morning_trip_cancelled_is_reported_once(self):
        clock = Clock(20, "06:00")
        api = FakeTransitous(clock, cancelled=True)
        msgs = Messages()
        t = Tracker([WATCH], api, clock=clock, tz=TZ, log=lambda line: None, notify=msgs, state_dir=self.dir)
        run(t, clock, 20, "06:30", "07:30")
        self.assertEqual(msgs.texts, ["Pendelweg: Bus 42 ab Musterdorf 07:05 fällt aus."])
        t2 = Tracker([WATCH], api, clock=clock, tz=TZ, log=lambda line: None, notify=msgs, state_dir=self.dir)
        run(t2, clock, 20, "07:31", "07:40")
        self.assertEqual(len(msgs.texts), 1)  # nach Neustart nicht noch einmal

    def test_return_cancelled_switches_to_next_bus(self):
        t, api, clock, msgs = self.make([RETURN_A])
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:00", "11:01")
        self.assertEqual(msgs.texts, ["A: Bus 42 ab Beispielstadt, Markt 11:50 fällt aus, nächster möglicher Bus 12:10."])
        run(t, clock, 20, "11:01", "11:10")
        self.assertEqual(t.snapshot()["trips"][0]["from"]["scheduled"], "12:10")

    def test_last_bus_cancelled_stays_visible(self):
        t, api, clock, msgs = self.make([RETURN_A], end="13:35")
        api.cancel = {"13:50"}
        run(t, clock, 20, "11:00", "11:30")
        self.assertEqual(msgs.texts, ["A: Bus 42 ab Beispielstadt, Markt 13:50 fällt aus, kein weiterer Bus."])
        trip = t.snapshot()["trips"][0]
        self.assertEqual((trip["from"]["scheduled"], trip["state"]), ("13:50", "cancelled"))

    def test_same_trip_for_two_entries_one_message_one_query(self):
        t, api, clock, msgs = self.make([RETURN_A, RETURN_B])
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:00", "11:01")
        self.assertEqual(msgs.texts, ["A + B: Bus 42 ab Beispielstadt, Markt 11:50 fällt aus, nächster möglicher Bus 12:10."])
        self.assertEqual(api.kinds().count("search"), 1)
        self.assertEqual(api.kinds().count("trip"), 1)

    def test_failed_message_is_retried(self):
        t, api, clock, msgs = self.make([RETURN_A], ok=False)
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:00", "11:04")
        self.assertEqual(len(msgs.texts), 1)  # neuer Versuch erst nach 5 Minuten
        msgs.ok = True
        run(t, clock, 20, "11:05", "11:30")
        self.assertEqual(len(msgs.texts), 2)

    def test_cancelled_at_departure_still_follows_next_bus(self):
        t, api, clock, msgs = self.make([RETURN_A])
        run(t, clock, 20, "11:00", "11:49")  # 11:50 erst um 11:50 als Ausfall bekannt
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:50", "12:00")
        self.assertEqual(t.snapshot()["trips"][0]["from"]["scheduled"], "12:10")
        self.assertIn("nächster möglicher Bus 12:10", msgs.texts[0])

    def test_failed_message_expires(self):
        t, api, clock, msgs = self.make([RETURN_A], ok=False)
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:00", "19:59")
        self.assertLessEqual(len(msgs.texts), 6)
        msgs.ok = True
        n = len(msgs.texts)
        run(t, clock, 21, "00:00", "00:30")
        self.assertEqual(len(msgs.texts), n)  # am nächsten Tag nichts Veraltetes

    def test_titles_merged_even_if_second_entry_polls_later(self):
        late_b = Watch(**dict(RETURN_B.__dict__, show_from="11:01"))
        t, api, clock, msgs = self.make([RETURN_A, late_b])
        api.cancel = {"11:50"}
        run(t, clock, 20, "11:00", "11:05")
        self.assertEqual(len(msgs.texts), 1)
        self.assertTrue(msgs.texts[0].startswith("A + B: "), msgs.texts[0])

    def test_skip_drops_choice_and_hides(self):
        t, api, clock, msgs = self.make([RETURN_A])
        run(t, clock, 20, "11:00", "11:00")
        self.assertEqual(len(t.snapshot()["trips"]), 1)
        api.skip = True
        run(t, clock, 20, "11:01", "11:10")
        self.assertEqual(t.snapshot()["trips"], [])
        self.assertEqual(msgs.texts, [])


if __name__ == "__main__":
    unittest.main()
