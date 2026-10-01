#!/usr/bin/env python3
"""
win2xcursor / cursor_converter.py - Convert Windows cursor packs to Linux XCursor themes.
Accepts: a .cur/.ani file, a .zip of cursor files, or a folder.
Output: a ready-to-use XCursor theme (inherits Vimix-cursors by default).
Options: --install (copy into ~/.icons), --apply (make it active).

Examples:
  python3 cursor_converter.py "My Cursor.zip" --theme My-Cursor --install --apply
  python3 cursor_converter.py "folder/of/cursors"
"""
import os, re, sys, struct, zipfile, io, argparse
from PIL import Image

__version__ = "1.1.0"
DEFAULT_INHERIT = "Vimix-cursors"
DEFAULT_OUT = os.path.expanduser("~/.icons")
def u32(d, o): return struct.unpack_from("<I", d, o)[0]

# ---------------------------------------------------------------- decoding ---
def parse_icondir(data):
    if len(data) < 6 or data[:2] != b"\x00\x00":
        return None
    typ, cnt = struct.unpack_from("<HH", data, 2)
    imgs = []
    for i in range(cnt):
        w, h, _, _, xh, yh, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        imgs.append((w or 256, xh, yh, data[off:off + size]))
    return imgs

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
    bpp = u32(blob, 14)
    comp = u32(blob, 16)
    pal_count = u32(blob, 32)
    bmih = 40
    if comp == 3:
        masks = [u32(blob, bmih), u32(blob, bmih + 4), u32(blob, bmih + 8)]
        amask = u32(blob, bmih + 12) if bpp == 32 else 0
        off = bmih + 12 + (pal_count or (2 if bpp <= 8 else 0)) * 4
    elif comp == 0:
        masks = [0x00FF0000, 0x0000FF00, 0x000000FF]  # B,G,R (Windows byte order)
        amask = 0
        off = bmih + (pal_count or (1 << bpp) if bpp <= 8 else 0) * 4
    else:
        return None
    rowbytes = ((bpp * w + 31) // 32) * 4
    hh = abs(h)
    avail = len(blob) - off
    want = hh * rowbytes
    if avail < want and hh % 2 == 0:
        hh = hh // 2  # doubled height packs the AND mask after the XOR image
    data = blob[off:off + hh * rowbytes]
    # 1-bit AND mask follows the XOR image (same orientation, MSB-first)
    mrow = ((w + 31) // 32) * 4
    mdata = blob[off + hh * rowbytes: off + hh * rowbytes + mrow * hh]
    has_mask = len(mdata) >= mrow * hh
    out = bytearray(w * hh * 4)
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
                    a = ((v & amask) >> dib_shift(amask)) & 0xFF
                else:
                    a = row[x*4+3]  # 32bpp cursor DIBs keep alpha in the high byte
                if transparent:
                    a = 0
                out[b], out[b+1], out[b+2], out[b+3] = (
                    (v & masks[0]) >> dib_shift(masks[0]),
                    (v & masks[1]) >> dib_shift(masks[1]),
                    (v & masks[2]) >> dib_shift(masks[2]),
                    a)
            elif bpp == 24:
                out[b:b+3] = row[x*3:x*3+3]
                out[b+3] = 0 if transparent else 255
            elif bpp == 8:
                p = row[x]
                pbase = bmih + pal_count * 4
                out[b], out[b+1], out[b+2], out[b+3] = (blob[pbase + p*4], blob[pbase + p*4 + 1],
                                                        blob[pbase + p*4 + 2],
                                                        0 if transparent else 255)
            else:
                return None
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
            out.append(blob[j + 4:j + 4 + sz])       # length-prefixed raw ICONDIR
            j += 4 + sz + (sz & 1)
        else:
            j += 4

def parse_ani(data, name, tint=None, strength=0.0):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        return None
    frames, rates, seq = [], [], []
    i = 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]; csize = u32(data, i + 4)
        body = data[i + 8:i + 8 + csize]
        if cid == b"rate":
            tags = struct.unpack("<%dI" % (csize // 4), body[:csize - csize % 4])
            rates.extend(tags)
        elif cid == b"seq " or cid == b"seqt":
            tags = struct.unpack("<%dI" % (csize // 4), body[:csize - csize % 4])
            seq.extend(tags)
        elif cid == b"LIST" and body[:4] == b"fram":
            j = 4
            while j + 8 <= len(body):
                tag = body[j:j + 4]
                sz = u32(body, j + 4)
                if tag in (b"icon", b"icon2"):
                    frames.append(body[j + 8:j + 8 + sz])
                elif tag == b"fram" and j + 12 <= len(body):
                    collect_icon(body[j + 8:j + 8 + sz], frames)
                elif tag in (b"LIST", b"fram "):
                    collect_icon(body[j + 8:j + 8 + sz], frames)
                j += 8 + sz + (sz & 1)
        i += 8 + csize + (csize & 1)
    if seq:
        frames = [frames[k] for k in seq if k < len(frames)]
    out = []
    for k, fr in enumerate(frames):
        im = parse_icondir(fr)
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
        out.append((rgba, max(1, delay), xh, yh, iw, ih))
    return out or None

def static_frames(data, name, tint=None, strength=0.0):
    imgs = parse_icondir(data)
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

def xcur_from_frames(frames):
    chunks = []
    for rgba, delay, xh, yh, w, h in frames:
        chunks.append((w, struct.pack("<9I", 36, 0xFFFD0002, w, 1, w, h, xh, yh, delay) + to_xcursor_bytes(rgba)))
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
    for kw, role in sorted(TABLE, key=lambda t: -len(t[0])):
        if len(kw.split()) > 1 and boundary(kw):
            return role
    # 2) single-word non-generic keywords, longest first
    singles = sorted(((kw, r) for kw, r in TABLE if len(kw.split()) == 1 and kw not in GENERIC),
                     key=lambda t: -len(t[0]))
    for kw, role in singles:
        if boundary(kw):
            return role
    # 3) generic fallback words (cursor/arrow/select/default)
    for tok in n.split():
        if tok in ("cursor", "arrow", "select", "default"):
            return "left_ptr"
    return "left_ptr"

ALIASES = {
    "crossed_circle": ["not-allowed", "forbidden", "no_drop", "dnd-no-drop"],
    "left_ptr": ["arrow", "default"],
    "hand2": ["hand1", "link", "pointer"],
    "fleur": ["pointer_move", "move", "4498f0e0c1937ffe01fd06f973665830"],
    "xterm": ["text"],
    "text": ["ibeam"],
    "crosshair": ["cross", "tcross"],
    "size_fdiag": ["fd_double_arrow", "nwse-resize", "resize-corner-2"],
    "size_bdiag": ["bd_double_arrow", "nesw-resize", "resize-corner-1"],
    "sb_h_double_arrow": ["h_double_arrow", "col-resize", "e-resize", "w-resize"],
    "sb_v_double_arrow": ["v_double_arrow", "row-resize", "n-resize", "s-resize"],
    "wait": ["watch"],
    "left_ptr_watch": ["08e8e1c95fe2fc01f976f1e063a24c0d",
                       "progress", "app_starting"],
    "watch": ["2825c929d5411e5819219e75ddaf8b9c", "wait"],
    "hand1": ["e29285e29e6d1d1d310ec8a0f3ee52"],
    "fleur": ["4498f0e0c1937ffe01fd06f973665830", "move"],
    "pencil": ["028006030e0e7ebffc7f707070c6d0c2", "handwriting"],
    "size_all": ["all-scroll"],
    "cell": ["crosshair-cell"],
    "zoom_in": ["zoom-in", "zoomin"],
    "zoom_out": ["zoom-out", "zoomout"],
    "grabbing": ["grab", "dnd-move"],
}

def cursors_from_single(data, name, tint=None, strength=0.0):
    frames = parse_ani(data, name, tint, strength)
    if frames is None:
        frames = static_frames(data, name, tint, strength)
    if not frames:
        return None
    return xcur_from_frames(frames), len(frames)

def sanitize(name):
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "Theme"

CURSOR_EXTS = (".cur", ".ani", ".zip")

def is_cursor_source(path):
    """True for convertible inputs: .cur/.ani/.zip files or directories."""
    if os.path.isdir(path):
        return True
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

def write_theme(all_roles, tdir, theme_name, inherit):
    import shutil as _sh
    if os.path.isdir(tdir):
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

def convert_input(path, theme_name=None, out_dir=None, inherit=DEFAULT_INHERIT, tint=None, strength=0.0):
    theme_map = {}
    if os.path.isdir(path):
        items = [os.path.join(path, f) for f in sorted(os.listdir(path))
                 if re.search(r"\.(cur|ani|zip)$", f, re.I)]
        base = os.path.basename(os.path.abspath(path.rstrip("/")))
        default_name = theme_name or sanitize(base)
    else:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"input not found: {path}")
        items = [path]
        default_name = theme_name or sanitize(os.path.splitext(os.path.basename(path))[0])
    built = 0
    for item in items:
        if re.search(r"\.zip$", item, re.I):
            try:
                zf = zipfile.ZipFile(item)
            except zipfile.BadZipFile:
                print(f"!! skipping corrupt zip: {item}")
                continue
            with zf as z:
                groups = _zip_variants(z)
                multi = len(groups) > 1
                for sub, ents in groups.items():
                    if built >= 20:
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
                        got = cursors_from_single(z.read(ent), nm, tint, strength)
                        if not got:
                            continue
                        xc, nfr = got
                        role = role_for(nm)
                        pri = _main_cursor(nm)
                        prior = all_roles.get(role)
                        if prior is None or nfr > prior[1] or (nfr == prior[1] and pri > prior[2]):
                            all_roles[role] = (xc, nfr, pri)
                    if not all_roles:
                        continue
                    tdir = os.path.join(out_dir or os.path.expanduser("~/.icons"), vname)
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
            got = cursors_from_single(raw, nm, tint, strength)
            if not got:
                print(f"!! no cursor frames in {item}")
                continue
            xc, _ = got
            all_roles = {role_for(nm): (xc, 1, _main_cursor(nm))}
            tdir = os.path.join(out_dir or os.path.expanduser("~/.icons"), default_name)
            write_theme(all_roles, tdir, default_name, inherit)
            theme_map[default_name] = tdir
    return theme_map

def install_theme(tdir):
    dst = os.path.join(os.path.expanduser("~/.icons"), os.path.basename(tdir))
    if os.path.abspath(tdir) == os.path.abspath(dst):
        return dst
    import shutil
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(tdir, dst)
    return dst

def apply_theme(theme):
    import shutil
    import subprocess
    if shutil.which("gsettings"):
        subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme])
    else:
        print("note: gsettings not found, skipping GNOME setting (theme still installed)")
    for f in ("~/.config/gtk-3.0/settings.ini", "~/.config/gtk-4.0/settings.ini"):
        p = os.path.expanduser(f)
        if os.path.exists(p):
            lines = ["%s\n" % (l if not l.startswith("gtk-cursor-theme-name=")
                               else f"gtk-cursor-theme-name={theme}") for l in open(p).read().splitlines()]
            open(p, "w").writelines(lines)
    prof = os.path.expanduser("~/.profile")
    if os.path.exists(prof):
        t = open(prof).read()
        t = re.sub(r"^export XCURSOR_THEME=.*$", f"export XCURSOR_THEME={theme}", t, flags=re.M)
        if "XCURSOR_THEME=" not in t:
            t += f"\nexport XCURSOR_THEME={theme}\n"
        open(prof, "w").write(t)
    print(f"applied: {theme} (re-login / restart apps to see it)")


def parse_recolor(spec):
    """Parse 'R,G,B' into an (r, g, b) tuple or raise ValueError with a clear message."""
    try:
        parts = [int(x) for x in spec.replace(";", ",").split(",")[:3]]
    except ValueError:
        raise ValueError(f'bad --recolor {spec!r}: expected "R,G,B" like "200,60,120"')
    if len(parts) != 3 or not all(0 <= v <= 255 for v in parts):
        raise ValueError(f'bad --recolor {spec!r}: expected "R,G,B" like "200,60,120"')
    return tuple(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert Windows cursors to Linux XCursor themes")
    ap.add_argument("input", help=".ani/.cur file, .zip pack, or folder of cursors")
    ap.add_argument("--theme", default=None, help="theme name (default: file/folder name)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output icon dir (default ~/.icons)")
    ap.add_argument("--inherit", default=DEFAULT_INHERIT,
                    help=f"fallback theme for missing roles (default {DEFAULT_INHERIT})")
    ap.add_argument("--install", action="store_true", help="copy into ~/.icons")
    ap.add_argument("--apply", action="store_true", help="also set as active cursor theme")
    ap.add_argument("--recolor", default=None, help='blend all colors toward R,G,B e.g. "255,0,0"', type=str)
    ap.add_argument("--strength", default=0.5, type=float, help="recolor strength 0-1 (default 0.5)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args()
    try:
        tint = parse_recolor(a.recolor) if a.recolor else None
    except ValueError as e:
        ap.error(str(e))
    if not 0 <= a.strength <= 1:
        ap.error("--strength must be between 0 and 1")
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