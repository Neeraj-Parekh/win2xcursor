# win2xcursor

Convert Windows cursor packs (`.ani` / `.cur` / `.zip`) into Linux **XCursor** themes — with a tiny Tkinter GUI. No heavy dependencies.

Tested on Ubuntu/GNOME. Converts animated `.ani` cursors frame-by-frame (RIFF/ACON, incl. old DIB `BITMAPINFOHEADER` frames and `rate`/`seq` timing), maps Windows role names to X11 roles (`left_ptr`, `hand2`, `watch`, `text`, resize arrows, …), splits multi-variant zips (dark/light, Static/Windows) into separate themes, and writes symlinks for legacy X11 alias names.

## Install

```bash
sudo apt install python3-tk python3-pil   # or: pip install -r requirements.txt
git clone https://github.com/Neeraj-Parekh/win2xcursor.git
cd win2xcursor
```

Only hard dependency is [Pillow](https://python-pillow.org/).

## CLI usage

```bash
# single pack -> ~/.icons/My-Theme (+ install + apply)
python3 cursor_converter.py "My Cursor.zip" --theme My-Theme --install --apply

# single .ani / .cur file
python3 cursor_converter.py pointer.ani --theme My-Pointer --install

# folder of cursors
python3 cursor_converter.py ./my-cursors --theme My-Theme --out ~/.icons

# optional recolor blend toward R,G,B
python3 cursor_converter.py "pack.zip" --theme My-Red --recolor 200,60,120 --strength 0.5
```

## Batch convert

```bash
python3 batch_convert.py --src ~/Downloads/cursors --out ~/.icons
python3 batch_convert.py --src ~/Downloads/cursors --list   # preview only
```

## Light GUI

```bash
python3 gui.py
```

List installed themes, **Apply** one, **Convert new cursor…** from a `.zip`/`.cur`/`.ani`, or revert to `Vimix-cursors`. That's it — plain Tkinter, no Electron, no browser.

## How it works

| Step | What happens |
|---|---|
| `parse_ani` | RIFF/ACON chunks → per-frame ICONDIR blobs, honors `rate` (jiffies→ms) and `seq` order |
| `dib_to_rgba` | fallback decoder for pre-PNG DIB cursors + 1-bit AND transparency mask |
| `to_xcursor_bytes` | RGBA → XCursor BGRA byte order (little-endian ARGB32) |
| `role_for` | Windows file names → X11 roles (`busy`→`watch`, `link`→`hand2`, diagonal resize→`size_fdiag`/`size_bdiag`, …) |
| `write_theme` | writes `cursors/*` + `index.theme`, symlinks legacy aliases (`arrow`, `ibeam`, `wait`, …) |
| `apply_theme` | `gsettings` + GTK `settings.ini` + `~/.profile` `XCURSOR_THEME` |

Native Linux packs (a `linux/` subfolder with `index.theme` + `cursors/`) don't need conversion — copy them straight into `~/.icons`.

## Files

| File | Purpose |
|---|---|
| `cursor_converter.py` | core converter + CLI (importable as `cursor_converter`) |
| `gui.py` | lightweight Tkinter GUI |
| `batch_convert.py` | batch-convert a folder of packs |
| `requirements.txt` | `Pillow` |

## Notes

- Output goes to `~/.icons/<Theme-Name>/` by default with `Inherits=Vimix-cursors` for missing roles.
- Re-login (or restart apps) after applying — GNOME caches cursors per app.
