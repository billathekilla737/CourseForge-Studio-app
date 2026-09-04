"""
courseforge_pdf.py - the friendly face of the PDF fastlane.

This is the entry point that gets FROZEN (PyInstaller) into
"CourseForge PDF Fixer.exe" for non-technical users: a numbered-menu
wizard that wraps the pdf_fastlane engine plus a stdlib-only Canvas
client (list / download / 3-step upload), so the installed app needs
NO Python, NO pip, and NO PowerShell.

Design rules for this file:
  - stdlib + the engine only (urllib, not requests - nothing new to freeze)
  - every message written for an instructor, not an operator
  - destructive step (upload) always shows a summary and asks first
  - the engine is the single source of truth: this file never re-implements
    fixing/verify logic, it only drives run_batch/apply_alt/run_validate

Power users / automation: the same exe accepts subcommands
  setup | check | upload | prove | selftest    (see --help)
"""
import io
import json
import multiprocessing
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import pdf_fastlane as engine

APP = "CourseForge PDF Fixer"
VERSION = "1.1.8"


def _documents_dir():
    """The Documents folder the user actually SEES in File Explorer. With
    OneDrive Known Folder Move, ~\\Documents is a near-empty legacy folder
    while the real one is under OneDrive - the shell folder honors that."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 \
                and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


WORKROOT = os.path.join(_documents_dir(), "CourseForge-PDF")

# Credentials live in LOCALAPPDATA, never in Documents. Documents is routinely
# redirected into OneDrive, and an encrypted-but-synced credential blob is a
# credential that left the machine. LOCALAPPDATA is never roamed or synced.
CREDROOT = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    "CourseForge-PDF")


def workroot_is_synced():
    """True when the work folder sits inside a cloud-synced Documents. Not an
    error - just something a deployer should know, because every fixed PDF
    gets uploaded twice and sync can hold a file mid-write."""
    low = WORKROOT.lower()
    if "onedrive" in low or "dropbox" in low or "box sync" in low:
        return True
    return bool(os.environ.get("OneDrive")
                and low.startswith(os.environ["OneDrive"].lower()))




def log_event(action, **fields):
    """Append-only JSONL activity log: development data + accountability.
    One line per action with timestamp, version, Windows user, and counts.
    Never logs tokens or credentials. Failure to log never blocks work."""
    try:
        os.makedirs(WORKROOT, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "v": VERSION,
               "user": os.environ.get("USERNAME", "?"), "action": action}
        rec.update(fields)
        with open(os.path.join(WORKROOT, "activity-log.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec) + chr(10))
    except Exception:
        pass


# ------------------------------------------------------------ bundled tools

def wire_bundled_tools():
    """When frozen, tesseract/veraPDF/JRE ship NEXT TO the exe - point the
    engine's env-var discovery at them so nothing else needs installing."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    tess = os.path.join(base, "tesseract", "tesseract.exe")
    if os.path.isfile(tess) and not os.environ.get("TESSERACT_EXE"):
        os.environ["TESSERACT_EXE"] = tess
        os.environ.setdefault("TESSDATA_PREFIX",
                              os.path.join(base, "tesseract", "tessdata"))
    vp = os.path.join(base, "verapdf", "verapdf.bat")
    if os.path.isfile(vp) and not os.environ.get("VERAPDF_BAT"):
        os.environ["VERAPDF_BAT"] = vp
    for jdir in ("jre", "java"):
        j = os.path.join(base, jdir, "bin", "java.exe")
        if os.path.isfile(j) and not os.environ.get("JAVACMD"):
            os.environ["JAVACMD"] = j
            break


