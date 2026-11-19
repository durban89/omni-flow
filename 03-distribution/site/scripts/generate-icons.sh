#!/usr/bin/env bash
# Regenerate CondomIQ favicons, app icons and the default social share image.
# Run from anywhere: bash 03-distribution/site/scripts/generate-icons.sh
# Requires ImageMagick 7 (`magick`).
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="static"
mkdir -p "$OUT"

TEAL="#0f766e"
INK="#101820"
MUTED="#5d6a76"
LINE="#dbe4e6"
PAPER="#f7faf9"

# --- geometry ---------------------------------------------------------------
# Shield + check mark, drawn at 512px so every raster size downscales cleanly.
SHIELD="path 'M 256,100 L 394,150 L 394,262 C 394,344 334,402 256,428 C 178,402 118,344 118,262 L 118,150 Z'"
CHECK="path 'M 196,256 L 236,296 L 318,212'"

draw_mark_rounded() {
  magick -size 512x512 xc:none \
    -fill "$TEAL" -draw 'roundrectangle 0,0 511,511 116,116' \
    -fill none -stroke white -strokewidth 30 -draw "$SHIELD" \
    -fill none -stroke white -strokewidth 34 -draw "stroke-linecap round stroke-linejoin round $CHECK" \
    "$1"
}

draw_mark_fullbleed() {
  magick -size 512x512 xc:"$TEAL" \
    -fill none -stroke white -strokewidth 30 -draw "$SHIELD" \
    -fill none -stroke white -strokewidth 34 -draw "stroke-linecap round stroke-linejoin round $CHECK" \
    "$1"
}

# --- raster marks -----------------------------------------------------------
draw_mark_rounded /tmp/condomiq-mark-rounded.png
draw_mark_fullbleed /tmp/condomiq-mark-square.png

# 32px: thicker strokes so the mark survives without a blur.
SHIELD_COMPACT="path 'M 256,96 L 400,148 L 400,264 C 400,350 336,410 256,438 C 176,410 112,350 112,264 L 112,148 Z'"
magick -size 512x512 xc:none \
  -fill "$TEAL" -draw 'roundrectangle 0,0 511,511 116,116' \
  -fill none -stroke white -strokewidth 42 -draw "$SHIELD_COMPACT" \
  -fill none -stroke white -strokewidth 50 \
  -draw "stroke-linecap round stroke-linejoin round path 'M 188,254 L 234,300 L 324,204'" \
  /tmp/condomiq-mark-compact.png

# 16px: a solid shield is the only shape that stays readable.
magick -size 512x512 xc:none \
  -fill "$TEAL" -draw 'roundrectangle 0,0 511,511 116,116' \
  -fill white -stroke none -draw "$SHIELD_COMPACT" \
  -fill "$TEAL" -stroke none -draw "stroke-linejoin round path 'M 196,254 L 238,298 L 322,206'" \
  /tmp/condomiq-mark-tiny.png

magick /tmp/condomiq-mark-rounded.png -resize 48x48 /tmp/condomiq-fav-48.png
magick /tmp/condomiq-mark-compact.png -resize 32x32 /tmp/condomiq-fav-32.png
magick /tmp/condomiq-mark-tiny.png -resize 16x16 /tmp/condomiq-fav-16.png
magick /tmp/condomiq-fav-48.png /tmp/condomiq-fav-32.png /tmp/condomiq-fav-16.png -strip "$OUT/favicon.ico"
magick /tmp/condomiq-mark-square.png -resize 180x180 -strip "$OUT/apple-touch-icon.png"
magick /tmp/condomiq-mark-square.png -resize 192x192 -strip "$OUT/icon-192.png"
magick /tmp/condomiq-mark-square.png -resize 512x512 -strip "$OUT/icon-512.png"

# --- vector favicon ---------------------------------------------------------
cat > "$OUT/favicon.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="CondomIQ">
  <rect width="512" height="512" rx="116" fill="#0f766e"/>
  <g fill="none" stroke="#fff" stroke-linejoin="round">
    <path d="M256 100 394 150v112c0 82-60 140-138 166-78-26-138-84-138-166V150Z" stroke-width="30"/>
    <path d="M196 256l40 40 82-84" stroke-width="34" stroke-linecap="round"/>
  </g>
</svg>
SVG

# --- default social share image (1200x630) ----------------------------------
magick -size 1200x630 "xc:$PAPER" \
  -fill none -stroke "#e2efed" -strokewidth 46 -draw 'circle 1010,315 1010,64' \
  -stroke "#eaf4f2" -strokewidth 26 -draw 'circle 1120,140 1120,26' \
  -stroke none -font Liberation-Sans-Bold -pointsize 88 -fill "$INK" -annotate +96+346 'CondomIQ' \
  -font Liberation-Sans -pointsize 34 -fill "$MUTED" -annotate +96+404 'Evidence-based condom & sexual health guides' \
  -stroke "$LINE" -strokewidth 2 -draw 'line 96,456 620,456' \
  -font Liberation-Sans-Bold -pointsize 27 -fill "$TEAL" -annotate +96+518 'condomiq.com' \
  \( /tmp/condomiq-mark-rounded.png -resize 132x132 \) -geometry +96+96 -composite \
  -strip "$OUT/og-default.png"

rm -f /tmp/condomiq-*.png
echo "generated:"
ls -1 "$OUT"/favicon.* "$OUT"/apple-touch-icon.png "$OUT"/icon-*.png "$OUT"/og-default.png
