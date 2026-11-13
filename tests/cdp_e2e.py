#!/usr/bin/env python3
"""CDP-E2E für das Cockpit-GPU-Plugin (Historie im /metrics-Stil).

Prüft gegen ein laufendes Cockpit (https://localhost:9090):
Login → Seite /gpu/ → Karten, Device-Name, Stundenblöcke, Aufklappen,
Graph-Sichtbarkeits-Dropdown (inkl. offen bleiben), Datums-Sprungauswahl,
Reihenfolge.

Vorbereitung (Wegwerf-Testaccount, danach wieder löschen):
    sudo useradd -m -s /bin/bash "$E2E_USER"
    echo "$E2E_USER:$E2E_PW" | sudo chpasswd
    E2E_USER=uitest E2E_PW='geheim' python3 tests/cdp_e2e.py
    sudo loginctl terminate-user "$E2E_USER"; sudo userdel -r "$E2E_USER"

Umgebungsvariablen:
    E2E_USER   Login-Name des Wegwerf-Accounts   (Default: uitest)
    E2E_PW     Passwort des Wegwerf-Accounts     (Pflicht; nur local/CDP)
    E2E_BROWSER  Browser-Binary                  (Default: chromium-browser,
                                               alternativ chromium /
                                               google-chrome / google-chrome-stable)
    E2E_PORT   CDP-Port                          (Default: 9333)
    E2E_SHOT   Screenshot-Zieldatei

ACHTUNG: Das Wegwerf-Passwort wird per CDP Runtime.evaluate in die Seite
getragen (json-escaped). Das ist nur für lokale Wegwerf-Accounts okay.

Benötigt: Chromium/Chromium-Browser, Python-Paket websockets.
"""
import asyncio, base64, json, os, subprocess, sys, time, urllib.request
import websockets

E2E_USER = os.environ.get("E2E_USER", "uitest")
E2E_PW = os.environ.get("E2E_PW", "")
if not E2E_PW:
    sys.exit("E2E_PW nicht gesetzt (Wegwerf-Passwort für den temporären Test-User)")
BROWSER = os.environ.get("E2E_BROWSER", "chromium-browser")
PORT = int(os.environ.get("E2E_PORT", "9333"))
PROFILE = f"/tmp/e2e-cdp-profile-{os.getpid()}"
SCREENSHOT = os.environ.get("E2E_SHOT", "/tmp/gpu-cockpit-e2e.png")

def log(m): print(m, flush=True)

