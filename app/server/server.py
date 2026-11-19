#!/usr/bin/env python3
"""Öffi-Ankunft: beobachtet Fahrten bei Transitous (tracker.py) und liefert den Stand für eine Kachel.

Nur Standardbibliothek, läuft unter Python 3.9 und 3.13.

    python3 server.py --port 8000 --data DIR --static DIR [--host 0.0.0.0] [--demo]

DIR/config.json enthält die beobachteten Fahrten (config.py, Beispiel config.example.json).

API:
    GET api/status -> {"now": "HH:MM", "trips": [...], "error": null|"no_config"|"bad_config"} (siehe tracker.py)

--demo (nur dev.sh) erfindet eine Fahrt um die aktuelle Uhrzeit und fragt Transitous nie.
"""
import argparse
import json
import socket
import os
import sys
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from config import ConfigError, Watch, load_config, load_notify
from tracker import Tracker, log_stderr
from transitous import USER_AGENT, http_get_json

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
TZ = ZoneInfo("Europe/Berlin")


def dump(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def demo_parts():
    """Eine erfundene Fahrt: vor 20 Minuten abgefahren, in 15 Minuten am Ziel, 3 Minuten später als geplant."""
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    dep, arr = now - timedelta(minutes=20), now + timedelta(minutes=15)

    def z(dt):
        return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get(url):
        if "stoptimes" in url:
            return {"stopTimes": [{"mode": "BUS", "routeShortName": "42", "tripId": "demo",
                                   "place": {"scheduledDeparture": z(dep)}}]}
        return {"legs": [{"mode": "BUS", "routeShortName": "42", "realTime": True, "cancelled": False,
                          "from": {"name": "Musterdorf", "stopId": "demo", "scheduledDeparture": z(dep),
                                   "departure": z(dep + timedelta(minutes=2))},
                          "intermediateStops": [],
                          "to": {"name": "Beispielstadt, Markt", "stopId": "ziel", "scheduledArrival": z(arr),
                                 "arrival": z(arr + timedelta(minutes=3))}}]}

    watch = Watch(stop_id="demo", line="42", departure=dep.strftime("%H:%M"), target="Beispielstadt, Markt",
                  title="Demo", weekdays=tuple(range(7)), show_from="00:00", show_until="23:59")
    return Tracker([watch], get, tz=TZ)


NOTIFY_TIMEOUT = 60


def post_json(url, body):
    """HTTP-Status der Antwort; Netzfehler werfen OSError, Zeitüberschreitung TimeoutError."""
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT) as answer:
            return answer.status
    except urllib.error.HTTPError as e:
        return e.code


def make_notifier(cfg, post=post_json, log=log_stderr):
    """text -> True (zugestellt, vielleicht zugestellt oder abgelehnt: nicht wiederholen) oder False (später neu)."""
    def notify(text):
        body = json.dumps({"text": text, "source": cfg["source"]}, ensure_ascii=False).encode("utf-8")
        try:
            status = post(cfg["url"], body)
        except (TimeoutError, socket.timeout):  # unter Python 3.9 ist socket.timeout kein TimeoutError
            log("Meldung vielleicht zugestellt (Zeitüberschreitung), wird nicht wiederholt")
            return True
        except OSError as e:
            log("Meldung nicht zugestellt (%s)" % type(e).__name__)
            return False
        if status in (200, 201, 202, 204, 504):
            return True
        if status in (400, 413):
            log("Meldung abgelehnt (Status %d), wird nicht wiederholt" % status)
            return True
        log("Meldung nicht zugestellt (Status %d)" % status)
        return False
    return notify


def main_parts(data_dir, demo=False, log=log_stderr):
    """(Tracker, Konfigurationsfehler oder None)."""
    if demo:
        return demo_parts(), None
    path = os.path.join(data_dir, "config.json")
    if not os.path.exists(path):
        log("Keine Konfiguration: %s fehlt (Beispiel: config.example.json)" % path)
        return Tracker([], http_get_json, tz=TZ, log=log), "no_config"
    try:
        watches = load_config(path)
        notify = load_notify(path)
    except ConfigError as e:
        log("Konfiguration fehlerhaft: %s" % e)
        return Tracker([], http_get_json, tz=TZ, log=log), "bad_config"
    notifier = make_notifier(notify, log=log) if notify else None
    return Tracker(watches, http_get_json, tz=TZ, log=log, notify=notifier, state_dir=data_dir), None


class Handler(BaseHTTPRequestHandler):
    server_version = "OeffiAnkunft"
    timeout = 60

    def log_message(self, format, *args):
        if not self.server.quiet:
            super().log_message(format, *args)

    def log_request(self, code="-", size="-"):
        # Die Startseite fragt regelmäßig; ins Log kommen nur Fehler.
        if code == 200:
            return
        super().log_request(code, size)

    def do_GET(self):
        path = urlsplit(self.path).path.lstrip("/")
        try:
            if path == "api/status":
                return self._json(200, dict(self.server.tracker.snapshot(), error=self.server.config_error))
            if not path.startswith("api/"):
                return self._static(unquote(path))
            self._json(404, {"error": "not_found"})
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "server_error"})

    def _static(self, rel):
        root = os.path.realpath(self.server.static_root)
        try:
            target = os.path.realpath(os.path.join(root, rel))
            if not target.startswith(root + os.sep) or os.path.isdir(target):
                return self._json(404, {"error": "not_found"})
            with open(target, "rb") as f:
                body = f.read()
        except (OSError, ValueError):
            return self._json(404, {"error": "not_found"})
        ctype = CONTENT_TYPES.get(os.path.splitext(target)[1].lower(), "application/octet-stream")
        self._send(200, body, ctype, {"Cache-Control": "no-store"})

    def _json(self, status, obj):
        self._send(status, dump(obj), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _send(self, status, body, ctype, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def make_server(tracker, static_root, port, host="127.0.0.1", quiet=False, config_error=None):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.tracker = tracker
    httpd.static_root = static_root
    httpd.quiet = quiet
    httpd.config_error = config_error
    return httpd


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Öffi-Ankunft")
    parser.add_argument("--port", type=int, default=8000)
    # Ohne --host nur von diesem Rechner erreichbar (dev.sh); der Container gibt 0.0.0.0 an (Dockerfile).
    parser.add_argument("--host", default="127.0.0.1", help="Adresse, auf der der Server lauscht")
    parser.add_argument("--data", required=True, help="Ordner mit config.json")
    parser.add_argument("--static", required=True, help="Ordner mit summary.js")
    parser.add_argument("--demo", action="store_true", help="erfundene Fahrt statt Transitous (nur zum Entwickeln)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    tracker, config_error = main_parts(args.data, demo=args.demo)
    httpd = make_server(tracker, args.static, args.port, host=args.host, config_error=config_error)
    # Erst nach dem Binden: Scheitert der Start (Port belegt), soll kein Neustart-Kreis Transitous befragen.
    threading.Thread(target=tracker.run_forever, args=(threading.Event(),), daemon=True).start()
    print("Öffi-Ankunft läuft auf %s:%d (%d Fahrten)" % (args.host, args.port, len(tracker.watches)), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
