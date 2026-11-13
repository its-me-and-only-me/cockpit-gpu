# cockpit-gpu — GPU-Überwachung für Cockpit (AMD Strix Halo)

Eigenes Cockpit-Modul (Menüpunkt **„GPU"**) mit Live-Karten und historischer
Verlaufsansicht für die **AMD Radeon 8060S** eines **Strix-Halo-APU**
(Ryzen AI Max 390, 120 GiB unified memory, Fedora mit Cockpit ≥ 266).

Cockpit selbst hat keinen GPU-Graphen (Upstream-Issue
[cockpit-project/cockpit#13675](https://github.com/cockpit-project/cockpit/issues/13675)),
daher die Kombination aus PCP-Datenerfassung + eigenem Frontend in diesem Repo.

![Cockpit GPU-Modul](docs/screenshot.png)

## Architektur

```
 sysfs (/sys/class/drm/card1/device)          sysfs/edac-like PMDA-Werte
        │                                            │
        ▼                                            ▼
 exporter/amdgpu-gtt-mmv  ──MMV──▶  pmdammv     pmdaamdgpu (pcp-pmda-amdgpu)
 (GTT-Belegung, gfxclk; läuft als Dienst)            │
        └───────────────┬────────────────────────────┘
                        ▼
                      pmcd  ◀── Live-Abfrage (pminfo)
                        │
                        ▼
                    pmlogger  ── pmlogconf-Gruppen „gpu/amdgpu" + „gpu/gtt",
                        │        Aufzeichnungsintervall 5 Sekunden
                        ▼
        PCP-Archive (/var/log/pcp/pmlogger/…)
                        │
                        ▼  (Index)
                    valkey  ──▶  pmproxy (REST auf :44322, pmseries-API)
                                     ▲
                                     │ 127.0.0.1 (serverseitig!)
  Browser ── Cockpit-Bridge (WebSocket) ┘  cockpit.http(44322).get(...)
     └─ (nur bei lokalem Seitenaufruf: direkter fetch nach localhost:44322)
```

Das Frontend spricht die **pmseries-REST-API** von pmproxy an
(`/series/query`, `/series/values`). Primärer Transport ist der
**Cockpit-Bridge-Kanal** `cockpit.http(44322, {address:'127.0.0.1'}).get(pfad)`:
die Cockpit-Bridge verbindet dann *auf dem Server*, weshalb die Seite auch bei
Fernzugriff auf Cockpit die richtigen Daten zeigt. Ein direktes `fetch()` im
Browser würde bei Fernzugriff den Client-Rechner adressieren und wird deshalb
nur benutzt, wenn die Seite selbst von diesem Rechner kommt (`localhost`).
Der direkte pmapi-Endpunkt `/pmapi/fetch` ist in pmproxy 7.2.2 nicht brauchbar
(400) und wird nicht genutzt.

## Metriken

| Anzeige            | Metrik                     | Quelle | Anmerkung |
|--------------------|----------------------------|--------|-----------|
| GPU-Auslastung     | `amdgpu.gpu.load`          | pmdaamdgpu | 0–100 % |
| GTT genutzt        | `mmv.amdgpu.gtt_used`      | MMV-Exporter (sysfs `mem_info_gtt_used`) | Bei Unified-Memory-APUs der *tatsächlich* von der GPU belegte Speicher; Skala gegen `mmv.amdgpu.gtt_total` (~120 GiB) |
| Temperatur         | `amdgpu.gpu.temperature`   | pmdaamdgpu | |
| GPU-Takt (gfxclk)  | `mmv.amdgpu.gfxclk`        | MMV-Exporter (hwmon `freq1_input` = echte sclk, Fallback `pp_dpm_sclk`) | `amdgpu.gpu.clock` des PMDA ist auf dieser APU **konstant 1000 MHz** und damit wertlos |
| Leistung           | `amdgpu.gpu.average_power` | pmdaamdgpu | |

Der MMV-Exporter liefert ausschließlich `mmv.amdgpu.*`; die `amdgpu.*`-Namen
kommen komplett vom PMDA. Der Speicher-Takt (`memclk`) wurde bewusst entfernt —
er steht beim LPDDR5 praktisch immer auf demselben Wert. (Nach dem Entfernen
kann `mmv.amdgpu.memclk` noch als Wert-0-Metrik in `pminfo` auftauchen, bis
pmcd neu gestartet wird — pmdammv-Puffer, kosmetisch.)

## Cockpit-Modul (Frontend)

* **Live-Karten** (oben): aktueller Wert, Ø/Max im gewählten Zeitraum
  (1 h / 6 h / 24 h / 7 d), Auto-Refresh 5–60 s oder Aus, „letzter
  Datenpunkt HH:MM:SS" als sichtbares Feedback; Klick auf eine Karte zeigt den
  Graphen in der Historie.
* **Historie** (unterhalb der Karten) — Nachbau des UX-Musters der
  Cockpit-Metriken-Seite (`/metrics`, Quelltext `pkg/metrics`):
  * **„Springe zu"**: Datumsdropdown „Heute" + 14 vorherige Tage (analog
    `Array(15)`/`today_midnight` im Original).
  * **„Graphen (n) ▾"**: Klick-Dropdown mit Checkboxen, stellt die sichtbaren
    Graphen zusammen (analog *Graph visibility* im Original). Auswahl und
    Datum bleiben per `localStorage` erhalten.
  * Pro **Stunde** ein Block mit den gewählten Graphen **nebeneinander**
    (Minuten-Mittel aus den 5-s-Rohdaten), neueste Stunde zuerst; per ▾
    aufklappbar zu großen Diagrammen mit Achsen (analog
    `metrics-hour`/`metrics-hour-compressed`).
  * Y-Skalierung je Metrik **tagesstabil** (GTT gegen `gtt_total`, sonst
    Tagesmaximum), damit Stunden visuell vergleichbar sind.
  * Leere Tage → „Keine Daten für diesen Tag."
  * Auto-Refresh aktualisiert die laufende Stunde nach (Teilfenster ab
    Stunde−2 h, max. minütlich).
* **CSP-konform**: keine Inline-Styles/Scripts; `manifest.json` erklärt
  `connect-src … http://localhost:44322 http://127.0.0.1:44322`.
  `ws:`/`wss:` ist nötig, weil der Cockpit-Bridge-Kanal (WebSocket) sonst
  durch die CSP blockiert würde — `'self'` deckt das `ws:`-Schema nicht ab.
* **Datenweg**: primär Cockpit-Bridge (`cockpit.http(44322).get()` →
  serverseitige Verbindung zu 127.0.0.1:44322), direkter Browser-`fetch` nur
  bei lokalem Seitenaufruf.

## Installation

```bash
sudo ./install.sh
```

Das Skript ist idempotent und richtet alles ein: Pakete (`pcp`,
`pcp-pmda-amdgpu`, `pcp-zeroconf`), eine pmcd-Erreichbarkeits-Prüfung,
PMDA-Registrierung, `pmproxy` + Key-Value-Store (`valkey`, wird bei Bedarf
wie `redis` nachinstalliert — `pcp-zeroconf` zieht ihn nicht automatisch),
MMV-Exporter als Dienst `amdgpu-mmv`
(wird ohne AMD-GPU mit `mem_info_gtt_used` übersprungen), pmlogconf-Gruppen,
5-Sekunden-Intervall in `config.default` (ankerbasierter, atomarer Edit),
`PMLOGGER_CHECK_SKIP_LOGCONF=yes` und das Cockpit-Plugin unter
`/usr/local/share/cockpit/gpu` (Admin-Pfad; eine Alt-Installation in
`/usr/share/cockpit/gpu` wird entfernt). Danach Cockpit-Seite neu laden.

Deinstallation: `sudo ./uninstall.sh` (löscht keine PCP-Archive).

### Voraussetzungen / Kontext

* Fedora mit `pcp ≥ 6` (getestet: PCP/pmproxy 7.2.2), Cockpit ≥ 266 (getestet: 370)
* AMD-GPU mit amdgpu-Treiber (sysfs `mem_info_gtt_used` vorhanden)
* SELinux: auf dem Zielhost disabled; bei aktivem SELinux sind Kontexte/Booleans
  für pmproxy-Zugriff zu prüfen

**Sicherheitshinweis pmproxy:** die pmseries-API ist unauthentifiziert und
`pcp-zeroconf` lässt pmproxy standardmäßig auf **allen** Schnittstellen
lauschen (`0.0.0.0:44322`) — ohne Härtung kann jeder im Netz die Metriken
des Hosts lesen. pmproxy wird trotzdem gebraucht: es indexiert die
pmlogger-Archive in valkey und ist damit der einzige Dienst, aus dem das
Frontend Historie abfragen kann (pmseries-API). Da die Cockpit-Bridge
serverseitig von `127.0.0.1` verbindet, genügt ein reiner Loopback-Bind
vollständig — `install.sh` setzt daher automatisch `PMPROXY_LOCAL=1` in
`/etc/sysconfig/pmproxy` (und `PMCD_LOCAL=1` für den pmcd, auf Fedora
ohnehin Default) und startet die Dienste bei Änderung neu. DNS-SD-
Bekanntmachung unterdrückt `-A` in `/etc/pcp/pmproxy/pmproxy.options`
(bereits Fedora-Default). Kontrolle:

```
ss -tlnp | grep -E '44321|44322'   # darf nur 127.0.0.1/[::1] zeigen
```

## Betrieb & Troubleshooting

Symptome und die in der Praxis verifizierten Ursachen:

| Problem | Ursache / Fix |
|---|---|
| „pmproxy ist nicht erreichbar" im Banner | `pmcd`, `pmlogger`, `pmproxy`, `valkey` prüfen (`systemctl status`) |
| Aufzeichnung trotz Config bei 60 s | `pmlogger_check` (Systemd-Timer) regeneriert `config.default` per `pmlogconf` → `PMLOGGER_CHECK_SKIP_LOGCONF=yes` in `/etc/sysconfig/pmlogger` |
| Intervall-Tag wird ignoriert | pmlogconf akzeptiert **nur kanonisch pluralen** Intervallstring (`5 seconds`, nicht `5 second`); sonst stillschweigend `default` |
| pmseries liefert nichts fürs Zeitfenster | `start`/`finish` nur als **Epoch-Sekunden**; ISO-8601 und Millisekunden funktionieren nicht, `samples` ist für Zeitfenster ungeeignet |
| GPU-Takt bleibt konstant | `amdgpu.gpu.clock` (PMDA) ist auf Strix Halo konstant → `mmv.amdgpu.gfxclk` (hwmon `freq1_input`) verwenden |
| MMV-Metriken fehlen/„invalid" | Enum-Werte: `PM_TYPE_U64=3`, `PM_SEM_INSTANT=2`, `PM_SEM_DISCRETE=3`; Fallback-Verzeichnis `/var/lib/pcp/tmp/mmv` muss existieren (Dienst läuft als User `pcp`) |
| „Keine Daten" im Verlauf kurz nach Start | normal — Historie beginnt erst mit dem ersten pmlogger-Start mit GPU-Gruppen |
| MMV-Metrik trotz Entfernen noch in `pminfo` (Wert 0) | pmdammv merkt sich die Metrik-Tabelle der Datei bis zum pmcd-Neustart; harmlos, nur kosmetisch |
| Installer hängt bei „config.default auf 5 seconds stellen" | `pmlogconf` löst das PMNS über den pmcd auf und wartet, wenn der nicht antwortet — oft blockiert ein frisch registrierter binary-PMDA (bekannt: `pcp-pmda-bpf`). Der Installer prüft das jetzt vorab (`pminfo -T kernel.all.load` mit Timeout); manuell: `systemctl status pmcd`, Problempmda mit `./Remove` + `systemctl restart pmcd` |
| Kein valkey/redis installiert | `pcp-zeroconf` zieht den Key-Value-Store **nicht** automatisch; `install.sh` installiert ihn bei Bedarf nach (`valkey`, sonst `redis`) |
| „pmproxy won't die!" und Dienst startet nicht (Host mit Incus/LXC-Containern) | PCPs Prozesssuche (`_get_pids_by_name` in `/etc/pcp.env`) sieht aus dem Host die pmproxy/pmcd/pmlogger-Prozesse **in Containern** und will sie töten — am UID-Mapping scheitert das („won't die"). `install.sh` patcht einen PID-Namespace-Filter in `/etc/pcp.env` (rpm-Config-Datei, update-fest; von Hand entfernen: `uninstall.sh`); Upstream filtert nur libpod/crun-Cgroups, nicht Incus |

Sichtungs-Befehle:

```bash
pminfo amdgpu mmv.amdgpu                                  # Live-Werte
pmlogsummary -y -S -120seconds <archiv> amdgpu.gpu.load   # Dichte: bei 5 s ≈ 24 Samples
pmseries 'mmv.amdgpu.gfxclk'                              # series-IDs
journalctl -u pmlogger -u amdgpu-mmv -u pmproxy --since -1h
```

## Entwicklung / Tests

```bash
# E2E gegen das laufende Cockpit (Chromium + websockets-Paket nötig):
sudo useradd -m -s /bin/bash uitest && echo 'uitest:<pw>' | sudo chpasswd
E2E_USER=uitest E2E_PW='<pw>' python3 tests/cdp_e2e.py
sudo loginctl terminate-user uitest; sudo userdel -r uitest

# Schnell-Regression ohne Cockpit: statischer Harness mit cockpit.js-Stub,
# z. B. Dateien nach /tmp/uitest/gpu kopieren +
chromium-browser --headless=new --user-data-dir=/tmp/prof \
  --virtual-time-budget=20000 --screenshot=/tmp/shot.png \
  http://localhost:8899/gpu/index.html
```

Der E2E-Test prüft: Kartenanzahl == Panel-Set, Device-Name aus der
String-Metrik `amdgpu.cardname` (kein Fallback), Stundenblöcke (neueste
zuerst, volle Zellenzahl), Stunde aufklappen (SVG 150 px),
Sichtbarkeits-Dropdown (Reduktion + Menü bleibt nach Checkbox-Klick offen),
Datumswechsel (Tageskopf wechselt „Heute" ↔ Vortag, Blöcke folgen), Reihenfolge
Historie unterhalb der Karten. Browser-Binary per `E2E_BROWSER`
(`chromium-browser`/`chromium`/`google-chrome`), CDP-Port per `E2E_PORT`;
Profilverzeichnis PID-basiert (Parallelläufe).

Während Entwicklung **nie** `systemctl restart cockpit` — das trennt die
Browser-Session; Plugin-Dateien sind statisch, Browser-Reload genügt.

## Repo-Inhalt

```
LICENSE           MIT
.github/workflows/static.yml  CI: shellcheck, py_compile, node --check, JSON-Lint
cockpit-plugin/   Cockpit-Modul (manifest.json, index.html, gpu.js, gpu.css)
exporter/         amdgpu-gtt-mmv  — PCP-MMV-Exporter (GTT, gfxclk) aus sysfs
                  amdgpu-mmv.service — systemd-Unit (User pcp)
pcp/pmlogconf/gpu/amdgpu  — pmlogconf-Gruppe: PMDA-Metriken
pcp/pmlogconf/gpu/gtt     — pmlogconf-Gruppe: MMV-Metriken
install.sh        Idempotenter Total-Installer (root)
uninstall.sh      Deinstallation (behält PCP-Archive)
tests/cdp_e2e.py  Browser-E2E über Chrome-DevTools-Protocol
docs/screenshot.png
```

## Bekannte Grenzen

* Historie erst ab dem Tag, an dem die GPU-Gruppen in der pmlogger-Config waren.
* pmproxy-`/series/extract` (Downsampling serverseitig) existiert in
  pmproxy 7.2.2 nicht → Minuten-Mittel werden clientseitig gebildet.
* Ein Voll-Tag ≈ 17 300 Rohsamples/Metrik; Teilaktualisierung begrenzt das
  beim Auto-Refresh auf 3 Stunden.
* Die 5-s-Aufzeichnung erhöht die I/O-Last geringfügig; pmlogger-roten
  (`pmlogger_check`) bleiben ohne `SKIP_LOGCONF` aktiv.
* **Eine GPU:** die Auswertung führt alle Instanzen einer Metrik pro
  Zeitstrang zusammen (Werte mehrerer Karten würden vermischt). Der
  Exporteur unterstützt zwar mehrere Karten, die UI ist aber für genau
  eine GPU pro Host gebaut.
* **Theme:** folgt dem `prefers-color-scheme` des Betriebssystems, nicht
  dem manuellen Cockpit-Theme-Umschalter (Cockpit vererbt sein Theme nicht
  an Fremd-Pakete; PatternFly-Tokens wären die langfristige Lösung).
* Beschriftungen/Doku sind deutsch.
* Diagramme werden beim Fenster-Resize neu gezeichnet; während laufender
  Historien-Navigation kann ein Resize bis zum nächsten Render durchgehen.
