# Öffi-Ankunft

Ein kleiner Dienst, der feste Fahrten im Nahverkehr beobachtet, etwa den Bus zur Arbeit, und zeigt, ob und wann
sie angekommen sind: „angekommen 07:43 (+3)“, „fällt aus“ oder „laut Plan“ (ohne Echtzeitdaten).
Gebaut als Kachel für ein Heimnetz-Dashboard, die JSON-Schnittstelle taugt aber auch für anderes.

Die Daten kommen von [Transitous](https://transitous.org) (MOTIS 2, freie Fahrplan- und Echtzeitdaten). Abgefragt werden
nur `v5/stoptimes` und `v5/trip`, je Fahrt höchstens alle 10 Minuten und nur im eingestellten Zeitfenster.

## Konfiguration

`<data>/config.json`, Vorlage: [`app/config.example.json`](app/config.example.json)

```json
{"watches": [{"title": "Pendelweg", "stop_id": "de-DELFI_…", "line": "42", "departure": "07:05",
              "target": "Beispielstadt, Markt", "weekdays": [0, 1, 2, 3, 4], "show_from": "06:30", "show_until": "12:00"}]}
```

- `stop_id`: Abfahrtshaltestelle bei Transitous, zu finden mit
  `https://api.transitous.org/api/v1/geocode?text=<Name>` (Feld `id`).
- `line`, `departure`: Linie und geplante Abfahrt (Ortszeit Europe/Berlin) der Fahrt.
- `target`: Name der Zielhaltestelle, genau wie im Fahrtverlauf bei Transitous.
- optional: `title`, `weekdays` (0 = Montag, Standard Mo–Fr), `show_from`/`show_until` (Standard 06:30–12:00).

**Rückfahrt nach einem Ende (etwa Arbeitsende):** statt `departure` eine Liste `departures` und `earliest`:

```json
{"title": "Rückweg", "stop_id": "de-DELFI_…", "line": "42", "departures": ["11:50", "12:10", "13:50"],
 "target": "Musterdorf", "earliest": {"url": "http://kalender.example/api/ende?person=a", "gap": 10},
 "show_from": "12:00", "show_until": "20:00"}
```

Die Adresse liefert `{"date": "JJJJ-MM-TT", "end": "HH:MM"}` (oder `"end": null`, wenn heute nichts ist). Beobachtet
wird die erste Abfahrt mindestens `gap` Minuten nach `end`; bis zur Abfahrt wird `end` jede Runde neu gelesen. Haben
zwei Einträge dieselbe Fahrt, zeigt die Kachel sie einmal mit beiden Titeln. Transitous wird nach der ersten Abfrage
erst wieder 30 Minuten vor der Abfahrt gefragt.
Liefert die Adresse `"skip": true`, wird heute nichts beobachtet (Sonderfall). Fällt der gewählte Bus vor der Abfahrt
aus, beobachtet der Dienst die nächste mögliche Abfahrt.

**Meldung bei Ausfall:** auf oberster Ebene `"notify": {"url": "http://…", "source": "Pendeln"}`. Fällt eine beobachtete
Fahrt aus, geht einmal je Fahrt und Tag `{"text", "source"}` per POST dorthin, etwa „A + B: Bus 42 ab Beispielstadt,
Markt 11:50 fällt aus, nächster möglicher Bus 12:10.“ Was gemeldet ist, steht in `<data>/gemeldet.json`. Gleiche
Anfragen an Transitous teilen sich fünf Minuten lang ein Ergebnis.

## Starten

```sh
python3 app/server/server.py --data DATA --static app/web --port 8000   # nur Python-Standardbibliothek
app/dev.sh                                                              # mit erfundener Fahrt, ohne Netz
```

`GET api/status` liefert `{"now", "trips": [...], "error"}`; je Fahrt Abfahrt und Ankunft (geplant, erwartet),
Verspätung in Minuten und `state`: `planned`, `underway`, `arrived`, `no_realtime`, `cancelled`, `unreachable`.
`app/web/summary.js` macht daraus eine Kachel. Docker: `app/Dockerfile`, Konfiguration unter `/data` (schreibbar für
`gemeldet.json`). `deploy.sh` testet und rollt per ssh in einen Compose-Stack aus (`DEPLOY_HOST`, `DEPLOY_DIR`).

Tests: `python3 -m unittest discover -s app/server` und `node --test 'app/test/*.test.js'`.

## Transitous fair nutzen

Transitous wird von Freiwilligen betrieben. Wer diesen Dienst einsetzt, hält sich an deren
[Nutzungsbedingungen](https://transitous.org/api/): nicht kommerziell, wenig Last, und ein sichtbarer Link auf
<https://transitous.org/sources/> dort, wo die Daten erscheinen. Wer den Code ändert und weitergibt, ändert bitte den
User-Agent in `app/server/transitous.py` auf das eigene Projekt und einen eigenen Kontakt.
Spenden: <https://transitous.org/donate/>

## Lizenz

MIT, siehe [LICENSE](LICENSE).
