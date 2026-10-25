'use strict';
const $ = (id) => document.getElementById(id);
const DESKTOP = document.body.classList.contains('desktop');
const tr = window.AcervoI18n.translate;
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const duration = (v) => v ? `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, '0')}` : 'Duração não informada';
const bytes = (v) => v ? `${(v / 1048576).toFixed(1)} MB` : 'Tamanho não informado';
const time = (v) => new Date(v).toLocaleString(window.AcervoI18n.language === 'en' ? 'en-US' : 'pt-BR');
const activeStates = ['Na fila', 'Baixando', 'Convertendo', 'Cancelando'];
let state = {tracks: [], jobs: [], events: []}, results = [], selected = new Set(), trackSelection = new Set();
let contextTrack = null, pendingDownloads = [], authenticated = false, polling = false, batchRunning = false, stopBatch = false;
let localFiles = [], localUrls = [], localIndex = 0, toastTimer, lastRefresh = 0;
let spotifyWatchUntil = 0;
let preparingTracks = false;
let pendingTrackIds = [];
let moveIds = [], chosenMoveFolder = null;
let quickRunning = false, settingsDirty = false;
let refreshTimer;
const formatJobs = new Map();
let currentView = 'discover';
const PAGE_SIZE = 50, pages = {tracks:0,jobs:0,history:0,events:0,reviews:0,results:0}, rendered = new Map(), trackOrder = new Map();

const tabQueries={library:'',review:'',queue:'',history:'',activity:'',settings:''};
let tabSearchTimer;
function searchText(value) {
  return String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR');
}
function filterItems(name, items) {
  const terms=searchText(tabQueries[name]).trim().split(/\s+/).filter(Boolean);
  if(!terms.length) return items;
  const albums=new Map(state.tracks.map(track=>[track.id,track.album]));
  return items.filter(item=>{
    const text=searchText(name==='activity' ? [item.message,tr(item.message),item.action,item.job_id,time(item.time)].join(' ') : [item.title,item.artist,item.album || albums.get(item.item_id),item.state,tr(item.state),item.output,item.error,tr(item.error),item.created ? time(item.created) : ''].join(' '));
    return terms.every(term=>text.includes(term));
  });
}
function showSearchCount(items) {
  $('tab-search-status').textContent=tabQueries[currentView]?.trim() ? `${items.length} resultado(s) nesta aba. A pesquisa considera todas as páginas.` : '';
}
function applyTabSearch() {
  if(currentView==='discover') return;
  tabQueries[currentView]=$('tab-search-input').value.trim();
  const list={library:'tracks',review:'reviews',queue:'jobs',history:'history',activity:'events'}[currentView];
  if(list) pages[list]=0;
  renderTracks();renderJobs();renderEvents();renderReviews();renderSettings();
  if(DESKTOP) updateDesktop();
}
$('tab-search-form').addEventListener('submit',e=>{e.preventDefault();clearTimeout(tabSearchTimer);applyTabSearch();});
$('tab-search-input').addEventListener('input',()=>{clearTimeout(tabSearchTimer);tabSearchTimer=setTimeout(applyTabSearch,180);});
$('tab-search-clear').addEventListener('click',()=>{clearTimeout(tabSearchTimer);$('tab-search-input').value='';applyTabSearch();$('tab-search-input').focus();});

function setHTML(id, html) {
  if (rendered.get(id) === html) return;
  $(id).innerHTML = html; rendered.set(id, html);
}

function pageItems(id, items) {
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  pages[id] = Math.min(pages[id], totalPages - 1);
  const start = pages[id] * PAGE_SIZE;
  const html = `<button class="button secondary" data-list="${id}" data-page="${pages[id]-1}" ${pages[id] ? '' : 'disabled'}>Anterior</button><span>Mostrando ${start+1}–${Math.min(start+PAGE_SIZE,items.length)} de ${items.length} · Página ${pages[id]+1} de ${totalPages}</span><button class="button secondary" data-list="${id}" data-page="${pages[id]+1}" ${pages[id]+1<totalPages ? '' : 'disabled'}>Próxima</button>`;
  for (const suffix of ['-pager','-pager-bottom']) {
    $(id+suffix).hidden = items.length <= PAGE_SIZE;
    setHTML(id+suffix, html);
  }
  return items.slice(start,start+PAGE_SIZE);
}

function cover(kind, item, className='result-cover') {
  return `<div class="${className}">${icon('disc')}<img class="cover-image" src="/api/covers/${kind}/${encodeURIComponent(item.id)}" alt="Capa de ${esc(item.title)}" width="54" height="54" loading="lazy" decoding="async"></div>`;
}
document.addEventListener('error', e => { if(e.target.matches?.('.cover-image')) e.target.hidden=true; },true);
document.addEventListener('click', e => {
  const button=e.target.closest('[data-list][data-page]');
  if(!button) return;
  pages[button.dataset.list]=Math.max(0,Number(button.dataset.page));
  if(button.dataset.list==='tracks') renderTracks(); else if(button.dataset.list==='events') renderEvents(); else if(button.dataset.list==='reviews') renderReviews(); else if(button.dataset.list==='results') renderResults(); else renderJobs();
  $('view-'+currentView).scrollIntoView({block:'start'});
});

function setUserText(id, value) {
  $(id).setAttribute('data-i18n-skip', '');
  $(id).textContent=value;
}

function toast(message, error = false) {
  $('toast').textContent = message;
  $('toast').className = error ? 'error-toast' : '';
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6500);
}

async function api(path, body) {
  let response;
  try {
    response = await fetch('/api/' + path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
  } catch {
    throw new Error('Sem conexão com o backend. Inicie o serviço ou confira a rede.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    const detail = Array.isArray(data.detail) ? 'Dados inválidos. Confira os campos enviados.' : data.detail;
    throw new Error(detail || `O servidor respondeu HTTP ${response.status}.`);
  }
  return data;
}

function showLogin() {
  authenticated = false;
  if (!$('login-dialog').open) $('login-dialog').showModal();
}

async function refresh(force = false) {
  if(window.acervoBackground) return false;
  if (polling && !force) return;
  while (polling) await new Promise(resolve => setTimeout(resolve, 25));
  polling = true;
  try {
    state = await api('state'+(currentView==='activity' ? '?events_all=true' : ''));
    indexFormatJobs();
    for (const track of state.tracks) if(!trackOrder.has(track.id)) trackOrder.set(track.id,trackOrder.size);
    state.tracks.sort((a,b)=>trackOrder.get(a.id)-trackOrder.get(b.id));
    lastRefresh = Date.now();
    authenticated = true;
    $('connection').classList.add('online');
    $('connection').textContent = DESKTOP ? 'Pronto para usar' : 'Backend conectado';
    $('library-count').textContent = state.tracks.length;
    $('queue-count').textContent = state.jobs.filter(j => activeStates.includes(j.state)).length;
    $('review-count').textContent = reviewTracks().length;
    $('queue-description').textContent = DESKTOP ? `Todas as selecionadas ficam na fila e seguem automaticamente, com até ${state.limits.workers} transferências simultâneas. Pode fechar e reabrir o Acervo para continuar as pendentes. Limite por arquivo: ${state.limits.file_mb} MB e 20 minutos.` : `Até ${state.limits.workers} tarefas por vez, fila de ${state.limits.queue}. Arquivos de até ${state.limits.file_mb} MB disponíveis por ${state.limits.ttl_hours} horas.`;
    renderTracks(); renderJobs(); renderEvents(); renderReviews(); renderSettings();
    if(currentView==='discover' && results.length) renderResults();
    if (DESKTOP) updateDesktop();
    return true;
  } catch (error) {
    $('connection').classList.remove('online');
    $('connection').textContent = 'Sem conexão ou sessão';
    if (!authenticated && !$('login-dialog').open) toast(error.message, true);
    return false;
  } finally { polling = false; scheduleRefresh(); }
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  const active=state.jobs.some(j=>activeStates.includes(j.state)) || batchRunning || state.spotify?.busy || state.cover_task?.running || state.move_task?.running || Date.now()<spotifyWatchUntil;
  const delay=window.acervoBackground || document.hidden ? 60000 : active ? 2000 : 20000;
  refreshTimer=setTimeout(async()=>{
    if(!document.hidden && !window.acervoBackground) await refresh();
    else scheduleRefresh();
  },delay);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden && !window.acervoBackground) refresh(true);});