def _dpapi(data, protect):
    """Windows DPAPI: encrypt/decrypt bound to the logged-in user account.
    The Canvas token is stored as a DPAPI blob - unreadable to other
    accounts on the machine and to anyone who copies the file elsewhere."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    k32 = ctypes.windll.kernel32
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    outb = BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb)):
        raise OSError("Windows data protection (DPAPI) call failed")
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        k32.LocalFree(ctypes.cast(outb.pbData, ctypes.c_void_p))


def _token_filename(base_url):
    host = urllib.parse.urlparse(base_url).netloc.replace(":", "_")
    return "token-%s.bin" % host


def _host_token_path(base_url):
    return os.path.join(CREDROOT, _token_filename(base_url))


def save_host_token(base_url, token):
    os.makedirs(CREDROOT, exist_ok=True)
    p = _host_token_path(base_url)
    tmp = p + ".part"
    with open(tmp, "wb") as f:
        f.write(_dpapi(token.encode("utf-8"), protect=True))
    os.replace(tmp, p)


def load_host_token(base_url):
    """Read the DPAPI blob, migrating one from the pre-1.1.8 location
    (Documents, which OneDrive may be syncing) into LOCALAPPDATA."""
    for p, legacy in ((_host_token_path(base_url), False),
                      (os.path.join(WORKROOT, _token_filename(base_url)), True)):
        if not os.path.isfile(p):
            continue
        try:
            tok = _dpapi(open(p, "rb").read(),
                         protect=False).decode("utf-8").strip()
        except Exception:
            continue         # different user account / corrupted blob
        if not tok:
            continue
        if legacy:
            try:
                save_host_token(base_url, tok)
                os.remove(p)
            except Exception:
                pass
        return tok
    return None


def read_clipboard():
    """Windows clipboard text via ctypes - no extra dependency. Console
    paste is a minefield for non-technical users (Ctrl+V dead on hidden
    prompts, QuickEdit right-click copying instead of pasting), so the
    token flow reads the clipboard DIRECTLY: copy in Canvas, press Enter."""
    import ctypes
    CF_UNICODETEXT = 13
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    # restype declarations are LOAD-BEARING on 64-bit: without them ctypes
    # truncates HANDLE/pointer returns to 32-bit ints and reads garbage
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _plausible_token(s):
    return 20 <= len(s) <= 300 and not re.search(r"\s", s) and "://" not in s


def ask_token():
    """Clipboard-first token entry. Three tries, always explains what it saw."""
    print()
    print("Now your Canvas access token. In Canvas: Account > Settings >")
    print("scroll to Approved Integrations > + New Access Token.")
    print()
    print("COPY the token in Canvas (Ctrl+C), come back to this window,")
    print("and just press ENTER - it will be read from your clipboard.")
    print("(You can also type or paste it at the prompt if you prefer.)")
    for attempt in range(3):
        raw = input("> ").strip()
        if raw:
            if _plausible_token(raw):
                return raw
            print("That doesn't look like a token (it has spaces or looks like a")
            print("web address). Copy the token itself and press ENTER.")
            continue
        clip = (read_clipboard() or "").strip()
        if _plausible_token(clip):
            print("Got it from the clipboard (%d characters)." % len(clip))
            return clip
        if clip:
            shown = clip[:47] + "..." if len(clip) > 50 else clip
            print("Your clipboard contains: %s" % shown)
            print("That's not a token. In Canvas, copy the token itself (the long")
            print("code shown when you create it), then press ENTER here again.")
        else:
            print("The clipboard is empty. Copy the token in Canvas (Ctrl+C),")
            print("then press ENTER here.")
    return None


# ------------------------------------------------------------ canvas client

RETRY_STATUS = (403, 429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5


def _is_throttle(err):
    """Canvas signals its rate limiter with 403 AND a body/header saying so -
    the SAME status it uses for 'you may not touch this course'. Telling them
    apart matters: one is worth retrying, the other never is."""
    if err.code == 429:
        return True
    if err.code != 403:
        return False
    try:
        if err.headers.get("X-Rate-Limit-Remaining") is not None:
            return True
    except Exception:
        pass
    try:
        body = err.read(400).decode("utf-8", "replace").lower()
    except Exception:
        return False
    return "rate limit" in body or "throttle" in body


class Canvas:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.token = token

    def _req(self, method, url, data=None, headers=None, timeout=120):
        """One request with backoff on throttling and transient 5xx. Canvas
        throttles hard on a 200-file course and a bare urlopen turned that
        into a confusing mid-run failure."""
        h = {"Authorization": "Bearer " + self.token}
        h.update(headers or {})
        last = None
        for attempt in range(MAX_ATTEMPTS):
            r = urllib.request.Request(url, data=data, headers=h, method=method)
            try:
                with urllib.request.urlopen(r, timeout=timeout) as resp:
                    return resp.read(), resp.headers.get("Link", "")
            except urllib.error.HTTPError as e:
                last = e
                retryable = (e.code in RETRY_STATUS
                             and (e.code != 403 or _is_throttle(e)))
                if not retryable or attempt == MAX_ATTEMPTS - 1:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if attempt == MAX_ATTEMPTS - 1:
                    raise
            time.sleep(min(20.0, (2 ** attempt) + random.random()))
        raise last                      # unreachable, kept for clarity

    def get_json(self, path_or_url):
        url = path_or_url if path_or_url.startswith("http") \
            else "%s/api/v1%s" % (self.base, path_or_url)
        body, link = self._req("GET", url)
        return json.loads(body.decode("utf-8")), link

    def paged(self, path):
        out, url = [], "%s/api/v1%s%sper_page=100" % (
            self.base, path, "&" if "?" in path else "?")
        seen = set()
        while url:
            if url in seen:             # a next link that does not advance
                break                   # would otherwise spin forever
            seen.add(url)
            items, link = self.get_json(url)
            out.extend(items)
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part[part.find("<") + 1:part.find(">")]
            if len(seen) > 500:          # 50k items: something is wrong
                print("  (stopping pagination after %d pages)" % len(seen))
                break
        return out

    def download(self, url, dest, expect_size=None):
        """Download to a .part file and rename only once it is complete.

        Writing straight to original.pdf meant a dropped connection left a
        TRUNCATED file that every later run treated as a valid backup (the
        code only checked that the path existed) - and ROLL BACK would then
        push that truncated PDF into Canvas over the good one. This is the
        one path in the tool that could destroy instructor content."""
        tmp = dest + ".part"
        got = 0
        # file urls are pre-signed; no auth header needed (or wanted)
        with urllib.request.urlopen(url, timeout=600) as resp:
            declared = resp.headers.get("Content-Length")
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
        want = None
        for cand in (declared, expect_size):
            try:
                if cand is not None and int(cand) > 0:
                    want = int(cand)
                    break
            except (TypeError, ValueError):
                pass
        if want is not None and got != want:
            os.remove(tmp)
            raise IOError("download incomplete: got %d of %d bytes"
                          % (got, want))
        if got == 0:
            os.remove(tmp)
            raise IOError("download produced an empty file")
        os.replace(tmp, dest)

    def upload_over(self, course_id, meta, local_path):
        """Canvas 3-step upload: slot -> multipart -> done. Same name and
        folder with on_duplicate=overwrite so course links keep working.

        The body is assembled on disk-backed chunks rather than read whole
        into memory twice: a scanned course handout can be 100MB+ and the old
        `buf.write(open(path).read())` held two copies of it at once."""
        size = os.path.getsize(local_path)
        slot_body = urllib.parse.urlencode({
            "name": meta["display_name"], "size": size,
            "content_type": "application/pdf",
            "parent_folder_id": meta["folder_id"],
            "on_duplicate": "overwrite"}).encode("ascii")
        slot, _ = self.get_json_post(
            "/courses/%s/files" % course_id, slot_body)
        boundary = uuid.uuid4().hex
        pre = io.BytesIO()
        for k, v in slot["upload_params"].items():
            pre.write(("--%s\r\nContent-Disposition: form-data; "
                       "name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v))
                      .encode("utf-8"))
        pre.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                   "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
                   % (boundary, meta["display_name"])).encode("utf-8"))
        post = ("\r\n--%s--\r\n" % boundary).encode("ascii")
        head = pre.getvalue()
        total = len(head) + size + len(post)

        def body_stream():
            yield head
            with open(local_path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    yield chunk
            yield post

        last = None
        for attempt in range(3):
            req = urllib.request.Request(
                slot["upload_url"], data=_ChunkedReader(body_stream()),
                method="POST",
                headers={"Content-Type":
                         "multipart/form-data; boundary=" + boundary,
                         "Content-Length": str(total)})
            try:
                with urllib.request.urlopen(req, timeout=900) as resp:
                    if resp.status not in (200, 201, 301, 302, 303):
                        raise RuntimeError("upload failed (%d)" % resp.status)
                    return
            except urllib.error.HTTPError as e:
                # the upload slot is single-use; a fresh one is needed, so
                # only transient transport errors are worth another go here
                raise RuntimeError("upload rejected (HTTP %d)" % e.code) from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise last

    def get_json_post(self, path, data):
        url = "%s/api/v1%s" % (self.base, path)
        body, link = self._req("POST", url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(body.decode("utf-8")), link


class _ChunkedReader:
    """Minimal read()-only file object over a generator of byte chunks, so
    urllib streams the upload body instead of materialising it."""

    def __init__(self, gen):
        self.gen = gen
        self.buf = b""
        self.done = False

    def read(self, n=-1):
        if n is None or n < 0:
            parts = [self.buf]
            self.buf = b""
            parts.extend(self.gen)
            self.done = True
            return b"".join(parts)
        while len(self.buf) < n and not self.done:
            try:
                self.buf += next(self.gen)
            except StopIteration:
                self.done = True
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


# ------------------------------------------------------------ config store

def course_dirs():
    """Every connected course. ONE unreadable config.json must never take the
    app down with it: this runs during window construction, and in the frozen
    windowed exe an exception here meant double-click -> no window, no error,
    nothing, forever (a truncated config from an interrupted write or a
    OneDrive sync conflict is enough). Bad entries are skipped and named."""
    if not os.path.isdir(WORKROOT):
        return []
    out = []
    for d in sorted(os.listdir(WORKROOT)):
        cfg = os.path.join(WORKROOT, d, "config.json")
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, encoding="utf-8-sig") as f:
                rec = json.load(f)
            if not (rec.get("course_id") and rec.get("base_url")):
                raise ValueError("missing course_id/base_url")
            rec.setdefault("course_name", "Course %s" % rec["course_id"])
            out.append(rec)
        except Exception as e:
            print("Skipping an unreadable course folder (%s): %s" % (d, e))
            print("  Reconnect that course, or delete %s" % os.path.dirname(cfg))
    return out


def save_course(base_url, course_id, name):
    d = os.path.join(WORKROOT, str(course_id))
    os.makedirs(d, exist_ok=True)
    json.dump({"base_url": base_url, "course_id": str(course_id),
               "course_name": name},
              open(os.path.join(d, "config.json"), "w", encoding="utf-8"))
    return d


def load_token(course_dir):
    """Encrypted per-host token first; migrate any legacy plaintext
    token.txt into the DPAPI blob and delete it."""
    cfg = json.load(open(os.path.join(course_dir, "config.json"),
                         encoding="utf-8-sig"))
    tok = load_host_token(cfg["base_url"])
    if tok:
        return tok
    p = os.path.join(course_dir, "token.txt")
    if os.path.isfile(p):
        tok = open(p, encoding="utf-8-sig").read().strip()
        if tok:
            save_host_token(cfg["base_url"], tok)
            try:
                os.remove(p)
            except OSError:
                pass
            return tok
    return None


def setup_course(interactive=True, url=None, token=None):
    print()
    print("Connect a Canvas course")
    print("-" * 40)
    if not url:
        url = input("Paste your course web address (from the browser):\n> ").strip()
    m = re.search(r"(https?://[^/]+)/courses/(\d+)", url)
    if not m:
        print("That does not look like a Canvas course address. It should look like")
        print("   https://YOURSCHOOL.instructure.com/courses/123456")
        return None
    base, cid = m.group(1), m.group(2)
    source = "given" if token else None
    if not token:
        token = load_host_token(base)
        if token:
            source = "saved"
            print()
            print("Using your saved Canvas token (stored securely on this PC).")
    if not token:
        token = ask_token()
        source = "asked"
    if not token:
        print("No token received - cancelled.")
        return None
    for _attempt in (1, 2):
        try:
            canvas = Canvas(base, token)
            course, _ = canvas.get_json("/courses/%s" % cid)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401 and source == "saved":
                print()
                print("Your saved token was rejected - it has probably expired or")
                print("been deleted in Canvas. Let's store a fresh one (one time).")
                token = ask_token()
                source = "asked"
                if not token:
                    print("No token received - cancelled.")
                    return None
                continue
            if e.code == 401:
                print()
                print("Canvas rejected the token (401 Unauthorized). Usually this means")
                print("the token was copied incompletely or has expired - generate a")
                print("fresh one in Canvas: Account > Settings > + New Access Token.")
            elif e.code in (403, 404):
                print()
                print("The token works for Canvas but not for THIS course (%d)." % e.code)
                print("Check that the course address is one of your own courses.")
            else:
                print("Could not connect: HTTP %d" % e.code)
            return None
        except Exception as e:
            print("Could not connect: %s" % e)
            print("Check the address (and your internet connection) and try again.")
            return None
    save_host_token(base, token)     # encrypted (DPAPI), one per Canvas site
    d = save_course(base, cid, course.get("name", "Course %s" % cid))
    legacy = os.path.join(d, "token.txt")
    if os.path.isfile(legacy):
        try:
            os.remove(legacy)        # plaintext copy superseded by the blob
        except OSError:
            pass
    log_event("connect", course_id=cid, course=course.get("name"))
    print()
    print("Connected: %s" % course.get("name"))
    if source == "asked":
        print("Your token is saved (encrypted) - you will not be asked again")
        print("on this PC, even for other courses.")
    return d


def pick_course():
    known = course_dirs()
    if not known:
        return setup_course()
    if len(known) == 1:
        return os.path.join(WORKROOT, known[0]["course_id"])
    print()
    for i, c in enumerate(known, 1):
        print("  [%d] %s" % (i, c["course_name"]))
    print("  [%d] connect a different course" % (len(known) + 1))
    try:
        n = int(input("> ").strip())
    except ValueError:
        return None
    if 1 <= n <= len(known):
        return os.path.join(WORKROOT, known[n - 1]["course_id"])
    return setup_course()




# --------------------------------------------- Claude-powered descriptions

def find_claude():
    """Locate an installed Claude Code CLI (signs in with the user's own
    claude.ai account - Max/Pro subscription, NO API key). Checked on PATH
    plus the native and npm install locations."""
    import shutil as _sh
    hit = _sh.which("claude") or _sh.which("claude.exe") or _sh.which("claude.cmd")
    if hit:
        return hit
    home = os.path.expanduser("~")
    for guess in (os.path.join(home, ".local", "bin", "claude.exe"),
                  os.path.join(home, "AppData", "Roaming", "npm", "claude.cmd"),
                  os.path.join(home, "AppData", "Local", "Programs",
                               "claude-code", "claude.exe")):
        if os.path.isfile(guess):
            return guess
    return None


def _extract_json(text):
    """First parseable JSON object anywhere in the text. Greedy regex broke
    live on chatter AFTER the JSON ("Extra data"); raw_decode from each
    opening brace is immune to prose on either side."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _end = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)
    return None


