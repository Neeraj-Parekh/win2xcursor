"""Loader-level regression test: every cursor name our themes claim must load
through the REAL libXcursor (the same call Mutter makes), at multiple sizes.

This is the test that would have caught the drag-cursor white box: 'grab'
resolved on disk yet failed XcursorLibraryLoadImages.
Skipped where libXcursor is absent (CI without X libs).
"""
import ctypes
import ctypes.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cursor_converter as C

LIB = ctypes.util.find_library("Xcursor")
HAS_XCURSOR = LIB is not None

# Every name any client stack can request: Wayland cursor-shape enum,
# Qt QCursor table, X cursor-font names, Adwaita reference, legacy hashes.
UNIVERSE = (
    # Wayland wp_cursor_shape_device_v1
    "default context-menu help pointer progress wait cell crosshair text "
    "vertical-text alias copy move no-drop not-allowed grab grabbing "
    "e-resize n-resize ne-resize nw-resize s-resize se-resize sw-resize "
    "w-resize ew-resize ns-resize nesw-resize nwse-resize col-resize "
    "row-resize all-scroll zoom-in zoom-out dnd-ask all-resize "
    # Qt QCursor table
    "up_arrow ibeam size_ver size_hor size_bdiag size_fdiag size_all "
    "split_v split_h pointing_hand whats_this openhand closedhand "
    "dnd-move dnd-copy dnd-link "
    # X core extras + twin themes
    "X_cursor x_cursor arrow left_ptr top_left_arrow ul_angle ur_angle "
    "hand hand1 hand2 link cross tcross plus cross_reverse diamond_cross "
    "ibeam pencil help question_arrow fleur move pointer_move size_all "
    "all-resize all-scroll sb_h_double_arrow h_double_arrow col-resize "
    "e-resize w-resize ew-resize left_side right_side sb_v_double_arrow "
    "v_double_arrow row-resize n-resize s-resize ns-resize top_side "
    "bottom_side size_fdiag fd_double_arrow nwse-resize nw-resize se-resize "
    "top_left_corner bottom_right_corner size_bdiag bd_double_arrow "
    "nesw-resize ne-resize sw-resize top_right_corner bottom_left_corner "
    "crossed_circle not-allowed forbidden no_drop dnd-no-drop wait "
    "2825c929d5411e5819219e75ddaf8b9c left_ptr_watch "
    "08e8e1c95fe2fc01f976f1e063a24c0d progress app_starting "
    "grabbing dnd-move grab handwriting copy cell crosshair-cell "
    "zoom-in zoom-out context-menu half-busy pointer-move clock "
    "sb_up_arrow sb_down_arrow sb_left_arrow sb_right_arrow top_tee "
    "bottom_tee left_tee right_tee ll_angle lr_angle double_arrow draft "
    "draft_large draft_small circle dot dotbox target color-picker spraycan "
    "pirate iron_cross exchange shuttle sizing man trek gumby heart star "
    "umbrella sailboat boat coffee_mug icon mouse leftbutton rightbutton "
    "rtl_logo bogosity spider wayland-cursor box_spiral draped_box gobbler "
    "dnd-none handwriting"
).split()


@unittest.skipUnless(HAS_XCURSOR, "libXcursor unavailable")
class TestRealLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(LIB)
        cls.lib.XcursorLibraryLoadImages.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        cls.lib.XcursorLibraryLoadImages.restype = ctypes.c_void_p
        cls.lib.XcursorImagesDestroy.argtypes = [ctypes.c_void_p]
        # minimal theme: three roles; links must cover the whole universe
        from tests.test_converter import make_cur, make_png
        frames = C.static_frames(make_cur(make_png()), "arrow")
        xc = C.xcur_from_frames(frames)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.theme = f"Win2xLoaderProbe{os.getpid()}"
        C.write_theme({"left_ptr": (xc, 1, 0), "fleur": (xc, 1, 0),
                       "hand2": (xc, 1, 0), "text": (xc, 1, 0),
                       "crosshair": (xc, 1, 0), "help": (xc, 1, 0),
                       "watch": (xc, 1, 0)},
                      os.path.join(cls.tmp.name, cls.theme),
                      cls.theme, "Adwaita")
        cls._old_path = os.environ.get("XCURSOR_PATH")
        os.environ["XCURSOR_PATH"] = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        if cls._old_path is None:
            os.environ.pop("XCURSOR_PATH", None)
        else:
            os.environ["XCURSOR_PATH"] = cls._old_path
        cls.tmp.cleanup()

    def test_universe_loads_at_all_sizes(self):
        missing = []
        for size in (24, 48, 96):
            for name in UNIVERSE:
                p = self.lib.XcursorLibraryLoadImages(
                    name.encode(), self.theme.encode(), size)
                if not p:
                    missing.append(f"{name}@{size}")
                else:
                    self.lib.XcursorImagesDestroy(p)
        self.assertEqual(missing, [], f"{len(missing)} names fail the real loader")


if __name__ == "__main__":
    unittest.main()