window.addEventListener('acervo-resume',()=>{window.acervoBackground=false;refresh(true);});
window.addEventListener('acervo-runtime-ready',()=>refresh(true));

function view(name) {
  clearTimeout(tabSearchTimer);
  currentView = name;
  const names = {discover: 'Descobrir', library: 'Minhas curtidas', review:'Revisar músicas', queue: 'Fila de downloads', history: 'Histórico', activity: 'Atividade', settings:'Configurações'};
  document.querySelectorAll('.view').forEach(v => { v.hidden = v.id !== 'view-' + name; });
  document.querySelectorAll('.nav').forEach(n => {
    n.classList.toggle('active', n.dataset.view === name);
    if (n.dataset.view === name) n.setAttribute('aria-current', 'page'); else n.removeAttribute('aria-current');
  });
  $('breadcrumb').innerHTML = `Biblioteca <span>/</span> ${names[name]}`;
  $('tab-search-form').hidden=name==='discover';
  $('tab-search-status').hidden=name==='discover';
  $('tab-search-input').value=tabQueries[name] || '';
  $('tab-search-label').textContent=`Pesquisar em ${tr(names[name])}`;
  $('tab-search-input').placeholder=name==='activity' ? 'Mensagem, data ou tarefa' : 'Música, artista, álbum ou estado';
  if(name==='activity') refresh(true);
  renderTracks(); renderJobs(); renderEvents(); renderReviews(); renderSettings();
  window.scrollTo(0,0);
}

function badge(value) {
  const type = ['Encontrada', 'Concluída'].includes(value) ? 'success' : ['Erro', 'Não encontrada'].includes(value) ? 'error' : 'warning';
  return `<span class="badge ${type}">${esc(value)}</span>`;
}

function empty(message, subtitle = '') {
  return `<div class="empty">${icon('disc')}<h3>${esc(message)}</h3><p>${esc(subtitle)}</p></div>`;
}

function selectionChanged() {
  $('selection-count').textContent = selected.size;
  $('download-selected').disabled = !selected.size;
  for(const id of ['selected-mp3','selected-mp4']) $(id).disabled=!selected.size;
  $('select-all-results').checked=!!results.length && selected.size===results.length;
  $('select-all-results').indeterminate=selected.size>0 && selected.size<results.length;
  $('select-all-results').disabled=!results.length;
  $('results-summary').textContent=results.length ? `${results.length} resultado(s) — ${selected.size} selecionado(s). MP3 e MP4 entram diretamente na fila.` : '';
}

function indexFormatJobs() {
  formatJobs.clear();
  const rank=job=>activeStates.includes(job.state) ? 3 : job.state==='Concluída' && job.ready ? 2 : 1;
  for(const job of state.jobs) for(const [kind,id] of [['track',job.item_id],['media',job.media_id]]) {
    if(!id) continue;
    const key=`${kind}:${id}:${job.output}`,previous=formatJobs.get(key);
    if(!previous || rank(job)>rank(previous)) formatJobs.set(key,job);
  }
}

function formatControl(kind, item, output, reviewing=false) {
  const label=output.toUpperCase(),job=formatJobs.get(`${kind}:${item.id}:${output}`);
  if(job && activeStates.includes(job.state)) {
    const percent=job.progress==null ? null : Math.max(0,Math.min(99,Math.round(job.progress)));
    const detail=job.state==='Na fila' ? tr('Na fila') : job.state==='Cancelando' ? tr('Cancelando') : percent==null ? tr(job.state) : `${tr(job.state)}: ${percent}%`;
    return `<button class="format-control format-progress ${percent==null ? 'indeterminate' : ''}" data-cancel="${esc(job.id)}" aria-label="Cancelar ${label} de ${esc(item.title)} — ${esc(detail)}" title="${esc(detail)} — clique no X para cancelar" ${job.state==='Cancelando' ? 'disabled' : ''}><svg class="progress-ring" viewBox="0 0 44 44" aria-hidden="true"><circle class="ring-track" cx="22" cy="22" r="19"/><circle class="ring-value" cx="22" cy="22" r="19" pathLength="100" stroke-dasharray="${percent==null ? '25 75' : percent+' 100'}" transform="rotate(-90 22 22)"/></svg>${icon('close')}<small>${label}</small></button>`;
  }
  if(job?.state==='Concluída' && job.ready) return `<button class="format-control format-ready" data-play-saved="${esc(job.id)}" title="Abrir ${label} salvo" aria-label="Abrir ${label} de ${esc(item.title)}">${icon('check')}<small>${label}</small></button>`;
  const action=kind==='track' ? `data-library-track="${esc(item.id)}"` : `data-quick-media="${esc(item.id)}" ${reviewing ? 'data-review-media' : ''}`;
  return `<button class="button ${output==='mp3' ? 'primary' : 'secondary'} format-choice" ${action} data-output="${output}" aria-label="Baixar ${label} de ${esc(item.title)}">${icon('download')}${label}</button>`;
}

function noReviewButton(track) {
  if(!DESKTOP) return '';
  const output=$('no-review-format').value,job=formatJobs.get(`track:${track.id}:${output}`);
  return `<button class="button secondary" data-no-review="${esc(track.id)}" ${job && activeStates.includes(job.state) ? 'disabled' : ''}>Baixar sem revisar (${output.toUpperCase()})</button>`;
}

function resultCards(items, reviewing=false) {
  return items.map(m => {
    const formats = m.formats?.length ? [...new Set(m.formats.map(f => f.ext.toUpperCase()))].join(' / ') : 'Formatos após análise';
    const origin = m.url?.startsWith('https://') ? `<a href="${esc(m.url)}" target="_blank" rel="noreferrer" class="text-link">Abrir origem</a>` : '';
    const buttons=DESKTOP ? formatControl('media',m,'mp3',reviewing)+formatControl('media',m,'mp4',reviewing)+`<button class="icon-button" data-change-folder aria-label="Trocar pasta dos próximos downloads" title="Trocar pasta">${icon('folder')}</button>` : `<button class="button secondary" data-download="${esc(m.id)}">${icon('download')}Baixar</button>`;
    return `<article class="result">${reviewing ? '' : `<input type="checkbox" class="result-check" value="${esc(m.id)}" aria-label="Selecionar ${esc(m.title)}" ${selected.has(m.id) ? 'checked' : ''}>`}${cover('media',m)}<div class="result-info"><strong data-i18n-skip>${esc(m.title)}</strong><p data-i18n-skip>${esc(m.artist)}${m.album ? ' — ' + esc(m.album) : ''}</p><div class="result-meta"><span class="badge">${esc(m.source)}</span><span>${esc(duration(m.duration))}</span><span>${esc(formats)}</span>${origin}${m.match ? badge(m.match.confident ? 'Encontrada' : 'Precisa revisar') : ''}</div>${m.license ? `<p>Licença informada pela fonte: ${esc(m.license)}</p>` : ''}${m.match ? `<p>${esc(m.match.score)}% de similaridade — ${esc(tr(m.match.reason))}</p>` : ''}</div><div class="result-actions">${reviewing ? `<button class="button secondary" data-confirm="${esc(m.id)}">Confirmar gravação</button>` : ''}${buttons}${DESKTOP && !reviewing ? `<button class="icon-button" data-download="${esc(m.id)}" title="Outros formatos, incluindo áudio original" aria-label="Outros formatos">${icon('settings')}</button>` : ''}</div></article>`;
  }).join('');
}

function renderResults() {
  $('search-context').hidden=true;
  const shown=pageItems('results',results);
  setHTML('results',results.length ? resultCards(shown) : empty('Nenhum arquivo encontrado.', 'Tente outro título, artista ou fonte. Nem toda música terá uma correspondência disponível.'));
  selectionChanged();
}

