#!/bin/bash
# Render flyer.html to deskman-flyer.pdf using headless Chrome.
cd "$(dirname "$0")"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT="deskman-flyer.pdf"
PROFILE="$(mktemp -d)"

rm -f "$OUT"
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
  --allow-file-access-from-files --virtual-time-budget=6000 \
  --user-data-dir="$PROFILE" \
  --print-to-pdf="$PWD/$OUT" \
  "file://$PWD/flyer.html" >/dev/null 2>&1 &

# Chrome writes the PDF but doesn't always exit, so wait for the file then stop it.
for i in $(seq 1 60); do
  sleep 1
  [ -s "$OUT" ] && break
done
sleep 2
pkill -f "$PROFILE" 2>/dev/null
sleep 1
rm -rf "$PROFILE" 2>/dev/null

if [ -s "$OUT" ]; then echo "Wrote $OUT"; else echo "Render failed"; exit 1; fi
