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


class TestDropParsing(unittest.TestCase):
    def test_braced_paths_with_spaces(self):
        data = "{/tmp/My Cursor Pack/a.zip} /tmp/b.cur"
        self.assertEqual(C.parse_drop_files(data),
                         ["/tmp/My Cursor Pack/a.zip", "/tmp/b.cur"])

    def test_ignores_blank(self):
        self.assertEqual(C.parse_drop_files(""), [])

    def test_is_cursor_source(self):
        self.assertTrue(C.is_cursor_source("/tmp/x.zip"))
        self.assertTrue(C.is_cursor_source("/tmp/x.ANI"))
        self.assertTrue(C.is_cursor_source("/tmp/x.cur"))
        self.assertFalse(C.is_cursor_source("/tmp/x.inf"))
        self.assertFalse(C.is_cursor_source("/tmp/x.jpg"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(C.is_cursor_source(tmp))


class TestThemeAssembly(unittest.TestCase):
    def test_write_theme_layout(self):
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "My-Theme")
            C.write_theme({"left_ptr": (xc, 1, 0)}, tdir, "My-Theme", "Adwaita")
            self.assertTrue(os.path.isfile(os.path.join(tdir, "cursors", "left_ptr")))
            with open(os.path.join(tdir, "index.theme")) as f:
                content = f.read()
            self.assertIn("Name=My-Theme", content)
            self.assertIn("Inherits=Adwaita", content)
            if hasattr(os, "symlink"):
                self.assertTrue(os.path.exists(os.path.join(tdir, "cursors", "arrow")))


class TestSpecCompliance(unittest.TestCase):
    # Names from Xcursor(3)/Adwaita reference + ArchWiki troubleshooting:
    # every one must resolve to a real file via roles or ALIASES links.
    LEGACY_NAMES = ["top_left_arrow", "ul_angle", "ur_angle", "X_cursor",
                    "hand", "xterm", "ibeam", "plus", "cross_reverse",
                    "diamond_cross", "question_arrow", "all-resize", "no-drop",
                    "top_side", "bottom_side", "left_side", "right_side",
                    "top_left_corner", "top_right_corner",
                    "bottom_left_corner", "bottom_right_corner",
                    "ew-resize", "ns-resize", "ne-resize", "nw-resize",
                    "se-resize", "sw-resize"]

    def test_legacy_names_all_aliased(self):
        flat = set()
        for role, targets in C.ALIASES.items():
            flat.add(role)
            flat.update(targets)
        for name in self.LEGACY_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, flat, f"{name} has no role or alias mapping")

    def test_aliases_land_on_disk(self):
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        roles = {"left_ptr": (xc, 1, 0), "hand2": (xc, 1, 0),
                 "text": (xc, 1, 0), "crosshair": (xc, 1, 0),
                 "help": (xc, 1, 0), "size_all": (xc, 1, 0),
                 "sb_h_double_arrow": (xc, 1, 0),
                 "sb_v_double_arrow": (xc, 1, 0),
                 "size_fdiag": (xc, 1, 0), "size_bdiag": (xc, 1, 0),
                 "crossed_circle": (xc, 1, 0)}
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "T")
            C.write_theme(roles, tdir, "T", "Adwaita")
            cdir = os.path.join(tdir, "cursors")
            for name in self.LEGACY_NAMES:
                with self.subTest(name=name):
                    self.assertTrue(os.path.exists(os.path.join(cdir, name)),
                                    f"{name} missing from built theme")

    def test_multisize_ladder(self):
        frames = C.static_frames(make_cur(make_png(128, 128)), "arrow")
        xc = C.xcur_from_frames(frames)
        import struct as _st
        n = _st.unpack("<I", xc[12:16])[0]
        sizes = set()
        for i in range(n):
            _t, _s, pos = _st.unpack_from("<III", xc, 16 + 12 * i)
            _h, _ty, _su, _v, w, h2, _xh, _yh, _d = _st.unpack_from("<9I", xc, pos)
            sizes.add((w, h2))
        for s in (24, 32, 48, 64, 96, 128):
            self.assertIn((s, s), sizes, f"size {s} missing from ladder")

    def test_install_theme_uses_default_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "Theme")
            os.makedirs(os.path.join(src, "cursors"))
            open(os.path.join(src, "cursors", "left_ptr"), "wb").write(b"Xcur")
            got = C.install_theme(src, dest_base=tmp)
            self.assertTrue(os.path.isfile(os.path.join(got, "cursors", "left_ptr")))


if __name__ == "__main__":
    unittest.main()
