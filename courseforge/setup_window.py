"""The first-run offer: install the optional tools before going in.

Shown once, on the first launch, when something the accessibility areas use is
missing. It is an offer and not a gate. Everything except OCR on scanned PDFs
and the PDF/UA compliance proof works without any of it, and an instructor on a
managed machine may simply not be allowed to install software, so "Not now"
always works and the answer is remembered either way.

The install runs on a worker thread and reports line by line, because winget
can sit for a minute on a slow link and a window that says nothing for a minute
looks like a window that has crashed.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

from . import tools

BG = "#1c2026"
CARD = "#232830"
INK = "#e7eaee"
MUTED = "#98a2b1"
OK = "#6fbf95"
WARN = "#d9a441"
BAD = "#e08078"


class SetupWindow:
    """Returns when the person has answered. `self.answer` says how."""

    def __init__(self, cfg, plan: dict, parent: tk.Tk | None = None):
        self.cfg = cfg
        self.plan = plan
        self.answer = "skip"
        self.result: dict = {}
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        self.root = tk.Toplevel(parent) if parent else tk.Tk()
        self.root.title("CourseForge Studio setup")
        self.root.configure(bg=BG)
        # Resizable, and never fixed to a guessed height. A hardcoded size is a
        # bet that three tool names, a font and a screen's scaling all render
        # the way they did on the machine that wrote it, and when that bet is
        # wrong the buttons are the part that falls off the bottom.
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._dismiss)

        self.f_title = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.f_body = tkfont.Font(family="Segoe UI", size=9)
        self.f_mono = tkfont.Font(family="Consolas", size=8)
        self.f_btn = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self._build()
        self._size_to_fit()
        self.root.after(120, self._drain)

    def _size_to_fit(self) -> None:
        """Take the height the content actually asked for, then place it.

        Capped at most of the screen so a long list cannot run off a laptop,
        and floored so the window never opens as a sliver.
        """
        self.root.update_idletasks()
        w = max(520, self.root.winfo_reqwidth())
        want = self.root.winfo_reqheight()
        h = max(360, min(want, int(self.root.winfo_screenheight() * 0.85)))
        self.root.minsize(480, min(h, 420))
        x = max(0, (self.root.winfo_screenwidth() - w) // 2)
        y = max(0, (self.root.winfo_screenheight() - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        pad = {"padx": 22}

        # Pack order is priority when there is not enough room, so the way out
        # is claimed first and everything else divides what is left. Built the
        # other way round -- heading, then list, then buttons -- a longer list
        # of tools pushes the buttons off the bottom and the window has no
        # answer left in it.
        row = tk.Frame(self.root, bg=BG)
        row.pack(side="bottom", fill="x", pady=(12, 16), **pad)

        # Starts short; it only earns height once there is something to read.
        self.log = tk.Text(self.root, height=4, bg="#14171b", fg=MUTED, font=self.f_mono,
                           relief="flat", wrap="word", state="disabled",
                           highlightthickness=0, padx=10, pady=8)
        self.log.pack(side="bottom", fill="both", expand=True, pady=(12, 0), **pad)

        n = len(self.plan["rows"])
        heading = ("One optional tool is missing" if n == 1
                   else f"{n} optional tools are missing")
        tk.Label(self.root, text=heading, font=self.f_title,
                 bg=BG, fg=INK).pack(anchor="w", pady=(18, 0), **pad)
        tk.Label(self.root,
                 text="CourseForge Studio works without them. They add OCR for scanned\n"
                      "PDFs and the PDF/UA compliance proof. You can do this later from\n"
                      "a terminal instead.",
                 font=self.f_body, bg=BG, fg=MUTED, justify="left").pack(anchor="w", pady=(6, 0), **pad)

        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", pady=(14, 0), **pad)
        found = tools.detect(self.cfg)
        for row_ in self.plan["rows"]:
            how = ("installs itself" if row_["how"] == "winget"
                   else "by hand" if row_["how"] == "manual"
                   else "downloaded and installed for you" if row_["how"] == "download"
                   else "pip")
            tk.Label(card, text=f"{row_['label']}  ({how})", font=self.f_body,
                     bg=CARD, fg=INK, anchor="w").pack(anchor="w", padx=12, pady=(8, 0))
            note = found.get(row_["tool"], {}).get("enables", "")
            # Say why a thing is by hand where the label is, not in a log nobody
            # has opened yet. "By hand" with no reason beside it just reads as
            # the tool being lazy about it.
            why = tools.MANUAL_WHY.get(row_["tool"], "")
            for text in (note, why):
                if text:
                    tk.Label(card, text=text, font=self.f_body, bg=CARD, fg=MUTED,
                             anchor="w", wraplength=440, justify="left").pack(anchor="w", padx=12)
        tk.Label(card, text="", bg=CARD).pack(pady=(0, 6))
        self.skip_btn = tk.Button(row, text="Not now", font=self.f_btn, command=self._skip,
                                  bg=CARD, fg=INK, activebackground="#2c323a",
                                  activeforeground=INK, relief="flat", cursor="hand2",
                                  padx=14, pady=8)
        self.skip_btn.pack(side="left")
        self.never_btn = tk.Button(row, text="Never ask again", font=self.f_body,
                                   command=self._never, bg=BG, fg=MUTED,
                                   activebackground=BG, activeforeground=INK,
                                   relief="flat", cursor="hand2", padx=10, pady=8)
        self.never_btn.pack(side="left", padx=(8, 0))
        self.go_btn = tk.Button(row, text="Install now", font=self.f_btn, command=self._install,
                                bg="#2f6f4f", fg="white", activebackground="#3d8a63",
                                activeforeground="white", relief="flat", cursor="hand2",
                                padx=16, pady=8)
        self.go_btn.pack(side="right")

    def _say(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ------------------------------------------------------------- answers
    def _skip(self) -> None:
        """The Not now button: a real answer, and it is remembered."""
        if self.busy:
            # Leaving mid-install would orphan the installer, not stop it.
            self._say("Still installing. Let it finish, or close the window from the taskbar.")
            return
        self.answer = "skip"
        tools.remember_setup(asked=True, installed=[], skipped=True)
        self.root.destroy()

    def _dismiss(self) -> None:
        """The titlebar X: not an answer, so nothing is written down.

        Closing a window and answering its question are different acts, and
        treating them the same is how someone ends up never seeing an offer
        they never actually declined -- which is exactly what happened when a
        layout bug put the buttons out of reach and the only way out was the X.
        There are two plain ways to stop being asked, and both are on the window.
        """
        if self.busy:
            self._say("Still installing. Let it finish, or close the window from the taskbar.")
            return
        self.answer = "dismissed"
        self.root.destroy()

    def _never(self) -> None:
        if self.busy:
            return
        self.answer = "never"
        tools.remember_setup(asked=True, never=True)
        self.root.destroy()

    def _install(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.go_btn.config(state="disabled", text="Installing...")
        self.never_btn.config(state="disabled")
        self._say("Installing. Windows may ask you to allow it.")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            out = tools.install_stream(self.cfg, on_line=lambda t: self.events.put(("line", t)))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("line", f"{type(exc).__name__}: {exc}"))
            out = {"installed": [], "failed": ["everything"], "manual": [], "steps": ""}
        self.events.put(("done", out))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line":
                    self._say(payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(120, self._drain)

    def _finish(self, out: dict) -> None:
        self.busy = False
        self.result = out
        self.answer = "installed"
        tools.remember_setup(asked=True, installed=out.get("installed") or [],
                             failed=out.get("failed") or [])
        if out.get("steps"):
            self._say("")
            self._say(out["steps"])
        self._say("")
        if out.get("installed"):
            self._say("Done. A tool installed just now is on the PATH of new programs "
                      "only, so close this window and start CourseForge Studio again "
                      "for it to be seen.")
        else:
            self._say("Nothing was installed. The app works without these; the "
                      "accessibility area will say what is missing where it matters.")
        self.go_btn.config(state="normal", text="Continue")
        self.go_btn.config(command=self.root.destroy)
        self.skip_btn.config(text="Close")

    def run(self) -> str:
        self.root.grab_set()
        self.root.wait_window()
        return self.answer


def offer(cfg, parent=None) -> str | None:
    """Show the first-run offer if there is one. None when there was nothing."""
    try:
        plan = tools.should_offer_setup(cfg)
    except Exception:  # noqa: BLE001
        return None
    if not plan:
        return None
    try:
        return SetupWindow(cfg, plan, parent).run()
    except Exception:  # noqa: BLE001
        # A setup window that will not draw must never stop the app starting.
        return None
