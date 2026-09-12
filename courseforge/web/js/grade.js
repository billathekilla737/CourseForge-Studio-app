/* CourseForge Studio - the grader. Everything here moved from app.js unchanged
   apart from two breadcrumb links; the shell it relies on (S, api, the jobs and
   their dock, the confirm dialog, the router) lives in core.js. */
'use strict';


/* ------------------------------------------------- reminders for one week */
/* The tests in a single week, as the filters currently leave them. One Claude
   call each, then one review screen: the drafts are worth reading together,
   because a week of five reminders written separately is where duplicated or
   contradictory wording shows up. */
function openWeekReminders(weekKey) {
  const rows = schedRows().filter(i => i.exam && weekKey === weekKey_(i));
  if (!rows.length) { setStatus('no tests in that week', 'err'); return; }
  const monday = new Date(weekKey + 'T00:00:00');
  const host = $('#modalHost');

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Reminders for the week of ${esc(monday.toLocaleDateString([],
        { month: 'long', day: 'numeric' }))}</h3>
      <div class="sub">${rows.length} assessment${rows.length > 1 ? 's' : ''},
        from what the filters are showing. One draft each, all editable before
        anything is posted.</div>
      <ul class="weekList">${rows.map(i => `<li>
        <span class="cbadge" style="background:${esc(i.colour)}">${
          esc(i.course_label || i.course_code)}</span>
        <span class="weekName">${esc(i.name)}</span>
        <span class="k ${KIND_CLASS[i.kind_label] || 'kind-asn'}">${
          esc(i.kind_label)}</span>
        <span class="weekWhen">${esc(fmtDay(i.when))}</span></li>`).join('')}</ul>
      <label class="annLabel">Anything to add to all of them?
        <span>optional — "mention the review session Thursday"</span>
        <input type="text" id="wkExtra" autocomplete="off">
      </label>
      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="wkCancel">Cancel</button>
        <button class="btn ai" id="wkGo">Draft ${rows.length}</button>
      </div>
    </div></div>`;
  $('#wkCancel').onclick = () => { host.innerHTML = ''; };
  $('#wkExtra').focus();
  $('#wkGo').onclick = () => {
    const extra = $('#wkExtra').value;
    const targets = rows.map(i => ({ course_id: i.course_id,
                                     assignment_id: i.assignment_id }));
    runJob(`Drafting ${targets.length} reminder(s)`,
      () => api('/schedule/announce-batch', { body: { targets, extra } }),
      r => { $('#modalHost').innerHTML = ''; showBatchDrafts(r); });
  };
}

/* The week a row belongs to, by the same local rule the sections use. */
function weekKey_(item) {
  return weekKey(item.when || new Date(item.due_at));
}

function showBatchDrafts(r) {
  const host = $('#modalHost');
  const drafts = r.drafts || [];
  if (!drafts.length) {
    setStatus('nothing came back', 'err');
    return;
  }
  const card = (d, i) => `
    <div class="bdCard">
      <label class="bdPick">
        <input type="checkbox" class="bdTick" data-i="${i}" checked>
        <span class="cbadge" style="background:${esc(schedColour(d.course_id))}">${
          esc(d.course_label || '')}</span>
        <span class="bdAbout">${esc(d.name || '')}</span>
      </label>
      <input type="text" class="bdTitle" data-i="${i}" value="${esc(d.title || '')}">
      <textarea class="bdBody" data-i="${i}" rows="5">${esc(d.message || '')}</textarea>
    </div>`;

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${drafts.length} reminder${drafts.length > 1 ? 's' : ''} to look over</h3>
      <div class="sub">${r.model ? 'drafted by ' + esc(r.model) : ''}${r.cost_usd
        ? ' · $' + Number(r.cost_usd).toFixed(3) : ''} · untick any you do not
        want, edit the rest</div>
      ${(r.failed || []).length ? `<div class="callout bad">${r.failed.length}
        could not be drafted: ${r.failed.map(f => esc(f.error)).join('; ')}</div>` : ''}
      <div class="bdList">${drafts.map(card).join('')}</div>
      <div class="callout">${r.writes_enabled
        ? 'Posting sends each ticked announcement to its own course. You will be '
          + 'asked to confirm once more before anything goes out.'
        : 'Canvas writes are locked off in config.json, so <b>Post</b> is '
          + 'disabled. Copy all still works.'}</div>
      <div class="foot">
        <button class="btn" id="bdCopy">Copy all</button>
        <span class="spacer"></span>
        <button class="btn" id="bdClose">Close</button>
        <button class="btn danger" id="bdPost" ${r.writes_enabled ? '' : 'disabled'}
          >Post ticked…</button>
      </div>
    </div></div>`;
  $('#bdClose').onclick = () => { host.innerHTML = ''; };

  const ticked = () => [...host.querySelectorAll('.bdTick')]
    .filter(c => c.checked)
    .map(c => {
      const i = c.dataset.i;
      return {
        op: 'announce',
        course_id: drafts[i].course_id,
        title: host.querySelector(`.bdTitle[data-i="${i}"]`).value.trim(),
        message: host.querySelector(`.bdBody[data-i="${i}"]`).value.trim(),
        _label: drafts[i].course_label,
      };
    })
    .filter(o => o.title && o.message);

  $('#bdCopy').onclick = () => {
    const text = drafts.map(d => `${d.course_label} — ${d.name}\n${d.title}\n\n`
      + `${d.message}`).join('\n\n----\n\n');
    navigator.clipboard.writeText(text)
      .then(() => setStatus(`${drafts.length} announcement(s) copied`, 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };

  $('#bdPost').onclick = () => {
    const ops = ticked();
    if (!ops.length) { setStatus('nothing ticked, or a draft is empty', 'err'); return; }
    runJobConfirmed(`Posting ${ops.length} announcement(s)`,
      token => api('/schedule/apply', { body: { dry_run: false, confirm: token,
        operations: ops.map(({ _label, ...op }) => op) } }),
      res => { $('#modalHost').innerHTML = ''; showApplyResult(res); },
      { title: `Post ${ops.length} announcement(s)?`,
        verb: 'Yes, post them',
        note: 'Each goes to its own course, and students see it straight away.' });
  };
}

/* ==========================================================================
   Test administration: settings, and who gets longer
   ==========================================================================

   One dialog, one screen at a time. Clicking a test gives a short menu of what
   can be changed; picking an item replaces the body with just that, and a back
   arrow returns. Everything on one screen was the alternative, and a quiz has
   fifteen settings across four unrelated concerns -- dates, secrecy, timing,
   what students see afterwards. Shown together they are a wall; shown one
   concern at a time each screen is four fields and a Save.

   The state below is deliberately small: which quiz, which panel, and the
   loaded quiz itself. Everything else is read from the DOM at Save time. */
const TA = { cid: null, qid: null, panel: 'menu', data: null, busy: false };

/* The menu. Each entry is a panel name, a label, the fields it owns, and a line
   that says what it currently holds so the menu is readable without opening
   anything. */
const TA_PANELS = [
  {
    key: 'dates', icon: '📅', label: 'Dates and availability',
    fields: ['unlock_at', 'due_at', 'lock_at'],
    blurb: d => {
      const due = d.settings.due_at;
      const from = d.settings.unlock_at, until = d.settings.lock_at;
      const bits = [due ? `due ${fmtDay(new Date(due))}` : 'no due date'];
      if (from) bits.push(`opens ${fmtDay(new Date(from))}`);
      if (until) bits.push(`closes ${fmtDay(new Date(until))}`);
      return bits.join(' · ');
    },
  },
  {
    key: 'access', icon: '🔒', label: 'Password and access',
    fields: ['access_code', 'ip_filter', 'published'],
    blurb: d => [
      d.has_access_code ? 'password set' : 'no password',
      d.settings.ip_filter ? `IP filter ${d.settings.ip_filter}` : 'any address',
      d.settings.published ? 'published' : 'UNPUBLISHED',
    ].join(' · '),
  },
  {
    key: 'timing', icon: '⏱', label: 'Timing and attempts',
    fields: ['time_limit', 'allowed_attempts', 'scoring_policy',
             'one_question_at_a_time', 'cant_go_back', 'shuffle_answers'],
    blurb: d => [
      d.settings.time_limit ? `${d.settings.time_limit} minutes` : 'untimed',
      d.settings.allowed_attempts === -1 ? 'unlimited attempts'
        : `${d.settings.allowed_attempts || 1} attempt${
            (d.settings.allowed_attempts || 1) === 1 ? '' : 's'}`,
      d.settings.shuffle_answers ? 'answers shuffled' : 'fixed order',
    ].join(' · '),
  },
  {
    key: 'results', icon: '👁', label: 'What students see afterwards',
    fields: ['hide_results', 'show_correct_answers', 'one_time_results'],
    blurb: d => d.settings.hide_results
      ? 'results hidden'
      : (d.settings.show_correct_answers ? 'shows correct answers'
                                         : 'score only, no answers'),
  },
  {
    key: 'accom', icon: '♿', label: 'Accommodations',
    blurb: d => {
      const n = (d.accommodated || []).length;
      if (!n) return d.roster_size
        ? 'nobody on your list is in this course'
        : 'nobody on your accommodation list yet';
      const off = d.accommodated.filter(r => !r.in_sync).length;
      return `${n} student${n === 1 ? '' : 's'}`
        + (off ? ` · ${off} not set in Canvas yet` : ' · all set in Canvas');
    },
  },
];

async function openTestAdmin(courseId, quizId, panel) {
  TA.cid = String(courseId);
  TA.qid = String(quizId);
  TA.panel = panel || 'menu';
  TA.data = null;
  taShell('Loading…', `<p class="descWait"><span class="spin"></span>reading this
    test from Canvas…</p>`, '<button class="btn" id="taClose">Close</button>');
  $('#taClose').onclick = taDismiss;
  try {
    TA.data = await api(`/quiz/${TA.cid}/${TA.qid}`);
  } catch (err) {
    taShell('Could not read this test',
      `<div class="callout bad">${esc(err.message)}</div>
       <div class="cfNote">Only classic Canvas quizzes can be edited here. New
         Quizzes and publisher tests (Pearson, ALEKS) keep their own settings.</div>`,
      '<span class="spacer"></span><button class="btn" id="taClose">Close</button>');
    $('#taClose').onclick = taDismiss;
    return;
  }
  taRender();
}

function taDismiss() { $('#modalHost').innerHTML = ''; }

function taShell(title, body, foot, sub) {
  $('#modalHost').innerHTML = `<div class="modalBack"><div class="modal wide taModal">
      <div class="taHead">
        <button class="taBack" id="taBack" hidden title="Back to the menu">‹</button>
        <div class="taTitles">
          <h3 id="taTitle">${title}</h3>
          <div class="sub" id="taSub">${sub || ''}</div>
        </div>
      </div>
      <div class="taBody" id="taBody">${body}</div>
      <div class="foot" id="taFoot">${foot || ''}</div>
    </div></div>`;
}

function taRender() {
  const d = TA.data;
  if (!d) return;
  const where = [d.course_label, d.question_count != null
    ? `${d.question_count} question${d.question_count === 1 ? '' : 's'}` : '',
    d.points != null ? `${num(d.points)} points` : ''].filter(Boolean).join(' · ');

  if (TA.panel === 'menu') {
    taShell(esc(d.title), TA_PANELS.map(p => `
      <button class="taRow" data-panel="${p.key}">
        <span class="taIcon">${p.icon}</span>
        <span class="taRowText"><b>${esc(p.label)}</b>
          <span>${esc(p.blurb(d))}</span></span>
        <span class="taChev">›</span>
      </button>`).join(''),
      `${d.url ? `<a class="btn" href="${esc(d.url)}" target="_blank"
         rel="noopener">Open in Canvas ↗</a>` : ''}
       <span class="spacer"></span>
       <span class="descFetched">read just now</span>
       <button class="btn" id="taClose">Close</button>`, esc(where));
    $('#taClose').onclick = taDismiss;
    $('#taBody').querySelectorAll('.taRow').forEach(el => {
      el.onclick = () => { TA.panel = el.dataset.panel; taRender(); };
    });
    return;
  }

  const spec = TA_PANELS.find(p => p.key === TA.panel);
  if (!spec) { TA.panel = 'menu'; return taRender(); }

  if (spec.key === 'accom') return taAccom();

  taShell(esc(spec.label), `<div class="taForm">${
    spec.fields.map(f => taField(f, d)).join('')}</div>`,
    `<span class="spacer"></span>
     <button class="btn" id="taCancel">Back</button>
     <button class="btn danger" id="taSave">Save to Canvas…</button>`,
    esc(d.title));
  $('#taBack').hidden = false;
  $('#taBack').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taCancel').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taSave').onclick = () => taSave(spec);
  taWireForm();
}

/* One row per setting. The kind decides the control; the label and kind both
   come from the server, so the editor cannot drift from what it will accept. */
function taField(name, d) {
  const meta = (d.fields || {})[name];
  if (!meta) return '';
  const value = d.settings[name];
  const id = 'ta_' + name;
  const help = TA_HELP[name] ? `<span class="taHelp">${esc(TA_HELP[name])}</span>` : '';

  if (meta.kind === 'bool') {
    return `<label class="taSwitch">
      <input type="checkbox" id="${id}" data-f="${name}" ${value ? 'checked' : ''}>
      <span class="taLbl">${esc(meta.label)}${help}</span></label>`;
  }
  if (meta.kind === 'date') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline">
        <input type="datetime-local" id="${id}" data-f="${name}"
               value="${esc(utcToLocalInput(value))}">
        <button class="taMini" data-clear="${id}">clear</button>
      </span></label>`;
  }
  if (meta.kind === 'minutes') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline"><input type="number" min="0" max="1440" id="${id}"
        data-f="${name}" value="${value == null ? '' : esc(String(value))}"
        placeholder="no limit"><span class="taUnit">minutes</span></span></label>`;
  }
  if (meta.kind === 'attempts') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline"><input type="number" min="-1" max="100" id="${id}"
        data-f="${name}" value="${value == null ? 1 : esc(String(value))}">
        <span class="taUnit">−1 = unlimited</span></span></label>`;
  }
  if (meta.kind === 'text') {
    const isPw = name === 'access_code';
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline">
        <input type="text" id="${id}" data-f="${name}" autocomplete="off"
          data-secret="${isPw ? '1' : ''}"
          placeholder="${isPw && d.has_access_code
            ? 'a password is set — type to replace it' : 'none'}">
        ${isPw && d.has_access_code
          ? '<button class="taMini" data-clear="' + id + '">remove it</button>' : ''}
      </span></label>`;
  }
  if ((meta.kind || '').startsWith('choice:')) {
    const opts = meta.kind.split(':')[1].split(',');
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <select id="${id}" data-f="${name}">${opts.map(o => `<option value="${esc(o)}"
        ${String(value || '') === o ? 'selected' : ''}>${esc(TA_CHOICE[o] || o
          || 'No — they can see them')}</option>`).join('')}</select></label>`;
  }
  return '';
}

const TA_HELP = {
  access_code: 'never shown back to you once saved',
  ip_filter: 'a single address, or a range like 10.1.0.0/16',
  lock_at: 'after this, nobody can open or submit it',
  unlock_at: 'before this, students cannot start',
  cant_go_back: 'only available with one question at a time',
  time_limit: 'blank means untimed',
  one_time_results: 'they see their results once, then never again',
};
const TA_CHOICE = {
  keep_highest: 'the highest score', keep_latest: 'the most recent score',
  always: 'Always — never show results',
  until_after_last_attempt: 'Until they have used every attempt',
};

function taWireForm() {
  $('#taBody').querySelectorAll('[data-clear]').forEach(btn => {
    btn.onclick = ev => {
      ev.preventDefault();
      const el = document.getElementById(btn.dataset.clear);
      el.value = '';
      el.dataset.cleared = '1';           // blank means "clear it", not "untouched"
      btn.classList.add('on');
    };
  });
}

/* Only what actually moved gets sent. A password left blank means untouched --
   the field starts empty because the current one is never echoed back, so an
   empty box cannot be read as "remove the password" unless it was cleared on
   purpose with the button beside it. */
function taChanges(spec) {
  const out = {};
  spec.fields.forEach(name => {
    const el = document.getElementById('ta_' + name);
    if (!el) return;
    const meta = (TA.data.fields || {})[name];
    if (meta.kind === 'bool') { out[name] = el.checked; return; }
    if (meta.kind === 'date') {
      if (el.dataset.cleared === '1' && !el.value) { out[name] = null; return; }
      if (el.value) out[name] = localToUtc(el.value);
      return;
    }
    if (meta.kind === 'text') {
      if (el.dataset.cleared === '1') { out[name] = null; return; }
      if (el.value.trim()) out[name] = el.value.trim();
      return;
    }
    if (meta.kind === 'minutes') {
      out[name] = el.value.trim() === '' ? null : el.value.trim();
      return;
    }
    out[name] = el.value;
  });
  return out;
}

async function taSave(spec) {
  if (TA.busy) return;
  const changes = taChanges(spec);
  TA.busy = true;
  setStatus('checking…');
  try {
    const r = await postConfirmed(
      token => api(`/quiz/${TA.cid}/${TA.qid}/settings`,
        { body: { changes, confirm: token } }),
      { title: 'Change this test in Canvas?', verb: 'Save it',
        note: 'Students see these settings the moment they are saved.' });
    if (r === null) return;                    // cancelled at the confirmation
    (r.rejected || []).forEach(x => setStatus(firstLine(x.why), 'err'));
    if (!(r.changed || []).length && !(r.rejected || []).length) {
      setStatus('nothing had changed', 'ok');
    } else if ((r.changed || []).length) {
      setStatus(r.message, 'ok');
    }
    TA.data = await api(`/quiz/${TA.cid}/${TA.qid}?refresh=1`);
    // The schedule is showing dates that may have just moved.
    if (S.sched) { S.sched = null; }
    TA.panel = 'menu';
    taRender();
    if ((r.rejected || []).length) {
      $('#taBody').insertAdjacentHTML('afterbegin',
        `<div class="callout bad">${r.rejected.map(x =>
          esc(x.why)).join('<br>')}</div>`);
    }
  } catch (err) {
    setStatus(firstLine(err.message), 'err');
  } finally { TA.busy = false; }
}

/* --------------------------------------------------- accommodations, in place */
function taAccom() {
  const d = TA.data;
  const rows = d.accommodated || [];
  const body = `
    ${rows.length ? `<table class="acTable"><thead><tr>
        <th>Student</th><th>Approved</th><th>On this test</th><th>In Canvas</th>
      </tr></thead><tbody>${rows.map(r => `<tr class="${r.in_sync ? '' : 'off'}">
        <td>${esc(r.name)}</td>
        <td class="acWhat">${esc(r.accommodation)}</td>
        <td class="acMin">${r.would_be == null
          ? '<span class="muted">n/a — untimed</span>'
          : '+' + r.would_be + ' min'}</td>
        <td>${r.in_sync
          ? '<span class="pill good">set</span>'
          : (r.current_extra_time
              ? `<span class="pill warn">+${r.current_extra_time} min</span>`
              : '<span class="pill warn">not set</span>')}</td>
      </tr>`).join('')}</tbody></table>`
      : `<div class="callout">${d.roster_size
          ? 'Nobody on your accommodation list is enrolled in this course.'
          : 'Your accommodation list is empty. <b>Student roster</b> pulls '
            + 'students from every course you teach, so a college approval is '
            + 'entered once and applies everywhere.'}</div>`}

    ${(d.unlisted || []).length ? `<details class="acUnlisted">
      <summary>${d.unlisted.length} student(s) have an extension in Canvas but
        are not on your list</summary>
      <ul>${d.unlisted.map(u => `<li>${esc(u.name)} —
        ${u.extra_time ? '+' + u.extra_time + ' min' : ''}
        ${u.extra_attempts ? ' +' + u.extra_attempts + ' attempts' : ''}
        ${u.manually_unlocked ? ' can start while locked' : ''}</li>`).join('')}</ul>
      <div class="cfNote">Usually someone set up by hand. Adding them to the
        list means it happens by itself next time.</div>
    </details>` : ''}

    <div class="acScopes">
      <button class="acScope" id="acThis" ${rows.length ? '' : 'disabled'}>
        <b>Apply to this test</b><span>${esc(d.title)}</span></button>
      <button class="acScope" id="acCourse" ${rows.length ? '' : 'disabled'}>
        <b>Apply to every quiz in this course</b>
        <span>${esc(d.course_label || 'this course')} — blanket accommodations,
          one pass</span></button>
      <button class="acScope" id="acAll" ${d.roster_size ? '' : 'disabled'}>
        <b>Apply across every course I teach</b>
        <span>this term, for everyone on the list</span></button>
    </div>`;

  taShell('Accommodations', body,
    `<button class="btn" id="acRoster">Student roster…</button>
     <span class="spacer"></span>
     <button class="btn" id="taCancel">Back</button>`, esc(d.title));
  $('#taBack').hidden = false;
  $('#taBack').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taCancel').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#acRoster').onclick = () => openRoster(() => openTestAdmin(TA.cid, TA.qid, 'accom'));
  const go = (scope) => applyAccommodations(scope, TA.cid, TA.qid,
    () => openTestAdmin(TA.cid, TA.qid, 'accom'));
  if (rows.length) {
    $('#acThis').onclick = () => go('quiz');
    $('#acCourse').onclick = () => go('course');
  }
  if (d.roster_size) $('#acAll').onclick = () => go('all');
}

/* ------------------------------------------------- applying, at any scope */
/* Plan first, always. The plan is a job because the term-wide scope reads every
   quiz in every course, and it comes back with the confirmation already
   attached: the review screen and the plan are the same step here. */
function applyAccommodations(scope, courseId, quizId, afterwards) {
  const label = { quiz: 'this test', course: 'this course',
                  all: 'every course this term' }[scope] || scope;
  runJob(`Working out what changes on ${label}`,
    () => api('/accommodations/plan', {
      body: { scope, course_id: courseId, quiz_id: quizId,
              term: (S.sched && S.sched.term) || null },
    }),
    plan => showAccomPlan(plan, scope, courseId, quizId, afterwards),
    { autoClose: true });
}

function showAccomPlan(plan, scope, courseId, quizId, afterwards) {
  const host = $('#modalHost');
  const rows = plan.rows || [];
  const already = (plan.skipped || []).filter(s => s.already);
  const noTime = (plan.skipped || []).filter(s => !s.quiet);

  if (!rows.length) {
    host.innerHTML = `<div class="modalBack"><div class="modal narrow">
        <h3>Nothing to change</h3>
        <div class="cfSummary">${esc(plan.summary)}</div>
        <div class="cfNote">${already.length} student-test pair(s) already hold
          the right value in Canvas${noTime.length
            ? `, and ${noTime.length} could not take extra time` : ''}.</div>
        <div class="foot"><span class="spacer"></span>
          <button class="btn" id="apOk">Close</button></div>
      </div></div>`;
    $('#apOk').onclick = () => { host.innerHTML = ''; if (afterwards) afterwards(); };
    return;
  }

  // Grouped by quiz, because that is the unit Canvas is written in and the unit
  // the instructor thinks in.
  const byQuiz = new Map();
  rows.forEach(r => {
    const key = r.course_id + ':' + r.quiz_id;
    if (!byQuiz.has(key)) byQuiz.set(key, []);
    byQuiz.get(key).push(r);
  });

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Check this before it happens</h3>
      <div class="sub">${esc(plan.summary)}${plan.quizzes_in_scope
        ? ` · ${plan.quizzes_in_scope} quiz(zes) looked at` : ''}</div>

      <div class="acPeople">${(plan.students || []).map(s =>
        `<span class="acChip">${esc(s.name || s.user_id)}
          <b>${esc(s.label)}</b></span>`).join('')}</div>

      <div class="apList">${[...byQuiz.values()].map(items => {
        const head = items[0];
        return `<div class="apQuiz">
          <div class="apQHead"><span class="cbadge" style="background:${
            esc(schedColour(head.course_id))}">${esc(head.course_label)}</span>
            <b>${esc(head.quiz_title)}</b>
            <span class="muted">${head.time_limit} min</span></div>
          <ul>${items.map(r => `<li>${esc(r.name)}
            <span class="apArrow">→</span>
            <b>+${r.extra_time} min</b>
            ${r.extra_attempts ? `<span class="apExtra">+${r.extra_attempts}
              attempt(s)</span>` : ''}
            ${r.manually_unlocked ? '<span class="apExtra">can start while '
              + 'locked</span>' : ''}
            ${(r.before || {}).extra_time
              ? `<span class="apWas">was +${r.before.extra_time}</span>` : ''}
          </li>`).join('')}</ul></div>`;
      }).join('')}</div>

      ${already.length ? `<details class="apSkipped">
        <summary>${already.length} already correct in Canvas — left alone</summary>
        <ul>${already.slice(0, 40).map(s => `<li>${esc(s.name)} · ${
          esc(s.quiz_title)}</li>`).join('')}</ul></details>` : ''}
      ${noTime.length ? `<details class="apSkipped">
        <summary>${noTime.length} could not take extra time</summary>
        <ul>${noTime.slice(0, 40).map(s => `<li>${esc(s.name)} · ${
          esc(s.quiz_title)} — ${esc(s.why)}</li>`).join('')}</ul></details>` : ''}

      <div class="cfNote">Canvas takes extra time as minutes, not a multiplier,
        so the numbers above are worked out per test from its own limit and
        rounded up.</div>

      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="apCancel">Cancel</button>
        <button class="btn danger" id="apGo">Apply to Canvas…</button>
      </div>
    </div></div>`;
  $('#apCancel').onclick = () => { host.innerHTML = ''; if (afterwards) afterwards(); };
  $('#apGo').onclick = () => {
    host.innerHTML = '';
    askConfirm({ summary: plan.summary, confirm: plan.confirm,
                 detail: `${byQuiz.size} quiz(zes)` },
      token => runJob('Setting accommodations in Canvas',
        () => api('/accommodations/apply', {
          body: { scope, course_id: courseId, quiz_id: quizId,
                  term: (S.sched && S.sched.term) || null, confirm: token },
        }),
        res => {
          setStatus(res.message, (res.failed || []).length ? 'err' : 'ok');
          if (afterwards) afterwards();
        }, { autoClose: !((plan.batches || []).length > 6) }),
      { title: 'Set these accommodations?', verb: 'Yes, apply them',
        note: 'Students get the extra time immediately, on every test listed.',
        onCancel: () => showAccomPlan(plan, scope, courseId, quizId, afterwards) });
  };
}

