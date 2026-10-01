#!/usr/bin/env python3
"""
win2xcursor GUI - simple lightweight Tkinter frontend to preview, convert and apply cursors.

Run:  python3 gui.py
Deps: python3, python3-tk, python3-pil (Pillow)
"""
import os, sys, glob, subprocess, tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cursor_converter as C

SEARCH = [os.path.expanduser("~/.icons"), os.path.expanduser("~/.local/share/icons"), "/usr/share/icons"]

def installed_themes():
    themes = {}
    for base in SEARCH:
        for d in glob.glob(os.path.join(base, "*")):
            if os.path.isdir(os.path.join(d, "cursors")) and os.path.exists(os.path.join(d, "index.theme")):
                themes[os.path.basename(d)] = d
    return themes

def inherited_from(path):
    try:
        for line in open(os.path.join(path, "index.theme")):
            if line.strip().lower().startswith("inherits="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

def current_theme():
    try:
        return subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"],
                              capture_output=True, text=True).stdout.strip().strip("'")
    except Exception:
        return "?"

class App:
    def __init__(self, root):
        self.root = root
        root.title("win2xcursor — Cursor Themes")
        root.geometry("620x420")
        root.configure(bg="#24273a")
        self.themes = installed_themes()
        self.info_var = tk.StringVar()

        pad = {"bg": "#24273a", "fg": "#cad3f5"}
        fps = {"bg": "#363a4f", "fg": "#cad3f5"}

        tk.Label(root, text="win2xcursor — Cursor Themes", font=("", 18, "bold"), **pad).pack(pady=10)

        self.list = tk.Listbox(root, height=12, font=("", 11), bg="#363a4f", fg="#cad3f5",
                               selectbackground="#8aadf4", selectforeground="#24273a", relief="flat")
        self.list.pack(fill="both", expand=True, padx=18)
        self.list.bind("<<ListboxSelect>>", self.show_info)
        self.refresh_list()

        tk.Label(root, textvariable=self.info_var, **fps, wraplength=560, justify="left").pack(
            fill="x", padx=18, pady=(8, 0))

        btns = tk.Frame(root, bg="#24273a")
        btns.pack(pady=12)
        for txt, cmd in [("Apply theme", self.apply_selected),
                         ("Convert new cursor…", self.convert_new),
                         ("Revert to Vimix", self.revert),
                         ("Refresh", self.refresh_list)]:
            tk.Button(btns, text=txt, command=cmd, bg="#8aadf5", fg="#24273a",
                      activebackground="#7dc4e4", padx=12, pady=4, relief="flat").pack(side="left", padx=5)

    def refresh_list(self):
        self.themes = installed_themes()
        cur = current_theme()
        self.list.delete(0, "end")
        for name in sorted(self.themes):
            mark = "  ◉" if name == cur else ""
            self.list.insert("end", name + mark)

    def show_info(self, _):
        sel = self.list.curselection()
        if not sel:
            return
        name = self.list.get(sel[0]).replace("  ◉", "")
        path = self.themes.get(name, "")
        n = len(os.listdir(os.path.join(path, "cursors"))) if path else 0
        inh = f", inherits: {inherited_from(path)}" if path else ""
        self.info_var.set(f"{name}: {n} cursor files{inh}")

    def apply_selected(self):
        sel = self.list.curselection()
        if not sel:
            messagebox.showinfo("win2xcursor", "Pick a theme first.")
            return
        name = self.list.get(sel[0]).replace("  ◉", "")
        C.apply_theme(name)
        self.refresh_list()
        messagebox.showinfo("win2xcursor", f"Applied: {name}\n\nRe-login (or restart apps) to see it everywhere.")

    def revert(self):
        C.apply_theme("Vimix-cursors")
        self.refresh_list()
        messagebox.showinfo("win2xcursor", "Switched back to Vimix-cursors.")

    def convert_new(self):
        paths = filedialog.askopenfilenames(
            title="Choose cursor file(s)", filetypes=[("Cursor packs", "*.zip *.cur *.ani"), ("All", "*")])
        if not paths:
            return
        name = simpledialog.askstring("Theme name", "Name for the new theme:", initialvalue="My-New-Cursor")
        if not name:
            messagebox.showinfo("win2xcursor", "Conversion cancelled.")
            return
        tint = None
        rec = simpledialog.askstring(
            "Recolor (optional)",
            "Optional: blend the cursor toward a color.\nEnter R,G,B (e.g. 200,60,120) or leave empty for natural colors.",
            initialvalue="")
        if rec and rec.strip():
            try:
                parts = [int(x) for x in rec.replace(";", ",").split(",")[:3]]
                if len(parts) == 3 and all(0 <= v <= 255 for v in parts):
                    tint = tuple(parts)
                else:
                    messagebox.showwarning("win2xcursor", "Bad color value; keeping original colors.")
            except ValueError:
                messagebox.showwarning("win2xcursor", "Bad color value; keeping original colors.")
        tdirs = {}
        for p in paths:
            t = C.convert_input(p, theme_name=name, out_dir=os.path.expanduser("~/.icons"),
                                tint=tint, strength=0.5 if tint else 0.0)
            tdirs.update(t)
        if not tdirs:
            messagebox.showerror("win2xcursor", "Could not convert those files.")
            return
        messagebox.showinfo("win2xcursor", f"Installed theme: {name}. Select it and Apply.")
        self.refresh_list()
        idx = [i for i, x in enumerate(self.list.get(0, "end")) if x.startswith(name)]
        if idx:
            self.list.selection_set(idx[0])

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
