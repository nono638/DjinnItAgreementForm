"""Makes the app's pictures from the artwork (generated with Google Gemini), kept out of git:

    .venv\\Scripts\\python.exe tools\\make_art.py [folder]     (folder: SillyImages by default)

The folder holds icon.jpg and icon_small.jpg (square), ready.jpg, stumped.jpg, newyear.jpg (wide), banner.jpg
(the website's link preview) and YinVideo1.mp4 (the swirling yin-yang). Written:
  minute_filler/assets/app.ico          the program's icon: icon_small.jpg up to 32 px, icon.jpg above
  minute_filler/assets/yin_*.jpg        the drop zone's pictures (working, done, stumped, newyear), 720 x 393
  minute_filler/assets/yin_working.webp the "working" picture, moving (a loop of the video)
  installer/wizard*.bmp, small*.bmp     the installer's pictures
  docs/                                 the website's icon, link preview, video and its first frame
  tools/yin_icon_source.jpg, tools/yin_icon_small_source.jpg   the icons' artwork, kept with the code
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "minute_filler" / "assets"
WIDE = (720, 393)       # the drop zone's pictures
LOOP_WIDTH = 560        # the moving picture: shown at most 300 px wide (x 1.5 on a high-DPI screen)
LOOP_FPS = 12
SMALL_SIZES = (16, 20, 24, 32)       # from the flat icon: it stays clear on the taskbar
LARGE_SIZES = (40, 48, 64, 96, 128, 256)


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """img cut to the shape of size (from the middle) and scaled to it."""
    w, h = img.size
    want = size[0] / size[1]
    if w / h > want:
        nw = round(h * want)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / want)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize(size, Image.LANCZOS)


def rounded_square(img: Image.Image, size: int) -> Image.Image:
    """A square icon of `size` px with rounded corners (transparent outside them)."""
    out = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    scale = 4  # the corners drawn larger and scaled down: smooth edges
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * scale - 1, size * scale - 1),
                                           radius=round(size * scale * 0.2), fill=255)
    out.putalpha(mask.resize((size, size), Image.LANCZOS))
    return out


def make_icon(big: Image.Image, small: Image.Image) -> None:
    """app.ico with a picture for each size Windows uses."""
    images = [rounded_square(small, n) for n in SMALL_SIZES] + [rounded_square(big, n) for n in LARGE_SIZES]
    largest = images[-1]
    largest.save(ASSETS / "app.ico", sizes=[im.size for im in images], append_images=images[:-1])


def video_frames(path: Path) -> list[tuple[int, Image.Image]]:
    """(milliseconds, frame) of the video, as it plays (Qt reads it: no other video library needed)."""
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["make_art", "-platform", "offscreen"])
    player, sink = QMediaPlayer(), QVideoSink()
    player.setVideoSink(sink)
    frames: list[tuple[int, Image.Image]] = []

    def got(frame):
        if frame.isValid():
            img = frame.toImage().convertToFormat(frame.toImage().Format.Format_RGB888)
            data = bytes(img.constBits())[:img.sizeInBytes()]
            pil = Image.frombuffer("RGB", (img.width(), img.height()), data, "raw", "RGB", img.bytesPerLine(), 1)
            frames.append((frame.startTime() // 1000, pil.copy()))

    sink.videoFrameChanged.connect(got)
    player.mediaStatusChanged.connect(lambda st: app.quit() if st == QMediaPlayer.EndOfMedia else None)
    player.errorOccurred.connect(lambda e, msg: (print("video:", msg), app.quit()))
    player.setSource(QUrl.fromLocalFile(str(path)))
    player.play()
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    return frames


def make_loop(video: Path) -> Image.Image:
    """yin_working.webp, a loop of the video; returns its first frame."""
    frames = video_frames(video)
    if not frames:
        raise SystemExit(f"no frames could be read from {video}")
    end = frames[-1][0]
    size = (LOOP_WIDTH, round(LOOP_WIDTH * WIDE[1] / WIDE[0]))
    picked, t = [], 0
    while t <= end:  # the frame nearest each tick of LOOP_FPS
        picked.append(cover(min(frames, key=lambda f: abs(f[0] - t))[1], size))
        t += 1000 // LOOP_FPS
    picked[0].save(ASSETS / "yin_working.webp", save_all=True, append_images=picked[1:],
                   duration=1000 // LOOP_FPS, loop=0, quality=72, method=6)
    print(f"loop: {len(picked)} frames from {len(frames)}, "
          f"{(ASSETS / 'yin_working.webp').stat().st_size // 1024} KB")
    return frames[0][1]


def make_installer(big: Image.Image, small: Image.Image) -> None:
    """The installer's side picture (the icon on its night sky) and its small corner picture."""
    for name, (w, h) in (("wizard.bmp", (164, 314)), ("wizard@2x.bmp", (328, 628))):
        art = cover(big.convert("RGB"), (w, w))
        side = Image.new("RGB", (w, h), art.getpixel((w // 2, 1)))  # the sky above the yin-yang
        fade = Image.new("L", (w, w), 255)  # the art's top and bottom edges fade into the sky
        edge = w // 4
        for y in range(edge):
            ImageDraw.Draw(fade).line((0, y, w, y), fill=255 * y // edge)
            ImageDraw.Draw(fade).line((0, w - 1 - y, w, w - 1 - y), fill=255 * y // edge)
        side.paste(art, (0, (h - w) // 2), fade)
        side.save(ROOT / "installer" / name)
    for name, n in (("small.bmp", 55), ("small@2x.bmp", 110)):
        icon = rounded_square(small if n < 64 else big, n)
        flat = Image.new("RGB", (n, n), (255, 255, 255))  # (the installer's header is white)
        flat.paste(icon, (0, 0), icon)
        flat.save(ROOT / "installer" / name)


def main(folder: Path) -> None:
    """Writes every picture listed in the module docstring from the artwork in folder."""
    def art(name: str) -> Image.Image:
        return Image.open(folder / name).convert("RGB")

    big, small = art("icon.jpg"), art("icon_small.jpg")
    make_icon(big, small)
    first = make_loop(folder / "YinVideo1.mp4")
    for mood, img in (("working", first), ("done", art("ready.jpg")), ("stumped", art("stumped.jpg")),
                      ("newyear", art("newyear.jpg"))):
        cover(img, WIDE).save(ASSETS / f"yin_{mood}.jpg", quality=88)
    make_installer(big, small)
    docs = ROOT / "docs"
    rounded_square(big, 64).save(docs / "favicon.png")
    rounded_square(big, 180).save(docs / "apple-touch-icon.png")
    cover(art("banner.jpg"), (1200, 630)).save(docs / "banner.jpg", quality=85)
    cover(first, (1280, 720)).save(docs / "yin.jpg", quality=82)
    shutil.copyfile(folder / "YinVideo1.mp4", docs / "yin.mp4")
    big.save(ROOT / "tools" / "yin_icon_source.jpg", quality=92)
    small.save(ROOT / "tools" / "yin_icon_small_source.jpg", quality=92)
    print("done")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "SillyImages")
