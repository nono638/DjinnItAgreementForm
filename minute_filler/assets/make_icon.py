"""Draws the app icon (a form with a pen) and writes app.ico. Run once; the .ico is checked in."""
from pathlib import Path
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((20, 20, 236, 236), radius=48, fill=(37, 99, 235, 255))
d.rounded_rectangle((70, 50, 186, 206), radius=10, fill=(255, 255, 255, 255))
for i, y in enumerate(range(80, 180, 22)):
    d.rounded_rectangle((88, y, 168 - (30 if i % 2 else 0), y + 8), radius=4, fill=(191, 208, 245, 255))
d.polygon([(150, 196), (206, 120), (222, 134), (166, 210), (146, 216)], fill=(245, 165, 36, 255))
out = Path(__file__).with_name("app.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix(".png"))
print(out)
