"""Takt: Für jede beobachtete Fahrt (config.Watch) am passenden Wochentag im Zeitfenster bei Transitous nachsehen.

- Ab show_from die Fahrt suchen (find_trip), danach ihren Verlauf abfragen (trip_status), je Fahrt höchstens alle
  POLL_SECONDS. Fertig (keine Abfragen mehr bis zum nächsten Tag), wenn die Fahrt ausfällt, mit Echtzeit die erwartete
  Ankunft DONE_AFTER_ARRIVAL vorbei ist oder die geplante Ankunft GIVE_UP_AFTER vorbei ist.
- Keine Fahrt gefunden (Wochenende, Feiertag): weiter suchen bis Abfahrt + GIVE_UP_AFTER, dann für heute „none“ (unsichtbar).
- Fehler zählen; ab FAILS_UNTIL_UNREACHABLE in Folge „unreachable“, der letzte gute Stand bleibt. Ein Erfolg setzt zurück.
- Der Stand lebt nur im Speicher; nach einem Neustart wird neu gesucht. Um Mitternacht beginnt jede Fahrt neu.
- Nach der ersten Abfrage fragt Transitous erst wieder ab LEAD_SECONDS vor der Abfahrt.
- Einträge mit departures und earliest (config.py): Vor jeder Runde, bis der gewählte Bus abgefahren ist, die Adresse
  lesen ({"date", "end"}) und die erste Abfahrt mindestens earliest_gap Minuten nach end wählen. Ändert sich die Wahl,
  beginnt die Fahrt neu. Kein end, falsches Datum oder keine passende Abfahrt: nichts zu zeigen, Transitous wird nicht
  gefragt. Fehler der Adresse zählen wie Fehler von Transitous. Liefert sie "skip": true (etwa ein Sonderfall), wird heute
  nichts beobachtet.
- Fällt eine Fahrt aus, geht einmal je Fahrt und Tag eine Nachricht an notify(text) -> bool (False: nach RETRY_SECONDS
  neu versuchen); was gemeldet ist, steht in state_dir/gemeldet.json. Haben mehrere Einträge dieselbe Fahrt, eine
  Nachricht mit allen Titeln. Bei Einträgen mit earliest wird vor der Abfahrt die nächste mögliche Abfahrt beobachtet.
- Gleiche Anfragen an Transitous teilen sich ein Ergebnis für CACHE_SECONDS (beide Einträge, derselbe Bus).
"""
import re
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from transitous import TransitousError, find_trip, trip_status

POLL_SECONDS = 600
DONE_AFTER_ARRIVAL = 120
GIVE_UP_AFTER = 3600
FAILS_UNTIL_UNREACHABLE = 3
TICK_SECONDS = 60
LEAD_SECONDS = 1800
CACHE_SECONDS = 300
RETRY_SECONDS = 300
SEND_DELAY = 60  # eine Runde warten, damit alle Einträge derselben Fahrt in die Nachricht kommen
MAX_TRIES = 6
STATE_FILE = "gemeldet.json"
MODES = {"BUS": "Bus", "COACH": "Bus", "TRAM": "Tram", "SUBWAY": "U-Bahn", "SUBURBAN": "S-Bahn", "FERRY": "Fähre",
         "REGIONAL_RAIL": "Zug", "REGIONAL_FAST_RAIL": "Zug", "HIGHSPEED_RAIL": "Zug", "LONG_DISTANCE": "Zug",
         "NIGHT_RAIL": "Zug", "RAIL": "Zug"}


def log_stderr(line):
    print(line, file=sys.stderr, flush=True)


class _Day:
    """Was an einem Tag zu einer Fahrt bekannt ist."""

    def __init__(self, day):
        self.day = day
        self.chosen = None  # beobachtete Abfahrt "HH:MM"
        self.searched = False  # für chosen schon einmal gesucht
        self.earliest = None  # früheste Abfahrt laut Ende (datetime), nur bei earliest
        self.cancelled = set()  # vor der Abfahrt ausgefallene Abfahrten "HH:MM"
        self.trip = None  # {"tripId", "mode", "line", "stopId"}
        self.status = None  # letzte Antwort von trip_status
        self.none = False  # heute keine passende Fahrt
        self.done = False
        self.fails = 0
        self.error = None
        self.last_query = None
        self.checked = None  # datetime der letzten erfolgreichen Abfrage


