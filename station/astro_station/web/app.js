/* Astro-Station Web-App: Zustand alle 1,5 s von /api/state, Befehle per POST. Kein Build-Schritt. */
'use strict';

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('astro-station.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('astro-station.' + k, JSON.stringify(v)); } catch (e) { /* privat */ } }
};
const S = {
  state: null, tab: store.get('tab', null), speed: store.get('speed', 1), sel: {}, tonight: [],
  search: null, lastEvent: 0, held: null, rigDirty: false
};
const SPEEDS = ['Langsam', 'Mittel', 'Schnell', 'Max'];
const DIRS_SEESTAR = { N: 'oben', S: 'unten', W: 'links', E: 'rechts' };
const DIRS_MOUNT = { N: 'Norden', S: 'Süden', W: 'Westen', E: 'Osten' };

function esc(s) { return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function de(n, d = 2) { return n == null ? '–' : Number(n).toFixed(d).replace('.', ','); }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok || (data && data.ok === false)) {
    const msg = (data && data.error) || `${r.status} ${r.statusText}`;
    toast(msg, 'warn');
    throw new Error(msg);
  }
  if (data && data.message) toast(data.message);
  return data;
}

let toastTimer = null;
function toast(text, kind) {
  $('toast-text').textContent = text;
  $('toast').className = 'toast' + (kind === 'warn' ? ' warn' : '');
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, kind === 'warn' ? 9000 : 5000);
}
$('toast-x').onclick = () => { $('toast').hidden = true; };

function ask(text) {
  return new Promise((resolve) => {
    $('modal-text').textContent = text;
    $('modal').hidden = false;
    $('modal-yes').focus();
    const done = (v) => { $('modal').hidden = true; $('modal-yes').onclick = $('modal-no').onclick = null; resolve(v); };
    $('modal-yes').onclick = () => done(true);
    $('modal-no').onclick = () => done(false);
  });
}

/* ---------------------------------------------------------------- Rotlicht */
function setRed(on) {
  document.documentElement.dataset.red = on ? '1' : '0';
  $('red').setAttribute('aria-pressed', on ? 'true' : 'false');
  $('red').textContent = on ? 'Normallicht' : 'Rotlicht';
  store.set('red', on);
}
setRed(store.get('red', false));
$('red').onclick = () => setRed(document.documentElement.dataset.red !== '1');
$('stopall').onclick = () => api('/api/stop_all', {}).catch(() => {});

/* ---------------------------------------------------------------- Zustand */
function cur() {
  const st = S.state;
  if (!st) return null;
  const on = st.devices.filter((d) => d.enabled);
  if (!on.some((d) => d.id === S.tab)) S.tab = on.length ? on[0].id : null;
  return on.find((d) => d.id === S.tab) || null;
}

async function poll() {
  try {
    S.state = await (await fetch('/api/state')).json();
    for (const ev of S.state.events || []) {
      if (ev.id > S.lastEvent) {
        if (S.lastEvent) toast(ev.text, ev.kind);
        S.lastEvent = ev.id;
      }
    }
    render();
  } catch (e) {
    $('subtitle').textContent = 'Station nicht erreichbar …';
  }
}

async function loadTonight() {
  try { S.tonight = await (await fetch('/api/tonight')).json(); renderTargets(); } catch (e) { /* spaeter */ }
}

/* ---------------------------------------------------------------- Kopf, Geräte, Tabs */
function renderHead(st) {
  const loc = st.location;
  $('loc').innerHTML = loc.lat == null ? 'Standort unbekannt'
    : `${de(loc.lat)}° ${loc.lat >= 0 ? 'N' : 'S'} · ${de(Math.abs(loc.lon))}° ${loc.lon >= 0 ? 'O' : 'W'} · <b>${esc(loc.source === 'seestar_alp' ? 'aus seestar_alp' : loc.source)}</b>`;
  if (st.sky) {
    $('night').innerHTML = `Astron. Nacht <b>${st.sky.astro[0]}–${st.sky.astro[1]}</b>`;
    const m = st.sky.moon;
    $('moon').innerHTML = `Mond ${m.illum} % ${m.waxing ? '↑' : '↓'} · <b>${m.up ? 'über dem Horizont' : (m.rise !== '–' ? 'Aufgang ' + m.rise : 'unter dem Horizont')}</b>`;
  }
  $('clock').textContent = st.now;
  const n = st.devices.filter((d) => d.enabled && d.online).length;
  $('subtitle').textContent = `${n} von ${st.devices.filter((d) => d.enabled).length} Geräten verbunden`;
}