function reviewTracks() {
  return state.tracks.filter(t=>t.state==='Precisa revisar' && t.candidate_count || ['Não encontrada','Erro'].includes(t.state));
}

function renderReviews() {
  if(currentView!=='review') return;
  const filtered=filterItems('review',reviewTracks());showSearchCount(filtered);
  $('review-summary').textContent=`${filtered.length} música(s) para conferir. Confirme apenas a gravação que você reconhece.`;
  setHTML('reviews', filtered.length ? pageItems('reviews',filtered).map(t=>`<article class="track">${cover('track',t)}<div class="result-info"><strong data-i18n-skip>${esc(t.title)}</strong><p data-i18n-skip>${esc(t.artist)}${t.album ? ' — '+esc(t.album) : ''}</p><div class="result-meta">${badge(t.state)}<span>${esc(duration(t.duration))}</span><span>${t.candidate_count} candidato(s)</span></div></div><div class="result-actions">${t.candidate_count ? `<button class="button primary" data-review="${esc(t.id)}">Comparar</button>` : ''}<button class="button secondary" data-search-track="${esc(t.id)}">${icon('search')}Buscar novamente</button>${noReviewButton(t)}</div></article>`).join('') : (pageItems('reviews',[]),empty('Nenhuma música pendente nesta pesquisa','As gravações duvidosas encontradas pelas buscas aparecem aqui.')));
  $('review-detail').hidden=!contextTrack;
  if(contextTrack) {
    $('review-original').textContent=`Sua lista: ${contextTrack.artist} — ${contextTrack.title} — ${duration(contextTrack.duration)}${contextTrack.album ? ' — '+tr('Álbum')+': '+contextTrack.album : ''} — ${tr(contextTrack.state)}`;
    setHTML('review-candidates',contextTrack.candidates?.length ? resultCards(contextTrack.candidates,true) : empty('Nenhum candidato disponível','Tente buscar novamente.'));
  }
}

function renderSettings() {
  if(currentView!=='settings') return;
  $('settings-folder').textContent=state.output_folder || 'Escolha uma pasta no aplicativo Windows';
  if(!settingsDirty) document.querySelector(`input[name="folder-mode"][value="${state.folder_mode || 'same'}"]`).checked=true;
  if(!settingsDirty) $('background-enabled').checked=state.background_enabled!==false;
  if(!settingsDirty) $('language').value=state.language || 'en';
  $('background-status').textContent=state.desktop_runtime?.available ? 'Bandeja pronta. O X recolhe a janela quando a opção está marcada. Os downloads continuam.' : 'Bandeja ainda indisponível. Enquanto isso, fechar a janela encerra o Acervo.';
  $('hide-window').disabled=!state.desktop_runtime?.available;
  const terms=searchText(tabQueries.settings).split(/\s+/).filter(Boolean);
  const panels=[...document.querySelectorAll('#view-settings .settings-panel')];
  for(const panel of panels) panel.hidden=!terms.every(term=>searchText(panel.textContent).includes(term));
  showSearchCount(panels.filter(p=>!p.hidden));
}

async function busy(button, label, action) {
  const old = button.innerHTML;
  button.disabled = true; button.textContent = label;
  try { return await action(); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = old; }
}

async function searchTrack(track, show = true) {
  const data = await api('search', {title: track.title, artist: track.artist, source: $('library-source').value, item_id: track.id});
  await refresh(true);
  if (show) {
    contextTrack = state.tracks.find(t => t.id === track.id) || track;
    contextTrack={...contextTrack,candidates:data.results};view('review');renderReviews();
    $('review-detail').scrollIntoView({block:'start'});
  }
  if (data.warnings?.length) toast(data.warnings.join(' '), true);
}

function renderTracks() {
  if(currentView !== 'library') return;
  const filtered=filterItems('library',state.tracks);
  showSearchCount(filtered);
  const shown=pageItems('tracks',filtered);
  const html = filtered.length ? shown.map(t => `<article class="track"><input type="checkbox" class="track-check" value="${esc(t.id)}" aria-label="Selecionar ${esc(t.title)}" ${trackSelection.has(t.id) ? 'checked' : ''}>${cover('track',t)}<div class="result-info"><strong data-i18n-skip>${esc(t.title)}</strong><p data-i18n-skip>${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</p><div class="result-meta">${badge(t.state)}<span>${esc(duration(t.duration))}</span></div></div><div class="result-actions"><button class="button secondary" data-search-track="${esc(t.id)}" ${batchRunning || t.state === 'Na fila' ? 'disabled' : ''}>${icon('search')}Buscar</button>${t.candidate_count ? `<button class="button secondary" data-review="${esc(t.id)}" ${t.state === 'Na fila' ? 'disabled' : ''}>Revisar</button>` : ''}${DESKTOP ? formatControl('track',t,'mp3')+formatControl('track',t,'mp4')+noReviewButton(t) : t.state === 'Encontrada' ? `<button class="icon-button" data-download-track="${esc(t.id)}" aria-label="Baixar ${esc(t.title)}">${icon('download')}</button>` : ''}</div></article>`).join('') : empty('Suas favoritas começam aqui.', 'Importe uma lista para pesquisar. Nenhuma conta Spotify é conectada nesta versão.');
  setHTML('tracks',!filtered.length && tabQueries.library ? empty('Nenhuma música encontrada nesta pesquisa.','Tente outro título, artista, álbum ou estado.') : html);
  updateLibraryControls();
}

function updateLibraryControls() {
  const matches=filterItems('library',state.tracks);
  const selectedMatches=matches.filter(t=>trackSelection.has(t.id)).length;
  $('select-all-tracks').checked=!!matches.length && selectedMatches===matches.length;
  $('select-all-tracks').indeterminate=selectedMatches>0 && selectedMatches<matches.length;
  $('select-all-tracks').disabled=!matches.length;
  $('track-select-label').textContent=tabQueries.library ? `Selecionar resultados (${matches.length})` : 'Selecionar todas';
  const chosen = state.tracks.filter(t => trackSelection.has(t.id));
  const found = chosen.filter(t => t.state === 'Encontrada' && t.media_id).length;
  const unsearched = chosen.filter(t => t.state === 'Precisa revisar' && !t.candidate_count).length;
  const review = chosen.filter(t => t.state === 'Precisa revisar' && t.candidate_count).length;
  $('selection-summary').textContent = chosen.length ? `${chosen.length} selecionada(s) · ${found} pronta(s) · ${unsearched} ainda sem busca · ${review} para revisar. ${DESKTOP ? 'Envie toda a seleção de uma vez; a busca e os downloads seguem pela fila.' : 'Prepare até 20 por lote.'}` : 'Selecione músicas e clique em MP3 ou MP4. A busca é feita na fonte escolhida; gravações duvidosas precisam de revisão.';
  if(tabQueries.library && chosen.length>selectedMatches) $('selection-summary').textContent+=` ${chosen.length-selectedMatches} selecionada(s) fora desta pesquisa.`;
  $('download-tracks').disabled = !chosen.length || batchRunning || preparingTracks;
  for(const id of ['library-mp3','library-mp4']) $(id).disabled=!chosen.length || batchRunning || preparingTracks;
  $('search-tracks').disabled = !chosen.length || batchRunning || preparingTracks;
  $('download-no-review-selected').disabled=!chosen.length || quickRunning;
  $('download-no-review-all').disabled=!state.tracks.length || quickRunning;
}

async function reviewTrack(identifier) {
  contextTrack = await api(`library/${identifier}`);
  view('review');renderReviews();$('review-detail').scrollIntoView({block:'start'});
}

