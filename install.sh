#!/bin/bash
# Installation des Cockpit-GPU-Moduls (AMD Strix Halo / Radeon 8060S) auf Fedora mit Cockpit.
# Idempotent; als root ausführen:  sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

PMDA_DIR=/var/lib/pcp/pmdas/amdgpu
PMLOGCONF_DIR=/var/lib/pcp/config/pmlogconf
SYSCONFIG=/etc/sysconfig/pmlogger

step() { echo "== $*"; }

# --- 1. Pakete -------------------------------------------------------------
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

# --- 3. pmproxy + valkey (PM series-Index) ---------------------------------
step "pmproxy und valkey aktivieren"
systemctl enable --now valkey pmproxy

# --- 4. MMV-Exporter (GTT + gfxclk) ----------------------------------------
step "MMV-Exporter installieren"
install -m 755 exporter/amdgpu-gtt-mmv /usr/local/bin/amdgpu-gtt-mmv
install -m 644 exporter/amdgpu-mmv.service /etc/systemd/system/amdgpu-mmv.service
systemctl daemon-reload
systemctl enable --now amdgpu-mmv.service

# --- 5. pmlogconf-Gruppen + 5-Sekunden-Intervall ----------------------------
step "pmlogconf-Gruppen einspielen"
mkdir -p "$PMLOGCONF_DIR/gpu"
install -m 644 pcp/pmlogconf/gpu/amdgpu "$PMLOGCONF_DIR/gpu/amdgpu"
install -m 644 pcp/pmlogconf/gpu/gtt    "$PMLOGCONF_DIR/gpu/gtt"

# Intervall „5 seconds“ NUR in kanonisch pluraler Schreibweise akzeptiert
# (sonst still auf „default“ zurückgesetzt).
step "config.default auf 5 seconds stellen"
pmlogconf -q /var/lib/pcp/config/pmlogger/config.default >/dev/null
python3 - <<'EOF'
p = '/var/lib/pcp/config/pmlogger/config.default'
s = open(p).read()
changed = 0
for tag in ('gpu/amdgpu', 'gpu/gtt'):
    old_tag = f'#+ {tag}:y:default:'
    if old_tag in s:
        s = s.replace(old_tag, f'#+ {tag}:y:5 seconds:')
        changed += 1
    i = s.index(f'#+ {tag}')
    old = 'log advisory on default {'
    j = s.index(old, i)
    if j - i < 500:
        s = s[:j] + 'log advisory on 5 seconds {' + s[j + len(old):]
        changed += 1
open(p, 'w').write(s)
print("config.default geändert:", changed)
EOF

# pmlogger_check (Timer) würde die Config sonst per pmlogconf regenerieren
# und die Intervalle zurücksetzen:
if ! grep -qs '^PMLOGGER_CHECK_SKIP_LOGCONF=yes' "$SYSCONFIG"; then
    step "PMLOGGER_CHECK_SKIP_LOGCONF=yes setzen"
    echo 'PMLOGGER_CHECK_SKIP_LOGCONF=yes' >> "$SYSCONFIG"
fi

# --- 6. Cockpit-Plugin ------------------------------------------------------
step "Cockpit-Plugin installieren"
mkdir -p /usr/share/cockpit/gpu
install -m 644 cockpit-plugin/{manifest.json,index.html,gpu.js,gpu.css} /usr/share/cockpit/gpu/

# --- 7. pmlogger neu starten (nimmt neue Config/Intervalle) ----------------
step "pmlogger neu starten"
systemctl restart pmlogger

echo
echo "Fertig. Prüfen mit:"
echo "  pminfo amdgpu mmv.amdgpu            # Live-Werte"
echo "  sleep 30 && pmlogsummary -y \\$(sudo ls -t /var/log/pcp/pmlogger/\\$(hostname) | grep -E '^[0-9]{8}' | head -1 | sed 's/\\.0$//') amdgpu.gpu.load mmv.amdgpu.gfxclk"
echo "  Cockpit neu laden → Menüpunkt „GPU“"