/* ---------------------------------------------------------- the saved roster */
/* The list itself: who has a standing accommodation, and what it is. Kept out
   of any one course on purpose -- a college approval applies to every course
   the student is in, and students repeat term after term. */
async function openRoster(afterwards) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Accommodation roster</h3>
      <div class="sub">loading…</div></div></div>`;
  let saved, everyone;
  try {
    [saved, everyone] = await Promise.all([
      api('/accommodations'), api('/accommodations/students'),
    ]);
  } catch (err) { setStatus(firstLine(err.message), 'err'); return; }

  const state = {
    rows: (saved.students || []).map(s => ({ ...s })), query: '',
    // The add panel starts open on an empty list and, once opened, stays open:
    // it is a search box, and a search box that folds itself away between
    // keystrokes cannot be typed into.
    addOpen: !(saved.students || []).length,
  };

  /* Everyone not already on the list, matched against the search box. */
  const poolFor = () => {
    const listed = new Set(state.rows.map(r => String(r.user_id)));
    const q = state.query.toLowerCase();
    return (everyone.students || []).filter(s =>
      !listed.has(s.user_id)
      && (!q || s.name.toLowerCase().includes(q)
             || (s.sis_user_id || '').toLowerCase().includes(q)));
  };

  const poolHtml = (pool) => `
    <div class="roPool">${pool.slice(0, 60).map(s => `
      <button class="roPick" data-uid="${esc(s.user_id)}">
        <b>${esc(s.name)}</b>
        <span>${esc(s.courses.slice(0, 3).join(', '))}${
          s.courses.length > 3 ? ` +${s.courses.length - 3} more` : ''}</span>
      </button>`).join('') || '<div class="muted">no match</div>'}</div>
    ${pool.length > 60 ? `<div class="muted roMore">${pool.length - 60}
      more — narrow the search</div>` : ''}`;

  const draw = () => {
    const pool = poolFor();

    host.innerHTML = `<div class="modalBack"><div class="modal wide rosterModal">
        <h3>Accommodation roster</h3>
        <div class="sub">${state.rows.length} student(s) with a standing
          accommodation · ${everyone.count} students across
          ${everyone.courses} of your courses</div>

        <div class="callout">Entered once, applied anywhere. A Canvas user id is
          the same person in every course, so a college approval set here reaches
          all of them — and it stays put for next term.</div>

        ${state.rows.length ? `<table class="acTable rosterTable"><thead><tr>
            <th>Student</th><th>Type</th><th>Amount</th><th>Attempts</th>
            <th title="Can start a quiz that is locked for everyone else">Unlock</th>
            <th>Note</th><th></th></tr></thead>
          <tbody>${state.rows.map((r, i) => `<tr>
            <td class="acName">${esc(r.name || r.user_id)}
              <span class="muted">${esc(r.sis_user_id || r.user_id)}</span></td>
            <td><select data-i="${i}" data-k="kind">
              <option value="percent" ${r.kind === 'percent' ? 'selected' : ''}
                >extra %</option>
              <option value="minutes" ${r.kind === 'minutes' ? 'selected' : ''}
                >extra minutes</option></select></td>
            <td class="acAmt">${r.kind === 'minutes'
              ? `<input type="number" min="1" max="10080" data-i="${i}"
                   data-k="minutes" value="${esc(String(r.minutes || 20))}">
                 <span class="taUnit">min</span>`
              : `<input type="number" min="1" max="400" step="5" data-i="${i}"
                   data-k="percent" value="${esc(String(r.percent || 50))}">
                 <span class="taUnit">% (${(1 + (+r.percent || 50) / 100)
                   .toFixed(2).replace(/0$/, '')}x)</span>`}</td>
            <td><input type="number" min="0" max="20" data-i="${i}"
              data-k="extra_attempts" value="${esc(String(r.extra_attempts || 0))}"></td>
            <td class="acTick"><input type="checkbox" data-i="${i}"
              data-k="manually_unlocked" ${r.manually_unlocked ? 'checked' : ''}></td>
            <td><input type="text" data-i="${i}" data-k="note" maxlength="120"
              value="${esc(r.note || '')}" placeholder="optional"></td>
            <td><button class="taMini" data-drop="${i}">remove</button></td>
          </tr>`).join('')}</tbody></table>`
        : '<div class="callout">Nobody on the list yet. Find a student below.</div>'}

        <details class="rosterAdd" id="roAdd" ${state.addOpen ? 'open' : ''}>
          <summary>Add a student from your courses</summary>
          <input type="search" id="roQuery" placeholder="Search by name or ID…"
            value="${esc(state.query)}" autocomplete="off">
          <div class="roPoolBox" id="roPoolBox">${poolHtml(pool)}</div>
        </details>

        <div class="foot">
          <button class="btn" id="roApply" ${state.rows.length ? '' : 'disabled'}
            >Save, then apply everywhere…</button>
          <span class="spacer"></span>
          <button class="btn" id="roClose">Close</button>
          <button class="btn ai" id="roSave">Save list</button>
        </div>
      </div></div>`;

    const commit = (fn) => { fn(); draw(); };
    host.querySelectorAll('[data-k]').forEach(el => {
      const write = () => {
        const row = state.rows[+el.dataset.i];
        row[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value;
      };
      // Redraw only where the shape of the row changes; typing in a number must
      // not steal focus back to the top of the table.
      if (el.dataset.k === 'kind' || el.dataset.k === 'percent') {
        el.onchange = () => commit(write);
        if (el.dataset.k === 'percent') el.oninput = write;
      } else {
        el.oninput = write;
        el.onchange = write;
      }
    });
    host.querySelectorAll('[data-drop]').forEach(b => {
      b.onclick = () => commit(() => state.rows.splice(+b.dataset.drop, 1));
    });
    const wirePool = () => {
      host.querySelectorAll('.roPick').forEach(b => {
        b.onclick = () => commit(() => {
          const s = (everyone.students || []).find(x => x.user_id === b.dataset.uid);
          if (s) state.rows.push({
            user_id: s.user_id, name: s.name, sis_user_id: s.sis_user_id,
            login_id: s.login_id, kind: 'percent', percent: 50,
            minutes: 0, extra_attempts: 0, manually_unlocked: false, note: '',
          });
        });
      });
    };
    wirePool();

    const details = $('#roAdd');
    if (details) details.ontoggle = () => { state.addOpen = details.open; };

    const search = $('#roQuery');
    if (search) {
      // Repaint only the list of matches. Redrawing the whole dialog on every
      // keystroke is what closed the panel and threw the caret away, which
      // made this typeable exactly one letter at a time.
      search.oninput = () => {
        state.query = search.value.trim();
        const box = $('#roPoolBox');
        if (box) { box.innerHTML = poolHtml(poolFor()); wirePool(); }
      };
      // Adding a student redraws the dialog; land back in the search box with
      // the last search selected. Any remaining matches for it are still on
      // screen (two students called Smith), and typing a new name replaces it
      // instead of running onto the end of the old one.
      if (state.addOpen && state.rows.length) {
        search.focus();
        try { search.select(); } catch (_) { /* older type=search */ }
      }
    }
    $('#roClose').onclick = () => { host.innerHTML = ''; if (afterwards) afterwards(); };
    $('#roSave').onclick = () => save().then(r => {
      if (r) { setStatus(`${r.count} student(s) on the accommodation list`, 'ok');
               host.innerHTML = ''; if (afterwards) afterwards(); }
    });
    $('#roApply').onclick = () => save().then(r => {
      if (r) applyAccommodations('all', null, null, () => openRoster(afterwards));
    });
  };

  const save = async () => {
    try {
      const r = await api('/accommodations', { body: { students: state.rows } });
      (r.rejected || []).forEach(x => setStatus(firstLine(x.why), 'err'));
      return r;
    } catch (err) { setStatus(firstLine(err.message), 'err'); return null; }
  };

  draw();
}

/* A UTC instant as the value a datetime-local input wants, in local time. */
function utcToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* --------------------------------------------- announcements & instructions */
/* Two ways to act from the schedule. Both end at the same place: a plan the
   instructor reads, then one confirmed call to /schedule/apply, which checks
   every operation again on the server and still has to get past
   allow_canvas_writes. Nothing here writes to Canvas on its own. */

/* A local wall-clock stamp ("2026-09-09T08:00:00") turned into a UTC instant.
   The browser is the only part of this that knows the timezone, and Date()
   parses an offset-less stamp as local, which is exactly what is wanted. */
function localToUtc(stamp) {
  if (!stamp) return null;
  const d = new Date(String(stamp).replace(' ', 'T'));
  return isNaN(d) ? null : d.toISOString();
}
function prettyLocal(stamp) {
  if (!stamp) return 'cleared';
  const d = new Date(String(stamp).replace(' ', 'T'));
  return isNaN(d) ? String(stamp) : `${fmtDay(d)} at ${fmtTime(d)}`;
}
function prettyUtc(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : `${fmtDay(d)} at ${fmtTime(d)}`;
}

/* ------------------------------------------------------------- announcement */
function openAnnounce(courseId, assignmentId) {
  const host = $('#modalHost');
  const item = (S.sched.items || []).find(
    i => String(i.assignment_id) === String(assignmentId)) || {};
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Announcement</h3>
      <div class="sub">${esc(item.course_label || '')}${item.name
        ? ' · ' + esc(item.name) : ''}</div>
      <label class="annLabel">Anything to add? <span>optional — "mention the
        review session", "keep it to two lines"</span>
        <input type="text" id="anExtra" autocomplete="off"
          placeholder="leave empty and it works from what Canvas knows">
      </label>
      <div class="foot" id="anFoot">
        <span class="spacer"></span>
        <button class="btn" id="anCancel">Cancel</button>
        <button class="btn ai" id="anDraft">Draft it</button>
      </div>
    </div></div>`;
  $('#anCancel').onclick = () => { host.innerHTML = ''; };
  $('#anExtra').focus();
  $('#anDraft').onclick = () => {
    const extra = $('#anExtra').value;
    runJob(`Drafting an announcement`,
      () => api('/schedule/announce', { body: {
        course_id: courseId, assignment_id: assignmentId, extra } }),
      draft => { $('#modalHost').innerHTML = ''; showAnnounceDraft(draft); });
  };
}

function showAnnounceDraft(d) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Announcement for ${esc(d.course_label || '')}</h3>
      <div class="sub">about ${esc(d.name || '')}${d.model
        ? ' · drafted by ' + esc(d.model) : ''}${d.cost_usd
        ? ' · $' + Number(d.cost_usd).toFixed(3) : ''} · edit anything before it
        goes out</div>
      ${d.parse_error ? `<div class="callout bad">Claude's reply was not clean
        JSON, so the text below is its raw answer. Read it before posting.</div>` : ''}
      <label class="annLabel">Subject
        <input type="text" id="anTitle" value="${esc(d.title || '')}">
      </label>
      <label class="annLabel">Message
        <textarea id="anBody" rows="9">${esc(d.message || '')}</textarea>
      </label>
      <div class="callout" id="anGate">${d.writes_enabled
        ? 'Posting puts this in front of every student in the course. You will be '
          + 'asked to confirm once more first.'
        : 'Canvas writes are locked off in config.json, so <b>Post</b> is '
          + 'disabled. Copy still works.'}</div>
      <div class="foot">
        <button class="btn" id="anCopy">Copy</button>
        <button class="btn" id="anRedo">Draft again</button>
        <span class="spacer"></span>
        <button class="btn" id="anClose">Close</button>
        <button class="btn danger" id="anPost" ${d.writes_enabled ? '' : 'disabled'}
          >Post to Canvas…</button>
      </div>
    </div></div>`;
  $('#anClose').onclick = () => { host.innerHTML = ''; };
  $('#anRedo').onclick = () => openAnnounce(d.course_id, d.assignment_id);
  $('#anCopy').onclick = () => {
    navigator.clipboard.writeText(`${$('#anTitle').value}\n\n${$('#anBody').value}`)
      .then(() => setStatus('announcement copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
  $('#anPost').onclick = () => {
    const title = $('#anTitle').value.trim();
    const message = $('#anBody').value.trim();
    if (!title || !message) { setStatus('needs a subject and a message', 'err'); return; }
    runJobConfirmed('Posting the announcement',
      token => api('/schedule/apply', { body: { dry_run: false, confirm: token,
        operations: [{ op: 'announce', course_id: d.course_id, title, message }] } }),
      r => {
        $('#modalHost').innerHTML = '';
        const ok = (r.applied || []).length;
        setStatus(ok ? 'announcement posted' :
          'not posted: ' + ((r.failed || [])[0]|| {}).error, ok ? 'ok' : 'err');
      });
  };
  $('#anTitle').focus();
}

/* -------------------------------------------------------- typed instruction */
const INSTRUCTION_EXAMPLES = [
  'unlock my Test 1 for Game Theory today at 8am',
  'push the College Algebra 1.1 homework to Friday at 11:59pm',
  'publish the Week 2 quiz in Game Theory',
  'close Test 2 in 3D Game Engine tonight and tell the class',
];

function openInstruct() {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Tell it what to change</h3>
      <div class="sub">Plain sentences about the assignments on this schedule.
        It shows you a plan first and changes nothing until you say so.</div>
      <textarea id="inText" rows="3" placeholder="unlock my test today"></textarea>
      <div class="inExamples">${INSTRUCTION_EXAMPLES.map(x =>
        `<button class="chip" data-eg="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <p class="inNote">It can set the <b>opens</b>, <b>due</b> and <b>closes</b>
        dates, <b>publish</b> or <b>unpublish</b> an assignment, and <b>post an
        announcement</b>. It cannot change a grade, change points, or delete
        anything.</p>
      <div id="inResult"></div>
      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="inCancel">Cancel</button>
        <button class="btn ai" id="inPlan">Work out a plan</button>
      </div>
    </div></div>`;
  $('#inCancel').onclick = () => { host.innerHTML = ''; };
  host.querySelectorAll('[data-eg]').forEach(el => {
    el.onclick = () => { $('#inText').value = el.dataset.eg; $('#inText').focus(); };
  });
  const go = () => {
    const instruction = $('#inText').value.trim();
    if (!instruction) { setStatus('type what you want changed', 'err'); return; }
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const now_local = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${
      pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
    runJob('Working out what to change',
      () => api('/schedule/instruct', { body: {
        instruction, now_local,
        zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        term: S.sched && S.sched.term } }),
      plan => { $('#modalHost').innerHTML = ''; showPlan(plan); });
  };
  $('#inPlan').onclick = go;
  $('#inText').onkeydown = ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); go(); }
  };
  $('#inText').focus();
}

function showPlan(p) {
  const host = $('#modalHost');
  const ops = p.operations || [];
  const rowFor = (op, i) => {
    const changes = Object.entries(op.dates || {}).map(([key, value]) => {
      const words = { unlock_at: 'opens', due_at: 'due', lock_at: 'closes' };
      const before = (op.before || {})[key];
      return `<div class="planChange"><span class="planField">${words[key] || key}</span>
        <span class="planWas">${prettyUtc(before)}</span>
        <span class="planArrow">→</span>
        <b>${esc(prettyLocal(value))}</b></div>`;
    }).join('');
    return `<label class="planOp">
      <input type="checkbox" class="planPick" data-i="${i}" checked>
      <span class="planBody">
        <span class="planHead">${esc(op.describe || '')}</span>
        ${changes}
        ${op.op === 'announce' ? `<span class="planAnn"><b>${esc(op.title)}</b>
          <span>${esc(op.message)}</span></span>` : ''}
        ${op.why ? `<span class="planWhy">${esc(op.why)}</span>` : ''}
      </span></label>`;
  };

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${ops.length ? 'Check this before it happens' : 'Nothing to do'}</h3>
      <div class="sub">you asked: “${esc(p.instruction || '')}”${p.model
        ? ' · read by ' + esc(p.model) : ''}${p.cost_usd
        ? ' · $' + Number(p.cost_usd).toFixed(3) : ''}</div>

      ${p.understood ? `<p class="planUnderstood">${esc(p.understood)}</p>` : ''}
      ${p.refused ? `<div class="callout bad"><b>Not doing that.</b>
        ${esc(p.refused)}</div>` : ''}
      ${(p.questions || []).length ? `<div class="callout"><b>It needs to know:</b>
        <ul class="planQs">${p.questions.map(q => `<li>${esc(q)}</li>`).join('')}</ul>
        Answer in the box and try again.</div>` : ''}

      ${ops.length ? `<div class="planList">${ops.map(rowFor).join('')}</div>` : ''}

      ${(p.rejected || []).length ? `<details class="planRejected">
        <summary>${p.rejected.length} thing(s) it suggested were not allowed</summary>
        <ul>${p.rejected.map(r => `<li>${esc(r.why)}${r.raw
          ? ` <span class="muted">${esc(r.raw)}</span>` : ''}</li>`).join('')}</ul>
      </details>` : ''}

      ${ops.length ? `<div class="callout" id="planGate">${p.writes_enabled
        ? 'These are live courses. Check the dates above, then apply — there is '
          + 'one more confirmation after this.'
        : 'Canvas writes are locked off in config.json, so <b>Apply</b> is '
          + 'disabled. <b>Dry run</b> works either way and changes nothing.'}</div>` : ''}

      <div class="foot">
        <button class="btn" id="planRedo">Change the wording</button>
        <span class="spacer"></span>
        <button class="btn" id="planClose">Close</button>
        ${ops.length ? `<button class="btn" id="planDry">Dry run</button>
        <button class="btn danger" id="planApply" ${p.writes_enabled ? '' : 'disabled'}
          >Apply…</button>` : ''}
      </div>
    </div></div>`;
  $('#planClose').onclick = () => { host.innerHTML = ''; };
  $('#planRedo').onclick = () => { openInstruct(); $('#inText').value = p.instruction || ''; };

  const picked = () => [...host.querySelectorAll('.planPick')]
    .filter(c => c.checked)
    .map(c => {
      const op = ops[+c.dataset.i];
      if (op.op !== 'set_dates') return op;
      // The local wall clock the model returned, converted here where the
      // timezone is actually known.
      const utc = {};
      Object.entries(op.dates || {}).forEach(([key, value]) => {
        utc[key] = value === null ? null : localToUtc(value);
      });
      return { ...op, dates_utc: utc };
    });

  const send = (dry) => {
    const operations = picked();
    if (!operations.length) { setStatus('nothing ticked', 'err'); return; }
    runJobConfirmed(dry ? 'Dry run' : 'Applying the changes',
      token => api('/schedule/apply', { body: { dry_run: dry, operations,
        confirm: token, term: S.sched && S.sched.term } }),
      async r => {
        $('#modalHost').innerHTML = '';
        showApplyResult(r);
        if (!dry && (r.applied || []).length) {
          S.sched = await api('/schedule' + (S.sched && S.sched.term
            ? '?term=' + encodeURIComponent(S.sched.term) : ''));
          renderSchedule();
        }
      },
      { title: `Apply ${operations.length} change(s) to your live courses?`,
        verb: 'Yes, apply them',
        note: operations.map(o => '· ' + o.describe).join('\n') });
  };
  const dry = $('#planDry'); if (dry) dry.onclick = () => send(true);
  const apply = $('#planApply'); if (apply) apply.onclick = () => send(false);
}

function showApplyResult(r) {
  const host = $('#modalHost');
  const rows = (list, cls) => list.map(x =>
    `<li class="${cls}">${esc(x.describe || '')}${x.error
      ? ` — <span class="muted">${esc(x.error)}</span>` : ''}${x.sends
      ? ` <span class="muted">sends ${esc(JSON.stringify(x.sends))}</span>` : ''}</li>`
  ).join('');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${r.dry_run ? 'Dry run — nothing was changed'
        : (r.ok ? 'Done' : 'Finished with problems')}</h3>
      <div class="sub">${r.dry_run
        ? 'This is exactly what would be sent to Canvas.'
        : `${(r.applied || []).length} applied, ${(r.failed || []).length} failed`}</div>
      <ul class="applyList">
        ${rows(r.planned || [], 'planned')}
        ${rows(r.applied || [], 'done')}
        ${rows(r.failed || [], 'bad')}
      </ul>
      ${(r.rejected || []).length ? `<div class="callout bad">Not allowed:
        ${r.rejected.map(x => esc(x.why)).join('; ')}</div>` : ''}
      ${r.error ? `<div class="callout bad">${esc(r.error)}</div>` : ''}
      <div class="foot"><span class="spacer"></span>
        <button class="btn" id="apClose">Close</button></div>
    </div></div>`;
  $('#apClose').onclick = () => { host.innerHTML = ''; };
}

/* ------------------------------------------------- assignment description */
/* Clicking a row in the schedule opens what the assignment actually asks for.
   Reading the prompt is what you want from a schedule; grading is the step
   after, so it is a button in here rather than the thing a click does.

   The HTML arrives already sanitised by courseforge/htmlclean.py: a real
   Canvas description carries a remote <script> and stylesheet, and neither has
   any business running inside this page. */
async function openScheduleItem(courseId, assignmentId) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide descModal">
      <h3 id="dsTitle">Loading…</h3>
      <div class="sub" id="dsMeta"></div>
      <div class="descBody" id="dsBody"><p class="descWait">
        <span class="spin"></span>reading this assignment from Canvas…</p></div>
      <div class="foot" id="dsFoot">
        <button class="btn" id="dsClose">Close</button>
      </div>
    </div></div>`;
  $('#dsClose').onclick = () => { host.innerHTML = ''; };

  let d;
  try {
    d = await api(`/schedule/item/${encodeURIComponent(courseId)}/${
      encodeURIComponent(assignmentId)}`);
  } catch (err) {
    $('#dsTitle').textContent = 'Could not load this assignment';
    $('#dsBody').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    return;
  }
  if (!$('#dsTitle')) return;              // closed while it was loading

  const due = d.due_at ? new Date(d.due_at) : null;
  const bits = [
    (d.course_label || d.course_code)
      ? `<span class="cbadge" style="background:${esc(schedColour(d.course_id))}"
           title="${esc(d.course_name || '')}">${
           esc(d.course_label || d.course_code)}</span>` : '',
    due ? `due ${esc(fmtDay(due))} at ${esc(fmtTime(due))}` : 'no due date',
    d.points == null ? '' : `${num(d.points)} points`,
    `<span class="k ${KIND_CLASS[d.kind_label] || 'kind-asn'}">${esc(d.kind_label)}</span>`,
    d.proctored ? '<span class="flag">⚠ PROCTORED</span>' : '',
    d.published ? '' : '<span class="pill warn">unpublished</span>',
    d.needs_grading ? `<span class="tgBadge">${d.needs_grading}</span> waiting to grade` : '',
  ].filter(Boolean);

  $('#dsTitle').textContent = d.name;
  $('#dsMeta').innerHTML = bits.join('<span class="dsDot">·</span>');

  const submits = (d.submission_types || []).map(t => t.replace(/_/g, ' ')).join(', ');
  $('#dsBody').innerHTML = `
    ${d.has_description
      ? `<div class="canvasHtml">${d.description}</div>`
      : `<p class="descWait">This assignment has no description in Canvas.</p>`}
    ${(d.rubric || []).length ? `<details class="descRubric" open>
      <summary>Rubric (${d.rubric.length} rows, ${num(
        d.rubric.reduce((a, r) => a + (+r.points || 0), 0))} points)</summary>
      <table class="descRubricTable"><tbody>${d.rubric.map(r => `<tr>
        <td>${esc(r.label)}${r.detail && r.detail !== r.label
          ? `<div class="muted">${esc(r.detail)}</div>` : ''}</td>
        <td class="pts">${r.points == null ? '' : num(r.points)}</td>
      </tr>`).join('')}</tbody></table>
    </details>` : ''}
    ${submits ? `<p class="descSubmit">Submitted as: ${esc(submits)}${
      d.allowed_attempts && d.allowed_attempts > 0
        ? ` · ${d.allowed_attempts} attempt(s) allowed` : ''}</p>` : ''}`;

  $('#dsFoot').innerHTML = `
    <a class="btn ai" href="#/c/${esc(d.course_id)}/a/${esc(d.assignment_id)}"
       id="dsGrade">${d.needs_grading ? `Grade ${d.needs_grading} waiting`
         : 'Open in the grader'}</a>
    ${d.quiz_id ? `<button class="btn" id="dsEdit"
       title="Dates, password, timing, accommodations">Edit test…</button>` : ''}
    <button class="btn" id="dsAnnounce">Announcement…</button>
    ${d.url ? `<a class="btn" href="${esc(d.url)}" target="_blank"
       rel="noopener">Open in Canvas ↗</a>` : ''}
    <span class="spacer"></span>
    <span class="descFetched">read from Canvas just now</span>
    <button class="btn" id="dsClose2">Close</button>`;
  $('#dsClose2').onclick = () => { host.innerHTML = ''; };
  $('#dsGrade').onclick = () => { host.innerHTML = ''; };
  $('#dsAnnounce').onclick = () => openAnnounce(d.course_id, d.assignment_id);
  const de = $('#dsEdit');
  if (de) de.onclick = () => openTestAdmin(d.course_id, d.quiz_id);
}