def _claude_logged_in(claude):
    """Cheap headless probe: distinguishes signed-in from 'Not logged in'."""
    import subprocess
    try:
        cp = subprocess.run([claude, "-p", "Reply with exactly: OK"],
                            capture_output=True, timeout=180,
                            creationflags=engine.SUBPROC_FLAGS)
        txt = (cp.stdout + cp.stderr).decode("utf-8", "replace")
        if "Not logged in" in txt or "/login" in txt:
            return False
        return cp.returncode == 0
    except Exception:
        return False


def _claude_sign_in(claude):
    """Launch Claude Code interactively so the user can sign in (it opens
    the browser login flow automatically when logged out). Returns True
    when a re-probe shows the sign-in stuck."""
    import subprocess
    print()
    print("You are not signed in to Claude on this PC yet. I can open the")
    print("Claude sign-in for you now - a browser window will appear; sign in")
    print("with your Claude (Max) account, then close the Claude window that")
    print("opened here (type /exit or press Ctrl+C) to come back.")
    if input("Press Enter to open Claude sign-in (or type anything to cancel): ").strip():
        return False
    try:
        subprocess.run([claude], timeout=600)
    except Exception:
        pass
    print("Checking sign-in...")
    return _claude_logged_in(claude)


def _shrink_for_reading(src, small_dir):
    """Alt text does not need 2550x3300 scans: a <=1750px copy reads faster
    AND costs fewer vision tokens. Falls back to the original on any error."""
    try:
        os.makedirs(small_dir, exist_ok=True)
        dst = os.path.join(small_dir, os.path.basename(src))
        if os.path.isfile(dst):
            return dst
        pix = engine.fitz.Pixmap(src)
        if max(pix.width, pix.height) <= 1750:
            return src
        while max(pix.width, pix.height) > 1750:
            pix.shrink(1)
        pix.save(dst)
        return dst
    except Exception:
        return src


