#!/bin/bash
# Deinstallation des Cockpit-GPU-Moduls. Löscht keine PCP-Archive.
set -euo pipefail
cd "$(dirname "$0")"

step() { echo "== $*"; }

step "Cockpit-Plugin entfernen"
rm -rf /usr/share/cockpit/gpu

step "MMV-Exporter deaktivieren"
systemctl disable --now amdgpu-mmv.service || true
rm -f /etc/systemd/system/amdgpu-mmv.service /usr/local/bin/amdgpu-gtt-mmv
systemctl daemon-reload

step "pmlogconf-Gruppen entfernen und Config neu generieren"
rm -rf /var/lib/pcp/config/pmlogconf/gpu
pmlogconf -q /var/lib/pcp/config/pmlogger/config.default >/dev/null
# Hinweis: PMLOGGER_CHECK_SKIP_LOGCONF in /etc/sysconfig/pmlogger bleibt
# erhalten (von Hand entfernen, falls unerwünscht).
systemctl restart pmlogger

echo "Fertig. (pcp-pmda-amdgpu kann mit dnf remove entfernt werden;"
echo "      PMDA-Registrierung dann vorher: cd /var/lib/pcp/pmdas/amdgpu && sudo ./Remove)"
