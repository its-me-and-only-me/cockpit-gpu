"use strict";

/* GPU-Ansicht für Cockpit, Daten aus PCP (pmproxy/pmseries).
   amdgpu.*  kommt vom pmdaamdgpu
   mmv.*     kommt vom amdgpu-gtt-mmv-Exporter (sysfs):
             GTT = tatsächlich belegter Speicher bei Unified-Memory-APUs
             (Strix Halo); gfxclk = realer Grafiktakt.
   Historie unten wie auf /metrics: Datums-Sprungauswahl +
   Graph-Sichtbarkeits-Dropdown, darunter pro Stunde ein Block mit
   komprimierten Verlaufsgraphen (Minutenauflösung), aufklappbar. */

const PANELS = [
    { key: "amdgpu.gpu.load",      title: "GPU-Auslastung", fmt: v => v.toFixed(0) + " %",   yMax: 100, yMin: 0 },
    { key: "mmv.amdgpu.gtt_used",  title: "GTT genutzt",    fmt: fmtBytes, maxKey: "mmv.amdgpu.gtt_total" },
    { key: "amdgpu.gpu.temperature", title: "Temperatur",   fmt: v => v.toFixed(0) + " °C" },
    { key: "mmv.amdgpu.gfxclk",    title: "GPU-Takt (gfxclk)", fmt: v => v.toFixed(0) + " MHz" },
    { key: "amdgpu.gpu.average_power", title: "Leistung",   fmt: v => v.toFixed(1) + " W" },
];

const PMPROXY_PORT = 44322;
const HOUR_MS = 3600 * 1000;
const MIN_MS = 60 * 1000;
const el = id => document.getElementById(id);

function fmtBytes(v) {
    if (v >= 1073741824) return (v / 1073741824).toFixed(1) + " GiB";
    if (v >= 1048576)    return (v / 1048576).toFixed(0) + " MiB";
    return v.toFixed(0) + " B";
}

