"""Tests for gui.py pure helpers (no display needed).

Skipped entirely when tkinter is unavailable — importing gui requires it.
App widget behavior is still verified via screenshots, not here.
Run under a tkinter python:  /usr/bin/python3 -m unittest discover -s tests -v
"""
import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import gui  # noqa: F401  (needs working tkinter)
    import cursor_converter as C
    from PIL import Image
    HAS_GUI = True
except Exception:
    HAS_GUI = False


@unittest.skipUnless(HAS_GUI, "tkinter unavailable")
class TestPreviewDecode(unittest.TestCase):
    def _write_xcur(self, d, frames):
        xc = C.xcur_from_frames(frames)
        p = os.path.join(d, "c")
        with open(p, "wb") as f:
            f.write(xc)
        return p

    def test_picks_largest_chunk_not_first(self):
        frames = C.static_frames(_cur(_png(128, 128)), "arrow")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write_xcur(tmp, frames)
            im = gui.xcur_first_frame(p, size=200)
            self.assertIsNotNone(im)
            # ladder writes 24px first; preview must use the 128px image
            self.assertEqual(im.size, (128, 128))

    def test_thumbnail_bounds(self):
        frames = C.static_frames(_cur(_png(128, 128)), "arrow")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write_xcur(tmp, frames)
            im = gui.xcur_first_frame(p)  # default preview size
            self.assertLessEqual(max(im.size), 104)

    def test_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "c")
            for blob in (b"", b"Xcur", b"\x00" * 64, bytes(range(256))):
                with open(p, "wb") as f:
                    f.write(blob)
                self.assertIsNone(gui.xcur_first_frame(p))

    def test_follows_valid_redirect_rejects_dotdot(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = C.static_frames(_cur(_png()), "arrow")
            with open(os.path.join(tmp, "real"), "wb") as f:
                f.write(C.xcur_from_frames(frames))
            with open(os.path.join(tmp, "redir"), "wb") as f:
                f.write(b"..")
            with open(os.path.join(tmp, "ok"), "wb") as f:
                f.write(b"real")
            self.assertIsNone(gui.xcur_first_frame(os.path.join(tmp, "redir")))
            good = gui.xcur_first_frame(os.path.join(tmp, "ok"), size=200)
            self.assertIsNotNone(good)

    def test_checkerboard(self):
        bg = gui.checkerboard(104)
        self.assertEqual(bg.size, (104, 104))
        self.assertEqual(bg.mode, "RGBA")
        # two adjacent tiles differ
        self.assertNotEqual(bg.getpixel((4, 4)), bg.getpixel((12, 4)))


@unittest.skipUnless(HAS_GUI, "tkinter unavailable")
class TestThemeScan(unittest.TestCase):
    def test_installed_themes_and_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = os.path.join(tmp, "T")
            os.makedirs(os.path.join(t, "cursors"))
            with open(os.path.join(t, "index.theme"), "w") as f:
                f.write("[Icon Theme]\nName=T\nInherits=Adwaita\n")
            old, gui.SEARCH = gui.SEARCH, [tmp]
            try:
                found = gui.installed_themes()
            finally:
                gui.SEARCH = old
            self.assertEqual(found, {"T": t})
            self.assertEqual(gui.inherited_from(t), "Adwaita")
            self.assertEqual(gui.inherited_from(os.path.join(tmp, "nope")), "")


def _png(w=4, h=4, color=(200, 50, 30, 255)):
    im = Image.new("RGBA", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _cur(png, xh=1, yh=2, w=4, h=4):
    off = 6 + 16
    return (struct.pack("<HHH", 0, 2, 1)
            + struct.pack("<BBBBHHII", w, h, 0, 0, xh, yh, len(png), off)
            + png)


if __name__ == "__main__":
    unittest.main()