async function prepareSelectedTracks() {
  if (preparingTracks || batchRunning) return;
  if (DESKTOP) {
    pendingTrackIds = state.tracks.filter(t => trackSelection.has(t.id)).map(t => t.id);
    if (!pendingTrackIds.length) return toast('Selecione as músicas que deseja baixar.', true);
    $('batch-count').textContent = `${pendingTrackIds.length} música(s) selecionada(s). Todas serão processadas pela fila, sem precisar repetir lotes.`;
    $('batch-folder').textContent = state.output_folder || 'Escolha a pasta';
    $('batch-source').value = $('library-source').value;
    $('batch-authorized').checked = false;
    $('batch-error').textContent = '';
    $('batch-dialog').showModal();
    return;
  }
  const chosenIds = new Set(trackSelection);
  let chosen = state.tracks.filter(t => chosenIds.has(t.id));
  if (!chosen.length) return toast('Selecione as músicas que deseja baixar.', true);
  const free = Math.min(20, state.limits.queue - state.jobs.filter(j => activeStates.includes(j.state)).length);
  if (free <= 0) return toast('A fila está cheia. Aguarde os downloads terminarem para preparar o próximo lote.', true);
  preparingTracks = true;
  updateLibraryControls();
  try {
    let found = chosen.filter(t => t.state === 'Encontrada' && t.media_id);
    if (!found.length) {
      const unsearched = chosen.filter(t => t.state === 'Precisa revisar' && !t.candidate_count);
      const retry = chosen.filter(t => ['Erro','Não encontrada'].includes(t.state) && !t.candidate_count);
      const pending = [...unsearched, ...retry].slice(0, free);
      if (pending.length) {
        $('batch-status').textContent = `Preparando ${pending.length} de ${chosen.length} selecionada(s). A busca acontece antes do download; as restantes ficam para os próximos lotes.`;
        await searchTracks(pending);
        if (stopBatch) return;
        chosen = state.tracks.filter(t => chosenIds.has(t.id));
        found = chosen.filter(t => t.state === 'Encontrada' && t.media_id);
      }
    }
    if (found.length) {
      $('batch-status').textContent = `${Math.min(found.length, free)} música(s) neste lote. Confira os formatos e confirme o download. As que precisam de revisão não foram incluídas.`;
      await prepareTracks(found.slice(0, free));
    } else {
      const review = chosen.find(t => t.candidate_count && t.state === 'Precisa revisar');
      if (review) {
        $('batch-status').textContent = 'A busca encontrou candidatos que precisam de revisão. Clique em Revisar, confirme a gravação e depois prepare o download.';
        await reviewTrack(review.id);
        toast('Escolha a gravação correta para preparar o download. Candidatos duvidosos não entram na fila automaticamente.');
      } else if (chosen.every(t => activeStates.includes(t.state) || t.state === 'Concluída')) {
        view('queue'); toast('As músicas selecionadas já estão na fila ou concluídas.');
      } else {
        $('batch-status').textContent = 'Não houve correspondência pronta neste lote. Confira os estados, tente outra fonte ou pesquise uma música individualmente.';
        toast('Nenhuma correspondência pronta neste lote. Confira a lista e a fonte escolhida.', true);
      }
    }
  } finally {
    preparingTracks = false;
    updateLibraryControls();
  }
}

function renderDesktopJob(j) {
  const active = activeStates.includes(j.state);
  return `<article class="job"><div class="job-main">${cover('job',j)}<div class="result-info"><strong data-i18n-skip>${esc(j.title)}</strong><p><span data-i18n-skip>${esc(j.artist)}</span> · ${j.output === 'mp4' ? 'MP4 · vídeo' : j.output === 'mp3' ? 'MP3' : 'Áudio original'} · ${esc(time(j.created))}</p><div class="result-meta">${badge(j.state)}${j.auto_selected ? badge('Escolha automática') : ''}${j.ready ? `<span>${esc(bytes(j.size))} · Salvo no PC</span>` : j.state === 'Concluída' ? '<span>Arquivo movido ou removido da pasta</span>' : ''}</div></div></div><div class="result-actions">${active ? `<button class="icon-button" data-cancel="${esc(j.id)}" aria-label="Cancelar ${esc(j.title)}" ${j.state === 'Cancelando' ? 'disabled' : ''}>${icon('close')}</button>` : ''}${j.ready ? `<button class="button primary" data-play-saved="${esc(j.id)}">${icon('wave')}${j.output === 'mp4' ? 'Ver vídeo' : 'Ouvir'}</button><button class="button secondary" data-open-folder="${esc(j.id)}">${icon('folder')}Abrir pasta</button><button class="button secondary" data-move-job="${esc(j.id)}">${icon('folder')}Mover</button>` : j.state === 'Precisa revisar' && j.item_id ? `<button class="button secondary" data-review-job="${esc(j.item_id)}">Revisar gravação</button>` : !active ? `<button class="button secondary" data-retry="${esc(j.id)}">${icon('retry')}Tentar novamente</button>` : ''}</div>${active ? `<div class="job-progress"><progress max="100" ${j.progress == null ? '' : `value="${esc(Math.round(j.progress))}"`} aria-label="Progresso de ${esc(j.title)}"></progress><span>${j.state === 'Convertendo' ? 'Convertendo' : j.progress == null ? bytes(j.bytes) : Math.round(j.progress) + '%'}</span></div>` : ''}${j.error ? `<p class="error job-error">${esc(j.error)}</p>` : ''}</article>`;
}

function renderJob(j) {
  if (DESKTOP) return renderDesktopJob(j);
  const active = activeStates.includes(j.state);
  return `<article class="job"><div class="job-main">${cover('job',j)}<div class="result-info"><strong data-i18n-skip>${esc(j.title)}</strong><p><span data-i18n-skip>${esc(j.artist)}</span> · ${j.output === 'mp4' ? 'MP4 · vídeo' : j.output === 'mp3' ? 'MP3' : 'Áudio original'} · ${esc(time(j.created))}</p><div class="result-meta">${badge(j.state)}${j.expired ? '<span>Arquivo temporário expirado</span>' : j.ready ? `<span>${esc(bytes(j.size))}</span>` : ''}</div></div></div><div class="result-actions">${active ? `<button class="icon-button" data-cancel="${esc(j.id)}" aria-label="Cancelar ${esc(j.title)}" ${j.state === 'Cancelando' ? 'disabled' : ''}>${icon('close')}</button>` : ''}${j.ready ? `<a class="button primary" href="/api/jobs/${esc(j.id)}/file" download>${icon('download')}Salvar no aparelho</a>` : j.state === 'Precisa revisar' && j.item_id ? `<button class="button secondary" data-review-job="${esc(j.item_id)}">Revisar gravação</button>` : !active ? `<button class="button secondary" data-retry="${esc(j.id)}">${icon('retry')}Tentar novamente</button>` : ''}</div>${active ? `<div class="job-progress"><progress max="100" ${j.progress == null ? '' : `value="${esc(Math.round(j.progress))}"`} aria-label="Progresso de ${esc(j.title)}"></progress><span>${j.state === 'Convertendo' ? 'Convertendo' : j.progress == null ? bytes(j.bytes) : Math.round(j.progress) + '%'}</span></div>` : ''}${j.error ? `<p class="error job-error">${esc(j.error)}</p>` : ''}</article>`;
}

function renderJobs() {
  if(!['queue','history'].includes(currentView)) return;
  const pending = state.jobs.filter(j => activeStates.includes(j.state)).length;
  $('queue-summary').textContent = `${pending} pendente(s) · ${state.jobs.filter(j => j.state === 'Concluída' && j.ready).length} salvo(s) · ${state.jobs.filter(j => j.state === 'Precisa revisar').length} para revisar · ${state.jobs.filter(j => j.state === 'Erro' || j.state === 'Não encontrada').length} com erro ou não encontrada(s).`;
  $('cancel-all').disabled = !pending;
  $('cancel-all').textContent = 'Cancelar fila inteira';
  if(currentView === 'queue') {
    const active = state.jobs.filter(j => activeStates.includes(j.state) || DESKTOP && new Date(j.created).getTime() > Date.now() - 300000);
    const rank = j => ['Baixando','Convertendo','Cancelando'].includes(j.state) ? 0 : j.state==='Na fila' ? 1 : 2;
    active.sort((a,b)=>rank(a)-rank(b) || a.created.localeCompare(b.created));
    const filtered=filterItems('queue',active);showSearchCount(filtered);
    const shown=pageItems('jobs',filtered);
    setHTML('jobs',filtered.length ? shown.map(renderJob).join('') : tabQueries.queue ? empty('Nenhuma tarefa encontrada nesta pesquisa.') : empty('Tudo tranquilo por aqui.', 'As próximas tarefas aparecerão nesta fila. Os arquivos prontos ficam no Histórico.'));
  } else {
    const history = state.jobs.filter(j => !activeStates.includes(j.state)).sort((a,b)=>b.created.localeCompare(a.created));
    const filtered=filterItems('history',history);showSearchCount(filtered);
    const shown=pageItems('history',filtered);
    setHTML('history',filtered.length ? shown.map(renderJob).join('') : tabQueries.history ? empty('Nenhum registro encontrado nesta pesquisa.') : empty('Seu histórico ainda está vazio.', 'Os resultados de cada tarefa ficam registrados aqui.'));
  }
}

