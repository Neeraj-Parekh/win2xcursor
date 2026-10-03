#!/usr/bin/env python3
"""
win2xcursor / cursor_converter.py - Convert Windows cursor packs to Linux XCursor themes.
Accepts: a .cur/.ani file, a .zip of cursor files, or a folder.
Output: a ready-to-use XCursor theme (inherits Adwaita by default).
Options: --install (copy into ~/.icons), --apply (make it active).

Examples:
  python3 cursor_converter.py "My Cursor.zip" --theme My-Cursor --install --apply
  python3 cursor_converter.py "folder/of/cursors"
"""
import os, re, sys, struct, zipfile, io, argparse
from PIL import Image

__version__ = "1.2.0"
# Xcursor(3) search order: ~/.local/share/icons first, ~/.icons for compat.
DEFAULT_INHERIT = "Adwaita"  # guaranteed present on GNOME; override with --inherit
DEFAULT_OUT = os.path.expanduser("~/.local/share/icons")
def u32(d, o): return struct.unpack_from("<I", d, o)[0]

# ---------------------------------------------------------------- decoding ---
def parse_icondir(data):
    if len(data) < 6 or data[:2] != b"\x00\x00":
        return None
    typ, cnt = struct.unpack_from("<HH", data, 2)
    if cnt == 0 or cnt > 256 or len(data) < 6 + 16 * cnt:
        return None  # corrupt/truncated directory
    imgs = []
    for i in range(cnt):
        w, h, _, _, xh, yh, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        if size <= 0 or off + size > len(data):
            continue  # skip dangling entries instead of aborting
        imgs.append((w or 256, xh, yh, data[off:off + size]))
    return imgs or None

def dib_shift(mask):
    if not mask:
        return 0
    s = 0
    while not (mask >> s) & 1 and s < 32:
        s += 1
    return s

