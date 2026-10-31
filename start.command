#!/bin/bash
# Double-cliquez pour lancer Relecto en local, puis ouvrez http://127.0.0.1:8000
cd "$(dirname "$0")"
(sleep 4 && open http://127.0.0.1:8000) &
./relecto serve