function renderEvents() {
  if(currentView !== 'activity') return;
  const filtered=filterItems('activity',state.events);showSearchCount(filtered);
  const shown=pageItems('events',filtered);
  const html = filtered.length ? shown.map(e => `<article class="event"><time datetime="${esc(e.time)}">${esc(time(e.time))}</time><span>${esc(e.message)}${e.job_id ? `<br><small class="muted">Tarefa ${esc(e.job_id)}</small>` : ''}</span></article>`).join('') : empty('Nenhum evento registrado.');
  setHTML('events',!filtered.length && tabQueries.activity ? empty('Nenhum evento encontrado nesta pesquisa.') : html);
  $('engine-status').textContent = DESKTOP ? `Ferramentas de áudio ${state.engine.ffmpeg && state.engine.ffprobe && state.engine.node ? tr('disponíveis no programa') : tr('incompletas: extraia o pacote inteiro, incluindo a pasta _internal')}. Os horários abaixo seguem o relógio deste computador.` : `FFmpeg: ${state.engine.ffmpeg ? tr('disponível') : tr('não instalado')} · ffprobe: ${state.engine.ffprobe ? tr('disponível') : tr('não instalado')} · Node (YouTube): ${state.engine.node ? tr('disponível') : tr('não instalado')}. A disponibilidade das ferramentas não garante acesso às fontes externas.`;
}

function formatOptions(m) {
  return m.formats.map(f => `<option value="${esc(f.id)}">${esc(f.ext.toUpperCase())}${f.kind === 'video' ? ` · vídeo com áudio · ${esc(f.height)}p` : f.abr ? ' · ~' + Math.round(f.abr) + ' kbps' : ' · áudio, taxa não informada'}${f.size ? ' · ' + bytes(f.size) : ''}</option>`).join('');
}

function outputOptions(m, formatId) {
  const f = m.formats.find(f => f.id === formatId) || m.formats[0];
  if (f.kind === 'video') return '<option value="mp4">MP4 — vídeo com áudio</option><option value="mp3:0">MP3 — extrair áudio, VBR qualidade 0</option>';
  const rates = f.abr ? [32,48,64,96,128,192].filter(b => b <= f.abr).slice(-2) : [128];
  return `<option value="original">Original — ${esc(f.ext.toUpperCase())}</option><option value="mp4">MP4 — criar vídeo com capa e áudio</option>${f.ext !== 'mp3' ? '<option value="mp3:0">MP3 — VBR qualidade 0 (conversão)</option>'+(rates.length ? rates : [32]).map(b => `<option value="mp3:${b}">MP3 — ${b} kbps (conversão)</option>`).join('') : ''}`;
}

async function prepareDownloads(entries) {
  if (!entries.length) return toast('Selecione pelo menos uma música encontrada.', true);
  if (entries.length > state.limits.queue) return toast(`Escolha até ${state.limits.queue} arquivos por vez.`, true);
  toast('Preparando formatos disponíveis…');
  pendingDownloads = [];
  for (const [index, entry] of entries.entries()) {
    if (entry.track) $('batch-status').textContent = `Verificando os formatos de ${index + 1} de ${entries.length}: ${entry.track.title}. O download começa depois da confirmação.`;
    let media = entry.media;
    if (!media.formats?.length) {
      const data = await api('analyze', {url: media.url});
      media = data.results[0];
    }
    if (!media?.formats?.length) throw new Error('A fonte não oferece formatos de áudio disponíveis.');
    media = {...media, formats: media.formats.filter(f => !f.size || f.size <= state.limits.file_mb * 1048576)};
    if (!media.formats.length) throw new Error('Os formatos da fonte ultrapassam o limite de tamanho.');
    const preferred = state.jobs.find(j => j.id === entry.track?.job_id)?.output;
    const formats = [...media.formats].sort((a, b) => (preferred === 'mp4' ? Number(b.kind === 'video') - Number(a.kind === 'video') : 0) || (b.abr || 0) - (a.abr || 0));
    pendingDownloads.push({media: {...media, formats}, track: entry.track, preferred});
  }
  $('download-options').innerHTML = pendingDownloads.map((e, i) => `<div class="download-item" data-index="${i}"><div class="download-fields"><div><label for="dl-artist-${i}">Artista</label><input id="dl-artist-${i}" value="${esc(e.track?.artist || e.media.artist)}" maxlength="200" required></div><div><label for="dl-title-${i}">Música</label><input id="dl-title-${i}" value="${esc(e.track?.title || e.media.title)}" maxlength="200" required></div></div><label for="dl-format-${i}">Formato disponível na fonte · ${esc(tr(e.media.source))}</label><select id="dl-format-${i}" data-format-index="${i}">${formatOptions(e.media)}</select><label for="dl-output-${i}">Arquivo de saída</label><select id="dl-output-${i}">${outputOptions(e.media, e.media.formats[0].id)}</select></div>`).join('');
  pendingDownloads.forEach((entry, index) => {
    const output = $('dl-output-' + index);
    const preferred = [...output.options].filter(option => option.value.split(':')[0] === entry.preferred).pop();
    if (preferred) output.value = preferred.value;
  });
  $('authorized').checked = false; $('download-error').textContent = '';
  $('download-dialog').showModal();
}

async function prepareTracks(tracks) {
  const entries = [];
  const found = tracks.filter(t => t.state === 'Encontrada' && t.media_id);
  if (found.length > state.limits.queue) throw new Error(`Escolha até ${state.limits.queue} arquivos por vez.`);
  for (const summary of found) {
    const t = await api(`library/${summary.id}`);
    const media = t.candidates.find(m => m.id === t.media_id);
    if (media) entries.push({track: t, media});
  }
  return prepareDownloads(entries);
}

