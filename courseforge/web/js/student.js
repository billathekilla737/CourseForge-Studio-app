/* Search across courses, then one student's record: courses, accommodation,
   extensions, grades, the audit lines, inbox and notes. The note and the
   history copy to Canvas user files. */
'use strict';

function courseCount(s) {
  if (s.course_refs && s.course_refs.length) return s.course_refs.length;
  return (s.courses || []).length;
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
    <b>${esc(s.name || s.user_id)}</b>
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
          </select>
        </label>
      </div>
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
      [s.name, s.sortable_name, s.sis_user_id, s.login_id, (s.courses || []).join(' ')]
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
  load('').catch(err => {
    const note = $('#stSearchNote');
    if (note) note.textContent = err.message;
  });
  $('#stQuery').focus();
}

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

  $('#areaTitle').textContent = data.name || ('Student ' + userId);
  $('#areaHint').textContent = data.note || '';
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
        <div><span class="muted">Canvas id</span> ${esc(data.user_id)}</div>
        ${data.sis_user_id ? `<div><span class="muted">SIS</span> ${esc(data.sis_user_id)}</div>` : ''}
        ${data.login_id ? `<div><span class="muted">Login</span> ${esc(data.login_id)}</div>` : ''}
        <div><span class="muted">On the accommodation list</span> ${data.on_roster ? 'yes' : 'no'}</div>
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
            <div>${esc(h.summary || '')}</div>
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