function pad(n) { return String(n).padStart(2, "0"); }
function hms(ts) { const d = new Date(ts); return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()); }
function hm(ts)  { const d = new Date(ts); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
function dateStr(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
const fmtFullDate = new Intl.DateTimeFormat("de-DE", { dateStyle: "full" });
function dayLabel(ds) { return ds === dateStr(new Date()) ? "Heute" : fmtFullDate.format(new Date(ds + "T00:00:00")); }

/* ---------- pmproxy-Zugriff: erst direkter fetch, sonst Cockpit-Bridge ---------- */

async function apiGet(path) {
    try {
        const r = await fetch("http://localhost:44322" + path, { cache: "no-store" });
        if (r.ok)
            return await r.json();
    } catch (e) { /* Fallback */ }
    if (!window.cockpit || !cockpit.http)
        throw new Error("weder fetch noch cockpit.http verfügbar");
    let req;
    try {
        req = cockpit.http({ address: "127.0.0.1", port: PMPROXY_PORT });
    } catch (e) {
        throw new Error("cockpit.http: " + e.message);
    }
    const body = await req.request({ path: path, method: "GET" });
    try {
        return JSON.parse(body);
    } catch (e) {
        throw new Error("pmproxy-Antwort kein JSON: " + String(body).slice(0, 120));
    }
}

function seriesFor(metric) {
    return apiGet("/series/query?expr=" + encodeURIComponent(metric)).then(ids => Array.isArray(ids) ? ids : []);
}

/* Werte im Fenster [startS, finishS] (Epoch-Sekunden), gemittelte Zeitpunkte */
function valuesFor(ids, startS, finishS) {
    if (!ids.length) return Promise.resolve([]);
    return Promise.all(ids.map(id =>
        apiGet("/series/values?series=" + id + "&start=" + startS + "&finish=" + finishS)
    )).then(results => {
        const out = [];
        for (const pts of results)
            for (const p of pts) {
                const num = Number(p.value);
                if (Number.isFinite(num))
                    out.push({ timestamp: Math.round(p.timestamp), value: num });
            }
        return out.sort((a, b) => a.timestamp - b.timestamp);
    });
}

async function fetchMetric(metric, startS, finishS) {
    return valuesFor(await seriesFor(metric), startS, finishS);
}

/* ---------- SVG-Diagramme ---------- */

function svgEl(ns, tag, attrs) {
    const e = document.createElementNS(ns, tag);
    for (const k in attrs)
        e.setAttribute(k, attrs[k]);
    return e;
}

/* Bereichsdiagramm; labelFmt=null → ohne Achsenbeschriftung (kompakt) */
function makeChart(container, pts, panel, height, labelFmt, yMaxOverride) {
    container.innerHTML = "";
    if (!pts.length) {
        container.innerHTML = '<div class="empty">Keine Daten.</div>';
        return;
    }
    const axis = !!labelFmt;
    const ns = "http://www.w3.org/2000/svg";
    const W = Math.max(container.clientWidth - 4, 120), H = height;
    const LP = axis ? 58 : 2, RP = axis ? 6 : 2, TP = axis ? 6 : 3, BP = axis ? 16 : 3;
    const t0 = pts[0].timestamp, t1 = pts[pts.length - 1].timestamp;
    const span = Math.max(t1 - t0, MIN_MS);
    const lo = panel.yMin !== undefined ? panel.yMin : 0;
    const hi = yMaxOverride !== undefined ? yMaxOverride
        : (panel.yMax !== undefined ? panel.yMax : Math.max(...pts.map(p => p.value), lo + 1));
    const x = ts => LP + (ts - t0) / span * (W - LP - RP);
    const y = v => H - BP - (Math.min(Math.max(v, lo), hi) - lo) / (hi - lo) * (H - TP - BP);

    const svg = svgEl(ns, "svg", { width: W, height: H });
    const ticks = H > 120 ? 4 : 2;
    for (let i = 0; i <= ticks; i++) {
        const v = lo + i / ticks * (hi - lo);
        svg.appendChild(svgEl(ns, "line", { x1: LP, x2: W - RP, y1: y(v), y2: y(v), class: "gridline" }));
        if (axis) {
            const lab = svgEl(ns, "text", { x: LP - 6, y: y(v) + 3, "text-anchor": "end", class: "axis" });
            lab.textContent = panel.fmt(v);
            svg.appendChild(lab);
        }
    }
    if (axis) {
        for (let i = 0; i <= 3; i++) {
            const ts = t0 + i / 3 * span;
            const lab = svgEl(ns, "text", {
                x: x(ts), y: H - 4,
                "text-anchor": i === 0 ? "start" : (i === 3 ? "end" : "middle"),
                class: "axis",
            });
            lab.textContent = labelFmt(ts);
            svg.appendChild(lab);
        }
    }
    const linePts = pts.map(p => x(p.timestamp).toFixed(1) + "," + y(p.value).toFixed(1)).join(" ");
    svg.appendChild(svgEl(ns, "polygon", { class: "area", points: x(t0) + "," + (H - BP) + " " + linePts + " " + x(t1) + "," + (H - BP) }));
    svg.appendChild(svgEl(ns, "polyline", { class: "plot", points: linePts }));
    container.appendChild(svg);
}

/* ---------- Live-Karten ---------- */

let cards = [];

function makeCard(panel) {
    const card = document.createElement("div");
    card.className = "card clickable";
    card.title = "Graph in der Historie anzeigen";
    card.innerHTML =
        '<h2>' + panel.title + '</h2>' +
        '<div class="now">—</div>' +
        '<div class="sub"></div>' +
        '<div class="plot-area"></div>';
    card.addEventListener("click", () => {
        if (!visibility[panel.key]) {
            visibility[panel.key] = true;
            syncVisMenu();
            savePrefs();
            loadHistory(true);
        }
        el("history").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    el("charts").appendChild(card);
    card._panel = panel;
    return card;
}

function updateCard(card, pts, dynMax) {
    const now = card.querySelector(".now");
    const sub = card.querySelector(".sub");
    const fmt = card._panel.fmt;
    if (!pts.length) {
        now.textContent = "–";
        sub.textContent = "";
        makeChart(card.querySelector(".plot-area"), pts, card._panel, 90, hm, dynMax);
        return;
    }
    const vals = pts.map(p => p.value);
    const cur = vals[vals.length - 1];
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    now.innerHTML = fmt(cur) + " <small>aktuell</small>";
    sub.textContent = "Ø " + fmt(avg) + "  ·  Max " + fmt(Math.max(...vals)) +
                      (dynMax ? "  ·  von " + fmt(dynMax) : "");
    makeChart(card.querySelector(".plot-area"), pts, card._panel, 90, hm, dynMax);
}

/* ---------- Historie (wie /metrics) ---------- */

let visibility = {};
for (const p of PANELS)
    visibility[p.key] = true;

let hist = null;               /* { day, buckets: {key: Map(minuteTs→avg)}, dayMax: {key}, gttTotal, loaded: Set } */
let expandedHours = new Set();
let histBusy = false;
let lastHistLoad = 0;

function visiblePanels() { return PANELS.filter(p => visibility[p.key]); }

function savePrefs() {
    try {
        localStorage.setItem("gpu-vis", JSON.stringify(PANELS.filter(p => visibility[p.key]).map(p => p.key)));
        localStorage.setItem("gpu-hist-date", el("hist-date").value);
    } catch (e) { /* egal */ }
}

function loadPrefs() {
    try {
        const v = JSON.parse(localStorage.getItem("gpu-vis"));
        if (Array.isArray(v) && v.length)
            for (const p of PANELS)
                visibility[p.key] = v.includes(p.key);
        const d = localStorage.getItem("gpu-hist-date");
        if (d && d <= dateStr(new Date()))
            el("hist-date").value = d;
    } catch (e) { /* egal */ }
}

/* Dropdown „Springe zu": Heute + 14 Folgetage zurück (wie /metrics) */
function buildDateSelect() {
    const sel = el("hist-date");
    for (let i = 0; i < 15; i++) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        const o = document.createElement("option");
        o.value = dateStr(d);
        o.textContent = dayLabel(dateStr(d));
        sel.appendChild(o);
    }
}

function buildVisMenu() {
    const menu = el("vis-menu");
    for (const p of PANELS) {
        const lab = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = p.key;
        cb.checked = visibility[p.key];
        cb.addEventListener("change", () => {
            visibility[p.key] = cb.checked;
            updateVisButton();
            savePrefs();
            loadHistory(true);
        });
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" " + p.title));
        menu.appendChild(lab);
    }
    el("vis-btn").addEventListener("click", ev => {
        ev.stopPropagation();
        menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", () => { menu.hidden = true; });
    syncVisMenu();
}

function syncVisMenu() {
    for (const cb of el("vis-menu").querySelectorAll("input"))
        cb.checked = visibility[cb.value];
    updateVisButton();
}

function updateVisButton() {
    const n = visiblePanels().length;
    el("vis-btn").textContent = "Graphen (" + n + ") ▾";
}

function histWindow() {
    const day = el("hist-date").value || dateStr(new Date());
    const d0 = Math.floor(new Date(day + "T00:00:00").getTime() / 1000);
    const nowS = Math.floor(Date.now() / 1000);
    return { day, d0, d1: Math.min(d0 + 86400, nowS + 60), isToday: day === dateStr(new Date()) };
}

function addMinuteAvg(key, pts) {
    const m = hist.buckets[key] || (hist.buckets[key] = new Map());
    const agg = new Map();
    for (const p of pts) {
        const b = Math.floor(p.timestamp / MIN_MS) * MIN_MS;
        const a = agg.get(b) || [0, 0];
        a[0] += p.value; a[1]++;
        agg.set(b, a);
    }
    for (const [b, [s, n]] of agg)
        m.set(b, s / n);
}

async function loadHistory(full) {
    if (histBusy) return;
    histBusy = true;
    const box = el("hist-hours");
    try {
        const { day, d0, d1, isToday } = histWindow();
        if (full || !hist || hist.day !== day) {
            hist = { day, buckets: {}, dayMax: {}, loaded: new Set(), gttTotal: undefined };
            expandedHours = new Set();
        }
        /* Teilaktualisierung (Auto-Refresh): nur laufende + vorige zwei Stunden */
        const from = (hist.loaded.size && !full)
            ? Math.max(d0, Math.floor(Date.now() / HOUR_MS) * 3600 - 2 * 3600)
            : d0;
        await Promise.all(PANELS.map(async p => {
            const start = (hist.loaded.has(p.key) && !full) ? from : d0;
            const pts = await fetchMetric(p.key, start, d1);
            addMinuteAvg(p.key, pts);
            hist.loaded.add(p.key);
        }));
        if (!hist.gttTotal) {
            const t = await fetchMetric("mmv.amdgpu.gtt_total", d0, d1);
            hist.gttTotal = t.length ? t[t.length - 1].value : undefined;
        }
        /* Tagesskalierung je Metrik */
        for (const p of PANELS) {
            const m = hist.buckets[p.key];
            if (!m || !m.size)
                continue;
            hist.dayMax[p.key] = p.key === "mmv.amdgpu.gtt_used" && hist.gttTotal
                ? hist.gttTotal
                : Math.max(...m.values());
        }
        lastHistLoad = Date.now();
        renderHistory(isToday);
    } catch (err) {
        box.innerHTML = "";
        const e = document.createElement("div");
        e.className = "empty";
        e.textContent = "Fehler beim Laden der Historie: " + ((err && err.message) || err);
        box.appendChild(e);
    } finally {
        histBusy = false;
    }
}

function renderHistory(isToday) {
    const box = el("hist-hours");
    box.innerHTML = "";
    const vis = visiblePanels();
    if (!vis.length) {
        box.innerHTML = '<div class="empty">Bitte über „Graphen" mindestens einen Graphen auswählen.</div>';
        return;
    }
    /* Stunden mit Daten einsammeln */
    const hours = new Set();
    for (const p of vis)
        for (const minuteTs of (hist.buckets[p.key] || new Map()).keys())
            hours.add(Math.floor(minuteTs / HOUR_MS) * HOUR_MS);
    const sorted = [...hours].sort((a, b) => b - a);
    /* Tagesüberschrift */
    const head = document.createElement("h3");
    head.className = "metrics-time";
    const t = document.createElement("time");
    t.textContent = dayLabel(hist.day);
    head.appendChild(t);
    box.appendChild(head);
    if (!sorted.length) {
        const none = document.createElement("div");
        none.className = "empty";
        none.textContent = "Keine Daten für diesen Tag.";
        box.appendChild(none);
        return;
    }
    for (const hourTs of sorted) {
        const isExp = expandedHours.has(hourTs);
        const block = document.createElement("div");
        block.className = "hour-block" + (isExp ? " expanded" : "");
        const hhead = document.createElement("div");
        hhead.className = "hour-head";
        const htime = document.createElement("time");
        htime.className = "hour-time";
        htime.textContent = hm(hourTs);
        hhead.appendChild(htime);
        const chevron = document.createElement("button");
        chevron.type = "button";
        chevron.className = "hour-toggle";
        chevron.setAttribute("aria-expanded", String(isExp));
        chevron.title = isExp ? "Stunde verkleinern" : "Stunde vergrößern";
        chevron.textContent = isExp ? "▴" : "▾";
        chevron.addEventListener("click", () => {
            if (expandedHours.has(hourTs))
                expandedHours.delete(hourTs);
            else
                expandedHours.add(hourTs);
            renderHistory(hist.day === dateStr(new Date()));
        });
        hhead.appendChild(chevron);
        block.appendChild(hhead);
        const graphs = document.createElement("div");
        graphs.className = "hour-graphs";
        graphs.style.setProperty("--graph-cnt", String(vis.length));
        block.appendChild(graphs);
        box.appendChild(block);

        for (const p of vis) {
            const cell = document.createElement("div");
            cell.className = "hour-cell";
            const lab = document.createElement("div");
            lab.className = "glabel";
            const m = hist.buckets[p.key] || new Map();
            let sum = 0, n = 0, mx = -Infinity;
            const pts = [];
            for (const [bts, v] of m) {
                if (bts >= hourTs && bts < hourTs + HOUR_MS) {
                    pts.push({ timestamp: bts, value: v });
                    sum += v; n++;
                    if (v > mx) mx = v;
                }
            }
            pts.sort((a, b) => a.timestamp - b.timestamp);
            lab.textContent = p.title + (n ? "  ·  Ø " + p.fmt(sum / n) + " · Max " + p.fmt(mx) : "");
            cell.appendChild(lab);
            const plot = document.createElement("div");
            plot.className = "hour-plot";
            cell.appendChild(plot);
            graphs.appendChild(cell);
            const h = isExp ? 150 : 44;
            makeChart(plot, pts, p, h, isExp ? hm : null, hist.dayMax[p.key]);
        }
    }
}

/* ---------- Live-Refresh ---------- */

let refreshTimer = null;
let busy = false;

function refresh() {
    if (busy) return;
    busy = true;
    const btn = el("btn-refresh");
    btn.disabled = true;
    btn.classList.add("busy");

    const finishS = Math.floor(Date.now() / 1000) + 60;
    const startS = finishS - Number(el("window").value);

    Promise.all([
        Promise.all(PANELS.map(p => fetchMetric(p.key, startS, finishS))),
        seriesFor("mmv.amdgpu.gtt_total").then(ids => valuesFor(ids, startS, finishS)),
        fetchMetric("amdgpu.cardname", finishS - 86400, finishS),
    ]).then(([perPanel, gttTotalPts, cardnamePts]) => {
        el("banner").style.display = "none";
        const gttMax = gttTotalPts.length ? gttTotalPts[gttTotalPts.length - 1].value : undefined;
        let latest = 0;
        perPanel.forEach((pts, i) => {
            updateCard(cards[i], pts, PANELS[i].maxKey ? gttMax : undefined);
            if (pts.length)
                latest = Math.max(latest, pts[pts.length - 1].timestamp);
        });
        if (cardnamePts.length)
            el("device").textContent = cardnamePts[cardnamePts.length - 1].value + " · PCP/pmseries";
        el("status").textContent = latest
            ? "Quelle: PCP/pmseries · Aufzeichnung alle 5 s · letzter Datenpunkt " + hms(latest) +
              " · aktualisiert " + hms(Date.now())
            : "Es sind noch keine GPU-Werte aufgezeichnet worden.";
        /* laufende Stunde in der Historie mitziehen (max. minütlich) */
        if (hist && hist.day === dateStr(new Date()) && Date.now() - lastHistLoad > 60000)
            loadHistory(false);
    }, err => {
        el("banner").style.display = "block";
        el("status").textContent = "Fehler: " + ((err && (err.message || err.problem)) || JSON.stringify(err));
    }).finally(() => {
        busy = false;
        btn.disabled = false;
        btn.classList.remove("busy");
    });
}

function applyRefreshTimer() {
    if (refreshTimer)
        clearInterval(refreshTimer);
    const sec = Number(el("refresh").value);
    if (sec > 0)
        refreshTimer = setInterval(refresh, sec * 1000);
}

function init() {
    cards = PANELS.map(makeCard);
    buildDateSelect();
    buildVisMenu();
    loadPrefs();
    el("window").addEventListener("change", refresh);
    el("refresh").addEventListener("change", () => { applyRefreshTimer(); refresh(); });
    el("btn-refresh").addEventListener("click", refresh);
    el("hist-date").addEventListener("change", () => { savePrefs(); loadHistory(true); });
    el("btn-hist-refresh").addEventListener("click", () => loadHistory(true));
    refresh();
    loadHistory(true);
    applyRefreshTimer();
}

document.addEventListener("DOMContentLoaded", init);
