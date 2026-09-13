/* CourseForge Studio - shared components. Everything an area draws more than
   once lives here so the five areas look like one tool: the area head and its
   sub-tabs, the five-step gateway, the alt-text grid, install cards, the empty
   state, the ledger, the stat strip, pills and a poller. Every component is
   keyboard-usable and leans on classes style.css already has. */
(function () {
  'use strict';

  /* Accept an element or a selector wherever a host is asked for. */
  function el(host) {
    return typeof host === 'string' ? $(host) : host;
  }

  const clamp = (text, n) => {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  };

  function fmtBytes(n) {
    if (n == null || n === '' || isNaN(+n)) return '';
    const v = +n;
    if (v < 1024) return v + ' B';
    if (v < 1024 * 1024) return (v / 1024).toFixed(v < 10240 ? 1 : 0) + ' KB';
    return (v / 1048576).toFixed(1) + ' MB';
  }

  /* ------------------------------------------------------------- pills */
  /* Pills carry words, never colour alone: the class tints, the text says. */
  function pill(text, cls) {
    return `<span class="pill ${esc(cls || '')}">${esc(text)}</span>`;
  }

  const LANE_WORDS = {
    full: 'full fix', light: 'light fix', refused: 'refused',
    queued: 'queued for a person', review: 'needs review', skipped: 'left alone',
  };
  function lanePill(lane) {
    const key = String(lane || '').toLowerCase();
    return pill(LANE_WORDS[key] || key || 'no lane', 'lane-' + (key || 'none'));
  }

  const VERDICT_WORDS = {
    pass: 'pass', fail: 'fail', warn: 'check', skip: 'skipped', unknown: 'not checked',
    partial: 'partly', ok: 'ok', error: 'error', na: 'not applicable',
  };
  function verdictPill(verdict) {
    const key = String(verdict == null ? 'unknown' : verdict).toLowerCase();
    const word = VERDICT_WORDS[key] || key;
    const cls = key === 'ok' ? 'pass' : key;
    return pill(word, 'v-' + cls);
  }

  const STATE_WORDS = {
    'not-fetched': 'not fetched', listed: 'not fetched', fetched: 'fetched', scanned: 'scanned',
    issues: 'has issues', clean: 'clean', fixed: 'fixed', verified: 'verified',
    failed: 'verify failed', pushed: 'pushed', applied: 'pushed', uploaded: 'uploaded',
    excluded: 'excluded', queued: 'queued', error: 'error',
  };
  function statePill(state) {
    const key = String(state || 'not-fetched').toLowerCase();
    return pill(STATE_WORDS[key] || key.replace(/[-_]/g, ' '), 'st-' + key);
  }

  /* --------------------------------------------------------------- poll */
  /* Calls fn every ms until the stopper is called. The first call happens
     after ms, not at once: the caller has usually just loaded the state
     itself. Register the stopper with onLeave. */
  function poll(fn, ms) {
    let stopped = false, timer = null, busy = false;
    const loop = async () => {
      if (stopped) return;
      if (!busy) {
        busy = true;
        try { await fn(); } catch (_) { /* the next tick tries again */ }
        busy = false;
      }
      if (!stopped) timer = setTimeout(loop, ms);
    };
    timer = setTimeout(loop, ms);
    return () => { stopped = true; clearTimeout(timer); };
  }

  /* ---------------------------------------------------------- area head */
  function areaHead(title, hint, actionsHtml) {
    const t = $('#areaTitle'), h = $('#areaHint'), a = $('#areaActions');
    if (t) t.textContent = title || '';
    if (h) h.textContent = hint || '';
    if (a) a.innerHTML = actionsHtml || '';
    if (title) document.title = `${title} · CourseForge Studio`;
  }

  /* An ARIA tablist. Arrow keys move focus between tabs; Enter or Space (the
     button's own click) picks one. Manual activation on purpose: picking
     usually changes the route and repaints, which would swallow focus mid-
     arrow-key. items = [{id, label, badge, title}]. */
  function areaTabs(items, activeId, onPick) {
    const host = $('#areaTabs');
    if (!host) return;
    items = items || [];
    host.hidden = !items.length;
    if (!items.length) { host.innerHTML = ''; return; }
    host.innerHTML = items.map(t => {
      const on = t.id === activeId;
      return `<button class="subTab" role="tab" type="button" id="tab-${esc(t.id)}"
        aria-selected="${on}" tabindex="${on ? 0 : -1}" aria-controls="areaBody"
        data-tab="${esc(t.id)}"${t.title ? ` title="${esc(t.title)}"` : ''}>${esc(t.label)}${
        (t.badge == null || t.badge === 0 || t.badge === '') ? '' : `<span class="n">${esc(t.badge)}</span>`}</button>`;
    }).join('');
    const body = $('#areaBody');
    if (body && activeId) body.setAttribute('aria-labelledby', 'tab-' + activeId);
    const tabs = [...host.querySelectorAll('[role=tab]')];
    tabs.forEach((tab, i) => {
      tab.onclick = () => {
        tabs.forEach(x => { x.setAttribute('aria-selected', String(x === tab)); x.tabIndex = x === tab ? 0 : -1; });
        if (body) body.setAttribute('aria-labelledby', tab.id);
        if (onPick) onPick(tab.dataset.tab);
      };
      tab.onkeydown = ev => {
        let j = null;
        if (ev.key === 'ArrowRight') j = (i + 1) % tabs.length;
        else if (ev.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
        else if (ev.key === 'Home') j = 0;
        else if (ev.key === 'End') j = tabs.length - 1;
        if (j == null) return;
        ev.preventDefault();
        tabs[j].focus();
      };
    });
  }

  /* --------------------------------------------------------- empty state */
  /* Says what the first action does, and that nothing is pushed. Returns an
     element; the caller appends it. */
  function emptyState(text, buttonLabel, onClick) {
    const box = document.createElement('div');
    box.className = 'empty';
    box.setAttribute('role', 'note');
    const p = document.createElement('p');
    p.textContent = text || 'Nothing here yet. Nothing has been pushed to Canvas.';
    box.appendChild(p);
    if (buttonLabel) {
      const btn = document.createElement('button');
      btn.className = 'btn primary';
      btn.type = 'button';
      btn.textContent = buttonLabel;
      if (onClick) btn.onclick = onClick;
      box.appendChild(btn);
    }
    return box;
  }

  /* ---------------------------------------------------------- stat strip */
  /* [{label, value, kind}] -> a row of figures. Inside a .bandHead it takes the
     navy-and-gold look; anywhere else it sits on the panel colour. */
  function statStrip(host, stats) {
    host = el(host);
    if (!host) return;
    stats = (stats || []).filter(s => s && s.label != null);
    host.innerHTML = `<div class="statStrip" role="list">${stats.map(s => `
      <div class="stat ${esc(s.kind || '')}" role="listitem"${s.title ? ` title="${esc(s.title)}"` : ''}>
        <b>${esc(s.value == null || s.value === '' ? '—' : s.value)}</b>
        <span>${esc(s.label)}</span>
      </div>`).join('')}</div>`;
  }

  /* -------------------------------------------------------- install cards */
  /* One card per missing tool: what is missing, what it enables, how to
     install it, and Check again. `tools` is the /api/health tools dict, or a
     list of names looked up in the current S.health.tools. */
  function needCards(tools, host) {
    const fresh = (S.health && S.health.tools) || {};
    const names = Array.isArray(tools) ? tools : Object.keys(tools || {});
    const dict = Array.isArray(tools) ? {} : (tools || {});
    const missing = names
      .map(n => [n, fresh[n] || dict[n] || { ok: false, enables: '', install: '' }])
      .filter(([, t]) => t && t.ok === false);
    const html = !missing.length ? '' : `<div class="needCards">${missing.map(([name, t]) => `
      <div class="needCard" role="group" aria-labelledby="need-${esc(name)}">
        <b id="need-${esc(name)}">Install ${esc(t.label || name)}</b>
        <span class="needWhat">to enable ${esc(t.enables || 'this part of the Studio')}.</span>
        ${t.install ? `<code class="needHow">${esc(t.install)}</code>` : ''}
        ${t.detail ? `<span class="needDetail muted">${esc(clamp(t.detail, 140))}</span>` : ''}
        <span class="needActs">
          <button class="btn sm" type="button" data-check="${esc(name)}">Check again</button>
        </span>
      </div>`).join('')}</div>`;
    host = el(host);
    if (host) {
      host.innerHTML = html;
      host.querySelectorAll('[data-check]').forEach(btn => {
        btn.onclick = async () => {
          btn.disabled = true;
          btn.textContent = 'Checking…';
          try { S.health = await api('/health'); }
          catch (err) { setStatus('could not re-check: ' + firstLine(err.message), 'err'); }
          const still = ((S.health && S.health.tools) || {})[btn.dataset.check];
          setStatus(still && still.ok ? btn.dataset.check + ' found' : btn.dataset.check + ' still missing',
            still && still.ok ? 'ok' : 'err');
          needCards(names, host);
        };
      });
    }
    return html;
  }

  /* -------------------------------------------------------------- ledger */
  /* Rows from GET /api/courses/<cid>/ledger: when, which area, the sentence,
     a link into Canvas, and Roll back where the area left an undo. */
  const AREA_WORDS = {
    grade: 'Grade', a11y: 'Accessibility', docs: 'Accessibility', pdf: 'Accessibility',
    content: 'Build', build: 'Build', courseops: 'Tools', tools: 'Tools', assistant: 'Assistant',
  };
  function renderLedger(host, rows, opts = {}) {
    host = el(host);
    if (!host) return;
    rows = Array.isArray(rows) ? rows : (rows && Array.isArray(rows.rows)) ? rows.rows : [];
    if (!rows.length) {
      host.replaceChildren(emptyState(opts.empty || 'Nothing has been written to Canvas from here yet.'));
      return;
    }
    host.innerHTML = `<ol class="ledger" aria-label="Canvas writes from the Studio">${rows.map((r, i) => `
      <li class="ledgerRow">
        <time class="ledgerWhen" datetime="${esc(r.at || '')}">${esc(fmtDate(r.at) || '')}</time>
        ${pill(AREA_WORDS[r.area] || r.area || 'Studio', 'area-' + esc(r.area || ''))}
        <span class="ledgerWhat">${esc(r.sentence || '')}${r.count != null && r.count !== '' ?
          ` <span class="muted">(${esc(r.count)})</span>` : ''}</span>
        <span class="ledgerActs">
          ${r.url ? `<a class="btn sm" href="${esc(r.url)}" target="_blank" rel="noopener">Open in Canvas</a>` : ''}
          ${r.undo && r.undo.route ? `<button class="btn sm danger" type="button" data-undo="${i}">Roll back</button>` : ''}
        </span>
      </li>`).join('')}</ol>`;
    host.querySelectorAll('[data-undo]').forEach(btn => {
      btn.onclick = () => {
        const row = rows[+btn.dataset.undo];
        const undo = row.undo;
        runMaybeJob('Rolling back: ' + clamp(row.sentence, 60),
          token => api(undo.route, { body: { ...(undo.body || {}), confirm: token } }),
          res => { setStatus('rolled back', 'ok'); if (opts.onChange) opts.onChange(res); },
          { title: 'Roll this change back?', verb: 'Yes, roll it back' });
      };
    });
  }

  /* ------------------------------------------------------- job or answer */
  /* Some endpoints answer at once, some hand back {job}, and either kind may
     be refused once with needs_confirm. This runs whichever comes back: a job
     gets the progress dialog and its confirm handshake; a plain answer goes
     straight to onDone. `start(token, choice)` is the call. */
  function runMaybeJob(title, start, onDone, opts = {}) {
    const go = (token, choice) => {
      Promise.resolve().then(() => start(token, choice)).then(res => {
        if (res && res.job) {
          runJob(title, () => Promise.resolve(res), onDone,
            { ...opts, onNeedsConfirm: info => askConfirm(info, go, opts) });
        } else if (res !== undefined && onDone) {
          onDone(res);
        }
      }).catch(err => {
        const body = (err && err.body) || {};
        if (body.needs_confirm) { askConfirm(body, go, opts); return; }
        setStatus(firstLine((err && err.message) || 'failed'), 'err');
        if (opts.onError) opts.onError(err);
      });
    };
    go(null);
  }

  /* ------------------------------------------------------------- gateway */
  /* List, Scan, Review, Dry run, Apply: one component for every content kind.
     Nothing reaches Canvas before step 5, and step 5 asks first. The state
     comes from spec.endpoints.state and is polled while the gateway is on
     screen; the person's selection and step survive each repaint.

     spec = {kind, courseId, label, endpoints:{state, list, fetch, describe,
             fixes, push, restore}, listColumns, reviewRenderer(host, state),
             confirmTitle, describeLabel, pollMs}
     state = {items:[{key, title, kind, size, modified, state, issues,
              verify:{ok, checks:[{label, ok, detail}]}, planned:{from, to}}],
              step, needs:[], summary} */
  const STEPS = [
    { id: 'list', label: 'List' },
    { id: 'scan', label: 'Scan' },
    { id: 'review', label: 'Review' },
    { id: 'dry', label: 'Dry run' },
    { id: 'apply', label: 'Apply' },
  ];
  const STEP_OF = {
    list: 1, listed: 1, scan: 2, scanned: 2, fetched: 2, review: 3, fixes: 3, fixed: 3,
    dry: 4, 'dry-run': 4, dryrun: 4, plan: 4, planned: 4, apply: 5, applied: 5, pushed: 5,
  };

  function gwItems(state) {
    if (!state) return [];
    if (Array.isArray(state.items)) return state.items;
    if (Array.isArray(state.files)) return state.files;
    return [];
  }
  function gwKey(it, i) { return String(it.key != null ? it.key : (it.id != null ? it.id : i)); }
  function gwTitle(it) { return it.title || it.name || it.key || ''; }
  function gwState(it) {
    if (it.state) return String(it.state).toLowerCase();
    if (it.verify) return it.verify.ok ? 'verified' : 'failed';
    return 'not-fetched';
  }
  function gwIssues(it) {
    const v = it.issues;
    if (Array.isArray(v)) return v;
    if (typeof v === 'number') return v ? [`${v} issue${v === 1 ? '' : 's'}`] : [];
    if (v && typeof v === 'object') return Object.entries(v).map(([k, n]) => n ? `${k}: ${n}` : '').filter(Boolean);
    return v ? [String(v)] : [];
  }
  function verifyChecks(it) {
    const v = it.verify;
    if (!v) return [];
    if (Array.isArray(v.checks)) return v.checks;
    return Object.entries(v).filter(([k]) => k !== 'ok').map(([k, val]) => ({
      label: k.replace(/[_-]/g, ' '), ok: !!val && typeof val === 'object' ? val.ok !== false : !!val,
    }));
  }
  function verifyHtml(it) {
    const checks = verifyChecks(it);
    const failed = it.verify && it.verify.ok === false;
    const bits = checks.map(c => pill((c.label || 'check') + (c.ok ? '' : (c.detail ? ': ' + clamp(c.detail, 40) : ': failed')),
      c.ok ? 'v-pass' : 'v-fail'));
    if (failed) bits.push(pill('excluded from push', 'v-fail'));
    if (!bits.length && it.verify && it.verify.ok === true) bits.push(pill('verified', 'v-pass'));
    return bits.join(' ');
  }

  const GW_FILTERS = [
    { id: 'all', label: 'All', test: () => true },
    { id: 'unfetched', label: 'Not fetched', test: it => /^(not|new|listed|unfetched|pending)/.test(gwState(it)) },
    { id: 'issues', label: 'Has issues', test: it => gwIssues(it).length > 0 },
    { id: 'verified', label: 'Verified', test: it => gwState(it) === 'verified' || !!(it.verify && it.verify.ok === true) },
    { id: 'pushed', label: 'Pushed', test: it => /pushed|applied|uploaded/.test(gwState(it)) },
  ];

  function renderGateway(host, spec) {
    host = el(host);
    if (!host) return null;
    const E = (spec && spec.endpoints) || {};
    const gw = {
      spec, host, state: null, items: [], step: 1, touched: false, picked: new Set(),
      filter: 'all', print: '', plan: null, result: null, stop: null, error: null,
    };

    host.innerHTML = `<div class="gw" data-kind="${esc(spec.kind || '')}">
      <ol class="steps" aria-label="${esc(spec.label || 'Steps')}">${STEPS.map((s, i) => `
        <li><button class="step" type="button" data-step="${i + 1}" aria-current="${i === 0 ? 'step' : 'false'}">
          <span class="stepNum" aria-hidden="true">${i + 1}</span>${esc(s.label)}</button></li>`).join('')}</ol>
      <div class="gwNeeds"></div>
      <div class="gwPane"></div>
      <div class="gwFoot">
        <span class="gwSummary" role="status"></span>
        <span class="spacer"></span>
        <span class="gwActs"></span>
      </div>
    </div>`;
    const root = host.firstElementChild;
    const pane = root.querySelector('.gwPane');
    const foot = root.querySelector('.gwActs');
    const summary = root.querySelector('.gwSummary');
    const needsBox = root.querySelector('.gwNeeds');

    const shown = () => {
      const f = GW_FILTERS.find(x => x.id === gw.filter) || GW_FILTERS[0];
      return gw.items.filter(f.test);
    };
    const keysFor = () => {
      const all = gw.items.map(gwKey);
      const picked = all.filter(k => gw.picked.has(k));
      return picked.length ? picked : shown().map(gwKey);
    };
    const count = () => keysFor().length;
    const stepButtons = [...root.querySelectorAll('.step')];

    function paintSteps() {
      stepButtons.forEach((b, i) => {
        const n = i + 1;
        b.setAttribute('aria-current', n === gw.step ? 'step' : 'false');
        b.classList.toggle('done', n < gw.step);
        b.disabled = n > gw.step + 1;
        b.title = n > gw.step + 1 ? 'Finish the earlier steps first' : '';
      });
    }

    function goto(n) {
      gw.step = Math.max(1, Math.min(5, n));
      gw.touched = true;
      paintSteps();
      paintPane();
    }
    stepButtons.forEach(b => { b.onclick = () => goto(+b.dataset.step); });

    function table(withIssues) {
      const rows = shown();
      const cols = spec.listColumns || null;
      const allOn = rows.length && rows.every(it => gw.picked.has(gwKey(it)));
      const chips = `<div class="chips gwChips" role="group" aria-label="Show">${GW_FILTERS.map(f => {
        const n = gw.items.filter(f.test).length;
        return `<button class="chip" type="button" data-filter="${f.id}" aria-pressed="${gw.filter === f.id}">${esc(f.label)}${
          f.id === 'all' ? '' : ` <span class="cnum">${n}</span>`}</button>`;
      }).join('')}</div>`;
      if (!gw.items.length) {
        return chips + `<div class="empty"><p>${esc(gw.error ? 'Could not read the state: ' + gw.error
          : `No ${spec.label || 'items'} listed yet. Refresh the list reads the names from Canvas; nothing is written.`)}</p></div>`;
      }
      const head = cols ? cols.map(c => `<th scope="col">${esc(c.label)}</th>`).join('')
        : `<th scope="col">Name</th><th scope="col">Type</th><th scope="col">Size</th>
           <th scope="col">Modified</th>${withIssues ? '<th scope="col">Issues</th>' : ''}<th scope="col">State</th>`;
      const body = rows.map((it, i) => {
        const k = gwKey(it, i);
        const cells = cols ? cols.map(c => `<td>${c.render ? c.render(it) : esc(it[c.key])}</td>`).join('')
          : `<td><b>${esc(gwTitle(it))}</b>${it.folder ? `<span class="muted"> · ${esc(it.folder)}</span>` : ''}</td>
             <td>${esc(it.kind || it.type || '')}</td>
             <td class="num">${esc(typeof it.size === 'number' ? fmtBytes(it.size) : (it.size || ''))}</td>
             <td class="muted">${esc(fmtDate(it.modified || it.updated_at) || it.modified || '')}</td>
             ${withIssues ? `<td>${gwIssues(it).length ? esc(gwIssues(it).join('; ')) : '<span class="muted">none</span>'}</td>` : ''}
             <td>${statePill(gwState(it))}</td>`;
        return `<tr data-key="${esc(k)}" class="${gw.picked.has(k) ? 'picked' : ''}">
          <td class="gwPick"><input type="checkbox" data-pick="${esc(k)}" ${gw.picked.has(k) ? 'checked' : ''}
            aria-label="Include ${esc(gwTitle(it))}"></td>${cells}</tr>`;
      }).join('');
      return chips + `<div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">${esc(spec.label || 'Items')}: ${rows.length} shown of ${gw.items.length}</caption>
        <thead><tr><th scope="col" class="gwPick"><input type="checkbox" id="gwAll" ${allOn ? 'checked' : ''}
          aria-label="Include everything shown"></th>${head}</tr></thead>
        <tbody>${body}</tbody></table></div>`;
    }

    function wireTable() {
      pane.querySelectorAll('[data-filter]').forEach(b => {
        b.onclick = () => { gw.filter = b.dataset.filter; paintPane(); };
      });
      pane.querySelectorAll('[data-pick]').forEach(cb => {
        cb.onchange = () => {
          if (cb.checked) gw.picked.add(cb.dataset.pick); else gw.picked.delete(cb.dataset.pick);
          cb.closest('tr').classList.toggle('picked', cb.checked);
          paintFoot();
        };
      });
      const all = pane.querySelector('#gwAll');
      if (all) all.onchange = () => {
        shown().forEach((it, i) => { const k = gwKey(it, i); if (all.checked) gw.picked.add(k); else gw.picked.delete(k); });
        paintPane();
      };
    }

    function summarySentence() {
      if (gw.state && gw.state.summary) return String(gw.state.summary);
      const n = gw.items.length;
      if (!n) return '';
      const issues = gw.items.filter(it => gwIssues(it).length).length;
      const verified = gw.items.filter(it => it.verify && it.verify.ok === true).length;
      const pushed = gw.items.filter(it => /pushed|applied|uploaded/.test(gwState(it))).length;
      return `${n} ${spec.label || 'items'} · ${issues} with issues · ${verified} verified · ${pushed} pushed`;
    }

    function plannedRows() {
      const fromPlan = gw.plan && (gw.plan.planned || gw.plan.items || gw.plan.plan);
      if (Array.isArray(fromPlan) && fromPlan.length) return fromPlan;
      return gw.items.filter(it => it.planned && !(it.verify && it.verify.ok === false)).map(it => ({
        key: gwKey(it), title: gwTitle(it), from: it.planned.from, to: it.planned.to, label: it.planned.label,
      }));
    }
    function excludedRows() {
      const fromPlan = gw.plan && (gw.plan.excluded || gw.plan.skipped);
      if (Array.isArray(fromPlan) && fromPlan.length) return fromPlan;
      return gw.items.filter(it => it.verify && it.verify.ok === false).map(it => ({
        key: gwKey(it), title: gwTitle(it), reason: (verifyChecks(it).find(c => !c.ok) || {}).label || 'verify failed',
      }));
    }

    function paintPane() {
      const keep = pane.contains(document.activeElement) && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)
        && document.activeElement.type !== 'checkbox';
      if (keep) return;                         // never repaint under a hand that is typing
      if (gw.step === 1) { pane.innerHTML = table(false); wireTable(); }
      else if (gw.step === 2) { pane.innerHTML = table(true); wireTable(); }
      else if (gw.step === 3) {
        pane.innerHTML = '<div class="gwReview"></div><div class="gwVerify"></div>';
        const rhost = pane.querySelector('.gwReview');
        try {
          if (spec.reviewRenderer) spec.reviewRenderer(rhost, gw.state || { items: gw.items });
          else rhost.appendChild(emptyState('This kind has no review pane yet; the verification list below is what the push will trust.'));
        } catch (err) {
          rhost.innerHTML = `<div class="callout bad">The review pane failed to draw: ${esc(err.message)}</div>`;
        }
        const keys = new Set(keysFor());
        const list = gw.items.filter((it, i) => keys.has(gwKey(it, i)));
        pane.querySelector('.gwVerify').innerHTML = !list.length ? '' : `<h3 class="gwH">Verification</h3>
          <table class="gwTable"><caption class="srOnly">Verification per item</caption>
          <thead><tr><th scope="col">Item</th><th scope="col">Checks</th></tr></thead>
          <tbody>${list.map(it => `<tr><td><b>${esc(gwTitle(it))}</b></td>
            <td>${verifyHtml(it) || '<span class="muted">not verified yet</span>'}</td></tr>`).join('')}</tbody></table>`;
      } else if (gw.step === 4) {
        const rows = plannedRows(), out = excludedRows();
        pane.innerHTML = `<h3 class="gwH">Will be pushed (${rows.length})</h3>
          ${rows.length ? `<ul class="applyList">${rows.map(r => `<li class="planned"><b>${esc(r.title || r.key || '')}</b>
            ${r.label ? `<span class="muted"> ${esc(r.label)}</span>` : ''}
            ${(r.from != null || r.to != null) ? `<span class="cfFrom">${esc(shownValue(r.from))}</span>
            <span class="cfArrow">→</span> <span class="cfTo">${esc(shownValue(r.to))}</span>` : ''}</li>`).join('')}</ul>`
          : '<div class="empty"><p>Nothing is planned. Run the dry run from Review, or pick items on List.</p></div>'}
          ${out.length ? `<h3 class="gwH">Will not be pushed (${out.length})</h3>
            <ul class="applyList">${out.map(r => `<li class="bad"><b>${esc(r.title || r.key || '')}</b>
              <span class="muted"> ${esc(r.reason || 'excluded')}</span></li>`).join('')}</ul>` : ''}
          <p class="muted gwNote">A dry run. Nothing has been sent to Canvas.</p>`;
      } else {
        const done = gw.result && (gw.result.items || gw.result.pushed || gw.result.applied || gw.result.results);
        const rows = Array.isArray(done) && done.length ? done
          : gw.items.filter(it => /pushed|applied|uploaded|failed|error/.test(gwState(it)));
        pane.innerHTML = `<h3 class="gwH">Applied (${rows.length})</h3>
          ${rows.length ? `<ul class="applyList">${rows.map(r => {
            const it = gw.items.find((x, i) => gwKey(x, i) === String(r.key != null ? r.key : r.id)) || r;
            const bad = /failed|error/.test(gwState(it)) || r.ok === false || r.error;
            return `<li class="${bad ? 'bad' : 'done'}"><b>${esc(gwTitle(it) || r.title || r.key || '')}</b>
              ${statePill(r.error ? 'error' : gwState(it))} ${verifyHtml(it)}
              ${r.error ? `<span class="muted"> ${esc(r.error)}</span>` : ''}</li>`;
          }).join('')}</ul>`
          : '<div class="empty"><p>Nothing has been applied in this course yet.</p></div>'}`;
      }
      paintFoot();
    }

    function btn(label, cls, id, title) {
      return `<button class="btn ${cls || ''}" type="button" data-act="${id}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    }

    function paintFoot() {
      const n = count();
      summary.textContent = summarySentence();
      let html = '';
      if (gw.step === 1) {
        html = (E.list ? btn('Refresh list', '', 'list', 'Read the names from Canvas; nothing is written') : '')
          + btn(`Fetch & scan ${n}`, 'primary', 'fetch', 'Download the selected items and scan them for issues');
      } else if (gw.step === 2) {
        html = (E.describe ? btn(spec.describeLabel || 'Describe images with Claude', 'ai', 'describe') : '')
          + btn('Review fixes', 'primary', 'fixes', 'Compute the fixes; nothing is sent');
      } else if (gw.step === 3) {
        html = btn('Dry run', 'primary', 'dry', 'Plan the push and show what would change');
      } else if (gw.step === 4) {
        html = btn(`Apply ${plannedRows().length}`, 'danger', 'apply', 'Write to Canvas. You are asked first.');
      } else {
        html = (E.restore ? btn('Restore previous bodies', 'danger', 'restore', 'Put the saved originals back. You are asked first.') : '')
          + btn('Back to list', '', 'back');
      }
      foot.innerHTML = html;
      foot.querySelectorAll('[data-act]').forEach(b => { b.onclick = () => act(b.dataset.act); });
      const disable = (id, why) => { const b = foot.querySelector(`[data-act="${id}"]`); if (b) { b.disabled = true; b.title = why; } };
      if (gw.step === 1 && !n) disable('fetch', 'Nothing to fetch: refresh the list or pick items');
      if (gw.step === 2 && !n) disable('fixes', 'Nothing scanned yet');
      if (gw.step === 4 && !plannedRows().length) disable('apply', 'Nothing is planned');
      if (gw.step <= 2 && !E.fetch) disable('fetch', 'This kind has no fetch endpoint yet');
    }

    function act(id) {
      const keys = keysFor();
      const body = { keys, kind: spec.kind };
      const after = step => res => { refresh().then(() => goto(step)); return res; };
      if (id === 'list') runMaybeJob(`Listing ${spec.label || 'items'}`, () => api(E.list, { body: {} }), after(1), { autoClose: true });
      else if (id === 'fetch') runMaybeJob(`Fetching and scanning ${keys.length} ${spec.label || 'items'}`, () => api(E.fetch, { body }), after(2));
      else if (id === 'describe') runMaybeJob(spec.describeLabel || 'Describing images with Claude', () => api(E.describe, { body }), after(2));
      else if (id === 'fixes') runMaybeJob('Computing fixes', () => api(E.fixes, { body }), after(3), { autoClose: true });
      else if (id === 'dry') runMaybeJob('Dry run', () => api(E.push, { body: { ...body, apply: false } }),
        res => { gw.plan = res || null; after(4)(res); }, { autoClose: true });
      else if (id === 'apply') runMaybeJob(spec.confirmTitle || `Applying ${plannedRows().length} changes`,
        token => api(E.push, { body: { ...body, apply: true, confirm: token } }),
        res => { gw.result = res || null; announce('Applied to Canvas'); after(5)(res); },
        { title: spec.confirmTitle || 'Send this to Canvas?', verb: 'Yes, apply it' });
      else if (id === 'restore') runMaybeJob('Restoring previous bodies',
        token => api(E.restore, { body: { ...body, confirm: token } }),
        res => { gw.result = res || null; after(5)(res); },
        { title: 'Restore the previous bodies?', verb: 'Yes, restore them' });
      else if (id === 'back') goto(1);
    }

    async function refresh() {
      if (!E.state) return;
      try {
        const state = await api(E.state);
        gw.error = null;
        gw.state = state || {};
        gw.items = gwItems(gw.state);
        const needs = Array.isArray(gw.state.needs) ? gw.state.needs : [];
        needsBox.innerHTML = '';
        if (needs.length) needCards(needs, needsBox);
        if (!gw.touched && gw.state.step != null) {
          const s = typeof gw.state.step === 'number' ? gw.state.step : STEP_OF[String(gw.state.step).toLowerCase()];
          if (s >= 1 && s <= 5) gw.step = s;
        }
        const print = JSON.stringify([gw.items, gw.state.summary]);
        if (print !== gw.print) { gw.print = print; paintSteps(); paintPane(); }
        else paintFoot();
      } catch (err) {
        gw.error = firstLine(err.message);
        if (!gw.items.length) { paintSteps(); paintPane(); }
        summary.textContent = 'Could not read the state: ' + gw.error;
      }
    }

    paintSteps();
    paintPane();
    refresh();
    gw.stop = poll(refresh, spec.pollMs || 4000);
    onLeave(gw.stop);
    return { refresh, goto, stop: gw.stop, get state() { return gw.state; }, get picked() { return [...gw.picked]; } };
  }

  /* ------------------------------------------------------------ AltGrid */
  /* A card per figure: the picture, a kind pill, where it sits, an editable
     description with a 110-character counter, who wrote it, and a Decorative
     tick. Two more sections whenever they are non-empty: figures with no
     picture to show, and ones left alone because their alt text is only a
     filename (read-only until Replace anyway).

     items = [{key, picture, kind, context, alt, source, decorative, page,
               noPicture, leftAlone}]
     opts  = {onChange(item, patch), onDescribeAll, noPicture:[...], leftAlone:[...],
              title, limit} */
  const ALT_LIMIT = 110;
  const SOURCE_WORDS = { claude: 'Claude wrote', you: 'you edited', user: 'you edited', human: 'you edited',
    placeholder: 'placeholder', file: 'from the file', filename: 'filename only', none: 'empty' };

  function AltGrid(host, items, opts = {}) {
    host = el(host);
    if (!host) return;
    items = Array.isArray(items) ? items : [];
    const limit = opts.limit || ALT_LIMIT;
    const noPic = (opts.noPicture || []).concat(items.filter(it => it.noPicture));
    const alone = (opts.leftAlone || []).concat(items.filter(it => it.leftAlone));
    const main = items.filter(it => !it.noPicture && !it.leftAlone);
    const timers = new Map();

    const card = (it, i, mode) => {
      const key = String(it.key != null ? it.key : i);
      const alt = it.alt == null ? '' : String(it.alt);
      const ro = mode === 'alone' && !it.replacing;
      const src = SOURCE_WORDS[String(it.source || '').toLowerCase()] || (it.source ? String(it.source) : 'empty');
      const over = alt.length > limit;
      return `<figure class="altCard ${mode}" data-key="${esc(key)}">
        ${mode === 'nopic'
          ? '<div class="altPic none" aria-hidden="true">No picture available</div>'
          : `<div class="altPic"><img src="${esc(it.picture || it.src || '')}" alt="Figure ${i + 1}${it.page ? ' on page ' + esc(it.page) : ''}" loading="lazy"></div>`}
        <figcaption class="altBody">
          <div class="altTop">${pill(it.kind || 'image', 'kind-' + esc(String(it.kind || 'image').toLowerCase()))}
            ${it.page ? `<span class="muted">page ${esc(it.page)}</span>` : ''}
            ${it.file ? `<span class="muted">${esc(clamp(it.file, 40))}</span>` : ''}</div>
          ${it.context ? `<div class="altCtx">${esc(clamp(it.context, 220))}</div>` : ''}
          <label class="srOnly" for="alt-${esc(key)}">Description for figure ${i + 1}</label>
          <textarea class="altText" id="alt-${esc(key)}" rows="2" data-alt="${esc(key)}"
            ${ro || it.decorative ? 'disabled' : ''} aria-describedby="altc-${esc(key)}"
            placeholder="${it.decorative ? 'Decorative: read as nothing' : 'What the picture shows, in one sentence'}">${esc(alt)}</textarea>
          <div class="altMeta">
            <span class="altCount ${over ? 'over' : ''}" id="altc-${esc(key)}">${alt.length}/${limit}${over ? ' (over)' : ''}</span>
            ${pill(src, 'src-' + esc(String(it.source || 'none').toLowerCase()))}
            <label class="tick altDeco"><input type="checkbox" data-deco="${esc(key)}" ${it.decorative ? 'checked' : ''} ${ro ? 'disabled' : ''}> Decorative</label>
            ${ro ? `<button class="btn sm" type="button" data-replace="${esc(key)}" title="Edit this description even though it was left alone">Replace anyway</button>` : ''}
          </div>
        </figcaption>
      </figure>`;
    };

    const section = (title, list, mode, note) => !list.length ? '' : `<section class="altSection">
      <h3 class="gwH">${esc(title)} <span class="muted">(${list.length})</span></h3>
      ${note ? `<p class="muted altNote">${esc(note)}</p>` : ''}
      <div class="altGrid">${list.map((it, i) => card(it, i, mode)).join('')}</div></section>`;

    const total = main.length + noPic.length + alone.length;
    host.innerHTML = `<div class="altBar">
        <span class="altSum">${total} figure${total === 1 ? '' : 's'}${opts.title ? ' · ' + esc(opts.title) : ''}
          · ${items.filter(it => !it.alt && !it.decorative).length} without a description</span>
        <span class="spacer"></span>
        ${opts.onDescribeAll ? '<button class="btn ai" type="button" id="altDescribeAll">Describe with Claude</button>' : ''}
      </div>
      ${total ? '' : '<div class="empty"><p>No figures found. Nothing has been pushed.</p></div>'}
      ${main.length ? `<div class="altGrid">${main.map((it, i) => card(it, i, 'main')).join('')}</div>` : ''}
      ${section('No picture available', noPic, 'nopic', 'The description can still be written from the context.')}
      ${section('Left alone', alone, 'alone', 'Their alt text is only a filename. They are not changed unless you replace them.')}`;

    const find = key => items.find((it, i) => String(it.key != null ? it.key : i) === key)
      || noPic.find(it => String(it.key) === key) || alone.find(it => String(it.key) === key);
    const change = (it, patch) => { Object.assign(it, patch); if (opts.onChange) opts.onChange(it, patch); };

    host.querySelectorAll('[data-alt]').forEach(ta => {
      const key = ta.dataset.alt;
      const counter = host.querySelector(`#altc-${CSS.escape(key)}`);
      ta.oninput = () => {
        const n = ta.value.length;
        if (counter) {
          counter.textContent = `${n}/${limit}${n > limit ? ' (over)' : ''}`;
          counter.classList.toggle('over', n > limit);
        }
        clearTimeout(timers.get(key));
        timers.set(key, setTimeout(() => { const it = find(key); if (it) change(it, { alt: ta.value, source: 'you' }); }, 350));
      };
      ta.onblur = () => {
        clearTimeout(timers.get(key));
        const it = find(key);
        if (it && it.alt !== ta.value) change(it, { alt: ta.value, source: 'you' });
      };
    });
    host.querySelectorAll('[data-deco]').forEach(cb => {
      cb.onchange = () => {
        const it = find(cb.dataset.deco);
        if (!it) return;
        change(it, { decorative: cb.checked });
        const ta = host.querySelector(`[data-alt="${CSS.escape(cb.dataset.deco)}"]`);
        if (ta) { ta.disabled = cb.checked; ta.placeholder = cb.checked ? 'Decorative: read as nothing' : 'What the picture shows, in one sentence'; }
      };
    });
    host.querySelectorAll('[data-replace]').forEach(b => {
      b.onclick = () => {
        const it = find(b.dataset.replace);
        if (!it) return;
        it.replacing = true;
        change(it, { replace: true });
        AltGrid(host, items, opts);
        const ta = host.querySelector(`[data-alt="${CSS.escape(b.dataset.replace)}"]`);
        if (ta) ta.focus();
      };
    });
    const all = host.querySelector('#altDescribeAll');
    if (all) all.onclick = () => opts.onDescribeAll(items);
  }

  Object.assign(window, {
    areaHead, areaTabs, renderGateway, AltGrid, needCards, emptyState, renderLedger,
    statStrip, pill, lanePill, verdictPill, statePill, poll, runMaybeJob, fmtBytes,
  });
  Object.assign(window.Studio || (window.Studio = {}), {
    areaHead, areaTabs, renderGateway, AltGrid, needCards, emptyState, renderLedger,
    statStrip, pill, lanePill, verdictPill, statePill, poll, runMaybeJob,
  });
})();
