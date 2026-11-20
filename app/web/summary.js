// Kachel für die lan-pi-Startseite (https://github.com/mucnoc/oeffi-ankunft). Die Startseite lädt diese Datei und ruft
// LanPiSummary.oeffi() auf. Andere Seiten können api/status direkt lesen.
//
// Die erste noch nicht angekommene Fahrt (sonst die zuletzt angekommene) bestimmt Zahl und Abzeichen: vor der Abfahrt
// (planned) die Abfahrtszeit, danach die Ankunftszeit. Jede Fahrt hat
// eine Zeile mit ihrem Titel; dieselbe Fahrt zweier Einträge steht einmal da. Ohne Fahrt im Zeitfenster: hidden.
// Ergebnis: { hidden, figure, figureLabel, tone, badge, meta: [Text], fresh, compact, wide, priority, todos, backup }
(function () {
  'use strict';

  const BASE = new URL('.', document.currentScript.src).href;
  const LATE = 5;
  const MODES = { BUS: 'Bus', COACH: 'Bus', TRAM: 'Tram', SUBWAY: 'U-Bahn', SUBURBAN: 'S-Bahn', FERRY: 'Fähre',
    REGIONAL_RAIL: 'Zug', REGIONAL_FAST_RAIL: 'Zug', HIGHSPEED_RAIL: 'Zug', LONG_DISTANCE: 'Zug', NIGHT_RAIL: 'Zug', RAIL: 'Zug' };

  function plus(n) {
    return n > 0 ? ' +' + n : '';
  }

  function badge(t) {
    const late = t.delay >= LATE ? 'warn' : null;
    switch (t.state) {
      case 'arrived': return { text: t.delay > 0 ? 'angekommen +' + t.delay : 'angekommen pünktlich', tone: late || 'ok' };
      case 'underway': return { text: 'unterwegs' + plus(t.delay), tone: late };
      // vor der Abfahrt zählt die Verspätung der Abfahrt, die groß dasteht
      case 'planned': return { text: 'geplant' + plus(t.departDelay), tone: t.departDelay >= LATE ? 'warn' : null };
      case 'no_realtime': return { text: 'laut Plan', tone: null };
      case 'cancelled': return { text: 'fällt aus', tone: 'bad' };
      default: return { text: 'keine Daten', tone: 'warn' };
    }
  }

  function name(t) {
    return (MODES[t.mode] || 'Linie') + ' ' + t.line;
  }

  function prefix(t) {
    return t.title ? t.title + ': ' : '';
  }

  function delayed(time, n) {
    return time + (n > 0 ? ' (+' + n + ')' : '');
  }

  function line(t, short) {
    if (!t.from.name) return prefix(t) + name(t) + ', keine Daten';
    // nur eine Fahrt und noch nicht abgefahren: die Abfahrt steht groß da, die Zeile nennt die Ankunft
    if (short && t.state === 'planned') return prefix(t) + name(t) + ' · an ' + t.to.name + ' ' + delayed(t.to.scheduled, t.delay);
    const start = prefix(t) + name(t) + ' ab ' + t.from.name + ' ' + t.from.scheduled + (t.departDelay > 0 ? ' (+' + t.departDelay + ')' : '');
    if (short) return start;
    return start + ' · an ' + t.to.name + ' ' + (t.to.expected || t.to.scheduled) + ' · ' + badge(t).text;
  }

  // Zwei Einträge, dieselbe Fahrt (zwei Personen im selben Bus): einmal zeigen, Titel zusammen
  function merge(trips) {
    const out = [];
    for (const t of trips) {
      const same = t.from.name && out.find((o) => o.line === t.line && o.from.name === t.from.name && o.from.scheduled === t.from.scheduled);
      if (same) same.title = [same.title, t.title].filter(Boolean).join(' + ') || null;
      else out.push(Object.assign({}, t));
    }
    return out;
  }

  const DONE = ['arrived', 'cancelled', 'no_realtime'];

  // Nach Abfahrt sortiert; Fahrten ohne Daten (unreachable vor der ersten Antwort) ans Ende
  function byTime(a, b) {
    return (a.from.scheduled || '99:99').localeCompare(b.from.scheduled || '99:99');
  }

  // Große Zahl: die früheste Fahrt mit Daten, die noch nicht angekommen ist, sonst die zuletzt angekommene, sonst die erste
  function main(trips) {
    const open = trips.find((t) => t.from.name && !DONE.includes(t.state));
    if (open) return open;
    const done = trips.filter((t) => DONE.includes(t.state));
    return done.length ? done[done.length - 1] : trips[0];
  }

  async function summary() {
    const res = await fetch(BASE + 'api/status', { cache: 'no-store' });
    if (!res.ok) throw new Error('api/status: ' + res.status);
    const data = await res.json();
    const trips = merge(data.trips || []).sort(byTime);
    const base = { compact: false, wide: true, priority: true, todos: [], backup: null, tone: null };
    if (!trips.length) return Object.assign(base, { hidden: true, figure: null, figureLabel: '', badge: null, meta: [], fresh: null });
    const first = main(trips);
    const before = first.state === 'planned';
    const end = before ? first.from : first.to;
    return Object.assign(base, {
      hidden: false,
      figure: first.state === 'cancelled' ? null : end.expected || end.scheduled || null,
      figureLabel: (first.title ? first.title + ' · ' : '') + (before ? 'ab ' : 'an ') + end.name,
      badge: badge(first),
      meta: trips.map((t) => line(t, trips.length === 1)),
      fresh: { text: first.checked ? 'Stand ' + first.checked : 'noch nicht abgefragt', tone: null },
    });
  }

  window.LanPiSummary = window.LanPiSummary || {};
  window.LanPiSummary.oeffi = summary;
})();