document.querySelector('nav').addEventListener('click', e => {
  const button = e.target.closest('[data-view]');
  if (button) view(button.dataset.view);
});
$('login-dialog').addEventListener('cancel', e => e.preventDefault());
$('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = e.submitter; button.disabled = true; $('login-error').textContent = '';
  try {
    await api('login', {password: $('password').value});
    $('password').value = ''; $('login-dialog').close(); authenticated = true;
    await refresh();
  } catch (error) { $('login-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('logout').addEventListener('click', async () => {
  try { await api('logout', {}); state = {tracks: [], jobs: [], events: []}; showLogin(); }
  catch (error) { toast(error.message, true); }
});
$('link-form').addEventListener('submit', e => {
  e.preventDefault();
  busy(e.submitter, 'Analisando…', async () => {
    const data = await api('analyze', {url: $('link-input').value.trim()});
    results = data.results; contextTrack = null; selected.clear(); renderResults(); await refresh();
  });
});
$('search-form').addEventListener('submit', e => {
  e.preventDefault();
  busy(e.submitter, 'Pesquisando…', async () => {
    const data = await api('search', {title: $('song-input').value.trim(), artist: $('artist-input').value.trim(), source: $('source-input').value});
    results = data.results; contextTrack = null; selected.clear(); renderResults();
    if (data.warnings?.length) toast(data.warnings.join(' '), true);
    await refresh();
  });
});
$('results').addEventListener('change', e => {
  if (e.target.matches('.result-check')) {
    if (e.target.checked) selected.add(e.target.value); else selected.delete(e.target.value);
    selectionChanged();
  }
});
  $('results').addEventListener('click', e => {
  const dl = e.target.closest('[data-download]');
  if (dl) busy(dl, 'Analisando…', () => prepareDownloads([{media: results.find(m => m.id === dl.dataset.download)}]));
});
$('download-selected').addEventListener('click', e => busy(e.currentTarget, 'Preparando…', () => prepareDownloads(results.filter(m => selected.has(m.id)).map(media => ({media})))));
$('download-options').addEventListener('change', e => {
  if (e.target.dataset.formatIndex !== undefined) {
    const i = Number(e.target.dataset.formatIndex);
    $('dl-output-' + i).innerHTML = outputOptions(pendingDownloads[i].media, e.target.value);
  }
});
$('confirm-download').addEventListener('click', async e => {
  $('download-error').textContent = '';
  if (!$('authorized').checked) { $('download-error').textContent = 'Confirme que tem autorização para obter os arquivos.'; return; }
  const button = e.currentTarget;
  button.disabled = true;
  let count = 0;
  try {
    if(!await prepareDestination()) return;
    for (let i = 0; i < pendingDownloads.length; i++) {
      const entry = pendingDownloads[i];
      if (entry.queued) continue;
      const title = $('dl-title-' + i).value.trim(), artist = $('dl-artist-' + i).value.trim();
      if (!title || !artist) throw new Error('Informe artista e música para nomear cada arquivo.');
      const output = $('dl-output-' + i).value.split(':');
      await api('jobs', {media_id: entry.media.id, format_id: $('dl-format-' + i).value, output: output[0], bitrate: Number(output[1] || 128), authorized: true, title, artist, item_id: entry.track?.id || null});
      entry.queued = true; count++;
    }
    $('download-dialog').close(); view('queue'); toast(`${count} tarefa(s) adicionada(s). Acompanhe a fila.`);
  } catch (error) { $('download-error').textContent = `${count ? count + ' tarefa(s) já adicionada(s). ' : ''}${error.message}`; }
  finally { button.disabled = false; await refresh(); }
});
$('import-file').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 1900000) throw new Error('Escolha uma lista de até 1,9 MB.');
    const data = await api('import', {filename: file.name, content: await file.text()});
    await refresh(); toast(`${data.count} música(s) importada(s). Selecione para pesquisar.`);
  } catch (error) { toast(error.message, true); }
  finally { e.target.value = ''; }
});
$('tracks').addEventListener('change', e => {
  if (e.target.matches('.track-check')) {
    if (e.target.checked) trackSelection.add(e.target.value); else trackSelection.delete(e.target.value);
    updateLibraryControls();
  }
});
$('select-all-tracks').addEventListener('change', e => {
  const matching=filterItems('library',state.tracks).map(t=>t.id);
  if(e.target.checked) trackSelection=new Set(matching); else for(const id of matching) trackSelection.delete(id);
  renderTracks();
});
$('tracks').addEventListener('click', e => {
  const search = e.target.closest('[data-search-track]'), review = e.target.closest('[data-review]'), dl = e.target.closest('[data-download-track]');
  if (search) busy(search, 'Buscando…', () => searchTrack(state.tracks.find(t => t.id === search.dataset.searchTrack)));
  if (review) {
    busy(review, 'Abrindo…', () => reviewTrack(review.dataset.review));
  }
  if (dl) busy(dl, 'Preparando…', () => prepareTracks([state.tracks.find(t => t.id === dl.dataset.downloadTrack)]));
});
async function searchTracks(tracks) {
  if (batchRunning) return;
  if (!tracks.length) return toast('Selecione as músicas que deseja pesquisar.', true);
  batchRunning = true; stopBatch = false; $('search-tracks').disabled = true; $('stop-search').hidden = false;
  let done = 0, failed = 0;
  try {
    for (const track of tracks) {
      if (stopBatch) break;
      $('batch-status').textContent = `Pesquisando ${done + 1} de ${tracks.length}: ${track.artist} — ${track.title}. A origem pode demorar.`;
      try { await searchTrack(track, false); } catch (error) { failed++; toast(error.message, true); }
      done++;
      if (done < tracks.length && !stopBatch) await new Promise(r => setTimeout(r, 2200));
    }
  } finally {
    batchRunning = false; $('search-tracks').disabled = false; $('stop-search').hidden = true;
    $('batch-status').textContent = `${done} busca(s) processada(s), ${failed} falha(s). ${stopBatch ? 'Próximas buscas interrompidas.' : 'Revise os resultados antes de baixar.'}`;
    await refresh();
  }
}
$('search-tracks').addEventListener('click', () => searchTracks(state.tracks.filter(t => trackSelection.has(t.id) && !activeStates.includes(t.state))));
$('stop-search').addEventListener('click', () => { stopBatch = true; $('batch-status').textContent = 'A busca atual termina; as próximas não serão iniciadas.'; });
$('download-tracks').addEventListener('click', () => prepareSelectedTracks().catch(error => toast(error.message, true)));
$('clear-library').addEventListener('click', async () => {
  if (!window.confirm(tr('Remover a lista importada do servidor? O histórico de tarefas será preservado.'))) return;
  try { await api('library/clear', {}); trackSelection.clear(); await refresh(); toast('Lista removida.'); }
  catch (error) { toast(error.message, true); }
});
for (const id of ['jobs', 'history']) $(id).addEventListener('click', e => {
  const review = e.target.closest('[data-review-job]');
  if (review) busy(review, 'Abrindo…', () => reviewTrack(review.dataset.reviewJob));
  const cancel = e.target.closest('[data-cancel]'), retry = e.target.closest('[data-retry]');
  if (cancel) busy(cancel, '…', async () => { await api(`jobs/${cancel.dataset.cancel}/cancel`, {}); await refresh(); });
  if (retry) busy(retry, 'Preparando…', async () => { if(!await prepareDestination()) return; await api(`jobs/${retry.dataset.retry}/retry`, {}); await refresh(); view('queue'); });
});
$('about-button').addEventListener('click', () => $('about-dialog').showModal());
document.querySelectorAll('.close-dialog').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

function updateDesktop() {
  const s = state.spotify;
  $('destination-label').textContent = state.output_folder || 'Escolha sua pasta';
  $('destination-label').title = state.output_folder || '';
  $('folders-current').textContent = state.output_folder || 'Escolha a pasta';
  const moving=state.move_task || {};
  const moveMessage=moving.running ? `Movendo ${moving.done}/${moving.total} · ${moving.moved} movido(s) · ${moving.errors} erro(s).` : moving.total ? `${moving.cancelled ? 'Movimentação interrompida. ' : ''}${moving.moved} movido(s), ${moving.skipped} já na pasta, ${moving.errors} erro(s). ${moving.last_error || ''}` : 'Os arquivos concluídos podem ser movidos para outra pasta, mantendo o histórico e as capas.';
  for(const id of ['move-status','folders-move-status']) $(id).textContent=moveMessage;
  for(const id of ['stop-move','folders-stop-move']) $(id).hidden=!moving.running;
  $('move-all-files').disabled=!!moving.running || !filterItems('history',state.jobs).some(j=>j.ready);
  $('move-all-files').innerHTML=icon('folder')+(tabQueries.history ? 'Mover resultados baixados' : 'Mover arquivos baixados');
  $('folders-move-all').disabled=!!moving.running || !state.jobs.some(j=>j.ready);
  const task=state.cover_task || {};
  $('update-covers').disabled=!!task.running;
  $('cover-status').textContent=task.running ? `Atualizando capas: ${task.done}/${task.total} · ${task.embedded} incluídas.` : task.total ? `Capas: ${task.embedded} incluídas, ${task.unavailable} indisponíveis. Áudio preservado.` : 'As capas aparecem nas listas quando disponíveis. Use Atualizar capas dos arquivos para incluir nos downloads antigos.';
  $('batch-folder').textContent = state.output_folder || 'Escolha a pasta';
  $('download-folder').textContent = state.output_folder || 'Escolha a pasta';
  if(currentView==='settings') renderSettings();
  $('library-intro').textContent = 'Leia suas curtidas pela página do Spotify. Sua conta pode ser gratuita.';
  $('spotify-message').textContent = s.message;
  $('spotify-connect').hidden = false;
  $('spotify-connect').textContent = s.browser_open ? 'Mostrar Spotify' : 'Abrir Spotify';
  $('spotify-connect').disabled = s.busy;
  $('spotify-sync').hidden = !s.browser_open;
  $('spotify-disconnect').hidden = !s.browser_open;
  $('spotify-sync').disabled = s.busy;
  $('spotify-sync').textContent = s.busy ? 'Lendo curtidas…' : 'Ler curtidas';
  $('spotify-stop').hidden = !s.busy;
  if (s.busy) spotifyWatchUntil = Date.now() + 300000;
  if (!state.tracks.length) $('tracks').innerHTML = empty('Suas favoritas, sem arquivos para importar.', 'Abra o Spotify, faça o login e clique em Ler curtidas. Nenhum cadastro de desenvolvedor é necessário.');
}