def _claude_describe_batch(claude, batch, cwd):
    """One headless Claude Code call: read the listed images, return strict
    JSON {id: alt}. batch = [(hash, png_path, context)].
    cwd MUST be the course workdir: headless Claude Code may only Read
    files inside its working directory - launched from anywhere else,
    every image read is permission-denied (seen live on the stats course).
    Model pinned to sonnet: excellent at image description and far cheaper
    against the user's subscription usage than the account default."""
    import subprocess
    lines = ["You are writing screen-reader alt text for images extracted "
             "from college course PDFs.",
             "STEP 1: Read ALL of the image files listed below, issuing every "
             "Read call IN PARALLEL in your very first response - do not read "
             "them one at a time.",
             "STEP 2: for each image write ONE concise description (110 "
             "characters or fewer) of what it shows. If an image is purely "
             "decorative (logo watermark, border, scanner stamp), use an "
             "empty string.",
             "Then respond with ONLY a JSON object mapping each id to its "
             "description string. No markdown, no commentary.", ""]
    for h, png, ctx in batch:
        lines.append("id: %s" % h)
        lines.append("file: %s" % png)
        if ctx:
            lines.append("document: %s" % ctx)
        lines.append("")
    # prompt goes via STDIN, never argv: cmd-shim launchers (npm's
    # claude.cmd) truncate multiline arguments at the first newline
    cp = subprocess.run([claude, "-p", "--model", "sonnet"],
                        input=chr(10).join(lines).encode("utf-8"),
                        capture_output=True, timeout=900, cwd=cwd,
                        creationflags=engine.SUBPROC_FLAGS)
    out = cp.stdout.decode("utf-8", "replace")
    data = _extract_json(out)
    if data is None:
        raise RuntimeError("Claude returned no JSON (exit %d): %s"
                           % (cp.returncode, (out or cp.stderr.decode(
                               "utf-8", "replace"))[:200]))
    clean = {}
    for h, _png, _ctx in batch:
        v = data.get(h)
        if isinstance(v, str) and len(v.strip()) <= 140:
            clean[h] = v.strip()
    return clean


