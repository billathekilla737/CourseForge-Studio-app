"""
courseforge_gui.py - modern desktop UI for CourseForge PDF Fixer (v1.1.0+).

CustomTkinter window over the SAME verbs as the console wizard
(courseforge_pdf.py): connect / back up & fix / describe with Claude /
upload / prove / back up / roll back. Design rules:

  - the core module stays the single source of truth: this file never
    re-implements Canvas or engine logic, it only calls the verbs
  - every verb runs on a worker thread; its print() output is captured
    and streamed into the log pane (one action at a time, buttons lock)
  - anything destructive gets a real dialog instead of "type YES"
  - CLI compatibility: any command-line argument routes to the console
    entry point, so scripted use (check/upload --yes/selftest) still works

Frozen entry point (PyInstaller --windowed). multiprocessing.freeze_support()
must run first or the engine's process pool forkbombs the app.
"""
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import tkinter.messagebox as mbox

import courseforge_pdf as core

# MGCCC brand palette - the same one every remediated course page uses
NAVY = "#061E3F"          # headings, primary fills
NAVY_MID = "#0E2C54"      # button fill
BLUE = "#236192"          # hover / info
GOLD = "#E9A821"          # accents: top bar, progress, CTA
GOLD_DARK = "#c78f1b"
RED = "#C11F31"           # brand red: destructive only
RED_DARK = "#8f1826"
PAGE_BG = "#f5f6f8"       # light ground, like the course pages


