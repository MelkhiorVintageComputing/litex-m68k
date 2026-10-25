#!/bin/sh
# Create ~/LITEX-68K/litex-venv: a venv that uses the LiteX repositories in ~/LITEX
# without modifying them.
#
# The repositories are not pip-installed (an editable install rewrites their
# *.egg-info); they are put on sys.path by one .pth file, and bytecode goes to
# ~/LITEX-68K/.pycache instead of __pycache__ inside them. A patched copy in
# ~/LITEX-68K/<repo> (see patches/README.md) takes the place of ~/LITEX/<repo>.
#
# Usage: scripts/setup_venv.sh            create, or refresh the .pth and wrappers
#        scripts/setup_venv.sh --force    recreate from scratch
set -eu

LITEX=${LITEX:-$HOME/LITEX}
TOP=${LITEX68K:-$HOME/LITEX-68K}
VENV=$TOP/litex-venv
HERE=$(cd "$(dirname "$0")/.." && pwd)

# The repositories LiteX needs, as in ~/LITEX/litex-venv.
REPOS="migen litex litex-boards litedram liteeth litei2c liteiclink litejesd204
       litepcie litesata litescope litesdcard litespi litedsp"

if [ "${1:-}" = "--force" ]; then
    rm -rf "$VENV"
fi

if [ ! -x "$VENV/bin/python" ]; then
    python3.13 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$HERE/scripts/requirements.txt"
fi

SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
PTH=$SITE/litex-68k.pth

# One path per line; the first line is executed (a .pth "import" line) and moves
# bytecode out of the source trees.
{
    echo "import sys; sys.pycache_prefix = '$TOP/.pycache'"
    for r in $REPOS $(cd "$LITEX" && ls -d pythondata-*); do
        if [ -d "$TOP/$r" ]; then
            echo "$TOP/$r"
        else
            echo "$LITEX/$r"
        fi
    done
    echo "$TOP/pythondata-cpu-rd68031"
    echo "$TOP/pythondata-cpu-rd68041"
    echo "$HERE"
} > "$PTH"

# LiteX's console scripts (litex/setup.py, entry_points).
while read -r name target; do
    mod=${target%:*}
    fn=${target#*:}
    cat > "$VENV/bin/$name" <<EOF
#!$VENV/bin/python
import sys
from $mod import $fn
sys.exit($fn())
EOF
    chmod +x "$VENV/bin/$name"
done <<EOF
litex_term litex.tools.litex_term:main
litex_server litex.tools.litex_server:main
litex_jtag litex.tools.litex_jtag:main
litex_cli litex.tools.litex_client:main
litex_soc_gen litex.tools.litex_soc_gen:main
litex_periph_gen litex.tools.litex_periph_gen:main
litex_sim litex.tools.litex_sim:main
litex_bare_metal_demo litex.soc.software.demo.demo:main
litex_json2dts_linux litex.tools.litex_json2dts_linux:main
litex_json2dts_zephyr litex.tools.litex_json2dts_zephyr:main
litex_json2renode litex.tools.litex_json2renode:main
litex_read_verilog litex.tools.litex_read_verilog:main
EOF

echo "venv ready: source $VENV/bin/activate"
