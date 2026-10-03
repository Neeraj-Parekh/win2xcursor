#!/usr/bin/env python3
"""
win2xcursor GUI - lightweight Tkinter frontend to preview, convert and apply cursors.

Run:  python3 gui.py            (use a Python with tkinter, e.g. /usr/bin/python3)
Deps: python3-tk, Pillow
"""
import os
import queue
import struct
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from tkinter import colorchooser, filedialog, messagebox, simpledialog

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:  # drag & drop is optional; file picker always works
    HAS_DND = False

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cursor_converter as C

try:
    from PIL import Image, ImageTk
    HAS_IMAGETK = True
except Exception:  # Pillow without tkinter support -> previews off, rest works
    from PIL import Image
    ImageTk = None
    HAS_IMAGETK = False

SEARCH = [os.path.expanduser("~/.local/share/icons"),
          os.path.expanduser("~/.icons"),
          "/usr/share/icons"]
PREVIEW_ROLES = ["left_ptr", "hand2", "text", "watch"]
PREVIEW_PX = 96  # preview cell size in pixels (4 cells must fit the panel)
OUT_DIR = os.path.expanduser("~/.local/share/icons")


def checkerboard(px):
    """Light/dark checker tile so cursor transparency reads correctly."""
    tile, n = 8, px // 8 + 1
    bg = Image.new("RGBA", (px, px), (58, 58, 68, 255))
    light = Image.new("RGBA", (tile, tile), (78, 78, 92, 255))
    for y in range(n):
        for x in range(n):
            if (x + y) % 2 == 0:
                bg.paste(light, (x * tile, y * tile))
    return bg


def installed_themes():
    themes = {}
    for base in SEARCH:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            d = os.path.join(base, name)
            if os.path.isdir(os.path.join(d, "cursors")) and os.path.exists(os.path.join(d, "index.theme")):
                themes.setdefault(name, d)
    return themes


def inherited_from(path):
    try:
        with open(os.path.join(path, "index.theme")) as f:
            for line in f:
                if line.strip().lower().startswith("inherits="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def current_theme():
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"],
                             capture_output=True, text=True, timeout=5).stdout.strip().strip("'\"")
        return out or "?"
    except Exception:
        return "?"


