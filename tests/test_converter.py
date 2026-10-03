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
        for bad in ("abc", "1,2", "1,2,3,4", "300,0,0", "-1,0,0", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    C.parse_recolor(bad)

    def test_check_theme_name(self):
        C._check_theme_name("My-Theme 2.0")
        for bad in ("", "  ", "a\nb", "x\x00y"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    C._check_theme_name(bad)

    def test_convert_sanitizes_theme_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.cur")
            open(src, "wb").write(make_cur(make_png()))
            out = os.path.join(tmp, "out")
            built = C.convert_input(src, theme_name="../evil", out_dir=out)
            self.assertEqual(list(built), ["evil"])
            self.assertTrue(os.path.isdir(os.path.join(out, "evil")))

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
            self.assertFalse(C.is_cursor_source(tmp))  # empty dir: nothing to convert
            open(os.path.join(tmp, "a.ani"), "wb").write(b"RIFF")
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
    # Every name here must resolve to a real file in a fully-built theme.
    # (alias/dnd-ask exist only in Adwaita and are covered by Inherits.)
    LEGACY_NAMES = ["top_left_arrow", "ul_angle", "ur_angle", "X_cursor",
                    "hand", "xterm", "ibeam", "plus", "cross_reverse",
                    "diamond_cross", "question_arrow", "all-resize", "no-drop",
                    "top_side", "bottom_side", "left_side", "right_side",
                    "top_left_corner", "top_right_corner",
                    "bottom_left_corner", "bottom_right_corner",
                    "ew-resize", "ns-resize", "ne-resize", "nw-resize",
                    "se-resize", "sw-resize"]

    def _full_roles(self):
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        return {r: (xc, 1, 0) for r in
                ["left_ptr", "hand2", "text", "crosshair", "help", "size_all",
                 "sb_h_double_arrow", "sb_v_double_arrow", "size_fdiag",
                 "size_bdiag", "crossed_circle", "fleur", "watch",
                 "left_ptr_watch", "grabbing", "pencil", "copy", "cell",
                 "size_all", "zoom_in", "zoom_out", "context-menu"]}

    def test_alias_keys_are_producible_roles(self):
        """No dead ALIASES keys: every key must be producible by role_for."""
        producible = {C.role_for(n) for n in
                      ["arrow", "busy", "link select", "text select", "move",
                       "all scroll", "horizontal resize", "vertical resize",
                       "diagonal resize 1", "diagonal resize 2", "precision",
                       "unavailable", "help", "working", "handwriting", "copy",
                       "cell", "zoom in", "zoom out", "location", "pen",
                       "alias", "grab"]}
        producible.add("left_ptr")
        for key in C.ALIASES:
            with self.subTest(key=key):
                self.assertIn(key, producible, f"dead ALIASES key: {key}")

    def test_aliases_land_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "T")
            C.write_theme(self._full_roles(), tdir, "T", "Adwaita")
            cdir = os.path.join(tdir, "cursors")
            for name in self.LEGACY_NAMES:
                with self.subTest(name=name):
                    self.assertTrue(os.path.exists(os.path.join(cdir, name)),
                                    f"{name} missing from built theme")

    def test_single_role_theme_resolves_core_names(self):
        """A one-cursor theme must still resolve arrow/default links."""
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "One")
            C.write_theme({"left_ptr": (xc, 1, 0)}, tdir, "One", "Adwaita")
            cdir = os.path.join(tdir, "cursors")
            for name in ("arrow", "default", "top_left_arrow", "X_cursor"):
                self.assertTrue(os.path.exists(os.path.join(cdir, name)))

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

    def test_install_theme_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src", "Theme")
            os.makedirs(os.path.join(src, "cursors"))
            open(os.path.join(src, "cursors", "left_ptr"), "wb").write(b"Xcur")
            dst_base = os.path.join(tmp, "icons")
            got = C.install_theme(src, dest_base=dst_base)
            self.assertTrue(os.path.isfile(os.path.join(got, "cursors", "left_ptr")))

    def test_install_theme_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            C.install_theme("/")


