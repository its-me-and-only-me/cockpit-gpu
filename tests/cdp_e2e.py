#!/usr/bin/env python3
"""CDP-E2E für das Cockpit-GPU-Plugin (Historie im /metrics-Stil).

Prüft gegen ein laufendes Cockpit (https://localhost:9090):
Login → Seite /gpu/ → Karten, Stundenblöcke, Aufklappen,
Graph-Sichtbarkeits-Dropdown, Datums-Sprungauswahl, Reihenfolge.

Vorbereitung (Wegwerf-Testaccount, danach wieder löschen):
    sudo useradd -m -s /bin/bash "$E2E_USER"
    echo "$E2E_USER:$E2E_PW" | sudo chpasswd
    E2E_USER=uitest E2E_PW='geheim' python3 tests/cdp_e2e.py
    sudo loginctl terminate-user "$E2E_USER"; sudo userdel -r "$E2E_USER"

Benötigt: Chromium/Chromium-Browser, Python-Paket websockets.
"""
import asyncio, base64, json, os, subprocess, sys, time, urllib.request
import websockets

E2E_USER = os.environ.get("E2E_USER", "uitest")
E2E_PW = os.environ.get("E2E_PW", "")
if not E2E_PW:
    sys.exit("E2E_PW nicht gesetzt (Wegwerf-Passwort für den temporären Test-User)")
PORT = int(os.environ.get("E2E_PORT", "9333"))
PROFILE = "/tmp/e2e-cdp-profile"
SCREENSHOT = os.environ.get("E2E_SHOT", "/tmp/gpu-cockpit-e2e.png")

def log(m): print(m, flush=True)