def xcur_first_frame(path, size=96, _depth=0):
    """Decode the first frame of an XCursor file into a PIL image (or None).

    Follows legacy redirection files (plain-text target name, e.g. Vimix's
    `left_ptr` containing "default") and filesystem symlinks.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:4] != b"Xcur":
        try:
            target = data.decode("ascii").strip().split("\x00")[0]
        except (UnicodeDecodeError, ValueError):
            return None
        if (not target or target in (".", "..") or "/" in target or "\\" in target
                or len(target) > 64 or _depth >= 4):
            return None
        return xcur_first_frame(os.path.join(os.path.dirname(path), target), size, _depth + 1)
    if len(data) < 16:
        return None
    try:
        ntoc, = struct.unpack_from("<I", data, 12)
        if ntoc == 0 or ntoc > 256 or 16 + 12 * ntoc > len(data):
            return None
        best = None  # (width, pos): Xcursor files hold a size ladder;
        for i in range(ntoc):  # preview wants the largest image, not chunk 0
            _type, _sub, p = struct.unpack_from("<III", data, 16 + 12 * i)
            if _type != 0xFFFD0002:
                continue
            if p < 16 + 12 * ntoc or p + 36 > len(data):
                continue
            (_h, _t, _s, _v, w, h, _xh, _yh, _d) = struct.unpack_from("<9I", data, p)
            if w <= 0 or h <= 0 or w > 512 or h > 512:
                continue
            if best is None or w * h > best[0] * best[1]:
                best = (w, h, p)
        if best is None:
            return None
        w, h, pos = best
        px = data[pos + 36:pos + 36 + w * h * 4]
        if len(px) < w * h * 4:
            return None
        im = Image.frombytes("RGBA", (w, h), bytes(px), "raw", "BGRA")
        im.thumbnail((size, size), Image.LANCZOS)
        return im
    except Exception:
        return None


class App:
    def __init__(self, root):
        self.root = root
        root.title("win2xcursor — Cursor Themes")
        root.geometry("760x640")
        # Never let the window shrink past a usable layout: below this,
        # buttons and previews get cut off instead of reflowing.
        root.minsize(700, 560)
        root.configure(bg="#24273a")
        self.themes = {}
        self.info_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Pick a theme, or convert a Windows pack.")
        self.tint = None
        self.job_q = queue.Queue()
        self.photos = []

        pad = {"bg": "#24273a", "fg": "#cad3f5"}

        tk.Label(root, text="win2xcursor — Cursor Themes", font=("", 18, "bold"), **pad).pack(pady=(10, 2))
        tk.Label(root, text="Windows .ani / .cur / .zip  →  Linux XCursor", font=("", 10), **pad).pack(pady=(0, 8))

        mid = tk.Frame(root, bg="#24273a")
        mid.pack(fill="both", expand=True, padx=18)

        self.filter_var = tk.StringVar()
        listframe = tk.Frame(mid, bg="#24273a")
        listframe.pack(side="left", fill="y")
        tk.Entry(listframe, textvariable=self.filter_var, font=("", 10),
                 bg="#363a4f", fg="#cad3f5", relief="flat", width=30).pack(fill="x", pady=(0, 4))
        self.filter_var.trace_add("write", lambda *a: self.refresh_list(keep_selection=True))
        lbframe = tk.Frame(listframe, bg="#24273a")
        lbframe.pack(fill="y", expand=True)
        self.list = tk.Listbox(lbframe, height=12, width=32, font=("", 11),
                               bg="#363a4f", fg="#cad3f5",
                               selectbackground="#8aadf4", selectforeground="#24273a", relief="flat")
        self.list.pack(side="left", fill="y")
        vsb = tk.Scrollbar(lbframe, orient="vertical", command=self.list.yview)
        vsb.pack(side="left", fill="y")
        hsb = tk.Scrollbar(listframe, orient="horizontal", command=self.list.xview)
        hsb.pack(fill="x")
        self.list.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.list.bind("<<ListboxSelect>>", self.show_info)
        self.list.bind("<Double-Button-1>", lambda e: self.apply_selected())

        right = tk.Frame(mid, bg="#24273a")
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(right, text="Preview (left, link, text, busy)", font=("", 10, "bold"), **pad).pack(anchor="w")
        self.prev = tk.Frame(right, bg="#363a4f")
        self.prev.pack(fill="x", pady=(2, 8))
        self.prev_imgs = []
        self.prev_caps = []
        for role in PREVIEW_ROLES:
            cell = tk.Frame(self.prev, bg="#363a4f")
            cell.pack(side="left", padx=5, pady=8)
            img = tk.Label(cell, bg="#363a4f", width=PREVIEW_PX, height=PREVIEW_PX)
            img.pack()
            cap = tk.Label(cell, bg="#363a4f", fg="#8aadf4", font=("", 9), width=14)
            cap.pack()
            self.prev_imgs.append(img)
            self.prev_caps.append(cap)

        tk.Label(right, textvariable=self.info_var, bg="#363a4f", fg="#cad3f5",
                 wraplength=320, justify="left").pack(fill="x", pady=(0, 8))

        # --- conversion options ---
        opt = tk.LabelFrame(right, text="Convert options", bg="#24273a", fg="#cad3f5",
                            padx=8, pady=10)
        opt.pack(fill="x", pady=(10, 8))

        tk.Label(opt, text="Inherits:", bg="#24273a", fg="#cad3f5").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.inherit_var = tk.StringVar(value=C.DEFAULT_INHERIT)
        tk.Entry(opt, textvariable=self.inherit_var, width=22).grid(row=0, column=1, padx=6, pady=2)

        self.color_var = tk.StringVar(value="no recolor")
        tk.Button(opt, text="Recolor…", command=self.pick_color,
                  bg="#8aadf5", fg="#24273a", relief="flat").grid(row=1, column=0, padx=6, pady=2)
        tk.Label(opt, textvariable=self.color_var, bg="#24273a", fg="#cad3f5").grid(
            row=1, column=1, sticky="w", padx=6, pady=2)

        tk.Label(opt, text="Strength:", bg="#24273a", fg="#cad3f5").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.strength = tk.DoubleVar(value=0.5)
        tk.Scale(opt, variable=self.strength, from_=0.0, to=1.0, resolution=0.05,
                 orient="horizontal", bg="#24273a", fg="#cad3f5",
                 highlightthickness=0, length=150).grid(row=2, column=1, padx=6, pady=2)

        btns = tk.Frame(root, bg="#24273a")
        btns.pack(pady=8)
        for txt, cmd in [("Apply theme", self.apply_selected),
                         ("Convert new cursor…", self.convert_new),
                         ("Delete theme", self.delete_selected),
                         ("Revert to Adwaita", self.revert),
                         ("Refresh", self.refresh_list)]:
            tk.Button(btns, text=txt, command=cmd, bg="#8aadf5", fg="#24273a",
                      activebackground="#7dc4e4", padx=10, pady=4, relief="flat").pack(side="left", padx=5)

        sizerow = tk.Frame(root, bg="#24273a")
        sizerow.pack(pady=(0, 4))
        tk.Label(sizerow, text="Cursor size:", bg="#24273a", fg="#cad3f5").pack(side="left", padx=4)
        self.size_var = tk.StringVar(value=self.current_size())
        tk.Spinbox(sizerow, from_=24, to=96, increment=8, width=5,
                   textvariable=self.size_var, bg="#363a4f", fg="#cad3f5",
                   relief="flat").pack(side="left", padx=4)
        tk.Button(sizerow, text="Set size", command=self.apply_size,
                  bg="#8aadf5", fg="#24273a", activebackground="#7dc4e4",
                  padx=10, relief="flat").pack(side="left", padx=4)

        self.prog = tk.Label(root, textvariable=self.status_var, bg="#24273a", fg="#8aadf4")
        self.prog.pack(pady=(0, 10))

        if HAS_DND:
            root.drop_target_register(DND_FILES)
            root.dnd_bind("<<Drop>>", self.on_drop)
            self.status_var.set("Pick a theme, convert a pack, or drag & drop files here.")

        self.refresh_list()

    # -- theme list -----------------------------------------------------
    def refresh_list(self, keep_selection=False):
        keep = self.selected_name()
        self.themes = installed_themes()
        cur = current_theme()
        filt = self.filter_var.get().strip().lower()
        self.list.delete(0, "end")
        names = sorted(n for n in self.themes
                       if not filt or filt in n.lower())
        for name in names:
            mark = "  ◉" if name == cur else ""
            self.list.insert("end", name + mark)
        target = keep if keep in names else (cur if cur in names else None)
        if target is not None:  # keep selection across refreshes
            idx = names.index(target)
            self.list.selection_set(idx)
            self.list.see(idx)
            self.show_info(None)

    def selected_name(self):
        sel = self.list.curselection()
        if not sel:
            return None
        text = self.list.get(sel[0])
        return text[:-len("  ◉")] if text.endswith("  ◉") else text

    def show_info(self, _):
        name = self.selected_name()
        if not name:
            return
        path = self.themes.get(name, "")
        try:
            n = len(os.listdir(os.path.join(path, "cursors"))) if path else 0
        except OSError:
            n = 0
        inh = f", inherits: {inherited_from(path)}" if path else ""
        self.info_var.set(f"{name}: {n} cursor files{inh}")
        self.show_preview(path)

    def show_preview(self, path):
        cdir = os.path.join(path, "cursors") if path else ""
        self.photos.clear()
        for lab, cap, role in zip(self.prev_imgs, self.prev_caps, PREVIEW_ROLES):
            im = xcur_first_frame(os.path.join(cdir, role), size=PREVIEW_PX) if cdir else None
            if im is not None and HAS_IMAGETK:
                bg = checkerboard(PREVIEW_PX)
                bg.alpha_composite(im, ((PREVIEW_PX - im.width) // 2,
                                        (PREVIEW_PX - im.height) // 2))
                ph = ImageTk.PhotoImage(bg)
                self.photos.append(ph)  # keep a reference
                lab.configure(image=ph)
                cap.configure(text=role)
            else:
                lab.configure(image="")
                cap.configure(text=role if (cdir and os.path.exists(os.path.join(cdir, role))) else "—")

    # -- actions --------------------------------------------------------
    @staticmethod
    def current_size():
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "cursor-size"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            return str(int(out))
        except Exception:
            return "24"

    def apply_size(self):
        try:
            size = int(self.size_var.get())
        except (ValueError, TypeError):
            messagebox.showerror("win2xcursor", "Size must be a number (e.g. 24, 32, 48).")
            return
        if size < 16 or size > 256:
            messagebox.showerror("win2xcursor", "Size must be between 16 and 256.")
            return
        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface",
                            "cursor-size", str(size)], check=True, timeout=10)
        except Exception as e:
            messagebox.showerror("win2xcursor", f"Could not set size:\n{e}")
            return
        messagebox.showinfo("win2xcursor", f"Cursor size set to {size}.\nRestart apps to see it everywhere.")

    def delete_selected(self):
        import shutil
        name = self.selected_name()
        if not name:
            messagebox.showinfo("win2xcursor", "Pick a theme first.")
            return
        path = self.themes.get(name, "")
        if not path or os.path.realpath(path).startswith("/usr/share/"):
            messagebox.showerror("win2xcursor", "Refusing to delete a system theme.")
            return
        if not messagebox.askyesno("win2xcursor", f"Delete theme {name!r}?\n{path}"):
            return
        try:
            if os.path.islink(path):
                os.unlink(path)
            else:
                shutil.rmtree(path)
        except OSError as e:
            messagebox.showerror("win2xcursor", f"Could not delete:\n{e}")
            return
        self.refresh_list()

    def apply_selected(self):
        name = self.selected_name()
        if not name:
            messagebox.showinfo("win2xcursor", "Pick a theme first.")
            return
        try:
            C.apply_theme(name)
        except (OSError, ValueError) as e:
            messagebox.showerror("win2xcursor", f"Could not apply:\n{e}")
            return
        self.refresh_list()
        messagebox.showinfo("win2xcursor", f"Applied: {name}\n\nRe-login (or restart apps) to see it everywhere.")

    def revert(self):
        try:
            C.apply_theme("Adwaita")
        except (OSError, ValueError) as e:
            messagebox.showerror("win2xcursor", f"Could not revert:\n{e}")
            return
        self.refresh_list()
        messagebox.showinfo("win2xcursor", "Switched back to Adwaita (system default).")

    def pick_color(self):
        rgb, _hex = colorchooser.askcolor(title="Blend cursors toward…")
        if rgb is None:  # cancelled: keep the previous choice
            return
        if rgb:
            self.tint = tuple(int(v) for v in rgb)
            self.color_var.set(f"rgb{self.tint}")
        else:
            self.tint = None
            self.color_var.set("no recolor")

    def convert_new(self):
        paths = filedialog.askopenfilenames(
            title="Choose cursor file(s)", filetypes=[("Cursor packs", "*.zip *.cur *.ani"), ("All", "*")])
        if not paths:
            return
        self.start_convert(list(paths))

    def on_drop(self, event):
        """Handle files dropped anywhere on the window (needs tkinterdnd2)."""
        paths = [p for p in C.parse_drop_files(event.data)
                 if os.path.exists(p) and C.is_cursor_source(p)]
        if not paths:
            messagebox.showinfo("win2xcursor", "Drop .zip / .cur / .ani files (or a folder of them).")
            return
        self.root.lift()
        self.start_convert(paths)

    def start_convert(self, paths):
        if getattr(self, "_job_active", False):
            messagebox.showinfo("win2xcursor", "A conversion is already running.")
            return
        default = C.sanitize(os.path.splitext(os.path.basename(paths[0]))[0])
        raw_name = simpledialog.askstring("Theme name", "Name for the new theme:", initialvalue=default)
        if raw_name is None or not raw_name.strip():
            messagebox.showinfo("win2xcursor", "Conversion cancelled.")
            return
        name = C.sanitize(raw_name.strip())
        try:
            strength = float(self.strength.get())
        except (tk.TclError, ValueError, TypeError):
            messagebox.showerror("win2xcursor", "Bad strength value; must be a number 0-1.")
            return
        inherit = self.inherit_var.get().strip() or C.DEFAULT_INHERIT
        if inherit not in self.themes:
            messagebox.showwarning("win2xcursor",
                f"Fallback theme {inherit!r} is not installed; missing roles will fall back to Adwaita instead.")
        self._job_active = True
        self.status_var.set(f"Converting {len(paths)} file(s) → {name} …")
        self.root.update_idletasks()
        t = threading.Thread(target=self._convert_bg,
                             args=(list(paths), name, inherit,
                                   self.tint, strength),
                             daemon=True)
        t.start()
        self.root.after(150, self._poll_job)

    def _convert_bg(self, paths, name, inherit, tint, strength):
        tdirs = {}
        errors = []
        try:
            for p in paths:
                try:
                    t = C.convert_input(p, theme_name=name, out_dir=OUT_DIR,
                                        inherit=inherit, tint=tint,
                                        strength=strength if tint else 0.0)
                except Exception as e:  # keep the batch going; report at the end
                    errors.append(f"{os.path.basename(p)}: {type(e).__name__}: {e}")
                    continue
                tdirs.update(t)
        except Exception as e:  # never silently die in a thread
            errors.append(str(e))
        self.job_q.put(("done", (name, tdirs, errors)))

    def _poll_job(self):
        try:
            kind, payload = self.job_q.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_job)
            return
        self._job_active = False
        if kind == "error":  # legacy payload; current code always sends "done"
            self.status_var.set("Conversion failed.")
            messagebox.showerror("win2xcursor", f"Could not convert:\n{payload}")
            return
        name, tdirs, errors = payload
        self.refresh_list()
        if errors:
            messagebox.showwarning("win2xcursor", "Some files failed:\n" + "\n".join(errors))
        if not tdirs:
            self.status_var.set("Nothing converted.")
            if not errors:
                messagebox.showerror("win2xcursor", "Could not convert those files.")
            return
        try:
            total = sum(len(os.listdir(os.path.join(d, "cursors"))) for d in tdirs.values())
        except OSError:
            total = 0
        self.status_var.set(f"Installed {name} ({total} cursors). Select it and Apply.")
        messagebox.showinfo("win2xcursor", f"Installed theme: {name} ({total} cursors).\nSelect it and Apply.")
        idx = [i for i, x in enumerate(self.list.get(0, "end")) if x.startswith(name)]
        if idx:
            self.list.selection_set(idx[0])
            self.show_info(None)


if __name__ == "__main__":
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    root.mainloop()
