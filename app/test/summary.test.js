'use strict';
// Kachel für die lan-pi-Startseite (web/summary.js), mit Ersatz-Server.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const BASE = 'http://lan-pi/oeffi/';
const plain = (value) => JSON.parse(JSON.stringify(value));

function trip(changes = {}) {
  return Object.assign({ title: 'Pendelweg', mode: 'BUS', line: '42',
    from: { name: 'Musterdorf', scheduled: '07:05', expected: '07:07' },
    to: { name: 'Beispielstadt, Markt', scheduled: '07:40', expected: '07:43' },
    delay: 3, departDelay: 2, state: 'arrived', checked: '07:50', error: null }, changes);
}

function load(body, status = 200) {
  const ctx = vm.createContext({
    URL, console,
    document: { currentScript: { src: BASE + 'summary.js' } },
    fetch: async (url) => {
      assert.equal(url, BASE + 'api/status');
      return { status, ok: status === 200, json: async () => structuredClone(body) };
    },
  });
  ctx.window = ctx;
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'web', 'summary.js'), 'utf8'), ctx, { filename: 'summary.js' });
  return ctx.LanPiSummary.oeffi;
}

const status = (...trips) => ({ now: '07:50', trips, error: null });

test('keine Fahrt im Fenster: Kachel versteckt', async () => {
  const s = plain(await load(status())());
  assert.equal(s.hidden, true);
  assert.deepEqual(s.todos, []);
});

test('angekommen mit Verspätung', async () => {
  const s = plain(await load(status(trip()))());
  assert.equal(s.hidden, false);
  assert.equal(s.figure, '07:43');
  assert.equal(s.figureLabel, 'Pendelweg · an Beispielstadt, Markt');
  assert.deepEqual(s.badge, { text: 'angekommen +3', tone: 'ok' });
  assert.deepEqual(s.meta, ['Pendelweg: Bus 42 ab Musterdorf 07:05 (+2)']);
  assert.deepEqual(s.fresh, { text: 'Stand 07:50', tone: null });
  assert.deepEqual([s.compact, s.wide, s.priority], [false, true, true]);
  assert.equal(s.backup, null);
});

test('Abzeichen je Zustand', async () => {
  const cases = [
    [{ delay: 6 }, { text: 'angekommen +6', tone: 'warn' }],
    [{ delay: 0 }, { text: 'angekommen pünktlich', tone: 'ok' }],
    [{ delay: -1 }, { text: 'angekommen pünktlich', tone: 'ok' }],
    [{ state: 'underway', delay: 2 }, { text: 'unterwegs +2', tone: null }],
    [{ state: 'underway', delay: 0 }, { text: 'unterwegs', tone: null }],
    [{ state: 'underway', delay: 7 }, { text: 'unterwegs +7', tone: 'warn' }],
    [{ state: 'planned', delay: 7, departDelay: 0 }, { text: 'geplant', tone: null }],
    [{ state: 'planned', delay: 0, departDelay: 5 }, { text: 'geplant +5', tone: 'warn' }],
    [{ state: 'no_realtime' }, { text: 'laut Plan', tone: null }],
    [{ state: 'cancelled' }, { text: 'fällt aus', tone: 'bad' }],
    [{ state: 'unreachable' }, { text: 'keine Daten', tone: 'warn' }],
  ];
  for (const [changes, badge] of cases) {
    assert.deepEqual(plain(await load(status(trip(changes)))()).badge, badge, JSON.stringify(changes));
  }
});

test('ohne Echtzeit: geplante Ankunft als Zahl', async () => {
  const s = plain(await load(status(trip({ state: 'no_realtime', delay: 0, departDelay: 0,
    from: { name: 'Musterdorf', scheduled: '07:05', expected: '07:05' },
    to: { name: 'Beispielstadt, Markt', scheduled: '07:40', expected: '07:40' } })))());
  assert.equal(s.figure, '07:40');
  assert.deepEqual(s.meta, ['Pendelweg: Bus 42 ab Musterdorf 07:05']);
});

test('nicht erreichbar, bevor die Fahrt gefunden ist', async () => {
  const s = plain(await load(status(trip({ state: 'unreachable', mode: null, delay: null, departDelay: null, checked: null,
    from: { name: null, scheduled: null, expected: null },
    to: { name: 'Beispielstadt, Markt', scheduled: null, expected: null }, error: 'unreachable (timeout)' })))());
  assert.equal(s.figure, null);
  assert.equal(s.figureLabel, 'Pendelweg · an Beispielstadt, Markt');
  assert.deepEqual(s.meta, ['Pendelweg: Linie 42, keine Daten']);
  assert.deepEqual(s.fresh, { text: 'noch nicht abgefragt', tone: null });
});

test('Verkehrsmittel in Worten, weitere Fahrten als Zeilen', async () => {
  const s = plain(await load(status(trip(), trip({ mode: 'REGIONAL_RAIL', line: 'RB 20', state: 'underway', delay: 0, departDelay: 0,
    from: { name: 'Beispielstadt', scheduled: '08:00', expected: '08:00' },
    to: { name: 'Hauptstadt', scheduled: '08:30', expected: '08:30' } })))());
  assert.deepEqual(s.meta, ['Pendelweg: Bus 42 ab Musterdorf 07:05 (+2) · an Beispielstadt, Markt 07:43 · angekommen +3',
    'Pendelweg: Zug RB 20 ab Beispielstadt 08:00 · an Hauptstadt 08:30 · unterwegs']);
});