def _alt_summary(workdir):
    p = os.path.join(workdir, "alt-summary.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _describe_context(entry):
    """A one-line hint for the describer: which document, and whether the png
    is the figure itself or the whole page it sits on. Telling the model the
    picture is a full page (rather than pretending it is the figure) is the
    difference between a useful description and a confident wrong one."""
    use = (entry.get("used_by") or [{}])[0]
    doc = use.get("file", "")
    kind = entry.get("kind")
    if kind == "page":
        return ("%s - this is the WHOLE PAGE the figure sits on; describe the "
                "main graphic on it" % doc) if doc else \
               "whole page; describe the main graphic on it"
    return doc


def do_describe(course_dir, assume_yes=False):
    """Write REAL image descriptions using the user's own Claude sign-in
    (Claude Code CLI, covered by a Max/Pro subscription - no API key)."""
    workdir = os.path.join(course_dir, "pdf-fastlane")
    if not os.path.isdir(workdir):
        print()
        print("Run the back up & fix step first - there is nothing to describe yet.")
        return
    claude = find_claude()
    if not claude:
        print()
        print("This option uses Claude Code, signed in with your own Claude")
        print("account (covered by Claude Max/Pro - no API key needed).")
        print("It is not installed on this PC yet. One-time setup:")
        print("  1. In PowerShell run:  irm https://claude.ai/install.ps1 | iex")
        print("  2. Run:  claude   and sign in when the browser opens")
        print("  3. Come back here and pick this option again")
        return
    engine.collect_alt_todo(workdir, quiet=True)
    tp = os.path.join(workdir, "alt-todo.json")
    todo = json.load(open(tp, encoding="utf-8-sig")) if os.path.isfile(tp) else {}
    stuck = _alt_summary(workdir).get("no_picture_available", 0)
    if not todo:
        print()
        print("No images are waiting for descriptions - you're all set.")
        if stuck:
            print()
            print("(%d figure(s) still hold a placeholder because no picture "
                  "of them could be produced - those need a person; see "
                  "alt-summary.json.)" % stuck)
        return
    small_dir = os.path.join(workdir, "figures-small")
    items = [(h, _shrink_for_reading(e["png"], small_dir),
              _describe_context(e))
             for h, e in todo.items() if e.get("png")]
    if not _claude_logged_in(claude):
        if not _claude_sign_in(claude):
            print("Still not signed in - descriptions skipped, placeholders remain.")
            return
        print("Signed in - continuing.")
    print()
    print("%d unique image(s) to describe using your Claude account." % len(items))
    if not assume_yes:
        print("This uses your normal Claude subscription usage - typically a few")
        print("minutes. Press Enter to start (or type anything to cancel).")
        if input("> ").strip():
            print("Cancelled.")
            return
    alts = {}
    B = 10
    WORKERS = 4   # parallel Claude sessions: shorter wall time for roughly the
                  # same total usage (the per-image work is unchanged; each
                  # extra session adds only its own prompt overhead). Kept
                  # small deliberately to stay friendly with account limits.
    batches = [items[i:i + B] for i in range(0, len(items), B)]
    print("  running %d batches, %d at a time..." % (len(batches), WORKERS))
    from concurrent.futures import as_completed
    auth_dead = False
    done_n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_claude_describe_batch, claude, b, workdir): b
                for b in batches}
        for fut in as_completed(futs):
            done_n += 1
            try:
                alts.update(fut.result())
                print("  batch %d/%d done (%d descriptions so far)"
                      % (done_n, len(batches), len(alts)))
            except Exception as e:
                if "Not logged in" in str(e):
                    if not auth_dead:
                        print("  Claude signed out mid-run - affected images")
                        print("  keep placeholders; run this option again.")
                    auth_dead = True
                else:
                    print("  (batch %d/%d failed: %s - placeholders kept)"
                          % (done_n, len(batches), str(e)[:120]))
    if not alts:
        print("No descriptions were produced - placeholders remain in place.")
        return
    # MERGE, never clobber: alt.json is the record of every description ever
    # written for this course. Overwriting it lost earlier work whenever a run
    # was partial or the fix step was repeated.
    ap = os.path.join(workdir, "alt.json")
    merged = {}
    if os.path.isfile(ap):
        try:
            with open(ap, encoding="utf-8-sig") as f:
                prev = json.load(f)
            if isinstance(prev, dict):
                merged.update(prev)
        except Exception:
            print("  (existing alt.json was unreadable - starting a fresh one)")
    merged.update(alts)
    tmp = ap + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1, sort_keys=True)
    os.replace(tmp, ap)
    engine.apply_alt(workdir, ap)
    # the fixed PDFs were verified BEFORE alt was written into them; re-verify
    # the ones we just touched so nothing unchecked can reach Canvas
    bad = _verify_after_alt(workdir)
    log_event("describe", workdir=workdir, requested=len(items),
              described=len(alts), reverify_failed=len(bad), model="sonnet")
    print()
    print("Wrote %d description(s) into the fixed PDFs." % len(alts))
    if bad:
        print()
        print("%d file(s) did NOT pass the re-check after the descriptions "
              "were written and were rolled back to their pre-description "
              "state:" % len(bad))
        for name in bad[:10]:
            print("   %s" % name)
        print("Those keep placeholders; everything else is ready.")
    print("Descriptions are saved in %s - spot-check a few, then" % ap)
    print("use the upload option to send the updated PDFs to Canvas.")


