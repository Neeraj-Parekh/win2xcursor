#!/usr/bin/env python3
"""Batch-convert Windows cursor packs into Linux XCursor themes.

Run:
  python3 batch_convert.py --src ~/Downloads/cursors --out ~/.local/share/icons
  python3 batch_convert.py --src /path/to/zips --out ~/.local/share/icons --inherit Adwaita

Any *.zip / *.cur / *.ani found directly under --src is converted.
Theme name defaults to the sanitized file name; use --list to preview.
"""
import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cursor_converter as C


def main():
    ap = argparse.ArgumentParser(description="Batch convert cursor packs to XCursor themes")
    ap.add_argument("--src", default=os.path.expanduser("~/Downloads/cursors"),
                    help="directory containing .zip/.cur/.ani packs")
    ap.add_argument("--out", default=os.path.expanduser("~/.local/share/icons"),
                    help="output icon directory (default ~/.local/share/icons)")
    ap.add_argument("--inherit", default=C.DEFAULT_INHERIT,
                    help="fallback theme for missing roles")
    ap.add_argument("--list", action="store_true",
                    help="only list what would be converted")
    ap.add_argument("--apply", default=None,
                    help="also set this theme active after building it")
    a = ap.parse_args()

    if not os.path.isdir(a.src):
        sys.exit(f"src is not a directory: {a.src}")
    if "\n" in a.inherit or "\r" in a.inherit or not a.inherit.strip():
        sys.exit("--inherit must be a single-line theme name")

    files = sorted(f for f in os.listdir(a.src)
                   if os.path.isfile(os.path.join(a.src, f))
                   and f.lower().endswith((".zip", ".cur", ".ani")))
    files = [os.path.join(a.src, f) for f in files]
    if not files:
        sys.exit(f"no cursor packs found in {a.src}")
    if a.list:
        for f in files:
            print(f"  {os.path.basename(f)} -> {C.sanitize(os.path.splitext(os.path.basename(f))[0])}")
        return

    used_themes = set()
    start = time.time()
    for path in files:
        theme = C.sanitize(os.path.splitext(os.path.basename(path))[0])
        base, i = theme, 2  # foo.zip + foo.cur must not delete each other
        while theme in used_themes:
            theme = f"{base}-{i}"
            i += 1
        used_themes.add(theme)
        target = os.path.join(a.out, theme)
        if os.path.lexists(target):
            print(f"   (replacing existing {theme})")
            if os.path.islink(target):
                os.unlink(target)
            else:
                shutil.rmtree(target)
        try:
            built = C.convert_input(path, theme_name=theme, out_dir=a.out, inherit=a.inherit)
        except Exception as e:  # keep batch going; report at end of line
            print(f"!! {os.path.basename(path)}: {type(e).__name__}: {e}")
            continue
        for k, td in built.items():
            try:
                n = len(os.listdir(os.path.join(td, "cursors")))
            except OSError:
                n = 0
            print(f"   {k:35s} {n:3d} cursors")
    if a.apply:
        C.apply_theme(a.apply)
    print(f"\nDone in {time.time() - start:.1f}s -> {a.out}")


if __name__ == "__main__":
    main()
