#!/bin/bash
# Record docs/gui.png and docs/demo.gif (macOS, local terminal with Screen Recording permission).
# Uses the conda env "dlsite-renamer" if it can be found; DLSITE_PYTHON=/path/to/python overrides.
cd "$(dirname "$0")/.." || exit 1
PY="$DLSITE_PYTHON"
if [ -z "$PY" ]; then
    for base in "${CONDA_EXE%/bin/conda}" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" \
                "/opt/homebrew/Caskroom/miniconda/base"; do
        if [ -n "$base" ] && [ -x "$base/envs/dlsite-renamer/bin/python" ]; then
            PY="$base/envs/dlsite-renamer/bin/python"
            break
        fi
    done
fi
PY="${PY:-python3}"
if ! "$PY" -c "import tkinter, requests, sys; sys.exit(tkinter.TkVersion < 8.6)" 2>/dev/null; then
    echo "$PY cannot run the GUI (needs Tk 8.6 and requests)." >&2
    echo "Run: DLSITE_PYTHON=/path/to/envs/dlsite-renamer/bin/python $0" >&2
    exit 1
fi
exec "$PY" tools/record_demo.py "$@"
