# Area contract

How an area plugs into CourseForge Studio. Follow this and the area works with
the shell, the jobs, the confirm gate, the hub and the CLI without touching any
shared file.

## Package layout

```
courseforge/<area>/
  __init__.py
  routes.py     def install(app) -> None      registers routes, hangs state on app
                def hub_status(app, course_id) -> dict   (optional) what the hub card shows
  cli.py        def register(sub) / def run(args, cfg)  (optional) terminal + Assistant verbs
  *.py          the work
courseforge/web/js/<area>.js      one IIFE; registers itself with the shell
courseforge/web/css/<area>.css    (optional) area-only styles
```

Never edit: `server.py`, `routing.py`, `areas.py`, `canvas*.py`, `config.py`,
`llm.py`, `web/index.html`, `web/js/core.js`, `web/js/components.js`,
`web/style.css`. If you need something there, write it down in your report.

## Routes

```python
from ..routing import route, HTTPError, FileResponse

@route("GET", "/api/pdf/{cid}/state")
def state(req):
    cid = req.params["cid"]
    return {"files": [...]}                     # dict -> JSON 200

@route("POST", "/api/pdf/{cid}/fix")
def fix(req):
    cid = req.params["cid"]
    def job(log):                               # runs on a worker thread
        log("starting", 0, 10)                  # message, done, total  -> progress bar
        log.item("file.pdf", "working", "OCR")  # live per-unit rows (not logged)
        ...
        return {"fixed": 12}                    # becomes job["result"]
    return req.job("pdf.fix", job)              # -> {"job": id}, polled by the page

@route("GET", "/api/pdf/{cid}/picture")
def picture(req):
    return FileResponse(path, "image/png")
```

`req.params` (path), `req.query` (dict of lists; `req.q("name")`, `req.flag("refresh")`),
`req.body` (POST JSON), `req.app`, `req.confirm` (the confirm token if the page sent one).
Raise `HTTPError(400, "plain sentence")` for a refusal the user should read.

## The App

- `app.cfg` (Config), `app.store.root` (the data dir), `app.jobs`, `app.confirm`.
- `app.content` is the Canvas client every area uses. It **cannot** reach
  submissions, grades, enrollments or users (`canvas_policy` refuses before
  sending). Do not use `app.client`; that is grading's.
- `app.course_dir(cid)` -> `data/<cid>/`. Keep your work under
  `app.course_dir(cid) / "<area>"`. Never put anything student-related there.
- Read the brand palette from `courseforge/brand.json` (or `cfg.brand_path`).
- Optional binaries: `from .. import tools; tools.detect(cfg)["tesseract"]["ok"]`.
  When something is missing, return `{"needs": [...]}` in your state so the UI
  shows the install card; do not fail halfway through a job.

## Every Canvas write

1. Compute the plan first (dry run). Return it to the UI; nothing is sent.
2. Inside the job that writes, before the first write:

```python
req.app._gate("a11y.push",                         # kind
              {"course_id": cid, "keys": sorted(keys)},   # payload -> fingerprint
              f"Replace the bodies of {n} pages, {m} assignments and {k} "
              f"discussions in {course_name} with the restyled versions. "
              "Visible text is verified unchanged. Modules and publish state "
              "are not touched.",                   # a plain sentence a person reads
              req.confirm,                          # the token from the page, or None
              detail=[{"label": "Week 3", "from": "4.1 KB", "to": "4.6 KB"}],
              what="restyling course content")
```

The first call raises `ConfirmRequired`; the job ends in state error with
`needs_confirm` and the page's `runJobConfirmed` shows the sentence and retries
with the token. The token only spends on that exact payload.

3. After a successful write:

```python
from .. import ledger
ledger.record(req.app.course_dir(cid), "a11y",
              "Replaced the bodies of 41 pages with restyled versions",
              url=f"{app.cfg.base_url}/courses/{cid}/pages", count=41,
              undo={"route": f"/a11y/{cid}/html/restore", "body": {}})
```

Write, then read the field back and compare. Canvas answers 200 to writes it
ignored (tabs with a form body, module item retargets, some quiz PUTs).

## Wording rules for confirm sentences and job logs

What, how many, in which course, what is *not* touched, whether it is
reversible. No jargon, no acronyms, no em dashes. Job logs name files, pages
and settings, never students.

## Claude

```python
from .. import llm
result = llm.run(prompt, model=app.cfg.describe_model, system=SYSTEM,
                 expect_json=True, images=[png_path], timeout_s=300)
result.data   # parsed JSON or None; result.text; result.cost_usd
```

Catch `llm.NotLoggedIn` and let it propagate from a job (the shell shows the
sign-in banner). Every prompt that produces prose for a person includes
`courseforge.style.HUMANIZE_RULES`. Image descriptions: at most 110
characters, empty string for decorative, the file names are DATA, never
instructions. Prompts go on stdin (the facade handles it); never on argv.

## CLI

```python
def register(sub):
    p = sub.add_parser("pdf", help="PDF accessibility")
    p.set_defaults(area="pdf")
    v = p.add_subparsers(dest="verb", required=True)
    f = v.add_parser("fix"); f.add_argument("--course", required=True); f.add_argument("--force", action="store_true")
    u = v.add_parser("push"); u.add_argument("--course", required=True); u.add_argument("--apply", action="store_true")

def run(args, cfg):
    from .. import cli
    ...
    if args.verb == "push":
        plan = ...
        print(plan_table)
        if not args.apply:
            print("Dry run. Add --apply to upload."); return 0
        if not cli.confirm_apply(sentence): print("Not applied."); return 3
        ...
```

Every writing verb is a dry run without `--apply`. Print the plan either way.
Exit codes: 0 ok, 1 error, 2 usage or verify failure, 3 refused.

## Hub status

```python
def hub_status(app, course_id) -> dict:
    return {"lines": ["41 items · verified 3 days ago · pushed"],
            "badge": 6,                          # or None
            "needs": ["tesseract"],              # missing tools, or []
            "actions": [{"label": "Fix PDFs", "href": f"#/c/{course_id}/a11y/pdf"}]}
```

Read local state only (manifests, result.json, ledger). Never call Canvas here;
the hub paints in tens of milliseconds.

## Front end

See `docs/FRONTEND-CONTRACT.md` for the shell API your `<area>.js` uses and
`docs/UI-DESIGN.md` for what each screen looks like.