/* The colour the schedule already assigned this course, so the badge in the
   reader matches the row it was opened from. */
function schedColour(courseId) {
  const found = ((S.sched && S.sched.courses) || [])
    .find(c => String(c.course_id) === String(courseId));
  return (found && found.colour) || 'var(--muted)';
}

/* ------------------------------------------------------------ remind missing */
/* Canvas's own "Message Students Who -> Haven't submitted yet", from here. One
   generic note, sent privately to every student with nothing turned in.

   The list is read live from Canvas, never from the last sync. This is offered
   from the term schedule, where an assignment may never have been synced at
   all, and a stale list means chasing someone who did hand their work in. The
   server re-reads it again at send time for the same reason. */
async function openRemind(courseId, assignmentId, only) {
  const host = $('#modalHost');
  const scope = (only && only.length) ? only.map(String) : null;
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Remind students with nothing turned in</h3>
      <div class="sub" id="rmSub"><span class="spin"></span>reading the roster from Canvas…</div>
      <div id="rmBody"></div>
      <div class="foot" id="rmFoot"><button class="btn" id="rmClose">Close</button></div>
    </div></div>`;
  $('#rmClose').onclick = () => { host.innerHTML = ''; };

  let d;
  try {
    d = await api(`/a/${encodeURIComponent(courseId)}/${encodeURIComponent(assignmentId)}/missing`);
  } catch (err) {
    if (!$('#rmSub')) return;
    $('#rmSub').textContent = 'Could not read the roster.';
    $('#rmBody').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    return;
  }
  if (!$('#rmSub')) return;                     // closed while it was loading

  const rows = scope ? (d.missing || []).filter(r => scope.includes(String(r.user_id)))
                     : (d.missing || []);
  const due = d.due_phrase ? ` · due ${esc(d.due_phrase)}` : '';
  $('#rmSub').innerHTML = `<b>${esc(d.name)}</b>${due}`;

  if (!rows.length) {
    $('#rmBody').innerHTML = `<div class="callout">${scope
      ? 'This student has turned something in after all, so there is nothing to chase.'
      : `Everyone on the roster has turned something in for this. Nothing to send.`}</div>`;
    return;
  }

  $('#rmSub').innerHTML = `<b>${esc(d.name)}</b>${due} · <b>${rows.length}</b> of ${
    d.roster} student(s) have turned nothing in.${d.past_due ? ''
    : ' <span class="pill warn">this is not past due yet</span>'}`;

  $('#rmBody').innerHTML = `
    <div class="rmWho">${rows.map(r => `
      <label class="tick"><input type="checkbox" class="rmPick" value="${esc(r.user_id)}" checked>
        <span>${esc(r.name)}${r.graded
          ? ` <span class="rmNote">already scored ${num(r.score)}</span>` : ''}</span>
      </label>`).join('')}</div>
    <label class="rmField">Subject
      <input type="text" id="rmSubject" value="${esc(d.subject)}"></label>
    <label class="rmField">Message
      <textarea id="rmText" rows="7" spellcheck="true">${esc(d.body)}</textarea></label>
    <div class="callout">Every student gets their own private copy in the Canvas
      Inbox. Nobody can see who else was written to, and there is no reply-all.</div>`;

  $('#rmFoot').innerHTML = `
    <button class="btn sm" id="rmAll">Select all</button>
    <button class="btn sm" id="rmNone">Clear</button>
    <span class="spacer"></span>
    <button class="btn" id="rmCancel">Cancel</button>
    <button class="btn primary" id="rmGo">Send the message…</button>`;

  const picks = () => [...host.querySelectorAll('.rmPick')];
  const chosen = () => picks().filter(el => el.checked).map(el => el.value);
  const paint = () => {
    const n = chosen().length;
    $('#rmGo').disabled = !n;
    $('#rmGo').textContent = n ? `Send to ${n} student${n === 1 ? '' : 's'}…`
                               : 'Nobody selected';
  };
  picks().forEach(el => { el.onchange = paint; });
  $('#rmAll').onclick = () => { picks().forEach(el => { el.checked = true; }); paint(); };
  $('#rmNone').onclick = () => { picks().forEach(el => { el.checked = false; }); paint(); };
  $('#rmCancel').onclick = () => { host.innerHTML = ''; };
  paint();

  $('#rmGo').onclick = () => {
    const ids = chosen();
    if (!ids.length) return;
    const subject = $('#rmSubject').value.trim();
    const message = $('#rmText').value.trim();
    if (!message) { setStatus('the message is empty', 'err'); return; }
    runJobConfirmed(`Messaging ${ids.length} student(s)`,
      token => api(`/a/${courseId}/${assignmentId}/remind`,
        { body: { only: ids, subject, body: message, confirm: token } }),
      r => {
        $('#modalHost').innerHTML = '';
        const bad = (r.failed || []).reduce((a, f) => a + (f.names || []).length, 0);
        setStatus(`messaged ${r.count} student(s)` + (bad ? `, ${bad} failed` : ''),
          bad ? 'err' : 'ok');
      },
      { title: `Send this to ${ids.length} student${ids.length === 1 ? '' : 's'}?`,
        verb: 'Yes, send it',
        note: 'Each gets a private Canvas Inbox message. A sent message cannot be '
              + 'unsent, and students are usually notified by email.',
        // Cancelling rebuilds the dialog from markup alone, which would leave it
        // with no handlers, so put a live one back.
        onCancel: () => openRemind(courseId, assignmentId, only) });
  };
}

/* ------------------------------------------------------------- schedule */
/* Every dated assignment across the term, grouped by week.

   Weeks are grouped HERE, in the browser, not on the server. Canvas hands back
   due dates in UTC and an 11:59 PM Central deadline arrives as 04:59Z the next
   day: bucketing that server-side without a timezone database would file a
   Sunday-night deadline under the following week, which is the one mistake this
   view cannot afford. The browser already knows its own zone. */
const KIND_CLASS = {
  FINAL: 'kind-exam', EXAM: 'kind-exam', TEST: 'kind-test', QUIZ: 'kind-quiz',
  PRACTICE: 'kind-prac', DISCUSSION: 'kind-disc', UNGRADED: 'kind-ung',
  ASSIGNMENT: 'kind-asn',
};
const DAY_MS = 86400000;

function startOfWeek(d) {                    // Monday, local time
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const weekKey = d => startOfWeek(d).toISOString().slice(0, 10);
const fmtRange = (a, b) => `${a.toLocaleDateString([], { month: 'short', day: 'numeric' })} – ${
  b.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;

function ago(seconds) {
  if (seconds == null) return 'never';
  if (seconds < 60) return 'just now';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h > 1 ? 's' : ''} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d > 1 ? 's' : ''} ago`;
}

async function openSchedule(refresh) {
  showView('schedule');
  crumbs([{ label: 'Courses', href: '#/' }, { label: 'Schedule' }]);
  $('#headerActions').innerHTML = '';
  if (!S.sched) {
    $('#schedBody').innerHTML = '<p class="schedNote">Loading…</p>';
    $('#schedFoot').textContent = '';
  }
  try {
    S.sched = await api('/schedule' + (S.term && S.term !== '__all'
      ? '?term=' + encodeURIComponent(S.term) : ''));
  } catch (err) {
    $('#schedBody').innerHTML = `<p class="schedNote">Could not load: ${esc(err.message)}</p>`;
    return;
  }
  S.schedFilter = S.schedFilter || { off: new Set(), hidePast: true, examOnly: false, gradeOnly: false };
  renderSchedule();
  // "Fetch directly from Canvas to stay up to date": the cache paints first so
  // the view is instant, then a stale copy quietly refreshes behind it.
  if (refresh || S.sched.stale) refreshSchedule(!S.sched.items.length);
}

function refreshSchedule(blocking) {
  const body = { term: S.sched && S.sched.term ? S.sched.term : null };
  const done = async () => {
    S.sched = await api('/schedule' + (body.term ? '?term=' + encodeURIComponent(body.term) : ''));
    renderSchedule();
  };
  if (blocking) {
    runJob('Reading this term from Canvas',
      () => api('/schedule/refresh', { body }), () => { $('#modalHost').innerHTML = ''; done(); });
    return;
  }
  // Quiet background refresh: the footer says it is happening.
  const foot = $('#schedFoot');
  if (foot) foot.dataset.busy = '1';
  renderSchedFoot();
  api('/schedule/refresh', { body }).then(({ job }) => {
    const poll = async () => {
      const info = await api('/jobs/' + job);
      if (info.state === 'running') return setTimeout(poll, 900);
      if (foot) delete foot.dataset.busy;
      if (info.state === 'error') { renderSchedFoot(info.error); return; }
      await done();
    };
    poll();
  }).catch(err => {
    if (foot) delete foot.dataset.busy;
    renderSchedFoot(err.message);
  });
}

function schedRows() {
  const sc = S.sched, f = S.schedFilter;
  const now = Date.now();
  return (sc.items || []).map(it => {
    const when = new Date(it.due_at);
    return { ...it, when, past: when.getTime() < now };
  }).filter(it => {
    if (f.off.has(it.course_code)) return false;
    // "Waiting to grade" is a work queue, not a calendar, and most of the
    // backlog is behind you: asking for it overrides hiding past due, or the
    // list would show a handful of items while 200 sat unseen.
    if (f.hidePast && it.past && !f.gradeOnly) return false;
    if (f.examOnly && !it.exam) return false;
    if (f.gradeOnly && !it.needs_grading) return false;
    return true;
  });
}

function renderSchedule() {
  const sc = S.sched;
  const f = S.schedFilter;
  const st = sc.stats || {};
  $('#schedTitle').textContent = 'Master Schedule';
  $('#schedSub').innerHTML = `${esc(sc.term || 'all terms')} ·
    every dated assignment across ${(sc.courses || []).length} courses ·
    times in your local timezone`;

  $('#schedStats').innerHTML = [
    [st.remaining, 'Items remaining'],
    [st.next_7_days, 'Due next 7 days'],
    [st.exams_left, 'Tests &amp; exams left'],
    [st.needs_grading, 'Submissions waiting'],
    [st.total, 'Total dated items'],
  ].map(([n, label]) => `<div class="schedStat"><b>${n == null ? '—' : n}</b>
    <span>${label}</span></div>`).join('');

  $('#schedControls').innerHTML = `
    <div class="schedChips">
      <span class="ctlLabel">Courses</span>
      <button class="chip" id="scAll" aria-pressed="${!f.off.size}">All</button>
      ${(sc.courses || []).map(c => `<button class="chip cchip ${
        f.off.has(c.code) ? 'off' : ''}" data-code="${esc(c.code)}"
        title="${esc(c.name)}"><span class="cdot" style="background:${esc(c.colour)}"></span>
        ${esc(c.label || c.code)} <span class="cnum">(${c.count}${
          c.needs_grading ? ` · ${c.needs_grading} to grade` : ''})</span></button>`).join('')}
    </div>
    <div class="schedChips row2">
      <div class="ctlGroup">
        <span class="ctlLabel">Actions</span>
        <button class="btn sm ai" id="scInstruct"
          title="Say what you want changed, in plain words">Tell it what to change…</button>
        <button class="btn sm" id="scRoster"
          title="Students with a standing accommodation, across every course you teach"
          >Student roster…</button>
        <button class="btn sm" id="scAccom"
          title="Apply every standing accommodation to every timed quiz this term"
          >Accommodations: apply all…</button>
      </div>
      <div class="ctlGroup ctlShow">
        <span class="ctlLabel">Show</span>
        <button class="chip" id="scPast" aria-pressed="${!!f.hidePast}">${
          f.hidePast ? 'Hiding past due' : 'Showing past due'}</button>
        <button class="chip" id="scExam" aria-pressed="${!!f.examOnly}">Tests &amp; exams only</button>
        <button class="chip" id="scGrade" aria-pressed="${!!f.gradeOnly}"
          title="Everything with submissions waiting, past due included">Waiting to grade</button>
      </div>
    </div>`;
  $('#scAll').onclick = () => {
    if (f.off.size) f.off.clear();
    else (sc.courses || []).forEach(c => f.off.add(c.code));
    renderSchedule();
  };
  $('#schedControls').querySelectorAll('.cchip').forEach(el => {
    el.onclick = () => {
      const code = el.dataset.code;
      if (f.off.has(code)) f.off.delete(code); else f.off.add(code);
      renderSchedule();
    };
  });
  $('#scInstruct').onclick = openInstruct;
  $('#scRoster').onclick = () => openRoster();
  $('#scAccom').onclick = () => applyAccommodations('all', null, null);
  $('#scPast').onclick = () => { f.hidePast = !f.hidePast; renderSchedule(); };
  $('#scExam').onclick = () => { f.examOnly = !f.examOnly; renderSchedule(); };
  $('#scGrade').onclick = () => { f.gradeOnly = !f.gradeOnly; renderSchedule(); };

  const rows = schedRows();
  if (!rows.length) {
    $('#schedBody').innerHTML = `<p class="schedNote">${(sc.items || []).length
      ? 'Nothing matches these filters.'
      : 'No dated assignments cached yet. Use Refresh to read this term from Canvas.'}</p>`;
    renderSchedFoot();
    return;
  }

  const weeks = new Map();
  rows.forEach(it => {
    const key = weekKey(it.when);
    if (!weeks.has(key)) weeks.set(key, []);
    weeks.get(key).push(it);
  });
  const thisWeek = weekKey(new Date());

  $('#schedBody').innerHTML = [...weeks.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1)
    .map(([key, items]) => {
      const monday = new Date(key + 'T00:00:00');
      const sunday = new Date(monday.getTime() + 6 * DAY_MS);
      const pts = items.reduce((a, b) => a + (+b.points || 0), 0);
      const wait = items.reduce((a, b) => a + (b.needs_grading || 0), 0);
      // Only the assessments in this week, and only the ones the current
      // filters are actually showing: the button acts on what is on screen.
      const exams = items.filter(i => i.exam);
      return `<section class="week ${key === thisWeek ? 'now' : ''}">
        <div class="whead">
          <h2>Week of ${monday.toLocaleDateString([], { month: 'short', day: 'numeric' })}${
            key === thisWeek ? ' <span class="nowTag">this week</span>' : ''}</h2>
          <div class="wmeta">${fmtRange(monday, sunday)}
            <span class="load">${items.length} item${items.length > 1 ? 's' : ''} ·
              ${num(pts)} pts${wait ? ` · ${wait} to grade` : ''}</span>
            ${exams.length ? `<button class="weekRemind" data-week="${key}"
              title="Draft a reminder announcement for each of these ${
                exams.length} assessment(s)">Remind ${exams.length} test${
                exams.length > 1 ? 's' : ''}</button>` : ''}</div>
        </div>
        <table><thead><tr><th>Due</th><th>Course</th><th>Item</th><th>Type</th>
          <th class="tg">To grade</th><th class="pts">Pts</th></tr></thead><tbody>
        ${items.map(it => schedRow(it)).join('')}
        </tbody></table></section>`;
    }).join('');

  $('#schedBody').querySelectorAll('.itemName').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, aid] = el.dataset.open.split(':');
      openScheduleItem(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-ann]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, aid] = el.dataset.ann.split(':');
      openAnnounce(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-remind]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, aid] = el.dataset.remind.split(':');
      openRemind(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-week]').forEach(el => {
    el.onclick = () => openWeekReminders(el.dataset.week);
  });
  $('#schedBody').querySelectorAll('[data-edit]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, qid] = el.dataset.edit.split(':');
      openTestAdmin(cid, qid);
    };
  });
  renderSchedFoot();

  const now = $('#schedBody .week.now');
  if (now && !S.schedScrolled) { now.scrollIntoView({ block: 'start' }); S.schedScrolled = true; }
}

function schedRow(it) {
  const today = new Date().toDateString() === it.when.toDateString();
  return `<tr class="${it.past ? 'past' : ''} ${today ? 'today' : ''}">
    <td class="daycell"><b>${fmtDay(it.when)}</b><span>${fmtTime(it.when)}</span></td>
    <td><span class="cbadge" style="background:${esc(it.colour)}"
      title="${esc(it.course_name || it.course_code)}">${
        esc(it.course_label || it.course_code)}</span></td>
    <td class="name">
      <a href="#" class="itemName" data-open="${esc(it.course_id)}:${
        esc(it.assignment_id)}" title="Read what this assignment asks for"
         >${esc(it.name)}</a>
      ${it.url ? `<a class="extLink" href="${esc(it.url)}" target="_blank"
         rel="noopener" title="Open in Canvas">↗</a>` : ''}
      <button class="rowAnnounce" data-ann="${esc(it.course_id)}:${
        esc(it.assignment_id)}"
        title="Draft a reminder announcement about this">announce</button>
      ${it.past ? `<button class="rowAnnounce rowRemind" data-remind="${
        esc(it.course_id)}:${esc(it.assignment_id)}"
        title="Message every student who has turned nothing in">remind</button>` : ''}
      ${it.quiz_id ? `<button class="rowEdit" data-edit="${esc(it.course_id)}:${
        esc(it.quiz_id)}"
        title="Dates, password, timing, accommodations">edit</button>` : ''}
      ${it.proctored ? '<span class="flag">⚠ PROCTORED</span>' : ''}
      ${it.published ? '' : '<span class="pill warn">unpublished</span>'}
    </td>
    <td class="kind"><span class="k ${KIND_CLASS[it.kind_label] || 'kind-asn'}">${
      esc(it.kind_label)}</span></td>
    <td class="tg">${it.needs_grading
      ? `<a class="tgBadge" href="#/c/${esc(it.course_id)}/a/${esc(it.assignment_id)}"
           title="${it.needs_grading} submission(s) Canvas says are waiting">${
             it.needs_grading}</a>`
      : '<span class="muted">—</span>'}</td>
    <td class="pts">${it.points == null ? '—' : num(it.points)}</td>
  </tr>`;
}

function renderSchedFoot(error) {
  const foot = $('#schedFoot');
  if (!foot) return;
  const sc = S.sched || {};
  const busy = foot.dataset.busy === '1';
  foot.innerHTML = `
    <span class="syncLine">
      ${busy ? '<span class="spin"></span>reading Canvas…'
        : `Canvas synced <b>${ago(sc.age_s)}</b>${sc.fetched_at
            ? ` (${new Date(sc.fetched_at).toLocaleString([], {
                month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })})` : ''}`}
      <button class="btn sm" id="scRefresh" ${busy ? 'disabled' : ''}>Refresh</button>
    </span>
    ${error ? `<span class="syncErr">refresh failed: ${esc(error)}</span>` : ''}
    ${(sc.partial || []).length ? `<span class="syncErr">${sc.partial.length}
      course(s) could not be read: ${sc.partial.map(p => esc(p.code)).join(', ')}</span>` : ''}
    <span class="footNote">Items with no due date in Canvas are not listed.
      Click a name to grade it here, or the arrow to open it in Canvas.
      Auto-refreshes when the cache is over ${sc.max_age_min || 30} minutes old.</span>`;
  const btn = $('#scRefresh');
  if (btn) btn.onclick = () => refreshSchedule(true);
}

/* --------------------------------------------------------------- courses */
/* Canvas file room. A corner chip rather than a panel: it is a number you
   glance at to know you are nowhere near the ceiling, and it earns about that
   much of the page. The detail lives in the tooltip for the day it matters. */
async function renderStorage() {
  const host = $('#storageBar');
  if (!host) return;
  let d;
  try { d = await api('/storage'); }
  catch (_) { host.innerHTML = ''; return; }      // offline: say nothing at all
  if (!$('#storageBar') || !d.quota) return;

  const mb = n => n / 1e6;
  const pct = Math.min(100, Math.max(0, 100 * d.used / d.quota));
  const level = pct >= 90 ? 'full' : pct >= 70 ? 'warm' : '';
  const carried = (d.handoffs || []).length;
  const tip = `Canvas file storage: ${mb(d.used).toFixed(1)} MB of ${
    Math.round(mb(d.quota))} MB used, ${mb(d.quota - d.used).toFixed(0)} MB free.
`
    + (carried
      ? `${carried} handoff file${carried === 1 ? '' : 's'} in ${d.folder}/ using ${
          mb(d.handoff_bytes).toFixed(1)} MB.`
      : `Nothing carried between machines yet; handoffs would live in ${d.folder}/.`);

  // A caption over the bar: on its own, "18 / 600 MB" in a corner does not say
  // what it is counting. No stray whitespace, because the indentation of a
  // template literal becomes text nodes inside the flex row and wraps the chip.
  host.innerHTML = `<span class="storeChip ${level}" title="${esc(tip)}">`
    + `<span class="storeCap">Canvas Storage</span>`
    + `<span class="storeRow">`
    + `<span class="storeTrack"><span class="storeFill" style="width:${pct.toFixed(2)}%">`
    + `</span></span><span>${Math.round(mb(d.used))} / ${Math.round(mb(d.quota))} MB</span>`
    + `</span></span>`;
}

async function openCourses(refresh) {
  const tb = $('#teachBar'); if (tb) tb.classList.add('hidden');
  showView('picker');
  crumbs([{ label: 'Courses' }]);
  $('#headerActions').innerHTML = '';
  $('#pickerTitle').textContent = 'Your courses';
  $('#pickerHint').textContent = 'Loading…';
  $('#btnRefresh').onclick = () => openCourses(true);
  $('#headerActions').innerHTML =
    '<button class="btn" id="btnSchedule">Term schedule</button>';
  $('#btnSchedule').onclick = () => { location.hash = '#/schedule'; };
  $('#headerActions').insertAdjacentHTML('beforeend',
    '<button class="btn" id="btnToken" title="Replace the saved Canvas token">' +
    'Canvas token…</button>');
  $('#btnToken').onclick = () => openSetup();
  try {
    S.courses = await api('/courses' + (refresh ? '?refresh=1' : ''));
    S.termInfo = await api('/terms');
  } catch (err) {
    $('#pickerHint').textContent = firstLine(err.message) + ' — see the banner above.';
    return;
  }

  // Default to the term we are actually in; remember an explicit choice.
  if (S.term == null) S.term = S.termInfo.default || '__all';
  const list = S.term === '__all'
    ? S.courses
    : S.courses.filter(c => (c.term_label || '') === S.term);

  const opts = (S.termInfo.terms || []).map(t =>
    `<option value="${esc(t.label)}"${t.label === S.term ? ' selected' : ''}>${esc(t.label)} (${t.count})</option>`).join('');
  $('#pickerHint').innerHTML =
    `<select class="termSel" id="selTerm">${opts}
       <option value="__all"${S.term === '__all' ? ' selected' : ''}>All terms (${S.courses.length})</option>
     </select>
     <span style="margin-left:10px">${list.length} course${list.length === 1 ? '' : 's'}</span>`;

  $('#pickerBody').innerHTML = list.length
    ? '<div class="cards">' + list.map(c => `
        <button class="card ${c.excluded ? 'off' : ''}" data-id="${c.id}" ${c.excluded ? 'disabled' : ''}>
          <div class="t">${esc(c.name)}</div>
          <div class="m">${esc(c.term_label || c.term || '')}${c.students != null ? ' · ' + c.students + ' students' : ''}</div>
          ${c.excluded ? '<div class="m" style="margin-top:6px"><span class="pill warn">excluded in config</span></div>' : ''}
        </button>`).join('') + '</div>'
    : '<p style="color:var(--muted)">No courses in this term.</p>';

  renderStorage();
  $('#selTerm').onchange = ev => { S.term = ev.target.value; openCourses(false); };
  $('#pickerBody').querySelectorAll('.card:not(.off)').forEach(b =>
    b.onclick = () => { location.hash = '#/c/' + b.dataset.id; });
}

/* ------------------------------------------------------------ assignments */
async function openCourse(courseId, refresh) {
  const tb = $('#teachBar'); if (tb) tb.classList.add('hidden');
  showView('picker');
  S.course = S.courses.find(c => String(c.id) === String(courseId)) || { id: courseId, name: 'Course ' + courseId };
  crumbs([{ label: 'Courses', href: '#/' }, { label: S.course.name, href: '#/c/' + courseId }, { label: 'Grade' }]);
  $('#headerActions').innerHTML = '';
  $('#pickerTitle').textContent = S.course.name;
  const sb = $('#storageBar'); if (sb) sb.innerHTML = '';
  $('#pickerHint').textContent = 'Loading assignments…';
  $('#btnRefresh').onclick = () => openCourse(courseId, true);
  try {
    S.assignments = await api(`/courses/${courseId}/assignments` + (refresh ? '?refresh=1' : ''));
  } catch (err) {
    $('#pickerHint').textContent = firstLine(err.message) + ' — see the banner above.';
    return;
  }
  const waiting = S.assignments.reduce((a, x) => a + (+x.needs_grading || 0), 0);
  $('#pickerHint').textContent = `${S.assignments.length} assignments`
    + (waiting ? ` · ${waiting} submission${waiting === 1 ? '' : 's'} waiting to grade` : '');
  $('#pickerBody').innerHTML = `<table><thead><tr>
      <th>Assignment</th><th>Due</th><th class="tg">To grade</th>
      <th style="text-align:right">Points</th>
      <th>Rubric</th><th>State</th></tr></thead><tbody>` +
    S.assignments.map(a => `<tr data-id="${a.id}">
        <td><b>${esc(a.name)}</b>${a.is_discussion ? ' <span class="pill">discussion</span>' : ''}
            ${a.published ? '' : ' <span class="pill warn">unpublished</span>'}</td>
        <td style="color:var(--muted)">${esc(fmtDate(a.due_at) || '—')}</td>
        <td class="tg">${a.needs_grading
      ? `<span class="tgBadge" title="${a.needs_grading} submission(s) Canvas says are waiting">${
          a.needs_grading}</span>`
      : '<span class="muted">—</span>'}</td>
        <td class="num">${a.points_possible ?? '—'}</td>
        <td>${a.has_rubric ? '<span class="pill good">rubric</span>' : '<span class="pill warn">none</span>'}</td>
        <td>${a.graded_at ? '<span class="pill ai">graded ' + esc(fmtDate(a.graded_at)) + '</span>'
      : a.synced_at ? '<span class="pill">synced</span>' : ''}
            ${a.has_instructions ? ' <span class="pill">notes</span>' : ''}</td>
      </tr>`).join('') + '</tbody></table>';
  $('#pickerBody').querySelectorAll('tbody tr').forEach(tr =>
    tr.onclick = () => { location.hash = `#/c/${courseId}/a/${tr.dataset.id}`; });
}