def _verify_after_alt(workdir):
    """Re-verify every fixed.pdf against its original after alt was written.

    apply_alt mutates a file that verify already blessed, so without this the
    last thing to touch a PDF before upload was never checked. A file that
    fails is restored from the .prealt backup apply_alt leaves behind, so a
    bad alt write can never be what gets published."""
    failed = []
    for d in sorted(os.listdir(workdir)):
        sub = os.path.join(workdir, d)
        fixed = os.path.join(sub, "fixed.pdf")
        orig = os.path.join(sub, "original.pdf")
        backup = fixed + ".prealt"
        if not (os.path.isfile(fixed) and os.path.isfile(orig)
                and os.path.isfile(backup)):
            continue
        name = d
        try:
            with open(os.path.join(sub, "file.json"), encoding="utf-8-sig") as f:
                name = json.load(f)["display_name"]
        except Exception:
            pass
        try:
            v = engine.verify_pair(orig, fixed, allow_font_drift=True)
        except Exception as e:
            v = {"ok": False, "detail": [str(e)]}
        if v.get("ok"):
            try:
                os.remove(backup)
            except OSError:
                pass
            continue
        failed.append(name)
        try:
            os.replace(backup, fixed)
        except OSError:
            pass
    return failed


# ------------------------------------------------------------ the verbs

def _ctx(course_dir):
    """(cfg, canvas, workdir) or None with a friendly message."""
    cfg = json.load(open(os.path.join(course_dir, "config.json"),
                         encoding="utf-8-sig"))
    tok = load_token(course_dir)
    if not tok:
        print()
        print("No saved Canvas token on this PC yet - pick option 1 first.")
        return None
    return cfg, Canvas(cfg["base_url"], tok), os.path.join(course_dir, "pdf-fastlane")


def fetch_originals(cfg, canvas, workdir):
    """Download every course PDF that is not already backed up locally.
    Returns the number of PDFs with a usable local original.

    One bad file used to abort the whole download (ex.map re-raises the first
    exception), so a single locked or expired URL cost the entire run. Now
    each failure is reported and the rest carry on."""
    os.makedirs(workdir, exist_ok=True)
    files = [f for f in canvas.paged("/courses/%s/files" % cfg["course_id"])
             if f["display_name"].lower().endswith(".pdf")
             or f.get("content_type") == "application/pdf"]
    if not files:
        return 0
    todo = []
    for f in files:
        d = os.path.join(workdir, str(f["id"]))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "file.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": f["id"], "display_name": f["display_name"],
                       "folder_id": f["folder_id"], "size": f["size"]}, fh)
        orig = os.path.join(d, "original.pdf")
        stale = orig + ".part"
        if os.path.isfile(stale):
            try:
                os.remove(stale)     # leftover from an interrupted run
            except OSError:
                pass
        if os.path.isfile(orig):
            # a backup that does not match the size Canvas reports is not a
            # backup; re-fetch rather than trust it (roll back reads these)
            try:
                if f.get("size") and os.path.getsize(orig) != int(f["size"]):
                    print("  re-fetching %s (local copy is %d bytes, Canvas "
                          "says %s)" % (f["display_name"],
                                        os.path.getsize(orig), f["size"]))
                    os.remove(orig)
            except OSError:
                pass
        if not os.path.isfile(orig):
            todo.append((f["display_name"], f["url"], orig, f.get("size")))
    print("Found %d PDF(s); downloading %d (6 at a time)..."
          % (len(files), len(todo)))
    failures = []

    def grab(job):
        name, url, dest, size = job
        try:
            canvas.download(url, dest, expect_size=size)
        except Exception as e:
            failures.append((name, e))

    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(grab, todo))
    if failures:
        print()
        print("%d PDF(s) could not be downloaded (nothing else is affected):"
              % len(failures))
        for name, e in failures[:10]:
            print("   %s - %s" % (name, str(e)[:120]))
        if len(failures) > 10:
            print("   ... and %d more" % (len(failures) - 10))
        print("Run this step again to retry just those.")
    have = sum(1 for f in files
               if os.path.isfile(os.path.join(workdir, str(f["id"]),
                                              "original.pdf")))
    log_event("fetch", course_id=cfg["course_id"], found=len(files),
              downloaded=len(todo) - len(failures), failed=len(failures))
    return have


def do_backup(course_dir):
    ctx = _ctx(course_dir)
    if not ctx:
        return
    cfg, canvas, workdir = ctx
    print()
    print("Backing up every PDF from: %s" % cfg["course_name"])
    n = fetch_originals(cfg, canvas, workdir)
    if not n:
        print("No PDFs found - nothing to back up.")
        return
    log_event("backup", course=cfg["course_name"],
              course_id=cfg["course_id"], pdfs=n)
    print()
    print("Backup complete: %d original PDF(s) stored under" % n)
    print("   %s" % workdir)
    print("Nothing in Canvas was changed.")


