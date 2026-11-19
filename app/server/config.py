"""Konfiguration: welche Fahrten beobachtet werden.

Datei (JSON):
    {"watches": [{"title": "Pendelweg", "stop_id": "<Transitous-Haltestellen-ID>", "line": "42",
                  "departure": "07:05", "target": "Beispielstadt, Markt",
                  "weekdays": [0, 1, 2, 3, 4], "show_from": "06:30", "show_until": "12:00"}]}

stop_id ist die Abfahrtshaltestelle, target der Name der Zielhaltestelle wie bei Transitous, departure die geplante
Abfahrt (Ortszeit). weekdays 0 = Montag … 6 = Sonntag. title, weekdays, show_from und show_until sind optional.

Statt departure geht auch eine Liste departures mit earliest {"url": "...", "gap": 10}: Die Adresse liefert
{"date": "JJJJ-MM-TT", "end": "HH:MM"|null} (etwa Arbeitsende), beobachtet wird die erste Abfahrt mindestens gap Minuten
nach end (tracker.py).

Optional auf oberster Ebene: "notify": {"url": "http://…", "source": "Name"}. Fällt eine Fahrt aus, geht
{"text", "source"} per POST an diese Adresse (load_notify, server.make_notifier).
"""
import json
import re
from dataclasses import dataclass

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Watch:
    stop_id: str
    line: str
    departure: str  # feste Abfahrt; None, wenn departures mit earliest gilt
    target: str
    title: str = None
    weekdays: tuple = (0, 1, 2, 3, 4)
    show_from: str = "06:30"
    show_until: str = "12:00"
    departures: tuple = ()  # mögliche Abfahrten, sortiert; bei fester Abfahrt (departure,)
    earliest_url: str = None
    earliest_gap: int = 10


def _text(entry, key, required=True):
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s fehlt oder ist kein Text" % key)
    return value.strip()


def _time(entry, key, default=None):
    value = entry.get(key, default)
    if not isinstance(value, str) or not _TIME.match(value):
        raise ConfigError("%s muss HH:MM sein" % key)
    return value


def _departures(entry):
    """(departure, departures, earliest_url, earliest_gap): entweder departure oder departures mit earliest."""
    if ("departure" in entry) == ("departures" in entry):
        raise ConfigError("entweder departure oder departures angeben")
    if "departure" in entry:
        if "earliest" in entry:
            raise ConfigError("earliest nur mit departures")
        departure = _time(entry, "departure")
        return departure, (departure,), None, 10
    values = entry["departures"]
    if not isinstance(values, list) or not values:
        raise ConfigError("departures muss eine nicht leere Liste sein")
    departures = tuple(sorted({_time({"departures": v}, "departures") for v in values}))
    earliest = entry.get("earliest")
    if not isinstance(earliest, dict):
        raise ConfigError("departures braucht earliest {\"url\", \"gap\"}")
    url = earliest.get("url")
    if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
        raise ConfigError("earliest.url muss eine http(s)-Adresse sein")
    gap = earliest.get("gap", 10)
    if not isinstance(gap, int) or isinstance(gap, bool) or not 0 <= gap <= 240:
        raise ConfigError("earliest.gap muss 0 bis 240 Minuten sein")
    return None, departures, url, gap


def _watch(entry):
    if not isinstance(entry, dict):
        raise ConfigError("Eintrag ist kein Objekt")
    weekdays = entry.get("weekdays", [0, 1, 2, 3, 4])
    if (not isinstance(weekdays, list) or not weekdays
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in weekdays)):
        raise ConfigError("weekdays muss eine Liste aus 0 bis 6 sein")
    departure, departures, url, gap = _departures(entry)
    watch = Watch(stop_id=_text(entry, "stop_id"), line=_text(entry, "line"), departure=departure,
                  target=_text(entry, "target"), title=_text(entry, "title", required=False),
                  weekdays=tuple(sorted(set(weekdays))), show_from=_time(entry, "show_from", "06:30"),
                  show_until=_time(entry, "show_until", "12:00"), departures=departures, earliest_url=url,
                  earliest_gap=gap)
    if watch.show_from >= watch.show_until:
        raise ConfigError("show_from muss vor show_until liegen")
    return watch


def load_config(path):
    """Liste der Watch-Einträge; ConfigError bei fehlender oder fehlerhafter Datei."""
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError("nicht lesbar (%s)" % type(e).__name__) from e
    if not isinstance(value, dict) or not isinstance(value.get("watches"), list):
        raise ConfigError("erwartet {\"watches\": [...]}")
    return [_watch(entry) for entry in value["watches"]]


def load_notify(path):
    """{"url", "source"} oder None; ConfigError bei fehlerhaftem Eintrag."""
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError("nicht lesbar (%s)" % type(e).__name__) from e
    notify = value.get("notify") if isinstance(value, dict) else None
    if notify is None:
        return None
    if not isinstance(notify, dict):
        raise ConfigError("notify muss ein Objekt sein")
    url = notify.get("url")
    if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
        raise ConfigError("notify.url muss eine http(s)-Adresse sein")
    return {"url": url, "source": _text(notify, "source")}