class QueueWriter:
    """stdout stand-in: verbs print(), the UI drains lines from a queue."""

    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class App:
    def __init__(self, ctk):
        self.ctk = ctk
        ctk.set_appearance_mode("light")
        self.root = ctk.CTk()
        self.root.title("%s  v%s" % (core.APP, core.VERSION))
        self.root.geometry("880x640")
        self.root.minsize(760, 520)
        ico = os.path.join(os.path.dirname(sys.executable)
                           if getattr(sys, "frozen", False)
                           else os.path.dirname(os.path.abspath(__file__)),
                           "cf-icon.ico")
        if os.path.isfile(ico):
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass

        self.q = queue.Queue()
        self.busy = False
        self.courses = []          # [{config dict}]

        self.root.configure(fg_color=PAGE_BG)
        ctk.CTkFrame(self.root, height=6, corner_radius=0,
                     fg_color=GOLD).pack(fill="x")
        ctk.CTkLabel(self.root, text="CourseForge PDF Fixer",
                     font=("Georgia", 24, "bold"), text_color=NAVY,
                     anchor="w").pack(fill="x", padx=16, pady=(10, 0))

        # ---- header row: course picker + connect
        top = ctk.CTkFrame(self.root, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(top, text="Course:", font=("Segoe UI", 13)).pack(side="left")
        self.course_var = ctk.StringVar(value="(no course connected)")
        self.course_menu = ctk.CTkOptionMenu(
            top, variable=self.course_var, values=["(no course connected)"],
            width=460, fg_color=NAVY_MID, button_color=NAVY,
            button_hover_color=BLUE, text_color="white")
        self.course_menu.pack(side="left", padx=8)
        self.btn_connect = ctk.CTkButton(
            top, text="Connect a course...", width=150,
            fg_color=GOLD, hover_color=GOLD_DARK, text_color=NAVY,
            font=("Segoe UI", 13, "bold"),
            command=self.connect_dialog)
        self.btn_connect.pack(side="left", padx=4)

        # ---- action buttons
        grid = ctk.CTkFrame(self.root, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=6)
        self.buttons = []

        def add(col, row, text, cmd, danger=False):
            b = ctk.CTkButton(
                grid, text=text, command=cmd, height=44,
                font=("Segoe UI", 13, "bold"),
                fg_color=(RED if danger else NAVY_MID),
                hover_color=(RED_DARK if danger else BLUE))
            b.grid(column=col, row=row, padx=5, pady=5, sticky="ew")
            self.buttons.append(b)
            return b

        for c in range(3):
            grid.grid_columnconfigure(c, weight=1)
        add(0, 0, "Back up & Fix PDFs", self.act_fix)
        add(1, 0, "Describe images (Claude)", self.act_describe)
        add(2, 0, "Upload to Canvas", self.act_upload)
        add(0, 1, "Prove compliance", self.act_prove)
        add(1, 1, "Back up only", self.act_backup)
        add(2, 1, "ROLL BACK originals", self.act_rollback, danger=True)

        # ---- progress + log
        self.progress = ctk.CTkProgressBar(self.root, mode="indeterminate",
                                           progress_color=GOLD)
        self.progress.pack(fill="x", padx=14, pady=(4, 2))
        self.progress.set(0)
        self.log = ctk.CTkTextbox(self.root, font=("Consolas", 12), wrap="word",
                                  fg_color="white", text_color="#2c3a4d",
                                  border_width=1, border_color="#d7dce3")
        self.log.pack(fill="both", expand=True, padx=14, pady=(4, 6))
        self.log.configure(state="disabled")

        foot = ctk.CTkFrame(self.root, fg_color="transparent")
        foot.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkButton(foot, text="Open work folder", width=140,
                      fg_color=BLUE, hover_color=NAVY,
                      command=self.open_folder).pack(side="left")
        ctk.CTkLabel(
            foot, text="Originals are always kept. Nothing changes in Canvas "
                       "without the Upload or Roll back buttons.",
            font=("Segoe UI", 11), text_color="#4b5563").pack(side="left", padx=10)

        self.refresh_courses()
        self.say("Welcome. Pick a course (or connect one) and choose an action.")
        if core.workroot_is_synced():
            self.say("")
            self.say("Note: your work folder is inside a cloud-synced "
                     "Documents folder:")
            self.say("  %s" % core.WORKROOT)
            self.say("Everything still works, but every PDF gets uploaded to "
                     "the cloud too, and sync can briefly lock a file. Your "
                     "Canvas token is NOT stored there.")
        self.root.after(100, self.drain)

    # ------------------------------------------------------------- helpers
    def say(self, text):
        self.q.put(text + "\n")

    def drain(self):
        try:
            chunks = []
            while True:
                chunks.append(self.q.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self.log.configure(state="normal")
            self.log.insert("end", "".join(chunks))
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(100, self.drain)

    def refresh_courses(self, select_id=None):
        self.courses = core.course_dirs()
        names = [c["course_name"] for c in self.courses] or ["(no course connected)"]
        self.course_menu.configure(values=names)
        if select_id:
            for c in self.courses:
                if c["course_id"] == str(select_id):
                    self.course_var.set(c["course_name"])
                    return
        if self.courses and self.course_var.get() not in names:
            self.course_var.set(names[0])

    def current_course_dir(self):
        name = self.course_var.get()
        for c in self.courses:
            if c["course_name"] == name:
                return os.path.join(core.WORKROOT, c["course_id"])
        mbox.showinfo(core.APP, "Connect a Canvas course first.")
        return None

    def set_busy(self, on):
        self.busy = on
        state = "disabled" if on else "normal"
        for b in self.buttons:
            b.configure(state=state)
        self.btn_connect.configure(state=state)
        if on:
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.set(0)

    def run_verb(self, label, fn, *args, **kw):
        """One verb at a time on a worker thread, stdout -> log pane."""
        if self.busy:
            return
        self.set_busy(True)
        self.say("")
        self.say("=== %s ===" % label)

        def worker():
            old = sys.stdout
            sys.stdout = QueueWriter(self.q)
            try:
                fn(*args, **kw)
            except Exception as e:
                core.log_event("error", step=label, error=str(e)[:300])
                print("Something went wrong: %s" % e)
                print("Nothing in Canvas is changed by an error like this.")
            finally:
                sys.stdout = old
                self.q.put("=== done ===\n")
                self.root.after(0, self.set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def open_folder(self):
        d = self.current_course_dir()
        if d:
            os.startfile(os.path.join(d, "pdf-fastlane")
                         if os.path.isdir(os.path.join(d, "pdf-fastlane")) else d)

    # ------------------------------------------------------------- actions
    def act_fix(self):
        d = self.current_course_dir()
        if d:
            self.run_verb("Back up & fix", core.do_check, d)

    def act_backup(self):
        d = self.current_course_dir()
        if d:
            self.run_verb("Back up only", core.do_backup, d)

    def act_prove(self):
        d = self.current_course_dir()
        if d:
            self.run_verb("Prove compliance (PDF/UA-1)", core.do_prove, d)

    def act_upload(self):
        d = self.current_course_dir()
        if not d:
            return
        if mbox.askyesno(core.APP,
                         "Upload the fixed PDFs to Canvas?\n\n"
                         "Files are replaced IN PLACE so course links keep "
                         "working, and the originals stay on this computer "
                         "(ROLL BACK can undo this)."):
            self.run_verb("Upload to Canvas", core.do_upload, d, assume_yes=True)

    def act_rollback(self):
        d = self.current_course_dir()
        if not d:
            return
        if mbox.askyesno(core.APP,
                         "ROLL BACK: put the ORIGINAL PDFs back into Canvas?\n\n"
                         "This replaces what is in Canvas now with the "
                         "originals saved on this computer.",
                         icon="warning"):
            self.run_verb("Roll back originals", core.do_rollback, d,
                          assume_yes=True)

    def act_describe(self):
        d = self.current_course_dir()
        if not d:
            return
        claude = core.find_claude()
        if not claude:
            mbox.showinfo(core.APP,
                          "This uses Claude Code with your own Claude (Max) "
                          "sign-in - no API key.\n\nIt is not installed yet. "
                          "One-time setup in PowerShell:\n\n"
                          "  irm https://claude.ai/install.ps1 | iex\n\n"
                          "then run  claude  once to sign in, and press this "
                          "button again.")
            return
        if not core._claude_logged_in(claude):
            if mbox.askyesno(core.APP,
                             "You are not signed in to Claude on this PC.\n\n"
                             "Open the Claude sign-in now? (A window will "
                             "open; sign in, then close it and press OK.)"):
                subprocess.Popen([claude],
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
                mbox.showinfo(core.APP, "Press OK here AFTER you have signed "
                                        "in and closed the Claude window.")
                if not core._claude_logged_in(claude):
                    mbox.showwarning(core.APP, "Still not signed in - "
                                               "descriptions skipped.")
                    return
            else:
                return
        if mbox.askyesno(core.APP,
                         "Describe this course's images using your Claude "
                         "account?\n\nThis uses your normal Claude "
                         "subscription usage (typically a few minutes)."):
            self.run_verb("Describe images with Claude", core.do_describe, d,
                          assume_yes=True)

    # ------------------------------------------------------------- connect
    def connect_dialog(self):
        ctk = self.ctk
        win = ctk.CTkToplevel(self.root)
        win.title("Connect a Canvas course")
        win.geometry("560x300")
        win.transient(self.root)
        win.grab_set()
        ctk.CTkLabel(win, text="Course web address (copy it from your browser):",
                     anchor="w").pack(fill="x", padx=16, pady=(16, 2))
        url_e = ctk.CTkEntry(win, width=520,
                             placeholder_text="https://school.instructure.com/courses/123456")
        url_e.pack(padx=16)
        ctk.CTkLabel(win, text="Canvas access token (Canvas > Account > Settings "
                               "> + New Access Token):", anchor="w").pack(
            fill="x", padx=16, pady=(14, 2))
        tok_row = ctk.CTkFrame(win, fg_color="transparent")
        tok_row.pack(fill="x", padx=16)
        tok_e = ctk.CTkEntry(tok_row, width=380, show="*",
                             placeholder_text="paste or use the button")
        tok_e.pack(side="left")

        def paste():
            v = (core.read_clipboard() or "").strip()
            if core._plausible_token(v):
                tok_e.delete(0, "end")
                tok_e.insert(0, v)
            else:
                mbox.showinfo(core.APP, "The clipboard doesn't hold a token - "
                                        "copy it in Canvas first.", parent=win)

        ctk.CTkButton(tok_row, text="Paste from clipboard", width=130,
                      fg_color=NAVY_MID, hover_color=BLUE,
                      command=paste).pack(side="left", padx=8)
        info = ctk.CTkLabel(win, text="(If a token is already saved on this PC "
                                      "for your school, leave it blank.)",
                            font=("Segoe UI", 11), text_color="#4b5563")
        info.pack(padx=16, pady=(6, 0))

        def go():
            url = url_e.get().strip()
            tok = tok_e.get().strip() or None
            win.destroy()

            def job():
                d = core.setup_course(url=url, token=tok)
                if d:
                    cid = os.path.basename(d)
                    self.root.after(0, self.refresh_courses, cid)

            self.run_verb("Connect course", job)

        ctk.CTkButton(win, text="Connect", fg_color=GOLD, text_color=NAVY,
                      font=("Segoe UI", 13, "bold"),
                      hover_color=GOLD_DARK, command=go).pack(pady=16)


def run_gui(smoke_ms=0):
    import customtkinter as ctk
    app = App(ctk)
    if smoke_ms:
        app.root.after(smoke_ms, app.root.destroy)
    app.root.mainloop()
    return 0


class _NullIO:
    def write(self, *_a):
        pass

    def flush(self):
        pass


def _report_startup_failure(exc):
    """A windowed exe that raises before mainloop() shows the user NOTHING:
    no window, no error, no console (stderr is a null sink by then). One
    damaged config.json used to be enough. Always leave a visible trace."""
    detail = "%s: %s" % (type(exc).__name__, exc)
    try:
        core.log_event("startup_error", error=detail[:300])
    except Exception:
        pass
    try:
        with open(os.path.join(core.WORKROOT, "startup-error.txt"), "w",
                  encoding="utf-8") as f:
            import traceback
            f.write(detail + "\n\n")
            traceback.print_exc(file=f)
    except Exception:
        pass
    try:
        mbox.showerror(
            core.APP,
            "CourseForge PDF Fixer could not start.\n\n%s\n\n"
            "This is usually a damaged course folder. Rename or delete the "
            "folder it names above (or the whole folder below) and reconnect "
            "the course:\n\n    %s\n\n"
            "If it keeps happening, send this message and "
            "startup-error.txt to your instructional designer."
            % (detail, core.WORKROOT))
    except Exception:
        pass


def main():
    multiprocessing.freeze_support()
    # double-clicked windowed exe: stdout/stderr are None; give every stray
    # print a safe sink so nothing can ever crash on write
    if sys.stdout is None:
        sys.stdout = _NullIO()
    if sys.stderr is None:
        sys.stderr = _NullIO()
    core.wire_bundled_tools()
    args = sys.argv[1:]
    if args and args[0] == "--smoke":
        return run_gui(smoke_ms=1500)
    if args:                       # CLI verbs keep working (scripting/tests)
        return core.main()
    try:
        return run_gui()
    except Exception as e:
        _report_startup_failure(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