test('fällt aus: keine Ankunftszeit als große Zahl', async () => {
  const s = plain(await load(status(trip({ state: 'cancelled' })))());
  assert.equal(s.figure, null);
  assert.deepEqual(s.badge, { text: 'fällt aus', tone: 'bad' });
});

const back = (title, dep, arr, changes = {}) => trip(Object.assign({ title, line: '42',
  from: { name: 'Beispielstadt, Markt', scheduled: dep, expected: dep },
  to: { name: 'Musterdorf', scheduled: arr, expected: arr }, delay: 0, departDelay: 0 }, changes));

test('gleiche Fahrt für zwei Einträge: eine Zeile mit beiden Titeln', async () => {
  const s = plain(await load(status(back('A', '11:50', '12:25'), back('B', '11:50', '12:25')))());
  assert.deepEqual(s.meta, ['A + B: Bus 42 ab Beispielstadt, Markt 11:50']);
  assert.equal(s.figureLabel, 'A + B · an Musterdorf');
});

test('große Zahl: erste noch nicht angekommene Fahrt, sonst die zuletzt angekommene', async () => {
  let s = plain(await load(status(back('A', '11:50', '12:25'), back('B', '13:50', '14:25', { state: 'planned' })))());
  assert.equal(s.figure, '13:50');
  assert.equal(s.figureLabel, 'B · ab Beispielstadt, Markt');
  assert.deepEqual(s.badge, { text: 'geplant', tone: null });
  assert.deepEqual(s.meta, ['A: Bus 42 ab Beispielstadt, Markt 11:50 · an Musterdorf 12:25 · angekommen pünktlich',
    'B: Bus 42 ab Beispielstadt, Markt 13:50 · an Musterdorf 14:25 · geplant']);
  s = plain(await load(status(back('A', '11:50', '12:25'), back('B', '13:50', '14:25')))());
  assert.equal(s.figure, '14:25');
  assert.equal(s.figureLabel, 'B · an Musterdorf');
});

test('nach Zeit, nicht nach Reihenfolge der Konfiguration', async () => {
  // A fährt später, B früher: die große Zahl gehört zuerst B, abends der zuletzt angekommenen A
  let s = plain(await load(status(back('A', '13:50', '14:25', { state: 'planned' }), back('B', '11:50', '12:25', { state: 'underway' })))());
  assert.equal(s.figureLabel, 'B · an Musterdorf');
  assert.equal(s.meta[0].slice(0, 2), 'B:');
  s = plain(await load(status(back('A', '13:50', '14:25'), back('B', '11:50', '12:25')))());
  assert.equal(s.figureLabel, 'A · an Musterdorf');
  s = plain(await load(status(back('A', '11:50', '12:25', { state: 'unreachable', from: { name: null, scheduled: null, expected: null },
    to: { name: 'Musterdorf', scheduled: null, expected: null } }), back('B', '13:50', '14:25', { state: 'planned' })))());
  assert.equal(s.figure, '13:50');
});

test('ohne Titel keine Vorsilbe', async () => {
  const s = plain(await load(status(trip({ title: null })))());
  assert.deepEqual(s.meta, ['Bus 42 ab Musterdorf 07:05 (+2)']);
  assert.equal(s.figureLabel, 'an Beispielstadt, Markt');
});

test('Server meldet Fehler: Kachel wirft', async () => {
  await assert.rejects(load({}, 502)());
});

test('Server ohne Konfiguration: versteckt', async () => {
  assert.equal(plain(await load({ now: '07:00', trips: [], error: 'no_config' })()).hidden, true);
});

test('vor der Abfahrt: Abfahrtszeit groß, Ankunft in der Zeile darunter', async () => {
  const s = plain(await load(status(trip({ state: 'planned', delay: 1, departDelay: 1, checked: '06:30',
    from: { name: 'Musterdorf', scheduled: '07:05', expected: '07:06' },
    to: { name: 'Beispielstadt, Markt', scheduled: '07:40', expected: '07:41' } })))());
  assert.equal(s.figure, '07:06');
  assert.equal(s.figureLabel, 'Pendelweg · ab Musterdorf');
  assert.deepEqual(s.badge, { text: 'geplant +1', tone: null });
  assert.deepEqual(s.meta, ['Pendelweg: Bus 42 · an Beispielstadt, Markt 07:40 (+1)']);
});

test('nach der Abfahrt wieder die Ankunft groß', async () => {
  const s = plain(await load(status(trip({ state: 'underway' })))());
  assert.equal(s.figure, '07:43');
  assert.equal(s.figureLabel, 'Pendelweg · an Beispielstadt, Markt');
  assert.deepEqual(s.meta, ['Pendelweg: Bus 42 ab Musterdorf 07:05 (+2)']);
});