function renderDevices(st) {
  $('devchips').innerHTML = st.devices.map((d) => `
    <button type="button" class="devchip" data-id="${esc(d.id)}" aria-pressed="${d.enabled}">
      <span class="box" aria-hidden="true">${d.enabled ? '✓' : ''}</span>
      <span class="lines"><span class="strong">${esc(d.name)}</span><span>${esc(d.enabled ? (d.online ? d.sub || '' : 'nicht verbunden') : 'aus')}</span></span>
    </button>`).join('');
  for (const b of $('devchips').querySelectorAll('.devchip')) {
    b.onclick = async () => {
      const d = st.devices.find((x) => x.id === b.dataset.id);
      await api('/api/session', { id: d.id, enabled: !d.enabled }).catch(() => {});
      if (!d.enabled) { S.tab = d.id; store.set('tab', d.id); }
      poll();
    };
  }
  const on = st.devices.filter((d) => d.enabled);
  $('tabs').innerHTML = on.map((d) => `
    <button type="button" class="tab" data-id="${esc(d.id)}" aria-current="${d.id === S.tab ? 'page' : 'false'}">
      <span class="dot${d.online ? ' on' : ''}" aria-hidden="true"></span>${esc(d.name)}</button>`).join('');
  for (const b of $('tabs').querySelectorAll('.tab')) {
    b.onclick = () => { S.tab = b.dataset.id; store.set('tab', S.tab); S.rigDirty = false; render(); renderTargets(); };
  }
  $('empty').hidden = on.length > 0;
  $('main').hidden = on.length === 0;
}

/* ---------------------------------------------------------------- Ziele */
function targetList() { return S.search !== null ? S.search : S.tonight; }

function renderTargets() {
  const d = cur();
  if (!d) return;
  $('t-title').textContent = S.search !== null ? 'Suchergebnis' : `Heute Nacht für ${d.name}`;
  const list = targetList();
  const sel = S.sel[d.id];
  $('targets').innerHTML = list.length ? list.map((t, i) => `
    <button type="button" class="target" data-i="${i}" aria-pressed="${sel && sel.id === t.id}">
      <span class="l"><span>${esc(t.name)}</span><span>${esc([t.window, t.peak].filter(Boolean).join(' · '))}</span></span>
      <span class="r"><span class="tag${['später', 'tief'].includes(t.tag) ? ' dim' : ''}">${esc(t.tag)}</span><span class="alt">${t.alt == null ? '' : t.alt + '°'}</span></span>
    </button>`).join('') : `<div class="muted small">${S.search !== null ? 'Nichts gefunden.' : 'Standort unbekannt oder keine Ziele – Standort kommt aus seestar_alp.'}</div>`;
  for (const b of $('targets').querySelectorAll('.target')) {
    b.onclick = () => { S.sel[d.id] = list[+b.dataset.i]; renderTargets(); renderSel(); };
  }
  renderSel();
}

