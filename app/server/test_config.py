import json
import os
import tempfile
import unittest

from config import ConfigError, load_config, load_notify

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config.example.json")


def write(value):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(value, f)
    f.close()
    return f.name


BASE = {"stop_id": "x", "line": "42", "departure": "07:05", "target": "Ziel"}


class ConfigTest(unittest.TestCase):
    def test_example_config_loads(self):
        watches = load_config(EXAMPLE)
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0].line, "42")
        self.assertEqual(watches[0].weekdays, (0, 1, 2, 3, 4))

    def test_defaults(self):
        w = load_config(write({"watches": [BASE]}))[0]
        self.assertEqual((w.show_from, w.show_until, w.title), ("06:30", "12:00", None))

    def test_rejects_bad_time(self):
        for bad in ("6:48", "25:00", "07:60", 705):
            with self.assertRaises(ConfigError, msg=bad):
                load_config(write({"watches": [dict(BASE, departure=bad)]}))

    def test_rejects_missing_field(self):
        entry = dict(BASE)
        del entry["target"]
        with self.assertRaises(ConfigError):
            load_config(write({"watches": [entry]}))

    def test_rejects_not_a_list(self):
        for value in ({"watches": {}}, [], {"watches": ["x"]}):
            with self.assertRaises(ConfigError):
                load_config(write(value))

    def test_rejects_bad_weekdays_and_unreadable(self):
        with self.assertRaises(ConfigError):
            load_config(write({"watches": [dict(BASE, weekdays=[7])]}))
        with self.assertRaises(ConfigError):
            load_config("/gibt/es/nicht.json")


class EarliestTest(unittest.TestCase):
    RETURN = {"stop_id": "x", "line": "42", "departures": ["13:50", "11:50", "12:10"], "target": "Musterdorf",
              "earliest": {"url": "http://kalender.test/api/ende?person=a", "gap": 10}}

    def test_departures_with_earliest(self):
        w = load_config(write({"watches": [self.RETURN]}))[0]
        self.assertEqual((w.departures, w.departure), (("11:50", "12:10", "13:50"), None))
        self.assertEqual((w.earliest_url, w.earliest_gap), ("http://kalender.test/api/ende?person=a", 10))

    def test_single_departure_keeps_working(self):
        w = load_config(write({"watches": [BASE]}))[0]
        self.assertEqual((w.departures, w.departure, w.earliest_url), (("07:05",), "07:05", None))

    def test_error_names_departures(self):
        with self.assertRaisesRegex(ConfigError, "departures"):
            load_config(write({"watches": [dict(self.RETURN, departures=["1:50"])]}))

    def test_gap_default(self):
        entry = dict(self.RETURN, earliest={"url": "http://a/b"})
        self.assertEqual(load_config(write({"watches": [entry]}))[0].earliest_gap, 10)

    def test_rejects_bad_combinations(self):
        bad = [
            dict(self.RETURN, departure="11:50"),            # beides
            {k: v for k, v in self.RETURN.items() if k != "earliest"},  # Liste ohne earliest
            dict(BASE, earliest={"url": "http://a/b"}),       # earliest ohne Liste
            dict(self.RETURN, departures=[]),
            dict(self.RETURN, departures=["12:4"]),
            dict(self.RETURN, earliest={"url": "", "gap": 10}),
            dict(self.RETURN, earliest={"url": "ftp://a", "gap": 10}),
            dict(self.RETURN, earliest={"url": "http://a/b", "gap": -1}),
            dict(self.RETURN, earliest="http://a/b"),
        ]
        for entry in bad:
            with self.assertRaises(ConfigError, msg=entry):
                load_config(write({"watches": [entry]}))


class NotifyConfigTest(unittest.TestCase):
    def test_notify_optional(self):
        self.assertIsNone(load_notify(write({"watches": [BASE]})))

    def test_notify_loaded(self):
        cfg = {"watches": [BASE], "notify": {"url": "http://melder.test/api", "source": "Pendeln"}}
        self.assertEqual(load_notify(write(cfg)), {"url": "http://melder.test/api", "source": "Pendeln"})

    def test_notify_rejects_bad(self):
        for notify in ({"url": "ftp://x", "source": "a"}, {"url": "http://x"}, "http://x", {"url": "http://x", "source": ""}):
            with self.assertRaises(ConfigError, msg=notify):
                load_notify(write({"watches": [BASE], "notify": notify}))


if __name__ == "__main__":
    unittest.main()
