#!/usr/bin/env python3
"""
song_gui.py — simple desktop UI for song_downloader.

Load a Firefox bookmarks HTML file, browse the folder tree, tick the folders
you want, and download YouTube / SoundCloud links as MP3s.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import song_downloader as sd

CHECK = "☑"
UNCHECK = "☐"


class SongGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Song Download")
        self.minsize(860, 560)
        self.geometry("980x640")

        self.supported: list[dict] = []
        self.total_counts: dict[tuple, int] = {}
        self.path_by_iid: dict[str, tuple] = {}
        self.checked: set[str] = set()
        self.children_of: dict[str, list[str]] = {}
        self.parent_of: dict[str, str | None] = {}

        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()

        self._build()
        self.after(100, self._drain_queue)

        default_bm = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookmarks.html")
        if os.path.isfile(default_bm):
            self.bookmarks_var.set(default_bm)
            self.load_bookmarks()

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # --- file row ---
        files = ttk.Frame(root)
        files.pack(fill="x", **pad)

        ttk.Label(files, text="Bookmarks").grid(row=0, column=0, sticky="w")
        self.bookmarks_var = tk.StringVar()
        ttk.Entry(files, textvariable=self.bookmarks_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(files, text="Browse…", command=self.browse_bookmarks).grid(row=0, column=2)
        ttk.Button(files, text="Load", command=self.load_bookmarks).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(files, text="Save MP3s to").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.out_var = tk.StringVar(value=os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads"))
        ttk.Entry(files, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(files, text="Browse…", command=self.browse_out).grid(row=1, column=2, pady=(8, 0))

        ttk.Label(files, text="Cookies").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.cookies_var = tk.StringVar(value=sd.DEFAULT_COOKIES_BROWSER)
        cookie_row = ttk.Frame(files)
        cookie_row.grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Combobox(
            cookie_row,
            textvariable=self.cookies_var,
            values=("firefox", "chrome", "edge", "brave", "none"),
            width=12,
            state="readonly",
        ).pack(side="left")
        ttk.Label(cookie_row, text="  (log into YouTube in that browser first)", foreground="#666").pack(side="left")

        files.columnconfigure(1, weight=1)

        # --- middle: tree + log ---
        mid = ttk.Panedwindow(root, orient="horizontal")
        mid.pack(fill="both", expand=True, **pad)

        left = ttk.Frame(mid)
        right = ttk.Frame(mid)
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        head = ttk.Frame(left)
        head.pack(fill="x")
        ttk.Label(head, text="Folders with YouTube / SoundCloud songs").pack(side="left")
        self.summary_var = tk.StringVar(value="Load a bookmarks file to begin.")
        ttk.Label(head, textvariable=self.summary_var, foreground="#555").pack(side="right")

        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.tree = ttk.Treeview(tree_wrap, columns=("songs",), selectmode="browse", show="tree headings")
        self.tree.heading("#0", text="Folder", anchor="w")
        self.tree.heading("songs", text="Songs", anchor="e")
        self.tree.column("#0", stretch=True, minwidth=200)
        self.tree.column("songs", width=70, minwidth=50, stretch=False, anchor="e")
        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_tree_click)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Select all", command=self.select_all).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear_selection).pack(side="left", padx=6)
        ttk.Button(btns, text="Expand all", command=lambda: self._set_open(True)).pack(side="left")
        ttk.Button(btns, text="Collapse", command=lambda: self._set_open(False)).pack(side="left", padx=6)
        self.selected_count_var = tk.StringVar(value="0 songs selected")
        ttk.Label(btns, textvariable=self.selected_count_var).pack(side="right")

        ttk.Label(right, text="Log").pack(anchor="w")
        log_wrap = ttk.Frame(right)
        log_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(log_wrap, wrap="word", height=10, state="disabled", font=("Segoe UI", 9))
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # --- bottom actions ---
        bottom = ttk.Frame(root)
        bottom.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x", side="top", pady=(0, 8))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        self.cancel_btn = ttk.Button(bottom, text="Cancel", command=self.cancel_download, state="disabled")
        self.cancel_btn.pack(side="right")
        self.download_btn = ttk.Button(bottom, text="Start", command=self.start_download)
        self.download_btn.pack(side="right", padx=(0, 8))

    # -------------------------------------------------------------- actions
    def browse_bookmarks(self):
        path = filedialog.askopenfilename(
            title="Firefox bookmarks HTML",
            filetypes=[("HTML / bookmarks", "*.html;*.htm"), ("All files", "*.*")],
        )
        if path:
            self.bookmarks_var.set(path)
            self.load_bookmarks()

    def browse_out(self):
        path = filedialog.askdirectory(title="Save MP3s to")
        if path:
            self.out_var.set(path)

    def load_bookmarks(self):
        path = self.bookmarks_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Missing file", f"Can't find bookmarks file:\n{path}")
            return
        try:
            all_links = sd.parse_bookmarks(path)
        except OSError as exc:
            messagebox.showerror("Read error", str(exc))
            return

        self.supported = [link for link in all_links if sd.is_supported(link["url"])]
        _own, self.total_counts = sd.build_folder_counts(self.supported)
        self._rebuild_tree()
        n_folders = len([p for p in self.total_counts if p])
        self.summary_var.set(f"{len(self.supported)} songs in {n_folders} folders")
        self.status_var.set("Bookmarks loaded. Tick folders, then Download.")
        self._log(f"Loaded {path}")
        self._log(f"Found {len(self.supported)} YouTube / SoundCloud songs.")
        self._update_selected_count()

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.path_by_iid.clear()
        self.checked.clear()
        self.children_of.clear()
        self.parent_of.clear()

        # Only show folders that actually contain downloadable songs.
        paths = sorted(p for p in self.total_counts if self.total_counts[p] > 0 and p != ())
        iid_for: dict[tuple, str] = {}

        for path in paths:
            parent_path = path[:-1]
            parent_iid = iid_for.get(parent_path, "")
            name = path[-1]
            count = self.total_counts.get(path, 0)
            iid = self.tree.insert(
                parent_iid,
                "end",
                text=f"{UNCHECK}  {name}",
                values=(count,),
                open=False,
            )
            iid_for[path] = iid
            self.path_by_iid[iid] = path
            self.parent_of[iid] = parent_iid or None
            self.children_of.setdefault(parent_iid, []).append(iid)
            self.children_of.setdefault(iid, [])

        # Auto-expand the first "Songs" branch if present.
        for iid, path in self.path_by_iid.items():
            if path and path[-1].casefold() == "songs":
                self.tree.item(iid, open=True)
                # open ancestors
                parent = self.parent_of.get(iid)
                while parent:
                    self.tree.item(parent, open=True)
                    parent = self.parent_of.get(parent)

    def on_tree_click(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row or col != "#0":
            return
        # Only toggle when clicking near the checkbox / label, not the expand arrow.
        element = self.tree.identify("element", event.x, event.y)
        if element == "Treeitem.indicator":
            return
        self._toggle(row)
        return "break"

    def _toggle(self, iid, force=None, cascade_down=True, cascade_up=True):
        if iid not in self.path_by_iid:
            return
        currently = iid in self.checked
        new_state = (not currently) if force is None else force
        if new_state:
            self.checked.add(iid)
        else:
            self.checked.discard(iid)
        self._refresh_label(iid)

        if cascade_down:
            for child in self.children_of.get(iid, []):
                self._toggle(child, force=new_state, cascade_down=True, cascade_up=False)

        if cascade_up and new_state is False:
            parent = self.parent_of.get(iid)
            while parent:
                if parent in self.checked:
                    self.checked.discard(parent)
                    self._refresh_label(parent)
                parent = self.parent_of.get(parent)

        self._update_selected_count()

    def _refresh_label(self, iid):
        path = self.path_by_iid[iid]
        mark = CHECK if iid in self.checked else UNCHECK
        self.tree.item(iid, text=f"{mark}  {path[-1]}")

    def select_all(self):
        for iid in self.path_by_iid:
            self.checked.add(iid)
            self._refresh_label(iid)
        self._update_selected_count()

    def clear_selection(self):
        for iid in list(self.checked):
            self.checked.discard(iid)
            self._refresh_label(iid)
        self._update_selected_count()

    def _set_open(self, open_state: bool):
        for iid in self.path_by_iid:
            self.tree.item(iid, open=open_state)

    def _selected_paths(self) -> list[tuple]:
        # Prefer deepest checked nodes only when a parent is also checked —
        # prefix match already covers children, so keep all checked paths;
        # links_under_folders de-dupes URLs.
        return [self.path_by_iid[iid] for iid in self.checked]

    def _update_selected_count(self):
        links = sd.links_under_folders(self.supported, self._selected_paths())
        self.selected_count_var.set(f"{len(links)} songs selected")

    # ----------------------------------------------------------- download
    def start_download(self):
        if self._worker and self._worker.is_alive():
            return

        selected = self._selected_paths()
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick one or more folders first.")
            return

        items = sd.links_under_folders(self.supported, selected)
        if not items:
            messagebox.showinfo("Empty", "No YouTube / SoundCloud songs in the selected folders.")
            return

        out_dir = self.out_var.get().strip() or "downloads"
        cookies = self.cookies_var.get().strip()
        if cookies.lower() == "none":
            cookies = ""

        ffmpeg_dir = sd.find_ffmpeg_dir()
        if not ffmpeg_dir:
            if not messagebox.askyesno(
                "FFmpeg missing",
                "FFmpeg was not found. MP3 conversion will fail.\nContinue anyway?",
            ):
                return

        self._cancel.clear()
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(maximum=max(len(items), 1), value=0)
        self.status_var.set(f"Downloading 0 / {len(items)}…")
        self._log(f"Starting download of {len(items)} songs → {out_dir}")

        failures_path = os.path.join(out_dir, "failed_links.txt")

        def worker():
            def on_progress(i, total, title, status, detail=""):
                self._ui_queue.put(("progress", i, total, title, status, detail))

            try:
                ok, failed, skipped, bot_hits = sd.download_all(
                    items,
                    out_dir,
                    ffmpeg_dir,
                    failures_path,
                    cookies,
                    "",
                    on_progress=on_progress,
                    should_cancel=self._cancel.is_set,
                )
                self._ui_queue.put(("done", ok, failed, skipped, bot_hits, out_dir, failures_path))
            except Exception as exc:  # noqa: BLE001
                self._ui_queue.put(("error", str(exc)))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def cancel_download(self):
        self._cancel.set()
        self.status_var.set("Cancelling after current song…")

    def _drain_queue(self):
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, i, total, title, status, detail = msg
                    self.progress.configure(maximum=total, value=i if status != "start" else i - 1)
                    if status == "start":
                        self.status_var.set(f"Downloading {i} / {total}…")
                        self._log(f"[{i}/{total}] {title}")
                    elif status == "saved":
                        self._log("    saved")
                    elif status == "skipped":
                        self._log("    already have it")
                    elif status == "failed":
                        self._log(f"    FAILED: {detail}")
                    elif status == "cancelled":
                        self._log("    cancelled")
                elif kind == "done":
                    _, ok, failed, skipped, bot_hits, out_dir, failures_path = msg
                    fresh = ok - skipped
                    self.progress.configure(value=self.progress["maximum"])
                    self.status_var.set(f"Done. {fresh} new, {skipped} skipped, {failed} failed.")
                    self._log("-" * 40)
                    self._log(self.status_var.get())
                    self._log(f"MP3s: {os.path.abspath(out_dir)}")
                    if failed:
                        self._log(f"Failures: {os.path.abspath(failures_path)}")
                    if bot_hits:
                        self._log(sd.BOT_HELP.strip())
                    self.download_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    messagebox.showinfo("Finished", self.status_var.get())
                elif kind == "error":
                    self.status_var.set("Error.")
                    self._log(f"ERROR: {msg[1]}")
                    self.download_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    messagebox.showerror("Download error", msg[1])
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    app = SongGui()
    # Prefer a readable default font on Windows.
    try:
        style = ttk.Style(app)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()