async function native(name, ...args) {
  if (!window.pywebview?.api?.[name]) throw new Error('A janela do Acervo ainda está iniciando. Aguarde um momento e tente novamente.');
  return window.pywebview.api[name](...args, window.pywebview.token);
}

async function prepareDestination() {
  if(!DESKTOP) return true;
  if(state.folder_mode==='ask' || !state.output_folder) {
    const folder=await native('choose_folder');
    if(!folder) {toast('Escolha cancelada. Nenhuma nova tarefa foi iniciada.');return false;}
    await refresh(true);
  }
  return true;
}

async function quickDownload(ids, output, trackId=null) {
  if(quickRunning) return toast('Aguarde a seleção de pasta ou o envio atual.');
  quickRunning=true;
  try {
    if(!ids.length) throw new Error('Selecione as músicas que deseja baixar.');
    if(!await prepareDestination()) return;
    if(trackId) {
      contextTrack=await api(`library/${trackId}`);
      if(contextTrack.media_id!==ids[0] || !['Encontrada','Na fila','Concluída'].includes(contextTrack.state)) {
        await api(`library/${trackId}/select`,{media_id:ids[0]});
        contextTrack=await api(`library/${trackId}`);
      }
    }
    const added=await api('jobs/quick',{media_ids:ids,output,item_id:trackId,authorized:true});
    await refresh(true);if(ids.length>1) view('queue');
    toast(`${added.count} arquivo(s) na fila em ${output.toUpperCase()}. ${added.skipped} já na fila.`);
  } finally {quickRunning=false;}
}

async function quickLibrary(output, ids=state.tracks.filter(t=>trackSelection.has(t.id)).map(t=>t.id), skipReview=false) {
  if(quickRunning) return toast('Aguarde o envio atual.');
  quickRunning=true;
  try {
    if(!ids.length) throw new Error('Selecione as músicas que deseja baixar.');
    if(!await prepareDestination()) return;
    const added=await api('library/download',{track_ids:ids,source:$('library-source').value,output,authorized:true,skip_review:skipReview});
    await refresh(true);if(ids.length>1) view('queue');toast(`${added.count} arquivo(s) na fila${skipReview ? ' sem revisão' : ''}. ${added.skipped} já na fila ou salvos.`);
  } finally {quickRunning=false;}
}

document.addEventListener('click',e=>{
  const folder=e.target.closest('[data-change-folder]');
  if(folder) busy(folder,'Escolhendo…',async()=>{if(await native('choose_folder')) await refresh(true);});
  const direct=e.target.closest('[data-quick-media]');
  if(direct) busy(direct,'Preparando…',()=>quickDownload([direct.dataset.quickMedia],direct.dataset.output,direct.hasAttribute('data-review-media') ? contextTrack?.id : null));
  const track=e.target.closest('[data-library-track]');
  if(track) busy(track,'Adicionando…',()=>quickLibrary(track.dataset.output,[track.dataset.libraryTrack]));
  const automatic=e.target.closest('[data-no-review]');
  if(automatic) busy(automatic,'Preparando…',()=>quickLibrary($('no-review-format').value,[automatic.dataset.noReview],true));
  const cancel=e.target.closest('[data-cancel]');
  if(cancel && !cancel.closest('#jobs,#history')) busy(cancel,'Cancelando…',async()=>{await api(`jobs/${cancel.dataset.cancel}/cancel`,{});await refresh(true);});
  const play=e.target.closest('[data-play-saved]');
  if(play && !play.closest('#jobs,#history')) playSaved(state.jobs.find(j=>j.id===play.dataset.playSaved));
});
for(const output of ['mp3','mp4']) {
  $('selected-'+output).addEventListener('click',e=>busy(e.currentTarget,'Adicionando…',()=>quickDownload([...selected],output)));
  $('library-'+output).addEventListener('click',e=>busy(e.currentTarget,'Adicionando…',()=>quickLibrary(output)));
}
for(const id of ['no-review-format','review-no-review-format']) $(id).addEventListener('change',e=>{
  $('no-review-format').value=e.target.value;$('review-no-review-format').value=e.target.value;renderTracks();renderReviews();
});
$('download-no-review-selected').addEventListener('click',e=>busy(e.currentTarget,'Preparando…',()=>quickLibrary($('no-review-format').value,undefined,true)));
$('download-no-review-all').addEventListener('click',e=>busy(e.currentTarget,'Preparando tudo…',()=>quickLibrary($('no-review-format').value,state.tracks.map(t=>t.id),true)));
$('select-all-results').addEventListener('change',e=>{selected=e.target.checked ? new Set(results.map(m=>m.id)) : new Set();renderResults();});
$('close-review').addEventListener('click',()=>{contextTrack=null;renderReviews();});
$('reviews').addEventListener('click',e=>{
  const review=e.target.closest('[data-review]'),search=e.target.closest('[data-search-track]');
  if(review) busy(review,'Abrindo…',()=>reviewTrack(review.dataset.review));
  if(search) busy(search,'Buscando…',()=>searchTrack(state.tracks.find(t=>t.id===search.dataset.searchTrack)));
});
$('review-candidates').addEventListener('click',e=>{
  const confirm=e.target.closest('[data-confirm]');
  if(confirm) busy(confirm,'Confirmando…',async()=>{
    const id=contextTrack.id;await api(`library/${id}/select`,{media_id:confirm.dataset.confirm});
    contextTrack=await api(`library/${id}`);await refresh(true);renderReviews();toast('Gravação confirmada. Escolha MP3 ou MP4 para baixar.');
  });
});
$('settings-form').addEventListener('change',()=>{settingsDirty=true;});
$('settings-form').addEventListener('submit',e=>{
  e.preventDefault();busy(e.submitter,'Salvando…',async()=>{
    const folder_mode=document.querySelector('input[name="folder-mode"]:checked').value;
    const language=$('language').value;
    await api('settings',{folder_mode,background_enabled:$('background-enabled').checked,language});settingsDirty=false;
    if(language!==window.AcervoI18n.language) {
      if(window.pywebview?.api?.update_language) await native('update_language');
      window.location.reload(); return;
    }
    await refresh(true);$('settings-message').textContent='Configurações salvas';
  });
});

function spotifySettings() { $('spotify-dialog').showModal(); }

async function spotifyConnect() {
  await api('spotify/connect', {});
  spotifyWatchUntil = Date.now() + 300000;
  await refresh();
  toast('Faça o login no navegador, abra Músicas Curtidas e depois clique em Ler curtidas.');
}

