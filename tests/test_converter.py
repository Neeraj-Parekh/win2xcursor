"""Unit tests for win2xcursor's converter core.

No fixture files needed: .cur blobs and .zips are synthesized in memory.
Run:  python3 -m unittest discover -s tests -v
"""
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import cursor_converter as C


def make_png(w=4, h=4, color=(200, 50, 30, 255)):
    im = Image.new("RGBA", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def make_cur(png, xh=1, yh=2, w=4, h=4):
    off = 6 + 16
    return (struct.pack("<HHH", 0, 2, 1)
            + struct.pack("<BBBBHHII", w, h, 0, 0, xh, yh, len(png), off)
            + png)


def make_zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, make_cur(make_png()))
    buf.seek(0)
    return zipfile.ZipFile(buf)


class TestStaticCur(unittest.TestCase):
    def test_roundtrip_keeps_hotspot(self):
        frames = C.static_frames(make_cur(make_png(), xh=1, yh=2), "Normal Select")
        self.assertTrue(frames)
        rgba, _delay, xh, yh, _w, _h = frames[0]
        self.assertEqual((xh, yh), (1, 2))

    def test_xcur_magic_and_bgra_order(self):
        red_png = make_png(color=(200, 50, 30, 255))  # R200 G50 B30
        frames = C.static_frames(make_cur(red_png), "arrow")
        xc = C.xcur_from_frames(frames)
        self.assertEqual(xc[:4], b"Xcur")
        # pixel payload starts after 16-byte file header + 12-byte TOC + 36-byte chunk header
        px = xc[16 + 12 + 36:16 + 12 + 36 + 4]
        self.assertEqual(tuple(px), (30, 50, 200, 255))  # B,G,R,A on disk

    def test_garbage_returns_none(self):
        self.assertIsNone(C.static_frames(b"not a cursor at all", "arrow"))
        self.assertIsNone(C.parse_ani(b"not a cursor at all", "busy"))


class TestRoles(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "Normal Select": "left_ptr",
            "Busy": "watch",
            "Working in Background": "left_ptr_watch",
            "Link Select": "hand2",
            "Text Select": "text",
            "Diagonal Resize 1": "size_fdiag",
            "Horizontal Resize": "sb_h_double_arrow",
            "Move": "fleur",
            "Unavailable": "crossed_circle",
            "Help Select": "help",
        }
        for name, role in cases.items():
            with self.subTest(name=name):
                self.assertEqual(C.role_for(name), role)

    def test_unknown_falls_back_to_arrow(self):
        self.assertEqual(C.role_for("zzz-no-such-cursor-zzz"), "left_ptr")


class TestHelpers(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(C.sanitize("Anya Melfissa Cursor!"), "Anya-Melfissa-Cursor")
        self.assertEqual(C.sanitize("..."), "Theme")

    def test_parse_recolor_ok(self):
        self.assertEqual(C.parse_recolor("200,60,120"), (200, 60, 120))

    def test_parse_recolor_bad(self):
        for bad in ("abc", "1,2", "300,0,0", "-1,0,0", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    C.parse_recolor(bad)

    def test_zip_variants_groups_by_folder(self):
        z = make_zip(["dark/arrow.cur", "dark/busy.ani", "light/arrow.cur", "loose.cur"])
        groups = C._zip_variants(z)
        self.assertEqual(set(groups), {"dark", "light", "."})

    def test_missing_input_raises_cleanly(self):
        with self.assertRaises(FileNotFoundError):
            C.convert_input("/tmp/win2xcursor-no-such-file.zip", "X", out_dir="/tmp/x")


class TestThemeAssembly(unittest.TestCase):
    def test_write_theme_layout(self):
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "My-Theme")
            C.write_theme({"left_ptr": (xc, 1, 0)}, tdir, "My-Theme", "Vimix-cursors")
            self.assertTrue(os.path.isfile(os.path.join(tdir, "cursors", "left_ptr")))
            with open(os.path.join(tdir, "index.theme")) as f:
                content = f.read()
            self.assertIn("Name=My-Theme", content)
            self.assertIn("Inherits=Vimix-cursors", content)
            if hasattr(os, "symlink"):
                self.assertTrue(os.path.exists(os.path.join(tdir, "cursors", "arrow")))


if __name__ == "__main__":
    unittest.main()
