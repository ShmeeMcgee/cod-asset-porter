"""CoD Asset Porter - add exported files, pick a game, click Convert.

Run with no arguments for the window, or from a terminal:
    python app.py --game bo3 --out "C:/ported" file_or_folder [...]
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from porter import __version__  # noqa: E402
from porter.engine import classify, collect, convert_batch, default_pack_name  # noqa: E402
from porter.games import GAMES, ORDER  # noqa: E402
from porter.installs import find_game_folders, next_steps  # noqa: E402
from porter.models import safe_name  # noqa: E402

DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Documents", "CoD Asset Porter")
KIND_LABEL = {"model": "Model", "anim": "Anim", "texture": "Texture", "sound": "Sound", "gdt": "GDT"}


# --------------------------------------------------------------------------- CLI
def run_cli(argv):
    p = argparse.ArgumentParser(description="Convert exported CoD assets for another game's mod tools.")
    p.add_argument("--game", required=True, choices=list(ORDER))
    p.add_argument("--out", default=None, help="output folder (default: Documents/CoD Asset Porter/<game>)")
    p.add_argument("--into-game", action="store_true",
                   help="write straight into the game's mod tools folder (auto-detected)")
    p.add_argument("--pack", default=None, help="subfolder/GDT name for this batch")
    p.add_argument("--rig", default=None, help="model whose skeleton .seanim/.cast anims use")
    p.add_argument("--scale", type=float, default=1.0, help="scale models and anims (e.g. 2.54)")
    p.add_argument("paths", nargs="+")
    a = p.parse_args(argv)
    game = GAMES[a.game]
    if a.into_game:
        out = find_game_folders().get(a.game)
        if not out:
            p.error(f"Couldn't find the {game.short} mod tools folder; use --out instead.")
    else:
        out = a.out or os.path.join(DEFAULT_OUT, game.short)
    s = convert_batch(a.paths, a.game, out, rig_path=a.rig, pack=a.pack, scale=a.scale,
                      log=lambda lvl, msg: print(("\n" + msg) if lvl == "file" else f"  [{lvl}] {msg}"))
    print(f"\nOutput: {out}")
    if s["ok"]:
        print(next_steps(game, s["pack"]))
    return 1 if s["failed"] else 0


# --------------------------------------------------------------------------- GUI
BG, PANEL, BORDER = "#15171c", "#1e2128", "#2c3039"
TEXT, MUTED, ACCENT, ACCENT_HI = "#e9ebef", "#8b919c", "#4f8cff", "#6fa2ff"
LEVEL_COLORS = {"ok": "#5ccf8a", "warn": "#f0b54a", "error": "#ff6b6b",
                "file": TEXT, "info": MUTED, "done": ACCENT_HI}


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, ttk

    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        root = TkinterDnD.Tk()
        has_dnd = True
    except Exception:
        root = tk.Tk()
        has_dnd = False

    root.title(f"CoD Asset Porter {__version__}")
    root.geometry("820x760")
    root.minsize(680, 620)
    root.configure(bg=BG)
    font = "Segoe UI" if sys.platform == "win32" else "Helvetica"
    try:
        icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")
        if sys.platform == "win32" and os.path.isfile(icon):
            root.iconbitmap(icon)
    except Exception:
        pass

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Bar.Horizontal.TProgressbar", troughcolor=PANEL,
                    background=ACCENT, bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED)

    installs = {}
    state = {"files": [], "game": tk.StringVar(value="bo3"),
             "out": tk.StringVar(value=DEFAULT_OUT), "rig": tk.StringVar(),
             "pack": tk.StringVar(), "into_game": tk.BooleanVar(value=False),
             "pack_edited": False, "busy": False, "stop": False, "last_out": None}
    q = queue.Queue()

    def button(parent, text, cmd, primary=False, **kw):
        return tk.Button(parent, text=text, command=cmd, relief="flat", bd=0, cursor="hand2",
                         font=(font, 11 if primary else 10, "bold" if primary else "normal"),
                         bg=ACCENT if primary else PANEL, fg="white" if primary else TEXT,
                         activebackground=ACCENT_HI if primary else BORDER,
                         activeforeground="white", padx=14, pady=8 if primary else 5, **kw)

    def label(parent, text, size=10, color=TEXT, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color,
                        font=(font, size, "bold" if bold else "normal"), **kw)

    def section(title):
        label(body, title, 11, bold=True, anchor="w").pack(fill="x", pady=(12, 6))

    def entry(parent, var):
        return tk.Entry(parent, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                        relief="flat", highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=ACCENT, font=(font, 10))

    # Header
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=22, pady=(16, 0))
    label(head, "CoD Asset Porter", 18, bold=True).pack(anchor="w")
    label(head, "Add your exported files, pick the game they're going into, click Convert.",
          10, MUTED).pack(anchor="w", pady=(2, 0))

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=22, pady=(0, 16))

    # 1. Files
    section("1.  Files")
    files_box = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
    files_box.pack(fill="both", expand=False)
    lb = tk.Listbox(files_box, height=7, bg=PANEL, fg=TEXT, bd=0, highlightthickness=0,
                    selectbackground=BORDER, selectforeground=TEXT, activestyle="none",
                    font=(font, 10), selectmode="extended")
    sb = ttk.Scrollbar(files_box, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
    sb.pack(side="right", fill="y")
    empty_hint = label(files_box, ("Drag files or folders here, or use Add files"
                                   if has_dnd else "Use Add files or Add folder"), 10, MUTED)
    kinds = {}

    def refresh():
        lb.delete(0, "end")
        counts = {}
        for p in state["files"]:
            k = kinds.get(p) or classify(p)
            kinds[p] = k
            counts[k] = counts.get(k, 0) + 1
            lb.insert("end", f"  {KIND_LABEL[k]:<9}{os.path.basename(p)}")
        if state["files"]:
            empty_hint.place_forget()
        else:
            empty_hint.place(relx=0.5, rely=0.5, anchor="center")
        summary = ", ".join(f"{n} {KIND_LABEL[k].lower()}{'s' if n > 1 and k != 'gdt' else ''}"
                            for k, n in counts.items())
        count_lbl.config(text=summary or "Nothing added yet")
        if not state["pack_edited"]:
            state["pack"].set(default_pack_name(state["added"]) if state["files"] else "")

    state["added"] = []

    def add_paths(paths):
        state["added"].extend(paths)
        new = [p for p in collect(paths) if p not in state["files"]]
        skipped = [p for p in paths if os.path.isfile(p) and not classify(p)]
        state["files"].extend(new)
        refresh()
        if skipped:
            log("warn", f"Skipped {len(skipped)} unsupported file(s): "
                        + ", ".join(os.path.basename(s) for s in skipped[:4]))

    def add_files():
        paths = filedialog.askopenfilenames(title="Add exported files", filetypes=[
            ("Supported files", "*.semodel *.seanim *.cast *.xmodel_export *.xanim_export "
                                "*.xmodel_bin *.xanim_bin *.gdt *.png *.dds *.tga *.tif *.tiff "
                                "*.jpg *.jpeg *.bmp *.wav *.flac *.ogg *.mp3"),
            ("All files", "*.*")])
        add_paths(list(paths))

    def add_folder():
        d = filedialog.askdirectory(title="Add a folder (everything supported inside is added)")
        if d:
            add_paths([d])

    def remove_selected():
        for i in reversed(lb.curselection()):
            del state["files"][i]
        refresh()

    def clear():
        state["files"].clear()
        state["added"].clear()
        state["pack_edited"] = False
        refresh()

    row = tk.Frame(body, bg=BG)
    row.pack(fill="x", pady=(8, 0))
    for text, cmd in (("Add files", add_files), ("Add folder", add_folder),
                      ("Remove", remove_selected), ("Clear", clear)):
        button(row, text, cmd).pack(side="left", padx=(0, 6))
    count_lbl = label(row, "", 9, MUTED)
    count_lbl.pack(side="right")

    if has_dnd:
        for w in (lb, files_box, empty_hint):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", lambda e: add_paths(list(root.tk.splitlist(e.data))))

    # 2. Game
    section("2.  Convert to")
    games_row = tk.Frame(body, bg=BG)
    games_row.pack(fill="x")
    game_buttons = {}
    game_note = label(body, "", 9, MUTED, anchor="w", justify="left", wraplength=740)
    game_note.pack(fill="x", pady=(4, 0))

    def pick_game(key):
        state["game"].set(key)
        for k, b in game_buttons.items():
            on = k == key
            b.config(bg=ACCENT if on else PANEL, fg="white" if on else TEXT,
                     activebackground=ACCENT_HI if on else BORDER)
        g = GAMES[key]
        game_note.config(text=g.notes)
        if key in installs:
            into_chk.config(state="normal", text=f"Put files straight into my {g.short} folder  ({installs[key]})")
        else:
            state["into_game"].set(False)
            into_chk.config(state="disabled", text=f"Put files straight into my {g.short} folder  (not found)")
        update_out_hint()

    for key in ORDER:
        b = button(games_row, GAMES[key].short, lambda k=key: pick_game(k))
        b.config(pady=10)
        b.pack(side="left", expand=True, fill="x", padx=(0, 6))
        game_buttons[key] = b

    # 3. Output
    section("3.  Save to")
    into_chk = tk.Checkbutton(body, variable=state["into_game"], bg=BG, fg=TEXT, selectcolor=PANEL,
                              activebackground=BG, activeforeground=TEXT, font=(font, 10),
                              anchor="w", command=lambda: update_out_hint())
    into_chk.pack(fill="x")
    out_row = tk.Frame(body, bg=BG)
    out_row.pack(fill="x", pady=(4, 0))
    out_entry = entry(out_row, state["out"])
    out_entry.pack(side="left", fill="x", expand=True, ipady=6)
    out_browse = button(out_row, "Browse", lambda: state["out"].set(
        filedialog.askdirectory(title="Output folder") or state["out"].get()))
    out_browse.pack(side="left", padx=(6, 0))
    out_hint = label(body, "", 9, MUTED, anchor="w")
    out_hint.pack(fill="x", pady=(4, 0))

    def update_out_hint():
        into = state["into_game"].get()
        out_entry.config(state="disabled" if into else "normal")
        out_browse.config(state="disabled" if into else "normal")
        g = GAMES[state["game"].get()]
        if into:
            out_hint.config(text="Files go into the game's model_export, xanim_export, texture_assets, "
                                 "sound and source_data folders.")
        else:
            out_hint.config(text=f"Files go into a \"{g.short}\" folder inside this one. "
                                 "Copy its contents into your game folder when you're ready.")

    opts = tk.Frame(body, bg=BG)
    opts.pack(fill="x", pady=(10, 0))
    label(opts, "Pack name", 9, MUTED).grid(row=0, column=0, sticky="w")
    label(opts, "Skeleton for .seanim / .cast animations (optional)", 9, MUTED).grid(
        row=0, column=1, sticky="w", padx=(12, 0))
    pack_entry = entry(opts, state["pack"])
    pack_entry.grid(row=1, column=0, sticky="ew", ipady=5)
    pack_entry.bind("<Key>", lambda e: state.update(pack_edited=True))
    rig_row = tk.Frame(opts, bg=BG)
    rig_row.grid(row=1, column=1, sticky="ew", padx=(12, 0))
    entry(rig_row, state["rig"]).pack(side="left", fill="x", expand=True, ipady=5)
    button(rig_row, "Browse", lambda: state["rig"].set(filedialog.askopenfilename(
        title="Model the animations belong to",
        filetypes=[("Models", "*.semodel *.cast *.xmodel_export *.xmodel_bin")]) or state["rig"].get())
    ).pack(side="left", padx=(6, 0))
    button(rig_row, "x", lambda: state["rig"].set("")).pack(side="left", padx=(6, 0))
    opts.columnconfigure(0, weight=1)
    opts.columnconfigure(1, weight=2)

    # Convert + progress
    act = tk.Frame(body, bg=BG)
    act.pack(fill="x", pady=(14, 8))
    convert_btn = button(act, "Convert", lambda: start(), primary=True)
    convert_btn.pack(side="left")
    open_btn = button(act, "Open output folder", lambda: open_folder(state["last_out"]))
    pbar = ttk.Progressbar(act, style="Bar.Horizontal.TProgressbar", mode="determinate")
    pbar.pack(side="left", fill="x", expand=True, padx=(12, 0))

    # Log
    log_box = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
    log_box.pack(fill="both", expand=True)
    txt = tk.Text(log_box, bg=PANEL, fg=TEXT, bd=0, highlightthickness=0, wrap="word",
                  font=("Consolas" if sys.platform == "win32" else "Courier", 9),
                  padx=10, pady=8, state="disabled")
    lsb = ttk.Scrollbar(log_box, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=lsb.set)
    txt.pack(side="left", fill="both", expand=True)
    lsb.pack(side="right", fill="y")
    for lvl, color in LEVEL_COLORS.items():
        txt.tag_config(lvl, foreground=color)
    txt.tag_config("file", foreground=TEXT, font=(font, 9, "bold"))

    def log(level, msg):
        txt.config(state="normal")
        if level == "file":
            txt.insert("end", "\n" + msg + "\n", "file")
        else:
            prefix = {"warn": "  ! ", "error": "  x ", "ok": "  \u2713 "}.get(level, "  ")
            txt.insert("end", prefix + msg + "\n", level)
        txt.see("end")
        txt.config(state="disabled")

    def open_folder(path):
        if not path or not os.path.isdir(path):
            return
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def start():
        if state["busy"]:
            state["stop"] = True
            convert_btn.config(text="Stopping...")
            return
        if not state["files"]:
            log("warn", "Add some files first.")
            return
        game = GAMES[state["game"].get()]
        if state["into_game"].get() and game.key in installs:
            out = installs[game.key]
        else:
            out = os.path.join(state["out"].get().strip() or DEFAULT_OUT, game.short)
        pack = safe_name(state["pack"].get().strip() or default_pack_name(state["added"]), "ported")
        state.update(busy=True, stop=False, last_out=out)
        convert_btn.config(text="Stop")
        open_btn.pack_forget()
        pbar.config(value=0, maximum=max(1, len(state["files"])))
        txt.config(state="normal")
        txt.delete("1.0", "end")
        txt.config(state="disabled")
        files = list(state["files"])
        rig = state["rig"].get().strip() or None

        def work():
            try:
                s = convert_batch(files, game.key, out, rig_path=rig, pack=pack,
                                  log=lambda lv, m: q.put(("log", lv, m)),
                                  progress=lambda i, n: q.put(("progress", i, n)),
                                  should_stop=lambda: state["stop"])
                q.put(("done", s, game))
            except Exception as e:  # never leave the window stuck
                q.put(("log", "error", f"Unexpected error: {e}"))
                q.put(("done", None, game))
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, a, b = q.get_nowait()
                if kind == "log":
                    log(a, b)
                elif kind == "progress":
                    pbar.config(value=a, maximum=b)
                elif kind == "done":
                    state["busy"] = False
                    convert_btn.config(text="Convert")
                    if a and a["ok"]:
                        log("info", f"Saved to {state['last_out']}")
                        steps = next_steps(b, a["pack"])
                        if steps:
                            log("done", steps)
                        open_btn.pack(side="left", padx=(8, 0), before=pbar)
        except queue.Empty:
            pass
        root.after(60, poll)

    def detect():
        try:
            installs.update(find_game_folders())
        except Exception:
            pass
        q.put(("log", "info", "Found mod tools for: " + (", ".join(GAMES[k].short for k in ORDER if k in installs)
                                                         or "none (choose a folder below)")))
        root.after(0, lambda: pick_game(state["game"].get()))

    pick_game("bo3")
    refresh()
    log("info", f"CoD Asset Porter {__version__}.  Models: .semodel .cast .xmodel_export .xmodel_bin   "
                "Anims: .seanim .cast .xanim_export .xanim_bin   Textures: .png .dds .tga .tif .jpg   "
                "Sounds: .wav .flac .ogg .mp3   Plus any .gdt that came with a pack.")
    threading.Thread(target=detect, daemon=True).start()
    root.after(60, poll)
    root.mainloop()


if __name__ == "__main__":
    # The windowed .exe has no console: give print() somewhere harmless to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))
    run_gui()