/* ------------------------------------------------------------------ handoff */
/* No button. Opening an assignment is when the two machines can be compared, so
   that is when it happens: take a newer copy if this machine has nothing of its
   own to lose, otherwise make sure Canvas has what is here. The one case that
   cannot be decided without asking is both sides having moved, and that gets a
   banner rather than a permanent control nobody needs. */
async function handoffOnOpen(courseId, assignmentId) {
  let r;
  try {
    const { job } = await api(`/a/${courseId}/${assignmentId}/handoff`,
                              { body: { do: 'auto' } });
    for (let i = 0; i < 240; i++) {
      const info = await api('/jobs/' + job);
      if (info.state !== 'running') { r = info.result || {}; break; }
      await new Promise(s => setTimeout(s, 500));
    }
  } catch (_) { return; }             // offline, or Canvas is down: carry on
  if (!r || !S.ids || S.ids.assignmentId !== assignmentId) return;

  if (r.did === 'picked_up') {
    setStatus(`picked up revision ${r.rev} from ${
      (r.from || {}).name || 'another machine'}`
      + (r.attached ? ` · ${r.attached} Blender result(s) restored` : ''), 'ok');
    try {
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      renderHeaderActions(); render();
    } catch (_) { /* the next pull will pick it up */ }
  } else if (r.did === 'diverged') {
    showDivergedBanner(courseId, assignmentId, r);
  }
}

/* The only thing here worth interrupting for: grading on this machine that was
   never carried, and a newer copy in Canvas that has never seen it. Either
   answer loses something, so neither is chosen automatically. */
function showDivergedBanner(courseId, assignmentId, r) {
  if ($('#divergedBar')) return;
  const bar = document.createElement('div');
  bar.id = 'divergedBar';
  bar.className = 'staleBar';
  const where = ((r.remote || {}).machine || {}).name || 'another machine';
  bar.innerHTML = `<b>This assignment has moved in two places.</b>
    Grading here was never carried across, and ${esc(where)} has since handed off
    revision ${(r.remote || {}).rev}. Keeping one means losing the other.
    <button class="btn sm" id="dvMine">Keep this machine's</button>
    <button class="btn sm" id="dvTheirs">Take the Canvas copy</button>`;
  document.body.appendChild(bar);
  const close = () => bar.remove();
  $('#dvMine').onclick = () => {
    close();
    runJob('Carrying this machine\'s work to Canvas',
      () => api(`/a/${courseId}/${assignmentId}/handoff`, { body: { do: 'send' } }),
      out => setStatus(`handed off as revision ${out.rev}`, 'ok'));
  };
  $('#dvTheirs').onclick = () => {
    close();
    runJobConfirmed('Picking up from Canvas',
      token => api(`/a/${courseId}/${assignmentId}/handoff`,
        { body: { do: 'fetch', confirm: token } }),
      async out => {
        setStatus(`picked up revision ${out.rev}`, 'ok');
        try {
          S.ws = await api(`/a/${courseId}/${assignmentId}`);
          renderHeaderActions(); render();
        } catch (_) { /* the next pull will pick it up */ }
      },
      { title: 'Replace the grading on this machine?',
        verb: 'Yes, take the Canvas copy',
        note: 'Anything graded here and not carried across is lost.' });
  };
}

/* -------------------------------------------------------------- workspace */
async function openAssignment(courseId, assignmentId, opts = {}) {
  showView('work');
  S.ids = { courseId, assignmentId };
  setStatus('loading…');
  try {
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
  } catch (err) { setStatus('failed: ' + err.message, 'err'); return; }
  setStatus('');
  const a = S.ws.assignment || {};
  S.assignment = a;
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: (S.course && S.course.name) || 'Course', href: '#/c/' + courseId + '/grade' },
    { label: a.name || 'Assignment' },
  ]);
  renderHeaderActions();
  S.sel = 0;
  render();
  // Choosing an assignment is the request to work on it, so fetch it rather
  // than waiting to be told again. The page is already drawn underneath, so
  // the sync reads as the page filling in. Only on a real arrival: the job
  // callbacks re-open the same assignment to pick up their own results, and
  // that must not kick off another sync.
  if (opts.arriving && staleSync(S.ws)) { doSync({ auto: true }); return; }
  startAutoPull();
  // Line up with the other machine. Fire and forget: it decides for itself and
  // only interrupts when both sides have moved.
  if (opts.arriving) handoffOnOpen(courseId, assignmentId);
}

/* --------------------------------------------------------- grade posting */
/* Two steps, and only two. A push writes scores into the Canvas gradebook and
   always lands them hidden; the push dialog then offers to show them straight
   away. Canvas's own automatic-vs-manual posting policy is not a question the
   instructor is asked any more: the server forces this assignment to manual
   before every push (see App._ensure_manual_posting), which is what makes
   "keep them hidden for now" a promise the tool can keep.

   This is the other half: releasing grades that are already in Canvas and
   hidden, from a push made earlier or on another machine. Changes no score. */
function openRelease(only) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  const scope = (only && only.length) ? only.map(String) : null;
  const canWrite = !!(S.health && S.health.allow_canvas_writes);
  const rows = Object.values(S.ws.extracted || {})
    .filter(s => s.canvas_score != null && (!scope || scope.includes(String(s.user_id))))
    .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const hidden = rows.filter(s => !s.canvas_posted_at);
  const live = rows.filter(s => s.canvas_posted_at);
  const list = arr => arr.map(s => `  ${s.name}: ${num(s.canvas_score)}`).join('\n') || '  none';
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Make grades live${scope ? ` — ${scope.length} selected` : ''}</h3>
      <div class="sub">This is Canvas's own <b>Post grades</b> button. It changes nothing about
        the scores; it lets students see what is already in the gradebook${scope
          ? ' for the students you selected' : ''}. The list is as of the last pull.</div>
      <div class="log" id="relLog">${rows.length
        ? `HIDDEN FROM STUDENTS (${hidden.length}):\n${list(hidden)}\n\nVISIBLE (${live.length}):\n${list(live)}`
        : 'No grades in Canvas yet' + (scope ? ' for these students' : ' for this assignment') + ' — push first.'}</div>
      ${canWrite ? '' : '<div class="callout" style="margin-top:12px">Read-only: Canvas writes are locked off in config.json.</div>'}
      <div class="foot">
        <button class="btn" id="relCancel">Cancel</button>
        <button class="btn" id="relHide" ${(canWrite && live.length) ? '' : 'disabled'}
          title="Hide these grades from students again">Hide again</button>
        <button class="btn primary" id="relGo" ${(canWrite && hidden.length) ? '' : 'disabled'}>
          Make ${hidden.length ? hidden.length + ' ' : ''}live</button>
      </div></div></div>`;
  $('#relCancel').onclick = () => { host.innerHTML = ''; };
  const run = hide => {
    const n = hide ? live.length : hidden.length;
    runJobConfirmed(hide ? 'Hiding grades' : 'Making grades live',
      token => api(`/a/${courseId}/${assignmentId}/release`,
        { body: { only: scope, hide, confirm: token } }),
      async () => { $('#modalHost').innerHTML = ''; await pullNow(false); },
      { title: hide ? `Hide ${n} grade${n === 1 ? '' : 's'} again?`
                    : `Show ${n} grade${n === 1 ? '' : 's'} to students now?`,
        verb: hide ? 'Yes, hide them' : 'Yes, show them',
        note: hide ? 'Students stop seeing these until you post them again.'
                   : 'This is the Canvas Post action. Students may be notified, '
                     + 'and it cannot be quietly undone.' });
  };
  $('#relGo').onclick = () => run(false);
  $('#relHide').onclick = () => run(true);
}

/* ------------------------------------------------------------- pulling */
/* A pull re-reads the gradebook side of every student -- score, rubric, whether
   the student can see it -- and merges it into the draft without touching
   unpushed local work (gradesync.py has the rules). It runs when an assignment
   opens and then on a timer, so grading finished on another machine is waiting
   here. Quiet unless something changed. */
function stopAutoPull() {
  if (S.pullTimer) { clearInterval(S.pullTimer); S.pullTimer = null; }
}
function startAutoPull() {
  stopAutoPull();
  pullNow(true);
  const every = +((S.health || {}).pull_interval_s) || 0;
  if (every > 0) S.pullTimer = setInterval(() => pullNow(true), Math.max(15, every) * 1000);
}
/* Mid-edit? A pull that re-rendered the sliders under a hand on the mouse would
   lose the drag, so a timed pull waits for the next tick instead. */
function editingNow() {
  if (saveTimer) return true;
  const el = document.activeElement;
  return !!(el && /^(INPUT|TEXTAREA)$/.test(el.tagName) && el.closest('#detail'));
}
/* What a re-render would change: enough to skip the render when nothing did. */
function syncPrint(ws) {
  const ex = (ws && ws.extracted) || {}, st = ((ws && ws.draft) || {}).students || {};
  return JSON.stringify([((ws && ws.draft) || {}).post_policy || null,
    Object.keys(ex).sort().map(u => [ex[u].canvas_score, ex[u].canvas_posted_at,
      (st[u] || {}).source, (st[u] || {}).total, (st[u] || {}).final_total, !!(st[u] || {}).conflict])]);
}
async function pullNow(quiet) {
  if (!S.ids || S.pulling) return;
  if (!(S.ws && S.ws.draft && S.ws.draft.synced_at)) return;   // nothing to pull into yet
  if ($('#modalHost').innerHTML) return;                       // a job or dialog owns the page
  if (quiet && editingNow()) return;
  const { courseId, assignmentId } = S.ids;
  S.pulling = true;
  if (!quiet) setStatus('checking Canvas…');
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/pull`, { body: {} });
    if (!S.ids || S.ids.courseId !== courseId || S.ids.assignmentId !== assignmentId) return;
    const was = syncPrint(S.ws);
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const changed = syncPrint(S.ws) !== was;
    if (changed) { renderHeaderActions(); render(); }
    const n = (r.adopted || []).length, c = (r.conflicts || []).length;
    if (changed || !quiet) {
      if (c) setStatus(`${c} conflict${c === 1 ? '' : 's'} with Canvas — see the Conflicts filter`, 'err');
      else if (n) setStatus(`pulled ${n} grade${n === 1 ? '' : 's'} from Canvas`, 'ok');
      else setStatus(`in step with Canvas · ${(r.hidden || []).length} hidden, ${(r.live || []).length} live`, 'ok');
    }
  } catch (err) {
    if (!quiet) setStatus('pull failed: ' + err.message, 'err');
  } finally { S.pulling = false; }
}

async function resolveConflicts(uids, choice) {
  const { courseId, assignmentId } = S.ids;
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/resolve`, { body: { only: uids, choice } });
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const n = (r.settled || []).length;
    setStatus(choice === 'canvas'
      ? `took Canvas for ${n}`
      : `kept yours for ${n} — the next push overwrites Canvas`, 'ok');
    render();
  } catch (err) { setStatus('could not resolve: ' + err.message, 'err'); }
}

function renderHeaderActions() {
  const synced = !!(S.ws.draft && S.ws.draft.synced_at);
  const canAI = !(S.health.claude && S.health.claude.logged_in === false);
  const models = S.health.models || [S.health.model];
  const hasBlend = Object.values(S.ws.extracted || {}).some(
    st => (st.filenames || []).some(f => /\.blend1?$/i.test(f)));
  const blenderOk = !!(S.health.blender && S.health.blender.ok);
  // Chasing missing work only makes sense once the deadline has gone.
  const dueAt = (S.ws.assignment || {}).due_at;
  const pastDue = !!(dueAt && new Date(dueAt).getTime() < Date.now());
  $('#headerActions').innerHTML = `
    <button class="btn" id="btnSync">${synced ? 'Re-sync' : 'Sync from Canvas'}</button>
    ${hasBlend ? `<button class="btn" id="btnBlend" ${blenderOk ? '' : 'disabled'}
      title="${blenderOk ? 'Open every .blend in Blender and extract stats, renders and a 3D preview'
                         : 'Blender was not found on this machine'}">Blender pass</button>` : ''}
    <select class="modelSel" id="selModel" title="Which Claude model grades with">
      ${models.map(m => `<option value="${esc(m)}"${m === S.health.model ? ' selected' : ''}>${esc(m)}</option>`).join('')}
    </select>
    <button class="btn ai" id="btnGrade" ${(!synced || !canAI) ? 'disabled' : ''}
      title="${canAI ? 'Grade every student with Claude against this rubric' : 'Claude CLI is not logged in'}">Auto-grade all</button>
    <button class="btn" id="btnView"
      title="Read what this assignment asks for, as Canvas has it now">View assignment</button>
    ${pastDue ? `<button class="btn" id="btnRemind"
      title="Message every student who has turned nothing in for this">Remind missing</button>` : ''}
    <button class="btn" id="btnInstr">Instructions${S.ws.instructions ? ' •' : ''}</button>
    <button class="btn" id="btnWork">${S.showWork ? 'Hide work' : 'Show work'}</button>
    <button class="btn ${S.view === 'insights' ? 'on' : ''}" id="btnCharts" ${synced ? '' : 'disabled'}>${S.view === 'insights' ? 'Back to grading' : 'Insights'}</button>
    <button class="btn" id="btnExport" ${synced ? '' : 'disabled'}>Export</button>
    <button class="btn danger" id="btnPush" ${synced ? '' : 'disabled'}>Push to Canvas…</button>
    <button class="btn" id="btnRelease" ${synced ? '' : 'disabled'}
      title="Show students the grades that are already in Canvas for this assignment">Make live…</button>`;
  $('#btnSync').onclick = doSync;
  $('#btnRelease').onclick = () => openRelease(null);
  $('#btnGrade').onclick = () => doGrade(null);
  $('#selModel').onchange = ev => setModel(ev.target.value);
  const bb = $('#btnBlend');
  if (bb) bb.onclick = () => runJob('Reading .blend files with Blender',
    () => api(`/a/${S.ids.courseId}/${S.ids.assignmentId}/blend`, { body: {} }),
    () => openAssignment(S.ids.courseId, S.ids.assignmentId));
  $('#btnCharts').onclick = () => {
    S.view = S.view === 'insights' ? 'grade' : 'insights';
    renderHeaderActions(); render();
  };
  $('#btnView').onclick = () => openScheduleItem(S.ids.courseId, S.ids.assignmentId);
  $('#btnInstr').onclick = openInstructions;
  $('#btnWork').onclick = toggleWork;
  $('#btnExport').onclick = doExport;
  $('#btnPush').onclick = openPush;
}

/* ------------------------------------------------------------------ setup */
/* Connecting to Canvas is the one thing a new user has to do by hand, and
   asking them to create a file with an exact name containing exactly one line
   is a step that goes wrong quietly. They paste it here instead and the server
   writes the file, after checking the token actually works.

   The value goes one way only: it is posted to this machine's own server and
   never read back out of it. */
async function openSetup(afterwards) {
  const host = $('#modalHost');
  let state = {};
  try { state = await api('/setup'); } catch (err) { state = {}; }

  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Connect to Canvas</h3>
      <div class="sub">Two things, once. They are saved on this machine only.</div>

      <div class="setupForm">
        <label>Your Canvas web address
          <input type="text" id="suHost" spellcheck="false" autocomplete="off"
            value="${esc(state.base_url || 'https://')}"
            placeholder="https://yourschool.instructure.com">
          <span class="setupHint">The address you log in at, nothing after
            <code>.com</code>.</span>
        </label>

        <label>Access token
          <span class="setupToken">
            <input type="password" id="suToken" spellcheck="false"
              autocomplete="off" placeholder="paste the token from Canvas">
            <button type="button" class="btn sm" id="suShow"
              title="Show what you pasted">Show</button>
          </span>
          <span class="setupHint" id="suWhere"></span>
        </label>
      </div>

      <details class="setupHow" ${state.token_len ? '' : 'open'}>
        <summary>Where do I get a token?</summary>
        <ol>
          <li>Open <a id="suLink" href="#" target="_blank" rel="noopener">your
            Canvas account settings</a></li>
          <li>Scroll to <b>Approved Integrations</b> and click
            <b>+ New Access Token</b></li>
          <li>Purpose: anything, e.g. <code>courseforge-studio</code>. Leave the
            expiry blank.</li>
          <li><b>Generate Token</b>, then copy it straight away — Canvas will not
            show it again</li>
        </ol>
      </details>

      <div id="suResult"></div>

      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="suCancel">Cancel</button>
        <button class="btn ai" id="suSave">Check and save</button>
      </div>
    </div></div>`;

  const setWhere = () => {
    $('#suWhere').innerHTML = state.token_len
      ? `A token is already saved (${state.token_len} characters). Pasting a new
         one replaces it.`
      : `Saved to <code>${esc(state.token_path || 'canvas.token')}</code>, which
         is gitignored. It is only ever sent to your own Canvas.`;
  };
  setWhere();

  const syncLink = () => {
    const base = ($('#suHost').value || '').trim().replace(/\/+$/, '');
    $('#suLink').href = (base.startsWith('http') ? base : 'https://' + base)
      + '/profile/settings';
  };
  syncLink();
  $('#suHost').oninput = syncLink;

  $('#suShow').onclick = () => {
    const field = $('#suToken');
    const shown = field.type === 'text';
    field.type = shown ? 'password' : 'text';
    $('#suShow').textContent = shown ? 'Show' : 'Hide';
  };
  $('#suCancel').onclick = () => { host.innerHTML = ''; };

  const save = async (force) => {
    const token = $('#suToken').value;
    const base_url = $('#suHost').value;
    $('#suSave').disabled = true;
    $('#suResult').innerHTML = '<div class="setupBusy"><span class="spin"></span>' +
      'asking Canvas whether that token works…</div>';
    let r;
    try {
      r = await api('/setup/token', { body: { token, base_url, force } });
    } catch (err) {
      r = { ok: false, error: err.message };
    }
    $('#suSave').disabled = false;
    if (!r.ok) {
      $('#suResult').innerHTML = `<div class="callout bad">${esc(r.error || 'failed')}
        ${r.reason === 'unreachable'
          ? '<button class="btn sm" id="suForce">Save it anyway</button>' : ''}</div>`;
      const forceBtn = $('#suForce');
      if (forceBtn) forceBtn.onclick = () => save(true);
      return;
    }
    $('#suResult').innerHTML = `<div class="callout ok">
      <b>${r.verified ? 'Connected as ' + esc(r.name) + '.' : 'Saved.'}</b>
      Written to <code>${esc(r.path)}</code>.
      ${r.env_override ? '<br><b>Note:</b> a CANVAS_TOKEN environment variable is '
        + 'set on this machine and takes priority over this file, so the app will '
        + 'keep using that one until you clear it.' : ''}</div>`;
    // Let them read the confirmation, then carry on into the app.
    setTimeout(async () => {
      host.innerHTML = '';
      try { S.health = await api('/health'); } catch (err) { /* banner covers it */ }
      renderBanners();
      if (afterwards) afterwards(); else openCourses(true);
    }, 1200);
  };
  $('#suSave').onclick = () => save(false);
  $('#suToken').onkeydown = ev => { if (ev.key === 'Enter') save(false); };
  $('#suToken').focus();
}

/* Never synced means there is nothing on screen to work with, so it always
   syncs. After that, a copy younger than assignment_max_age_min is taken as
   current: re-syncing is not free, and the grade pull on a timer already keeps
   scores in step. Set that to 0 in config.json to sync on every open. */
function staleSync(ws) {
  const at = ws && ws.draft && ws.draft.synced_at;
  if (!at) return true;
  const maxMin = (S.health || {}).assignment_max_age_min;
  const limit = (maxMin == null ? 15 : +maxMin);
  if (!(limit > 0)) return true;
  const age = (Date.now() - new Date(at).getTime()) / 60000;
  return !(age >= 0) || age > limit;      // an unreadable date counts as stale
}

function doSync(opts = {}) {
  const { courseId, assignmentId } = S.ids;
  runJob('Syncing from Canvas', () => api(`/a/${courseId}/${assignmentId}/sync`, { body: {} }),
    () => {
      openAssignment(courseId, assignmentId);
      if (opts.auto) setStatus('synced from Canvas just now', 'ok');
    },
    { autoClose: !!opts.auto });
}
function doGrade(only) {
  const { courseId, assignmentId } = S.ids;
  const n = only ? only.length : Object.keys(S.ws.extracted || {}).length;
  const label = only ? `Grading 1 student` : `Auto-grading ${n} students with ${S.health.model}`;
  runJob(label, () => api(`/a/${courseId}/${assignmentId}/grade`, { body: { only } }),
    () => openAssignment(courseId, assignmentId));
}
async function setModel(model) {
  const previous = S.health.model;
  try {
    const r = await api('/settings', { body: { model } });
    S.health.model = r.model;
    setStatus('grading model: ' + r.model, 'ok');
  } catch (err) {
    S.health.model = previous;
    const sel = $('#selModel'); if (sel) sel.value = previous;
    setStatus('could not switch model: ' + err.message, 'err');
  }
}

function doExport() {
  const { courseId, assignmentId } = S.ids;
  api(`/a/${courseId}/${assignmentId}/export`, { body: {} })
    .then(r => setStatus(`exported ${r.rows} rows`, 'ok'))
    .catch(err => setStatus('export failed: ' + err.message, 'err'));
}

