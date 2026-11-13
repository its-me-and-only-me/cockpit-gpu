#!/bin/bash
# Deinstallation des Cockpit-GPU-Moduls. Löscht keine PCP-Archive.
set -euo pipefail
cd "$(dirname "$0")"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen (sudo ./uninstall.sh)" >&2; exit 1; }

step() { echo "== $*"; }

step "Cockpit-Plugin entfernen"
rm -rf /usr/local/share/cockpit/gpu /usr/share/cockpit/gpu

step "PCP-Namespace-Filter aus /etc/pcp.env entfernen"
python3 - <<'PYEOF'
import os

MARK_B = "# >>> cockpit-gpu: PID-namespace-sichere Prozesssuche >>>"
MARK_E = "# <<< cockpit-gpu: PID-namespace-sichere Prozesssuche <<<"
path = "/etc/pcp.env"
try:
    src = open(path).read()
except OSError:
    raise SystemExit(0)
if MARK_B in src and MARK_E in src:
    pre, rest = src.split(MARK_B, 1)
    _, post = rest.split(MARK_E, 1)
    tmp = path + ".tmp-uninstall"
    with open(tmp, "w") as f:
        f.write(pre.rstrip("\n") + "\n" + post.lstrip("\n"))
        os.fsync(f.fileno())
    os.replace(tmp, path)
    print("   (/etc/pcp.env: Namespace-Filter entfernt)")
else:
    print("   (kein Namespace-Filter gefunden — nichts zu tun)")
PYEOF

step "MMV-Exporter deaktivieren"
systemctl disable --now amdgpu-mmv.service || true
rm -f /etc/systemd/system/amdgpu-mmv.service /usr/local/bin/amdgpu-gtt-mmv
systemctl daemon-reload

if [ -e /var/lib/pcp/config/pmlogconf/gpu ]; then
    step "pmlogconf-Gruppen entfernen und Config neu generieren"
    rm -rf /var/lib/pcp/config/pmlogconf/gpu
    pmlogconf -q /var/lib/pcp/config/pmlogger/config.default >/dev/null
    timeout 120 systemctl restart pmlogger || \
        echo "WARNUNG: pmlogger-Neustart nicht durchgelaufen (ps -o pid,stat,cmd -C pmlogger)" >&2
else
    step "pmlogconf-Gruppen nicht vorhanden — Config bleibt unangetastet"
fi

# Hinweis: PMLOGGER_CHECK_SKIP_LOGCONF in /etc/sysconfig/pmlogger bleibt
# erhalten (von Hand entfernen, falls unerwünscht).

echo "Fertig. (pcp-pmda-amdgpu kann mit dnf remove entfernt werden;"
echo "      PMDA-Registrierung dann vorher: cd /var/lib/pcp/pmdas/amdgpu && sudo ./Remove)"