async def cdp(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        r = json.loads(await asyncio.wait_for(ws.recv(), 60))
        if r.get("id") == mid:
            if "error" in r: raise RuntimeError(f"{method}: {r['error']}")
            return r.get("result", {})

async def ev_raw(ws, mid_ref, expr, awaitp=False):
    mid_ref[0] += 1
    r = await cdp(ws, mid_ref[0], "Runtime.evaluate", {
        "expression": expr, "awaitPromise": awaitp, "returnByValue": True, "timeout": 25000})
    if r.get("exceptionDetails"):
        raise RuntimeError("JS: " + json.dumps(r["exceptionDetails"])[:300])
    return r["result"].get("value")

async def poll_until(ws, mid_ref, js_predicate, timeout=25.0, why=""):
    """Wait until the JS predicate (in top frame) is truthy; error otherwise."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if await ev_raw(ws, mid_ref, js_predicate):
            return
        await asyncio.sleep(0.4)
    raise RuntimeError(f"Timeout ({timeout}s) bei: {why or js_predicate[:80]}")

FR = """(() => {
    const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
    return fr ? fr.contentDocument : document;
})()"""

async def main():
    proc = subprocess.Popen([
        BROWSER, f"--remote-debugging-port={PORT}", "--headless=new",
        "--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1750", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = []
    try:
        page = None
        for _ in range(40):
            try:
                t = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next((x for x in t if x["type"] == "page"), None)
                if page: break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        if not page:
            raise RuntimeError("CDP-Endpoint nicht erreichbar — Browser-Binary korrekt? (E2E_BROWSER)")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=60*1024*1024) as ws:
            mid = [0]
            async def ev(expr, awaitp=False):
                return await ev_raw(ws, mid, expr, awaitp)
            for m_ in ("Page.enable", "Runtime.enable"):
                mid[0] += 1
                await cdp(ws, mid[0], m_)

            # Login als temporärer Test-User
            mid[0] += 1
            await cdp(ws, mid[0], "Page.navigate", {"url": "https://localhost:9090/"})
            await asyncio.sleep(2)
            if await ev("!!document.querySelector('#login-user-input')"):
                login_js = (
                    "new Promise((res, rej) => { const t0 = Date.now();"
                    "(function f(){ const u=document.querySelector('#login-user-input'),"
                    " p=document.querySelector('#login-password-input');"
                    f" if (u && p) {{ u.value={json.dumps(E2E_USER)}; p.value={json.dumps(E2E_PW)};"
                    " u.dispatchEvent(new Event('input',{bubbles:true}));"
                    " p.dispatchEvent(new Event('input',{bubbles:true})); res('ok'); }"
                    " else if (Date.now()-t0>15000) rej('no form'); else setTimeout(f,300); })(); })")
                await ev(login_js, True)
                await ev("document.querySelector('#login-button').click()")
                await poll_until(ws, mid, "!document.querySelector('#login-user-input')", 30, "Login durch")
            log("angemeldet")

            mid[0] += 1
            await cdp(ws, mid[0], "Page.navigate", {"url": "https://localhost:9090/gpu/"})

            # 1) Karten + Device-Name (H2-Regression)
            await poll_until(ws, mid,
                f"{FR}.querySelectorAll('#charts .card h2').length > 0", 30, "Karten gerendert")
            n_panels = await ev(f"{FR}.querySelectorAll('#vis-menu input').length")
            cards = await ev(f"[...{FR}.querySelectorAll('#charts .card h2')].map(h=>h.textContent)")
            # Device-Name kommt erst mit dem ersten Refresh (async) nach — nicht zu früh lesen
            await poll_until(ws, mid,
                f"{FR}.getElementById('device').textContent !== 'AMD GPU (PCP)'", 30, "Device-Name geladen")
            device = await ev(f"{FR}.getElementById('device').textContent")
            log(f"Karten: {len(cards or [])} (PANELS={n_panels}), Device: {device!r}")
            if len(cards or []) != n_panels: fails.append("Kartenanzahl != PANELS")
            if "PCP" not in (device or ""):
                fails.append("Device-Name ohne Quellen-Suffix: " + str(device))

            # 2) Grundzustand Historie
            await poll_until(ws, mid,
                f"{FR}.querySelectorAll('.hour-block').length > 0", 30, "Stundenblöcke da")
            blocks = await ev(f"{FR}.querySelectorAll('.hour-block').length")
            cells0 = await ev(f"{FR}.querySelectorAll('.hour-block')[0].querySelectorAll('.hour-cell').length")
            dayhead = await ev(f"{FR}.querySelector('.metrics-time time').textContent")
            order = await ev(f"""(() => {{
                const hs=[...{FR}.querySelectorAll('.hour-time')].map(t=>t.textContent);
                return hs.join(',').localeCompare([...hs].sort().reverse().join(',')) === 0; }})()""")
            log(f"Stundenblöcke: {blocks}, Zellen/Block: {cells0}, Tageskopf: {dayhead!r}, neuste zuerst: {order}")
            if cells0 != n_panels: fails.append(f"Block hat nicht {n_panels} Zellen")
            if dayhead != "Heute": fails.append("Tageskopf != Heute")
            if not order: fails.append("Stunden nicht absteigend")

            # 3) Erste Stunde aufklappen
            await ev(f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                fr.contentDocument.querySelector('.hour-toggle').click(); return 1; }})()""")
            await poll_until(ws, mid, f"{FR}.querySelector('.hour-block').classList.contains('expanded')", 10, "Block expanded")
            h = await ev(f"{FR}.querySelector('.hour-block svg').getAttribute('height')")
            log(f"SVG-Höhe aufgeklappt: {h}")
            if h != "150": fails.append("Aufklappen ohne Höhenwechsel")

            # 4) Graphen-Dropdown: erst öffnen, dann Checkbox togglen —
            #    Menü muss OFFEN bleiben (M2-Regression)
            open_js = f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                fr.contentDocument.getElementById('vis-btn').click();
                return fr.contentDocument.getElementById('vis-menu').hidden === false; }})()"""
            if not await ev(open_js):
                fails.append("Menü öffnet nicht per Button-Klick")
            hide_js = f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const cb = fr.contentDocument.querySelector('#vis-menu input[value="amdgpu.gpu.temperature"]');
                cb.click();
                return {{ checked: cb.checked, menuHidden: fr.contentDocument.getElementById('vis-menu').hidden }}; }})()"""
            res = await ev(hide_js)
            log(f"Checkbox geklickt: checked={res['checked']} (erwartet False), Menü offen={not res['menuHidden']}")
            if res["checked"]: fails.append("Checkbox toggelt nicht")
            if res["menuHidden"]: fails.append("Menü schließt bei Checkbox-Klick (M2 wieder da)")
            await poll_until(ws, mid,
                f"{FR}.querySelector('.hour-block').querySelectorAll('.hour-cell').length === {n_panels - 1}",
                15, "Zellen reduziert")
            btn = await ev(f"{FR}.getElementById('vis-btn').textContent")
            log(f"Button nach Deaktivierung: {btn!r}")
            if f"({n_panels - 1})" not in (btn or ""): fails.append("Button-Label nicht aktualisiert")

            # Toggle zurückgängig machen
            await ev(hide_js)
            await poll_until(ws, mid,
                f"{FR}.querySelector('.hour-block').querySelectorAll('.hour-cell').length === {n_panels}",
                15, "Zellen wiederhergestellt")

            # 5) Gestern wählen -> Umschalten wirkt (Vortag kann je nach
            #    Aufzeichnungsstart Teildaten haben, daher Tagesspruch prüfen);
            #    zurück zu Heute
            await ev(f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const s = fr.contentDocument.getElementById('hist-date');
                const d = new Date(); d.setDate(d.getDate()-1);
                const p = n => String(n).padStart(2,'0');
                s.value = d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());
                s.dispatchEvent(new fr.contentWindow.Event('change')); return 1; }})()""")
            await poll_until(ws, mid,
                f"""{FR}.querySelector('.metrics-time time').textContent !== 'Heute'""",
                25, "Tageskopf wechselt von Heute weg")
            log("Gestern gewählt: Tageskopf = "
                + str(await ev(f"{FR}.querySelector('.metrics-time time').textContent")))
            await ev(f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const s = fr.contentDocument.getElementById('hist-date');
                s.selectedIndex = 0; s.dispatchEvent(new fr.contentWindow.Event('change')); return 1; }})()""")
            await poll_until(ws, mid,
                f"""{FR}.querySelector('.metrics-time time').textContent === 'Heute'
                    && {FR}.querySelectorAll('.hour-block').length > 0""",
                30, "zurück zu Heute (Tageskopf + Blöcke)")
            log("Zurück zu Heute: Tageskopf und Blöcke wieder da")

            # 6) Seitenreihenfolge: Historie unterhalb der Karten
            pos = await ev(f"""(() => {{
                const d = {FR};
                const c = d.getElementById('charts').getBoundingClientRect().bottom;
                const h = d.getElementById('history').getBoundingClientRect().top;
                return h >= c - 5; }})()""")
            if not pos: fails.append("Historie nicht unterhalb der Karten")
            log(f"Reihenfolge Karten vor Historie: {pos}")

            r = await cdp(ws, mid[0], "Page.captureScreenshot", {"format": "png"}); mid[0] += 1
            open(SCREENSHOT, "wb").write(base64.b64decode(r["data"]))
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        try:
            import shutil; shutil.rmtree(PROFILE, ignore_errors=True)
        except Exception: pass
    log("Screenshot: " + SCREENSHOT)
    log("ERGEBNIS: " + ("OK" if not fails else "FEHLER: " + "; ".join(fails)))
    return 0 if not fails else 1

sys.exit(asyncio.run(main()))
