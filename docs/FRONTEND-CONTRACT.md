# Front-end contract

Vanilla JS, no build step. Scripts share one global scope, so every new file is
an IIFE that exposes exactly what it registers and prefixes internal names.

## Load order (index.html)

```
vendor/highlight/highlight.min.js
js/core.js          shared state S, $, esc, api, jobs, dock, confirm, banners, router, showView, crumbs, registerArea
js/components.js    renderGateway, AltGrid, tabs, needCards, emptyState, renderLedger, statStrip, pills
js/grade.js         the grader (formerly app.js), unchanged apart from two crumb hrefs
js/hub.js           the course hub
js/a11y.js  js/docs.js  js/pdf.js  js/content.js  js/courseops.js  js/assistant.js
js/boot.js          boot();
viewer.js           (module)
```

## Shell API (core.js), available to every area

| Function | Purpose |
|---|---|
| `S` | global state; areas use `S.<area> = S.<area> || {}` |
| `$(sel)`, `esc(text)` | query one element; HTML-escape |
| `api(path, {body, method})` | fetch `/api` + path; POST when `body`; throws `Error` with `.status` and `.body` |
| `runJob(title, start, onDone, opts)` | start a job (`start()` returns the `{job}` promise), show the progress dialog, poll, call `onDone(result)` |
| `runJobConfirmed(title, start, onDone, opts)` | same, for jobs that write: `start(token)` is called with `null`, then again with the confirm token after the person agrees |
| `postConfirmed(start, opts)` | the same handshake for a synchronous POST |
| `askConfirm(info, proceed, opts)` | the confirmation modal (server sentence + detail list) |
| `banner(id, html, kind)` / `hideBanner(id)` | persistent notices under the header |
| `setStatus(text, kind)` | the header status line (`kind` = `ok` or `err`) |
| `crumbs([{label, href}])` | breadcrumbs |
| `showView(which)` | `picker` `schedule` `work` `hub` `area`; hides the rest, stops pollers, sets `body.dataset.area` |
| `registerArea({id, label, zone, open, badge})` | register an area (see below) |
| `onLeave(fn)` | run `fn` when the user leaves the current view (stop your poller here) |
| `fmtDate`, `fmtDay`, `fmtTime`, `shownValue`, `firstLine`, `num` | formatting helpers |
| `openModal(html)` / `closeModal()` | the single modal host, with focus trap and Escape |

## Area registration

```js
(function () {
  'use strict';
  function openPdf(courseId, rest) {
    showView('area');                    // mounts #viewArea and the area bar
    crumbs([{ label: 'Courses', href: '#/' }, { label: S.course?.name || 'Course', href: '#/c/' + courseId }, { label: 'Accessibility' }]);
    $('#headerActions').innerHTML = '<button class="btn" id="pdfRefresh">Refresh</button>';
    areaHead('PDF files', 'Fix, describe, prove, upload.');
    areaTabs([...]);                     // optional sub-tabs, see components.js
    $('#areaBody').innerHTML = '...';
  }
  registerArea({ id: 'pdf', label: 'PDFs', zone: 'a11y', open: openPdf,
                 badge: hub => hub?.pdf?.badge ?? null });
})();
```

Routes: `#/c/<cid>/<area>[/...]` dispatch to `open(courseId, rest)` where `rest`
is the remaining path parts. The Accessibility area bar tab points to `a11y`;
`a11y.js` renders the kind tabs (HTML, PowerPoint, Word, PDFs, PDF text, Office
text) and delegates `pdf` to `pdf.js` and the document kinds to `docs.js`
through `Studio.a11yKinds[kind] = openFn`. `docs.js` and `pdf.js` therefore
register their openers on `Studio.a11yKinds` **and** `registerArea` themselves
so `#/c/<cid>/pdf` also works.

## Components (components.js)

- `areaHead(title, hint, actionsHtml)` fills `#areaTitle`, `#areaHint`, `#areaActions`.
- `areaTabs(items, activeId, onPick)` renders `#areaTabs` as an ARIA tablist with arrow keys; `items = [{id, label, badge}]`.
- `renderGateway(host, spec)` the five-step List, Scan, Review, Dry run, Apply component. `spec = {kind, courseId, label, endpoints:{state, list, fetch, describe, fixes, push}, listColumns, reviewRenderer(host, state), confirmTitle, describeLabel}`. It polls `endpoints.state`, drives jobs with `runJob`/`runJobConfirmed`, and calls `reviewRenderer` for step 3.
- `AltGrid(host, items, {onChange, onDescribeAll})` cards with picture, kind pill, context, editable description with a 110-char counter, source tag, Decorative tick; plus the "No picture available" and "Left alone" sections when given.
- `needCards(tools)` renders install cards for missing tools from `/api/health` `tools`.
- `emptyState(text, buttonLabel, onClick)`.
- `renderLedger(host, rows)` rows from `/api/courses/<cid>/ledger`.
- `statStrip(host, [{label, value, kind}])`.
- `pill(text, cls)`, `lanePill(lane)`, `verdictPill(verdict)`.
- `poll(fn, ms)` returns a stopper; register it with `onLeave`.

## CSS

Tokens: `--zone`, `--zone-soft` follow `body[data-area]`; area colours
`--a11y`, `--build`, `--tools`, plus the existing `--accent` (grade) and `--ai`
(assistant). Reuse `.btn .btn.primary .btn.ai .btn.danger .btn.sm .pill .chip
.card .cards .modal .progTrack .jobChip .banner .applyList .planned .callout`.
Area-only styles go in `web/css/<area>.css`, loaded by index.html. Zone colour
appears in three places per area only: the tab underline, the area head rule,
the one primary button. Pills carry words, never colour alone. Every clickable
thing is a `<button>` or `<a>`, with a visible focus ring.

## Rules

- Never `location.reload()` to refresh; re-run your `open`.
- One poller per view; stop it in `onLeave`.
- Show the server's confirm sentence as-is; do not paraphrase it.
- No `confirm()`/`alert()`; use `askConfirm` and banners.
- Anything that writes uses `runJobConfirmed`/`postConfirmed`, full-size `.btn.danger`.
- Empty states say what the first action does and that nothing is pushed.
