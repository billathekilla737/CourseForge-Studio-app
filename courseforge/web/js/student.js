/* Search across courses, then one student's record: courses, accommodation,
   extensions, grades, the audit lines, inbox and notes. The note and the
   history copy to Canvas user files.

   Nicknames are a display spelling (John "Jack" Doe). The legal name stays
   on .name for sorting, for Canvas, and for anything that gets saved. */
'use strict';

let nickById = {};
let nickLoad = null;

function formatNick(legal, nick) {
  const name = String(legal || '').trim().replace(/\s+/g, ' ');
  let n = String(nick || '').replace(/[“”"]/g, '').trim();
  n = n.replace(/^['‘’]+|['‘’]+$/g, '').replace(/\s+/g, ' ');
  if (!n) return name;
  const quoted = '"' + n + '"';
  if (name.indexOf(quoted) !== -1) return name;
  if (!name) return quoted;
  if (name.indexOf(',') !== -1) {
    const bits = name.split(',');
    const last = bits[0].trim();
    const rest = bits.slice(1).join(' ').trim().split(/\s+/).filter(Boolean);
    if (rest.length && last) return [rest[0], quoted].concat(rest.slice(1), [last]).join(' ');
  }
  const parts = name.split(' ');
  return [parts[0], quoted].concat(parts.slice(1)).join(' ');
}

function studentLabel(person) {
  if (person == null) return '';
  if (typeof person === 'string') return person;
  const uid = String(person.user_id || person.id || '');
  const nick = person.nickname || nickById[uid] || '';
  const legal = person.legal_name || person.name || '';
  return formatNick(legal, nick) || legal || uid;
}

function loadNicks() {
  if (!nickLoad) {
    nickLoad = api('/nicknames').then(data => {
      nickById = (data && data.by_id) || {};
      return nickById;
    }).catch(() => nickById);
  }
  return nickLoad;
}

async function saveNickname(userId, raw) {
  const data = await api('/student/' + encodeURIComponent(userId) + '/nickname', {
    body: { nickname: raw },
  });
  const nick = (data && data.nickname) || '';
  if (nick) nickById[String(userId)] = nick;
  else delete nickById[String(userId)];
  return data;
}

/* Swap a stored legal name for the nickname spelling in a sentence the
   instructor is reading. Short single words are left alone. */
function nickSentence(text, people) {
  let out = String(text || '');
  (people || []).forEach(p => {
    const legal = (p && (p.legal_name || p.name)) || '';
    const shown = studentLabel(p);
    if (!legal || shown === legal) return;
    if (legal.indexOf(' ') === -1 && legal.indexOf(',') === -1) return;
    out = out.split(legal).join(shown);
  });
  return out;
}

function courseCount(s) {
  if (s.course_refs && s.course_refs.length) return s.course_refs.length;
  return (s.courses || []).length;
}

/* RISK-INDEX: riskById, riskMark, the Check button, the Needs-a-look sort,
   and the section on the student page. See docs/RISK-INDEX.md. */
let riskById = {};
let repaintStudents = function () {};
let riskWatch = null;

function riskMove(n) {
  if (n == null) return '';
  return n > 0 ? ' +' + n : ' ' + n;
}

function riskMark(s) {
  const row = riskById[String(s.user_id)];
  if (!row) return '';
  const attend = row.attend == null ? 0 : row.attend;
  const why = (row.reasons || []).slice(0, 3).concat(row.attend_reasons || []).slice(0, 4).join('; ');
  return `<span class="stScore" title="${esc(why)}">
    <span class="stRisk stRisk-${esc(row.band)}">${row.score}${esc(riskMove(row.trend))}</span><span class="stKey">work</span>
    <span class="stRisk stRisk-${esc(row.attend_band || 'low')}">${attend}${esc(riskMove(row.attend_trend))}</span><span class="stKey">attend</span>
  </span>`;
}

function studentHit(s, mode) {
  const courses = s.courses || [];
  const marks = [];
  if (mode === 'courses') {
    const n = courseCount(s);
    marks.push(`<span class="stCount" title="${n} course${n === 1 ? '' : 's'} this term">${n}</span>`);
  }
  if (s.on_roster) marks.push('<span class="pill good">On the list</span>');
  else if (mode === 'accommodation') marks.push('<span class="pill muted">Not on the list</span>');
  return `<a class="stHit" href="#/student/${esc(s.user_id)}">
    <b>${esc(studentLabel(s))}</b>
    ${riskMark(s)}
    ${marks.join('')}
    <span>${esc(courses.slice(0, 3).join(', '))}${
      courses.length > 3 ? ' +' + (courses.length - 3) : ''}</span>
  </a>`;
}

function openStudents() {
  showView('area');
  $('#areaTitle').textContent = 'Students';
  $('#areaHint').textContent = 'This term, across the courses you are teaching now.';
  $('#areaActions').innerHTML = '';
  $('#areaTabs').hidden = true;
  $('#areaBody').innerHTML = `
    <div class="studentPage">
      <div class="stSearchRow">
        <label class="stSearch">Find a student
          <input id="stQuery" type="search" autocomplete="off"
            placeholder="Filter the list by name, SIS id or login">
        </label>
        <label class="rosterSort">Term
          <select id="stTerm" aria-label="Which term"></select>
        </label>
        <label class="rosterSort">Organize
          <select id="stSort" aria-label="Organize the student list">
            <option value="name">Name A–Z</option>
            <option value="name-desc">Name Z–A</option>
            <option value="course">By course, then name</option>
            <option value="accommodation">Accommodation list first</option>
            <option value="courses">In the most courses</option>
            <option value="risk">Needs a look</option>
          </select>
        </label>
      </div>
      <section class="stRiskBox" id="stRiskBox">
        <h3>Who needs a look</h3>
        <p class="muted" id="stRiskNote">No check yet. This reads each class you are teaching this term, once, and saves the result on this computer.</p>
        <div class="stBar" id="stRiskBar" hidden role="progressbar"
          aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i></i></div>
        <div class="foot">
          <button class="btn" id="stRiskRun" type="button">Check this term</button>
        </div>
        <div id="stRiskSummary"></div>
      </section>
      <p class="muted" id="stSearchNote">Loading this term’s students…</p>
      <div id="stResults"></div>
    </div>`;
  document.title = 'Students · CourseForge Studio';
  crumbs([{ label: 'Courses', href: '#/' }, { label: 'Students' }]);

  let master = [];
  const lastName = s => {
    const sort = String(s.sortable_name || '').trim();
    if (sort) return sort.toLowerCase();
    const parts = String(s.name || '').trim().split(/\s+/);
    return ((parts[parts.length - 1] || '') + ' ' + (s.name || '')).toLowerCase();
  };
  const byName = (a, b) => lastName(a).localeCompare(lastName(b))
    || String(a.name || '').localeCompare(String(b.name || ''));
  const paint = () => {
    const q = (($('#stQuery') || {}).value || '').trim().toLowerCase();
    const mode = (($('#stSort') || {}).value) || 'name';
    let rows = !q ? master.slice() : master.filter(s =>
      [s.name, s.sortable_name, s.nickname, studentLabel(s), s.sis_user_id, s.login_id,
        (s.courses || []).join(' ')]
        .join(' ').toLowerCase().includes(q));
    const note = $('#stSearchNote');
    const host = $('#stResults');
    if (note) note.textContent = master.length
      ? (q ? `${rows.length} of ${master.length}` : `${master.length} student${master.length === 1 ? '' : 's'}`)
      : 'No students in this term yet.';
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = '<p class="muted">No student matches that.</p>';
      return;
    }
    if (mode === 'course') {
      const groups = new Map();
      rows.forEach(s => {
        const refs = (s.course_refs && s.course_refs.length)
          ? s.course_refs : (s.courses || []).map(name => ({ name }));
        (refs.length ? refs : [{ name: 'No course' }]).forEach(ref => {
          const title = ref.name || 'Course';
          if (!groups.has(title)) groups.set(title, []);
          groups.get(title).push(s);
        });
      });
      const titles = [...groups.keys()].sort((a, b) => a.localeCompare(b));
      host.innerHTML = titles.map(title => {
        const people = groups.get(title).slice().sort(byName);
        return `<h3 class="stGroup">${esc(title)} <span class="muted">${people.length}</span></h3>`
          + people.map(s => studentHit(s, mode)).join('');
      }).join('');
      return;
    }
    if (mode === 'name-desc') rows.sort((a, b) => byName(b, a));
    else if (mode === 'accommodation') {
      rows.sort((a, b) => ((b.on_roster === true) - (a.on_roster === true)) || byName(a, b));
    } else if (mode === 'risk') {
      rows.sort((a, b) => {
        const ra = riskById[String(a.user_id)];
        const rb = riskById[String(b.user_id)];
        const sa = ra ? Math.max(ra.score || 0, ra.attend || 0) : -1;
        const sb = rb ? Math.max(rb.score || 0, rb.attend || 0) : -1;
        return sb - sa || byName(a, b);
      });
    } else if (mode === 'courses') {
      const groups = new Map();
      rows.forEach(s => {
        const n = courseCount(s);
        if (!groups.has(n)) groups.set(n, []);
        groups.get(n).push(s);
      });
      const levels = [...groups.keys()].sort((a, b) => b - a);
      host.innerHTML = levels.map(n => {
        const people = groups.get(n).slice().sort(byName);
        const label = n === 1 ? '1 course' : n + ' courses';
        return `<h3 class="stGroup">${esc(label)}</h3>` + people.map(s => studentHit(s, mode)).join('');
      }).join('');
      return;
    } else rows.sort(byName);
    host.innerHTML = rows.map(s => studentHit(s, mode)).join('');
  };

  const load = async (term) => {
    await loadNicks();
    const note = $('#stSearchNote');
    if (note) note.textContent = 'Loading the roster…';
    const qs = term ? ('?term=' + encodeURIComponent(term)) : '';
    const data = await api('/students' + qs);
    master = data.students || [];
    const sel = $('#stTerm');
    if (sel && !sel.dataset.ready) {
      const terms = data.terms || [];
      sel.innerHTML = terms.map(t =>
        `<option value="${esc(t.label)}"${t.label === data.term ? ' selected' : ''}>${
          esc(t.label)} (${t.count})</option>`).join('')
        + `<option value="__all"${data.term ? '' : ' selected'}>All terms</option>`;
      sel.dataset.ready = '1';
      sel.onchange = () => load(sel.value).catch(err => { if (note) note.textContent = err.message; });
    }
    paint();
  };

  const sortBox = $('#stSort');
  if (sortBox) {
    let saved = 'name';
    try { saved = localStorage.getItem('cg.studentSort') || 'name'; } catch (_) { /* private mode */ }
    if ([...sortBox.options].some(o => o.value === saved)) sortBox.value = saved;
    sortBox.onchange = () => {
      try { localStorage.setItem('cg.studentSort', sortBox.value); } catch (_) { /* private mode */ }
      paint();
    };
  }
  $('#stQuery').oninput = paint;
  repaintStudents = paint;
  const riskBtn = $('#stRiskRun');
  if (riskBtn) riskBtn.onclick = () => runRiskScan();
  loadSavedRisk().catch(() => {});
  load('').catch(err => {
    const note = $('#stSearchNote');
    if (note) note.textContent = err.message;
  });
  $('#stQuery').focus();
}

function scannedLabel(iso) {
  if (!iso) return 'Not scanned yet';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'Last scanned ' + iso;
  return 'Last scanned ' + d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function paintRiskSummary(data) {
  if (riskWatch) return;
  const note = $('#stRiskNote');
  const host = $('#stRiskSummary');
  if (!data || data.empty) {
    if (note) note.textContent = 'No check yet. This reads each class you are teaching this term, once, and saves the result on this computer.';
    if (host) host.innerHTML = '';
    return;
  }
  const counts = data.counts || {};
  if (note) {
    const when = scannedLabel(data.scanned_at);
    const fresh = data.new_due ? ` Read ${data.new_due} newly due.` : '';
    const kept = data.already_scanned ? ` ${data.already_scanned} already scanned were left as they were.` : '';
    note.textContent = when
      + `. Checked ${data.courses_checked || 0} class(es)`
      + (data.seconds != null ? ` in ${data.seconds}s` : '')
      + '.' + fresh + kept
      + ' Work is grades and missing assignments. Attend is absences; tardies add a little. Higher needs a look sooner.';
  }
  if (!host) return;
  const fails = data.failures || [];
  const attendCounts = data.attend_counts || {};
  host.innerHTML = `<p><span class="stKey">Work</span>
      <span class="stRisk stRisk-high">${counts.high || 0} high</span>
      <span class="stRisk stRisk-medium">${counts.medium || 0} medium</span>
      <span class="stRisk stRisk-low">${counts.low || 0} low</span></p>
    <p><span class="stKey">Attend</span>
      <span class="stRisk stRisk-high">${attendCounts.high || 0} high</span>
      <span class="stRisk stRisk-medium">${attendCounts.medium || 0} medium</span>
      <span class="stRisk stRisk-low">${attendCounts.low || 0} low</span></p>`
    + (fails.length ? `<p class="muted">${fails.length} class(es) could not be read: ${
        esc(fails.map(f => f.name).join(', '))}</p>` : '');
}

function applyRisk(data) {
  riskById = {};
  (data && data.students || []).forEach(row => { riskById[String(row.user_id)] = row; });
  paintRiskSummary(data);
  repaintStudents();
}

async function loadSavedRisk() {
  const data = await api('/students/risk');
  applyRisk(data);
  return data;
}

function ensureRiskChip() {
  if ($('#riskChip')) return;
  const el = document.createElement('div');
  el.id = 'riskChip';
  const inbox = $('#inboxChip');
  if (inbox && inbox.parentNode) inbox.parentNode.insertBefore(el, inbox);
  else document.querySelector('header') && document.querySelector('header').appendChild(el);
}

function paintRiskProgress(info) {
  const note = $('#stRiskNote');
  const bar = $('#stRiskBar');
  const btn = $('#stRiskRun');
  const running = !info || info.state === 'running';
  const done = +((info && info.done) || 0);
  const total = +((info && info.total) || 0);
  const pct = info && info.state === 'done' ? 100
    : (total ? Math.min(99, Math.round(100 * done / total)) : 8);
  if (bar) {
    bar.hidden = !running;
    const fill = bar.querySelector('i');
    if (fill) fill.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(running ? pct : 100));
  }
  if (note && running) note.textContent = (info && info.message) || 'Checking classes…';
  if (btn) btn.disabled = !!running;
  ensureRiskChip();
  const host = $('#riskChip');
  if (!host) return;
  if (running) {
    host.innerHTML = `<a class="riskChip" href="#/students" title="${esc((info && info.message) || 'Checking classes')}">
      <span class="riskChipBar" aria-hidden="true"><i style="width:${pct}%"></i></span>
      ${total ? `Reading ${Math.min(done + 1, total)} of ${total}` : 'Checking…'}</a>`;
    return;
  }
  if (info && info.state === 'error') {
    host.innerHTML = `<a class="riskChip bad" href="#/students" title="${esc(info.error || 'The check failed')}">Check failed</a>`;
    if (note) note.textContent = info.error || 'The check failed. The student list is unchanged.';
  }
}

async function watchRiskJob(id, opts) {
  opts = opts || {};
  if (riskWatch) return riskWatch;
  ensureRiskChip();
  try { sessionStorage.setItem('cg.riskJob', id); } catch (_) { /* private mode */ }
  riskWatch = (async () => {
    try {
      for (;;) {
        let info;
        try { info = await api('/jobs/' + id); }
        catch (err) {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          paintRiskProgress({ state: 'error', error: err.message });
          return;
        }
        paintRiskProgress(info);
        if (info.state === 'done') {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          const data = await api('/students/risk');
          riskWatch = null;
          applyRisk(data);
          paintRiskProgress({ state: 'done', done: info.total, total: info.total, message: info.message });
          const counts = (data && data.counts) || {};
          const host = $('#riskChip');
          const high = counts.look != null ? counts.look : (counts.high || 0);
          if (host) host.innerHTML = `<a class="riskChip ${high ? 'lit' : ''}" href="#/students"
            title="${esc(scannedLabel(data && data.scanned_at))}${data && data.seconds != null ? ' · ' + data.seconds + 's' : ''}">${
            high ? high + ' need a look' : 'Check finished'}</a>`;
          if (!opts.background) {
            const box = $('#stSort');
            if (box) {
              box.value = 'risk';
              try { localStorage.setItem('cg.studentSort', 'risk'); } catch (_) {}
            }
            repaintStudents();
          }
          return;
        }
        if (info.state === 'error') {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    } finally {
      riskWatch = null;
    }
  })();
  return riskWatch;
}

async function runRiskScan(opts) {
  opts = opts || {};
  if (riskWatch) return riskWatch;
  const note = $('#stRiskNote');
  if (note) note.textContent = 'Starting…';
  paintRiskProgress({ state: 'running', done: 0, total: 0, message: 'Starting…' });
  try {
    const started = await api('/students/risk', { method: 'POST', body: {} });
    return watchRiskJob(started.job, opts);
  } catch (err) {
    paintRiskProgress({ state: 'error', error: err.message });
  }
}

document.addEventListener('studio:booted', () => {
  if (!S.health || !S.health.canvas || S.health.canvas.ok === false) return;
  ensureRiskChip();
  let existing = '';
  try { existing = sessionStorage.getItem('cg.riskJob') || ''; } catch (_) {}
  if (existing) {
    watchRiskJob(existing, { background: true });
    return;
  }
  try {
    if (sessionStorage.getItem('cg.riskBoot')) return;
    sessionStorage.setItem('cg.riskBoot', '1');
  } catch (_) { /* still start; a second tab may overlap */ }
  runRiskScan({ background: true });
});

async function openStudent(userId, courseId) {
  showView('area');
  $('#areaTitle').textContent = 'Student';
  $('#areaHint').textContent = 'loading…';
  $('#areaActions').innerHTML = '<a class="btn sm" href="#/students">All students</a>';
  $('#areaTabs').hidden = true;
  $('#areaTabs').innerHTML = '';
  $('#areaBody').innerHTML = '<p class="muted">Loading what this machine and Canvas already know…</p>';
  document.title = 'Student · CourseForge Studio';
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: 'Students', href: '#/students' },
    { label: 'Student' },
  ]);

  let data;
  try {
    const q = courseId ? ('?course=' + encodeURIComponent(courseId)) : '';
    data = await api('/student/' + encodeURIComponent(userId) + q);
  } catch (err) {
    $('#areaHint').textContent = '';
    $('#areaBody').innerHTML = '<p class="err">' + esc(err.message) + '</p>';
    return;
  }

  const shown = data.display_name || studentLabel(data);
  if (data.nickname) nickById[String(userId)] = data.nickname;
  $('#areaTitle').textContent = shown || ('Student ' + userId);
  document.title = (shown || 'Student') + ' · CourseForge Studio';
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: 'Students', href: '#/students' },
    { label: shown || 'Student' },
  ]);
  $('#areaHint').textContent = (data.nickname && data.name && data.nickname !== data.name)
    ? ('Canvas name: ' + data.name) : (data.note || '');
  const stand = data.standing;
  const notes = (data.notes && data.notes.notes) || '';
  const threads = data.inbox || [];
  const refs = data.course_refs && data.course_refs.length
    ? data.course_refs
    : (data.courses || []).map(name => ({ id: '', name }));
  const history = data.history || [];

  $('#areaBody').innerHTML = `
    <div class="studentPage">
      <section class="stIdentity">
        <div><span class="muted">Canvas name</span> ${esc(data.name || '')}</div>
        <div><span class="muted">Canvas id</span> ${esc(data.user_id)}</div>
        ${data.sis_user_id ? `<div><span class="muted">SIS</span> ${esc(data.sis_user_id)}</div>` : ''}
        ${data.login_id ? `<div><span class="muted">Login</span> ${esc(data.login_id)}</div>` : ''}
        <div><span class="muted">On the accommodation list</span> ${data.on_roster ? 'yes' : 'no'}</div>
      </section>

      <section id="stRiskSec" hidden>
        <h3>How they're doing in each class</h3>
        <div id="stRiskBody"></div>
      </section>

      <section>
        <h3>Courses</h3>
        ${refs.length ? `<ul class="stCourses">${refs.map(c => `<li>${
          c.id ? `<a href="#/c/${esc(c.id)}/grade">${esc(c.name || c.id)}</a>`
                    : esc(c.name || '')
        }</li>`).join('')}</ul>` : '<p class="muted">No course on this login.</p>'}
      </section>

      <section>
        <h3>Standing accommodation</h3>
        <p>${stand
          ? `<b>${esc(stand.label || stand.kind)}</b>${stand.note ? ' — ' + esc(stand.note) : ''}`
          : 'Not on the standing list.'}
          <a href="#/roster">Open the roster</a></p>
      </section>

      <section>
        <h3>Applied extras and extensions</h3>
        <p class="muted" id="stLive">Checking this term only…</p>
        <div id="stExtras"></div>
      </section>

      <section>
        <h3>History</h3>
        <p class="muted">Grades and submissions on this computer, plus the record of
          what was done. A copy is kept in your Canvas files so another PC can show it.</p>
        ${history.length ? `<ol class="stHistory">${history.map(h => `<li>
            <span class="muted">${esc(h.at || '')}</span>
            <b>${esc(h.kind || '')}</b>
            ${h.course ? esc(h.course) : ''}
            ${h.title ? ' · ' + esc(h.title) : ''}
            <div>${esc(nickSentence(h.summary || '', [data]))}</div>
            ${h.course_id && h.assignment_id
              ? `<a href="#/c/${esc(h.course_id)}/a/${esc(h.assignment_id)}">Open the assignment</a>`
              : ''}
          </li>`).join('')}</ol>`
          : '<p class="muted">Nothing recorded yet. Grade, extend or write a note and it will land here.</p>'}
      </section>

      <section>
        <h3>Canvas Inbox</h3>
        <div id="stInbox">${threads.map(t => t.error
          ? `<p class="err">${esc(t.error)}</p>`
          : `<div class="stThread"><a href="#/inbox/${esc(t.id)}">${esc(t.subject)}</a>
              <span class="muted">${esc(t.ago || t.at || '')}</span>
              <p>${esc(t.preview || '')}</p></div>`).join('')
          || '<p class="muted">No recent Canvas Inbox threads with this person.</p>'}</div>
      </section>

      <section>
        <h3>Nickname</h3>
        <p class="muted">Studio shows this spelling on your screens. For John Doe
          and Jack, that is John "Jack" Doe. Grades pushed to Canvas still use
          the Canvas name, and the model never receives the nickname. The
          nickname is kept in your Canvas files so another computer signed in
          as you shows the same spelling.</p>
        <div class="nickRow">
          <label>Goes by
            <input id="stNick" maxlength="40" value="${esc(data.nickname || '')}"
              placeholder="Jack" autocomplete="off">
          </label>
          <button class="btn" id="stNickSave" type="button">Save nickname</button>
        </div>
        <p class="nickPreview" id="stNickPreview"></p>
      </section>

      <section>
        <h3>Instructor notes</h3>
        <p class="muted">Stored with the history in your Canvas user files. Nothing here is sent to Claude.</p>
        <textarea id="stNotes" rows="6" maxlength="8000">${esc(notes)}</textarea>
        <div class="foot" style="margin:.6rem 0 1.2rem">
          <button class="btn" id="stSave">Save note</button>
          <span id="stSaveMsg" class="muted">${data.notes && data.notes.conflict
            ? 'Canvas has a newer copy — reload before saving over it.'
            : (data.notes && data.notes.written_at
              ? ('last written ' + esc(data.notes.written_at)) : '')}</span>
        </div>
      </section>
    </div>`;

  const nickInput = $('#stNick');
  const nickPreview = $('#stNickPreview');
  const paintNick = () => {
    if (!nickPreview) return;
    const next = formatNick(data.name || '', (nickInput && nickInput.value) || '');
    nickPreview.textContent = next && next !== (data.name || '')
      ? ('Shows as ' + next) : 'No nickname. The Canvas name is used as it is.';
  };
  paintNick();
  if (nickInput) nickInput.oninput = paintNick;
  const nickSave = $('#stNickSave');
  if (nickSave) nickSave.onclick = async () => {
    nickSave.disabled = true;
    try {
      const saved = await saveNickname(userId, (nickInput && nickInput.value) || '');
      data.nickname = saved.nickname || '';
      data.display_name = saved.display_name || formatNick(data.name || '', data.nickname);
      $('#areaTitle').textContent = data.display_name || data.name || ('Student ' + userId);
      document.title = ($('#areaTitle').textContent) + ' · CourseForge Studio';
      if (nickPreview) nickPreview.textContent = data.nickname
        ? ('Saved. Shows as ' + (data.display_name || data.name))
        : 'Cleared. The Canvas name is used as it is.';
      setStatus(data.nickname ? 'nickname saved' : 'nickname cleared', 'ok');
    } catch (err) {
      if (nickPreview) nickPreview.textContent = err.message;
    } finally {
      nickSave.disabled = false;
    }
  };

  const save = $('#stSave');
  if (save) save.onclick = async () => {
    const msg = $('#stSaveMsg');
    try {
      const r = await api('/student/' + encodeURIComponent(userId) + '/notes', {
        body: { notes: ($('#stNotes') || {}).value || '' },
      });
      if (msg) msg.textContent = (r && (r.did === 'sent' || r.state === 'sent' || r.did === 'in_sync'))
        ? 'saved to your Canvas files'
        : 'saved';
    } catch (err) {
      if (msg) msg.textContent = err.message;
    }
  };

  paintStudentLive(userId);
  paintStudentRisk(userId);
}