class TestRobustness(unittest.TestCase):
    def test_truncated_icondir_no_crash(self):
        self.assertIsNone(C.parse_icondir(b"\x00\x00\x02\x01\xff\xff"))
        self.assertIsNone(C.parse_icondir(b"\x00\x00\x02\x01\x02\x00" + b"\x00" * 10))
        # dangling off/size entry is skipped, valid sibling still parsed
        import struct as _st
        good = make_cur(make_png())
        png = good[22:]
        head = _st.pack("<HHH", 0, 2, 2)          # cnt=2
        e_valid = _st.pack("<BBBBHHII", 4, 4, 0, 0, 1, 2, len(png), 6 + 32)
        e_bad = _st.pack("<BBBBHHII", 4, 4, 0, 0, 0, 0, 10, 99999)  # off past EOF
        blob = head + e_valid + e_bad + png
        got = C.parse_icondir(blob)
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 1)

    def test_garbage_never_raises(self):
        for blob in (b"", b"Xcur", b"RIFF....ACON" + b"\x00" * 40,
                     bytes(range(256)), b"\x00" * 1000):
            with self.subTest(blob=blob[:8]):
                try:
                    C.static_frames(blob, "x")
                    C.parse_ani(blob, "x")
                    C.cursors_from_single(blob, "x")
                except Exception as e:
                    self.fail(f"raised {type(e).__name__}: {e}")

    def test_dib_24bpp_color_order(self):
        import struct as _st
        w, h = 2, 2  # bottom-up, BGR triplets: pure red pixel first
        row = bytes([0, 0, 255, 0, 0, 255]) + b"\x00\x00"  # stride 8
        blob = (_st.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, 0, 0, 0, 0, 0)
                + row * 2 + b"\x00" * 8)
        (size, rgba) = C.dib_to_rgba(blob)
        self.assertEqual(size, (2, 2))
        self.assertEqual(tuple(rgba[:4]), (255, 0, 0, 255))  # R,G,B,A

    def test_dib_8bpp_palette_and_bounds(self):
        import struct as _st
        pal = bytes([30, 20, 10, 0, 0, 255, 0, 0])  # idx0=BGR(30,20,10), idx1=green
        row = bytes([0, 1]) + b"\x00\x00"
        blob = (_st.pack("<IiiHHIIiiII", 40, 2, 2, 1, 8, 0, 0, 0, 0, 2, 0)
                + pal + row * 2 + b"\x00" * 8)
        (size, rgba) = C.dib_to_rgba(blob)
        self.assertEqual(tuple(rgba[:4]), (10, 20, 30, 255))
        self.assertEqual(tuple(rgba[4:8]), (0, 255, 0, 255))
        bad = bytearray(blob)
        bad[40 + 8] = 9  # palette index out of range
        self.assertIsNone(C.dib_to_rgba(bytes(bad)))

    def test_dib_rejects_bogus_bpp(self):
        import struct as _st
        blob = _st.pack("<IiiHHIIiiII", 40, 4, 4, 1, 16, 0, 0, 0, 0, 0, 0) + b"\x00" * 64
        self.assertIsNone(C.dib_to_rgba(blob))

    def test_folder_accumulates_loose_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "pack")
            os.makedirs(src)
            open(os.path.join(src, "a.cur"), "wb").write(make_cur(make_png()))
            open(os.path.join(src, "b.cur"), "wb").write(make_cur(make_png()))
            out = os.path.join(tmp, "out")
            built = C.convert_input(src, theme_name="Pack", out_dir=out)
            cdir = os.path.join(built["Pack"], "cursors")
            self.assertGreaterEqual(len(os.listdir(cdir)), 1)

    def test_fallbacks_leave_no_essential_missing(self):
        """Even a one-cursor theme must resolve every FALLBACKS name."""
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "Min")
            C.write_theme({"left_ptr": (xc, 1, 0)}, tdir, "Min", "Adwaita")
            cdir = os.path.join(tdir, "cursors")
            for name, _cands in C.FALLBACKS:
                with self.subTest(name=name):
                    self.assertTrue(os.path.exists(os.path.join(cdir, name)),
                                    f"essential {name} unresolvable")

    def test_hotspot_clamped_in_ladder(self):
        import struct as _st
        frames = [(b"\xff\x00\x00\xff" * 16, 100, 500, 500, 4, 4)]
        xc = C.xcur_from_frames(frames)
        self.assertIsNotNone(xc)
        n = _st.unpack("<I", xc[12:16])[0]
        for i in range(n):
            _t, _s, pos = _st.unpack_from("<III", xc, 16 + 12 * i)
            _h, _ty, _su, _v, w, h2, xh, yh, _d = _st.unpack_from("<9I", xc, pos)
            self.assertLess(xh, w)
            self.assertLess(yh, h2)


if __name__ == "__main__":
    unittest.main()