def do_rollback(course_dir, assume_yes=False):
    """Undo: upload the ORIGINAL PDFs back over whatever is in Canvas now."""
    ctx = _ctx(course_dir)
    if not ctx:
        return
    cfg, canvas, workdir = ctx
    have, suspect = [], []
    if os.path.isdir(workdir):
        for d in sorted(os.listdir(workdir)):
            sub = os.path.join(workdir, d)
            op, mp = os.path.join(sub, "original.pdf"), os.path.join(sub, "file.json")
            if not (os.path.isfile(op) and os.path.isfile(mp)):
                continue
            try:
                with open(mp, encoding="utf-8-sig") as fh:
                    meta = json.load(fh)
            except Exception:
                continue
            # never restore a backup we cannot vouch for: pushing a truncated
            # "original" over a good Canvas file is unrecoverable
            try:
                if meta.get("size") and os.path.getsize(op) != int(meta["size"]):
                    suspect.append((meta.get("display_name", d),
                                    os.path.getsize(op), int(meta["size"])))
                    continue
            except (OSError, TypeError, ValueError):
                pass
            have.append((meta, op))
    if suspect:
        print()
        print("%d backed-up original(s) do not match the size Canvas recorded "
              "and will NOT be restored:" % len(suspect))
        for name, got, want in suspect[:10]:
            print("   %s (%d bytes locally, %d expected)" % (name, got, want))
        print("Run 'Back up only' to fetch clean copies of those first.")
    if not have:
        print()
        print("No backed-up originals found for this course on this PC.")
        print("(Backups are made by the backup or check & fix steps.)")
        return
    print()
    print("ROLL BACK %d PDF(s) in '%s' to the original versions" % (len(have), cfg["course_name"]))
    print("saved on this computer. This replaces what is in Canvas NOW with")
    print("those originals (fixed versions stay on this PC and can be re-uploaded).")
    if not assume_yes:
        if input("Type YES to roll back: ").strip().upper() != "YES":
            print("Cancelled - nothing was changed.")
            return
    okc = err = 0
    for meta, op in have:
        try:
            canvas.upload_over(cfg["course_id"], meta, op)
            okc += 1
            print("  restored %s" % meta["display_name"])
        except Exception as e:
            err += 1
            print("  FAILED   %s (%s) - run roll back again to retry" %
                  (meta["display_name"], e))
    log_event("rollback", course=cfg["course_name"], course_id=cfg["course_id"],
              restored=okc, failed=err,
              files=[m["display_name"] for m, _f in have][:200])
    print()
    print("Restored %d, failed %d." % (okc, err))


def do_check(course_dir):
    ctx = _ctx(course_dir)
    if not ctx:
        return
    cfg, canvas, workdir = ctx
    print()
    print("Looking for PDFs in: %s" % cfg["course_name"])
    if not fetch_originals(cfg, canvas, workdir):
        print("No PDFs found - nothing to do.")
        return
    print("Fixing (this is the fast part)...")
    t0 = time.perf_counter()
    engine.run_batch(workdir, max(1, (os.cpu_count() or 4) - 2))
    _fixed = _fallback = 0
    for _d in os.listdir(workdir):
        _rp = os.path.join(workdir, _d, "result.json")
        if os.path.isfile(_rp):
            try:
                with open(_rp, encoding="utf-8-sig") as _f:
                    _st = json.load(_f).get("status")
            except Exception:
                continue
            if _st in ("ok", "review"):
                _fixed += 1
            elif _st == "fallback":
                _fallback += 1
    engine.collect_alt_todo(workdir, quiet=True)
    alt = _alt_summary(workdir)
    log_event("check_fix", course=cfg["course_name"],
              course_id=cfg["course_id"], fixed=_fixed, fallback=_fallback,
              needs_alt=alt.get("needs_alt", 0),
              no_picture=alt.get("no_picture_available", 0),
              seconds=round(time.perf_counter() - t0, 1))
    print()
    print("Done in %.0f seconds. A copy of every original is kept in:" %
          (time.perf_counter() - t0))
    print("   %s" % workdir)
    qp = os.path.join(workdir, "queue.json")
    q = []
    if os.path.isfile(qp):
        try:
            with open(qp, encoding="utf-8-sig") as f:
                q = json.load(f)
        except Exception:
            q = []
    if q:
        print()
        print("%d file(s) need a person to look at them (nothing was broken -" % len(q))
        print("they were skipped, not damaged). Share this file with your designer:")
        print("   %s" % qp)
    needs = alt.get("needs_alt", 0)
    stuck = alt.get("no_picture_available", 0)
    if needs:
        print()
        print("%d image(s) currently hold a SAFE PLACEHOLDER description."
              % needs)
        print("Use the describe option for real descriptions, or send this")
        print("folder to your instructional designer: %s" % workdir)
    if stuck:
        # this is the honest counterpart to a green PDF/UA report: these
        # figures satisfy the standard's "has alt" rule with words that tell a
        # screen-reader user nothing, and no picture of them could be produced
        print()
        print("%d figure(s) CANNOT be described automatically - no picture of"
              % stuck)
        print("them could be produced. They will pass a compliance scan while")
        print("still being useless to a screen reader, so a person needs to")
        print("write those by hand. They are listed in:")
        print("   %s" % os.path.join(workdir, "alt-summary.json"))
    junk = alt.get("useless_existing_alt", 0)
    if junk:
        print()
        print("%d figure(s) already have alt text that is only a filename"
              % junk)
        print("(\"image0.jpeg\") or \"Picture 3\". That passes a compliance")
        print("scan but describes nothing. It was left exactly as the author")
        print("wrote it - replacing someone's own words is not this tool's")
        print("call - so those are worth a human pass. See alt-summary.json.")
    print()
    print("Nothing has been changed in Canvas yet. Use the upload option when ready.")


