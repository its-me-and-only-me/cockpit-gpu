# cockpit-gpu: PID-namespace-sichere PCP-Prozesssuche (Incus/LXC)
#
# PCPs _get_pids_by_name() in /etc/pcp.env identifiziert Dämone über ps und
# filtert Fremdcontainer nur anhand von libpod/crun-Cgroup-Mustern
# (0::/machine.slice/*/container/*). Incus-/LXC-Container treffen das nicht
# und landen im "unbekannt"-Zweig -> sie werden mitgezählt und beim
# Dienst-Stop/-Start mitgetötet versucht. Aus dem Host sieht der Service-
# Nutzer 'pcp' die gemappte Container-UID nicht als eigene -> kein Signal
# kommt an -> "Warning: pmproxy won't die!" und der Host-Dienst startet
# gar nicht erst.
#
# Dieser Block vergleicht stattdessen den PID-Namespace (exakt,
# containermanager-unabhängig, auch bei Verschachtelung) und ignoriert
# Zombies (tot, aber vom Elternprozess noch nicht reapbar — auf die
# wartet kein Stop-Loop sinnvoll).
#
# ACHTUNG: /etc/pcp.env wird rekursiv/quell-mehrfach gesourct. Der Wrapper
# muss daher bei JEDEM Sourcing neu definiert werden (sonst gewinnt eine
# spätere Roh-Definition); das Original wird nur einmal gesichert.
#
# Wird vom Installer idempotent zwischen den Marker-Zeilen in
# /etc/pcp.env injected; uninstall.sh entfernt den Block wieder.
# ACHTUNG: /etc/pcp.env ist pures %config (ohne noreplace) — ein PCP-Update
# ersetzt die Datei (unsere Änderung landet in .rpmsave). Nach Updates
# daher install.sh erneut laufen lassen; der Block wird dann neu gesetzt.

if declare -F _get_pids_by_name >/dev/null 2>&1
then
    if ! declare -F __cgpu_get_pids_by_name_orig >/dev/null 2>&1
    then
        eval "$(declare -f _get_pids_by_name |\
               sed '1s/^_get_pids_by_name/__cgpu_get_pids_by_name_orig/')"
    fi

    # PIDs mit Prozess-/Binary-Namen $1 im eigenen PID-Namespace
    __cgpu_local_pids()
{
    __cgpu_self=$(readlink /proc/self/ns/pid 2>/dev/null)
    for __cgpu_p in /proc/[0-9]*
    do
        __cgpu_p=${__cgpu_p#/proc/}
        # fremde Namespaces (und via Yama nicht lesbare Prozesse) überspringen
        [ "$(readlink "/proc/$__cgpu_p/ns/pid" 2>/dev/null)" = "$__cgpu_self" ] || continue
        # Zombies überspringen — die überleben jeden SIGKILL bis zum Reap
        grep -q '^State:[[:space:]]*Z' "/proc/$__cgpu_p/status" 2>/dev/null && continue
        case "$(readlink -f "/proc/$__cgpu_p/exe" 2>/dev/null)" in
            */"$1"|"$1") echo "$__cgpu_p" ;;
        esac
    done
}

    _get_pids_by_name()
{
    __cgpu_self=$(readlink /proc/self/ns/pid 2>/dev/null)
    __cgpu_get_pids_by_name_orig "$@" | while read -r __cgpu_pid
    do
        case "$__cgpu_pid" in
        ''|*[!0-9]*)      echo "$__cgpu_pid"; continue ;;
        esac
        __cgpu_ns=$(readlink "/proc/$__cgpu_pid/ns/pid" 2>/dev/null)
        [ -n "$__cgpu_ns" ] && [ "$__cgpu_ns" = "$__cgpu_self" ] &&\
            ! grep -q '^State:[[:space:]]*Z' "/proc/$__cgpu_pid/status" 2>/dev/null &&\
            echo "$__cgpu_pid"
    done
}

    # pmsignal kann ebenfalls mit Prozessnamen füttern; Namen vor dem
    # Signalisieren auf den eigenen Namespace herunterbrechen, damit aus
    # dem Host nie ein Container-Daemon (z. B. bei privilegierten
    # Containern mit gleicher UID) getroffen wird.
    if command -v pmsignal >/dev/null 2>&1
    then
        pmsignal()
{
            case " $* " in
            *" -s "*) ;;
            *) command pmsignal "$@"; return $? ;;
            esac
            __cgpu_sig='' __cgpu_tgts=''
            while [ $# -gt 0 ]
            do
                case "$1" in
                -s) shift; __cgpu_sig="$1" ;;
                -a|-p) ;;
                *)
                    case "$1" in
                    ''|*[!0-9]*) __cgpu_tgts="$__cgpu_tgts $(__cgpu_local_pids "$1")" ;;
                    *)           __cgpu_tgts="$__cgpu_tgts $1" ;;
                    esac
                    ;;
                esac
                shift
            done
            # shellcheck disable=SC2086  # absichtliche Wort-Zerlegung der PID-Liste
            set -- $__cgpu_tgts
            [ $# -gt 0 ] || return 0
            command pmsignal -s "${__cgpu_sig:-TERM}" "$@"
        }
    fi
fi
