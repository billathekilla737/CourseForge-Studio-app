/* inbox.js: the Canvas Inbox.

   The chip in the header is painted once at boot and refreshed on a slow
   timer, because mail waiting is true wherever you are standing and every
   view clears #headerActions.

   The screen itself is a list of threads and one open thread beside it. Read
   with Claude fills in what the student is asking and drafts a reply into a
   box. The box is the only thing that can be sent, one thread at a time, and
   sending goes through the server's confirm gate like every other Canvas
   write: refused once with a sentence, sent on the second pass. There is no
   send-all and no rule that sends by itself. */
(function () {
  'use strict';

  S.inbox = S.inbox || { scope: '', threads: [], open: null, read: {}, drafts: {} };
  const mem = () => S.inbox;
  const CHIP_MS = 120000;

  const SCOPES = [
    { id: '', label: 'Inbox' },
    { id: 'unread', label: 'Unread' },
    { id: 'archived', label: 'Archived' },
    { id: 'sent', label: 'Sent' },
  ];

  /* ------------------------------------------------------------- the chip */
  /* A number in the corner is never worth a banner, so a failed count says
     nothing at all and tries again on the next tick. */
  async function inboxChip() {
    const host = $('#inboxChip');
    if (!host) return;
    let info = null;
    try { info = await api('/inbox/unread'); } catch (_) { info = null; }
    if (!info || info.unread == null) { host.innerHTML = ''; return; }
    const n = +info.unread || 0;
    host.innerHTML = `<a class="btn sm inboxBtn${n ? ' lit' : ''}" href="#/inbox"
      title="${n ? n + ' unread in your Canvas Inbox' : 'Your Canvas Inbox'}">Inbox${
      n ? `<span class="n">${esc(n)}</span>` : ''}</a>`;
  }

  function startChip() {
    inboxChip();
    if (mem().chipTimer) clearInterval(mem().chipTimer);
    mem().chipTimer = setInterval(inboxChip, CHIP_MS);
  }

  /* ------------------------------------------------------------ the screen */
  async function openInbox(threadId) {
    showView('inbox');
    crumbs([{ label: 'Courses', href: '#/' }, { label: 'Inbox' }]);
    $('#headerActions').innerHTML =
      '<button class="btn" id="ibRefresh" title="Read the inbox from Canvas again">Refresh</button>';
    $('#ibRefresh').onclick = () => openInbox(mem().open);

    const host = $('#viewInbox');
    host.innerHTML = `<div class="sectionHead">
        <h2>Canvas Inbox</h2>
        <span class="hint" id="ibHint">Reading…</span>
        <span class="spacer"></span>
        <div class="chips" id="ibScopes"></div>
      </div>
      <p class="hint ibNote">Messages from students across every course. Claude reads a
        thread with the names taken out and drafts a reply; nothing is sent until you
        press Send on that reply.</p>
      <div class="ibCols">
        <div id="ibList">Loading…</div>
        <div id="ibPane"></div>
      </div>`;

    $('#ibScopes').innerHTML = SCOPES.map(s =>
      `<button class="chip" type="button" data-scope="${esc(s.id)}"
        aria-pressed="${s.id === mem().scope}">${esc(s.label)}</button>`).join('');
    $('#ibScopes').querySelectorAll('[data-scope]').forEach(b => {
      b.onclick = () => { mem().scope = b.dataset.scope; openInbox(null); };
    });

    let data;
    try {
      data = await api('/inbox?scope=' + encodeURIComponent(mem().scope));
    } catch (err) {
      $('#ibList').innerHTML = '';
      $('#ibList').appendChild(emptyState('Could not read the inbox: '
        + firstLine(err.message) + '. Nothing has been changed.'));
      $('#ibHint').textContent = '';
      return;
    }
    if (S.view !== 'inbox') return;
    mem().threads = data.threads || [];
    $('#ibHint').textContent = `${mem().threads.length} thread${
      mem().threads.length === 1 ? '' : 's'}`
      + (data.unread ? ` · ${data.unread} unread` : '');
    drawList();
    inboxChip();
    if (threadId) openThread(threadId);
    else $('#ibPane').appendChild(emptyState('Pick a thread on the left. Reading one '
      + 'never marks it read in Canvas.'));
  }

  function drawList() {
    const host = $('#ibList');
    if (!host) return;
    const rows = mem().threads;
    if (!rows.length) {
      host.innerHTML = '';
      host.appendChild(emptyState('Nothing in this view.'));
      return;
    }
    host.innerHTML = '<div class="ibList">' + rows.map(t => {
      const who = (t.with || []).map(p => p.name || p.tag).join(', ') || 'someone';
      return `<button class="ibRow${t.unread ? ' unread' : ''}${
        String(mem().open) === String(t.id) ? ' picked' : ''}" type="button"
        data-id="${esc(t.id)}">
        <span class="ibTop"><span class="ibWho">${esc(who)}</span>
          <span class="ibAgo">${esc(t.ago || '')}</span></span>
        <span class="ibSubj">${esc(t.subject)}</span>
        <span class="ibPrev">${esc(t.preview || '')}</span>
      </button>`;
    }).join('') + '</div>';
    host.querySelectorAll('[data-id]').forEach(b => {
      b.onclick = () => openThread(b.dataset.id);
    });
  }

  async function openThread(id) {
    mem().open = id;
    drawList();
    const pane = $('#ibPane');
    pane.innerHTML = '<p class="hint">Opening…</p>';
    let t;
    try { t = await api('/inbox/' + encodeURIComponent(id)); }
    catch (err) {
      pane.innerHTML = '';
      pane.appendChild(emptyState('Could not open that thread: ' + firstLine(err.message)));
      return;
    }
    if (String(mem().open) !== String(id)) return;
    drawThread(t);
  }

  function drawThread(t) {
    const pane = $('#ibPane');
    const who = (t.with || []).map(p => p.name || p.tag).join(', ');
    const read = mem().read[t.id];
    pane.innerHTML = `<div class="ibThread">
      <div class="ibHead">
        <h3>${esc(t.subject)}</h3>
        <p class="hint">${esc(who)}${t.course_id ? ' · course ' + esc(t.course_id) : ''}
          · ${esc(t.ago || '')}</p>
      </div>
      <div id="ibInsight"></div>
      <div class="ibMsgs">${(t.transcript || []).map(m => `
        <div class="ibMsg${m.from === 'you' ? ' mine' : ''}">
          <div class="ibFrom">${esc(m.from === 'you' ? 'You' : nameFor(t, m.from))}</div>
          <div class="ibBody">${esc(m.body)}</div>
        </div>`).join('')}</div>
      <div class="ibVerbs">
        <button class="btn ai" type="button" id="ibRead">Read it with Claude</button>
        <a class="btn" href="${esc(canvasLink(t))}" target="_blank" rel="noopener">Open in Canvas</a>
      </div>
      <div id="ibDraft"></div>`;
    $('#ibRead').onclick = () => readThread(t);
    if (read) drawInsight(t, read);
    if (mem().drafts[t.id] != null) drawDraft(t, mem().drafts[t.id]);
  }

  /* The transcript says Student-14; the header says who that is. Both are
     true and the pairing is the point: you read a name, Anthropic read a tag. */
  function nameFor(t, tag) {
    const hit = (t.with || []).find(p => p.tag === tag);
    return hit ? (hit.name || tag) : tag;
  }

  function canvasLink(t) {
    const base = (S.health && S.health.base_url) || '';
    return base ? base + '/conversations/' + encodeURIComponent(t.id) : '#';
  }

  function readThread(t) {
    runJob('Reading the thread', () => api('/inbox/' + encodeURIComponent(t.id) + '/read',
      { body: {} }), out => {
      if (!out) return;
      mem().read[t.id] = out;
      mem().drafts[t.id] = out.draft || '';
      drawInsight(t, out);
      drawDraft(t, out.draft || '');
      setStatus('drafted; nothing sent', 'ok');
    });
  }

  function drawInsight(t, out) {
    const host = $('#ibInsight');
    if (!host) return;
    const urgency = String(out.urgency || 'routine');
    host.innerHTML = `<div class="ibInsight${out.needs_you ? ' needsYou' : ''}">
      <div class="ibAsk"><b>Asking:</b> ${esc(out.asking || 'it did not say')}</div>
      <div class="ibTags">
        ${pill(out.kind || 'other', 'ai')}
        ${pill(urgency === 'routine' ? 'routine' : urgency, urgency === 'routine' ? '' : 'warn')}
        ${out.needs_you ? pill('needs your judgement', 'warn') : ''}
      </div>
      ${out.needs_you && out.why ? `<p class="ibWhy">${esc(out.why)}</p>` : ''}
    </div>`;
  }

  function drawDraft(t, text) {
    const host = $('#ibDraft');
    if (!host) return;
    host.innerHTML = `<div class="ibDraftBox">
      <label class="srOnly" for="ibReply">Your reply</label>
      <textarea id="ibReply" rows="6"
        placeholder="Nothing is sent until you press Send.">${esc(text || '')}</textarea>
      <div class="row">
        <button class="btn primary" type="button" id="ibSend">Send this reply</button>
        <button class="btn" type="button" id="ibRedo">Draft it again</button>
        <button class="btn" type="button" id="ibClear">Discard</button>
        <span class="spacer"></span>
        <span class="hint">Goes to ${esc((t.with || []).map(p => p.name || p.tag).join(', '))}
          as you, from your Canvas account.</span>
      </div>
    </div>`;
    const box = $('#ibReply');
    box.oninput = () => { mem().drafts[t.id] = box.value; };
    $('#ibRedo').onclick = () => readThread(t);
    $('#ibClear').onclick = () => {
      delete mem().drafts[t.id];
      host.innerHTML = '';
      setStatus('draft discarded; nothing was sent', 'ok');
    };
    $('#ibSend').onclick = () => {
      const body = (box.value || '').trim();
      if (!body) { setStatus('there is nothing in the box to send', 'err'); box.focus(); return; }
      runJobConfirmed('Sending the reply',
        (token) => api('/inbox/' + encodeURIComponent(t.id) + '/reply',
          { body: { body, confirm: token } }),
        out => {
          if (!out) return;
          delete mem().drafts[t.id];
          delete mem().read[t.id];
          setStatus('sent to ' + (out.sent_to || []).join(', '), 'ok');
          openInbox(t.id);
        },
        { title: 'Send this to a student?',
          verb: 'Yes, send it',
          note: 'This goes to the student from your Canvas account, under your name. '
              + 'A sent message cannot be taken back.' });
    };
  }

  window.openInbox = openInbox;
  window.inboxChip = inboxChip;
  Object.assign(window.Studio || (window.Studio = {}), { openInbox, startChip });
  document.addEventListener('studio:booted', startChip);
})();
