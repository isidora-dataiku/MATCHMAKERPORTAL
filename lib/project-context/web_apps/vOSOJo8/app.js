// ── State ────────────────────────────────────────────────────────────────────
const state = {
  status: 'IDLE',       // IDLE | LOADING | RESULTS
  sessionId: null,
  candidateId: null,
  kandidaat: null,
  matches: [],
  decisions: {},        // match_id → { beslissing, reden, toelichting }
  startTimes: {},       // match_id → Date.now() when card was expanded
  fallback: false,
};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const candidateInput = document.getElementById('candidate-input');
const searchBtn      = document.getElementById('search-btn');
const searchError    = document.getElementById('search-error');
const kandidaatKaart = document.getElementById('kandidaat-kaart');
const kandidaatContent = document.getElementById('kandidaat-content');
const idleEl         = document.getElementById('idle-state');
const loadingEl      = document.getElementById('loading-state');
const resultsEl      = document.getElementById('results-state');
const resultsTitle   = document.getElementById('results-title');
const resultsCount   = document.getElementById('results-count');
const matchCards     = document.getElementById('match-cards');
const fallbackBanner = document.getElementById('fallback-banner');
const rejectModal    = document.getElementById('reject-modal');
const modalSubtitle  = document.getElementById('modal-subtitle');
const redenSelect    = document.getElementById('reden-select');
const toelichtingInput = document.getElementById('toelichting-input');
const modalCancelBtn = document.getElementById('modal-cancel-btn');
const modalConfirmBtn= document.getElementById('modal-confirm-btn');

let pendingRejectMatchId = null;

// ── Event listeners ───────────────────────────────────────────────────────────
searchBtn.addEventListener('click', startSearch);
candidateInput.addEventListener('keydown', e => { if (e.key === 'Enter') startSearch(); });
modalCancelBtn.addEventListener('click', closeModal);
modalConfirmBtn.addEventListener('click', confirmReject);

// ── Search ───────────────────────────────────────────────────────────────────
function startSearch() {
  const id = candidateInput.value.trim().toUpperCase();
  if (!id) { showError('Vul een kandidaat-ID in (bijv. C-001)'); return; }
  hideError();
  setStatus('LOADING');

  fetch(getWebAppBackendUrl('api/match'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_id: id }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.error) { showError(data.error); setStatus('IDLE'); return; }
    state.sessionId   = data.session_id;
    state.candidateId = data.candidate_id;
    state.kandidaat   = data.kandidaat || {};
    state.matches     = data.matches   || [];
    state.decisions   = {};
    state.startTimes  = {};
    state.fallback    = !!data.fallback;
    renderKandidaatKaart();
    renderMatches();
    setStatus('RESULTS');
    toggleFallbackBanner(state.fallback);
  })
  .catch(err => { showError('Netwerkfout: ' + err.message); setStatus('IDLE'); });
}

// ── Render kandidaatkaart ─────────────────────────────────────────────────────
function renderKandidaatKaart() {
  const k = state.kandidaat;
  const niveauLabel = k.carriere_niveau || '—';
  const skills = Array.isArray(k.vaardigheden_gestandaardiseerd) ? k.vaardigheden_gestandaardiseerd
    : (typeof k.vaardigheden_gestandaardiseerd === 'string' ? tryParseJSON(k.vaardigheden_gestandaardiseerd) || [] : []);

  let html = `
    <div class="kv-row"><span class="kv-label">ID</span><span class="kv-value">${esc(k.candidate_id||'—')}</span></div>
    <div class="kv-row"><span class="kv-label">Niveau</span><span class="kv-value">${esc(niveauLabel)}</span></div>
    <div class="kv-row"><span class="kv-label">Opleiding</span><span class="kv-value">${esc(k.opleiding_niveau||'—')} — ${esc(k.opleiding_richting||'—')}</span></div>
    <div class="kv-row"><span class="kv-label">Ervaring</span><span class="kv-value">${esc(String(k.werkervaring_jaren||'—'))} jaar</span></div>
    <div class="kv-row"><span class="kv-label">Beschikbaarheid</span><span class="kv-value">${esc(k.beschikbaarheid||'—')}</span></div>
    <div class="kv-row"><span class="kv-label">Mobiliteit</span><span class="kv-value">${esc(String(k.mobiliteitskm||'—'))} km</span></div>
    <div class="kv-row"><span class="kv-label">Gemeente</span><span class="kv-value">${esc(k.gemeente||'—')}</span></div>
  `;
  if (skills.length) {
    html += `<div style="margin-top:8px"><span class="kv-label" style="font-size:12px">Vaardigheden</span><div style="margin-top:4px">`;
    skills.slice(0,12).forEach(s => { html += `<span class="skill-tag">${esc(s)}</span>`; });
    html += `</div></div>`;
  }
  if (k.profiel_samenvatting) {
    html += `<div class="profiel-samenvatting">${esc(k.profiel_samenvatting)}</div>`;
  }

  kandidaatContent.innerHTML = html;
  kandidaatKaart.classList.remove('hidden');
}

