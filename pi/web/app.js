/* Astro-Pi Web-App – Montierung, Kamera, Fokus, Status. Spricht mit astro_pi.server (REST + WebSocket). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let st = null, sel = null, hfrHist = [], focusLoop = false, liveTimer = null;

  // ---------------- Hilfen ----------------
  const hms = (h) => { if (h == null) return "–"; h = ((h % 24) + 24) % 24; const H = Math.floor(h), m = Math.floor((h - H) * 60), s = Math.round(((h - H) * 60 - m) * 60);
    return `${String(H).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${String(s % 60).padStart(2, "0")}s`; };
  const dms = (d) => { if (d == null) return "–"; const sg = d < 0 ? "−" : "+"; d = Math.abs(d); const D = Math.floor(d), m = Math.floor((d - D) * 60);
    return `${sg}${String(D).padStart(2, "0")}° ${String(m).padStart(2, "0")}′`; };
  function toast(msg, err = false) { const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), err ? 6000 : 2500); }
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  const act = (fn, okMsg) => async (...a) => { try { await fn(...a); if (okMsg) toast(okMsg); } catch (e) { toast(e.message, true); } };

  // ---------------- Tabs, Rotlicht ----------------
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + b.dataset.tab));
    try { localStorage.setItem("tab", b.dataset.tab); } catch { /* egal */ }
  }));
  try { const t = localStorage.getItem("tab"); if (t) document.querySelector(`.tabs [data-tab="${t}"]`)?.click(); } catch { /* egal */ }
  const setRed = (on) => { document.documentElement.classList.toggle("red", on); $("red").setAttribute("aria-pressed", on);
    try { localStorage.setItem("red", on ? "1" : ""); } catch { /* egal */ } };
  try { setRed(localStorage.getItem("red") === "1"); } catch { /* egal */ }
  $("red").onclick = () => setRed(!document.documentElement.classList.contains("red"));
  $("stop").onclick = act(() => api("/api/abort", {}), "Gestoppt");
  $("vnc").href = `${location.protocol}//${location.hostname}:6080/vnc.html?autoconnect=1&resize=scale`;

  // ---------------- Zustand ----------------
  function render(s) {
    st = s;
    const m = s.mount, c = s.camera;
    $("conn").textContent = s.indi ? "verbunden" : "INDI getrennt";
    $("conn").className = "pill " + (s.indi ? "ok" : "off");
    $("m-ra").textContent = hms(m.ra); $("m-dec").textContent = dms(m.dec);
    $("m-alt").textContent = m.alt == null ? "–" : `${m.alt.toFixed(1)}°`;
    $("m-target").textContent = m.target || "–";
    const ms = !m.connected ? ["getrennt", "off"] : m.slewing ? ["schwenkt", "busy"] : m.parked ? ["geparkt", ""] : m.tracking ? ["führt nach", "ok"] : ["steht", ""];
    $("m-state").textContent = ms[0]; $("m-state").className = "pill " + ms[1];
    $("track").textContent = m.tracking ? "Nachführung aus" : "Nachführung an";
    if (!$("s-target").value && m.target) $("s-target").placeholder = m.target;

    const isoSel = $("s-iso");
    if (c.iso_options.length && isoSel.options.length - 1 !== c.iso_options.length) {
      isoSel.innerHTML = `<option value="">wie Kamera${c.iso ? ` (${esc(c.iso)})` : ""}</option>` + c.iso_options.map((o) => `<option>${esc(o)}</option>`).join("");
    }
    const se = s.series, pr = $("s-progress");
    pr.hidden = !(se.running || se.done);
    if (!pr.hidden) {
      const pct = se.count > 0 ? (100 * se.done) / se.count : 0;
      pr.firstElementChild.style.width = `${pct}%`;
      pr.lastElementChild.textContent = `${se.target || "–"}: ${se.done}${se.count > 0 ? ` / ${se.count}` : ""} × ${se.exposure}s${se.running ? (c.exposing ? " · belichtet …" : " · läuft") : se.error ? ` · Fehler: ${se.error}` : " · fertig"}`;
    }
    $("s-start").disabled = se.running; $("s-stop").disabled = !se.running;
    if (s.preview && s.preview.time && s.preview.time !== render.pt) {
      render.pt = s.preview.time; showPreview(s.preview);
    }
    $("live-hint").hidden = c.live; $("live-img").hidden = !c.live;
    if (c.live && !liveTimer) liveTimer = setInterval(pollLive, 700);
    if (!c.live && liveTimer) { clearInterval(liveTimer); liveTimer = null; }

    $("devs").innerHTML = [["Montierung", m.device || "–", m.connected], ["Kamera", c.device || "–", c.connected]]
      .map(([k, v, ok]) => `<dt>${k}</dt><dd>${esc(v)} <span class="pill ${ok ? "ok" : "off"}">${ok ? "verbunden" : "getrennt"}</span></dd>`).join("")
      + `<dt>Speicher frei</dt><dd>${s.disk_free_gb ?? "–"} GB</dd>`
      + `<dt>Standort</dt><dd>${s.location.lat != null ? `${s.location.lat.toFixed(2)}°, ${s.location.lon.toFixed(2)}°` : "nicht gesetzt (Höhe unbekannt)"}</dd>`;
    $("msgs").innerHTML = (s.messages || []).slice().reverse().map((x) => `<li><small>${new Date(x.t * 1000).toLocaleTimeString()} · ${esc(x.device)}</small><br>${esc(x.text)}</li>`).join("") || "<li class='muted'>keine</li>";
  }

  function showPreview(p) {
    const img = $("p-img"); img.src = `/api/preview.jpg?t=${p.time}`; img.hidden = false;
    $("p-info").textContent = `${p.exposure ?? "?"} s · ${p.hfr ? `HFR ${p.hfr.toFixed(2)} px · ${p.stars} Sterne` : "keine Sterne erkannt"}`;
    if (p.hfr) { hfrHist.push(p.hfr); hfrHist = hfrHist.slice(-30); $("hfr").textContent = `HFR ${p.hfr.toFixed(2)}`; drawHfr(); }
  }

  function drawHfr() {
    const cv = $("hfr-chart"), ctx = cv.getContext("2d"), w = (cv.width = cv.clientWidth * devicePixelRatio), h = (cv.height = 90 * devicePixelRatio);
    ctx.clearRect(0, 0, w, h);
    if (hfrHist.length < 2) return;
    const mx = Math.max(...hfrHist), mn = Math.min(...hfrHist), span = mx - mn || 1;
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent"); ctx.lineWidth = 2 * devicePixelRatio;
    ctx.beginPath();
    hfrHist.forEach((v, i) => { const x = (i / (hfrHist.length - 1)) * (w - 10) + 5, y = h - 8 - ((v - mn) / span) * (h - 16); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
  }

  // ---------------- WebSocket ----------------
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "state") render(m.state);
      else if (m.type === "center") toast(`Zentrieren: Versuch ${m.attempt}, Abweichung ${m.error_arcmin}′`);
      else if (m.type === "done") toast(m.result?.ok === false ? `Zentrieren unvollständig (${m.result.error_arcmin}′)` : "Fertig");
      else if (m.type === "error") toast(m.message, true);
      else if (m.type === "preview") api("/api/state").then(render).catch(() => {});
    };
    ws.onclose = () => { $("conn").textContent = "Pi getrennt"; $("conn").className = "pill off"; setTimeout(connect, 2000); };
  }
  connect();

  // ---------------- Ziel suchen, GoTo ----------------
  let qTimer;
  $("q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(search, 200); });
  async function search() {
    const q = $("q").value.trim(), ul = $("hits");
    if (q.length < 2) { ul.innerHTML = ""; return; }
    try {
      const hits = await api(`/api/targets?q=${encodeURIComponent(q)}&limit=15`);
      ul.innerHTML = "";
      hits.forEach((h) => {
        const li = document.createElement("li");
        const low = h.altitude != null && h.altitude < 15;
        li.className = low ? "low" : "";
        li.innerHTML = `<span><b>${esc(h.name)}</b> ${esc(h.common || h.alt || "")}<br><small>${esc(h.type)}${h.mag != null ? ` · ${h.mag} mag` : ""}${h.major ? ` · ${h.major}′` : ""}</small></span>`
          + `<small>${h.altitude != null ? `${h.altitude.toFixed(0)}°` : ""}</small>`;
        li.onclick = () => { sel = h; ul.querySelectorAll("li").forEach((x) => x.removeAttribute("aria-selected")); li.setAttribute("aria-selected", "true");
          $("sel").hidden = false; $("sel-name").textContent = `${h.name}${h.common ? ` – ${h.common}` : ""}`;
          $("sel-info").textContent = `${hms(h.ra)} ${dms(h.dec)}${h.altitude != null ? ` · Höhe ${h.altitude.toFixed(0)}°` : ""}`; };
        ul.appendChild(li);
      });
      if (!hits.length) ul.innerHTML = "<li class='muted'>nichts gefunden</li>";
    } catch (e) { toast(e.message, true); }
  }
  const lowOk = () => !(sel.altitude != null && sel.altitude < 10) || confirm(`${sel.name} steht nur ${sel.altitude.toFixed(0)}° hoch. Trotzdem anfahren?`);
  $("goto").onclick = act(async () => { if (!sel || !lowOk()) return; await api("/api/mount/goto", { ra: sel.ra, dec: sel.dec, name: sel.name }); toast(`Fahre ${sel.name} an`); });
  $("goto-center").onclick = act(async () => { if (!sel || !lowOk()) return;
    await api("/api/mount/goto", { ra: sel.ra, dec: sel.dec, name: sel.name, center: true, exposure: 5 }); toast(`Fahre ${sel.name} an und zentriere …`); });

  // ---------------- Manuell ----------------
  $("rate").onchange = act(() => api("/api/mount/move", { rate: +$("rate").value }));
  document.querySelectorAll(".pad [data-dir]").forEach((b) => {
    const go = (on) => api("/api/mount/move", { dir: b.dataset.dir, on }).catch((e) => toast(e.message, true));
    b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture(e.pointerId); go(true); });
    ["pointerup", "pointercancel"].forEach((t) => b.addEventListener(t, () => go(false)));
  });
  $("track").onclick = act(() => api("/api/mount/track", { on: !st?.mount.tracking }));
  $("park").onclick = act(async () => { if (confirm("Montierung parken?")) await api("/api/mount/park", { park: true }); });
  $("unpark").onclick = act(() => api("/api/mount/park", { park: false }));

  // ---------------- Kamera ----------------
  $("s-start").onclick = act(async () => {
    const b = { exposure: +$("s-exp").value, count: +$("s-count").value, delay: +$("s-delay").value,
      target: $("s-target").value.trim() || st?.mount.target || "", iso: $("s-iso").value };
    if (!b.target && !confirm("Kein Ziel gesetzt – Aufnahmen landen unter „Unbekannt“. Trotzdem starten?")) return;
    await api("/api/camera/series", b); toast("Serie gestartet");
  });
  $("s-stop").onclick = act(() => api("/api/camera/series", { stop: true }), "Serie gestoppt");
  $("s-test").onclick = act(async () => { toast("Belichte …"); await api("/api/camera/capture", { exposure: Math.min(+$("s-exp").value, 10), iso: $("s-iso").value }); });
  const showSolve = (r) => ($("solve-res").textContent = `Bildmitte ${hms(r.ra)} ${dms(r.dec)} · ${r.scale}″/px`);
  $("solve").onclick = act(async () => { $("solve-res").textContent = "löse …"; showSolve(await api("/api/solve", {})); });
  $("solve-sync").onclick = act(async () => { $("solve-res").textContent = "löse …"; showSolve(await api("/api/solve", { sync: true })); toast("Montierung synchronisiert"); });

  // ---------------- Fokus ----------------
  $("live-on").onclick = act(() => api("/api/camera/live", { on: true }));
  $("live-off").onclick = act(() => api("/api/camera/live", { on: false }));
  function pollLive() { const img = $("live-img"); if (!img.dataset.busy) { img.dataset.busy = 1; const n = new Image();
    n.onload = () => { img.src = n.src; delete img.dataset.busy; }; n.onerror = () => delete img.dataset.busy; n.src = `/api/live.jpg?t=${Date.now()}`; } }
  const applyZoom = () => { const z = +$("zoom").value, img = $("live-img"); img.style.transform = `scale(${z})`; };
  $("zoom").oninput = applyZoom;
  $("live-wrap").addEventListener("click", (e) => { const r = e.currentTarget.getBoundingClientRect();
    $("live-img").style.transformOrigin = `${((e.clientX - r.left) / r.width) * 100}% ${((e.clientY - r.top) / r.height) * 100}%`; applyZoom(); });
  const fshot = () => api("/api/camera/capture", { exposure: +$("f-exp").value });
  $("f-shot").onclick = act(fshot);
  $("f-loop").onclick = async () => {
    focusLoop = !focusLoop; $("f-loop").textContent = focusLoop ? "Schleife stoppen" : "Schleife";
    while (focusLoop) { try { await fshot(); } catch (e) { toast(e.message, true); focusLoop = false; $("f-loop").textContent = "Schleife"; } }
  };

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
})();
