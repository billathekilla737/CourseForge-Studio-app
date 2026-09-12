"""
cf_theme.py - the ONE place the CourseForge desktop apps get their look.

Both windows (CourseForge PDF Fixer and CourseForge Assistant) import their
palette, fonts and shared widget builders from here, so an employee sees the
same gold bar, the same Georgia title, the same navy buttons and the same
white Consolas log pane whichever app they open - and a brand change is one
edit.

The palette mirrors skill/brand.json (the same colours every remediated
course page uses). When brand.json can be found it wins; the literals below
are the fallback so a frozen exe with no brand.json beside it still looks
right. Override the file location with the CF_BRAND environment variable.

Stdlib only at import time; customtkinter is passed in by the caller (the
apps import it lazily so a CLI run never pays for tkinter).
"""
import json
import os
import sys

# ---------------------------------------------------------------- palette
NAVY = "#061E3F"          # headings, primary fills
NAVY_MID = "#0E2C54"      # button fill
BLUE = "#236192"          # hover / info / quiet buttons
GOLD = "#E9A821"          # accents: top bar, progress, the one CTA
GOLD_DARK = "#c78f1b"
RED = "#C11F31"           # brand red: destructive only
RED_DARK = "#8f1826"
PAGE_BG = "#f5f6f8"       # light ground, like the course pages
CARD_FILL = "#ffffff"
BODY_TEXT = "#2c3a4d"
MUTED_TEXT = "#4b5563"
HAIRLINE = "#d7dce3"
GOAL_FILL = "#eef4fa"     # pale blue: informational panels
ALERT_FILL = "#fbe9eb"    # pale red: warnings

# ------------------------------------------------------------------ fonts
FONT_TITLE = ("Georgia", 24, "bold")
FONT_SUBTITLE = ("Segoe UI", 12)
FONT_UI = ("Segoe UI", 13)
FONT_UI_BOLD = ("Segoe UI", 13, "bold")
FONT_SMALL = ("Segoe UI", 11)
FONT_SMALL_BOLD = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 12)
FONT_MONO_SMALL = ("Consolas", 11)

_BRAND_KEYS = {
    "navy": "NAVY", "navy_mid": "NAVY_MID", "blue": "BLUE", "gold": "GOLD",
    "gold_dark": "GOLD_DARK", "red": "RED", "red_dark": "RED_DARK",
    "page_bg": "PAGE_BG", "card_fill": "CARD_FILL", "body_text": "BODY_TEXT",
    "muted_text": "MUTED_TEXT", "hairline": "HAIRLINE", "goal_fill": "GOAL_FILL",
    "alert_fill": "ALERT_FILL",
}


