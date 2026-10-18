#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
python3 -m coach init
if [ ! -f data/current.json ]; then
  python3 -m coach demo
fi
exec python3 -m coach serve "$@"