function paintStudentRisk(userId) {
  const sec = $('#stRiskSec');
  const host = $('#stRiskBody');
  if (!sec || !host) return;
  const row = riskById[String(userId)];
  if (!row) {
    api('/students/risk').then(data => {
      applyRisk(data);
      const again = riskById[String(userId)];
      if (!again) return;
      paintStudentRisk(userId);
    }).catch(() => {});
    return;
  }
  sec.hidden = false;
  const trend = row.trend == null ? 'First check, so there is no trend yet.'
    : (row.trend > 0 ? `Up ${row.trend} since the last check.`
      : row.trend < 0 ? `Down ${Math.abs(row.trend)} since the last check.`
      : 'Same as the last check.');
  const attend = row.attend == null ? 0 : row.attend;
  const attendTrend = row.attend_trend == null ? ''
    : (row.attend_trend > 0 ? `Up ${row.attend_trend} since the last check.`
      : row.attend_trend < 0 ? `Down ${Math.abs(row.attend_trend)} since the last check.`
      : 'Same as the last check.');
  host.innerHTML = `<p><span class="stKey">Work</span>
      <span class="stRisk stRisk-${esc(row.band)}">${row.score}</span>
      <span class="stHint">grades and missing work. ${esc(trend)}</span></p>
    <p><span class="stKey">Attend</span>
      <span class="stRisk stRisk-${esc(row.attend_band || 'low')}">${attend}</span>
      <span class="stHint">absences; tardies count less. ${esc(attendTrend)}</span></p>
    <ul class="stCourses">${(row.courses || []).map(c => {
      const move = c.trend == null ? '' : (c.trend > 0 ? ` +${c.trend}` : ` ${c.trend}`);
      const attendMove = c.attend_trend == null ? ''
        : (c.attend_trend > 0 ? ` +${c.attend_trend}` : ` ${c.attend_trend}`);
      const attendN = c.attend == null ? 0 : c.attend;
      const why = (c.reasons || []).concat(c.attend_reasons || []);
      return `<li><b>${esc(c.name)}</b>
        <span class="stKey">work</span> <span class="stRisk stRisk-${esc(c.band)}">${c.score}${esc(move)}</span>
        <span class="stKey">attend</span> <span class="stRisk stRisk-${esc(c.attend_band || 'low')}">${attendN}${esc(attendMove)}</span>
        <div>${esc(why.join('; ') || 'Nothing standing out.')}</div></li>`;
    }).join('')}</ul>`;
}