/* ---------------------------------------------------------- instructions */
function openInstructions() {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Custom grading instructions</h3>
      <div class="sub">Anything Claude should know that is not in the Canvas description or rubric —
        a requirement you changed in class, an extension you granted, a common misreading to be lenient about,
        how strictly to treat length floors. These override the rubric wording where they conflict.</div>
      <textarea class="instructions" id="instrText" placeholder="e.g. I told the class in person they could use any game, not just the five on the list — do not dock the edge-case table for off-list cases this time.">${esc(S.ws.instructions || '')}</textarea>
      <div class="foot">
        <button class="btn" id="instrCancel">Cancel</button>
        <button class="btn primary" id="instrSave">Save</button>
      </div></div></div>`;
  $('#instrCancel').onclick = () => { host.innerHTML = ''; };
  $('#instrSave').onclick = async () => {
    const text = $('#instrText').value;
    try {
      await api(`/a/${courseId}/${assignmentId}/instructions`, { body: { text } });
      S.ws.instructions = text;
      host.innerHTML = '';
      renderHeaderActions();
      setStatus('instructions saved', 'ok');
    } catch (err) { setStatus('save failed: ' + err.message, 'err'); }
  };
}

/* ------------------------------------------------------------------- ask */
const PROMPTS = [
  'Did they actually cite a source, or just name one?',
  'Is there anything here that reads like it was written by AI?',
  'Quote every place they name a specific rule or mechanic.',
  'Did they answer every part of the prompt? List what is missing.',
  'Is my score for the weakest criterion defensible? Argue the other side.',
];

function openAsk(student) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  S.askHistory = S.askHistory || {};
  const key = assignmentId + ':' + student.user_id;
  const history = S.askHistory[key] = S.askHistory[key] || [];

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Ask about ${esc(student.name)}'s work</h3>
      <div class="sub">Claude answers from this submission only, and says so when the
        answer is not in there. Nothing here changes the grade.</div>
      <div class="askThread" id="askThread"></div>
      <div class="askChips">${PROMPTS.map((p, i) =>
    `<button class="chip" data-p="${i}">${esc(p)}</button>`).join('')}</div>
      <textarea id="askText" class="askBox" rows="3"
        placeholder="Ask anything about this submission…"></textarea>
      <div class="foot">
        <span class="sub" id="askState" style="margin:0;flex:1;text-align:left"></span>
        <button class="btn" id="askClose">Close</button>
        <button class="btn ai" id="askGo">Ask</button>
      </div></div></div>`;

  const thread = $('#askThread');
  const paint = () => {
    thread.innerHTML = history.length ? history.map(t =>
      `<div class="askTurn ${t.role}"><b>${t.role === 'user' ? 'You' : 'Claude'}</b>
        <div>${esc(t.text)}</div></div>`).join('')
      : '<div class="askEmpty">No questions yet.</div>';
    thread.scrollTop = 1e6;
  };
  paint();

  $('#askClose').onclick = () => { host.innerHTML = ''; };
  host.querySelectorAll('.askChips .chip').forEach(b => b.onclick = () => {
    $('#askText').value = PROMPTS[+b.dataset.p]; $('#askText').focus();
  });

  const ask = async () => {
    const q = $('#askText').value.trim();
    if (!q) return;
    history.push({ role: 'user', text: q });
    $('#askText').value = '';
    paint();
    $('#askGo').disabled = true;
    $('#askState').textContent = 'thinking…';
    try {
      busyOn();
      const { job } = await api(`/a/${courseId}/${assignmentId}/ask/${student.user_id}`,
        { body: { question: q, history: history.slice(0, -1) } });
      const poll = async () => {
        const info = await api('/jobs/' + job);
        if (info.state === 'running') {
          const live = (info.items || [])[0];
          $('#askState').textContent = live
            ? `${live.state}${live.detail ? ' · ' + live.detail : ''} · ${clock(live.elapsed_s)}`
            : 'thinking…';
          return setTimeout(poll, 700);
        }
        busyOff();
        $('#askGo').disabled = false;
        $('#askState').textContent = '';
        if (info.state === 'error') {
          history.push({ role: 'error', text: info.error || 'failed' });
        } else {
          history.push({ role: 'claude', text: (info.result || {}).answer || '(no answer)' });
        }
        paint();
      };
      poll();
    } catch (err) {
      busyOff();
      $('#askGo').disabled = false;
      $('#askState').textContent = '';
      history.push({ role: 'error', text: err.message });
      paint();
    }
  };
  $('#askGo').onclick = ask;
  $('#askText').addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); ask(); }
  });
  $('#askText').focus();
}

/* --------------------------------------------------------------- overlap */
function pctText(v) { return (100 * (v || 0)).toFixed(0) + '%'; }