async def cdp(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == mid:
            if "error" in r: raise RuntimeError(f"{method}: {r['error']}")
            return r.get("result", {})

async def ev(ws, mid, expr, awaitp=False):
    r = await cdp(ws, mid[0], "Runtime.evaluate", {
        "expression": expr, "awaitPromise": awaitp, "returnByValue": True, "timeout": 25000})
    mid[0] += 1
    if r.get("exceptionDetails"):
        raise RuntimeError("JS: " + json.dumps(r["exceptionDetails"])[:300])
    return r["result"].get("value")

FR = """(() => {
    const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
    return fr ? fr.contentDocument : document;
})()"""

async def main():
    proc = subprocess.Popen([
        "chromium-browser", f"--remote-debugging-port={PORT}", "--headless=new",
        "--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1750", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = []
    try:
        for _ in range(40):
            try:
                t = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(x for x in t if x["type"] == "page"); break
            except Exception:
                await asyncio.sleep(0.5)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=60*1024*1024) as ws:
            mid = [1]
            for m_ in ("Page.enable", "Runtime.enable"):
                await cdp(ws, mid[0], m_); mid[0] += 1

            # Login als temporärer Test-User
            await cdp(ws, mid[0], "Page.navigate", {"url": "https://localhost:9090/"}); mid[0] += 1
            await asyncio.sleep(3)
            login_js = (
                "new Promise((res, rej) => { const t0 = Date.now();"
                "(function f(){ const u=document.querySelector('#login-user-input'),"
                " p=document.querySelector('#login-password-input');"
                f" if (u && p) {{ u.value={json.dumps(E2E_USER)}; p.value={json.dumps(E2E_PW)};"
                " u.dispatchEvent(new Event('input',{bubbles:true}));"
                " p.dispatchEvent(new Event('input',{bubbles:true})); res('ok'); }"
                " else if (Date.now()-t0>15000) rej('no form'); else setTimeout(f,300); })(); })")
            await ev(ws, mid, login_js, True)
            await ev(ws, mid, "document.querySelector('#login-button').click()")
            for _ in range(40):
                if not await ev(ws, mid, "!!document.querySelector('#login-user-input')"): break
                await asyncio.sleep(0.75)
            log("angemeldet")

            await cdp(ws, mid[0], "Page.navigate", {"url": "https://localhost:9090/gpu/"}); mid[0] += 1
            await asyncio.sleep(10)

            # 1) Grundzustand
            cards = await ev(ws, mid, f"[...{FR}.querySelectorAll('#charts .card h2')].map(h=>h.textContent)")
            blocks = await ev(ws, mid, f"{FR}.querySelectorAll('.hour-block').length")
            cells0 = await ev(ws, mid, f"{FR}.querySelectorAll('.hour-block')[0] ? {FR}.querySelectorAll('.hour-block')[0].querySelectorAll('.hour-cell').length : 0")
            dayhead = await ev(ws, mid, f"{FR}.querySelector('.metrics-time time').textContent")
            order = await ev(ws, mid, f"""(() => {{
                const hs=[...{FR}.querySelectorAll('.hour-time')].map(t=>t.textContent);
                return hs.join(',').localeCompare([...hs].sort().reverse().join(',')) === 0; }})()""")
            log(f"Karten: {len(cards or [])}, Stundenblöcke: {blocks}, Zellen/Block: {cells0}, Tageskopf: {dayhead!r}, neuste zuerst: {order}")
            if len(cards or []) != 5: fails.append("falsche Kartenanzahl")
            if not blocks: fails.append("keine Stundenblöcke")
            if cells0 != 5: fails.append("Block hat nicht 5 Zellen")
            if dayhead != "Heute": fails.append("Tageskopf != Heute")
            if not order: fails.append("Stunden nicht absteigend")

            # 2) Erste Stunde aufklappen
            await ev(ws, mid, f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                fr.contentDocument.querySelector('.hour-toggle').click(); return 1; }})()""")
            await asyncio.sleep(2)
            exp = await ev(ws, mid, f"""(() => {{
                const b = {FR}.querySelector('.hour-block');
                const svg = b.querySelector('svg');
                return {{ cls: b.className, h: svg ? svg.getAttribute('height') : null }}; }})()""")
            log(f"Aufklappen: class={exp['cls']}, SVG-Höhe={exp['h']}")
            if "expanded" not in (exp["cls"] or "") or exp["h"] != "150": fails.append("Aufklappen ohne Wirkung")

            # 3) Temperatur in „Graphen" deaktivieren
            await ev(ws, mid, f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const cb = fr.contentDocument.querySelector('#vis-menu input[value="amdgpu.gpu.temperature"]');
                cb.checked = false; cb.dispatchEvent(new fr.contentWindow.Event('change')); return 1; }})()""")
            await asyncio.sleep(4)
            cells1 = await ev(ws, mid, f"{FR}.querySelectorAll('.hour-block')[0] ? {FR}.querySelectorAll('.hour-block')[0].querySelectorAll('.hour-cell').length : 0")
            btn = await ev(ws, mid, f"{FR}.getElementById('vis-btn').textContent")
            log(f"Nach Deaktivieren Temperatur: Zellen/Block={cells1}, Button={btn!r}")
            if cells1 != 4: fails.append("Sichtbarkeit reduziert nicht auf 4 Zellen")
            if "(4)" not in (btn or ""): fails.append("Button-Label nicht aktualisiert")

            # 4) Gestern wählen -> keine Daten; zurück zu Heute
            await ev(ws, mid, f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const s = fr.contentDocument.getElementById('hist-date');
                const d = new Date(); d.setDate(d.getDate()-1);
                const p = n => String(n).padStart(2,'0');
                s.value = d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());
                s.dispatchEvent(new fr.contentWindow.Event('change')); return 1; }})()""")
            await asyncio.sleep(6)
            emptymsg = await ev(ws, mid, f"""(() => {{
                const b = {FR}.getElementById('hist-hours');
                return [...b.querySelectorAll('.empty')].map(e=>e.textContent).join('|'); }})()""")
            log(f"Gestern: {emptymsg!r}")
            if "Keine Daten" not in (emptymsg or ""): fails.append("Gestern zeigt keine 'Keine Daten'-Meldung")
            await ev(ws, mid, f"""(() => {{
                const fr = [...document.querySelectorAll('iframe')].find(i => i.contentDocument && i.src.includes('gpu'));
                const s = fr.contentDocument.getElementById('hist-date');
                s.selectedIndex = 0; s.dispatchEvent(new fr.contentWindow.Event('change')); return 1; }})()""")
            await asyncio.sleep(8)
            back = await ev(ws, mid, f"{FR}.querySelectorAll('.hour-block').length")
            log(f"Zurück zu Heute: {back} Stundenblöcke")
            if not back: fails.append("Heute-Reset ohne Blöcke")

            # 5) Seitenreihenfolge: Historie unterhalb der Karten
            pos = await ev(ws, mid, f"""(() => {{
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
    log("Screenshot: " + SCREENSHOT)
    log("ERGEBNIS: " + ("OK" if not fails else "FEHLER: " + "; ".join(fails)))
    return 0 if not fails else 1

sys.exit(asyncio.run(main()))