// ── Render match cards ────────────────────────────────────────────────────────
function renderMatches() {
  resultsTitle.textContent = `Matches voor ${state.candidateId}`;
  resultsCount.textContent = state.matches.length;
  matchCards.innerHTML = '';

  state.matches.forEach((m, idx) => {
    const score = Math.round((m.match_score || 0) * 100);
    const scoreClass = score >= 70 ? 'high' : score >= 45 ? 'mid' : 'low';
    const matchingSkills = Array.isArray(m.top_matching_skills) ? m.top_matching_skills : tryParseJSON(m.top_matching_skills) || [];
    const missingSkills  = Array.isArray(m.missing_skills)      ? m.missing_skills      : tryParseJSON(m.missing_skills)      || [];

    const card = document.createElement('div');
    card.className = 'match-card';
    card.dataset.matchId = m.match_id;
    card.innerHTML = `
      <div class="match-card-header">
        <div class="match-rank">${idx + 1}</div>
        <div class="match-title">
          <strong>${esc(m.job_titel || m.job_id)}</strong>
          <span>${esc(m.bedrijf_naam || '')} ${m.gemeente ? '· ' + esc(m.gemeente) : ''}</span>
        </div>
        <div class="score-pill ${scoreClass}">${score}%</div>
        <div class="expand-icon">▾</div>
      </div>
      <div class="score-bar-wrap">
        <div class="score-bar-bg"><div class="score-bar-fill" style="width:${score}%"></div></div>
      </div>
      <div class="fit-row">
        ${fitBadge('Vaardigheden', Math.round((m.skill_overlap_pct||0)*100) + '%', score >= 50 ? 'ok' : 'warn')}
        ${fitBadge('Ervaring', m.experience_fit || '?', m.experience_fit === 'match' ? 'ok' : 'warn')}
        ${fitBadge('Opleiding', m.education_fit || '?', m.education_fit === 'match' ? 'ok' : 'warn')}
        ${fitBadge('Locatie', m.location_fit ? '✓' : '✗', m.location_fit ? 'ok' : 'bad')}
        ${fitBadge('Uren', m.hours_fit ? '✓' : '✗', m.hours_fit ? 'ok' : 'bad')}
      </div>
      <div class="match-details">
        <div class="match-summary">${esc(m.match_summary_nl || '')}</div>
        <div class="skills-section">
          ${matchingSkills.length ? `<h4>Overeenkomende vaardigheden</h4><div>${matchingSkills.map(s=>`<span class="skill-tag">${esc(s)}</span>`).join('')}</div>` : ''}
          ${missingSkills.length  ? `<h4>Ontbrekende vaardigheden</h4><div>${missingSkills.map(s=>`<span class="skill-tag" style="background:#fde8e8;color:#c0392b">${esc(s)}</span>`).join('')}</div>` : ''}
        </div>
        <div class="decision-row" id="decision-row-${m.match_id}">
          <button class="btn-success" data-action="accept">✓ Accepteren</button>
          <button class="btn-danger"  data-action="reject">✗ Afwijzen</button>
        </div>
      </div>
    `;

    const header = card.querySelector('.match-card-header');
    header.addEventListener('click', () => toggleCard(card, m.match_id));

    card.querySelector('[data-action="accept"]').addEventListener('click', (e) => { e.stopPropagation(); acceptMatch(m.match_id); });
    card.querySelector('[data-action="reject"]').addEventListener('click', (e) => { e.stopPropagation(); openRejectModal(m.match_id); });

    matchCards.appendChild(card);
  });
}

