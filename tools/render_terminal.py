"""Render a captured terminal log into a PNG that looks like a terminal window.

Usage: python tools/render_terminal.py <input.log> <output.png> "<title>"
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

inp, outp, title = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "PyClaw demo")
text = Path(inp).read_text(encoding="utf-8").rstrip("\n")
lines = text.split("\n")

# Layout
PAD = 22
TITLEBAR = 36
LINE_H = 20
FONT_SIZE = 14
MAXCOLS = max((len(l) for l in lines), default=80)

def load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else None,
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

font = load_font(FONT_SIZE)
tfont = load_font(13)

# Measure char width with the mono font
tmp = Image.new("RGB", (10, 10))
d0 = ImageDraw.Draw(tmp)
cw = d0.textlength("M", font=font) or 8.5
char_w = cw

W = int(PAD * 2 + char_w * MAXCOLS) + 8
H = TITLEBAR + PAD * 2 + LINE_H * len(lines)
W = max(W, 720)

BG = (13, 17, 23)        # GitHub dark
TITLE_BG = (32, 38, 46)
FG = (201, 209, 217)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
CYAN = (57, 197, 207)
DIM = (139, 148, 158)
YELLOW = (210, 168, 88)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# Title bar + traffic lights
d.rectangle([0, 0, W, TITLEBAR], fill=TITLE_BG)
for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
    d.ellipse([16 + i * 22, 12, 28 + i * 22, 24], fill=col)
d.text((W // 2 - d0.textlength(title, font=tfont) / 2, 10), title, font=tfont, fill=DIM)

def color_for(line: str):
    s = line.strip()
    if s.startswith("=" * 5) or s.startswith("-" * 5):
        return DIM
    if "PASS" in line or "-> True" in line or s.startswith(">>> PASS") or "RESULT:" in line:
        return GREEN
    if "BLOCK" in line or "blocked" in line or "FAIL" in line or "can't" in line.lower() or "can’t" in line.lower():
        return RED
    if s.startswith("Q:") or s.startswith("PART") or s.startswith("SCENARIO"):
        return CYAN
    if s.startswith("A:") or "AUDIT" in line or "FILES WRITTEN" in line:
        return YELLOW
    return FG

y = TITLEBAR + PAD
for line in lines:
    d.text((PAD, y), line, font=font, fill=color_for(line))
    y += LINE_H

img.save(outp)
print(f"wrote {outp} ({W}x{H})")