function openOverlapReport(r) {
  const host = $('#modalHost');
  if (!r) return;
  const notable = (r.pairs || []).filter(p => p.notable);
  const rest = (r.pairs || []).filter(p => !p.notable);

  const pairCard = p => `
    <div class="ovPair${p.worth_a_conversation ? ' hot' : ''}">
      <div class="ovWho">
        <b>${esc(p.a_name || p.a)}</b> <span class="ovAmp">and</span>
        <b>${esc(p.b_name || p.b)}</b>
        <span class="ovNums">${p.longest_words} words in a row ·
          ${pctText(p.containment)} of the shorter one</span>
      </div>
      ${p.reading ? `<div class="ovTags"><span class="tag">${esc(p.reading)}</span>
        ${p.worth_a_conversation ? '<span class="tag warn">worth a conversation</span>' : ''}</div>` : ''}
      ${p.what_i_see ? `<p class="ovSee">${esc(p.what_i_see)}</p>` : ''}
      ${p.innocent_explanation
        ? `<p class="ovBenign"><b>Most likely innocent explanation.</b>
             ${esc(p.innocent_explanation)}</p>` : ''}
      <div class="ovPassages">
        ${(p.passages || []).slice(0, 3).map(x =>
          `<div class="ovQuote"><span class="ovLen">${x.words}w</span>${esc(x.text)}</div>`).join('')}
      </div>
    </div>`;

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Overlap check</h3>
      <div class="sub">${r.n} submissions · ${r.compared != null ? r.compared
        : (r.pairs || []).length} pairs compared ·
        ${r.window_words}-word windows${r.model ? ' · read by ' + esc(r.model) : ''}
        ${r.cost_usd ? ' · $' + Number(r.cost_usd).toFixed(3) : ''}</div>

      <div class="callout" style="margin:12px 0">
        <b>This measures shared wording. It is not a finding of misconduct.</b>
        Wording the whole group shares (the prompt, the rubric, your instructions,
        anything common to more than a third of these submissions) was removed
        before measuring, so what is quoted below is wording these submissions
        share and the others do not. Students still overlap for innocent reasons:
        the same source, a narrow prompt, permitted collaboration, the same
        tutorial. Read the passages yourself before drawing any conclusion, and
        take anything further through your institution's process.
      </div>

      ${r.read ? `<p class="ovNote">${esc(r.read)}</p>` : ''}

      ${notable.length ? notable.map(pairCard).join('')
        : '<p class="ovNote">No pair stood out enough to quote.</p>'}

      ${(r.no_text || []).length ? `<div class="ovAside">
        <b>Not compared — nothing the student wrote:</b>
        ${(r.no_text || []).map(x => esc(x.label) + ' (' + esc(x.why) + ')').join('; ')}
      </div>` : ''}
      ${(r.skipped_short || []).length ? `<div class="ovAside">
        <b>Too short to compare:</b> ${(r.skipped_short || []).map(esc).join(', ')}
      </div>` : ''}

      ${rest.length ? `<details class="ovRest"><summary>${rest.length} pair(s)
        below the threshold</summary>
        <table class="ovTable"><tbody>
        ${rest.map(p => `<tr><td>${esc(p.a_name || p.a)}</td><td>${esc(p.b_name || p.b)}</td>
          <td>${p.longest_words}w</td><td>${pctText(p.containment)}</td></tr>`).join('')}
        </tbody></table></details>` : ''}

      <div class="foot">
        <button class="btn" id="ovCopy">Copy as text</button>
        <button class="btn" id="ovClose">Close</button>
      </div>
    </div></div>`;
  $('#ovClose').onclick = () => { host.innerHTML = ''; };
  $('#ovCopy').onclick = () => {
    const lines = [`Overlap check - ${r.n} submissions, ${r.compared != null ? r.compared
      : (r.pairs || []).length} pairs`,
      'This measures shared wording. It is not a finding of misconduct.', ''];
    if (r.read) lines.push(r.read, '');
    notable.forEach(p => {
      lines.push(`${p.a_name || p.a} and ${p.b_name || p.b}: ${p.longest_words} words in a row, ` +
        `${pctText(p.containment)} of the shorter submission` +
        (p.reading ? ` - ${p.reading}` : ''));
      if (p.what_i_see) lines.push('  ' + p.what_i_see);
      if (p.innocent_explanation) lines.push('  Innocent explanation: ' + p.innocent_explanation);
      (p.passages || []).slice(0, 3).forEach(x => lines.push(`  (${x.words}w) "${x.text}"`));
      lines.push('');
    });
    navigator.clipboard.writeText(lines.join('\n'))
      .then(() => setStatus('overlap report copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
}

/* ------------------------------------------------------------- teaching zone */
/* The bar along the bottom is a different job from the bar along the top. The
   top bar acts on grades: sync, grade, curve, push. Nothing down here changes a
   grade or touches Canvas. It reads the same results back as evidence about the
   teaching and the assignment, which is a question the instructor asks after the
   grading is done, so it gets its own zone and its own colour. */
function teachSignal() { return (S.ws && S.ws.teaching_signal) || null; }

function renderTeachBar() {
  const bar = $('#teachBar');
  if (!bar) return;
  // S.view is only set once someone toggles to insights, so it says nothing
  // about whether an assignment is open. The workspace payload does.
  if (!S.ws || !S.ids) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');

  const sig = teachSignal();
  const graded = sig ? sig.n_graded : 0;
  const scoped = S.picked.size ? ` · ${S.picked.size} selected` : '';
  const un = (sig && sig.unscored) || {};
  const nMissing = (un.no_submission || []).length;

  if (!graded) {
    bar.innerHTML = `<div class="teachLabel">Teaching</div>
      <span class="teachIdle">Grade a few students and this bar starts reading the
        results back as feedback on the assignment.</span>`;
    return;
  }

  // The chips are measured locally, so they are already true before anyone asks.
  const chips = [];
  const weakest = (sig.health || [])[0];
  if (weakest && weakest.mean_pct < 70) {
    chips.push({ cls: 'warn', text: `weakest row: ${esc(weakest.label)}
      ${weakest.mean_pct}%`, go: 'rubric' });
  }
  const notDoingJob = (sig.health || []).filter(h =>
    h.findings.some(f => ['ceiling', 'no_spread', 'weak_link'].includes(f.kind))).length;
  if (notDoingJob) {
    chips.push({ cls: '', text: `${notDoingJob} rubric row${notDoingJob > 1 ? 's' : ''}
      not separating students`, go: 'rubric' });
  }
  const voice = sig.voice || { n_students: 0, by_category: [] };
  if (voice.n_students) {
    const top = voice.by_category[0];
    chips.push({ cls: 'voice', text: `${voice.n_students} student${voice.n_students > 1 ? 's' : ''}
      told you something${top ? ` · ${top.count} ${esc(top.label)}` : ''}`, go: 'voice' });
  }
  if ((sig.patterns || []).length) {
    chips.push({ cls: 'warn', text: `${sig.patterns.length} weakness${
      sig.patterns.length > 1 ? 'es' : ''} recurring across the course`, go: 'patterns' });
  }
  if (!chips.length) {
    chips.push({ cls: 'good', text: 'nothing measured stands out on this one',
      go: 'rubric' });
  }

  bar.innerHTML = `
    <div class="teachLabel">Teaching</div>
    <div class="teachChips">${chips.map((c, i) =>
      `<button class="teachChip ${c.cls}" data-go="${c.go}" data-i="${i}">${c.text}</button>`).join('')}</div>
    <span class="spacer"></span>
    <span class="teachMeta">${graded} graded${
      nMissing ? ` · ${nMissing} did not submit` : ''}${scoped}</span>
    <button class="btn sm" id="tbRubric">Rubric health</button>
    <button class="btn sm" id="tbVoice" ${voice.n_students ? '' : 'disabled'}
      title="${voice.n_students ? 'What students said about the assignment in their own words'
                                : 'No student wrote anything about their own experience'}">Student voice</button>
    <button class="btn sm" id="tbPatterns" ${(sig.patterns || []).length ? '' : 'disabled'}
      title="${(sig.patterns || []).length ? 'Rubric rows that keep coming out weak in this course'
                                           : 'Needs more than one graded assignment in this course'}">Course patterns</button>
    <button class="btn sm ai" id="tbRead">${sig.has_read ? 'Teaching read ✓' : 'What should I change?'}</button>`;

  bar.querySelectorAll('.teachChip').forEach(el => {
    el.onclick = () => openTeaching(el.dataset.go);
  });
  $('#tbRubric').onclick = () => openTeaching('rubric');
  $('#tbVoice').onclick = () => openTeaching('voice');
  $('#tbPatterns').onclick = () => openTeaching('patterns');
  $('#tbRead').onclick = doTeachingRead;
}

function doTeachingRead() {
  const { courseId, assignmentId } = S.ids;
  const only = S.picked.size ? pickedIds() : null;
  runJob(only ? `Reading ${only.length} selected results` : 'Reading the results back',
    () => api(`/a/${courseId}/${assignmentId}/teaching`, { body: { only } }),
    async read => {
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      $('#modalHost').innerHTML = '';
      render();
      openTeachingRead(read);
    });
}

/* ------------------------------------------------------- the measured panels */
function openTeaching(which) {
  const sig = teachSignal();
  if (!sig) return;
  const host = $('#modalHost');
  const draft = S.ws.draft || {};
  const saved = draft.teaching;

  const tabs = [
    ['rubric', 'Rubric health'],
    ['voice', `Student voice${sig.voice.n_students ? ` (${sig.voice.n_students})` : ''}`],
    ['patterns', `Course patterns${(sig.patterns || []).length ? ` (${sig.patterns.length})` : ''}`],
  ];

  host.innerHTML = `<div class="modalBack"><div class="modal wide teachModal">
      <h3>Teaching signal</h3>
      <div class="sub">Measured from ${sig.n_graded} graded submissions. Nothing
        here changes a grade.</div>
      <div class="teachTabs">${tabs.map(([id, label]) =>
        `<button class="chip" data-tab="${id}" aria-pressed="${id === which}">${label}</button>`).join('')}</div>
      <div id="teachBody"></div>
      <div class="foot">
        ${saved ? '<button class="btn" id="tShowRead">Show the teaching read</button>' : ''}
        <span class="spacer"></span>
        <button class="btn" id="tClose">Close</button>
      </div>
    </div></div>`;

  const paint = id => {
    $('#teachBody').innerHTML =
      id === 'voice' ? voiceHTML(sig.voice)
        : id === 'patterns' ? patternsHTML(sig.patterns)
          : rubricHealthHTML(sig.health, sig.n_graded);
    host.querySelectorAll('.teachTabs .chip').forEach(c =>
      c.setAttribute('aria-pressed', String(c.dataset.tab === id)));
  };
  host.querySelectorAll('.teachTabs .chip').forEach(c => {
    c.onclick = () => paint(c.dataset.tab);
  });
  $('#tClose').onclick = () => { host.innerHTML = ''; };
  const sr = $('#tShowRead');
  if (sr) sr.onclick = () => openTeachingRead(saved);
  paint(which || 'rubric');
}

const FINDING_WORDS = {
  ceiling: 'not separating anyone', no_spread: 'no spread',
  floor: 'almost nobody scored', weak: 'class did not clear it',
  weak_link: 'measures something else',
};

function rubricHealthHTML(health, n) {
  if (!health || !health.length) {
    return '<p class="teachNote">This assignment has no rubric to analyse.</p>';
  }
  return `
    <p class="teachNote">Each row of your rubric, worst average first. The point of
      this table is the rows that are not doing their job: a row everyone aces is
      not measuring anything, and a row nobody clears is usually about the
      teaching or the wording rather than the class.</p>
    <table class="teachTable">
      <thead><tr><th>Rubric row</th><th>Average</th><th>Spread</th>
        <th>Full marks</th><th>Near zero</th><th>Tracks grade</th></tr></thead>
      <tbody>${health.map(h => `
        <tr class="${h.findings.length ? 'flagged' : ''}">
          <td><b>${esc(h.label)}</b> <span class="muted">${num(h.points)} pts</span>
            ${h.findings.map(f => `<div class="teachFinding">
              <span class="tag ${f.kind === 'ceiling' || f.kind === 'no_spread' ? '' : 'warn'}">${
                FINDING_WORDS[f.kind] || f.kind}</span> ${esc(f.text)}</div>`).join('')}</td>
          <td class="mono">${num(h.mean)}<span class="muted">/${num(h.points)}</span>
            <div class="muted">${h.mean_pct}%</div></td>
          <td class="mono">${h.spread_pct}%</td>
          <td class="mono">${h.at_ceiling}/${h.n}</td>
          <td class="mono">${h.at_floor}/${h.n}</td>
          <td class="mono">${h.r_with_rest == null
            ? `<span class="muted" title="needs at least 8 graded students">—</span>`
            : h.r_with_rest}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function voiceHTML(voice) {
  if (!voice || !voice.n_students) {
    return `<p class="teachNote">No student wrote anything about their own
      experience of this assignment, in their submission or in a Canvas comment.
      That is not the same as nothing being wrong; it usually means there was
      nowhere obvious to say it.</p>`;
  }
  return `
    <p class="teachNote">Sentences where a student is talking about their own
      experience rather than about the subject. Pulled from what they wrote and
      from their Canvas comments, never from anything this tool generated.</p>
    <div class="voiceBands">${voice.by_category.map(b =>
      `<span class="tag">${b.count} × ${esc(b.label)}</span>`).join('')}</div>
    <div class="voiceList">${voice.students.map(p => `
      <div class="voicePerson">
        <div class="voiceWho">${esc(p.name)}</div>
        ${p.items.map(i => `<div class="voiceQuote">
          <span class="voiceCat">${esc(i.category)}${
            i.source === 'comment' ? ' · Canvas comment' : ''}</span>
          ${esc(i.text)}</div>`).join('')}
      </div>`).join('')}</div>`;
}

function patternsHTML(patterns) {
  if (!patterns || !patterns.length) {
    return `<p class="teachNote">Nothing recurring yet. This compares rubric rows
      across every graded assignment in this course and needs the same row to come
      out weak in at least two of them.</p>`;
  }
  return `
    <p class="teachNote">Rubric rows that came out weak in more than one assignment
      in this course, matched on their wording. One weak row is an assignment; the
      same row weak repeatedly is the course.</p>
    <div class="patList">${patterns.map(p => `
      <div class="patItem">
        <div class="patHead"><b>${esc(p.label)}</b>
          <span class="muted">weak in ${p.times_weak} of ${p.times_seen}
            assignments · ${p.mean_pct}% average where weak</span></div>
        <ul>${p.where.map(w => `<li>${esc(w.assignment)} —
          <span class="mono">${w.mean_pct}%</span></li>`).join('')}</ul>
      </div>`).join('')}</div>`;
}

/* ------------------------------------------------------------ the model read */
const CAUSE_WORDS = {
  'not taught': 'not taught',
  'taught but not practised': 'taught, not practised',
  'assignment was unclear': 'the assignment was unclear',
  'tooling or logistics': 'tooling or logistics',
};

function openTeachingRead(r) {
  if (!r) return;
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide teachModal">
      <h3>What to change</h3>
      <div class="sub">${r.n_graded} graded submissions, class average
        ${r.mean_pct}%${r.model ? ' · read by ' + esc(r.model) : ''}${
        r.cost_usd ? ' · $' + Number(r.cost_usd).toFixed(3) : ''}${
        r.scope === 'selection' ? ' · selected students only' : ''}</div>

      ${r.headline ? `<p class="teachHeadline">${esc(r.headline)}</p>` : ''}

      ${(r.reteach || []).length ? `<h4 class="teachH">Worth going back over</h4>
        <div class="reteachList">${r.reteach.map((t, i) => `
          <div class="reteachItem">
            <div class="reteachTop">
              <span class="reteachNum">${i + 1}</span>
              <b>${esc(t.what)}</b>
              ${t.cause ? `<span class="tag ${
                t.cause === 'assignment was unclear' ? 'warn' : ''}">${
                esc(CAUSE_WORDS[t.cause] || t.cause)}</span>` : ''}
              ${t.students_affected != null
                ? `<span class="muted">${t.students_affected} students</span>` : ''}
            </div>
            ${t.evidence ? `<p class="reteachWhy"><b>Evidence.</b> ${esc(t.evidence)}</p>` : ''}
            ${t.action ? `<p class="reteachDo"><b>Next class.</b> ${esc(t.action)}</p>` : ''}
          </div>`).join('')}</div>` : ''}

      ${(r.assignment_fixes || []).length ? `<h4 class="teachH">Changes to the
        assignment itself</h4>
        <div class="fixList">${r.assignment_fixes.map(f => `
          <div class="fixItem"><b>${esc(f.what)}</b>
            ${f.why ? `<span class="muted">${esc(f.why)}</span>` : ''}</div>`).join('')}</div>` : ''}

      ${r.worked ? `<h4 class="teachH">What clearly landed</h4>
        <p class="teachNote">${esc(r.worked)}</p>` : ''}
      ${r.watch_next_time ? `<h4 class="teachH">What to watch next time</h4>
        <p class="teachNote">${esc(r.watch_next_time)}</p>` : ''}

      <div class="foot">
        <button class="btn" id="trCopy">Copy as text</button>
        <button class="btn" id="trEvidence">Show the measured evidence</button>
        <span class="spacer"></span>
        <button class="btn" id="trClose">Close</button>
      </div>
    </div></div>`;
  $('#trClose').onclick = () => { host.innerHTML = ''; };
  $('#trEvidence').onclick = () => openTeaching('rubric');
  $('#trCopy').onclick = () => {
    const lines = [`What to change - ${r.n_graded} graded, average ${r.mean_pct}%`, ''];
    if (r.headline) lines.push(r.headline, '');
    (r.reteach || []).forEach((t, i) => {
      lines.push(`${i + 1}. ${t.what}` + (t.cause ? ` [${t.cause}]` : ''));
      if (t.evidence) lines.push(`   Evidence: ${t.evidence}`);
      if (t.action) lines.push(`   Next class: ${t.action}`);
      lines.push('');
    });
    if ((r.assignment_fixes || []).length) {
      lines.push('Changes to the assignment:');
      r.assignment_fixes.forEach(f => lines.push(`- ${f.what}${f.why ? ` (${f.why})` : ''}`));
      lines.push('');
    }
    if (r.worked) lines.push('What landed: ' + r.worked, '');
    if (r.watch_next_time) lines.push('Watch next time: ' + r.watch_next_time);
    navigator.clipboard.writeText(lines.join('\n'))
      .then(() => setStatus('teaching read copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
}

/* ----------------------------------------------------------------- curves */
const CURVE_KINDS = [
  { id: 'target_mean', label: 'Raise the average to…',
    hint: 'Everyone gains the same number of points, enough to move the average to your target. This is the one for a criterion the class did badly on.',
    needs: 'target', suffix: '%', dflt: 75 },
  { id: 'flat', label: 'Add a flat number of points',
    hint: 'Everyone gains the same points, no matter where they started.',
    needs: 'amount', suffix: 'points', dflt: 5 },
  { id: 'sqrt', label: 'Square-root curve',
    hint: 'Helps the bottom of the class most and the top barely at all: a 49% becomes a 70%, a 90% becomes a 95%. Use this when the whole class did poorly across the board.',
    needs: null },
  { id: 'floor', label: 'Lift everyone below…',
    hint: 'Nobody ends below your floor. Students already above it are untouched.',
    needs: 'target', suffix: '%', dflt: 60 },
  { id: 'to_top', label: 'Curve to the top score',
    hint: 'Adds whatever the highest scorer was missing to everyone, so the best paper becomes full marks.',
    needs: null },
  { id: 'scale', label: 'Scale every score up by…',
    hint: 'Multiplies each score, so it gives more points to students who already had more.',
    needs: 'amount', suffix: '%', dflt: 10 },
];

function openCurve(only) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  const scope = (only && only.length) ? only.slice() : null;
  const crits = rubric();
  const anyCurve = Object.values((S.ws.draft || {}).students || {})
    .some(e => e && (e.curve || e.final_total != null));

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Curve grades${scope ? ` — ${scope.length} selected` : ' — whole class'}</h3>
      <div class="sub">Nothing changes until you press Apply. The earned score is
        always kept underneath, so a curve can be removed exactly.</div>

      <div class="curveForm">
        <label>Apply to
          <select id="cvScope">
            <option value="total">the whole score</option>
            ${crits.map(c => `<option value="${esc(c.id)}">${esc(c.label)}
              (${num(c.points)} pts)</option>`).join('')}
          </select>
        </label>
        <label>Curve
          <select id="cvKind">
            ${CURVE_KINDS.map(k => `<option value="${k.id}">${esc(k.label)}</option>`).join('')}
          </select>
        </label>
        <label id="cvValWrap">Value
          <span class="cvVal"><input type="number" id="cvVal" step="0.5" value="75">
            <span id="cvSuffix">%</span></span>
        </label>
        <label>Note (optional)
          <input type="text" id="cvLabel" maxlength="80"
            placeholder="why you are curving, for your own record">
        </label>
      </div>
      <p class="cvHint" id="cvHint"></p>

      <div id="cvPreview" class="cvPreview">Working out the preview…</div>

      <div class="foot">
        ${anyCurve ? `<button class="btn danger" id="cvRemove">Remove
          ${scope ? 'their' : 'all'} curves</button>` : ''}
        <span class="spacer"></span>
        <button class="btn" id="cvCancel">Cancel</button>
        <button class="btn ai" id="cvApply" disabled>Apply curve</button>
      </div>
    </div></div>`;

  const kindOf = () => CURVE_KINDS.find(k => k.id === $('#cvKind').value);
  let latest = null;

  function syncForm() {
    const k = kindOf();
    $('#cvHint').textContent = k.hint;
    $('#cvValWrap').hidden = !k.needs;
    if (k.needs) {
      $('#cvSuffix').textContent = k.suffix;
      if (document.activeElement !== $('#cvVal')) $('#cvVal').value = k.dflt;
    }
  }

  async function preview() {
    const k = kindOf();
    const body = {
      kind: k.id, scope: $('#cvScope').value, only: scope,
      amount: k.needs === 'amount' ? +$('#cvVal').value : 0,
      target: k.needs === 'target' ? +$('#cvVal').value : null,
    };
    $('#cvApply').disabled = true;
    try {
      latest = await api(`/a/${courseId}/${assignmentId}/curve`, { body });
      $('#cvPreview').innerHTML = curvePreviewHTML(latest);
      $('#cvApply').disabled = !latest.n_changed;
    } catch (err) {
      latest = null;
      $('#cvPreview').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    }
  }

  const debounced = (() => {
    let t;
    return () => { clearTimeout(t); t = setTimeout(preview, 250); };
  })();

  $('#cvKind').onchange = () => { syncForm(); preview(); };
  $('#cvScope').onchange = preview;
  $('#cvVal').oninput = debounced;
  $('#cvCancel').onclick = () => { host.innerHTML = ''; };
  $('#cvApply').onclick = async () => {
    if (!latest || !latest.n_changed) return;
    const k = kindOf();
    const what = latest.criterion_label === 'whole score'
      ? 'the whole score' : `"${latest.criterion_label}"`;
    if (!confirm(`Apply ${k.label.replace(/…$/, '')} to ${what} for `
      + `${latest.n_changed} student(s)?\n\n`
      + `Class average ${latest.before.mean} → ${latest.after.mean}`
      + ` (${latest.before.mean_pct}% → ${latest.after.mean_pct}%).\n\n`
      + 'The earned scores are kept and this can be removed later.')) return;
    try {
      const done = await api(`/a/${courseId}/${assignmentId}/curve`, {
        body: {
          kind: k.id, scope: $('#cvScope').value, only: scope,
          amount: k.needs === 'amount' ? +$('#cvVal').value : 0,
          target: k.needs === 'target' ? +$('#cvVal').value : null,
          label: $('#cvLabel').value.trim(), apply: true,
        },
      });
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      host.innerHTML = '';
      setStatus(`curve applied to ${done.n_changed} student(s)`, 'ok');
      render();
    } catch (err) { setStatus('curve failed: ' + err.message, 'err'); }
  };
  const rm = $('#cvRemove');
  if (rm) rm.onclick = async () => {
    if (!confirm(scope
      ? `Remove any curve from the ${scope.length} selected student(s)?`
      : 'Remove every curve on this assignment? Scores go back to what was earned.')) return;
    try {
      const done = await api(`/a/${courseId}/${assignmentId}/curve`,
        { body: { remove: true, only: scope } });
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      host.innerHTML = '';
      setStatus(`curve removed from ${(done.removed || []).length} student(s)`, 'ok');
      render();
    } catch (err) { setStatus('could not remove: ' + err.message, 'err'); }
  };

  syncForm();
  preview();
}

function curvePreviewHTML(p) {
  const bandRow = (before, after) => {
    const rows = before.distribution.map((b, i) => {
      const a = after.distribution[i];
      const moved = a.count - b.count;
      return `<tr>
        <td class="ltrCell">${b.letter}</td>
        <td>${b.count} <span class="muted">(${(100 * b.share).toFixed(0)}%)</span></td>
        <td class="arrowCell">→</td>
        <td>${a.count} <span class="muted">(${(100 * a.share).toFixed(0)}%)</span></td>
        <td class="${moved > 0 ? 'up' : moved < 0 ? 'down' : 'muted'}">${
          moved ? (moved > 0 ? '+' + moved : moved) : '—'}</td>
      </tr>`;
    }).join('');
    return `<table class="cvBands"><thead><tr><th></th><th>now</th><th></th>
      <th>after</th><th>change</th></tr></thead><tbody>${rows}</tbody></table>`;
  };

  const rows = (p.rows || []).filter(r => r.delta);
  const nudge = [];
  if (p.n_lowered_blocked) {
    nudge.push(`<div class="callout bad"><b>This curve would have lowered
      ${p.n_lowered_blocked} student(s), so it does nothing for them.</b>
      A curve here never takes points away. If you meant to raise the average,
      check that your target is above the current one.</div>`);
  }
  if (p.n_capped) {
    nudge.push(`<div class="callout"><b>${p.n_capped} student(s) hit the maximum</b>
      and gained less than the others, because a curve cannot push a score above
      full marks.</div>`);
  }
  if ((p.ungraded_skipped || []).length) {
    nudge.push(`<div class="callout">${p.ungraded_skipped.length} selected
      student(s) are not graded yet, so there is nothing to curve for them.</div>`);
  }

  return `
    ${nudge.join('')}
    <div class="cvStats">
      <div><span class="cvBig">${p.n_changed}</span><span>of ${p.n_selected}
        students change</span></div>
      <div><span class="cvBig">${p.before.mean} → ${p.after.mean}</span>
        <span>class average (${p.before.mean_pct}% → ${p.after.mean_pct}%)</span></div>
      <div><span class="cvBig">+${num(p.mean_gain)}</span><span>average gain,
        biggest +${num(p.biggest_gain)}</span></div>
    </div>
    <div class="cvSplit">
      <div>
        <h4>Letter grades</h4>
        ${bandRow(p.before, p.after)}
      </div>
      <div>
        <h4>Who changes${rows.length > 12 ? ` (top 12 of ${rows.length})` : ''}</h4>
        <table class="cvRows"><tbody>
        ${rows.slice(0, 12).map(r => `<tr>
          <td>${esc(r.name || r.user_id)}</td>
          <td class="mono">${num(r.before)} → ${num(r.after)}</td>
          <td class="mono up">+${num(r.delta)}</td>
          <td>${r.letter_before !== r.letter_after
            ? `<span class="ltrMove">${r.letter_before} → ${r.letter_after}</span>`
            : `<span class="muted">${r.letter_after}</span>`}</td>
        </tr>`).join('')}
        ${!rows.length ? '<tr><td colspan="4" class="muted">Nobody changes.</td></tr>' : ''}
        </tbody></table>
      </div>
    </div>
    <p class="cvFoot">Scope: <b>${esc(p.criterion_label)}</b>
      (max ${num(p.scope_max)}). Letter bands come from
      <code>grade_scale</code> in config.json.</p>`;
}

/* ------------------------------------------------------------------ push */
function openPush(only) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  const scope = (only && only.length) ? only : null;
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Push grades to Canvas${scope ? ` — ${scope.length} selected` : ''}</h3>
      <div class="sub">This writes into your gradebook. The plan below is worked
        out read-only and changes nothing; nothing reaches Canvas until you press
        <b>Post for real</b> and confirm.${scope
          ? ' Only the students you selected are included.' : ''}</div>
      <label class="tick" style="margin:12px 0">
        <input type="checkbox" id="pushComments">
        also post the written comments to students
        <span style="color:var(--muted)">(off by default: read them first, they are AI drafts)</span>
      </label>
      <div class="log" id="pushLog">Working out what would be written…</div>
      <div class="foot">
        <button class="btn" id="pushCancel">Cancel</button>
        <button class="btn danger" id="pushGo" disabled>Post for real</button>
      </div></div></div>`;
  $('#pushCancel').onclick = () => { host.innerHTML = ''; };
  // Turning comments on or off changes what would be written, so the plan is
  // worked out again rather than left on screen describing the other setting.
  $('#pushComments').onchange = () => { $('#pushGo').disabled = true; plan(); };

  /* The plan, every time this opens. It is the dry run under another name --
     the same read-only call, which is why it can just happen: what the roster
     cannot tell you is which students are about to be left out and why, and a
     score you can see is not proof it will be written (one still flagged for
     review is held back). Asking for that by button meant it could be skipped
     on the way to a live gradebook write. */
  const plan = async () => {
    const log = $('#pushLog');
    if (!log) return;                       // closed while this was in flight
    log.textContent = 'Working out what would be written…';
    try {
      const { job } = await api(`/a/${courseId}/${assignmentId}/push`,
        { body: { dry_run: true, include_comments: $('#pushComments').checked,
                  only: scope } });
      const poll = async () => {
        const info = await api('/jobs/' + job);
        const box = $('#pushLog');
        if (!box) return;                   // closed while this was in flight
        if (info.state === 'running') return setTimeout(poll, 500);
        if (info.state === 'error') { box.textContent = 'Failed: ' + info.error; return; }
        const r = info.result || {};
        const write = r.would_post || [], held = r.skipped || [];
        // Held back first: the roster already shows every score, so the part
        // worth reading here is who is about to be left out -- a score you can
        // see is not proof it will be written. A curved score shows its
        // arithmetic, because writing a number the instructor never saw would
        // be the worst surprise of the lot.
        box.textContent = [
          (r.include_comments ? 'SCORES + COMMENTS' : 'SCORES ONLY (no comments)'),
          `WILL WRITE ${write.length} · HELD BACK ${held.length}`,
          held.length ? '\nHELD BACK - nothing is written for these:\n'
            + held.map(s => `  ${s.name}: ${s.why}`).join('\n') : '',
          write.length ? '\nWILL WRITE:\n'
            + write.map(s => `  ${s.name}: ${s.score}`
              + (s.curved_by ? `   (${s.earned} earned ${s.curved_by > 0 ? '+' : ''}${
                  s.curved_by} curve)` : '')).join('\n') : '',
        ].filter(Boolean).join('\n');
        $('#pushGo').disabled = !write.length;
      };
      poll();
    } catch (err) {
      const box = $('#pushLog');
      if (box) box.textContent = 'Failed: ' + err.message;
    }
  };
  // Worked out as soon as the dialog opens. It changes nothing, so there is
  // nothing to press: the plan is not a step, it is the dialog's content.
  plan();

  $('#pushGo').onclick = () => {
    const withComments = $('#pushComments').checked;

    // The write runs as a job, and the server refuses the first attempt from
    // inside that job -- so the refusal arrives on the finished job, not on the
    // HTTP response. postConfirmed only ever sees the HTTP side, which is why
    // it never asked and every real push died reporting its own confirmation
    // summary as the error. This is the job-level handshake instead, the same
    // one every other confirmed write in the app uses.
    runJobConfirmed(
      scope ? `Pushing ${scope.length} grade(s) to Canvas` : 'Pushing grades to Canvas',
      (token, show) => api(`/a/${courseId}/${assignmentId}/push`,
        { body: { dry_run: false, include_comments: withComments, only: scope,
                  show: !!show, confirm: token } }),
      async r => {
        const n = (r.posted || []).length, bad = (r.failed || []).length;
        setStatus(`pushed ${n} grade(s)`
          + (bad ? `, ${bad} failed` : '')
          + (n ? (r.shown ? ` · ${r.shown} now visible to students`
                          : ' · hidden from students') : ''),
          bad ? 'err' : 'ok');
        // The push recorded what Canvas now holds; show it without waiting for
        // the pull timer.
        try {
          S.ws = await api(`/a/${courseId}/${assignmentId}`);
          renderHeaderActions();
          render();
        } catch (_) { /* the job dialog still holds the result */ }
      },
      { title: scope ? `Push grades for ${scope.length} student(s)?`
                     : 'Push these grades to Canvas?',
        note: (withComments ? 'Comments will be written as well.'
                            : 'Scores only, no comments.')
              + ' Push hidden puts them in the gradebook where only you can see them,'
              + ' and nothing reaches students until you press Make live.'
              + ' Push live writes them and posts them at once, so the class can'
              + ' read them straight away.',
        actions: [{ label: 'Push hidden', value: false, cls: '' },
                  { label: 'Push live', value: true, cls: 'danger' }],
        // Cancelling restores the dialog underneath from markup alone, which
        // leaves it with no handlers at all, so put a live one back.
        onCancel: () => openPush(only) });
  };
}

/* -------------------------------------------------------- letters & curves */
/* Mirrors courseforge/curve.py: cutoffs are the minimum percent per letter,
   and anything under the lowest is an F. The scale comes from config.json via
   /health so the charts match the institution's own bands. */
function gradeScale() {
  return (S.health && S.health.grade_scale) || { A: 90, B: 80, C: 70, D: 60 };
}
function gradeLadder() {
  return Object.entries(gradeScale())
    .map(([name, cut]) => [name, +cut])
    .sort((a, b) => b[1] - a[1]);
}
function letterOf(pct) {
  if (pct == null || isNaN(pct)) return '';
  for (const [name, cut] of gradeLadder()) if (pct >= cut) return name;
  return 'F';
}
function letterBands(percents) {
  const steps = gradeLadder();
  const names = steps.map(([n]) => n).concat('F');
  const counts = Object.fromEntries(names.map(n => [n, 0]));
  percents.forEach(p => { counts[letterOf(p)] += 1; });
  const total = percents.length;
  return names.map((name, i) => ({
    letter: name, count: counts[name],
    share: total ? counts[name] / total : 0,
    low: name === 'F' ? 0 : steps[i][1],
    high: name === 'F' ? (steps.length ? steps[steps.length - 1][1] : 0)
      : (i ? steps[i - 1][1] : null),
  }));
}

/* Mirrors curve.is_scored in Python. A missing submission is not a zero: it has
   no score, it is reported as a non-submission, and it stays out of every
   average and every letter band. Older drafts stored a fabricated 0 for these,
   so the marker is checked as well as the total. */
const UNSCORED_SOURCES = ['auto-skip', 'no-submission'];
function isScored(e) {
  return !!e && e.total != null && !UNSCORED_SOURCES.includes(e.source);
}
function unscoredReason(s, e) {
  if (isScored(e)) return '';
  if (s && s.status === 'unsubmitted') return 'no submission';
  if (e && e.unscored_reason) return e.unscored_reason;
  if (e) return 'nothing readable';
  return 'not graded yet';
}

/* The score that counts: earned plus any curve. The server keeps final_total in
   step whenever a curve is applied or the earned score changes. */
function finalOf(e) {
  if (!isScored(e)) return null;
  return e.final_total != null ? e.final_total : e.total;
}
function earnedOf(e) { return isScored(e) ? e.total : null; }
function curveDelta(e) {
  const f = finalOf(e), b = earnedOf(e);
  return (f == null || b == null) ? 0 : Math.round((f - b) * 100) / 100;
}
/* A criterion's score including any curve aimed at that criterion, so the
   per-criterion charts show the effect of a curve instead of the old numbers. */
function critScore(e, cid) {
  const raw = +((e.scores || {})[cid] || 0);
  const add = +(((e.curve || {}).by_criterion || {})[cid] || 0);
  const crit = rubric().find(c => c.id === cid);
  const top = crit ? +crit.points : Infinity;
  return Math.max(0, Math.min(raw + add, top));
}

/* How late, not just whether. A paper six hours past the deadline and one 27
   days past are different situations to grade, and both were showing the same
   word. Canvas's own `late` flag decides IF it counts as late: seconds_late is
   filled in for a student who never submitted at all, so reading it alone would
   label every missing submission "7.7 days late". The flag gates it; the number
   only supplies the size. */
function lateText(s) {
  if (!s || !s.late) return '';
  const secs = +(s.seconds_late || 0);
  if (secs <= 0) return 'late';                 // flagged, but no figure given
  const days = secs / 86400;
  if (days >= 10) return `${Math.round(days)} days late`;
  const shown = Math.round(days * 10) / 10;
  // Below a tenth of a day the decimal reads 0.0, which says nothing.
  if (shown >= 0.1) return `${shown} day${shown === 1 ? '' : 's'} late`;
  return `${Math.max(1, Math.round(secs / 60))} min late`;
}

/* ---------------------------------------------------------------- roster */
function rubric() { return (S.ws.draft && S.ws.draft.rubric) || []; }
function entryOf(uid) { return ((S.ws.draft && S.ws.draft.students) || {})[String(uid)] || null; }
function infoOf(uid) { return (S.ws.extracted || {})[String(uid)] || {}; }

/* Surname, the way a roster is usually read. */
function sortKey(s) { return String(s.name || '').trim().split(/\s+/).pop(); }

function students() {
  const all = Object.values(S.ws.extracted || {});
  // Whoever handed something in comes first, then everyone who did not, each
  // half alphabetical on its own. Grading runs top to bottom, and a roster that
  // interleaves empty rows with real work makes you skip past them all the way
  // down. The people with nothing to read are still listed, just after the
  // people who gave you something.
  all.sort((a, b) => (
    ((a.status === 'unsubmitted') - (b.status === 'unsubmitted'))
    || sortKey(a).localeCompare(sortKey(b))));
  return all.filter(s => {
    if (S.query && !String(s.name).toLowerCase().includes(S.query)) return false;
    const e = entryOf(s.user_id);
    if (S.filter === 'review') return !!(e && e.needs_human);
    if (S.filter === 'human') return !!(e && (e.source === 'human' || e.source === 'canvas'));
    if (S.filter === 'ungraded') return !isScored(e);
    // The working set most of the time: whoever handed something in. Everyone
    // else is a roster entry with nothing to read, and scrolling past them is
    // the whole reason this chip exists.
    if (S.filter === 'submitted') return s.status !== 'unsubmitted';
    if (S.filter === 'missing') return s.status === 'unsubmitted';
    if (S.filter === 'conflict') return !!(e && e.conflict);
    return true;
  });
}

/* ------------------------------------------------------------- selection */
function pickedIds() { return [...S.picked]; }
function pickedEntries() { return pickedIds().map(uid => entryOf(uid)).filter(Boolean); }

/* Shift-click takes a range, ctrl/cmd-click toggles one, a plain click goes back
   to single-student review. The range runs over the rows as currently filtered,
   which is what you see and therefore what you mean. */
function onRowClick(ev, index, uid) {
  const list = students();
  if (ev.shiftKey && list.length) {
    const from = Math.min(S.anchor, index), to = Math.max(S.anchor, index);
    for (let i = from; i <= to; i++) S.picked.add(String(list[i].user_id));
    S.sel = index;
  } else if (ev.ctrlKey || ev.metaKey) {
    const key = String(uid);
    if (S.picked.has(key)) S.picked.delete(key); else S.picked.add(key);
    S.sel = index; S.anchor = index;
  } else {
    S.picked.clear();
    S.sel = index; S.anchor = index;
  }
  render();
}

function selectAllShown() {
  students().forEach(s => S.picked.add(String(s.user_id)));
  render();
}
function clearPicked() {
  if (!S.picked.size) return;
  S.picked.clear();
  if (S.insightsScope === 'selection') S.insightsScope = 'class';
  render();
}

function renderBulkBar() {
  const bar = $('#bulkBar');
  if (!bar) return;
  const n = S.picked.size;
  if (!n) {
    bar.className = 'bulkBar';
    bar.innerHTML = `<span class="bulkHint">Shift-click for a range, ctrl-click to add one.</span>`;
    return;
  }
  const canAI = !(S.health.claude && S.health.claude.logged_in === false);
  const entries = pickedEntries();
  // Everything already approved? Then the useful action is the reverse.
  const allOk = entries.length === n && entries.every(e => e && e.human_ok);
  const flagged = entries.filter(e => e && e.needs_human && !e.human_ok).length;
  const clashes = entries.filter(e => e && e.conflict).length;
  bar.className = 'bulkBar show';
  bar.innerHTML = `
    <div class="bulkHead">
      <b>${n} selected</b>
      ${flagged ? `<span class="bulkNote">${flagged} flagged for review</span>` : ''}
      ${clashes ? `<span class="bulkNote">${clashes} in conflict with Canvas</span>` : ''}
      <button class="btn sm" id="bulkAll">Select all shown</button>
      <button class="btn sm" id="bulkNone">Clear</button>
    </div>
    <div class="bulkActs">
      ${clashes ? `<button class="btn sm" id="bulkTakeCanvas"
        title="Replace the local score with what Canvas holds, for the selected students in conflict">Take Canvas</button>
      <button class="btn sm" id="bulkKeepMine"
        title="Keep the local score for these; the next push overwrites Canvas">Keep mine</button>` : ''}
      <button class="btn sm ai" id="bulkGrade" ${canAI ? '' : 'disabled'}
        title="Re-grade just these students">Re-grade</button>
      <button class="btn sm" id="bulkReview"
        title="${allOk ? 'Put the review flag back on these'
                       : 'Clears the review block so these can be pushed to Canvas'}">
        ${allOk ? 'Un-mark reviewed' : 'Mark reviewed'}</button>
      <button class="btn sm" id="bulkInsights"
        title="Charts and a written read for just these students">Insights</button>
      <button class="btn sm" id="bulkOverlap" ${(canAI && n >= 2) ? '' : 'disabled'}
        title="${n >= 2 ? 'Compare what these students wrote for shared wording'
                        : 'Pick at least two students'}">Overlap check</button>
      <button class="btn sm" id="bulkCurve"
        title="Curve just these students">Curve…</button>
      <button class="btn sm danger" id="bulkPush"
        title="Push only these students to Canvas">Push…</button>
      <button class="btn sm" id="bulkRelease"
        title="Show these students the grades already in Canvas for them">Make live…</button>
    </div>`;
  $('#bulkAll').onclick = selectAllShown;
  $('#bulkNone').onclick = clearPicked;
  $('#bulkRelease').onclick = () => openRelease(pickedIds());
  const tc = $('#bulkTakeCanvas'); if (tc) tc.onclick = () => resolveConflicts(pickedIds(), 'canvas');
  const km = $('#bulkKeepMine'); if (km) km.onclick = () => resolveConflicts(pickedIds(), 'mine');
  $('#bulkGrade').onclick = () => doGrade(pickedIds());
  $('#bulkReview').onclick = () => doMarkReviewed(!allOk);
  $('#bulkInsights').onclick = () => {
    S.insightsScope = 'selection'; S.view = 'insights';
    renderHeaderActions(); render();
  };
  $('#bulkOverlap').onclick = doOverlap;
  $('#bulkCurve').onclick = () => openCurve(pickedIds());
  $('#bulkPush').onclick = () => openPush(pickedIds());
}

async function doMarkReviewed(reviewed) {
  const { courseId, assignmentId } = S.ids;
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/review`,
      { body: { only: pickedIds(), reviewed } });
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const n = (r.marked || []).length;
    let msg = reviewed
      ? `${n} marked reviewed — they can be pushed now`
      : `${n} put back to needs-review`;
    if ((r.no_score || []).length) {
      msg += `; ${r.no_score.length} still has no score to push`;
    }
    if ((r.not_graded || []).length) {
      msg += `; ${r.not_graded.length} not graded yet, nothing to mark`;
    }
    setStatus(msg, 'ok');
    render();
  } catch (err) { setStatus('could not mark: ' + err.message, 'err'); }
}

function doOverlap() {
  const { courseId, assignmentId } = S.ids;
  const only = pickedIds();
  runJob(`Overlap check on ${only.length} submissions`,
    () => api(`/a/${courseId}/${assignmentId}/overlap`, { body: { only } }),
    report => { $('#modalHost').innerHTML = ''; openOverlapReport(report); });
}

function renderRoster() {
  const list = students(), host = $('#roster'), keep = host.scrollTop;
  host.innerHTML = '';
  list.forEach((s, i) => {
    const e = entryOf(s.user_id);
    const shown = finalOf(e);
    const total = shown != null ? num(shown) : '—';
    const bump = curveDelta(e);
    const noScore = !!e && !isScored(e);
    const dot = e && e.source === 'human' ? '<span class="dot human"></span>'
      : e && e.source === 'canvas' ? '<span class="dot canvas"></span>'
        : e && e.needs_human ? '<span class="dot flag"></span>'
          : s.status === 'unsubmitted' ? '<span class="dot none"></span>' : '';
    const sub = s.status === 'unsubmitted' ? 'no submission'
      : noScore ? esc(unscoredReason(s, e))
        : (e && e.conflict) ? `Canvas has ${num(e.conflict.canvas_score)}`
          : (e && e.needs_human ? 'needs review' : (lateText(s) || `${s.words || 0} words`));
    const picked = S.picked.has(String(s.user_id));
    const b = document.createElement('button');
    b.className = 'rrow' + (picked ? ' picked' : '');
    b.setAttribute('aria-current', String(i === S.sel));
    b.setAttribute('aria-pressed', String(picked));
    const ok = e && e.human_ok ? '<span class="okTick" title="marked reviewed">✓</span>' : '';
    const clash = e && e.conflict
      ? `<span class="clashMark" title="Canvas holds ${num(e.conflict.canvas_score)}; you have ${total}. Nothing was overwritten.">!</span>` : '';
    const curveMark = bump
      ? `<span class="curveMark" title="includes a curve of +${num(bump)}">↑</span>` : '';
    // Video never gets an automatic score, so the roster has to say which rows
    // are waiting on someone to sit and watch them.
    const vidMark = (s.videos || []).length
      ? `<span class="vidMark" title="${esc((s.videos || []).join(', '))} — plays in the work pane">▶</span>` : '';
    // Is this grade in Canvas, and can the student see it? Quiet glyph: the
    // detail pane has the words.
    const postMark = s.canvas_score == null ? ''
      : s.canvas_posted_at
        ? '<span class="postMark live" title="in Canvas, visible to the student">●</span>'
        : '<span class="postMark hidden" title="in Canvas, hidden from the student">◌</span>';
    b.innerHTML = `<span><span class="nm">${dot}${esc(s.name)}${ok}${clash}${vidMark}</span><span class="sub">${esc(sub)}</span></span>
                   <span class="sc">${postMark}${curveMark}${total}</span>`;
    b.onclick = ev => onRowClick(ev, i, s.user_id);
    host.appendChild(b);
  });
  if (!list.length) host.innerHTML = '<div style="padding:18px;color:var(--muted);font-size:12.5px">No students match.</div>';
  host.scrollTop = keep;
  S.anchor = Math.max(0, Math.min(S.anchor, list.length - 1));
  renderBulkBar();
}

/* --------------------------------------------------------------- insights */
const CHART = {
  pad: 34,
  ring: [0.25, 0.5, 0.75, 1],
};

/* Students with no score, split by why, so the dashboard can report them as
   their own category instead of burying them in the F band. */
function unscoredRows() {
  const scoped = S.insightsScope === 'selection' && S.picked.size;
  const out = { no_submission: [], nothing_readable: [], not_graded_yet: [] };
  Object.values(S.ws.extracted || {}).forEach(s => {
    if (scoped && !S.picked.has(String(s.user_id))) return;
    const e = entryOf(s.user_id);
    if (isScored(e)) return;
    const why = unscoredReason(s, e);
    const bucket = why === 'no submission' ? 'no_submission'
      : why === 'not graded yet' ? 'not_graded_yet' : 'nothing_readable';
    out[bucket].push(s.name);
  });
  return out;
}

function statsByCriterion(includeMissing) {
  const crits = rubric();
  const scoped = S.insightsScope === 'selection' && S.picked.size;
  const rows = Object.values(S.ws.extracted || {}).map(s => ({ s, e: entryOf(s.user_id) }));
  const pool = rows.filter(({ s, e }) => {
    if (scoped && !S.picked.has(String(s.user_id))) return false;
    // A non-submission only enters the arithmetic if the instructor explicitly
    // asks for the "what if I zero these" view, and then as a synthetic 0.
    if (!isScored(e)) return includeMissing && s.status === 'unsubmitted';
    return true;
  });
  const per = crits.map(c => {
    const vals = pool.map(({ e }) => isScored(e) ? critScore(e, c.id) : 0);
    const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    const sorted = vals.slice().sort((a, b) => a - b);
    const median = sorted.length ? (sorted.length % 2 ? sorted[(sorted.length - 1) / 2]
      : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2) : 0;
    return {
      ...c, n: vals.length, mean, median,
      min: sorted[0] ?? 0, max: sorted[sorted.length - 1] ?? 0,
      pct: c.points ? mean / c.points : 0,
    };
  });
  return { crits, per, pool };
}

function svgWrap(w, h, inner, title) {
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" xmlns="http://www.w3.org/2000/svg"
    font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" role="img"
    aria-label="${esc(title)}"><title>${esc(title)}</title>${inner}</svg>`;
}

/* radar: one axis per rubric criterion, class mean plus optional student */
function radarSVG(per, student) {
  const size = 420, cx = size / 2, cy = size / 2 + 6, R = size / 2 - 74;
  const n = per.length;
  if (n < 3) return '<p class="chartNote">A radar needs at least three criteria — see the bars below.</p>';
  const ang = i => (-Math.PI / 2) + (i * 2 * Math.PI / n);
  const pt = (i, r) => [cx + r * Math.cos(ang(i)), cy + r * Math.sin(ang(i))];
  const poly = vals => vals.map((v, i) => pt(i, Math.max(0, Math.min(1, v)) * R).map(x => x.toFixed(1)).join(',')).join(' ');

  let g = '';
  CHART.ring.forEach(r => {
    g += `<polygon points="${poly(per.map(() => r))}" fill="none"
      stroke="var(--line)" stroke-width="1"/>`;
  });
  per.forEach((c, i) => {
    const [x, y] = pt(i, R);
    g += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--line)"/>`;
    const [lx, ly] = pt(i, R + 22);
    const anchor = Math.abs(lx - cx) < 6 ? 'middle' : (lx > cx ? 'start' : 'end');
    g += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}"
      dominant-baseline="middle" font-size="12" font-weight="600" fill="var(--muted)">C${i + 1}
      <title>${esc(c.label)}</title></text>`;
  });
  CHART.ring.forEach(r => {
    g += `<text x="${cx + 4}" y="${(cy - r * R).toFixed(1)}" font-size="9"
      fill="var(--muted)" opacity="0.75">${Math.round(r * 100)}%</text>`;
  });

  g += `<polygon points="${poly(per.map(c => c.pct))}" fill="var(--accent)" fill-opacity="0.22"
    stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>`;
  per.forEach((c, i) => {
    const [x, y] = pt(i, c.pct * R);
    g += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="var(--accent)"/>`;
  });

  if (student) {
    const vals = per.map(c => c.points ? (+(student.scores || {})[c.id] || 0) / c.points : 0);
    g += `<polygon points="${poly(vals)}" fill="var(--ai)" fill-opacity="0.16"
      stroke="var(--ai)" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round"/>`;
    vals.forEach((v, i) => {
      const [x, y] = pt(i, v * R);
      g += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="var(--ai)"/>`;
    });
  }
  return svgWrap(size, size + 10, g, 'Rubric criteria radar');
}

/* horizontal bars: mean % per criterion, weakest first */
function barsSVG(per) {
  const rowH = 44, w = 560, left = 46, right = 58;
  const h = per.length * rowH + 12;
  const sorted = per.map((c, i) => ({ ...c, idx: i })).sort((a, b) => a.pct - b.pct);
  const barW = w - left - right;
  let g = '';
  sorted.forEach((c, row) => {
    const y = row * rowH + 12;
    const fill = c.pct < 0.6 ? 'var(--danger)' : c.pct < 0.78 ? 'var(--warn)' : 'var(--accent)';
    g += `<text x="0" y="${y + 11}" font-size="12" font-weight="700" fill="var(--muted)">C${c.idx + 1}</text>`;
    g += `<rect x="${left}" y="${y}" width="${barW}" height="15" rx="3" fill="var(--line)" opacity="0.55"/>`;
    g += `<rect x="${left}" y="${y}" width="${(barW * c.pct).toFixed(1)}" height="15" rx="3" fill="${fill}"><title>${esc(c.label)}</title></rect>`;
    g += `<text x="${w - right + 8}" y="${y + 12}" font-size="12" font-weight="600"
      fill="var(--ink)">${Math.round(c.pct * 100)}%</text>`;
    g += `<text x="${left}" y="${y + 29}" font-size="10.5" fill="var(--muted)">${esc(
      c.label.length > 62 ? c.label.slice(0, 60) + '…' : c.label)} · mean ${c.mean.toFixed(1)} of ${num(c.points)}</text>`;
  });
  return svgWrap(w, h + 14, g, 'Mean achievement per criterion');
}

/* heatmap: students down, criteria across */
function heatSVG(per, pool) {
  if (!pool.length) return '';
  const cw = 62, ch = 20, left = 132, top = 40;
  const w = left + per.length * cw + 46;
  const h = top + pool.length * ch + 10;
  const rows = pool.slice().sort((a, b) => (finalOf(b.e) || 0) - (finalOf(a.e) || 0));
  let g = '';
  per.forEach((c, i) => {
    g += `<text x="${left + i * cw + cw / 2}" y="${top - 12}" text-anchor="middle"
      font-size="11" font-weight="700" fill="var(--muted)">C${i + 1}<title>${esc(c.label)}</title></text>`;
  });
  g += `<text x="${left + per.length * cw + 8}" y="${top - 12}" font-size="11"
    font-weight="700" fill="var(--muted)">Tot</text>`;
  rows.forEach((r, row) => {
    const y = top + row * ch;
    const name = r.s.name.length > 20 ? r.s.name.slice(0, 19) + '…' : r.s.name;
    g += `<text x="0" y="${y + 14}" font-size="11" fill="var(--ink)">${esc(name)}</text>`;
    per.forEach((c, i) => {
      const v = +(r.e.scores || {})[c.id] || 0;
      const p = c.points ? v / c.points : 0;
      g += `<rect x="${left + i * cw}" y="${y + 2}" width="${cw - 3}" height="${ch - 4}" rx="2"
        fill="var(--accent)" fill-opacity="${(0.10 + 0.85 * p).toFixed(3)}"/>`;
      g += `<text x="${left + i * cw + (cw - 3) / 2}" y="${y + 15}" text-anchor="middle"
        font-size="10" font-weight="600" fill="${p > 0.55 ? '#07130d' : 'var(--muted)'}">${num(v)}</text>`;
    });
    g += `<text x="${left + per.length * cw + 8}" y="${y + 15}" font-size="11"
      font-weight="700" fill="var(--ink)">${num(finalOf(r.e))}</text>`;
  });
  return svgWrap(w, h, g, 'Per-student criterion heatmap');
}

/* histogram of totals */
/* Letter bands, lowest first, each labelled with its count and its share of the
   graded pool. The count alone makes a class of 12 and a class of 40 look the
   same, so both numbers are on the chart rather than only in a tooltip. */
function histSVG(pool, possible) {
  const percents = pool.map(({ e }) =>
    possible ? 100 * (isScored(e) ? finalOf(e) : 0) / possible : 0);
  const bands = letterBands(percents).slice().reverse();      // F .. A
  const peak = Math.max(1, ...bands.map(b => b.count));
  const w = 460, h = 210, bw = 66, gap = 18, base = h - 50;
  const colourFor = name => name === 'F' ? 'var(--danger)'
    : name === 'D' ? 'var(--warn)' : 'var(--accent)';
  let g = '';
  bands.forEach((b, i) => {
    const x = 26 + i * (bw + gap);
    // Leave room above the tallest bar for its two stacked labels, or the
    // count on the biggest band gets clipped by the top of the viewBox.
    const bh = Math.round((b.count / peak) * (base - 46));
    g += `<rect x="${x}" y="${base - bh}" width="${bw}" height="${Math.max(bh, 2)}" rx="4"
      fill="${colourFor(b.letter)}"><title>${b.letter}: ${b.count} of ${percents.length}
      (${(100 * b.share).toFixed(1)}%)</title></rect>`;
    g += `<text x="${x + bw / 2}" y="${base - bh - 21}" text-anchor="middle" font-size="13"
      font-weight="700" fill="var(--ink)">${b.count}</text>`;
    g += `<text x="${x + bw / 2}" y="${base - bh - 7}" text-anchor="middle" font-size="10.5"
      font-weight="600" fill="var(--muted)">${(100 * b.share).toFixed(0)}%</text>`;
    g += `<text x="${x + bw / 2}" y="${base + 18}" text-anchor="middle" font-size="14"
      font-weight="700" fill="var(--ink)">${b.letter}</text>`;
    g += `<text x="${x + bw / 2}" y="${base + 33}" text-anchor="middle" font-size="10"
      fill="var(--muted)">${b.letter === 'F' ? 'under ' + num(b.high)
        : num(b.low) + (b.high == null ? '+' : '–' + (b.high - 0.1).toFixed(1))}</text>`;
  });
  return svgWrap(w, h, g, 'Letter grade distribution');
}

/* The same numbers as text, because a chart is not a table and the exact counts
   are what get read out in a meeting. */
function letterLegend(pool, possible) {
  const percents = pool.map(({ e }) =>
    possible ? 100 * (isScored(e) ? finalOf(e) : 0) / possible : 0);
  const bands = letterBands(percents);
  const n = percents.length;
  const un = unscoredRows();
  // Non-submissions get their own line, outside the letters. They are not an F,
  // and they are not in the percentages above.
  const extra = [
    ['No submission', un.no_submission],
    ['Nothing readable', un.nothing_readable],
    ['Not graded yet', un.not_graded_yet],
  ].filter(([, list]) => list.length);
  return `<div class="legend letterLegend">${bands.map(b =>
    `<div><b>${b.letter}</b> <span>${b.count} of ${n} ·
      ${(100 * b.share).toFixed(1)}% · ${b.letter === 'F'
        ? 'under ' + num(b.high) + '%'
        : num(b.low) + '%' + (b.high == null ? ' and up' : '–' + (b.high - 0.1).toFixed(1) + '%')}
    </span></div>`).join('')}</div>
    ${extra.length ? `<div class="unscoredNote">
      ${extra.map(([label, list]) => `<div><b>${label}</b>
        <span>${list.length} student${list.length > 1 ? 's' : ''} ·
        not counted in the letters or the average${
          list.length <= 6 ? ' · ' + list.map(esc).join(', ') : ''}</span></div>`).join('')}
      <p>These have no score. ${S.includeMissing
        ? 'The charts above are currently counting non-submissions as zero, because you asked for that view.'
        : 'Nothing above includes them.'}</p>
    </div>` : ''}`;
}

function renderInsights() {
  const host = $('#detail');
  const draft = S.ws.draft || {};
  const includeMissing = !!S.includeMissing;
  const { crits, per, pool } = statsByCriterion(includeMissing);
  const possible = draft.points_possible || 0;

  if (!pool.length) {
    host.innerHTML = `<div class="who"><h2>Insights</h2></div>
      <p style="color:var(--muted);max-width:60ch">Nothing is graded yet. Run
      <b>Auto-grade all</b>, or score a few students by hand, and the charts will fill in.</p>`;
    $('#workPane').innerHTML = '';
    return;
  }

  const list = students();
  const selected = list[Math.min(S.sel, list.length - 1)];
  const selEntry = selected ? entryOf(selected.user_id) : null;
  const useStudent = selEntry && selEntry.total != null ? selEntry : null;

  const totals = pool.map(({ e }) => isScored(e) ? finalOf(e) : 0).sort((a, b) => a - b);
  const mean = totals.reduce((a, b) => a + b, 0) / totals.length;
  const curvedCount = pool.filter(({ e }) => curveDelta(e)).length;
  const weakest = per.slice().sort((a, b) => a.pct - b.pct)[0];
  const summary = draft.class_summary;

  host.innerHTML = `
    <div class="who"><h2>Insights</h2>
      <span class="id">${esc(draft.assignment_name || '')} · ${pool.length} graded of ${
        (S.insightsScope === 'selection' && S.picked.size) ? S.picked.size + ' selected'
          : Object.keys(S.ws.extracted).length}</span></div>
    ${(S.insightsScope === 'selection' && S.picked.size) ? `<div class="scopeBar">
      Showing <b>${S.picked.size} selected students</b> only.
      <button class="btn sm" id="scopeAll">Show the whole class</button></div>` : ''}

    <div class="insightBar">
      <label class="tick" title="Off by default: a non-submission has no score and
        is reported separately rather than averaged in as a zero.">
        <input type="checkbox" id="chkMissing" ${includeMissing ? 'checked' : ''}>
        also count non-submissions as zero</label>
      <span class="spacer"></span>
      <button class="btn sm ai" id="btnCurve">Curve grades…</button>
      <button class="btn sm" id="btnPrint">Print / Save as PDF</button>
      <button class="btn sm" id="dlAll">Download charts (SVG)</button>
    </div>

    <div class="takeaway"><b>Weakest criterion:</b> ${esc(weakest.label)}.
      Class mean ${weakest.mean.toFixed(1)} of ${num(weakest.points)}
      (${Math.round(weakest.pct * 100)}%). Class average
      ${mean.toFixed(1)} / ${possible}.</div>

    <figure class="chart summaryCard" id="fig-summary">
      <figcaption>How the class did
        <span>${summary ? `written by ${esc(summary.model || 'claude')} from ${summary.n} graded submissions` : 'a written read of the whole class'}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn sm ai" id="btnSummary">${summary ? 'Rewrite' : 'Write class summary'}</button>
      </figcaption>
      ${summary
      ? `<div class="summaryText">${esc(summary.text)}</div>
           <div class="summaryMeta">generated ${esc(fmtDate(summary.generated_at))}${summary.include_missing ? ' · counting unsubmitted as zero' : ''}</div>`
      : `<p class="chartNote">Claude reads the per-criterion results and the notes it wrote
           while grading, then tells you where the class struggled and what to reteach.
           Needs at least three graded students.</p>`}
    </figure>

    <div class="chartGrid">
      <figure class="chart" id="fig-radar">
        <figcaption>Criterion achievement
          <span>class mean${useStudent ? `, dashed = ${esc(selected.name)}` : ''}</span></figcaption>
        ${radarSVG(per, useStudent)}
      </figure>
      <figure class="chart" id="fig-hist">
        <figcaption>Letter grades<span>count and share of the ${pool.length}
          graded</span></figcaption>
        ${histSVG(pool, possible)}
        ${letterLegend(pool, possible)}
      </figure>
    </div>

    <figure class="chart" id="fig-bars">
      <figcaption>Where the points went<span>weakest criterion first</span></figcaption>
      ${barsSVG(per)}
    </figure>

    <div class="legend">${per.map((c, i) =>
    `<div><b>C${i + 1}</b> ${esc(c.label)} <span>· mean ${c.mean.toFixed(1)} / ${num(c.points)}
      · median ${num(c.median)} · range ${num(c.min)}–${num(c.max)}</span></div>`).join('')}</div>

    <figure class="chart scrollX" id="fig-heat">
      <figcaption>Per-student breakdown<span>darker is closer to full marks</span></figcaption>
      ${heatSVG(per, pool)}
    </figure>`;

  host.scrollTop = 0;
  $('#workPane').innerHTML = '';
  const scopeBtn = $('#scopeAll');
  if (scopeBtn) scopeBtn.onclick = () => { S.insightsScope = 'class'; render(); };
  $('#chkMissing').onchange = ev => { S.includeMissing = ev.target.checked; renderInsights(); };
  $('#dlAll').onclick = downloadCharts;
  $('#btnCurve').onclick = () => openCurve(
    S.insightsScope === 'selection' && S.picked.size ? pickedIds() : null);
  $('#btnPrint').onclick = () => window.print();
  const scopeSel = S.insightsScope === 'selection' && S.picked.size;
  $('#btnSummary').onclick = () => {
    const { courseId, assignmentId } = S.ids;
    runJob(scopeSel ? `Reading ${S.picked.size} selected students` : 'Reading the whole class',
      () => api(`/a/${courseId}/${assignmentId}/summary`,
        { body: { include_missing: !!S.includeMissing,
                  only: scopeSel ? pickedIds() : null } }),
      async () => {
        S.ws = await api(`/a/${courseId}/${assignmentId}`);
        $('#modalHost').innerHTML = '';
        renderInsights();
      });
  };
}

function downloadCharts() {
  const stamp = (S.ws.draft.assignment_name || 'assignment')
    .replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50);
  let n = 0;
  ['fig-radar', 'fig-bars', 'fig-heat', 'fig-hist'].forEach(id => {
    const svg = document.querySelector('#' + id + ' svg');
    if (!svg) return;
    // Inline the computed theme colours so the file stands alone outside this page.
    const clone = svg.cloneNode(true);
    const css = getComputedStyle(document.body);
    ['--accent', '--ai', '--line', '--muted', '--ink', '--warn', '--danger'].forEach(v => {
      const val = css.getPropertyValue(v).trim();
      clone.innerHTML = clone.innerHTML.split(`var(${v})`).join(val);
    });
    clone.setAttribute('style', 'background:' + css.getPropertyValue('--panel').trim());
    const blob = new Blob([clone.outerHTML], { type: 'image/svg+xml' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${stamp}-${id.replace('fig-', '')}.svg`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    n++;
  });
  setStatus(`downloaded ${n} chart${n === 1 ? '' : 's'}`, 'ok');
}

