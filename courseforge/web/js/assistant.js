/* assistant.js: the Assistant panel. One conversation per course, in plain
   words, with every change to the live course stopping at an Allow / Deny card
   that shows the exact command.

   The transcript is built from GET .../events?since=<seq>, polled at 700 ms
   while this view is open and stopped in onLeave. Nothing here decides what is
   safe: the gate in the Claude session does that, and this page shows the
   gate's own sentence rather than the model's description of it. */
(function () {
  'use strict';

  S.assistant = S.assistant || {};
  const asstBase = cid => '/assistant/' + encodeURIComponent(cid);
  const asstMem = () => S.assistant;
  const asstClamp = (text, n) => {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  };
  const asstClock = secs => {
    const s = Math.max(0, Math.round(secs || 0));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  };

  /* ------------------------------------------------------------- the area */
  async function openAssistant(courseId, rest) {
    const mem = asstMem();
    mem.courseId = String(courseId);
    mem.seq = 0;
    mem.pending = [];
    mem.answering = null;
    showView('area');
    crumbs([
      { label: 'Courses', href: '#/' },
      { label: (S.course && S.course.name) || 'Course', href: '#/c/' + courseId },
      { label: 'Assistant' },
    ]);
    $('#headerActions').innerHTML = '';
    areaHead('Assistant', 'Ask for work in plain words. Every change to the live course '
      + 'stops here for your Allow, with the exact command shown.');
    areaTabs([], null, null);

    const body = $('#areaBody');
    body.innerHTML = `<div class="asst">
      <div class="asstMain">
        <div class="asstChips" id="asstChips"></div>
        <div class="asstLog" id="asstLog" role="log" aria-live="polite" aria-label="The conversation"></div>
        <div class="asstCompose">
          <label class="srOnly" for="asstText">What do you want done?</label>
          <textarea id="asstText" placeholder="Tell it what to do. Enter sends; Shift and Enter make a new line."></textarea>
          <div class="col">
            <button class="btn ai" type="button" id="asstSend">Send</button>
            <button class="btn" type="button" id="asstStop" disabled title="Nothing is running">Stop</button>
            <button class="btn" type="button" id="asstNew">New conversation</button>
          </div>
        </div>
        <div class="asstFoot">
          <span id="asstState">Not started.</span>
          <span class="spacer"></span>
          <span class="hint">Nothing reaches Canvas without an Allow.</span>
        </div>
      </div>
      <aside class="asstRail" aria-label="This conversation">
        <div class="card"><div class="t">This conversation</div><dl class="asstFacts" id="asstFacts"></dl></div>
        <div class="card"><div class="t">What it changed</div><div id="asstLedger"></div></div>
      </aside>
    </div>`;

    $('#asstSend').onclick = () => asstSend(courseId);
    $('#asstStop').onclick = () => asstStop(courseId);
    $('#asstNew').onclick = () => asstNew(courseId);
    const text = $('#asstText');
    text.onkeydown = ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.altKey && !ev.ctrlKey && !ev.metaKey) {
        ev.preventDefault();
        asstSend(courseId);
      }
    };

    let state = null;
    try { state = await api(asstBase(courseId) + '/state'); }
    catch (err) {
      $('#asstLog').innerHTML = `<div class="asstNotice err">The Assistant is not available on this
        server: ${esc(firstLine(err.message))}</div>`;
      return;
    }
    if (String(mem.courseId) !== String(courseId)) return;
    mem.headlines = state.headlines || {};
    asstChips($('#asstChips'), state);
    asstFacts(state);
    asstBoot(state);
    asstLedger(courseId);

    /* One poller for this view, stopped on the way out. A question still
       waiting when the person leaves is said out loud, because the only other
       place it shows is a card they have just walked away from. */
    const stop = poll(() => asstTick(courseId), 700);
    onLeave(() => {
      stop();
      if ((asstMem().pending || []).length) {
        setStatus('a change is waiting for your answer', 'err');
      }
    });
  }

  /* Quick jobs fill the box; they never send. The person reads what they are
     about to ask for, and edits it, before anything starts. */
  function asstChips(host, state) {
    const jobs = state.quick_jobs || [];
    if (!host || !jobs.length) { if (host) host.innerHTML = ''; return; }
    host.innerHTML = '<span class="lbl">Start with:</span>' + jobs.map((j, i) =>
      `<button class="chip" type="button" data-job="${i}" title="Puts this in the box. It does not send it.">${esc(j.label)}</button>`).join('');
    host.querySelectorAll('[data-job]').forEach(btn => {
      btn.onclick = () => {
        const job = jobs[+btn.dataset.job];
        const box = $('#asstText');
        if (!job || !box) return;
        box.value = job.prompt;
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
        setStatus('put in the box, not sent', 'ok');
      };
    });
  }

  function asstFacts(state) {
    const host = $('#asstFacts');
    if (!host) return;
    const rows = [
      ['Course', state.course_name || state.course_id],
      ['Model', state.model || 'Claude Code default'],
      ['Session', state.alive ? (state.busy ? 'working' : 'open, waiting for you') : 'not running'],
      ['Started', state.started ? fmtDate(state.started) : 'not yet'],
      ['Last used', state.last_used ? fmtDate(state.last_used) : '—'],
      ['Working folder', state.workspace || '', true],
    ];
    host.innerHTML = rows.map(([k, v, mono]) => `<dt>${esc(k)}</dt>
      <dd${mono ? ' class="mono"' : ''}>${esc(v || '—')}</dd>`).join('');
    if (state.claude === false) {
      banner('err', '<b>Claude Code is not on this PC.</b> The Assistant needs the '
        + '<code>claude</code> command; install it and reopen this page.');
    }
  }

  async function asstLedger(courseId) {
    const host = $('#asstLedger');
    if (!host) return;
    let rows = [];
    try { rows = (await api('/courses/' + encodeURIComponent(courseId) + '/ledger?limit=8')).rows || []; }
    catch (_) { /* the rail is a courtesy; the conversation is the point */ }
    if (!host.isConnected) return;
    if (!rows.length) {
      host.innerHTML = '<p class="ledgerEmpty">Nothing has been written to Canvas from here yet.</p>';
      return;
    }
    host.innerHTML = `<ul class="ledger">${rows.map(r => `<li>
      <span class="when">${esc(fmtDate(r.at) || '')}</span>
      ${esc(r.sentence || '')}
      ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">open</a>` : ''}</li>`).join('')}</ul>`;
  }

  /* What the ring has forgotten, written to disk at the time: shown once, at
     the top, so a restarted server does not look like a lost conversation. */
  function asstBoot(state) {
    const log = $('#asstLog');
    if (!log) return;
    log.innerHTML = '';
    if (state.last_error) {
      log.insertAdjacentHTML('beforeend', `<div class="asstNotice err">${esc(state.last_error)}</div>`);
    }
    if (state.history) {
      log.insertAdjacentHTML('beforeend', `<div class="asstHistory"><div class="hdr">Earlier</div>${
        esc(state.history.trim())}</div>`);
    }
    if (!state.history && !state.seq) {
      log.insertAdjacentHTML('beforeend', `<div class="asstEmpty">Nothing has been asked for in this
        course yet. Say what you want done — "make this course ADA compliant", "add a study
        guide for the midterm to week 8" — and it works in a folder of its own. Every change to
        the live course stops for your Allow first, and the exact command is shown before you
        answer.</div>`);
    }
    asstBusy(state);
  }

  function asstBusy(state) {
    const send = $('#asstSend'), stop = $('#asstStop'), line = $('#asstState');
    if (!send) return;
    const busy = !!state.busy;
    send.disabled = busy;
    send.title = busy ? 'Claude is still working on the last message' : '';
    stop.disabled = !busy;
    stop.title = busy ? 'Stop this turn. The conversation is kept.' : 'Nothing is running';
    if (line) {
      line.className = busy ? 'working' : '';
      line.textContent = busy ? 'Working…'
        : state.alive ? 'Waiting for you.' : 'Not started.';
    }
  }

  /* ----------------------------------------------------------- the poller */
  async function asstTick(courseId) {
    const mem = asstMem();
    if (String(mem.courseId) !== String(courseId)) return;
    let data;
    try { data = await api(asstBase(courseId) + '/events?since=' + (mem.seq || 0)); }
    catch (_) { return; }                       // the next tick tries again
    if (String(mem.courseId) !== String(courseId) || !$('#asstLog')) return;
    if (data.gap) {                             // the ring forgot the middle
      mem.seq = 0;
      const log = $('#asstLog');
      if (log) log.innerHTML = '';
      mem.live = null;
      return;
    }
    // Only what is genuinely new. The server filters by `since` already; this
    // is here so a repeat can never print the same line of the conversation
    // twice, which is the one thing a transcript must not do.
    (data.events || []).forEach(ev => {
      if (ev.seq != null && ev.seq <= (mem.seq || 0)) return;
      if (ev.seq != null) mem.seq = ev.seq;
      asstAppend(ev);
    });
    if (data.seq != null && data.seq > (mem.seq || 0)) mem.seq = data.seq;
    mem.pending = data.pending || [];
    asstBusy(data);
    asstPermission(courseId);
  }

  /* ------------------------------------------------------- the transcript */
  function asstAppend(ev) {
    const log = $('#asstLog');
    if (!log) return;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    const mem = asstMem();
    const empty = log.querySelector('.asstEmpty');
    if (empty && ev.kind !== 'init') empty.remove();

    if (ev.kind === 'user') {
      mem.live = null;
      asstAdd(log, `<div class="asstMsg you">${esc(ev.text || '')}</div>`);
    } else if (ev.kind === 'text') {
      if (!mem.live || !mem.live.isConnected) {
        mem.live = asstAdd(log, '<div class="asstMsg claude live"></div>');
      }
      mem.live.textContent += ev.text || '';
    } else if (ev.kind === 'text_end') {
      if (mem.live) mem.live.classList.remove('live');
      mem.live = null;
    } else if (ev.kind === 'tool') {
      mem.live = null;
      asstTool(log, ev);
    } else if (ev.kind === 'tool_result') {
      asstToolResult(log, ev);
    } else if (ev.kind === 'permission_answered') {
      mem.live = null;
      const word = ev.auto ? 'No answer, so it was denied'
        : ev.decision === 'allow' ? 'You allowed it' : 'You denied it';
      asstAdd(log, `<div class="asstDecision ${esc(ev.decision === 'allow' ? 'allow' : 'deny')}">
        <b>${esc(word)}</b> ${esc(asstClamp(ev.what || '', 120))}</div>`);
      if (ev.decision === 'allow') asstLedger(asstMem().courseId);
    } else if (ev.kind === 'notice') {
      asstAdd(log, `<div class="asstNotice">${esc(ev.text || '')}</div>`);
    } else if (ev.kind === 'result' && ev.error) {
      asstAdd(log, `<div class="asstNotice err">Claude stopped with an error.
        ${esc(asstClamp(ev.text || '', 200))}</div>`);
    } else if (ev.kind === 'exit' && ev.text) {
      asstAdd(log, `<div class="asstNotice err">${esc(ev.text)}</div>`);
    }
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function asstAdd(log, html) {
    log.insertAdjacentHTML('beforeend', html);
    return log.lastElementChild;
  }

  /* Tool rows are collapsed, and a run of them is one row that says how many.
     The work is not the point; what it changed is, and that has its own card. */
  function asstTool(log, ev) {
    let group = log.lastElementChild;
    if (!group || !group.classList || !group.classList.contains('asstTools')) {
      group = asstAdd(log, `<details class="asstTools"><summary>
        <span class="count">1</span> <span class="what">step</span></summary></details>`);
    }
    const asking = ev.decision !== 'allow';
    group.insertAdjacentHTML('beforeend', `<div class="asstTool${asking ? ' asking' : ''}"
        data-tool="${esc(ev.id || '')}">
        <span class="name">${esc(ev.name || 'tool')}</span>
        <span class="sum">${esc(ev.summary || ev.what || '')}</span>
        ${ev.detail ? `<span class="det">${esc(asstClamp(ev.detail, 300))}</span>` : ''}
      </div>`);
    const n = group.querySelectorAll('.asstTool').length;
    group.querySelector('.count').textContent = String(n);
    group.querySelector('.what').textContent = n === 1 ? 'step' : 'steps';
  }

  function asstToolResult(log, ev) {
    if (!ev.error) return;                      // a step that worked says nothing
    const row = log.querySelector(`.asstTool[data-tool="${CSS.escape(String(ev.id || ''))}"]`);
    if (!row) return;
    row.insertAdjacentHTML('beforeend', `<span class="err">${esc(asstClamp(ev.text || 'it failed', 200))}</span>`);
    const group = row.closest('.asstTools');
    if (group) group.open = true;               // an error is not something to fold away
  }

  /* ------------------------------------------------------ Allow  /  Deny */
  /* One card at a time, in the transcript where the question was asked. Deny
     has the focus, Allow is the dangerous one, and the countdown says what
     happens if nobody answers: it is denied. */
  function asstPermission(courseId) {
    const mem = asstMem();
    const log = $('#asstLog');
    if (!log) return;
    const pending = mem.pending || [];
    const card = log.querySelector('.asstPerm');
    const want = pending[0] || null;

    if (!want) {
      if (card) card.remove();
      return;
    }
    if (card && card.dataset.req === want.request_id) {
      const left = want.expires_at - (Date.now() / 1000);
      const clock = card.querySelector('.countdown b');
      if (clock) {
        clock.textContent = left > 0 ? asstClock(left) : 'over';
        clock.parentElement.classList.toggle('soon', left < 60);
      }
      return;
    }
    if (card) card.remove();

    const headline = (mem.headlines || {})[want.kind]
      || 'Claude wants to use a tool that may change something';
    log.insertAdjacentHTML('beforeend', `<div class="asstPerm" data-req="${esc(want.request_id)}"
        role="group" aria-label="A change is waiting for your answer">
      <h3>${esc(headline)}</h3>
      <p class="what"><b>What:</b> ${esc(want.what || want.summary || 'it did not say')}</p>
      ${want.description ? `<p class="says">Claude describes it as: ${esc(want.description)}</p>` : ''}
      ${want.why ? `<p class="why">Why it asks: ${esc(want.why)}</p>` : ''}
      <pre class="cmd">${esc(want.command || '')}</pre>
      <div class="row">
        <button class="btn" type="button" data-deny="1">Deny</button>
        <button class="btn danger" type="button" data-allow="1">Allow it once</button>
        <span class="countdown">No answer in <b>${esc(asstClock(want.expires_at - Date.now() / 1000))}</b>
          means Deny</span>
      </div>
    </div>`);
    const fresh = log.querySelector('.asstPerm');
    fresh.querySelector('[data-deny]').onclick = () => asstAnswer(courseId, want.request_id, 'deny');
    fresh.querySelector('[data-allow]').onclick = () => asstAnswer(courseId, want.request_id, 'allow');
    fresh.querySelector('[data-deny]').focus();
    announce(headline);
    log.scrollTop = log.scrollHeight;
  }

  function asstAnswer(courseId, requestId, decision) {
    const mem = asstMem();
    if (mem.answering === requestId) return;
    mem.answering = requestId;
    const card = $('#asstLog') && $('#asstLog').querySelector('.asstPerm');
    if (card) card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    api(asstBase(courseId) + '/answer', { body: { request_id: requestId, decision } })
      .then(() => {
        mem.pending = (mem.pending || []).filter(p => p.request_id !== requestId);
        setStatus(decision === 'allow' ? 'allowed, once' : 'denied', decision === 'allow' ? 'ok' : 'err');
        asstPermission(courseId);
      })
      .catch(err => {
        setStatus('could not answer: ' + firstLine(err.message), 'err');
        if (card) card.querySelectorAll('button').forEach(b => { b.disabled = false; });
      })
      .finally(() => { mem.answering = null; });
  }

  /* ------------------------------------------------------------- the verbs */
  function asstSend(courseId) {
    const box = $('#asstText');
    if (!box) return;
    const text = (box.value || '').trim();
    if (!text) { setStatus('type something to send first', 'err'); box.focus(); return; }
    const send = $('#asstSend');
    send.disabled = true;
    api(asstBase(courseId) + '/send', { body: { text } })
      .then(res => {
        box.value = '';
        box.focus();
        const mem = asstMem();
        if (res && res.seq != null && mem.seq > res.seq) mem.seq = 0;
        setStatus('sent', 'ok');
      })
      .catch(err => {
        send.disabled = false;
        setStatus(firstLine(err.message), 'err');
        banner('err', '<b>That message did not go.</b> ' + esc(err.message));
      });
  }

  function asstStop(courseId) {
    api(asstBase(courseId) + '/stop', { body: {} })
      .then(() => setStatus('stopped; the conversation is kept', 'ok'))
      .catch(err => setStatus('could not stop: ' + firstLine(err.message), 'err'));
  }

  function asstNew(courseId) {
    askConfirm({
      summary: 'Start a new conversation. Claude forgets this one; the files it made in the '
        + 'working folder stay, and nothing in Canvas changes.',
    }, () => {
      api(asstBase(courseId) + '/new', { body: {} }).then(() => {
        const mem = asstMem();
        mem.seq = 0;
        mem.live = null;
        mem.pending = [];
        const log = $('#asstLog');
        if (log) log.innerHTML = '';
        setStatus('new conversation', 'ok');
      }).catch(err => setStatus('could not start a new one: ' + firstLine(err.message), 'err'));
    }, { title: 'Forget this conversation?', verb: 'Yes, start fresh',
      note: 'Nothing in Canvas changes either way.' });
  }

  /* ------------------------------------------------------------ register */
  window.openAssistant = openAssistant;
  registerArea({
    id: 'assistant', label: 'Assistant', zone: 'ai', open: openAssistant,
    badge: hub => (hub && hub.assistant && hub.assistant.badge != null) ? hub.assistant.badge : null,
  });
  Object.assign(window.Studio || (window.Studio = {}), { openAssistant });
})();