def app_base_dir():
    """Folder the running program lives in: next to the exe when frozen,
    next to this source file otherwise. Bundled tools sit beside it."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def brand_candidates():
    base = app_base_dir()
    env = os.environ.get("CF_BRAND")
    cands = [env] if env else []
    cands += [
        os.path.join(base, "brand.json"),                   # frozen: beside exe
        os.path.join(base, "skill", "brand.json"),          # frozen: bundled skill
        os.path.join(os.path.dirname(base), "brand.json"),  # dev: skill/brand.json
    ]
    return [c for c in cands if c]


def load_brand():
    """Apply brand.json colours over the module defaults. Never raises: a
    missing or damaged brand file must not stop an app from opening."""
    for path in brand_candidates():
        try:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            colors = data.get("colors") or {}
            g = globals()
            for key, name in _BRAND_KEYS.items():
                v = colors.get(key)
                if isinstance(v, str) and v.startswith("#") and len(v) in (4, 7):
                    g[name] = v
            return path
        except Exception:
            continue
    return None


load_brand()


def find_icon(name="cf-icon.ico"):
    """The .ico beside the exe (the installer drops it there) or beside the
    installer sources when running from the repo."""
    base = app_base_dir()
    for cand in (os.path.join(base, name),
                 os.path.join(os.path.dirname(os.path.dirname(base)),
                              "installer", name)):
        if os.path.isfile(cand):
            return cand
    return None


# ---------------------------------------------------------- widget builders
# Every builder takes the customtkinter module as its first argument so this
# file stays import-cheap and testable without a display.

def apply_window_chrome(ctk, root, title, subtitle=None, icon_name="cf-icon.ico",
                        size="880x640", minsize=(760, 520)):
    """Light mode, page background, gold top bar, Georgia title (+ optional
    muted subtitle on the same row). Returns the title label."""
    ctk.set_appearance_mode("light")
    root.title(title)
    root.geometry(size)
    root.minsize(*minsize)
    ico = find_icon(icon_name)
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass
    root.configure(fg_color=PAGE_BG)
    ctk.CTkFrame(root, height=6, corner_radius=0, fg_color=GOLD).pack(fill="x")
    row = ctk.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", padx=16, pady=(10, 0))
    label = ctk.CTkLabel(row, text=title.split("  v")[0], font=FONT_TITLE,
                         text_color=NAVY, anchor="w")
    label.pack(side="left")
    if subtitle:
        ctk.CTkLabel(row, text=subtitle, font=FONT_SUBTITLE,
                     text_color=MUTED_TEXT, anchor="w").pack(
            side="left", padx=(12, 0), pady=(8, 0))
    return label


def primary_button(ctk, parent, text, command, **kw):
    """Navy action button - the workhorse."""
    opts = dict(text=text, command=command, height=44, font=FONT_UI_BOLD,
                fg_color=NAVY_MID, hover_color=BLUE, text_color="white")
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


def gold_button(ctk, parent, text, command, **kw):
    """Gold call-to-action: one per screen (Connect, Send, Allow)."""
    opts = dict(text=text, command=command, font=FONT_UI_BOLD,
                fg_color=GOLD, hover_color=GOLD_DARK, text_color=NAVY)
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


def danger_button(ctk, parent, text, command, **kw):
    """Brand red: destructive or refusing actions only (Roll back, Deny, Stop)."""
    opts = dict(text=text, command=command, height=44, font=FONT_UI_BOLD,
                fg_color=RED, hover_color=RED_DARK, text_color="white")
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


def quiet_button(ctk, parent, text, command, **kw):
    """Blue secondary button (Open folder, New conversation)."""
    opts = dict(text=text, command=command, font=FONT_UI,
                fg_color=BLUE, hover_color=NAVY, text_color="white")
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


def chip_button(ctk, parent, text, command, **kw):
    """Outlined navy chip: a suggestion, not an action. Clicking fills
    something in rather than doing something."""
    opts = dict(text=text, command=command, height=30, font=FONT_SMALL_BOLD,
                fg_color=CARD_FILL, hover_color=GOAL_FILL, text_color=NAVY,
                border_width=1, border_color=NAVY_MID, corner_radius=15)
    opts.update(kw)
    return ctk.CTkButton(parent, **opts)


def option_menu(ctk, parent, variable, values, width=460, **kw):
    opts = dict(variable=variable, values=values, width=width,
                fg_color=NAVY_MID, button_color=NAVY, button_hover_color=BLUE,
                text_color="white", font=FONT_UI)
    opts.update(kw)
    return ctk.CTkOptionMenu(parent, **opts)


def log_textbox(ctk, parent, **kw):
    """White, hairline-bordered, Consolas, word-wrapped read pane."""
    opts = dict(font=FONT_MONO, wrap="word", fg_color=CARD_FILL,
                text_color=BODY_TEXT, border_width=1, border_color=HAIRLINE)
    opts.update(kw)
    return ctk.CTkTextbox(parent, **opts)


def progress_bar(ctk, parent, **kw):
    opts = dict(mode="indeterminate", progress_color=GOLD)
    opts.update(kw)
    bar = ctk.CTkProgressBar(parent, **opts)
    bar.set(0)
    return bar


def footer_note(ctk, parent, text):
    return ctk.CTkLabel(parent, text=text, font=FONT_SMALL, text_color=MUTED_TEXT,
                        anchor="w", justify="left")