/* Stays on this page. The old job dialog walked every course ever taught
   and covered the record while it counted. */
async function paintStudentLive(userId) {
  const box = $('#stLive');
  const extras = $('#stExtras');
  const paint = (live) => {
    if (!box || !extras || !live) return;
    const quizzes = live.quiz_extras || [];
    const overs = live.extensions || [];
    const where = live.term ? `this term (${live.term})` : 'this term';
    const n = live.courses_checked;
    box.textContent = (quizzes.length || overs.length)
      ? `${quizzes.length} quiz extra(s), ${overs.length} deadline override(s) · ${where}`
      : (n === 0 ? `No courses ${where}.` : `Nothing extra ${where}.`);
    const blocks = [];
    if (quizzes.length) {
      blocks.push('<h4>Quiz extras</h4><ul class="stCourses">' + quizzes.map(x =>
        `<li>${esc(x.course)} · ${esc(x.title)} · extra ${esc(x.extra_time || x.extra_minutes || 0)} min</li>`
      ).join('') + '</ul>');
    }
    if (overs.length) {
      blocks.push('<h4>Extensions</h4><ul class="stCourses">' + overs.map(x =>
        `<li>${esc(x.course)} · <a href="#/c/${esc(x.course_id)}/a/${esc(x.assignment_id)}">${
          esc(x.title)}</a> · due ${esc(x.due_at || '—')}</li>`
      ).join('') + '</ul>');
    }
    extras.innerHTML = blocks.join('')
      || '<p class="muted">No quiz extras or deadline overrides in this term.</p>';
  };
  try {
    const started = await api('/student/' + encodeURIComponent(userId) + '/live',
      { method: 'POST', body: {} });
    const id = started.job;
    for (;;) {
      const info = await api('/jobs/' + id);
      if (box && info.message) box.textContent = info.message;
      if (info.state === 'done') { paint(info.result || {}); return; }
      if (info.state === 'error') {
        if (box) box.textContent = info.error || 'Could not read extras. The rest of the page is still good.';
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  } catch (_) {
    if (box) box.textContent = 'Could not read extras. The rest of the page is still good.';
  }
}

window.openStudent = openStudent;
window.openStudents = openStudents;