def dib_to_rgba(blob):
    """Decode an old-style BMP DIB (BITMAPINFOHEADER) cursor image to RGBA,
    honoring the trailing 1-bit AND mask for transparency."""
    if len(blob) < 40 or u32(blob, 0) != 40:
        return None
    w = struct.unpack_from("<i", blob, 4)[0]
    h = struct.unpack_from("<i", blob, 8)[0]
    if w <= 0 or h == 0 or w > 512 or abs(h) > 512:
        return None
    bpp = struct.unpack_from("<H", blob, 14)[0]  # bitcount is 16-bit, not 32
    comp = u32(blob, 16)
    if bpp not in (1, 4, 8, 24, 32):
        return None
    pal_count = u32(blob, 32)
    bmih = 40
    if comp == 3:
        if len(blob) < bmih + 16:
            return None  # truncated BITFIELDS masks
        masks = [u32(blob, bmih), u32(blob, bmih + 4), u32(blob, bmih + 8)]
        if bpp == 32:
            amask = u32(blob, bmih + 12)
            mask_bytes = 16
        else:
            amask = 0
            mask_bytes = 12
        pal_entries = pal_count or ((1 << bpp) if bpp <= 8 else 0)
        off = bmih + mask_bytes + pal_entries * 4
    elif comp == 0:
        masks = [0x00FF0000, 0x0000FF00, 0x000000FF]  # B,G,R (Windows byte order)
        amask = 0
        off = bmih + (pal_count or (1 << bpp) if bpp <= 8 else 0) * 4
    else:
        return None  # RLE (1/2) etc: Pillow already failed, nothing to do
    if off > len(blob):
        return None
    rowbytes = ((bpp * w + 31) // 32) * 4
    hh = abs(h)
    avail = len(blob) - off
    want = hh * rowbytes
    if avail < want and hh % 2 == 0:
        hh = hh // 2  # doubled height packs the AND mask after the XOR image
    data = blob[off:off + hh * rowbytes]
    if len(data) < hh * rowbytes:
        return None  # truncated pixel data
    # 1-bit AND mask follows the XOR image (same orientation, MSB-first)
    mrow = ((w + 31) // 32) * 4
    mdata = blob[off + hh * rowbytes: off + hh * rowbytes + mrow * hh]
    has_mask = len(mdata) >= mrow * hh
    def chan(v, mask):
        # Extract a channel and scale it to full 8-bit range.
        if not mask:
            return 0
        width = bin(mask).count("1")
        return ((v & mask) >> dib_shift(mask)) * 255 // max(1, (1 << width) - 1)

    out = bytearray(w * hh * 4)
    pal_entries = pal_count or ((1 << bpp) if bpp <= 8 else 0)
    pal_base = off - pal_entries * 4  # palette sits just before the pixel data
    for y in range(hh):
        src = (hh - 1 - y) if h > 0 else y
        row = data[src * rowbytes: src * rowbytes + rowbytes]
        mrow_data = mdata[src * mrow: src * mrow + mrow]
        for x in range(w):
            b = (y * w + x) * 4
            transparent = False
            if has_mask:
                transparent = (mrow_data[x // 8] >> (7 - (x % 8))) & 1
            if bpp == 32:
                v = row[x*4] | (row[x*4+1] << 8) | (row[x*4+2] << 16) | (row[x*4+3] << 24)
                if amask:
                    a = chan(v, amask)
                else:
                    a = row[x*4+3]  # 32bpp cursor DIBs keep alpha in the high byte
                if transparent:
                    a = 0
                out[b], out[b+1], out[b+2], out[b+3] = (
                    chan(v, masks[0]),
                    chan(v, masks[1]),
                    chan(v, masks[2]),
                    a)
            elif bpp == 24:
                out[b], out[b+1], out[b+2] = row[x*3+2], row[x*3+1], row[x*3]  # BGR -> RGB
                out[b+3] = 0 if transparent else 255
            elif bpp == 8:
                p = row[x]
                if p >= pal_entries:
                    return None  # corrupt palette index
                out[b], out[b+1], out[b+2], out[b+3] = (blob[pal_base + p*4 + 2],
                                                        blob[pal_base + p*4 + 1],
                                                        blob[pal_base + p*4],
                                                        0 if transparent else 255)
            else:
                return None  # 1/4/16bpp: unsupported without Pillow
    return (w, hh), bytes(out)

def blob_to_rgba(blob, tint=None, strength=0.0):
    try:
        with Image.open(io.BytesIO(blob)) as im:
            im.load(); im = im.convert("RGBA")
    except Exception:
        dib = dib_to_rgba(blob)
        if dib is None:
            raise ValueError("unsupported cursor image")
        size, rgba = dib
        im = Image.frombytes("RGBA", size, rgba)
    if tint is not None and strength > 0:
        color = Image.new("RGBA", im.size, (tint[0], tint[1], tint[2], 255))
        im = Image.blend(im, color, strength)
    return im.size, im.tobytes()

def default_hotspot(name, w, h):
    n = name.lower()
    if "resize" in n or "move" in n:
        return w // 2, h // 2
    return min(2, w - 1), min(2, h - 1)


def collect_icon(blob, out):
    """Collect ICONDIR frame blobs from an ANI fram body / icon chunks."""
    j = 0
    while j + 4 <= len(blob):
        tag = blob[j:j + 4]
        if j + 4 + 4 > len(blob):
            break
        sz = u32(blob, j + 4)
        if tag in (b"icon", b"icon2"):
            out.append(blob[j + 8:j + 8 + sz])      # 'icon' body is a raw ICONDIR
            j += 8 + sz + (sz & 1)
        elif tag in (b"LIST", b"fram"):
            collect_icon(blob[j + 8:j + 8 + sz], out)
            j += 8 + sz + (sz & 1)
        elif tag in (b"rate", b"seq"):
            j += 8 + sz + (sz & 1)
        elif blob[j:j + 2] == b"\x00\x00":
            # Bare ICONDIR without a chunk wrapper: take the rest as one blob;
            # parse_icondir validates it, garbage gets skipped downstream.
            out.append(blob[j:])
            break
        else:
            j += 4

def parse_ani(data, name, tint=None, strength=0.0):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        return None
    frames, rates, seq = [], [], []
    i = 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        csize = min(u32(data, i + 4), len(data) - (i + 8))  # clamp corrupt sizes
        body = data[i + 8:i + 8 + csize]
        if cid == b"rate":
            tags = struct.unpack("<%dI" % (len(body) // 4), body[:len(body) - len(body) % 4])
            rates.extend(tags)
        elif cid == b"seq " or cid == b"seqt":
            tags = struct.unpack("<%dI" % (len(body) // 4), body[:len(body) - len(body) % 4])
            seq.extend(tags)
        elif cid == b"LIST" and body[:4] == b"fram":
            j = 4
            while j + 8 <= len(body):
                tag = body[j:j + 4]
                sz = u32(body, j + 4)
                if j + 8 + sz > len(body):
                    break  # truncated inner chunk
                if tag in (b"icon", b"icon2"):
                    frames.append(body[j + 8:j + 8 + sz])
                elif tag == b"fram" and j + 12 <= len(body):
                    collect_icon(body[j + 8:j + 8 + sz], frames)
                elif tag == b"LIST":
                    collect_icon(body[j + 8:j + 8 + sz], frames)
                j += 8 + sz + (sz & 1)
        i += 8 + csize + (csize & 1)
    if seq:
        frames = [frames[k] for k in seq if 0 <= k < len(frames)]
    out = []
    for k, fr in enumerate(frames):
        try:
            im = parse_icondir(fr)
        except struct.error:
            continue
        if not im: continue
        best = max(im, key=lambda e: e[0])
        try:
            (iw, ih), rgba = blob_to_rgba(best[3], tint, strength)
        except Exception:
            continue
        xh, yh = best[1], best[2]
        if xh == 0 and yh == 0:
            xh, yh = default_hotspot(name, iw, ih)
        delay = round(rates[k] * 1000 / 60) if k < len(rates) else 125
        out.append((rgba, min(max(1, delay), 60000), xh, yh, iw, ih))
    return out or None

def static_frames(data, name, tint=None, strength=0.0):
    try:
        imgs = parse_icondir(data)
    except struct.error:
        return None
    if not imgs: return None
    by = {}
    for w, xh, yh, blob in imgs:
        by.setdefault(w, (xh, yh, blob))
    out = []
    for w, (xh, yh, blob) in by.items():
        try:
            (iw, ih), ok = blob_to_rgba(blob, tint, strength)
        except Exception:
            continue
        if xh == 0 and yh == 0:
            xh, yh = default_hotspot(name, iw, ih)
        out.append((ok, 0, xh, yh, iw, ih))
    return out

# --------------------------------------------------------------- xcur build ---
def to_xcursor_bytes(rgba):
    """RGBA(R,G,B,A) -> file byte order (B,G,R,A on little-endian, ARGB32 pixel).
    IMPORTANT: writing RGBA here made every cursor display orange-tinted (
    B and R swapped)."""
    out = bytearray(len(rgba))
    out[0::4] = rgba[2::4]   # B
    out[1::4] = rgba[1::4]   # G
    out[2::4] = rgba[0::4]   # R
    out[3::4] = rgba[3::4]   # A
    return bytes(out)

MAX_IMAGE_DIM = 512    # frames larger than this are skipped (Xcursor limit is far lower)
MAX_XCUR_CHUNKS = 1500  # bounds memory on pathological animated packs

def xcur_from_frames(frames, sizes=(24, 32, 48, 64, 96)):
    """Build XCursor bytes with a multi-size ladder per frame.

    Wayland compositors pick the chunk nearest their requested cursor size;
    single-size (e.g. 128-only) themes render as white boxes on some paths
    (notably resize cursors during tiling). Hotspots scale with size.
    """
    if not frames:
        return None
    chunks = []
    for rgba, delay, xh, yh, w, h in frames:
        if w < 1 or h < 1 or max(w, h) > MAX_IMAGE_DIM:
            continue
        try:
            base = Image.frombytes("RGBA", (w, h), rgba)
        except Exception:
            continue
        ladder = [s for s in sizes if s < max(w, h)]
        ladder.append(max(w, h))
        for s in ladder:
            if len(chunks) >= MAX_XCUR_CHUNKS:
                break
            sw = s if w >= h else max(1, round(w * s / max(w, h)))
            sh = s if h >= w else max(1, round(h * s / max(w, h)))
            if (sw, sh) == (w, h):
                scaled = to_xcursor_bytes(rgba)
                sxh, syh = xh, yh
            else:
                im = base.resize((sw, sh), Image.LANCZOS)
                scaled = to_xcursor_bytes(im.tobytes())
                sxh = round(xh * sw / w)
                syh = round(yh * sh / h)
            sxh = min(max(0, sxh), sw - 1)  # Xcursor requires hotspot inside image
            syh = min(max(0, syh), sh - 1)
            chunks.append((sw, struct.pack("<9I", 36, 0xFFFD0002, sw, 1, sw, sh, sxh, syh, delay) + scaled))
    if not chunks:
        return None
    n = len(chunks)
    head = bytearray(b"Xcur") + struct.pack("<III", 16, 0x00010000, n)
    pos = 16 + 12 * n
    body = bytearray()
    for size, data in chunks:
        head += struct.pack("<III", 0xFFFD0002, size, pos)
        body += data
        pos += len(data)
    return bytes(head) + bytes(body)

# -------------------------------------------------------------------- roles ---
TABLE = [
    # busy / loading
    ("working","left_ptr_watch"), ("working in background", "left_ptr_watch"), ("app starting", "left_ptr_watch"),
    ("background", "left_ptr_watch"), ("progress", "left_ptr_watch"),
    ("work", "left_ptr_watch"),
    ("busy hour glass", "watch"), ("busy", "watch"), ("loading", "watch"), ("load", "watch"),
    ("wait", "watch"),
    # selection / pointing
    ("link select", "hand2"), ("link", "hand2"), ("hand", "hand2"), ("pointer", "hand2"),
    ("hand grab", "grabbing"), ("hand hover", "hand2"), ("hand point", "hand2"),
    ("grab", "grabbing"), ("grabbing", "grabbing"),
    ("normal select", "left_ptr"), ("normal", "left_ptr"),
    ("alternate", "left_ptr"), ("alternate select", "left_ptr"),
    ("unavailable", "crossed_circle"), ("no dro", "crossed_circle"), ("no drop", "crossed_circle"),
    ("not allowed", "crossed_circle"), ("forbidden", "crossed_circle"), ("unavail", "crossed_circle"),
    ("diagonal resize 1", "size_fdiag"), ("diagonal resize 2", "size_bdiag"),
    ("diagonal resize", "size_fdiag"), ("resize 1", "size_fdiag"), ("resize 2", "size_bdiag"),
    ("diagonal1", "size_fdiag"), ("diagonal2", "size_bdiag"), ("diag1", "size_fdiag"), ("diag2", "size_bdiag"),
    ("dgn1", "size_fdiag"), ("dgn2", "size_bdiag"),
    ("resize down left", "size_bdiag"), ("resize down right", "size_fdiag"),
    ("stretch tl br", "size_fdiag"), ("stretch bl tr", "size_bdiag"),
    ("stretch horizontal", "sb_h_double_arrow"), ("stretch vertical", "sb_v_double_arrow"),
    ("stretch horiz", "sb_h_double_arrow"), ("stretch vert", "sb_v_double_arrow"),
    ("stretch horz", "sb_h_double_arrow"),
    ("horizontal resize", "sb_h_double_arrow"), ("vertical resize", "sb_v_double_arrow"),
    ("north/south resize", "sb_v_double_arrow"), ("west/east resize", "sb_h_double_arrow"),
    ("h resize", "sb_h_double_arrow"), ("v resize", "sb_v_double_arrow"),
    ("horiz", "sb_h_double_arrow"), ("horizontal", "sb_h_double_arrow"),
    ("vert", "sb_v_double_arrow"), ("vertical", "sb_v_double_arrow"),
    ("col resize", "sb_h_double_arrow"), ("col-resize", "sb_h_double_arrow"), ("col", "sb_h_double_arrow"),
    ("row resize", "sb_v_double_arrow"), ("row-resize", "sb_v_double_arrow"), ("row", "sb_v_double_arrow"),
    ("help", "help"),
    ("text ballot", "text"), ("text select", "text"), ("text", "text"), ("ibeam", "text"),
    ("precision", "crosshair"), ("cross", "crosshair"), ("crosshair", "crosshair"),
    ("move", "fleur"), ("all scroll", "size_all"), ("size all", "size_all"),
    ("cell", "cell"), ("cross cell", "cell"), ("crosshair cell", "cell"),
    ("vertical text", "vertical-text"), ("alt scroll", "pointer_move"),
    ("pointer move", "fleur"),
    ("handwriting", "pencil"), ("pen", "pencil"), ("pencil", "pencil"),
    ("alias", "dnd-move"), ("copy", "copy"),
    ("zoom in", "zoom_in"), ("zoom-in", "zoom_in"), ("zoom out", "zoom_out"), ("zoom-out", "zoom_out"),
    ("location", "context-menu"),
    ("nw resize", "nw-resize"), ("ne resize", "ne-resize"),
    ("sw resize", "sw-resize"), ("se resize", "se-resize"),
    ("northwest", "nw-resize"), ("northeast", "ne-resize"),
    ("southwest", "sw-resize"), ("southeast", "se-resize"),
    ("cursor", "left_ptr"), ("arrow", "left_ptr"), ("select", "left_ptr"), ("default", "left_ptr"),
]

def _role_order():
    return [r for _, r in TABLE]

ROLE_NAMES = ["left_ptr", "hand2", "watch", "left_ptr_watch", "crossed_circle",
              "size_fdiag", "size_bdiag", "sb_h_double_arrow", "sb_v_double_arrow",
              "help", "text", "crosshair", "fleur", "size_all", "cell",
              "vertical-text", "handwriting", "dnd_no_drop", "copy", "zoom_in",
              "zoom_out", "grabbing", "context-menu", "move", "pointer_move",
              "n-resize", "s-resize", "e-resize", "w-resize",
              "ne-resize", "nw-resize", "se-resize", "sw-resize"]

GENERIC = {"cursor", "arrow", "select", "default"}

NAME_OVERRIDES = {
    "unavaliable": "crossed_circle", "unavail": "crossed_circle",
    "unavailable": "crossed_circle", "unavailiable": "crossed_circle",
    "unavralible": "crossed_circle", "unavaible": "crossed_circle",
    "hardwriting": "pencil", "handwritng": "pencil",
    "cross reverse": "crosshair", "crossreversed": "crosshair",
    "pan": "fleur", "panned": "fleur", "pan hand": "fleur",
    "grab": "grabbing", "make": "grabbing",
    "maximize": "size_fdiag", "minimize": "size_bdiag",
    "help select": "help", "help selection": "help",
    "horz": "sb_h_double_arrow", "vert": "sb_v_double_arrow",
    "loc": "context-menu", "pointer": "left_ptr",
    "dgn1": "size_fdiag", "dgn2": "size_bdiag",
    "diagonal 1": "size_fdiag", "diagonal 2": "size_bdiag",
    "nw se": "size_fdiag", "ne sw": "size_bdiag",
}

# Pre-sorted once: per-file sorting of ~100 regexes was the hot loop.
_TABLE_MULTI = sorted(((kw, r) for kw, r in TABLE if len(kw.split()) > 1),
                      key=lambda t: -len(t[0]))
_TABLE_SINGLE = sorted(((kw, r) for kw, r in TABLE
                        if len(kw.split()) == 1 and kw not in GENERIC),
                       key=lambda t: -len(t[0]))

def role_for(name):
    n = name.lower()
    n = re.sub(r"[^a-z0-9]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        return "left_ptr"
    if n in NAME_OVERRIDES:
        return NAME_OVERRIDES[n]
    hay = " " + n + " "
    def boundary(hit):
        return re.search(r"(?:^| )" + re.escape(hit) + r"(?:$| )", hay) is not None
    # 1) multi-word phrases (most specific) — match the phrase as a sequence
    for kw, role in _TABLE_MULTI:
        if boundary(kw):
            return role
    # 2) single-word non-generic keywords, longest first
    for kw, role in _TABLE_SINGLE:
        if boundary(kw):
            return role
    # 3) generic fallback words (cursor/arrow/select/default)
    for tok in n.split():
        if tok in ("cursor", "arrow", "select", "default"):
            return "left_ptr"
    return "left_ptr"

ALIASES = {
    "crossed_circle": ["not-allowed", "forbidden", "no_drop", "no-drop", "dnd-no-drop"],
    "left_ptr": ["arrow", "default", "top_left_arrow", "ul_angle", "ur_angle", "X_cursor"],
    "hand2": ["hand", "link", "pointer", "e29285e29e6d1d1d310ec8a0f3ee52"],
    "fleur": ["pointer_move", "move", "size_all", "all-resize", "all-scroll",
              "4498f0e0c1937ffe01fd06f973665830"],
    "text": ["ibeam", "xterm"],
    "crosshair": ["cross", "tcross", "plus", "cross_reverse", "diamond_cross"],
    "size_fdiag": ["bd_double_arrow", "nwse-resize", "nw-resize", "se-resize",
                   "top_left_corner", "bottom_right_corner", "resize-corner-2"],
    "size_bdiag": ["fd_double_arrow", "nesw-resize", "ne-resize", "sw-resize",
                   "top_right_corner", "bottom_left_corner", "resize-corner-1"],
    "sb_h_double_arrow": ["h_double_arrow", "col-resize", "e-resize", "w-resize",
                          "ew-resize", "left_side", "right_side"],
    "sb_v_double_arrow": ["v_double_arrow", "row-resize", "n-resize", "s-resize",
                          "ns-resize", "top_side", "bottom_side"],
    "help": ["question_arrow"],
    "left_ptr_watch": ["08e8e1c95fe2fc01f976f1e063a24c0d",
                       "progress", "app_starting"],
    "watch": ["2825c929d5411e5819219e75ddaf8b9c", "wait"],
    "pencil": ["028006030e0e7ebffc7f707070c6d0c2", "handwriting"],
    "size_all": ["all-scroll", "all-resize", "fleur", "move"],
    "cell": ["crosshair-cell"],
    "zoom_in": ["zoom-in", "zoomin"],
    "zoom_out": ["zoom-out", "zoomout"],
    "grabbing": ["grab", "hand1", "dnd-move"],
}

def cursors_from_single(data, name, tint=None, strength=0.0):
    frames = parse_ani(data, name, tint, strength)
    if frames is None:
        frames = static_frames(data, name, tint, strength)
    if not frames:
        return None
    xc = xcur_from_frames(frames)
    if xc is None:
        return None
    return xc, len(frames)

def sanitize(name):
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "Theme"

CURSOR_EXTS = (".cur", ".ani", ".zip")

def is_cursor_source(path):
    """True for convertible inputs: .cur/.ani/.zip files, or directories
    containing at least one of them."""
    if os.path.isdir(path):
        try:
            return any(f.lower().endswith(CURSOR_EXTS) for f in os.listdir(path))
        except OSError:
            return False
    return os.path.splitext(path)[1].lower() in CURSOR_EXTS

def parse_drop_files(data):
    """Split a TkDND '<<Drop>>' file list (Tcl list: {braced} or bare paths)
    into a plain list of paths."""
    out = []
    for braced, bare in re.findall(r"\{([^}]*)\}|(\S+)", data):
        p = (braced or bare).strip()
        if p:
            out.append(p)
    return out

# ------------------------------------------------------------ theme assembly ---
def _main_cursor(nm):
    n = nm.lower()
    if any(k in n for k in ("pointer", "normal", "cursor", "arrow", "default select", "sweezycursors-pointer")):
        return 2
    if any(k in n for k in ("alternate", "person", "help", "link", "working")):
        return 1
    return 0

# Last-resort chains: these names MUST resolve to something in-theme, or the
# compositor shows a blank box (seen during overview window drags, which ask
# for grabbing/dnd-move even when the pack never shipped them). First
# existing candidate wins; left_ptr is the final backstop — an arrow is
# always better than nothing. Runs after ALIASES so real art always wins.
FALLBACKS = [
    ("grabbing", ["grabbing", "fleur", "hand2", "left_ptr"]),
    ("dnd-move", ["dnd-move", "fleur", "grabbing", "hand2", "left_ptr"]),
    ("dnd-copy", ["dnd-copy", "copy", "hand2", "left_ptr"]),
    ("dnd-link", ["dnd-link", "hand2", "left_ptr"]),
    ("dnd-ask", ["dnd-ask", "help", "copy", "left_ptr"]),
    ("alias", ["alias", "copy", "hand2", "left_ptr"]),
    ("move", ["move", "fleur", "hand2", "left_ptr"]),
    ("fleur", ["fleur", "move", "hand2", "left_ptr"]),
    ("copy", ["copy", "hand2", "left_ptr"]),
    ("help", ["help", "left_ptr"]),
    ("text", ["text", "left_ptr"]),
    ("crosshair", ["crosshair", "left_ptr"]),
    ("cell", ["cell", "crosshair", "left_ptr"]),
    ("crossed_circle", ["crossed_circle", "left_ptr"]),
    ("dnd-no-drop", ["dnd-no-drop", "crossed_circle", "left_ptr"]),
    ("watch", ["watch", "left_ptr_watch", "left_ptr"]),
    ("left_ptr_watch", ["left_ptr_watch", "watch", "left_ptr"]),
    ("hand2", ["hand2", "left_ptr"]),
    ("size_all", ["size_all", "fleur", "hand2", "left_ptr"]),
    ("pencil", ["pencil", "left_ptr"]),
    ("vertical-text", ["vertical-text", "text", "left_ptr"]),
    ("context-menu", ["context-menu", "left_ptr"]),
    ("zoom_in", ["zoom_in", "hand2", "left_ptr"]),
    ("zoom_out", ["zoom_out", "hand2", "left_ptr"]),
]

def write_theme(all_roles, tdir, theme_name, inherit):
    import shutil as _sh
    _check_theme_name(theme_name)
    if "\n" in inherit or "\r" in inherit or not inherit.strip():
        raise ValueError(f"bad inherit theme name: {inherit!r}")
    # Never follow a planted symlink: unlink it instead of rmtree-ing its target.
    if os.path.islink(tdir):
        os.unlink(tdir)
    elif os.path.isdir(tdir):
        _sh.rmtree(tdir)
    cdir = os.path.join(tdir, "cursors")
    os.makedirs(cdir, exist_ok=True)
    for role, entry in all_roles.items():
        if isinstance(entry, tuple):
            xc = entry[0]
        else:
            xc = entry
        with open(os.path.join(cdir, role), "wb") as f:
            f.write(xc)
    for role, targets in ALIASES.items():
        if os.path.exists(os.path.join(cdir, role)):
            for a in targets:
                p = os.path.join(cdir, a)
                if not os.path.exists(p):
                    os.symlink(role, p)
    for name, candidates in FALLBACKS:
        if os.path.exists(os.path.join(cdir, name)):
            continue
        for cand in candidates:
            if cand != name and os.path.exists(os.path.join(cdir, cand)):
                os.symlink(cand, os.path.join(cdir, name))
                break
    with open(os.path.join(tdir, "index.theme"), "w") as f:
        f.write(f"[Icon Theme]\nName={theme_name}\nComment=Converted from a Windows cursor pack\nInherits={inherit}\n")

ROOT_ONLY = {".", ""}

def _zip_variants(z):
    """Group zip entries by top-level folder; multi-variant packs (dark/light,
    shadow, Static/Windows) become separate themes instead of colliding."""
    groups = {}
    for ent in z.namelist():
        if not re.search(r"\.(cur|ani)$", ent, re.I):
            continue
        parts = ent.replace("\\", "/").split("/")
        key = parts[0] if len(parts) > 1 else "."
        groups.setdefault(key, []).append(ent)
    return groups

MAX_ZIP_ENTRIES = 2000   # bounds zip-bomb walking
MAX_ZIP_BYTES = 500 * 1024 * 1024
MAX_VARIANTS = 20

def _merge_role(all_roles, role, xc, nfr, pri):
    """Keep the best candidate per role: most frames wins, then main-cursor priority."""
    prior = all_roles.get(role)
    if prior is None or nfr > prior[1] or (nfr == prior[1] and pri > prior[2]):
        all_roles[role] = (xc, nfr, pri)

def convert_input(path, theme_name=None, out_dir=None, inherit=DEFAULT_INHERIT, tint=None, strength=0.0):
    import tempfile as _tf
    theme_map = {}
    if os.path.isdir(path):
        items = [os.path.join(path, f) for f in sorted(os.listdir(path))
                 if re.search(r"\.(cur|ani|zip)$", f, re.I)]
        base = os.path.basename(os.path.abspath(path.rstrip("/")))
        default_name = sanitize(theme_name) if theme_name else sanitize(base)
    else:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"input not found: {path}")
        items = [path]
        if theme_name:
            default_name = sanitize(theme_name)
        else:
            default_name = sanitize(os.path.splitext(os.path.basename(path))[0])
    if out_dir is None:
        out_dir = _tf.mkdtemp(prefix="win2xcursor-")
        print(f"note: no --out given, building into {out_dir} (use --install to keep it)")
    built = 0
    loose_roles = {}  # non-zip files in a folder accumulate into one theme
    for item in items:
        if re.search(r"\.zip$", item, re.I):
            try:
                zf = zipfile.ZipFile(item)
            except zipfile.BadZipFile:
                print(f"!! skipping corrupt zip: {item}")
                continue
            with zf as z:
                infos = [i for i in z.infolist()
                         if re.search(r"\.(cur|ani)$", i.filename, re.I)]
                if len(infos) > MAX_ZIP_ENTRIES:
                    print(f"!! {item}: {len(infos)} cursor entries, only first {MAX_ZIP_ENTRIES} tried")
                    infos = infos[:MAX_ZIP_ENTRIES]
                if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                    print(f"!! skipping {item}: uncompressed size over {MAX_ZIP_BYTES // 1024 // 1024}MB")
                    continue
                groups = _zip_variants(z)
                # _zip_variants re-filters namelist; restrict to the capped set
                keep = {i.filename for i in infos}
                groups = {k: [e for e in v if e in keep] for k, v in groups.items()}
                groups = {k: v for k, v in groups.items() if v}
                multi = len(groups) > 1
                for sub, ents in groups.items():
                    if built >= MAX_VARIANTS:
                        print(f"!! stopping at {MAX_VARIANTS} themes (variants beyond this dropped)")
                        break
                    vname = default_name
                    if multi and sub not in (".", ""):
                        slug = sanitize(sub)
                        root_slug = sanitize(theme_name or os.path.splitext(os.path.basename(path))[0]) if isinstance(path, str) else default_name
                        if root_slug.lower() in slug.lower() or slug.lower() in root_slug.lower():
                            vname = slug
                        else:
                            vname = default_name + "-" + slug
                    all_roles = {}
                    for ent in ents:
                        nm = os.path.splitext(os.path.basename(ent))[0]
                        try:
                            raw = z.read(ent)
                        except Exception as e:
                            print(f"!! unreadable entry {ent}: {e}")
                            continue
                        try:
                            got = cursors_from_single(raw, nm, tint, strength)
                        except Exception as e:
                            print(f"!! bad cursor data in {ent}: {e}")
                            continue
                        if not got:
                            continue
                        xc, nfr = got
                        _merge_role(all_roles, role_for(nm), xc, nfr, _main_cursor(nm))
                    if not all_roles:
                        continue
                    tdir = os.path.join(out_dir, vname)
                    write_theme(all_roles, tdir, vname, inherit)
                    theme_map[vname] = tdir
                    built += 1
        else:
            nm = os.path.splitext(os.path.basename(item))[0]
            try:
                with open(item, "rb") as f:
                    raw = f.read()
            except OSError as e:
                print(f"!! cannot read {item}: {e}")
                continue
            try:
                got = cursors_from_single(raw, nm, tint, strength)
            except Exception as e:
                print(f"!! bad cursor data in {item}: {e}")
                continue
            if not got:
                print(f"!! no cursor frames in {item}")
                continue
            xc, nfr = got
            _merge_role(loose_roles, role_for(nm), xc, nfr, _main_cursor(nm))
    if loose_roles:
        tdir = os.path.join(out_dir, default_name)
        write_theme(loose_roles, tdir, default_name, inherit)
        theme_map[default_name] = tdir
    return theme_map

def _check_theme_name(theme):
    """Theme names end up in gsettings, file paths and shell files: reject
    control characters/newlines that could break out of those contexts."""
    if not theme or not theme.strip() or re.search(r"[\x00-\x1f\x7f]", theme):
        raise ValueError(f"bad theme name: {theme!r}")
    return theme

def install_theme(tdir, dest_base=None):
    base = os.path.basename(os.path.normpath(tdir))
    if not base or base in (".", ".."):
        raise ValueError(f"refusing to install theme with empty name from {tdir!r}")
    dest_base = os.path.expanduser(dest_base or DEFAULT_OUT)
    dst = os.path.join(dest_base, base)
    if os.path.realpath(tdir) == os.path.realpath(dst):
        return dst
    if not os.path.realpath(dst).startswith(os.path.realpath(dest_base) + os.sep):
        raise ValueError(f"install destination escapes icon dir: {dst!r}")
    import shutil
    if os.path.islink(dst):
        os.unlink(dst)
    elif os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(tdir, dst, symlinks=True)  # keep alias symlinks as links
    return dst

def apply_theme(theme):
    import shutil
    import subprocess
    _check_theme_name(theme)
    if shutil.which("gsettings"):
        subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme])
    else:
        print("note: gsettings not found, skipping GNOME setting (theme still installed)")
    for f in ("~/.config/gtk-3.0/settings.ini", "~/.config/gtk-4.0/settings.ini"):
        p = os.path.expanduser(f)
        if os.path.exists(p):
            try:
                old = open(p).read().splitlines()
            except OSError as e:
                print(f"note: cannot read {p}: {e}")
                continue
            if any(l.startswith("gtk-cursor-theme-name=") for l in old):
                lines = ["%s\n" % (l if not l.startswith("gtk-cursor-theme-name=")
                                   else f"gtk-cursor-theme-name={theme}") for l in old]
            else:
                lines = [l + "\n" for l in old] + [f"gtk-cursor-theme-name={theme}\n"]
            try:
                open(p, "w").writelines(lines)
            except OSError as e:
                print(f"note: cannot write {p}: {e}")
    prof = os.path.expanduser("~/.profile")
    if os.path.exists(prof):
        t = open(prof).read()
        t = re.sub(r"^export XCURSOR_THEME=.*$", lambda m: f"export XCURSOR_THEME={theme}", t, flags=re.M)
        if "XCURSOR_THEME=" not in t:
            t += f"\nexport XCURSOR_THEME={theme}\n"
        # Wayland compositors read themes through libXcursor: the user icon
        # dir must be on XCURSOR_PATH (ArchWiki cursor-themes note).
        want_path = "$HOME/.local/share/icons:/usr/share/icons"
        if "XCURSOR_PATH=" not in t:
            t += f"\nexport XCURSOR_PATH={want_path}:$XCURSOR_PATH\n"
        open(prof, "w").write(t)
    print(f"applied: {theme} (re-login / restart apps to see it)")


def parse_recolor(spec):
    """Parse 'R,G,B' into an (r, g, b) tuple or raise ValueError with a clear message."""
    raw = [x for x in spec.replace(";", ",").split(",")]
    if len(raw) != 3:
        raise ValueError(f'bad --recolor {spec!r}: expected exactly "R,G,B" like "200,60,120"')
    try:
        parts = [int(x) for x in raw]
    except ValueError:
        raise ValueError(f'bad --recolor {spec!r}: expected "R,G,B" like "200,60,120"')
    if len(parts) != 3 or not all(0 <= v <= 255 for v in parts):
        raise ValueError(f'bad --recolor {spec!r}: expected "R,G,B" like "200,60,120"')
    return tuple(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert Windows cursors to Linux XCursor themes")
    ap.add_argument("input", help=".ani/.cur file, .zip pack, or folder of cursors")
    ap.add_argument("--theme", default=None, help="theme name (default: file/folder name)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output icon dir (default ~/.local/share/icons)")
    ap.add_argument("--inherit", default=DEFAULT_INHERIT,
                    help=f"fallback theme for missing roles (default {DEFAULT_INHERIT})")
    ap.add_argument("--install", action="store_true", help="copy into the output icon dir")
    ap.add_argument("--apply", action="store_true", help="also set as active cursor theme")
    ap.add_argument("--recolor", default=None, help='blend all colors toward R,G,B e.g. "255,0,0"', type=str)
    ap.add_argument("--strength", default=0.5, type=float, help="recolor strength 0-1 (default 0.5)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args()
    a.out = os.path.expanduser(a.out)
    try:
        tint = parse_recolor(a.recolor) if a.recolor else None
    except ValueError as e:
        ap.error(str(e))
    if not 0 <= a.strength <= 1:
        ap.error("--strength must be between 0 and 1")
    if "\n" in a.inherit or "\r" in a.inherit or not a.inherit.strip():
        ap.error("--inherit must be a single-line theme name")
    if a.theme is not None:
        try:
            _check_theme_name(a.theme)
        except ValueError as e:
            ap.error(str(e))
    if a.apply and not a.install and os.path.realpath(a.out) != os.path.realpath(DEFAULT_OUT):
        ap.error("--apply needs --install when --out is not the icon dir")
    if tint is not None and a.strength <= 0:
        tint, a.strength = None, 0.0
    if tint is not None:
        print(f"recoloring toward {tint} at {a.strength:.0%}")
    try:
        themes = convert_input(a.input, a.theme, a.out, a.inherit, tint, a.strength)
    except FileNotFoundError as e:
        ap.error(str(e))
    if not themes:
        sys.exit("no cursor files found / converted")
    for tname, tdir in themes.items():
        print(f"converted: {tname} -> {tdir}")
        if a.install:
            print(f"  installed -> {install_theme(tdir)}")
        if a.apply:
            apply_theme(tname)