/* ---------------------------------------------------------------- detail */
function render() {
  renderRoster();
  if (S.view === 'insights') renderInsights();
  else renderDetail();
  renderTeachBar();
}

function renderDetail() {
  const list = students(), host = $('#detail');
  const draft = S.ws.draft || {};
  if (!Object.keys(S.ws.extracted || {}).length) {
    host.innerHTML = `<div class="wrap" style="padding:0">
      <h2 style="margin-top:0">${esc(S.assignment.name || '')}</h2>
      <p style="color:var(--muted);max-width:60ch">Nothing has been pulled from Canvas for this assignment yet.
      Hit <b>Sync from Canvas</b> to download submissions and extract their text, then <b>Auto-grade all</b>.</p></div>`;
    $('#workPane').innerHTML = '';
    return;
  }
  if (!list.length) { host.innerHTML = ''; return; }
  S.sel = Math.min(S.sel, list.length - 1);
  const s = list[S.sel];
  const e = entryOf(s.user_id) || {};
  const crits = rubric();
  const possible = draft.points_possible || 0;
  const scores = e.scores || {};
  const total = finalOf(e);
  const earned = earnedOf(e);
  const bump = curveDelta(e);

  let tags = '';
  if (e.total !== undefined && !isScored(e)) {
    tags += `<span class="tag">no score · ${esc(unscoredReason(s, e))}</span>`;
  }
  if (bump) tags += `<span class="tag ai">curved +${num(bump)}</span>`;
  if (e.human_ok) tags += '<span class="tag ok">reviewed by you</span>';
  if (e.source === 'human') tags += '<span class="tag edit">edited by you</span>';
  else if (e.source === 'canvas') tags += '<span class="tag edit">pulled from Canvas</span>';
  else if (e.source === 'claude') tags += `<span class="tag ai">graded by ${esc(e.model || 'claude')}</span>`;
  if (s.status === 'unsubmitted') tags += '<span class="tag danger">no submission</span>';
  if (s.late) tags += `<span class="tag warn">${esc(lateText(s))}</span>`;
  tags += canvasTag(s, e);
  (e.flags || []).forEach(f => { tags += `<span class="tag warn">${esc(f)}</span>`; });
  if (e.confidence && e.confidence !== 'high') tags += `<span class="tag">confidence: ${esc(e.confidence)}</span>`;

  let html = `<div class="who"><h2>${esc(s.name)}</h2>
      <span class="id">user ${esc(s.user_id)}${S.ws.pseudonymize ? ' · sent as ' + esc(s.pseudonym) : ''}</span></div>
    <div class="tags">${tags || '<span class="tag">not graded yet</span>'}</div>`;

  if (e.total !== undefined && !isScored(e)) {
    html += noScoreNote(s, e);
  } else if (e.needs_human) {
    html += `<div class="callout bad" style="margin-bottom:14px"><b>Claude flagged this for you.</b>
      ${esc(e.needs_human_reason || 'It could not grade this fairly.')}</div>`;
  }

  if (e.conflict) {
    const c = e.conflict;
    html += `<div class="callout bad conflictBox" style="margin-bottom:14px">
      <b>Canvas and this machine disagree.</b> Canvas holds <b>${num(c.canvas_score)}</b>${c.canvas_graded_at
        ? ' (graded ' + esc(fmtDate(c.canvas_graded_at)) + ')' : ''}${c.canvas_posted_at
        ? ', visible to the student' : ', hidden from the student'}; you have <b>${num(total)}</b>
      here and it has not been pushed. Nothing was overwritten. Pick one:
      <div class="actionRow" style="margin:10px 0 0">
        <button class="btn sm" id="cfCanvas">Take Canvas (${num(c.canvas_score)})</button>
        <button class="btn sm" id="cfMine">Keep mine (${num(total)})</button>
      </div></div>`;
  }
  if (e.total_only && isScored(e)) {
    html += `<div class="callout" style="margin-bottom:14px">Canvas holds a total for this student
      with no rubric breakdown, so the sliders below start at zero. Moving one replaces the
      Canvas total with your rubric score.</div>`;
  }

  html += `<div class="actionRow">
      <button class="btn on" id="btnWorkInline">${S.showWork ? 'Hide' : 'View'} submitted work</button>
      <button class="btn ai" id="btnGradeOne">Re-grade this student</button>
      <button class="btn" id="btnAsk">Ask about this work…</button>
      ${e.needs_human || e.human_ok ? `<button class="btn" id="btnReviewOne"
        title="${e.human_ok ? 'Put the review flag back'
                            : 'Clears the review block so this can be pushed'}">${
        e.human_ok ? 'Un-mark reviewed' : 'Mark reviewed'}</button>` : ''}
      ${s.status === 'unsubmitted' ? `<button class="btn" id="btnRemindOne"
        title="Send this student a Canvas message about the missing work">Remind this student</button>` : ''}
      <a class="btn" target="_blank" rel="noopener" href="${sgUrl(s.user_id)}">SpeedGrader ↗</a>
    </div>
    <div class="totalCard">
      <span class="bigScore">${total == null ? '—' : num(total)}<span class="of"> / ${possible}</span></span>
      <span class="pct">${(total != null && possible)
        ? (100 * total / possible).toFixed(1) + '%'
          + ` <b class="ltr">${letterOf(100 * total / possible)}</b>` : ''}</span>
      <span class="origNote">${bump
        ? `earned ${num(earned)}, curved +${num(bump)}`
        : aiDeltaNote(e)}</span>
    </div>
    ${bump ? `<div class="curveNote">
      This score includes a curve you applied${curveSteps(e)}.
      The earned score, ${num(earned)}, is kept underneath and is what comes back
      if you remove the curve.</div>` : ''}`;

  crits.forEach(c => {
    const v = +(scores[c.id] || 0);
    const tiers = (c.ratings || []).map(r => r.points).filter(p => p != null).sort((a, b) => a - b);
    const useTiers = S.snap && tiers.length > 1;
    let input, ticks;
    if (useTiers) {
      let idx = tiers.indexOf(v);
      if (idx < 0) idx = tiers.reduce((best, t, i) => Math.abs(t - v) < Math.abs(tiers[best] - v) ? i : best, 0);
      input = `<input type="range" min="0" max="${tiers.length - 1}" step="1" value="${idx}"
                 data-cid="${esc(c.id)}" data-tiers="${tiers.join(',')}">`;
      ticks = `<div class="ticks">${tiers.map(t => '<span>' + num(t) + '</span>').join('')}</div>`;
    } else {
      input = `<input type="range" min="0" max="${c.points}" step="1" value="${v}" data-cid="${esc(c.id)}">`;
      ticks = `<div class="ticks"><span>0</span><span>${num(c.points)}</span></div>`;
    }
    const why = (e.rationales || {})[c.id];
    html += `<div class="crit">
        <div class="critHead"><span class="lbl">${esc(c.label)}</span>
          <span class="val">${num(v)}<span style="color:var(--muted);font-weight:400"> / ${num(c.points)}</span></span></div>
        ${input}${ticks}
        ${why ? `<div class="why"><b>Claude's reasoning</b><br>${esc(why)}</div>` : ''}
      </div>`;
  });

  html += `<label class="fieldLabel" for="cmt">Comment to student
      <span class="hint">— posted to Canvas with the grade</span></label>
    <textarea class="comment" id="cmt">${esc(e.comment || '')}</textarea>
    <div class="actionRow" style="margin-top:16px">
      <button class="btn" id="prevS">← Previous</button>
      <button class="btn" id="nextS">Next →</button>
    </div>`;

  host.innerHTML = html;
  host.scrollTop = 0;

  host.querySelectorAll('input[type=range]').forEach(r => {
    r.addEventListener('input', ev => {
      const el = ev.target;
      const val = el.dataset.tiers ? +el.dataset.tiers.split(',')[+el.value] : +el.value;
      const head = el.closest('.crit').querySelector('.val');
      const c = crits.find(x => x.id === el.dataset.cid);
      head.innerHTML = `${num(val)}<span style="color:var(--muted);font-weight:400"> / ${num(c.points)}</span>`;
      queueSave(s.user_id);
    });
  });
  $('#cmt').addEventListener('input', () => queueSave(s.user_id));
  $('#prevS').onclick = () => { S.sel = Math.max(0, S.sel - 1); render(); };
  $('#nextS').onclick = () => { S.sel = Math.min(list.length - 1, S.sel + 1); render(); };
  $('#btnWorkInline').onclick = toggleWork;
  $('#btnGradeOne').onclick = () => doGrade([String(s.user_id)]);
  const remindOne = $('#btnRemindOne');
  if (remindOne) remindOne.onclick = () =>
    openRemind(S.ids.courseId, S.ids.assignmentId, [String(s.user_id)]);
  const one = $('#btnReviewOne');
  if (one) one.onclick = async () => {
    const keep = new Set(S.picked);
    S.picked = new Set([String(s.user_id)]);
    await doMarkReviewed(!e.human_ok);
    S.picked = keep;
    render();
  };
  $('#btnAsk').onclick = () => openAsk(s);
  const cfC = $('#cfCanvas'); if (cfC) cfC.onclick = () => resolveConflicts([String(s.user_id)], 'canvas');
  const cfM = $('#cfMine'); if (cfM) cfM.onclick = () => resolveConflicts([String(s.user_id)], 'mine');

  renderWork(s, e);
}

/* What the gradebook holds for this student, and whether the student can see
   it. `posted_at` is Canvas's word for visible; a score with no posted_at is
   in the gradebook but hidden. */
function canvasTag(s, e) {
  if (s.canvas_score == null) return '';
  const vis = s.canvas_posted_at ? 'visible to student' : 'hidden from student';
  const mine = finalOf(e);
  const differs = mine != null && Math.abs(mine - s.canvas_score) >= 0.005;
  const when = fmtDate(s.canvas_graded_at);
  return `<span class="tag ${s.canvas_posted_at ? 'ok' : ''}" title="${when ? 'graded in Canvas ' + esc(when) : 'in the Canvas gradebook'}">Canvas: ${num(s.canvas_score)} · ${vis}${differs ? ' · differs from yours' : ''}</span>`;
}

/* Names the curve steps in the order they were applied, so a stacked curve is
   legible rather than just a number. */
function curveSteps(e) {
  const steps = ((e.curve || {}).steps) || [];
  if (!steps.length) return '';
  const words = steps.map(st => {
    const where = st.scope && st.scope !== 'total'
      ? (rubric().find(c => c.id === st.scope) || {}).label || st.scope
      : 'the whole score';
    const how = st.kind === 'flat' ? `+${num(st.amount)} points`
      : st.kind === 'scale' ? `+${num(st.amount)}%`
        : st.kind === 'target_mean' ? `mean to ${num(st.target)}%`
          : st.kind === 'floor' ? `floor at ${num(st.target)}%`
            : st.kind === 'to_top' ? 'curved to the top score'
              : st.kind === 'sqrt' ? 'square-root curve' : st.kind;
    return `${how} on ${where}${st.label ? ` (${st.label})` : ''}`;
  });
  return ': ' + words.join(', then ');
}

function noScoreNote(s, e) {
  const why = unscoredReason(s, e);
  return `<div class="callout" style="margin-bottom:14px"><b>No score:
    ${esc(why)}.</b> This is not a zero. It is left out of the class average, the
    letter grades and the rubric analysis, and it is not pushed to Canvas.
    ${why === 'no submission'
      ? 'Whether a missing submission earns a zero is your call: set it below, or let your Canvas missing-submission policy handle it.'
      : 'Open the submitted work and score it by hand, or re-sync if the file should have been readable.'}</div>`;
}

function aiDeltaNote(e) {
  if (!isScored(e)) return 'not graded yet';
  const ai = e.ai && e.ai.total;
  if (e.source === 'human' && ai != null && ai !== e.total) {
    const d = e.total - ai;
    return `Claude proposed ${ai} · <b>${d > 0 ? '+' : ''}${d}</b>`;
  }
  if (e.source === 'human') return 'set by you';
  if (e.source === 'canvas') {
    return 'pulled from Canvas' + (ai != null && ai !== e.total ? ` · Claude proposed ${ai}` : '');
  }
  return 'proposed by Claude — adjust the sliders to override';
}

function sgUrl(uid) {
  const base = (S.health.base_url || '').replace(/\/$/, '');
  return `${base}/courses/${S.ids.courseId}/gradebook/speed_grader?assignment_id=${S.ids.assignmentId}&student_id=${uid}`;
}

let saveTimer = null;
function queueSave(uid) {
  setStatus('unsaved…');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveStudent(uid), 700);
}
async function saveStudent(uid) {
  const crits = rubric();
  const scores = {};
  document.querySelectorAll('#detail input[type=range]').forEach(el => {
    scores[el.dataset.cid] = el.dataset.tiers ? +el.dataset.tiers.split(',')[+el.value] : +el.value;
  });
  const comment = ($('#cmt') || {}).value || '';
  try {
    const r = await api(`/a/${S.ids.courseId}/${S.ids.assignmentId}/student/${uid}`,
      { body: { scores, comment, source: 'human' } });
    S.ws.draft.students[String(uid)] = r.student;
    setStatus('saved', 'ok');
    renderRoster();
    const card = $('.bigScore');
    if (card) card.innerHTML = `${r.student.total}<span class="of"> / ${S.ws.draft.points_possible || 0}</span>`;
    const note = $('.origNote'); if (note) note.innerHTML = aiDeltaNote(r.student);
  } catch (err) { setStatus('save failed: ' + err.message, 'err'); }
}

/* -------------------------------------------------------------- work pane */
function toggleWork() {
  S.showWork = !S.showWork;
  document.body.classList.toggle('showWork', S.showWork);
  const b = $('#btnWork'); if (b) b.textContent = S.showWork ? 'Hide work' : 'Show work';
  const i = $('#btnWorkInline'); if (i) i.textContent = (S.showWork ? 'Hide' : 'View') + ' submitted work';
}

/* -------------------------------------------------------- code rendering */
// extract.py wraps source files in a language-tagged fence. Split on those so
// code becomes a highlighted block and surrounding prose stays prose.
const FENCE = /```([A-Za-z0-9_+-]*)\n([\s\S]*?)```/g;

// Our file extensions mapped onto highlight.js language names. Anything absent
// falls through to auto-detection, which is fine for the long tail.
const HLJS_LANG = {
  cs: 'csharp', py: 'python', js: 'javascript', ts: 'typescript',
  jsx: 'javascript', tsx: 'typescript', java: 'java', kt: 'kotlin',
  c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', m: 'objectivec',
  swift: 'swift', go: 'go', rs: 'rust', rb: 'ruby', php: 'php', lua: 'lua',
  sql: 'sql', html: 'xml', htm: 'xml', xml: 'xml', css: 'css', scss: 'scss',
  yaml: 'yaml', yml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini',
  sh: 'bash', bat: 'dos', json: 'json', md: 'markdown',
  shader: 'glsl', hlsl: 'glsl', glsl: 'glsl',
};

function codeBlock(lang, code) {
  const mapped = HLJS_LANG[(lang || '').toLowerCase()] || '';
  const body = code.replace(/\s+$/, '');
  let out = esc(body);
  const hl = window.hljs;
  if (hl) {
    try {
      out = (mapped && hl.getLanguage(mapped))
        ? hl.highlight(body, { language: mapped, ignoreIllegals: true }).value
        : hl.highlightAuto(body).value;
    } catch { /* keep the escaped source */ }
  }
  const lines = body.split('\n').length;
  return `<div class="codeBlock"><div class="codeBar">
      <span>${esc(mapped || lang || 'text')}</span>
      <span style="flex:1"></span>
      <span>${lines} line${lines === 1 ? '' : 's'}</span></div>
    <pre class="hljs"><code>${out}</code></pre></div>`;
}

function renderProse(text) {
  let html = '', last = 0, m;
  FENCE.lastIndex = 0;
  while ((m = FENCE.exec(text)) !== null) {
    const before = text.slice(last, m.index).trim();
    if (before) html += `<div class="prose">${esc(before)}</div>`;
    html += codeBlock(m[1], m[2]);
    last = m.index + m[0].length;
  }
  const rest = text.slice(last).trim();
  if (rest) html += `<div class="prose">${esc(rest)}</div>`;
  return html || `<div class="prose">${esc(text)}</div>`;
}

/* ---------------------------------------------------------- blend / 3D */
function fileUrl(name) {
  return `/api/a/${S.ids.courseId}/${S.ids.assignmentId}/file?name=${encodeURIComponent(name)}`;
}

const CHIPS = [
  ['objects', 'objects', v => v > 0],
  ['faces', 'faces', v => v > 0],
  ['tris', 'tris', v => v > 0],
  ['quad_pct', '% quads', v => v >= 90],
  ['ngons', 'n-gons', v => v === 0],
  ['unapplied_scale', 'unapplied scale', v => v === 0],
  ['unapplied_rotation', 'unapplied rot', v => v === 0],
  ['default_names', 'default names', v => v === 0],
  ['no_uvs', 'meshes w/o UVs', v => v === 0],
  ['missing_textures', 'missing textures', v => v === 0],
  ['non_manifold', 'non-manifold', v => v === 0],
];

function blendCard(p) {
  const d = p.data || {};
  const st = d.stats || {};
  let html = `<div class="workSec blendCard"><h4>${esc(p.label)}
      <span class="wc">Blender ${esc(st.saved_with || '?')}</span></h4>`;

  if (d.empty_scene) html += '<div class="callout bad">This file opens but has no mesh objects at all.</div>';
  if (d.default_scene) html += '<div class="callout">This looks like Blender\'s untouched startup file.</div>';

  html += '<div class="chips blendChips">' + CHIPS.map(([k, label, good]) => {
    const v = st[k];
    if (v === undefined || v === null) return '';
    const cls = good(v) ? 'ok' : 'bad';
    return `<span class="statChip ${cls}"><b>${esc(String(v))}</b> ${esc(label)}</span>`;
  }).join('') + '</div>';

  if (d.model) {
    const mb = (d.glb_bytes || 0) / 1e6;
    html += `<div class="viewerWrap" data-glb="${esc(fileUrl(d.model))}" data-bytes="${d.glb_bytes || 0}">
        <div class="viewerBar">
          <button class="btn sm" data-vw="load">Load 3D model${mb ? ` (${mb.toFixed(1)} MB)` : ''}</button>
          <span class="viewerTools hidden">
            <button class="btn sm" data-vw="textured">Textured</button>
            <button class="btn sm" data-vw="clay">Clay</button>
            <button class="btn sm" data-vw="normals">Normals</button>
            <button class="btn sm" data-vw="wire">Wireframe</button>
            <button class="btn sm" data-vw="grid">Grid</button>
            <button class="btn sm" data-vw="reset">Reset view</button>
          </span>
          <span class="spacer" style="flex:1"></span>
          <span class="viewerInfo"></span>
        </div>
        <div class="viewerHost"></div>
      </div>`;
  } else if (d.glb_bytes === 0 && d.status) {
    html += '<div class="callout">No 3D preview was produced for this file. The report above still applies.</div>';
  }

  if (d.sheet) {
    html += `<img class="workImg" loading="lazy" alt="rendered views of the model"
       src="${fileUrl(d.sheet)}">
      <div class="sheetNote">Rendered by this tool with neutral solid shading, not by the
        student. Judge form and proportion here, not lighting or presentation.</div>`;
  }

  html += `<details class="blendReport"><summary>Full scene report</summary>
      <div class="prose small">${esc(p.text || '')}</div></details>`;
  if ((d.notes || []).length) {
    html += `<details class="blendReport"><summary>Safety changes made before reading
      (${d.notes.length})</summary><div class="prose small">${esc(d.notes.join('\n'))}</div></details>`;
  }
  return html + '</div>';
}

/* --------------------------------------------------------------- video */
/* A screen recording is graded by watching it, not by reading it, so the only
   job here is to make watching fast: it plays in place, seeks against a byte
   range off local disk, and remembers nothing about the grade. Nothing on this
   card is ever sent to Claude -- video is by far the most expensive thing a
   student can submit and the cheapest thing to watch, so the tool watches
   nothing and says so. */
const VIDEO_RATES = [1, 1.25, 1.5, 2];

/* Mirrors VIDEO_EXT and VIDEO_PLAYABLE in extract.py. Kept here as well because
   an assignment synced before video was understood has the file sitting on disk
   but a part that still reads "no text extractor for .mov": the extension is
   enough to put a player on it, which beats re-syncing to watch something that
   was already downloaded. */
const VIDEO_RE = /\.(mov|mp4|m4v|webm|avi|mkv|mpg|mpeg|wmv|ogv|3gp|mts|flv)$/i;
const VIDEO_PLAYABLE_RE = /\.(mov|mp4|m4v|webm|ogv)$/i;
function isVideoPart(p) {
  return p.kind === 'video' || (!!p.path && VIDEO_RE.test(p.path));
}

function videoCard(p) {
  const d = p.data || {};
  const name = (p.path || '').split(/[\\/]/).pop();
  const url = fileUrl(name);
  const mb = (d.bytes || 0) / 1e6;
  const size = mb ? `${mb.toFixed(mb < 10 ? 1 : 0)} MB` : '';
  // A part cached before video was understood carries no data payload, so
  // everything below falls back to the filename.
  const ext = (d.ext || (name.match(/\.[^.]+$/) || [''])[0] || '').toLowerCase();
  const playable = d.playable != null ? d.playable : VIDEO_PLAYABLE_RE.test(name);
  const head = `<h4>${esc(p.label)} <span class="wc">video${
    size ? ' · ' + size : ''}</span></h4>`;

  if (!playable) {
    return `<div class="workSec videoCard">${head}
      <div class="callout">${esc(ext || 'this format')} is one most browsers
        cannot play. Download it and open it in a desktop player, or watch it in
        SpeedGrader.
        <div class="videoBar" style="margin-top:9px">
          <a class="btn sm" href="${url}&amp;dl=1" download>Download</a>
        </div></div></div>`;
  }

  return `<div class="workSec videoCard">${head}
    <div class="videoWrap" data-dl="${url}&amp;dl=1">
      <video class="workVideo" controls preload="metadata" playsinline
        src="${url}"></video>
      <div class="videoBar">
        <span class="videoSpeed">${VIDEO_RATES.map(r =>
          `<button class="btn sm${r === 1 ? ' on' : ''}" data-rate="${r}"
            >${r}x</button>`).join('')}</span>
        <span class="spacer" style="flex:1"></span>
        <span class="videoNote">not graded automatically — watch it and score by hand</span>
        <a class="btn sm" href="${url}&amp;dl=1" download>Download</a>
      </div>
    </div></div>`;
}

/* Speed is the one control a browser does not put on screen, and it is the one
   that matters when there are forty recordings to get through. */
function wireVideos(host) {
  host.querySelectorAll('.videoWrap').forEach(wrap => {
    const vid = wrap.querySelector('video');
    if (!vid) return;

    // A .mov can hold anything. H.264 plays everywhere; ProRes and some HEVC
    // phone recordings do not decode in a browser at all, and an empty black
    // box with no explanation is the worst version of that. Say what happened
    // and give them the file.
    vid.onerror = () => {
      const dl = wrap.dataset.dl || '';
      wrap.innerHTML = `<div class="callout" style="margin:0">This file will not
        play in the browser — usually a codec it cannot decode (ProRes, or some
        phone HEVC). Download it and open it in a desktop player, or watch it in
        SpeedGrader.
        <div class="videoBar" style="margin-top:9px;border:0;padding:0;background:none">
          <a class="btn sm" href="${dl}" download>Download</a>
        </div></div>`;
    };

    wrap.querySelectorAll('[data-rate]').forEach(btn => {
      btn.onclick = () => {
        vid.playbackRate = +btn.dataset.rate;
        wrap.querySelectorAll('[data-rate]').forEach(b => b.classList.remove('on'));
        btn.classList.add('on');
      };
    });
  });
}

// Models auto-load, but after a beat. The roster is keyboard-navigable with j/k,
// so mounting instantly would fetch and build a scene for every student you pass
// through. The timer is cleared on the next render, so only the student you
// actually stop on gets loaded.
const AUTO_LOAD_DELAY_MS = 280;
const AUTO_LOAD_MAX_BYTES = 25e6;
let autoLoadTimer = null;

function wireViewers(host) {
  host.querySelectorAll('.viewerWrap').forEach(wrap => {
    const url = wrap.dataset.glb;
    const bytes = +(wrap.dataset.bytes || 0);
    const hostEl = wrap.querySelector('.viewerHost');
    const tools = wrap.querySelector('.viewerTools');
    const info = wrap.querySelector('.viewerInfo');
    const loadBtn = wrap.querySelector('[data-vw="load"]');

    const start = () => {
      const V = window.BlendViewer;
      if (!V) { info.textContent = '3D viewer failed to load'; return; }
      if (loadBtn) { loadBtn.disabled = true; loadBtn.classList.add('hidden'); }
      V.mount(hostEl, url, { onReady: r => {
        info.textContent = `${r.triangles.toLocaleString()} triangles`;
        tools.classList.remove('hidden');
      }});
    };

    // Anything unusually large still waits for a deliberate click.
    if (bytes && bytes <= AUTO_LOAD_MAX_BYTES) {
      autoLoadTimer = setTimeout(start, AUTO_LOAD_DELAY_MS);
    }

    wrap.querySelectorAll('[data-vw]').forEach(btn => btn.onclick = () => {
      const V = window.BlendViewer;
      if (!V) { info.textContent = '3D viewer failed to load'; return; }
      const act = btn.dataset.vw;
      if (act === 'load') {
        clearTimeout(autoLoadTimer);
        start();
      } else if (act === 'wire') { btn.classList.toggle('on', V.toggleWireframe()); }
      else if (act === 'grid') { btn.classList.toggle('on', !V.toggleHelpers()); }
      else if (act === 'reset') { V.reset(); }
      else { V.setMode(act);
             tools.querySelectorAll('[data-vw]').forEach(b => {
               if (['textured', 'clay', 'normals'].includes(b.dataset.vw)) b.classList.remove('on');
             });
             btn.classList.add('on'); }
    });
  });
}

function renderWork(s, e) {
  // Free the previous WebGL context before innerHTML orphans its canvas, and
  // drop any auto-load that was queued for the student we just left.
  clearTimeout(autoLoadTimer);
  if (window.BlendViewer) window.BlendViewer.dispose();
  const host = $('#workPane');
  // innerHTML alone leaves an orphaned <video> downloading in the background,
  // and j/k through the roster would stack up a fetch per student.
  host.querySelectorAll('video').forEach(v => { v.pause(); v.removeAttribute('src'); v.load(); });
  const meta = [];
  if (s.submitted_at) meta.push('submitted ' + fmtDate(s.submitted_at));
  if (s.late) meta.push(lateText(s).toUpperCase());
  meta.push(`${s.words || 0} words`);

  let body = '';
  if (s.status === 'unsubmitted') {
    body = `<div class="callout bad">Canvas shows this student as <b>unsubmitted</b> — there is nothing to read.
      <br><br><a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Confirm in SpeedGrader ↗</a></div>`;
  } else {
    if (s.filenames && s.filenames.length) {
      body += `<div class="workSec"><h4>Attachments</h4>
        <div style="font:11.5px var(--mono);color:var(--muted);word-break:break-all">
        ${s.filenames.map(f => esc(decodeURIComponent(f))).join('<br>')}</div></div>`;
    }
    (s.parts || []).forEach(p => {
      if (p.kind === 'text' && p.text && p.text.trim()) {
        body += `<div class="workSec"><h4>${esc(p.label)} <span class="wc">${p.words} words</span></h4>
          ${renderProse(p.text)}</div>`;
      } else if (p.kind === 'image') {
        const name = (p.path || '').split(/[\\/]/).pop();
        body += `<div class="workSec"><h4>${esc(p.label)}</h4>
          <img class="workImg" loading="lazy" alt="${esc(p.label)}"
            src="/api/a/${S.ids.courseId}/${S.ids.assignmentId}/file?name=${encodeURIComponent(name)}"></div>`;
      } else if (isVideoPart(p)) {
        body += videoCard(p);
      } else if (p.kind === 'blend') {
        body += blendCard(p);
      } else if (p.note) {
        body += `<div class="callout">${esc(p.label)} — ${esc(p.note)}
          <br><br><a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Open in SpeedGrader ↗</a></div>`;
      }
    });
    if (s.discussion) {
      const d = s.discussion;
      if (d.post) {
        body += `<div class="workSec"><h4>Original post <span class="wc">${d.post.words} words · ${esc(fmtDate(d.post.created_at))}</span></h4>
          <div class="prose">${esc(d.post.text)}</div></div>`;
      } else body += '<div class="callout bad">No original post on this topic.</div>';
      (d.replies || []).forEach(r => {
        body += `<div class="workSec"><h4>Reply to ${esc(r.to)} <span class="wc">${r.words} words</span></h4>
          <div class="quoted">${esc(r.to_excerpt)}</div><div class="prose small">${esc(r.text)}</div></div>`;
      });
      if (!(d.replies || []).length) body += '<div class="callout bad">No reply to a classmate on this topic.</div>';
    }
    if (!body) body = `<div class="callout bad">Nothing readable was extracted.
      <a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Open in SpeedGrader ↗</a></div>`;
  }

  host.innerHTML = `<div class="workHead"><h3>Submitted work</h3>
      <span class="meta">${esc(meta.join(' · '))}</span><span class="spacer"></span>
      <a class="btn sm" target="_blank" rel="noopener" href="${sgUrl(s.user_id)}">SpeedGrader ↗</a>
      <button class="btn sm" onclick="toggleWork()">Close</button></div>
    <div class="workBody">${body}</div>`;
  wireViewers(host);
  wireVideos(host);
  host.scrollTop = 0;
}

/* ----------------------------------------------------------------- events */
$('#search').addEventListener('input', ev => { S.query = ev.target.value.trim().toLowerCase(); S.sel = 0; render(); });
$('#filters').querySelectorAll('.chip').forEach(c => c.onclick = () => {
  setFilter(c.dataset.f);
  render();
});

/* The chip is a working preference, not a property of the assignment, so it
   outlives both. Someone who works from "Turned in" wants it still chosen the
   next morning, not reset to All by a browser restart. */
function setFilter(name) {
  S.filter = name;
  S.sel = 0;
  $('#filters').querySelectorAll('.chip').forEach(
    x => x.setAttribute('aria-pressed', String(x.dataset.f === name)));
  try { localStorage.setItem('cg.filter', name); } catch (_) { /* private mode */ }
}

(function restoreFilter() {
  let saved = null;
  try { saved = localStorage.getItem('cg.filter'); } catch (_) { /* ignore */ }
  // Matched against the chips that exist rather than fed to a selector: a
  // stored name from an older build should be ignored, not thrown over.
  const known = [...$('#filters').querySelectorAll('.chip')].some(c => c.dataset.f === saved);
  if (known) setFilter(saved);
})();

document.addEventListener('keydown', ev => {
  if (/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) return;
  if ($('#viewWork').classList.contains('hidden')) return;
  const n = students().length;
  if (ev.key === 'j' || ev.key === 'ArrowDown') { ev.preventDefault(); S.sel = Math.min(n - 1, S.sel + 1); render(); }
  if (ev.key === 'k' || ev.key === 'ArrowUp') { ev.preventDefault(); S.sel = Math.max(0, S.sel - 1); render(); }
  if (ev.key === 'w') { ev.preventDefault(); toggleWork(); }
});

/* The grader is the Grade area of the Studio: the first card on the course hub
   and the first tab of the area bar. */
registerArea({
  id: 'grade', label: 'Grade', zone: 'grade',
  open: (cid) => openCourse(cid),
  badge: hub => hub?.grade?.badge ?? null,
});
