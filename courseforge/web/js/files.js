/* ADA file compliance: the PDFs, decks and Word documents across several
   courses at once.

   The per-course screens fix one course. This asks the question an instructor
   actually has at the start of a term -- "which of my handouts are a problem?"
   -- and then drives the same per-course pipelines over the answer.

   Three steps, in the order a person would take them:
     Survey  read-only, one file listing per course. What is out there.
     Scan    download a copy of everything and look at it. Repairs PDFs on this
             machine. Still uploads nothing.
     Upload  put the fixed files back. The only step that changes Canvas, and
             it asks once for the whole run. */
(function () {
  'use strict';
  window.Studio = window.Studio || {};

  const KINDS = [
    { id: 'pdf', label: 'PDFs', hint: 'Tagged reading order, a text layer on scans, title and language.' },
    { id: 'pptx', label: 'PowerPoint', hint: 'Alt text on pictures, a title on every slide, header rows on tables.' },
    { id: 'docx', label: 'Word', hint: 'Alt text on pictures, real headings, header rows on tables.' },
  ];

  function st() {
    S.files = S.files || {};
    if (!S.files.kinds) S.files.kinds = KINDS.map(k => k.id);
    if (S.files.picked == null) S.files.picked = [];
    return S.files;
  }

  function kindCard(k, on) {
    return '<label class="a11yCourse fileKind' + (on ? ' on' : '') + '">'
      + '<input type="checkbox" class="fileKind__box" value="' + k.id + '"' + (on ? ' checked' : '') + '>'
      + '<span class="a11yCourseBody"><b>' + esc(k.label) + '</b>'
      + '<span class="hint">' + esc(k.hint) + '</span></span></label>';
  }

  async function openFileCompliance() {
    showView('area');
    // The same zone as the rest of accessibility: this is that area working
    // across courses rather than inside one, and the route names neither.
    document.body.dataset.area = 'a11y';
    crumbs([{ label: 'Courses', href: '#/' }, { label: 'ADA file compliance' }]);
    $('#headerActions').innerHTML = '';
    areaHead('ADA file compliance',
      'The PDFs, slide decks and Word documents in several courses at once. '
      + 'Nothing is uploaded until you say so.');
    if (typeof areaTabs === 'function') areaTabs([], null, null);
    const body = $('#areaBody');
    body.innerHTML = '<div class="hint">Loading your courses...</div>';

    const state = st();
    let courses = [];
    try {
      const got = (S.courses && S.courses.length) ? S.courses : await api('/courses');
      courses = (Array.isArray(got) ? got : (got && got.courses) || []).filter(c => !c.excluded);
    } catch (err) {
      body.innerHTML = '<div class="callout">Could not load the course list: ' + esc(err.message) + '</div>';
      return;
    }
    if (!S.termInfo) S.termInfo = await api('/terms').catch(() => ({ terms: [] }));
    if (state.term == null) state.term = (S.termInfo && S.termInfo.default) || '__all';
    const last = await api('/batch/files').catch(() => null);

    const picked = new Set((state.picked || []).map(String));
    const shown = state.term === '__all' ? courses
      : courses.filter(c => (c.term_label || '') === state.term);
    const shownIds = new Set(shown.map(c => String(c.id)));
    const hidden = courses.filter(c => picked.has(String(c.id)) && !shownIds.has(String(c.id)));
    const termOpts = ((S.termInfo && S.termInfo.terms) || []).map(t =>
      '<option value="' + esc(t.label) + '"' + (t.label === state.term ? ' selected' : '') + '>'
      + esc(t.label) + ' (' + t.count + ')</option>').join('');

    body.innerHTML = ''
      + '<div class="a11yBatch">'
      + '<section><div class="a11yCourseHead"><h3 id="fcCoursesH">Courses</h3>'
      + '<label class="srOnly" for="fcTerm">Term</label>'
      + '<select class="termSel" id="fcTerm">' + termOpts
      + '<option value="__all"' + (state.term === '__all' ? ' selected' : '') + '>All terms ('
      + courses.length + ')</option></select>'
      + '<span class="hint">' + shown.length + ' course' + (shown.length === 1 ? '' : 's')
      + ' &middot; <span id="fcPicked">' + picked.size + '</span> picked</span>'
      + '<span class="spacer"></span>'
      + '<button class="btn sm" id="fcAll" type="button">Select all shown</button>'
      + '<button class="btn sm" id="fcNone" type="button">Clear</button></div>'
      + '<div class="a11yCourseList" role="group" aria-labelledby="fcCoursesH">'
      + (shown.length
        ? shown.map(c => Studio.courseCard(c, picked.has(String(c.id)), state.term === '__all')).join('')
        : '<div class="a11yNone">No courses in ' + esc(state.term) + '.</div>')
      + '</div>'
      + (hidden.length
        ? '<div class="callout a11yWarn">' + hidden.length + ' picked course'
          + (hidden.length === 1 ? ' is' : 's are') + ' in another term and not listed here. '
          + 'They are still part of the run. <button class="btn sm" id="fcDrop" type="button">Drop them</button></div>'
        : '')
      + '<div class="a11yCourseFoot">'
      + (state.term === '__all' ? '<span class="hint">Showing every term.</span>'
        : '<button class="btn" id="fcShowAll" type="button">Show all terms ('
          + courses.length + ' courses)</button>')
      + '</div></section>'

      + '<section><h3 id="fcKindsH">Files</h3>'
      + '<div class="a11yCourseList" role="group" aria-labelledby="fcKindsH">'
      + KINDS.map(k => kindCard(k, state.kinds.indexOf(k.id) >= 0)).join('')
      + '</div></section>'

      + '<section><div class="a11yVerbs">'
      + '<button class="btn" id="fcSurvey">Survey</button>'
      + '<button class="btn primary" id="fcScan">Scan and repair</button>'
      + '<button class="btn ai" id="fcDescribe" title="Write real descriptions for the '
      + 'pictures the repair could only stub. Uploads nothing.">Describe images with Claude</button>'
      + '<button class="btn danger" id="fcPush">Upload the fixed files</button>'
      + '<span class="hint">Survey, Scan and Describe never change Canvas. Repairs and '
      + 'descriptions are written on this computer and stay here until you upload them.</span>'
      + '</div><div id="fcResult"></div></section></div>';

    const pickedIds = () => Array.from(new Set(
      Array.from(body.querySelectorAll('.a11yPick:checked')).map(el => el.value)
        .concat(hidden.map(c => String(c.id)))));
    const kindIds = () => Array.from(body.querySelectorAll('.fileKind__box:checked')).map(el => el.value);
    const remember = () => {
      state.picked = pickedIds();
      const n = $('#fcPicked');
      if (n) n.textContent = String(state.picked.length);
      body.querySelectorAll('.a11yCourse').forEach(lb => {
        const box = lb.querySelector('.a11yPick, .fileKind__box');
        lb.classList.toggle('on', !!(box && box.checked));
      });
      state.kinds = kindIds();
    };
    body.querySelectorAll('.a11yPick, .fileKind__box').forEach(el => { el.onchange = remember; });
    $('#fcTerm').onchange = ev => { state.term = ev.target.value; openFileCompliance(); };
    const showAll = $('#fcShowAll');
    if (showAll) showAll.onclick = () => { state.term = '__all'; openFileCompliance(); };
    const drop = $('#fcDrop');
    if (drop) drop.onclick = () => {
      const gone = new Set(hidden.map(c => String(c.id)));
      state.picked = (state.picked || []).filter(id => !gone.has(String(id)));
      openFileCompliance();
    };
    $('#fcAll').onclick = () => {
      body.querySelectorAll('.a11yPick').forEach(el => { el.checked = true; });
      remember();
    };
    $('#fcNone').onclick = () => {
      body.querySelectorAll('.a11yPick').forEach(el => { el.checked = false; });
      state.picked = hidden.map(c => String(c.id));
      remember();
    };

    const result = $('#fcResult');
    if (last && (last.rows || []).length) renderFiles(result, last, true);

    const ask = () => {
      const ids = pickedIds(), kinds = kindIds();
      if (!ids.length) { setStatus('pick at least one course', 'err'); return null; }
      if (!kinds.length) { setStatus('pick at least one kind of file', 'err'); return null; }
      return { course_ids: ids, kinds };
    };
    $('#fcSurvey').onclick = () => {
      const b = ask(); if (!b) return;
      runJob('Survey: what is in these courses',
        () => api('/batch/files/survey', { body: b }), r => renderFiles(result, r, false));
    };
    $('#fcScan').onclick = () => {
      const b = ask(); if (!b) return;
      runJob('Scan and repair (nothing is uploaded)',
        () => api('/batch/files/scan', { body: b }), r => renderFiles(result, r, false));
    };
    $('#fcDescribe').onclick = () => {
      const b = ask(); if (!b) return;
      runJob('Describing pictures (nothing is uploaded)',
        () => api('/batch/files/describe', { body: b }), r => renderFiles(result, r, false));
    };
    const upload = b => runJobConfirmed('Upload the fixed files',
      token => api('/batch/files/push', { body: Object.assign({}, b, { apply: true, confirm: token }) }),
      r => renderFiles(result, r, false),
      { title: 'Upload the fixed files to these courses?' });
    $('#fcPush').onclick = () => {
      const b = ask(); if (!b) return;
      /* Repairing gives an undescribed figure a safe placeholder, so a file
         can be structurally valid and still describe nothing. Uploading at
         that point looks like the job is done, which is the worse of the two
         ways to be non-compliant. Say so before the confirm dialog, not in a
         footnote afterwards. The shell's own modal, not the browser's dialog:
         the front end contract rules that dialog out, and it escapes the
         focus trap and cannot be styled like the rest of the tool. */
      const waiting = placeholdersLeft(st().last);
      if (!waiting) { upload(b); return; }
      openModal('<h3>Some pictures are still on a placeholder</h3>'
        + '<p>' + waiting + ' picture' + (waiting === 1 ? '' : 's')
        + ' across these courses still hold a placeholder description rather than a real one. '
        + 'Uploading now gives you files that pass an automated scanner and tell a blind '
        + 'student nothing.</p>'
        + '<p class="hint">Describe images with Claude first, or upload them as they are. '
        + 'Nothing has been sent yet.</p>'
        + '<div class="foot"><span class="spacer"></span>'
        + '<button class="btn" id="fcWarnClose" type="button">Go back</button>'
        + '<button class="btn danger" id="fcWarnGo" type="button">Upload them as they are</button>'
        + '</div>', { label: 'Pictures still on a placeholder' });
      $('#fcWarnClose').onclick = closeModal;
      $('#fcWarnGo').onclick = () => { closeModal(); upload(b); };
    };
  }

  /* How many pictures across the last scan are still on a placeholder. Read
     from whatever the last run reported; zero when nothing has been scanned,
     because a warning about a number nobody has measured is just noise. */
  function placeholdersLeft(last) {
    let n = 0;
    for (const row of ((last && last.rows) || [])) {
      for (const k of Object.values(row.kinds || {})) {
        n += +(k.alt_todo || k.needs_alt || 0) || 0;
      }
    }
    return n;
  }

  const KIND_LABEL = { pdf: 'PDFs', pptx: 'PowerPoint', docx: 'Word' };

  function cell(k, action) {
    if (!k) return '<td class="num">&mdash;</td>';
    if (action === 'describe') {
      // A describe row carries only what was written; reading it as a survey
      // row printed "0" files for every course that had just been described.
      const n = k.described || 0;
      return '<td>' + (n ? '<span class="pill good">' + n + ' described</span>'
        : '<span class="muted">nothing left to describe</span>') + '</td>';
    }
    if (action === 'push') {
      const up = k.uploaded != null ? k.uploaded : k.ready;
      return '<td class="num">' + (up || 0) + (k.failed ? ' <span class="pill warn">'
        + k.failed + ' failed</span>' : '') + '</td>';
    }
    const bits = [String(k.files || 0)];
    if (k.needs_person) bits.push('<span class="pill warn">' + k.needs_person + ' need a person</span>');
    if (k.issues) bits.push('<span class="pill warn">' + k.issues + ' to fix</span>');
    if (k.ready) bits.push('<span class="pill good">' + k.ready + ' ready</span>');
    if (k.uploaded) bits.push('<span class="pill good">' + k.uploaded + ' uploaded</span>');
    return '<td>' + bits.join(' ') + '</td>';
  }

  function renderFiles(host, s, previous) {
    if (!host) return;
    // Whatever ran last is what the upload warning reads its counts from.
    if (s && s.rows && (s.action !== 'describe' || placeholdersLeft(s))) st().last = s;
    const rows = (s && s.rows) || [];
    const kinds = (s && s.kinds) || [];
    const action = (s && s.action) || 'survey';
    if (!rows.length) {
      host.innerHTML = '<div class="a11yNone">Nothing surveyed yet. Survey lists what is in '
        + 'each course and reads nothing else. Nothing is uploaded.</div>';
      return;
    }
    const title = action === 'push' ? (s.applied ? 'Uploaded' : 'Would upload')
      : action === 'scan' ? 'Scanned and repaired on this computer'
        : action === 'describe' ? 'Described on this computer (nothing uploaded)'
          : 'What is in these courses';
    host.innerHTML = '<h3>' + (previous ? 'Last run' : title)
      + ' <span class="hint">' + esc((s.ran_at && typeof fmtDate === 'function' ? fmtDate(s.ran_at) : s.ran_at || '')
        + (action === 'push' && !s.applied ? ' (dry run, nothing written)' : '')) + '</span></h3>'
      + (s.sentence && action === 'push' && !s.applied
        ? '<div class="callout">' + esc(s.sentence) + '</div>' : '')
      + '<div class="a11yTableWrap"><table class="a11yTable">'
      + '<caption class="srOnly">One row per course</caption><thead><tr>'
      + '<th scope="col">Course</th>'
      + kinds.map(k => '<th scope="col">' + esc(KIND_LABEL[k] || k) + '</th>').join('')
      + '<th scope="col">Trouble</th></tr></thead><tbody>'
      + rows.map(r => '<tr><td>' + esc(r.name || r.course) + '</td>'
        + kinds.map(k => cell((r.kinds || {})[k], action)).join('')
        + '<td>' + (r.error ? '<span class="pill warn">' + esc(r.error) + '</span>' : '') + '</td></tr>').join('')
      + '</tbody></table></div>'
      + (action !== 'push'
        ? '<p class="hint">Open a course to work through its files one at a time, or run '
          + 'Scan and repair on the lot. A file the tool will not touch is counted as '
          + 'needing a person and is never changed.</p>' : '');
  }

  window.openFileCompliance = openFileCompliance;
  Object.assign(Studio, { openFileCompliance });
})();
