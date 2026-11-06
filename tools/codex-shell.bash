# Source this file by its absolute path from ~/.bashrc.
# Plain `codex` starts the workflow only in this checkout (including subfolders).
codex() {
    local esd_root esd_checkout
    esd_checkout="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return
    esd_root="$(command git rev-parse --show-toplevel 2>/dev/null)" || esd_root=''
    if [[ $# -eq 0 && "$esd_root" == "$esd_checkout" ]]; then
        command bash "$esd_checkout/tools/codex-start.sh"
    else
        command codex "$@"
    fi
}