class Tracker:
    def __init__(self, watches, get, clock=time.time, tz=ZoneInfo("Europe/Berlin"), log=log_stderr, notify=None,
                 state_dir=None):
        self.watches = list(watches)
        self._fetch = get
        self._clock = clock
        self._tz = tz
        self._log = log
        self._notify = notify
        self._state_dir = state_dir
        self._lock = threading.Lock()
        self._days = [None] * len(self.watches)
        self._cache = {}  # url -> (Zeitpunkt, Antwort)
        self._pending = {}  # Schlüssel -> {"titles", "text", "tried"}
        self._sent = None  # gemeldete Schlüssel "JJJJ-MM-TT|tripId", beim ersten Bedarf aus der Datei

    def _get(self, url):
        """Transitous-Antworten kurz teilen; die Adressen für earliest immer frisch."""
        if any(url == w.earliest_url for w in self.watches):
            return self._fetch(url)
        now = self._clock()
        hit = self._cache.get(url)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        data = self._fetch(url)
        self._cache = {u: v for u, v in self._cache.items() if now - v[0] < CACHE_SECONDS}
        self._cache[url] = (now, data)
        return data

    # ---- Meldungen ----

    def _path(self):
        return os.path.join(self._state_dir, STATE_FILE) if self._state_dir else None

    def _sent_keys(self, today):
        if self._sent is None:
            self._sent = set()
            try:
                with open(self._path(), encoding="utf-8") as f:
                    value = json.load(f)
                if isinstance(value, list):
                    self._sent = {k for k in value if isinstance(k, str)}
            except (OSError, ValueError, TypeError):
                pass
        self._sent = {k for k in self._sent if k.startswith(today)}
        return self._sent

    def _remember(self, key):
        self._sent.add(key)
        path = self._path()
        if not path:
            return
        try:
            with open(path + ".neu", "w", encoding="utf-8") as f:
                json.dump(sorted(self._sent), f)
            os.replace(path + ".neu", path)
        except OSError as e:
            self._log("%s nicht gespeichert (%s)" % (STATE_FILE, type(e).__name__))

    def _report_cancel(self, watch, d, status, now):
        if self._notify is None:
            return
        key = "%s|%s" % (now.date().isoformat(), d.trip["tripId"])
        if key in self._sent_keys(now.date().isoformat()):
            return
        entry = self._pending.get(key)
        if entry is not None:
            if watch.title and watch.title not in entry["titles"]:
                entry["titles"].append(watch.title)
            return
        place = status["from"]
        text = "%s %s ab %s %s fällt aus" % (MODES.get(status["mode"], "Linie"), status["line"] or watch.line, place["name"],
                                             self._clock_text(place["scheduled"]))
        if watch.earliest_url is not None:
            nxt = self._next_departure(watch, d, now)
            text += ", nächster möglicher Bus %s." % nxt if nxt else ", kein weiterer Bus."
        else:
            text += "."
        self._pending[key] = {"titles": [watch.title] if watch.title else [], "text": text, "tried": None,
                              "created": now, "tries": 0}

    def _send_pending(self, now):
        today = now.date().isoformat()
        for key, entry in list(self._pending.items()):
            if not key.startswith(today) or entry["tries"] >= MAX_TRIES:
                del self._pending[key]  # veraltet oder zu oft gescheitert: nicht mehr schicken
                self._log("Ausfall-Meldung aufgegeben")
                continue
            if (now - entry["created"]).total_seconds() < SEND_DELAY:
                continue
            if entry["tried"] is not None and (now - entry["tried"]).total_seconds() < RETRY_SECONDS:
                continue
            entry["tried"] = now
            entry["tries"] += 1
            text = (" + ".join(entry["titles"]) + ": " if entry["titles"] else "") + entry["text"]
            try:
                done = self._notify(text)
            except Exception:
                traceback.print_exc()
                done = False
            if done:
                del self._pending[key]
                self._remember(key)
            else:
                self._log("Ausfall-Meldung nicht zugestellt, neuer Versuch später")

    def _next_departure(self, watch, d, now):
        return next((t for t in watch.departures if t != d.chosen and t not in d.cancelled
                     and d.earliest is not None and self._local(now, t) >= d.earliest
                     and self._local(now, t) > self._local(now, d.chosen)), None)

    def _now(self):
        return datetime.fromtimestamp(self._clock(), self._tz)

    def _local(self, now, hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    def _active(self, watch, now):
        return now.weekday() in watch.weekdays and watch.show_from <= now.strftime("%H:%M") < watch.show_until

    def _state(self, i, now):
        d = self._days[i]
        if d is None or d.day != now.date():
            d = self._days[i] = _Day(now.date())
        return d

    # ---- Abfragen ----

    def tick(self):
        now = self._now()
        for i, watch in enumerate(self.watches):
            with self._lock:
                d = self._state(i, now)
            # last_query, done, cancelled, earliest, searched schreibt nur dieser Faden, snapshot() liest sie nicht
            if not self._active(watch, now) or d.done:
                continue
            if d.last_query is not None and (now - d.last_query).total_seconds() < POLL_SECONDS:
                continue
            if (d.fails >= FAILS_UNTIL_UNREACHABLE and d.chosen is not None
                    and now >= self._local(now, d.chosen) + timedelta(seconds=GIVE_UP_AFTER)):
                d.done = True  # Dauerfehler lange nach der Abfahrt: aufhören statt bis Fensterende zu fragen
                continue
            d.last_query = now
            try:
                self._query(watch, d, now)
            except Exception as e:
                if not isinstance(e, TransitousError):
                    traceback.print_exc()
                with self._lock:
                    d.fails += 1
                    # Unerwartetes nur mit seinem Typ: der Text käme sonst auf die freie Seite
                    d.error = (str(e) or type(e).__name__) if isinstance(e, TransitousError) else type(e).__name__
                self._log("Abfrage für %s gescheitert (%s)" % (watch.line, d.error))
        if self._pending:
            self._send_pending(now)

    def _choose(self, watch, d, now):
        """Abfahrt, die heute beobachtet wird, oder None."""
        if watch.earliest_url is None:
            return watch.departure
        if d.chosen is not None and now >= self._local(now, d.chosen) and d.chosen not in d.cancelled:
            return d.chosen  # abgefahren: die Wahl steht fest
        data = self._get(watch.earliest_url)
        today = isinstance(data, dict) and data.get("date") == now.date().isoformat()
        if today and data.get("skip") is True:
            return None  # Sonderfall: heute nichts beobachten, auch eine frühere Wahl nicht
        end = data.get("end") if today else None
        if not isinstance(end, str) or not re.match(r"^\d\d:\d\d$", end):
            return d.chosen  # kein Ende bekannt (etwa Neustart der Quelle): eine getroffene Wahl bleibt
        try:
            earliest = self._local(now, end) + timedelta(minutes=watch.earliest_gap)
        except ValueError:
            return d.chosen
        d.earliest = earliest
        candidates = [t for t in watch.departures if self._local(now, t) >= earliest and t not in d.cancelled]
        if not candidates and d.chosen in d.cancelled:
            return d.chosen  # auch der letzte Bus fällt aus: der Ausfall bleibt sichtbar
        return candidates[0] if candidates else None

    def _query(self, watch, d, now):
        chosen = self._choose(watch, d, now)
        if chosen != d.chosen:
            with self._lock:
                d.chosen, d.trip, d.status, d.searched = chosen, None, None, False
        if chosen is None:
            with self._lock:
                d.fails, d.error = 0, None
            return
        departure = self._local(now, chosen)
        if d.status is not None and now < departure - timedelta(seconds=LEAD_SECONDS):
            with self._lock:
                d.fails, d.error = 0, None
            return  # bis kurz vor der Abfahrt ändert sich bei Transitous wenig
        if d.trip is None:
            if d.searched and now < departure - timedelta(seconds=LEAD_SECONDS):
                return  # schon einmal ohne Treffer gesucht: erst kurz vor der Abfahrt wieder
            d.searched = True
            trip = find_trip(self._get, watch, now.date(), self._tz, departure=chosen)
            if trip is None:
                with self._lock:
                    d.fails, d.error = 0, None
                    if now >= departure + timedelta(seconds=GIVE_UP_AFTER):
                        d.none = d.done = True
                return
            with self._lock:
                d.trip = trip
        stop_id = d.trip.get("stopId") or watch.stop_id
        status = trip_status(self._get, d.trip["tripId"], stop_id, watch.target, self._tz)
        arrival = status["to"]
        # Ein Ausfall beendet einen Eintrag mit earliest nicht, solange ein späterer Bus in Frage kommt (auch bei einer
        # Absage in letzter Minute) oder sich das Ende vor der Abfahrt noch ändern kann
        later = watch.earliest_url is not None and self._next_departure(watch, d, now) is not None
        finished = ((status["cancelled"] and (watch.earliest_url is None or (now >= departure and not later)))
                    or (status["realTime"] and now >= arrival["expected"] + timedelta(seconds=DONE_AFTER_ARRIVAL))
                    or now >= arrival["scheduled"] + timedelta(seconds=GIVE_UP_AFTER))
        with self._lock:
            d.status, d.fails, d.error, d.checked, d.done = status, 0, None, now, bool(finished)
        if status["cancelled"]:
            self._report_cancel(watch, d, status, now)
            if watch.earliest_url is not None:
                d.cancelled.add(chosen)  # nächste Runde: nächste mögliche Abfahrt

    def run_forever(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()
            stop.wait(TICK_SECONDS)

    # ---- für die Seite ----

    def _clock_text(self, dt):
        return dt.astimezone(self._tz).strftime("%H:%M") if dt else None

    def _place(self, place, name=None):
        if place is None:
            return {"name": name, "scheduled": None, "expected": None}
        return {"name": place["name"], "scheduled": self._clock_text(place["scheduled"]),
                "expected": self._clock_text(place["expected"])}

    @staticmethod
    def _minutes(place):
        return round((place["expected"] - place["scheduled"]).total_seconds() / 60)

    def _trip_state(self, d, now):
        s = d.status
        if d.fails >= FAILS_UNTIL_UNREACHABLE:
            return "unreachable"
        if s["cancelled"]:
            return "cancelled"
        if s["realTime"]:
            if now < s["from"]["expected"]:
                return "planned"
            # „angekommen“ erst, wenn eine Abfrage nach der erwarteten Ankunft sie bestätigt hat; vorher ist es nur
            # eine Prognose, und ein Bus, der sich weiter verspätet, stünde sonst zu früh als angekommen da.
            return "arrived" if d.checked >= s["to"]["expected"] else "underway"
        if now >= s["to"]["scheduled"]:
            return "no_realtime"
        return "planned" if now < s["from"]["scheduled"] else "underway"

    def snapshot(self):
        now = self._now()
        trips = []
        with self._lock:
            for i, watch in enumerate(self.watches):
                d = self._days[i]
                if not self._active(watch, now) or d is None or d.day != now.date() or d.none:
                    continue
                if d.status is None and d.fails < FAILS_UNTIL_UNREACHABLE:
                    continue  # noch nichts zu zeigen
                s = d.status
                trips.append({
                    "title": watch.title,
                    "mode": (s or {}).get("mode") or (d.trip or {}).get("mode"),
                    "line": (s or {}).get("line") or watch.line,
                    "from": self._place(s and s["from"]),
                    "to": self._place(s and s["to"], watch.target),
                    "delay": self._minutes(s["to"]) if s else None,
                    "departDelay": self._minutes(s["from"]) if s else None,
                    "state": self._trip_state(d, now) if s else "unreachable",
                    "checked": self._clock_text(d.checked),
                    "error": d.error if d.fails >= FAILS_UNTIL_UNREACHABLE else None,
                })
        return {"now": now.strftime("%H:%M"), "trips": trips}
