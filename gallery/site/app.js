// Astro-Galerie – Frontend. Liest catalog.json (erzeugt von gallery/build/gallery_build.py).
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const $ = (id) => document.getElementById(id);
  const KIND = { galaxy: "Galaxien", nebula: "Nebel", cluster: "Sternhaufen", wide: "Weitwinkel", other: "Sonstiges" };
  const KIND1 = { galaxy: "Galaxie", nebula: "Nebel", cluster: "Sternhaufen", wide: "Weitwinkel", other: "Objekt" };
  const DAY = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
  const MONTH = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

  // ---------------- Formatierung ----------------
  const de = (n) => n.toLocaleString("de-DE");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtInt = (s) => { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`; };
  const d12 = (iso) => new Date(iso + "T12:00:00");
  const THIS_YEAR = String(new Date().getFullYear());
  // Jahr nur anzeigen, wenn es nicht das aktuelle ist (27.10. bzw. 27.10.2024)
  const fmtDate = (iso) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y === THIS_YEAR ? "" : y}`; };
  const fmtDay = (iso) => `${DAY[d12(iso).getDay()]} ${fmtDate(iso)}`;
  const slug = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  // ---------------- TAN-WCS (wie gallery/build/wcs.py) ----------------
  const D2R = Math.PI / 180, R2D = 180 / Math.PI;
  function inv2([[a, b], [c, d]]) { const det = a * d - b * c; return [[d / det, -b / det], [-c / det, a / det]]; }
  function w2p(ra, dec, w) {
    const ra0 = w.crval[0] * D2R, dec0 = w.crval[1] * D2R, r = ra * D2R, d = dec * D2R, dra = r - ra0;
    const cosc = Math.sin(dec0) * Math.sin(d) + Math.cos(dec0) * Math.cos(d) * Math.cos(dra);
    if (cosc <= 0) return null;
    const xi = Math.cos(d) * Math.sin(dra) / cosc * R2D;
    const eta = (Math.cos(dec0) * Math.sin(d) - Math.sin(dec0) * Math.cos(d) * Math.cos(dra)) / cosc * R2D;
    const iv = inv2(w.cd);
    return [w.crpix[0] + iv[0][0] * xi + iv[0][1] * eta, w.crpix[1] + iv[1][0] * xi + iv[1][1] * eta];
  }
  function p2w(x, y, w) {
    const dx = x - w.crpix[0], dy = y - w.crpix[1];
    const xi = (w.cd[0][0] * dx + w.cd[0][1] * dy) * D2R, eta = (w.cd[1][0] * dx + w.cd[1][1] * dy) * D2R;
    const ra0 = w.crval[0] * D2R, dec0 = w.crval[1] * D2R, rho = Math.hypot(xi, eta);
    if (!rho) return [w.crval[0], w.crval[1]];
    const c = Math.atan(rho);
    const dec = Math.asin(Math.cos(c) * Math.sin(dec0) + eta * Math.sin(c) * Math.cos(dec0) / rho);
    const ra = ra0 + Math.atan2(xi * Math.sin(c), rho * Math.cos(dec0) * Math.cos(c) - eta * Math.sin(dec0) * Math.sin(c));
    return [((ra * R2D) % 360 + 360) % 360, dec * R2D];
  }

  // ---------------- Mondphasen (mittlerer synodischer Monat, ±1 Tag) ----------------
  function moonPhases(from, to) {
    const syn = 29.530588853, ref = Date.UTC(2000, 0, 6, 18, 14) / 864e5;   // Neumond 6.1.2000
    const out = [], a = from.getTime() / 864e5, b = to.getTime() / 864e5;
    for (let n = Math.floor((a - ref) / syn) - 1; ref + n * syn <= b + syn; n++) {
      [[0, "new"], [0.5, "full"]].forEach(([f, t]) => { const d = ref + (n + f) * syn; if (d >= a && d <= b) out.push([new Date(d * 864e5), t]); });
    }
    return out;
  }

  // ---------------- SVG-Helfer ----------------
  const el = (n, a = {}, p) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };

  // ---------------- Daten ----------------
  let groups = [], kindFilter = "all", sortBy = "recent", sample = false;
  const isCombined = (e) => (e.filter || "").includes("+");

  function groupEntries(entries) {
    const m = new Map();
    for (const e of entries) {
      // ein Eintrag je Objekt; Filter, Naechte und Kombination sind Versionen darin
      const key = slug(e.target.name);
      if (!m.has(key)) m.set(key, { key, target: e.target, entries: [] });
      m.get(key).entries.push(e);
    }
    for (const g of m.values()) {
      // Titelbild: Kombination aller Filter, dann Master mit der laengsten Belichtung, sonst die neueste Nacht
      const rank = (e) => (isCombined(e) ? 0 : e.stack === "master" ? 1 : 2);
      g.entries.sort((a, b) => rank(a) - rank(b) || (rank(a) < 2 ? b.seconds - a.seconds : (b.last || "").localeCompare(a.last || "")));
      g.primary = g.entries[0];
      g.filter = g.primary.filter;
      g.nights = [...new Map(g.entries.flatMap((e) => e.nights).map((n) => [n.date, n])).values()].sort((a, b) => a.date.localeCompare(b.date));
      g.seconds = g.primary.seconds; g.last = g.nights.at(-1)?.date || "";
      g.kind = KIND[g.target.kind] ? g.target.kind : "other";
    }
    return [...m.values()];
  }

  // ---------------- Uebersicht ----------------
  function renderStats() {
    const nights = new Set(groups.flatMap((g) => g.nights.map((n) => n.date)));
    const frames = groups.reduce((s, g) => s + g.primary.frames, 0), secs = groups.reduce((s, g) => s + g.seconds, 0);
    $("stats").innerHTML = [[groups.length, "Objekte"], [nights.size, "Beobachtungsnächte"], [de(frames), "Einzelframes"], [fmtInt(secs), "Gesamtbelichtung"]]
      .map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
  }

  function renderCal() {
    const all = groups.flatMap((g) => g.nights.map((n) => ({ ...n, g })));
    if (!all.length) { $("cal-box").hidden = true; return; }
    const dates = all.map((n) => n.date).sort();
    const s0 = d12(dates[0]), s1 = d12(dates.at(-1));
    const start = new Date(s0.getFullYear(), s0.getMonth(), 1, 12), end = new Date(s1.getFullYear(), s1.getMonth() + 1, 0, 12);
    const days = Math.round((end - start) / 864e5) + 1;
    // Kurzer Zeitraum: auf 900 Einheiten verteilen. Langer Zeitraum: fester Tagesabstand und
    // waagerecht scrollen – das SVG wird dann 1:1 gezeichnet statt hochskaliert.
    const long = days > 100, step = long ? 6 : 860 / Math.max(days - 1, 1), x0 = 20, x1 = x0 + step * (days - 1);
    const W = Math.ceil(x1 + 20);
    const X = (d) => x0 + Math.round((d - start) / 864e5) * step;
    const per = {};
    all.forEach((n) => (per[n.date] ||= []).push(n));
    const maxPer = Math.max(...Object.values(per).map((l) => l.length));
    const moonY = 24 - (maxPer - 1) * 9 - 14, top = moonY - 6;
    const svg = $("cal"); svg.innerHTML = ""; svg.setAttribute("viewBox", `0 ${top} ${W} ${58 - top}`);
    svg.style.width = long ? W + "px" : "100%";
    svg.style.minWidth = long ? W + "px" : "";
    for (let i = 0; i < days; i++) {
      const d = new Date(start.getTime() + i * 864e5), x = x0 + i * step, first = d.getDate() === 1;
      if (long) {
        if (!first) continue;
        el("line", { x1: x, y1: 26, x2: x, y2: 36, class: "tick" }, svg);
        el("text", { x: x + 3, y: 50 }, svg).textContent = `${MONTH[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
        continue;
      }
      el("line", { x1: x, y1: 30, x2: x, y2: first ? 20 : 34, class: "tick" }, svg);
      if (first || (d.getDate() % 5 === 0 && d.getDate() < 30)) el("text", { x, y: 52, "text-anchor": "middle" }, svg).textContent = first ? `1. ${MONTH[d.getMonth()]}` : d.getDate();
    }
    el("line", { x1: x0, y1: 32, x2: x1, y2: 32, class: "tick" }, svg);
    moonPhases(start, end).forEach(([d, t]) => el("circle", { cx: X(d), cy: moonY, r: long ? 3 : 4, class: t === "full" ? "moonfull" : "moonnew" }, svg));
    Object.entries(per).forEach(([d, list]) => list.forEach((n, i) => {
      const c = el("circle", { cx: X(d12(d)), cy: 24 - i * 9, r: 4, class: "night" + (n.g.filter === "LP" ? " lp" : "") }, svg);
      el("title", {}, c).textContent = `${fmtDay(d)} – ${n.g.target.name}, ${n.frames} Frames`;
    }));
    if (long) svg.parentElement.scrollLeft = svg.parentElement.scrollWidth;   // neueste Naechte zuerst sichtbar
    const m0 = MONTH[start.getMonth()], m1 = MONTH[end.getMonth()], y0 = start.getFullYear(), y1 = end.getFullYear();
    $("cal-title").textContent = `Beobachtungsnächte · ${y0 !== y1 ? `${m0} ${y0} – ${m1} ${y1}` : m0 === m1 ? `${m0} ${y1}` : `${m0} – ${m1} ${y1}`}`;
  }

  function renderChips() {
    const counts = groups.reduce((m, g) => ((m[g.kind] = (m[g.kind] || 0) + 1), m), {});
    const items = [["all", "Alle", groups.length], ...Object.entries(KIND).filter(([k]) => counts[k]).map(([k, l]) => [k, l, counts[k]])];
    const box = $("chips");
    box.innerHTML = items.map(([k, l, n]) => `<button class="chip" data-k="${k}" aria-pressed="${k === kindFilter}">${l}<small>${n}</small></button>`).join("");
    box.querySelectorAll(".chip").forEach((b) => (b.onclick = () => { kindFilter = b.dataset.k; renderChips(); renderGrid(); }));
  }

  function annotationSvg(entry, onlyMain) {
    const { w, h } = entry.image, svg = el("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "xMidYMid meet", "aria-hidden": "true" });
    const g = el("g", {}, svg);
    entry.annotations.filter((a) => !onlyMain || a.main).forEach((a) => drawMarker(g, a, w));
    return svg;
  }

  function renderGrid() {
    const list = groups.filter((g) => kindFilter === "all" || g.kind === kindFilter);
    list.sort(sortBy === "recent" ? (a, b) => b.last.localeCompare(a.last) : sortBy === "exposure" ? (a, b) => b.seconds - a.seconds
      : (a, b) => (a.target.sort ?? 9e9) - (b.target.sort ?? 9e9) || a.target.name.localeCompare(b.target.name));
    const grid = $("grid"); grid.innerHTML = "";
    if (!list.length) { grid.innerHTML = `<p class="empty">Keine Objekte in dieser Kategorie.</p>`; return; }
    for (const g of list) {
      const e = g.primary, b = document.createElement("button");
      b.className = "tile"; b.setAttribute("aria-label", `${g.target.name} ${g.target.common || ""} öffnen`);
      b.innerHTML = `<div class="thumb"><img loading="lazy" src="${esc(e.image.thumb)}" alt=""></div>
        <div class="tile-t"><h3>${esc(g.target.name)} <em>${esc(g.target.common || "")}</em></h3>${g.filter ? `<span class="badge${g.filter === "LP" ? " lp" : ""}">${esc(g.filter)}</span>` : ""}</div>
        <div class="tile-m">${g.seconds ? `<span><b>${fmtInt(g.seconds)}</b> Belichtung</span>` : `<span>App-Stack</span>`}<span><b>${g.nights.length}</b> ${g.nights.length === 1 ? "Nacht" : "Nächte"}</span><span>zuletzt <b>${fmtDate(g.last)}</b></span></div>`;
      const svg = annotationSvg(e, true); svg.setAttribute("preserveAspectRatio", "xMidYMid slice"); svg.classList.add("dso"); b.querySelector(".thumb").appendChild(svg);
      b.onclick = () => openGroup(g.key);
      grid.appendChild(b);
    }
  }

  // ---------------- Marker ----------------
  const frame = $("frame"), card = $("card"), ovg = $("ovg"), svgD = $("ov");

  function drawMarker(parent, a, imgW, interactive) {
    const k = imgW / 1920, grp = el("g", { class: `mk ${a.category === "star" ? "star" : "dso"}` }, parent);
    if (a.rx > 0) el("ellipse", { cx: a.x, cy: a.y, rx: a.rx, ry: a.ry, transform: `rotate(${a.rot} ${a.x} ${a.y})`, class: a.dashed ? "shape big" : "shape" }, grp);
    else el("circle", { cx: a.x, cy: a.y, r: 18 * k, class: "shape" }, grp);
    const lab = a.label || (a.category === "star" || a.small ? [Math.max(a.rx, 14 * k) + 16 * k, -Math.max(a.ry, 14 * k) - 10 * k] : [Math.min(Math.max(a.rx * 0.7, 40 * k), 260 * k), -Math.min(Math.max(a.ry * 0.9, 60 * k), 220 * k)]);
    const lx = a.x + lab[0], ly = a.y + lab[1];
    el("line", { x1: a.x, y1: a.y, x2: lx, y2: ly + (lab[1] < 0 ? 8 * k : -30 * k), class: "lead" }, grp);
    const t = el("text", { x: lx, y: ly, class: "lbl", "text-anchor": lab[0] < 0 ? "end" : "start" }, grp);
    t.style.fontSize = (a.category === "star" ? 23 : a.small ? 27 : 46) * k * (interactive ? 1 : 2.4) + "px";
    t.textContent = a.name;
    if (interactive) {
      el("circle", { cx: a.x, cy: a.y, r: a.rx ? Math.max(40 * k, Math.min(a.ry, 160 * k)) : 30 * k, class: "hit" }, grp);
      grp.addEventListener("pointerenter", () => showCard(a, grp));
      grp.addEventListener("pointerleave", hideCard);
      grp.addEventListener("click", (ev) => { ev.stopPropagation(); showCard(a, grp); });
    }
    return grp;
  }

  function drawOff(parent, o, w, h) {
    const k = w / 1920, grp = el("g", { class: "dso offscreen" }, parent);
    let { x, y } = o, d, tx, ty, anchor = "start";
    const a = 14 * k, m = 46 * k, tip = 22 * k;
    if (y > h || y < 0) {
      x = Math.min(Math.max(x, 60 * k), w - 60 * k);
      d = y > h ? `M${x - a} ${h - m} L${x + a} ${h - m} L${x} ${h - tip} Z` : `M${x - a} ${m} L${x + a} ${m} L${x} ${tip} Z`;
      if (x > w / 2) { tx = x - 24 * k; anchor = "end"; } else tx = x + 24 * k;
      ty = y > h ? h - 28 * k : 50 * k;
    } else if (x > w) {
      y = Math.min(Math.max(y, 60 * k), h - 60 * k); d = `M${w - m} ${y - a} L${w - m} ${y + a} L${w - tip} ${y} Z`; tx = w - 56 * k; ty = y + 9 * k; anchor = "end";
    } else {
      y = Math.min(Math.max(y, 60 * k), h - 60 * k); d = `M${m} ${y - a} L${m} ${y + a} L${tip} ${y} Z`; tx = 56 * k; ty = y + 9 * k;
    }
    el("path", { d }, grp);
    const t = el("text", { x: tx, y: ty, class: "lbl", "text-anchor": anchor }, grp);
    t.style.fontSize = 28 * k + "px"; t.textContent = `${o.name} · ${o.dist_arcmin}′ außerhalb`;
  }

  function drawConstellations(parent, e) {
    const list = e.constellations || []; if (!list.length) return;
    const k = e.image.w / 1920, g = el("g", { class: "constl" }, parent);
    list.forEach((c) => {
      c.paths.forEach((p) => el("polyline", { points: p.map((q) => q.join(",")).join(" ") }, g));
      if (c.label) { const t = el("text", { x: c.label[0], y: c.label[1], "text-anchor": "middle" }, g); t.style.fontSize = 30 * k + "px"; t.textContent = c.name; }
    });
  }

  function drawGrid(parent, e) {
    const { w, h } = e.image, wcs = e.wcs, gG = el("g", { class: "gridl" }, parent), k = w / 1920;
    const corners = [[0, 0], [w, 0], [0, h], [w, h], [w / 2, h / 2]].map(([x, y]) => p2w(x, y, wcs));
    let ras = corners.map((c) => c[0]); const decs = corners.map((c) => c[1]);
    const wrap = Math.max(...ras) - Math.min(...ras) > 180; if (wrap) ras = ras.map((r) => (r > 180 ? r - 360 : r));
    const dec0 = corners[4][1], fovDeg = e.solve.fov[0];
    const decStep = [5, 10, 15, 20, 30, 60].map((m) => m / 60).find((s) => fovDeg / s <= 7) || 1;
    const raStep = [1, 2, 5, 10, 15, 20, 30, 60].map((m) => m / 4).find((s) => (fovDeg / Math.max(Math.cos(dec0 * D2R), 0.05)) / s <= 7) || 15;
    const line = (pts, label, mode) => {
      const px = pts.map(([r, d]) => w2p(r, d, wcs)).filter(Boolean);
      if (px.length < 2) return;
      el("polyline", { points: px.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ") }, gG);
      const inside = px.filter(([x, y]) => x > 10 && x < w - 10 && y > 10 && y < h - 10);
      if (inside.length) { const [lx, ly] = inside.reduce((b, p) => (mode === "left" ? p[0] < b[0] : p[1] < b[1]) ? p : b);
        const t = el("text", { x: lx + 6 * k, y: ly - 6 * k }, gG); t.style.fontSize = 17 * k + "px"; t.textContent = label; }
    };
    const rMin = Math.min(...ras) - raStep, rMax = Math.max(...ras) + raStep, dMin = Math.min(...decs) - decStep, dMax = Math.max(...decs) + decStep;
    for (let d = Math.ceil(dMin / decStep) * decStep; d <= dMax; d += decStep) {
      const pts = []; for (let i = 0; i <= 40; i++) pts.push([rMin + (rMax - rMin) * i / 40, d]);
      const a = Math.abs(d), deg = Math.floor(a + 1e-9), min = Math.round((a - deg) * 60);
      line(pts, `${d < 0 ? "−" : "+"}${deg}°${String(min).padStart(2, "0")}′`, "left");
    }
    for (let r = Math.ceil(rMin / raStep) * raStep; r <= rMax; r += raStep) {
      const pts = []; for (let i = 0; i <= 40; i++) pts.push([(r + 360) % 360, dMin + (dMax - dMin) * i / 40]);
      const hh = ((r + 360) % 360) / 15, H = Math.floor(hh + 1e-9), M = Math.round((hh - H) * 60);
      line(pts, `${H}h${String(M).padStart(2, "0")}m`, "top");
    }
  }

  function showCard(a, grp) {
    ovg.querySelectorAll(".mk.active").forEach((m) => m.classList.remove("active")); grp.classList.add("active");
    card.innerHTML = `<h3>${esc(a.name)}</h3><p class="type">${esc(a.type)}${a.alt ? " · " + esc(a.alt) : ""}</p>
      <dl><dt>Helligkeit</dt><dd>${esc(a.mag)}${a.mag !== "–" ? " mag" : ""}</dd><dt>Größe</dt><dd>${esc(a.size)}</dd><dt>Entfernung</dt><dd>${esc(a.dist)}</dd></dl>`;
    card.hidden = false;
    const fr = frame.getBoundingClientRect(), b = grp.getBoundingClientRect();
    let left = b.right - fr.left + 12, top = b.top - fr.top;
    if (left + card.offsetWidth > fr.width - 8) left = Math.max(8, b.left - fr.left - card.offsetWidth - 12);
    top = Math.min(Math.max(8, top), fr.height - card.offsetHeight - 8);
    card.style.left = left + "px"; card.style.top = top + "px";
  }
  function hideCard() { card.hidden = true; ovg.querySelectorAll(".mk.active").forEach((m) => m.classList.remove("active")); }

  // ---------------- Detail ----------------
  function openGroup(key, entryId) {
    const g = groups.find((x) => x.key === key); if (!g) return;
    const e = g.entries.find((x) => x.id === entryId) || g.primary;
    $("v-list").hidden = true; $("v-detail").hidden = false; $("d-sample").hidden = !e.sample;
    if (location.hash.slice(1) !== key) history.pushState(null, "", "#" + key);
    window.scrollTo(0, 0);
    const nN = e.nights.length;
    $("d-eyebrow").textContent = `${KIND1[g.kind]} · ${isCombined(e) ? `Alle Filter (${e.filter.replaceAll("+", " + ")})` : e.stack === "master" ? `Master aus ${nN} Nächten` : "Einzelnacht"} · ${e.device?.label || "Seestar S50"}`;
    $("d-title").innerHTML = `${esc(g.target.name)} <em>${esc(g.target.common || "")}</em>`;
    $("d-caption").textContent = e.caption || "";
    $("d-integ").innerHTML = e.seconds ? `${fmtInt(e.seconds)} <small>gesamt</small>` : `– <small>Belichtung unbekannt</small>`;
    $("d-exp").innerHTML = `<dt>Frames</dt><dd>${e.frames ? `${de(e.frames)} × ${e.exposure_s} s` : "–"}</dd><dt>Nächte</dt><dd>${nN}</dd><dt>Filter</dt><dd>${esc(e.filter)}</dd>` + (e.gain != null ? `<dt>Gain</dt><dd>${e.gain}</dd>` : "");
    const max = Math.max(...e.nights.map((n) => n.frames), 1);
    $("d-nights").innerHTML = e.nights.map((n) => `<li><span>${fmtDay(n.date)}</span><span class="bar${e.filter === "LP" ? " lp" : ""}"><i style="width:${(n.frames / max * 100).toFixed(1)}%"></i></span><span class="n">${n.frames}</span></li>`).join("");
    const s = e.solve;
    $("d-solve").innerHTML = !s ? `<dt>Status</dt><dd>nicht gelöst</dd>` : `<dt>Bildmitte RA</dt><dd>${s.ra}</dd><dt>Bildmitte Dec</dt><dd>${s.dec}</dd><dt>Bildfeld</dt><dd>${String(s.fov[0]).replace(".", ",")}° × ${String(s.fov[1]).replace(".", ",")}°</dd><dt>Pixelmaßstab</dt><dd>${String(s.pixscale).replace(".", ",")}″/px</dd><dt>Norden</dt><dd>${s.north_txt}</dd>`;
    const vs = $("d-versions"); vs.innerHTML = "";
    g.entries.forEach((x) => {
      const li = document.createElement("li"); if (x.id === e.id) li.className = "cur";
      const multi = new Set(g.entries.map((y) => y.device?.id || "s50")).size > 1;
      const filters = new Set(g.entries.map((y) => y.filter || "")).size > 1;
      const label = (x.source === "app" ? "Seestar-App-Stack" : isCombined(x) ? "Alle Filter · Siril" : x.stack === "master" ? "Master · Siril" : "Einzelnacht · Siril")
        + (filters && !isCombined(x) && x.filter ? ` · ${esc(x.filter)}` : "")
        + (multi ? ` · ${esc(x.device?.label || "Seestar S50")}` : "");
      const info = x.stack === "master" ? `${x.nights.length} Nächte` : fmtDate(x.last);
      li.innerHTML = x.id === e.id ? `<span>${label}</span><span>${info}</span>` : `<button type="button">${label}</button><span>${info}</span>`;
      li.querySelector("button")?.addEventListener("click", () => openGroup(key, x.id));
      vs.appendChild(li);
    });
    (e.versions || []).forEach((v) => { const li = document.createElement("li"); li.innerHTML = `<span>${esc(v.label)}</span><span>${esc(v.where)}</span>`; vs.appendChild(li); });

    // Bild + Overlay
    const { w, h } = e.image, img = $("d-img");
    img.src = e.image.web; img.alt = `${g.target.name} ${g.target.common || ""}`;
    frame.style.aspectRatio = `${w} / ${h}`;
    frame.style.width = `min(100%, calc(78vh * ${(w / h).toFixed(4)}))`;   // Hochformat nicht hoeher als der Bildschirm
    svgD.setAttribute("viewBox", `0 0 ${w} ${h}`);
    ovg.innerHTML = ""; hideCard(); setPin(false);
    if (e.wcs) drawGrid(ovg, e);
    $("t-grid").hidden = !e.wcs;
    svgD.classList.add("hide-grid"); $("t-grid").setAttribute("aria-pressed", "false");
    drawConstellations(ovg, e);
    $("t-const").hidden = !(e.constellations || []).length;
    e.annotations.forEach((a) => drawMarker(ovg, a, w, true));
    e.offscreen.forEach((o) => drawOff(ovg, o, w, h));
    $("t-star").hidden = !e.annotations.some((a) => a.category === "star");
    if (!s) return;
    // Kompass + Massstab
    const k = w / 1920, c = el("g", { class: "compass" }, ovg), ox = w - 120 * k, oy = 110 * k, L = 70 * k, N = s.north, E = s.east;
    el("line", { x1: ox, y1: oy, x2: ox + N[0] * L, y2: oy + N[1] * L }, c);
    el("line", { x1: ox, y1: oy, x2: ox + E[0] * L * 0.7, y2: oy + E[1] * L * 0.7 }, c);
    [["N", N, L + 18 * k], ["O", E, L * 0.7 + 18 * k]].forEach(([t, v, r]) => {
      const tx = el("text", { x: ox + v[0] * r - 6 * k, y: oy + v[1] * r + 7 * k }, c); tx.style.fontSize = 20 * k + "px"; tx.textContent = t; });
    const wideBar = s.fov[0] >= 4, sb = el("g", { class: "scalebar" }, ovg), len = (wideBar ? 300 : 10) * 60 / s.pixscale;
    el("line", { x1: 60 * k, y1: h - 40 * k, x2: 60 * k + len, y2: h - 40 * k }, sb);
    const st = el("text", { x: 60 * k, y: h - 52 * k }, sb); st.style.fontSize = 18 * k + "px"; st.textContent = wideBar ? "5°" : "10′";
  }

  function showList() { $("v-detail").hidden = true; $("v-list").hidden = false; }
  $("back").onclick = () => { history.pushState(null, "", location.pathname + location.search); showList(); };
  window.addEventListener("popstate", () => { const k = location.hash.slice(1); k ? openGroup(k) : showList(); });

  // ---------------- Bedienung ----------------
  const pinBtn = $("t-pin");
  function setPin(v) { frame.classList.toggle("pinned", v); pinBtn.setAttribute("aria-pressed", v); if (!v) hideCard(); }
  pinBtn.onclick = () => setPin(!frame.classList.contains("pinned"));
  let lastPointer = "mouse";
  frame.addEventListener("pointerdown", (e) => { lastPointer = e.pointerType || "mouse"; });
  frame.addEventListener("click", () => { if (lastPointer !== "mouse") setPin(!frame.classList.contains("pinned")); else hideCard(); });
  frame.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPin(!frame.classList.contains("pinned")); } });
  const toggle = (id, cls) => { const b = $(id); b.onclick = () => { const on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", on); svgD.classList.toggle(cls, !on); }; };
  toggle("t-dso", "hide-dso"); toggle("t-star", "hide-star"); toggle("t-grid", "hide-grid"); toggle("t-const", "hide-const");
  document.querySelectorAll(".red-t").forEach((b) => (b.onclick = () => {
    const on = !document.documentElement.classList.contains("redlight");
    document.documentElement.classList.toggle("redlight", on);
    document.querySelectorAll(".red-t").forEach((x) => x.setAttribute("aria-pressed", on));
    try { localStorage.setItem("astro-redlight", on ? "1" : "0"); } catch (e) { /* egal */ }
  }));
  try { if (localStorage.getItem("astro-redlight") === "1") document.querySelector(".red-t").click(); } catch (e) { /* egal */ }
  $("sort").onchange = (e) => { sortBy = e.target.value; renderGrid(); };

  // ---------------- Start ----------------
  fetch("catalog.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((cat) => {
      groups = groupEntries(cat.entries || []);
      sample = groups.some((g) => g.primary.sample);
      $("sample-badge").hidden = !sample;
      if (!groups.length) { $("status").textContent = "Noch keine Stacks vorhanden."; return; }
      $("status").hidden = true;
      ["stats", "cal-box", "filterbar"].forEach((id) => ($(id).hidden = false));
      renderStats(); renderCal(); renderChips(); renderGrid();
      const k = location.hash.slice(1); if (k) openGroup(k);
    })
    .catch((err) => { const s = $("status"); s.classList.add("err"); s.textContent = `catalog.json konnte nicht geladen werden (${err.message}).`; });
})();