function fitBadge(label, value, cls) {
  return `<span class="fit-badge ${cls}">${esc(label)}: <strong>${esc(value)}</strong></span>`;
}

function toggleCard(card, matchId) {
  const wasExpanded = card.classList.contains('expanded');
  // Collapse all
  document.querySelectorAll('.match-card.expanded').forEach(c => c.classList.remove('expanded'));
  if (!wasExpanded) {
    card.classList.add('expanded');
    if (!state.startTimes[matchId]) state.startTimes[matchId] = Date.now();
  }
}

// ── Decisions ─────────────────────────────────────────────────────────────────
function acceptMatch(matchId) {
  if (state.decisions[matchId]) return;
  const match = state.matches.find(m => m.match_id === matchId);
  const elapsed = Math.round((Date.now() - (state.startTimes[matchId] || Date.now())) / 1000);

  submitFeedback({
    match_id: matchId,
    session_id: state.sessionId,
    candidate_id: state.candidateId,
    job_id: match.job_id,
    beslissing: 'geaccepteerd',
    afwijzing_reden_code: '',
    afwijzing_toelichting: '',
    match_score_at_time: match.match_score,
    match_summary_at_time: match.match_summary_nl,
    time_to_decide_seconds: elapsed,
  }, matchId, 'geaccepteerd');
}

function openRejectModal(matchId) {
  if (state.decisions[matchId]) return;
  const match = state.matches.find(m => m.match_id === matchId);
  pendingRejectMatchId = matchId;
  modalSubtitle.textContent = `Vacature: ${match.job_id}`;
  redenSelect.value = '';
  toelichtingInput.value = '';
  rejectModal.classList.remove('hidden');
}

function closeModal() {
  rejectModal.classList.add('hidden');
  pendingRejectMatchId = null;
}

function confirmReject() {
  const reden = redenSelect.value;
  if (!reden) { redenSelect.style.borderColor = '#c0392b'; return; }
  redenSelect.style.borderColor = '';

  const matchId = pendingRejectMatchId;
  const match = state.matches.find(m => m.match_id === matchId);
  const elapsed = Math.round((Date.now() - (state.startTimes[matchId] || Date.now())) / 1000);
  closeModal();

  submitFeedback({
    match_id: matchId,
    session_id: state.sessionId,
    candidate_id: state.candidateId,
    job_id: match.job_id,
    beslissing: 'afgewezen',
    afwijzing_reden_code: reden,
    afwijzing_toelichting: toelichtingInput.value.trim(),
    match_score_at_time: match.match_score,
    match_summary_at_time: match.match_summary_nl,
    time_to_decide_seconds: elapsed,
  }, matchId, 'afgewezen');
}

function submitFeedback(payload, matchId, beslissing) {
  const row = document.getElementById('decision-row-' + matchId);
  if (row) row.innerHTML = '<span style="color:#888;font-size:13px">Opslaan...</span>';

  fetch(getWebAppBackendUrl('api/feedback'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(data => {
    state.decisions[matchId] = beslissing;
    if (row) {
      const label = beslissing === 'geaccepteerd' ? 'Geaccepteerd' : 'Afgewezen';
      const cls   = beslissing === 'geaccepteerd' ? 'accepted' : 'rejected';
      row.innerHTML = `<span class="decision-status ${cls}">${label}</span>`;
    }
  })
  .catch(err => {
    if (row) row.innerHTML = `<span style="color:#c0392b;font-size:13px">Fout: ${esc(err.message)}</span>`;
  });
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setStatus(s) {
  state.status = s;
  idleEl.classList.toggle('hidden',    s !== 'IDLE');
  loadingEl.classList.toggle('hidden', s !== 'LOADING');
  resultsEl.classList.toggle('hidden', s !== 'RESULTS');
  searchBtn.disabled = s === 'LOADING';
}

function showError(msg) { searchError.textContent = msg; searchError.classList.remove('hidden'); }
function hideError()    { searchError.classList.add('hidden'); }
function toggleFallbackBanner(show) { fallbackBanner.classList.toggle('hidden', !show); }

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function tryParseJSON(s) { try { return JSON.parse(s); } catch(e) { return null; } }

// ── Init ──────────────────────────────────────────────────────────────────────
setStatus('IDLE');
