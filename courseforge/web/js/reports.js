/* reports.js: the two reports meant for somebody other than you.

   An accessibility score with a before and an after, and a syllabus policy
   check across courses. Both are reads; neither has an Apply. Both print what
   they did not check as loudly as what they did, because a compliance report
   that quietly averages over an assumption is worse than no report. */
(function () {
  'use strict';

  S.reports = S.reports || { tab: 'score', picked: null, score: null, policies: null };
  const mem = () => S.reports;

  const TABS = [
    { id: 'score', label: 'Accessibility score' },
    { id: 'policies', label: 'Syllabus policies' },
  ];

  async function openReports(which) {
    showView('inbox');                       // shares the plain full-width view
    const host = $('#viewInbox');
    mem().tab = TABS.some(t => t.id === which) ? which : mem().tab;
    crumbs([{ label: 'Courses', href: '#/' }, { label: 'Reports' }]);
    $('#headerActions').innerHTML = '';

    host.innerHTML = `<div class="sectionHead">
        <h2>Reports</h2>
        <span class="hint">For somebody other than you. Nothing here changes a course.</span>
      </div>
      <div class="chips rpTabs" id="rpTabs"></div>
      <div id="rpPick" class="rpPick"></div>
      <div id="rpBody"></div>`;

    $('#rpTabs').innerHTML = TABS.map(t =>
      `<button class="chip" type="button" data-tab="${esc(t.id)}"
        aria-pressed="${t.id === mem().tab}">${esc(t.label)}</button>`).join('');
    $('#rpTabs').querySelectorAll('[data-tab]').forEach(b => {
      b.onclick = () => { location.hash = '#/reports/' + b.dataset.tab; };
    });

    await drawPicker();
    draw();
  }

  /* The same course picker both reports use. Defaults to the current term,
     because "every course I have ever taught" is never the question. */
  async function drawPicker() {
    const host = $('#rpPick');
    if (!S.courses || !S.courses.length || !S.termInfo) {
      try {
        const got = await api('/picker');
        S.courses = got.courses || [];
        S.termInfo = got.terms || S.termInfo;
      } catch (_) { S.courses = S.courses || []; }
    }
    // Arriving straight on #/reports, nothing has chosen a term yet, and
    // "every course I have ever taught" is never the question being asked.
    const term = S.term || (S.termInfo && S.termInfo.default) || null;
    const list = S.courses.filter(c => !c.excluded && (!term || c.term_label === term));
    if (!mem().picked) mem().picked = new Set(list.map(c => String(c.id)));
    host.innerHTML = `<div class="rpPickHead">
        <b>${esc(list.length)} course${list.length === 1 ? '' : 's'}</b>
        <span class="hint">${esc(term || 'all terms')}</span>
        <span class="spacer"></span>
        <button class="btn sm" type="button" id="rpAll">All</button>
        <button class="btn sm" type="button" id="rpNone">None</button>
        <button class="btn primary" type="button" id="rpRun">Run the report</button>
      </div>
      <div class="rpCourses">${list.map(c => `
        <label class="rpCourse"><input type="checkbox" value="${esc(c.id)}"
          ${mem().picked.has(String(c.id)) ? 'checked' : ''}>
          <span>${esc(c.title || c.name)}</span></label>`).join('')}</div>`;
    const boxes = () => host.querySelectorAll('input[type=checkbox]');
    host.querySelectorAll('input[type=checkbox]').forEach(b => {
      b.onchange = () => {
        if (b.checked) mem().picked.add(b.value); else mem().picked.delete(b.value);
      };
    });
    $('#rpAll').onclick = () => { boxes().forEach(b => { b.checked = true; mem().picked.add(b.value); }); };
    $('#rpNone').onclick = () => { boxes().forEach(b => { b.checked = false; mem().picked.delete(b.value); }); };
    $('#rpRun').onclick = run;
  }

  function run() {
    const ids = [...mem().picked];
    if (!ids.length) { setStatus('pick at least one course', 'err'); return; }
    const tab = mem().tab;
    const path = tab === 'score' ? '/reports/score' : '/reports/policies';
    runJob(tab === 'score' ? 'Scoring the courses' : 'Reading the syllabi',
      () => api(path, { body: { course_ids: ids } }),
      out => { if (out) { mem()[tab] = out; draw(); } });
  }

  function draw() {
    const host = $('#rpBody');
    if (!host) return;
    const out = mem()[mem().tab];
    if (!out) {
      host.innerHTML = '';
      host.appendChild(emptyState(mem().tab === 'score'
        ? 'Pick the courses and run it. The score is built from files already on '
          + 'this computer, so it makes no Canvas calls at all.'
        : 'Pick the courses and run it. Each syllabus is read once; nothing is changed.'));
      return;
    }
    host.innerHTML = mem().tab === 'score' ? scoreHtml(out) : policyHtml(out);
  }

  /* ------------------------------------------------------------- the score */
  function scoreHtml(out) {
    const band = out.before == null ? '' : `<div class="rpBand">
      <div class="rpBig"><span class="was">${esc(out.before)}</span>
        <span class="arrow">→</span><span class="now">${esc(out.after)}</span></div>
      <p>${esc(out.sentence_done || '')}</p>
    </div>`;
    const rows = (out.courses || []).map(c => `
      <tr>
        <th scope="row">${esc(c.course || c.course_id)}</th>
        <td class="num">${c.before == null ? '<span class="muted">not scanned</span>' : esc(c.before)}</td>
        <td class="num">${c.after == null ? '' : esc(c.after)}</td>
        <td class="num">${c.gain == null ? '' : (c.gain > 0 ? '+' + esc(c.gain) : esc(c.gain))}</td>
        <td class="num">${esc(c.files)}</td>
        <td>${c.not_checked && c.not_checked.length
          ? `<span class="pill warn">${esc(c.not_checked.join(', '))} not scanned</span>` : ''}</td>
      </tr>`).join('');
    return band + `<div class="gwTableWrap"><table class="gwTable">
      <caption class="srOnly">Accessibility score per course</caption>
      <thead><tr><th scope="col">Course</th><th scope="col">Before</th>
        <th scope="col">Now</th><th scope="col">Change</th>
        <th scope="col">Files</th><th scope="col">Not counted</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="callout rpMethod"><b>How this is worked out.</b>
        ${esc(out.method || '')}</div>`;
  }

  /* ---------------------------------------------------------- the policies */
  function policyHtml(out) {
    const heads = (out.by_policy || []).map(p =>
      `<th scope="col" title="${esc(p.why)}">${esc(p.label)}</th>`).join('');
    const rows = (out.courses || []).map(c => {
      const cells = (out.by_policy || []).map(p => {
        const row = (c.rows || []).find(r => r.id === p.id) || {};
        const ok = !!row.present;
        return `<td class="rpCell ${ok ? 'ok' : 'no'}"
          title="${esc(ok ? (row.excerpt || 'found') : (row.why || 'not found'))}">${
          ok ? '✓' : '—'}</td>`;
      }).join('');
      return `<tr><th scope="row">
        <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.course)}</a>
        ${c.empty ? '<span class="pill warn">no syllabus</span>' : ''}</th>${cells}</tr>`;
    }).join('');
    const worst = (out.by_policy || []).filter(p => p.missing).sort((a, b) => b.missing - a.missing);
    return `<div class="rpBand plain"><p>${esc(out.summary || '')}</p></div>
      <div class="gwTableWrap"><table class="gwTable rpMatrix">
      <caption class="srOnly">Which syllabi mention which required statement</caption>
      <thead><tr><th scope="col">Course</th>${heads}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${worst.length ? `<div class="rpGaps"><h3>What to fix first</h3><ul>${worst.map(p =>
        `<li><b>${esc(p.label)}</b> — missing from ${esc(p.missing)} course${
          p.missing === 1 ? '' : 's'}. <span class="muted">${esc(p.why)}</span></li>`).join('')}</ul></div>` : ''}
      <div class="callout rpMethod"><b>What a tick means.</b> ${esc(out.note || '')}</div>`;
  }

  window.openReports = openReports;
  Object.assign(window.Studio || (window.Studio = {}), { openReports });
})();
