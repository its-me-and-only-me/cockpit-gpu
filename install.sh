#!/bin/bash
# Installation des Cockpit-GPU-Moduls (AMD Strix Halo / Radeon 8060S) auf Fedora mit Cockpit.
# Idempotent; als root ausführen:  sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen (sudo ./install.sh)" >&2; exit 1; }

PMDA_DIR=/var/lib/pcp/pmdas/amdgpu
PMLOGCONF_DIR=/var/lib/pcp/config/pmlogconf
SYSCONFIG=/etc/sysconfig/pmlogger
PLUGIN_DIR=/usr/local/share/cockpit/gpu   # Admin-installierter Cockpit-Content (I3)

step() { echo "== $*"; }

# --- 0. Hardware-Prüfung (I2) ------------------------------------------------
HAVE_GPU=no
if compgen -G "/sys/class/drm/card*/device/mem_info_gtt_used" > /dev/null; then
    HAVE_GPU=yes
fi

# --- 1. Pakete ---------------------------------------------------------------
step "PCP-Pakete prüfen/installieren"
pkgs="pcp pcp-pmda-amdgpu pcp-zeroconf"
missing=()
for p in $pkgs; do rpm -q "$p" >/dev/null 2>&1 || missing+=("$p"); done
if ((${#missing[@]})); then
    dnf install -y "${missing[@]}"
fi

# --- 2. amdgpu-PMDA registrieren -------------------------------------------
if [ ! -e "$PMDA_DIR/root" ]; then
    step "amdgpu-PMDA registrieren"
    (cd "$PMDA_DIR" && ./Install </dev/null)
fi

# --- 3. pmproxy + Key-Value-Store (PM series-Index) -------------------------
# Fedora ≥ 41 nutzt valkey, älteres Fedora/EPEL redis — was auch immer da ist.
step "pmproxy und valkey/redis aktivieren"
systemctl enable --now pmproxy
if systemctl list-unit-files valkey.service >/dev/null 2>&1 &&
   systemctl cat valkey.service >/dev/null 2>&1; then
    systemctl enable --now valkey
elif systemctl cat redis >/dev/null 2>&1; then
    systemctl enable --now redis
else
    echo "WARNUNG: weder valkey noch redis gefunden — pmseries-Index fehlt!" >&2
fi

# --- 4. MMV-Exporter (GTT + gfxclk) ----------------------------------------
if [ "$HAVE_GPU" = yes ]; then
    step "MMV-Exporter installieren"
    install -m 755 exporter/amdgpu-gtt-mmv /usr/local/bin/amdgpu-gtt-mmv
    install -m 644 exporter/amdgpu-mmv.service /etc/systemd/system/amdgpu-mmv.service
    systemctl daemon-reload
    systemctl enable --now amdgpu-mmv.service
else
    step "Keine AMD-GPU mit mem_info_gtt_used gefunden — MMV-Exporter wird ÜBERSPRUNGEN"
    echo "   (Karten-Grafik funktioniert nur mit PMDA-Metriken; Exporteur manuell:"
    echo "    systemctl enable --now amdgpu-mmv.service, Unit zuvor installieren)"
fi

# --- 5. pmlogconf-Gruppen + 5-Sekunden-Intervall ----------------------------
step "pmlogconf-Gruppen einspielen"
mkdir -p "$PMLOGCONF_DIR/gpu"
install -m 644 pcp/pmlogconf/gpu/amdgpu "$PMLOGCONF_DIR/gpu/amdgpu"
install -m 644 pcp/pmlogconf/gpu/gtt    "$PMLOGCONF_DIR/gpu/gtt"

step "config.default auf 5 seconds stellen"
pmlogconf -q /var/lib/pcp/config/pmlogger/config.default >/dev/null
# Ankerbasierter, idempotenter, atomarer Edit (I1):
#  - behandelt sowohl ':default:' als auch bereits gesetzte Intervall-Tags
#  - patcht NUR das 'log advisory on …'-Statement innerhalb der gpu-Gruppen
#  - scheitert laut, statt stillzulochen; schreibt über Temp-Datei + os.replace
python3 - <<'PYEOF'
import os
import re
import sys

path = '/var/lib/pcp/config/pmlogger/config.default'
src = open(path).read()

TAG_RE = re.compile(r'^#\+ gpu/(amdgpu|gtt):y:[^:\n]*:$', re.M)
INTERVAL = '5 seconds'

def canonical_tag(m):
    return f'#+ gpu/{m.group(1)}:y:{INTERVAL}:'

out = TAG_RE.sub(canonical_tag, src)

# Interval-Zeile nur innerhalb des Blocks der jeweiligen Gruppe ersetzen:
# Block = von '#+ gpu/…' bis zur nächsten '#+ '- oder '#----'-Marke.
def fix_block(m):
    block = m.group(0)
    block = re.sub(r'log advisory on [^{\n]+{', 'log advisory on %s {' % INTERVAL, block, count=1)
    return block

out = re.sub(r'(?m)^#\+ gpu/(?:amdgpu|gtt):y:[^:\n]*:$.*?(?=^#\+ |^#----|\Z)',
             fix_block, out, flags=re.S)

n_tags = len(TAG_RE.findall(out))
n_log = len(re.findall(r'(?m)^log advisory on 5 seconds \{', out))
if n_tags != 2:
    sys.exit(f"FEHLER: erwartete 2 gpu-Gruppen-Tags in {path}, gefunden: {n_tags}")
if n_log != 2:
    sys.exit(f"FEHLER: erwartete 2 'log advisory on 5 seconds'-Blöcke, gefunden: {n_log}")

if out != src:
    tmp = path + '.tmp-install'
    with open(tmp, 'w') as f:
        f.write(out)
    os.fsync(f.fileno())
    os.replace(tmp, path)
    print("config.default: gpu-Gruppen auf '5 seconds' gesetzt")
else:
    print("config.default: bereits korrekt (idempotent, keine Änderung)")
PYEOF

# pmlogger_check (Systemd-Timer) würde die Config sonst per pmlogconf regenerieren
# und die Intervalle zurücksetzen:
if ! grep -qsE '^[[:space:]]*PMLOGGER_CHECK_SKIP_LOGCONF=yes' "$SYSCONFIG"; then
    step "PMLOGGER_CHECK_SKIP_LOGCONF=yes setzen"
    echo 'PMLOGGER_CHECK_SKIP_LOGCONF=yes' >> "$SYSCONFIG"
fi

# --- 6. Cockpit-Plugin ------------------------------------------------------
step "Cockpit-Plugin installieren nach $PLUGIN_DIR"
mkdir -p "$PLUGIN_DIR"
install -m 644 cockpit-plugin/{manifest.json,index.html,gpu.js,gpu.css} "$PLUGIN_DIR/"
# Altbestand aus /usr/share entfernen (I3; wäre sonst die aktive Kopie)
if [ -e /usr/share/cockpit/gpu ]; then
    rm -rf /usr/share/cockpit/gpu
    echo "   (alt installiertes /usr/share/cockpit/gpu entfernt)"
fi

# --- 7. pmlogger neu starten (nimmt neue Config/Intervalle) ----------------
step "pmlogger neu starten"
systemctl restart pmlogger

echo
echo "Fertig. Prüfen mit:"
echo "  pminfo amdgpu mmv.amdgpu            # Live-Werte"
# shellcheck disable=SC1111  # deutsche Anführungszeichen im Ausgabetext beabsichtigt
echo "  Cockpit neu laden → Menüpunkt „GPU“  (läuft alles über den Cockpit-Bridge-Kanal)"
# shellcheck disable=SC2016,SC2028  # wörtliches Shell-Kommando als Hinweis-Text
echo '  Dichte: pmlogsummary -y -S -120seconds $(ls -t /var/log/pcp/pmlogger/$(hostname) | grep -E "^[0-9]{8}" | head -1 | sed "s/\\.0$//") amdgpu.gpu.load  → ≈24 Samples'
