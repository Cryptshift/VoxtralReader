#!/usr/bin/env bash
# Double-click this file in Finder to launch Voxtral Reader.
# It opens in Terminal, starts both servers, then opens the browser.

cd "$(dirname "$0")"

# Open browser after a short delay (gives servers time to start)
(sleep 6 && open http://localhost:7860) &

bash start.sh