def do_upload(course_dir, assume_yes=False):
    ctx = _ctx(course_dir)
    if not ctx:
        return
    cfg, canvas, workdir = ctx
    ready, skipped = [], 0
    for d in sorted(os.listdir(workdir)) if os.path.isdir(workdir) else []:
        sub = os.path.join(workdir, d)
        rp, fx = os.path.join(sub, "result.json"), os.path.join(sub, "fixed.pdf")
        mp = os.path.join(sub, "file.json")
        if not (os.path.isfile(rp) and os.path.isfile(fx) and os.path.isfile(mp)):
            continue
        try:
            with open(rp, encoding="utf-8-sig") as f:
                r = json.load(f)
            with open(mp, encoding="utf-8-sig") as f:
                meta = json.load(f)
        except Exception as e:
            skipped += 1
            print("  skipping %s: unreadable bookkeeping (%s)" % (d, e))
            continue
        if r.get("status") in ("ok", "review"):
            ready.append((meta, fx))
    if not ready:
        print("Nothing is ready to upload - run the check/fix step first.")
        return
    # a .prealt left behind means the post-description re-check never ran to
    # completion; refuse rather than publish something unverified
    pending = [m["display_name"] for m, fx in ready
               if os.path.isfile(fx + ".prealt")]
    if pending:
        print()
        print("%d file(s) are mid-description and have not been re-checked:"
              % len(pending))
        for name in pending[:10]:
            print("   %s" % name)
        print("Run the describe step again (it finishes the check), then")
        print("upload. Nothing has been uploaded.")
        return
    print()
    print("Ready to upload %d fixed PDF(s) into: %s" % (len(ready), cfg["course_name"]))
    print("Files replace the originals IN PLACE - every course link keeps working,")
    print("and untouched copies stay on this computer.")
    stuck = _alt_summary(workdir).get("no_picture_available", 0)
    if stuck:
        print()
        print("NOTE: %d figure(s) still carry a placeholder description that "
              "no automated step could improve - they need a person. Uploading "
              "now publishes them as they are." % stuck)
    if not assume_yes:
        if input("Type YES to upload: ").strip().upper() != "YES":
            print("Cancelled - nothing was uploaded.")
            return
    okc = err = 0
    for meta, fx in ready:
        try:
            canvas.upload_over(cfg["course_id"], meta, fx)
            okc += 1
            print("  uploaded %s" % meta["display_name"])
        except Exception as e:
            err += 1
            print("  FAILED   %s (%s) - run upload again to retry" %
                  (meta["display_name"], e))
    log_event("upload", course=cfg["course_name"], course_id=cfg["course_id"],
              uploaded=okc, failed=err,
              files=[m["display_name"] for m, _f in ready][:200])
    print()
    print("Uploaded %d, failed %d." % (okc, err))


def do_prove(course_dir):
    workdir = os.path.join(course_dir, "pdf-fastlane")
    if not os.path.isdir(workdir) or not any(
            os.path.isfile(os.path.join(workdir, d, "fixed.pdf"))
            for d in os.listdir(workdir)
            if os.path.isdir(os.path.join(workdir, d))):
        print()
        print("There are no fixed PDFs to check yet - run the check & fix")
        print("step first, then come back to this option.")
        return 1
    rc = engine.run_validate(workdir)
    vp = os.path.join(workdir, "validation.json")
    try:
        _v = json.load(open(vp, encoding="utf-8-sig"))
        log_event("prove", workdir=workdir, files=_v.get("files"),
                  compliant=_v.get("compliant"),
                  noncompliant=_v.get("noncompliant"))
    except Exception:
        pass
    if os.path.isfile(vp):
        print()
        print("Full detail: %s" % vp)
    return rc


MENU = """
%s
%s
  [1] Connect a Canvas course (first time / switch course)
  [2] Back up & fix this course's PDFs (downloads originals + repairs locally)
  [3] Describe images with Claude      (uses YOUR Claude sign-in, no API key)
  [4] Upload the fixed PDFs to Canvas  (asks before changing anything)
  [5] Prove compliance (PDF/UA-1 report)
  [6] Back up only (download originals, change nothing)
  [7] ROLL BACK - restore the original PDFs to Canvas (undo an upload)
  [8] Exit
"""


def wizard():
    print(MENU % (APP, "=" * len(APP)))
    course = None
    actions = {"2": do_check, "3": do_describe, "4": do_upload,
               "5": do_prove, "6": do_backup, "7": do_rollback}
    while True:
        choice = input("Pick a number > ").strip()
        try:
            if choice == "1":
                course = setup_course()
            elif choice in actions:
                course = course or pick_course()
                if course:
                    actions[choice](course)
            elif choice == "8" or choice.lower() in ("q", "exit", "quit"):
                return 0
            else:
                print("Please pick 1-8.")
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as e:
            # one bad step must never close the window on a non-technical user
            log_event("error", step=choice, error=str(e)[:300])
            print()
            print("Something went wrong with that step: %s" % e)
            print("Nothing in Canvas was changed by an error like this.")
            print("You can try again, or share this message with your designer.")
        print(MENU % (APP, "=" * len(APP)))


def main():
    multiprocessing.freeze_support()      # REQUIRED frozen: batch uses a pool
    wire_bundled_tools()
    args = sys.argv[1:]
    if not args:
        try:
            return wizard()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        except Exception as e:
            # last-resort net: keep the window open so the error is readable
            print()
            print("Unexpected problem: %s" % e)
            try:
                input("Press Enter to close.")
            except Exception:
                pass
            return 1
    cmd = args[0].lower()
    if cmd == "selftest":
        return engine.selftest()
    if cmd == "setup":
        url = args[1] if len(args) > 1 else None
        return 0 if setup_course(url=url) else 1
    verbs = ("check", "upload", "prove", "backup", "rollback", "describe")
    course = pick_course() if cmd in verbs else None
    if course is None and cmd in verbs:
        return 1
    if cmd == "check":
        do_check(course); return 0
    if cmd == "upload":
        do_upload(course, assume_yes="--yes" in args); return 0
    if cmd == "backup":
        do_backup(course); return 0
    if cmd == "rollback":
        do_rollback(course, assume_yes="--yes" in args); return 0
    if cmd == "describe":
        do_describe(course); return 0
    if cmd == "prove":
        return do_prove(course)
    print("Usage: courseforge-pdf [setup <url> | check | upload [--yes] | "
          "backup | rollback [--yes] | prove | selftest]"
          "  (no arguments = friendly menu)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