$('choose-folder').addEventListener('click', () => $('folders-dialog').showModal());
for (const id of ['choose-batch-folder', 'choose-download-folder']) $(id).addEventListener('click', e => busy(e.currentTarget, 'Escolhendo…', async () => {
  if (await native('choose_folder')) await refresh(true);
}));
$('confirm-batch').addEventListener('click', e => busy(e.currentTarget, 'Adicionando toda a seleção…', async () => {
  $('batch-error').textContent = '';
  if (!$('batch-authorized').checked) { $('batch-error').textContent = 'Confirme que tem autorização para obter os arquivos.'; return; }
  try {
    if(!await prepareDestination()) return;
    const added = await api('library/download', {track_ids:pendingTrackIds, source:$('batch-source').value, output:$('batch-output').value, authorized:true});
    $('library-source').value = $('batch-source').value;
    $('batch-dialog').close(); view('queue'); await refresh(true);
    toast(`${added.count} arquivo(s) na fila. ${added.skipped} já na fila ou salvos. A busca e os downloads seguem automaticamente.`);
  } catch (error) { $('batch-error').textContent = error.message; }
}));
$('cancel-all').addEventListener('click', e => busy(e.currentTarget, 'Cancelando…', async () => {
  const data = await api('jobs/cancel-all', {}); await refresh(true); toast(`${data.count} tarefa(s) cancelada(s).`);
}));
$('open-logs').addEventListener('click', e => busy(e.currentTarget, 'Abrindo…', () => native('open_logs')));
$('spotify-settings').addEventListener('click', spotifySettings);
$('spotify-connect').addEventListener('click', e => busy(e.currentTarget, 'Conectando…', spotifyConnect));
$('spotify-sync').addEventListener('click', e => busy(e.currentTarget, 'Carregando…', async () => {await api('spotify/sync', {}); await refresh();}));
$('spotify-disconnect').addEventListener('click', e => busy(e.currentTarget, 'Desconectando…', async () => {await api('spotify/disconnect', {}); await refresh();}));
$('spotify-stop').addEventListener('click', e => busy(e.currentTarget, 'Parando…', async () => {await api('spotify/stop', {}); await refresh();}));
function playSaved(j) {
  if(!j?.ready || j.state!=='Concluída') return toast('O arquivo ainda não está disponível.',true);
    if (j.output === 'mp4') {
      $('audio').pause();
      setUserText('video-title', j.artist + ' — ' + j.title);
      $('video').src = '/api/jobs/' + j.id + '/file?play=true';
      $('video-dialog').showModal();
      $('video').play().catch(() => toast('Toque no vídeo para iniciar.'));
      return;
    }
    $('player-cover').innerHTML=cover('job',j,'player-art');
    localUrls.forEach(url => URL.revokeObjectURL(url)); localUrls = []; localFiles = [];
    $('audio').src = '/api/jobs/' + j.id + '/file?play=true';
    setUserText('player-title', j.artist + ' — ' + j.title);
    $('player-subtitle').textContent = 'Arquivo salvo no seu PC';
    $('audio').play().catch(() => toast('Toque no player para iniciar.'));
}
for (const container of ['jobs','history']) $(container).addEventListener('click', e => {
  const folder=e.target.closest('[data-open-folder]'),play=e.target.closest('[data-play-saved]');
  if(folder) busy(folder,'Abrindo…',()=>native('open_folder',folder.dataset.openFolder));
  if(play) playSaved(state.jobs.find(j=>j.id===play.dataset.playSaved));
});
if (DESKTOP) {
  document.querySelector('#download-dialog > p.muted').textContent = 'O áudio será salvo diretamente na pasta escolhida. Arquivos existentes não serão substituídos.';
  $('confirm-download').textContent = 'Baixar na pasta escolhida';
}

function playLocal(index, autoplay = true) {
  localIndex = index;
  $('player-cover').innerHTML=icon('wave');
  $('audio').src = localUrls[index];
  setUserText('player-title', localFiles[index].name);
  $('player-subtitle').textContent = `Arquivo local · ${index + 1} de ${localFiles.length} · não enviado ao servidor`;
  $('playlist').innerHTML = localFiles.map((f, i) => `<button data-i18n-skip data-play="${i}" class="${index === i ? 'active' : ''}">${i + 1}. ${esc(f.name)}</button>`).join('');
  if (autoplay) $('audio').play().catch(() => toast('Toque no player para iniciar a reprodução.'));
}
$('local-files').addEventListener('change', e => {
  if (!e.target.files.length) return;
  $('audio').pause(); $('audio').removeAttribute('src'); $('audio').load();
  localUrls.forEach(url => URL.revokeObjectURL(url));
  localFiles = [...e.target.files]; localUrls = localFiles.map(f => URL.createObjectURL(f)); playLocal(0);
});
$('playlist-button').addEventListener('click', () => {
  if (!localFiles.length) return toast('Abra arquivos locais para criar uma lista de reprodução.');
  $('playlist').hidden = !$('playlist').hidden;
});
$('playlist').addEventListener('click', e => {
  const button = e.target.closest('[data-play]');
  if (button) playLocal(Number(button.dataset.play));
});
$('audio').addEventListener('ended', () => { if (localIndex + 1 < localFiles.length) playLocal(localIndex + 1); });
$('audio').addEventListener('error', () => { if ($('audio').src) toast('O navegador não conseguiu reproduzir este formato. Tente MP3 ou outro player local.', true); });
window.addEventListener('beforeunload', () => localUrls.forEach(url => URL.revokeObjectURL(url)));
refresh();

$('video-dialog').addEventListener('close', () => { $('video').pause(); $('video').removeAttribute('src'); $('video').load(); });

$('update-covers').addEventListener('click',e=>busy(e.currentTarget,'Atualizando…',async()=>{await api('covers/update-downloaded',{});await refresh(true);}));

function openMoveDialog(identifiers=null) {
  if(state.move_task?.running) return toast('Há uma movimentação em andamento. Acompanhe em Pastas ou no Histórico.');
  moveIds=identifiers || state.jobs.filter(j=>j.ready).map(j=>j.id);
  if(!moveIds.length) return toast('Não há arquivos concluídos disponíveis para mover.',true);
  chosenMoveFolder=null;
  $('move-count').textContent=moveIds.length===1 ? `Mover: ${state.jobs.find(j=>j.id===moveIds[0])?.title || 'arquivo selecionado'}.` : `Mover ${moveIds.length} arquivos já baixados.`;
  $('move-folder').textContent='Escolha a pasta de destino';
  $('move-error').textContent='';$('confirm-move').disabled=true;
  if($('folders-dialog').open) $('folders-dialog').close();
  $('move-dialog').showModal();
}
$('change-output-folder').addEventListener('click',e=>busy(e.currentTarget,'Escolhendo…',async()=>{
  const folder=await native('choose_folder');
  if(!folder) return;
  const applied=$('folders-queued').checked ? await api('folders/apply-pending',{}) : {count:0};
  await refresh(true);toast(`Pasta de destino alterada. ${applied.count} tarefa(s) pendente(s) atualizada(s).`);
}));
for(const id of ['move-all-files','folders-move-all']) $(id).addEventListener('click',()=>openMoveDialog(id==='move-all-files' && tabQueries.history ? filterItems('history',state.jobs).filter(j=>j.ready).map(j=>j.id) : null));
for(const id of ['jobs','history']) $(id).addEventListener('click',e=>{
  const button=e.target.closest('[data-move-job]');if(button) openMoveDialog([button.dataset.moveJob]);
});
$('choose-move-folder').addEventListener('click',e=>busy(e.currentTarget,'Escolhendo…',async()=>{
  const folder=await native('pick_move_folder');
  if(folder){chosenMoveFolder=folder;$('move-folder').textContent=folder;$('confirm-move').disabled=false;}
}));
$('confirm-move').addEventListener('click',e=>busy(e.currentTarget,'Iniciando…',async()=>{
  if(!chosenMoveFolder) return;
  $('move-error').textContent='';
  try {
    $('audio').pause();$('audio').removeAttribute('src');$('audio').load();
    $('video').pause();$('video').removeAttribute('src');$('video').load();
    await api('folders/move',{job_ids:moveIds});
    $('move-dialog').close();view('history');await refresh(true);
    toast('Movimentação iniciada. Acompanhe o progresso no Histórico ou em Pastas.');
  }catch(error){$('move-error').textContent=error.message;}
}));
for(const id of ['stop-move','folders-stop-move']) $(id).addEventListener('click',e=>busy(e.currentTarget,'Parando…',async()=>{
  await api('folders/stop',{});await refresh(true);
}));
$('hide-window').addEventListener('click',e=>busy(e.currentTarget,'Recolhendo…',()=>native('hide_window')));
$('exit-app').addEventListener('click',()=>native('exit_app').catch(error=>toast(error.message,true)));