function hms(ra) {
  if (ra == null) return '';
  const t = Math.round(ra * 3600); const h = Math.floor(t / 3600); const m = Math.floor((t % 3600) / 60); const s = t % 60;
  return `${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
}
function dms(dec) {
  if (dec == null) return '';
  const sg = dec < 0 ? '−' : '+'; const t = Math.round(Math.abs(dec) * 3600);
  return `${sg}${Math.floor(t / 3600)}° ${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}′ ${String(t % 60).padStart(2, '0')}″`;
}

function renderSel() {
  const d = cur();
  const t = d && S.sel[d.id];
  $('sel-name').textContent = t ? t.name : '–';
  $('sel-coords').textContent = !t ? '' : t.kind === 'moon' ? 'Suchlauf am Seestar · ohne Koordinaten'
    : t.kind === 'planet' ? 'Position wird beim GoTo live abgefragt' : `RA ${hms(t.ra)} · Dec ${dms(t.dec)} (J2000)`;
  $('goto').disabled = !t;
  $('goto-center').hidden = !(d && d.can && d.can.center);
  $('goto-center').disabled = !t;
  if (t && d && d.can && d.can.series && !$('s-target').matches(':focus') && !S.seriesTargetEdited) $('s-target').value = t.short || t.name;
}

let searchTimer = null;
$('q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  const q = $('q').value.trim();
  if (!q) { S.search = null; renderTargets(); return; }
  searchTimer = setTimeout(async () => {
    try { S.search = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json(); } catch (e) { S.search = []; }
    renderTargets();
  }, 250);
});

async function doGoto(center) {
  const d = cur(); const t = d && S.sel[d.id];
  if (!t) return;
  await api(`/api/dev/${d.id}/goto`, { target: t, center: !!center }).catch(() => {});
  poll();
}
$('goto').onclick = () => doGoto(false);
$('goto-center').onclick = () => doGoto(true);

/* ---------------------------------------------------------------- Aufbau und Serie */
function renderRig(d) {
  const rig = d.rig;
  $('sec-rig').hidden = !(d.can && d.can.rig);
  if (!rig) return;
  $('rig-title').textContent = `Aufbau an der ${d.name}`;
  $('cams').innerHTML = rig.cameras.map((c) => `<button type="button" class="pickbtn cam" data-id="${esc(c.id)}" aria-pressed="${c.id === rig.camera}"><span class="strong">${esc(c.name)}</span><small>${esc(c.sub)}</small></button>`).join('');
  for (const b of $('cams').querySelectorAll('.pickbtn')) b.onclick = () => saveRig({ camera: b.dataset.id });
  if (!S.rigDirty) {
    $('optic').innerHTML = rig.optics.map((o) => `<option${o.name === rig.optic ? ' selected' : ''}>${esc(o.name)}</option>`).join('') + `<option value="__own"${rig.optics.some((o) => o.name === rig.optic) ? '' : ' selected'}>eigene …</option>`;
    $('focal').value = rig.focal ?? '';
    $('aperture').value = rig.aperture ?? '';
  }
  const fov = rig.fov ? `${de(rig.fov[0], 1)}° × ${de(rig.fov[1], 1)}°` : '–';
  $('rig-info').innerHTML = `<span>Bildfeld <b>${fov}</b></span><span>Bildmaßstab <b>${rig.scale ? de(rig.scale, 1) + '″/px' : '–'}</b></span><span>Ablage als <b>${esc(rig.device_id || '–')}</b> in siril-auto</span>`;
}

async function saveRig(change) {
  const d = cur();
  S.rigDirty = false;
  await api(`/api/dev/${d.id}/action`, { action: 'rig', params: change }).catch(() => {});
  poll();
}
$('optic').onchange = () => {
  const d = cur(); const o = d.rig.optics.find((x) => x.name === $('optic').value);
  if (o) saveRig({ optic: o.name, focal: o.focal, aperture: o.aperture });
  else { S.rigDirty = true; $('focal').focus(); }
};
for (const id of ['focal', 'aperture']) {
  $(id).addEventListener('input', () => { S.rigDirty = true; });
  $(id).addEventListener('change', () => saveRig({ optic: $('optic').value === '__own' ? 'eigene Optik' : $('optic').value, focal: $('focal').value, aperture: $('aperture').value }));
}

function renderSeries(d) {
  $('sec-series').hidden = !(d.can && d.can.series);
  if (!d.can || !d.can.series) return;
  const rig = d.rig || {};
  $('series-title').textContent = `Serie mit ${rig.camera_name || 'Kamera'}`;
  const iso = $('s-iso');
  const opts = d.iso_options || [];
  if (iso.dataset.opts !== opts.join(',')) {
    iso.innerHTML = '<option value="">wie Kamera</option>' + opts.map((o) => `<option>${esc(o)}</option>`).join('');
    iso.dataset.opts = opts.join(',');
  }
  const s = d.series || {};
  const exp = parseFloat($('s-exp').value) || 0; const n = parseInt($('s-count').value, 10) || 0;
  const tgt = ($('s-target').value || 'Ziel').replace(/[^A-Za-z0-9 +._-]/g, '').trim().replace(/ /g, '_');
  $('s-info').textContent = s.running
    ? `Läuft: ${s.done} / ${s.count || '∞'} × ${s.exposure} s – ${s.target}${s.last_file ? ' · zuletzt ' + s.last_file.split('/').pop() : ''}`
    : `Gesamt ${n ? Math.round(n * exp / 60) + ' Min' : 'endlos'} · Ablage ${tgt}_sub/Light_${tgt}_${exp}s_${rig.filter || 'DSLR'}_…fits · Header: Ziel, ${d.name}, ${rig.camera_name || ''}, ${rig.optic || ''}, Standort`;
  $('s-start').textContent = s.running ? 'Serie stoppen' : 'Serie starten';
  $('s-start').className = 'btn ' + (s.running ? 'danger' : 'primary');
}
$('s-target').addEventListener('input', () => { S.seriesTargetEdited = true; });
$('s-test').onclick = () => { const d = cur(); api(`/api/dev/${d.id}/action`, { action: 'test', params: { exposure: $('s-exp').value, target: $('s-target').value } }).catch(() => {}); };
$('s-live').onclick = () => {
  const d = cur(); const on = !(S.liveOn && S.liveOn[d.id]);
  S.liveOn = { ...(S.liveOn || {}), [d.id]: on };
  $('s-live').textContent = on ? 'Live View aus' : 'Live View (Fokus)';
  api(`/api/dev/${d.id}/action`, { action: on ? 'live_on' : 'live_off' }).catch(() => {});
};
$('s-start').onclick = () => {
  const d = cur();
  if (d.series && d.series.running) return api(`/api/dev/${d.id}/action`, { action: 'series_stop' }).catch(() => {});
  return api(`/api/dev/${d.id}/action`, { action: 'series', params: { target: $('s-target').value, type: $('s-type').value, exposure: $('s-exp').value, count: $('s-count').value, iso: $('s-iso').value, delay: $('s-delay').value } }).catch(() => {});
};
for (const id of ['s-exp', 's-count']) $(id).addEventListener('input', () => { const d = cur(); if (d) renderSeries(d); });

/* ---------------------------------------------------------------- Bild */
let imgSrc = '';
function renderImage(d) {
  const im = d.image || {};
  $('img-title').textContent = im.title || 'Bild';
  $('img-hint').textContent = im.hint || '';
  let src = '';
  if (d.kind === 'seestar') {
    src = im.jpeg || '';
    $('img-ph').textContent = d.online ? 'Live-Bild, sobald eine Ansicht läuft – oder im Live-Fenster' : 'nicht verbunden';
  } else if (im.jpeg) {
    src = im.jpeg + '?t=' + Math.floor(Date.now() / 3000);
  } else {
    $('img-ph').textContent = 'Nach „Testbild“ oder „GoTo + zentrieren“ erscheint hier die letzte Aufnahme';
  }
  if (src !== imgSrc) {
    imgSrc = src;
    $('img').hidden = !src;
    $('img-ph').hidden = !!src;
    if (src) $('img').src = src; else $('img').removeAttribute('src');
  }
}

/* ---------------------------------------------------------------- Status, Steuern, Aktionen */
function renderStatus(d) {
  $('st-name').textContent = d.name;
  $('st-conn').textContent = d.online ? 'verbunden' : 'getrennt';
  $('st-conn').className = 'pill' + (d.online ? ' on' : '');
  $('facts').innerHTML = (d.facts || []).map((f) => `<div class="min0"><div>${esc(f.label)}</div><div>${esc(f.value)}</div></div>`).join('');
  $('op').textContent = d.op || '–';
  $('result').textContent = d.result || '–';
  $('result').className = d.result_ok === false ? 'bad' : '';
  $('sec-move').hidden = !(d.can && d.can.move);
  const dirs = d.kind === 'seestar' ? DIRS_SEESTAR : DIRS_MOUNT;
  for (const b of document.querySelectorAll('.padbtn[data-dir]')) b.setAttribute('aria-label', 'Nach ' + dirs[b.dataset.dir]);
  $('move-hint').textContent = d.kind === 'seestar' && d.arm_closed ? 'Arm eingeklappt – erst ausfahren' : 'gedrückt halten · loslassen stoppt';
  $('speeds').innerHTML = SPEEDS.map((s, i) => `<button type="button" class="pickbtn" data-i="${i}" aria-pressed="${i === S.speed}">${s}</button>`).join('');
  for (const b of $('speeds').querySelectorAll('.pickbtn')) b.onclick = () => { S.speed = +b.dataset.i; store.set('speed', S.speed); renderStatus(d); };
  $('actions').innerHTML = (d.actions || []).map((a, i) => `
    <button type="button" class="act${a.danger ? ' danger' : a.primary ? ' primary' : ''}" data-i="${i}" ${a.disabled ? 'disabled' : ''} title="${esc(a.disabled)}">
      <span>${esc(a.label)}</span><span>${esc(a.disabled || a.hint)}</span></button>`).join('');
  for (const b of $('actions').querySelectorAll('.act')) {
    b.onclick = async () => {
      const a = d.actions[+b.dataset.i];
      if (a.confirm && !(await ask(a.confirm))) return;
      await api(`/api/dev/${d.id}/action`, { action: a.id }).catch(() => {});
      poll();
    };
  }
  $('stel').innerHTML = d.stellarium_port ? `Stellarium: „Externe Software“ → <span class="mono">localhost:${d.stellarium_port}</span> · JNow · Strg+1 fährt hin${d.stellarium_clients ? ' · <b>verbunden</b>' : ''}` : '';
}

async function hold(dir, on) {
  const d = cur();
  if (!d) return;
  if (on && S.held === dir) return;
  S.held = on ? dir : null;
  for (const b of document.querySelectorAll('.padbtn[data-dir]')) b.classList.toggle('held', on && b.dataset.dir === dir);
  try { await api(`/api/dev/${d.id}/move`, { dir, speed: S.speed, on }); } catch (e) { S.held = null; }
}
for (const b of document.querySelectorAll('.padbtn[data-dir]')) {
  b.addEventListener('pointerdown', (e) => { b.setPointerCapture(e.pointerId); hold(b.dataset.dir, true); });
  b.addEventListener('pointerup', () => hold(b.dataset.dir, false));
  b.addEventListener('pointercancel', () => hold(b.dataset.dir, false));
}
$('pad-stop').onclick = () => { const d = cur(); if (d) api(`/api/dev/${d.id}/move`, { dir: 'N', on: false }).catch(() => {}); };
const KEYS = { ArrowUp: 'N', ArrowDown: 'S', ArrowLeft: 'W', ArrowRight: 'E' };
function typing() { const el = document.activeElement; return el && ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName); }
document.addEventListener('keydown', (e) => {
  if (!KEYS[e.key] || typing() || !$('modal').hidden) return;
  e.preventDefault();
  if (!e.repeat) hold(KEYS[e.key], true);
});
document.addEventListener('keyup', (e) => {
  if (!KEYS[e.key] || typing()) return;
  e.preventDefault();
  hold(KEYS[e.key], false);
});
window.addEventListener('blur', () => { if (S.held) hold(S.held, false); });

/* ---------------------------------------------------------------- Zeichnen */
let lastTab = null;
function render() {
  const st = S.state;
  if (!st) return;
  renderHead(st);
  renderDevices(st);
  const d = cur();
  if (!d) return;
  if (d.id !== lastTab) { lastTab = d.id; renderTargets(); }
  renderSel();
  renderRig(d);
  renderSeries(d);
  renderImage(d);
  if (!document.querySelector('.act:focus')) renderStatus(d);
}

poll();
loadTonight();
setInterval(poll, 1500);
setInterval(loadTonight, 5 * 60 * 1000);
