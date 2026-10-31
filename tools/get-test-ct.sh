#!/bin/bash
# Download public test chest CTs (LIDC-IDRI, DICOM) to ../dicom-test/
set -e
cd "$(dirname "$0")/.."
TOOLS="$HOME/.relecto-tools"
if [ ! -x "$TOOLS/bin/python" ]; then
  echo "Installing the download tool (one time)…"
  python3 -m venv "$TOOLS"
  "$TOOLS/bin/pip" install -q --upgrade pip
  "$TOOLS/bin/pip" install -q idc-index
fi
exec "$TOOLS/bin/python" backend/tools/download_test_ct.py "$@"
