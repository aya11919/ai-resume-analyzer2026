// URL du backend — définie globalement dans index.html (window.BACKEND_URL)
const BACKEND_URL = (window.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

// ═══════════════════════════════════════════════════════
//  AI Resume Analyzer — Dashboard App (v2 clean + charts)
// ═══════════════════════════════════════════════════════

const state = {
  backendOnline: false,
  user: null,
  history: [],
  chatQCount: 0,
  currentFiles: [],
  lastResult: null,
  lastResults: [], // [{ fileName, data }, ...] — tous les CV de la dernière analyse
  selectedChatId: null, // id du CV actuellement discuté dans l'Assistant IA
  sessionJobTitleAbbrs: {}, // { fileName: 'DFS' } — mémoire de session (non persisté en base)
  sessionJobSkills: {}, // { fileName: ['Python', 'React'] } — compétences requises par l'offre, mémoire de session
  charts: {}
};

const $ = id => document.getElementById(id);

// ─── Bootstrap ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // NOTE : l'auto-login a été retiré ici. L'utilisateur doit maintenant
  // passer par le formulaire de connexion / inscription (modal #auth-modal)
  // pour accéder au dashboard. C'est handleAuthSubmit() (dans le HTML)
  // qui appelle showDashboard(user) une fois le formulaire validé.

  initNetworkBackground();
  checkBackend();
  setupDashboard();
  setupDropzone();
  setupAnalyzeButton();
  setupChat();
  setupResumesToolbar();
  if (typeof bindExportButtons === 'function') bindExportButtons();
  if (typeof bindOverviewButtons === 'function') bindOverviewButtons();
});

// ═══════════════════════════════════════════════════════
//  "MES CV" TOOLBAR — recherche, tri, filtres, nouvelle analyse
// ═══════════════════════════════════════════════════════
const resumesToolbar = {
  searchTerm: '',
  minScore: 0,
  sortMode: 'score-desc', // 'date-desc' | 'date-asc' | 'score-desc' | 'score-asc'
};

function setupResumesToolbar() {
  $('btn-new-analysis-dash')?.addEventListener('click', () => switchPanel('analyze'));

  $('saas-search')?.addEventListener('input', e => {
    resumesToolbar.searchTerm = e.target.value.trim().toLowerCase();
    renderOverviewTable();
  });

  $('saas-sort-btn')?.addEventListener('click', () => {
    const order = ['date-desc', 'date-asc', 'score-desc', 'score-asc'];
    const labels = {
      'date-desc': 'Trier : Plus récent',
      'date-asc': 'Trier : Plus ancien',
      'score-desc': 'Trier : Score ↓',
      'score-asc': 'Trier : Score ↑',
    };
    const next = order[(order.indexOf(resumesToolbar.sortMode) + 1) % order.length];
    resumesToolbar.sortMode = next;
    const btn = $('saas-sort-btn');
    if (btn) {
      const label = btn.querySelector('.sort-label');
      if (label) label.textContent = labels[next];
      else btn.lastChild.textContent = ' ' + labels[next];
    }
    renderOverviewTable();
  });

  $('saas-filter-btn')?.addEventListener('click', () => {
    const input = window.prompt(
      'Afficher uniquement les CV avec un score ATS supérieur ou égal à :\n(laisser vide ou 0 pour tout afficher)',
      resumesToolbar.minScore || ''
    );
    if (input === null) return; // annulé
    const val = parseInt(input, 10);
    resumesToolbar.minScore = isNaN(val) ? 0 : Math.max(0, Math.min(100, val));
    const btn = $('saas-filter-btn');
    if (btn) {
      const label = btn.querySelector('.filter-label');
      const text = resumesToolbar.minScore > 0 ? `Filtres (≥ ${resumesToolbar.minScore})` : 'Filtres';
      if (label) label.textContent = text;
      else btn.lastChild.textContent = ' ' + text;
    }
    renderOverviewTable();
  });
}

function applyResumesToolbar(history) {
  let result = history;

  if (resumesToolbar.searchTerm) {
    result = result.filter(h =>
      (h.fileName || '').toLowerCase().includes(resumesToolbar.searchTerm) ||
      (h.job_title || '').toLowerCase().includes(resumesToolbar.searchTerm)
    );
  }

  if (resumesToolbar.minScore > 0) {
    result = result.filter(h => (h.atsScore || 0) >= resumesToolbar.minScore);
  }

  const sorted = [...result];
  switch (resumesToolbar.sortMode) {
    case 'date-asc':
      sorted.reverse();
      break;
    case 'score-desc':
      sorted.sort((a, b) => (b.atsScore || 0) - (a.atsScore || 0));
      break;
    case 'score-asc':
      sorted.sort((a, b) => (a.atsScore || 0) - (b.atsScore || 0));
      break;
    // 'date-desc' : déjà dans cet ordre (state.history est unshift à chaque nouvelle analyse)
  }
  return sorted;
}

// ═══════════════════════════════════════════════════════
//  BACKEND HEALTH CHECK
// ═══════════════════════════════════════════════════════
async function checkBackend() {
  try {
    const res = await fetch(`${BACKEND_URL}/`);
    if (res.ok) {
      state.backendOnline = true;
      const dot = $('status-dot');
      const txt = $('status-text');
      if (dot) dot.classList.add('on');
      if (txt) txt.textContent = 'Backend connecté';
    }
  } catch {
    // offline — mode démo
  }
}

// ═══════════════════════════════════════════════════════
//  DASHBOARD NAVIGATION
// ═══════════════════════════════════════════════════════
function setupDashboard() {
  $('menu-overview')?.addEventListener('click', e => { e.preventDefault(); switchPanel('overview'); });
  $('menu-analyze')?.addEventListener('click', e => { e.preventDefault(); switchPanel('analyze'); });
  $('menu-resumes')?.addEventListener('click', e => { e.preventDefault(); switchPanel('resumes'); });
  $('menu-chat')?.addEventListener('click', e => { e.preventDefault(); switchPanel('chat'); });
  $('menu-calendar')?.addEventListener('click', e => { e.preventDefault(); switchPanel('calendar'); loadAndDisplayCalendar(); });

  $('btn-logout')?.addEventListener('click', () => {
    state.user = null; state.history = []; state.chatQCount = 0;
    state.currentFile = null; state.lastResult = null;
    if ($('chat-msgs')) $('chat-msgs').innerHTML = '';
    hideDashboard();
    toast('Vous avez été déconnecté.', 'i');
  });
}

function switchPanel(name) {
  ['overview', 'analyze', 'resumes', 'chat', 'calendar'].forEach(n => {
    $(`panel-${n}`)?.classList.toggle('active', n === name);
    $(`menu-${n}`)?.classList.toggle('active', n === name);
  });
  if (name === 'resumes') {
    if (typeof renderHistoryGrid === 'function') renderHistoryGrid();
    if (typeof renderOverviewTable === 'function') renderOverviewTable();
  }
  if (name === 'overview') {
    if (typeof refreshStats === 'function') refreshStats();
  }
  if (name === 'chat') {
    if (typeof populateChatCvSelect === 'function') populateChatCvSelect();
  }
}

function bindOverviewButtons() {
  $('btn-new-analysis-overview')?.addEventListener('click', () => switchPanel('analyze'));
  $('btn-new-from-resumes')?.addEventListener('click', () => switchPanel('analyze'));
  $('btn-view-all-resumes')?.addEventListener('click', () => switchPanel('resumes'));
  $('btn-start-first')?.addEventListener('click', () => switchPanel('analyze'));
}

// ═══════════════════════════════════════════════════════
//  DROPZONE & FILE HANDLING
// ═══════════════════════════════════════════════════════
function setupDropzone() {
  const dz = $('dropzone');
  if (!dz) return;

  dz.addEventListener('click', () => $('file-input')?.click());
  $('file-input')?.addEventListener('change', () => {
    handleFiles($('file-input').files);
  });
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('drag-over');
    if (e.dataTransfer.files?.length) handleFiles(e.dataTransfer.files);
  });
}

function handleFiles(fileList) {
  const allowedTypes = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'];
  let added = 0, rejected = 0;

  Array.from(fileList || []).forEach(file => {
    const validType = allowedTypes.includes(file.type) || file.name.match(/\.(pdf|docx|pptx)$/i);
    if (!validType) { rejected++; return; }
    if (file.size > 10 * 1024 * 1024) { rejected++; return; }
    const dup = state.currentFiles.some(f => f.name === file.name && f.size === file.size);
    if (dup) return;
    state.currentFiles.push(file);
    added++;
  });

  renderFileList();

  if (added > 0) {
    toast(added === 1 ? `CV ajouté : ${fileList[fileList.length - 1].name}` : `${added} CV ajoutés.`, 's');
  }
  if (rejected > 0) {
    toast(`${rejected} fichier(s) ignoré(s) (format ou taille invalide).`, 'e');
  }

  const btn = $('btn-analyze');
  if (btn) btn.disabled = false;

  // Permet de resélectionner le(s) même(s) fichier(s) après retrait
  const fi = $('file-input');
  if (fi) fi.value = '';
}

function removeFileAt(index) {
  state.currentFiles.splice(index, 1);
  renderFileList();
  const btn = $('btn-analyze');
  if (btn) btn.disabled = false;
}

function renderFileList() {
  const list = $('file-list');
  if (!list) return;

  if (state.currentFiles.length === 0) {
    list.classList.remove('visible');
    list.innerHTML = '';
    return;
  }

  list.classList.add('visible');
  const header = `<div class="file-list-count">${state.currentFiles.length} CV sélectionné${state.currentFiles.length > 1 ? 's' : ''}</div>`;
  const cards = state.currentFiles.map((file, i) => `
    <div class="file-card">
      <div class="file-card-info">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
        <div>
          <div class="file-card-name">${esc(file.name)}</div>
          <div class="file-card-size">${formatBytes(file.size)}</div>
        </div>
      </div>
      <button type="button" class="file-remove" onclick="event.stopPropagation(); removeFileAt(${i})">✕ Retirer</button>
    </div>
  `).join('');

  list.innerHTML = header + cards;
}

function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' Ko';
  return (b / (1024 * 1024)).toFixed(1) + ' Mo';
}

// ═══════════════════════════════════════════════════════
//  ANALYZE BUTTON
// ═══════════════════════════════════════════════════════
function setupAnalyzeButton() {
  $('btn-analyze')?.addEventListener('click', runAnalysis);
  $('btn-share-linkedin')?.addEventListener('click', shareJobOfferOnLinkedIn);
  $('btn-preview-offer')?.addEventListener('click', openOfferPreview);
  setupOfferPreviewModal();
}

// ═══════════════════════════════════════════════════════
//  OFFER PREVIEW MODAL
// ═══════════════════════════════════════════════════════

// Stocke la data URL de l'image générée pour le téléchargement
let _previewPosterDataUrl = null;
let _previewPostText = '';
let _previewFilename = '';

function setupOfferPreviewModal() {
  // Fermer le modal en cliquant sur X
  $('close-offer-preview')?.addEventListener('click', () => {
    $('offer-preview-modal').style.display = 'none';
  });
  // Fermer en cliquant sur le fond
  $('offer-preview-modal')?.addEventListener('click', (e) => {
    if (e.target === $('offer-preview-modal')) {
      $('offer-preview-modal').style.display = 'none';
    }
  });
  // Télécharger l'affiche
  $('preview-download-btn')?.addEventListener('click', () => {
    if (!_previewPosterDataUrl) return;
    const link = document.createElement('a');
    link.download = _previewFilename;
    link.href = _previewPosterDataUrl;
    link.click();
    toast('🖼️ Affiche téléchargée !', 's');
  });
  // Publier depuis le modal
  $('preview-publish-btn')?.addEventListener('click', () => {
    $('offer-preview-modal').style.display = 'none';
    const url = `https://www.linkedin.com/feed/?shareActive=true&text=${encodeURIComponent(_previewPostText)}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  });
}

async function openOfferPreview() {
  const titre = $('jd-titre')?.value.trim() || '';
  const diplome = $('jd-entreprise')?.value.trim() || '';
  const competences = $('jd-competences')?.value.trim() || '';
  const reference = $('jd-reference')?.value.trim() || '';

  if (!titre) {
    toast('Remplis au moins l\'intitulé du poste pour prévisualiser.', 'e');
    return;
  }

  const titleCase = s => s.replace(/\S+/g, w => w.charAt(0).toUpperCase() + w.slice(1));

  // Préparation du texte du post
  const lines = [
    `Nous recrutons : ${titleCase(titre)}`,
    '',
    `Nous renforçons nos équipes et recherchons un(e) ${titleCase(titre)} pour nous accompagner dans nos projets.`,
  ];
  const criteria = [];
  if (diplome) criteria.push(`Formation : ${titleCase(diplome)}`);
  if (competences) criteria.push(`Compétences clés : ${competences}`);
  if (criteria.length) {
    lines.push('', 'Profil recherché :', ...criteria.map(c => `• ${c}`));
  }
  lines.push('', 'Si cette opportunité vous correspond, n\'hésitez pas à nous transmettre votre candidature.');
  if (reference) lines.push('', `Réf. offre : ${reference}`);
  lines.push('', '#Recrutement #Emploi #Opportunité #RH #Nousrejoindre');
  _previewPostText = lines.join('\n');

  // Affiche le texte dans le modal
  const textEl = $('preview-post-text');
  if (textEl) textEl.textContent = _previewPostText;

  // Génère l'image de l'affiche
  const posterContainer = $('job-poster-card');
  if (posterContainer) {
    posterContainer.innerHTML = renderJobPosterHTML({
      title: titre, diploma: diplome, skills: competences,
      reference: reference, location: 'Casablanca, Maroc'
    });
  }

  // Affiche le modal avec un spinner pendant la génération
  const imgEl = $('preview-poster-img');
  if (imgEl) { imgEl.src = ''; imgEl.style.display = 'none'; }
  $('offer-preview-modal').style.display = 'block';

  try {
    const canvas = await html2canvas(posterContainer, { scale: 2, useCORS: true, backgroundColor: '#ffffff' });
    _previewPosterDataUrl = canvas.toDataURL('image/png');
    _previewFilename = `Affiche_Offre_${titre.replace(/[^a-zA-Z0-9_-]/g, '_')}.png`;
    if (imgEl) {
      imgEl.src = _previewPosterDataUrl;
      imgEl.style.display = 'block';
    }
  } catch (err) {
    console.error('Erreur génération aperçu:', err);
    if ($('preview-poster-image-wrap')) {
      $('preview-poster-image-wrap').style.display = 'none';
    }
  }
}

// Génère l'affiche visuelle de l'offre en HTML et la convertit en image HD
function renderJobPosterHTML(data) {
  const titleCase = s => s.replace(/\S+/g, w => w.charAt(0).toUpperCase() + w.slice(1));
  const rawTitle = (data.title || 'DÉVELOPPEUR FULL STACK').trim().toUpperCase();
  
  const words = rawTitle.split(/\s+/);
  let formattedTitle = rawTitle;
  if (words.length >= 3) {
    const mid = Math.ceil(words.length / 2);
    formattedTitle = words.slice(0, mid).join(' ') + '<br>' + words.slice(mid).join(' ');
  } else if (words.length === 2 && rawTitle.length > 14) {
    formattedTitle = words[0] + '<br>' + words[1];
  }

  const diplome = titleCase(data.diploma || 'Licence Informatique');
  const competencesRaw = data.skills || 'Python, SQL, React';
  const compList = competencesRaw.split(/[,;·•]/).map(s => titleCase(s.trim())).filter(Boolean);
  const competencesFormatted = compList.length ? compList.join(' · ') : competencesRaw;
  const reference = data.reference || '';
  const location = data.location || 'Casablanca, Maroc';

  return `
    <div style="background: linear-gradient(135deg, #0e0d12 0%, #17151e 100%); padding: 44px 38px 36px; color: #ffffff; position: relative; overflow: hidden; text-align: left;">
      <svg style="position: absolute; top: -40px; left: -40px; opacity: 0.85; pointer-events: none;" width="260" height="260" viewBox="0 0 200 200">
        <circle cx="20" cy="20" r="14" fill="#f97316" />
        <circle cx="20" cy="20" r="45" fill="none" stroke="#ea580c" stroke-width="6" opacity="0.9"/>
        <circle cx="20" cy="20" r="75" fill="none" stroke="#ea580c" stroke-width="6" opacity="0.7"/>
        <circle cx="20" cy="20" r="105" fill="none" stroke="#ea580c" stroke-width="6" opacity="0.5"/>
      </svg>

      <div style="position: absolute; top: 28px; right: 28px; background: #ea580c; color: #ffffff; font-weight: 900; font-size: 0.85rem; padding: 7px 18px; border-radius: 8px; transform: rotate(4deg); letter-spacing: 1.5px; box-shadow: 0 4px 14px rgba(234, 88, 12, 0.4); text-transform: uppercase;">
        ON RECRUTE
      </div>

      <div style="color: #ea580c; font-size: 0.88rem; font-weight: 800; letter-spacing: 3.5px; text-transform: uppercase; margin-bottom: 14px; margin-top: 10px;">
        NOUS RECRUTONS
      </div>

      <h1 style="font-size: 2.6rem; line-height: 1.1; font-weight: 900; text-transform: uppercase; margin: 0 0 20px 0; color: #f97316; text-shadow: 3px 3px 0px #c2410c, 5px 5px 0px #9a3412, 8px 8px 16px rgba(0,0,0,0.6); letter-spacing: -0.5px; font-family: 'Inter', sans-serif;">
        ${formattedTitle}
      </h1>

      <p style="font-size: 1.05rem; color: #e2e8f0; line-height: 1.5; margin: 0; max-width: 480px; font-weight: 400;">
        Nous renforçons nos équipes et recherchons un(e) ${esc(titleCase(data.title || 'Développeur Full Stack'))} pour nous accompagner dans nos projets.
      </p>
    </div>

    <div style="padding: 32px 38px 28px; background: #ffffff; text-align: left;">
      <div style="display: inline-block; margin-bottom: 20px; border-bottom: 4px solid #f97316; padding-bottom: 5px;">
        <h2 style="font-size: 1.1rem; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #0f172a; margin: 0;">
          PROFIL RECHERCHÉ
        </h2>
      </div>

      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 24px;">
        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 16px 14px;">
          <div style="font-size: 0.7rem; font-weight: 800; color: #ea580c; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;">
            DIPLÔME
          </div>
          <div style="font-size: 0.92rem; font-weight: 700; color: #0f172a; line-height: 1.35; word-break: break-word;">
            ${esc(diplome)}
          </div>
        </div>

        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 16px 14px;">
          <div style="font-size: 0.7rem; font-weight: 800; color: #ea580c; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;">
            COMPÉTENCES CLÉS
          </div>
          <div style="font-size: 0.92rem; font-weight: 700; color: #0f172a; line-height: 1.35; word-break: break-word;">
            ${esc(competencesFormatted)}
          </div>
        </div>

        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 16px 14px;">
          <div style="font-size: 0.7rem; font-weight: 800; color: #ea580c; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;">
            LIEU
          </div>
          <div style="font-size: 0.92rem; font-weight: 700; color: #0f172a; line-height: 1.35; word-break: break-word;">
            ${esc(location)}
          </div>
        </div>
      </div>

      <p style="font-size: 0.9rem; color: #475569; line-height: 1.5; margin: 0;">
        Si cette opportunité vous correspond, n'hésitez pas à nous transmettre votre candidature. ${reference ? `(Réf : ${esc(reference)})` : ''}
      </p>
    </div>

    <div style="background: #0e0d12; border-top: 4px solid #ea580c; padding: 26px 24px 20px; text-align: center; color: #ffffff; position: relative;">
      <div style="position: absolute; top: -16px; left: 50%; transform: translateX(-50%); background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 4px 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: inline-flex; align-items: center; gap: 4px;">
        <span style="font-weight: 900; font-size: 0.9rem; color: #000; font-family: sans-serif; font-style: italic;">votre</span>
        <span style="font-weight: 800; font-size: 0.8rem; color: #ea580c;">entreprise</span>
      </div>

      <div style="font-size: 1.05rem; font-weight: 900; color: #f97316; letter-spacing: 2px; text-transform: uppercase; margin-top: 8px; margin-bottom: 4px;">
        POSTULEZ MAINTENANT
      </div>
      <div style="font-size: 0.85rem; color: #94a3b8; font-weight: 500;">
        ${(typeof state !== 'undefined' && state.user && state.user.email) ? state.user.email : 'recrutement@votre-entreprise.com'}
      </div>
    </div>
  `;
}

// Prépare le texte de l'offre et ouvre LinkedIn
async function shareJobOfferOnLinkedIn() {
  const titre = $('jd-titre')?.value.trim() || '';
  const diplome = $('jd-entreprise')?.value.trim() || '';
  const competences = $('jd-competences')?.value.trim() || '';
  const reference = $('jd-reference')?.value.trim() || '';

  if (!titre) {
    toast('Remplis au moins l\'intitulé du poste avant de publier sur LinkedIn.', 'e');
    return;
  }

  const titleCase = s => s.replace(/\S+/g, w => w.charAt(0).toUpperCase() + w.slice(1));

  // 1. Préparation du texte du post LinkedIn
  const lines = [
    `Nous recrutons : ${titleCase(titre)}`,
    '',
    `Nous renforçons nos équipes et recherchons un(e) ${titleCase(titre)} pour nous accompagner dans nos projets.`,
  ];

  const criteria = [];
  if (diplome) criteria.push(`Formation : ${titleCase(diplome)}`);
  if (competences) criteria.push(`Compétences clés : ${competences}`);
  if (criteria.length) {
    lines.push('', 'Profil recherché :', ...criteria.map(c => `• ${c}`));
  }

  lines.push(
    '',
    'Si cette opportunité vous correspond, n\'hésitez pas à nous transmettre votre candidature.',
  );
  if (reference) lines.push('', `Réf. offre : ${reference}`);
  lines.push('', '#Recrutement #Emploi #Opportunité #RH #Nousrejoindre');

  const text = lines.join('\n');

  // Copie le texte dans le presse-papier
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    }
  } catch (e) {}

  // 2. Ouverture de LinkedIn avec le texte de l'offre pré-rempli
  const linkedinShareUrl = `https://www.linkedin.com/feed/?shareActive=true&text=${encodeURIComponent(text)}`;

  setTimeout(() => {
    window.open(linkedinShareUrl, '_blank', 'noopener,noreferrer');
  }, 400);
}

// Génère une abréviation courte à partir d'un intitulé de poste
// (ex: "Développeur Full Stack" -> "DFS"), en ignorant les petits mots
// de liaison (de, du, des, le, la, en...).
function abbreviateJobTitle(title) {
  if (!title) return '';
  const stopWords = new Set(['de', 'du', 'des', 'le', 'la', 'les', 'en', 'et', 'un', 'une', 'à', 'a']);
  const letters = title
    .trim()
    .split(/\s+/)
    .filter(w => w.length > 0 && !stopWords.has(w.toLowerCase()))
    .map(w => w[0].toUpperCase())
    .join('');
  return letters.slice(0, 6); // évite les abréviations disproportionnées sur des intitulés très longs
}

async function runAnalysis() {
  // 1. Contrôle obligatoire du CV
  if (!state.currentFiles || !state.currentFiles.length) {
    toast("Veuillez d'abord glisser ou sélectionner au moins un CV.", "e");
    return;
  }

  // 2. Contrôle obligatoire des informations de l'offre d'emploi
  const jobTitleInput = ($('jd-titre')?.value || '').trim();
  const jobDiplomaInput = ($('jd-entreprise')?.value || '').trim();
  const jobSkillsInput = ($('jd-competences')?.value || '').trim();
  const jobRefInput = ($('jd-reference')?.value || '').trim();

  if (!jobTitleInput) {
    toast("Veuillez remplir l'intitulé du poste dans l'offre d'emploi avant de lancer l'analyse.", "e");
    $('jd-titre')?.focus();
    return;
  }

  if (!jobSkillsInput) {
    toast("Veuillez remplir les compétences requises dans l'offre d'emploi avant de lancer l'analyse.", "e");
    $('jd-competences')?.focus();
    return;
  }

  const jobTitleAbbr = abbreviateJobTitle(jobTitleInput);
  const jobSkillsList = jobSkillsInput ? jobSkillsInput.split(/[,;\n]/).map(s => s.trim()).filter(Boolean) : [];
  const jd = [jobTitleInput, jobDiplomaInput, jobSkillsInput, jobRefInput ? `Référence : ${jobRefInput}` : null]
    .filter(Boolean).join('\n');

  const total = state.currentFiles.length;
  let lastResult = null;
  const batchResults = []; // [{ fileName, data }] pour CE lancement d'analyse
  let usedBackendAny = false;
  let successCount = 0;

  try {
    for (let i = 0; i < total; i++) {
      const file = state.currentFiles[i];
      showLoader(
        total > 1 ? `Analyse en cours (${i + 1}/${total})…` : 'Analyse en cours…',
        `Extraction sémantique et scoring ATS — ${file.name}`
      );

      let result;
      let usedBackend = false;

      if (state.backendOnline) {
        try {
          const fd = new FormData();
          fd.append('file', file);
          if (jd) fd.append('job_description', jd);
          if (jobTitleInput) fd.append('job_title', jobTitleInput);
          if (jobRefInput) fd.append('job_reference', jobRefInput);
          const res = await fetch(`${BACKEND_URL}/api/analyze`, { method: 'POST', headers: authHeaders(), body: fd });
          if (!res.ok) throw new Error('API ' + res.status);
          const raw = await res.json();
          const matchScore = raw.job_description_match?.match_score ?? null;
          result = {
            candidate_name: raw.candidate_name || file.name,
            ats_score: raw.overall_score ?? 0,
            scores: { ats: raw.overall_score ?? 0, match: matchScore },
            candidate_profile: { summary: raw.summary || '', skills: raw.skills || [] },
            strengths: raw.strengths || [],
            weaknesses: raw.weaknesses || [],
            recommendations: raw.recommendations || [],
            _backend_id: raw.id,
          };
          usedBackend = true;
        } catch (err) {
          console.warn('Backend failed, using demo:', err);
          await delay(1000);
          result = mockResult(file.name, !!jd);
        }
      } else {
        await delay(1400);
        result = mockResult(file.name, !!jd);
      }

      usedBackendAny = usedBackendAny || usedBackend;
      lastResult = result;
      batchResults.push({ fileName: file.name, data: result });
      successCount++;

      if (jobTitleAbbr) state.sessionJobTitleAbbrs[file.name] = jobTitleAbbr;
      if (jobSkillsList.length) state.sessionJobSkills[file.name] = jobSkillsList;

      state.history.unshift({
        id: result.id || (Date.now() + i),
        fileName: file.name,
        candidateName: result.candidate_name || file.name,
        fileSize: file.size,
        date: new Date().toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }),
        atsScore: result.ats_score ?? 0,
        matchScore: result.scores?.match ?? null,
        jobTitleAbbr: jobTitleAbbr,
        jobSkills: jobSkillsList,
        email: result.email || '',
        phone: result.phone || '',
        city: result.city || '',
        job_reference: result.job_reference || '',
        data: result,
      });
    }

    if (lastResult) {
      state.lastResult = lastResult;
      state.lastResults = batchResults;
      renderAllResults(batchResults);
    }
    refreshStats();

    if (total > 1) {
      toast(`${successCount} CV analysés ! Le détail de chacun est affiché ci-dessous, avec son propre nom.`, 's');
    } else {
      toast(usedBackendAny ? 'Analyse terminée !' : 'Analyse démo terminée (backend hors ligne).', 's');
    }

    state.currentFiles = [];
    renderFileList();
    const btn = $('btn-analyze');
    if (btn) btn.disabled = true;

  } catch (err) {
    console.error(err);
    toast('Erreur inattendue lors de l\'analyse.', 'e');
  } finally {
    hideLoader();
  }
}

function mockResult(filename, hasJD) {
  const ats = Math.floor(Math.random() * 30) + 60;
  const match = hasJD ? Math.floor(Math.random() * 30) + 60 : null;
  return {
    ats_score: ats,
    scores: { ats, match },
    candidate_profile: {
      summary: `Profil professionnel structuré avec ${Math.floor(Math.random() * 8) + 2} ans d'expérience. Bonnes réalisations quantifiées.`,
      skills: ['Python', 'JavaScript', 'Machine Learning', 'SQL', 'Agile', 'Communication', 'Leadership'],
    },
    strengths: [
      'Expériences bien mises en valeur avec des métriques quantifiables.',
      'Compétences techniques clairement listées et pertinentes.',
      'Mise en page lisible pour les ATS.',
    ],
    weaknesses: [
      'Section compétences manque de mots-clés sectoriels.',
      'Lettre de motivation absente.',
      'Certifications non mentionnées.',
    ],
    recommendations: [
      'Ajoutez 3 à 5 mots-clés tirés directement de l\'offre d\'emploi cible.',
      'Quantifiez les réalisations (%) et réduisez les verbes passifs.',
      'Intégrez une section "Projets personnels".',
    ],
  };
}

// ═══════════════════════════════════════════════════════
//  RENDER RESULTS — une carte détaillée par CV, nommée individuellement
// ═══════════════════════════════════════════════════════

// Construit le HTML d'une carte de résultat pour le CV d'index `i`.
function buildResultCardHTML(i, displayName) {
  return `
    <div class="cv-result-card" data-index="${i}">
      <div class="cv-result-header">
        <div class="cv-result-name-wrap">
          <div class="cv-result-index">${i + 1}</div>
          <h3 class="cv-result-name" title="${esc(displayName)}">${esc(displayName)}</h3>
        </div>
        <div class="cv-result-badges">
          <span class="cv-result-badge ats" id="badge-ats-${i}">ATS —</span>
          <span class="cv-result-badge match" id="badge-match-${i}" style="display:none">Match —</span>
        </div>
      </div>

      <div class="scores-row">
        <div class="card score-card ats">
          <div class="score-ring-wrap">
            <svg width="150" height="150">
              <circle class="ring-bg" cx="75" cy="75" r="60" />
              <circle class="ring-val violet" id="ring-ats-${i}" cx="75" cy="75" r="60" />
            </svg>
            <div class="score-num-wrap">
              <span class="score-num" id="ats-num-${i}">0</span>
              <span class="score-unit">/100</span>
            </div>
          </div>
          <div class="score-name">Score ATS Global</div>
          <div class="score-hint">Lisibilité, clarté et impact global.</div>
        </div>
        <div class="card score-card match" id="score-match-card-${i}" style="display:none">
          <div class="score-ring-wrap">
            <svg width="150" height="150">
              <circle class="ring-bg" cx="75" cy="75" r="60" />
              <circle class="ring-val green" id="ring-match-${i}" cx="75" cy="75" r="60" />
            </svg>
            <div class="score-num-wrap">
              <span class="score-num" id="match-num-${i}">0</span>
              <span class="score-unit">%</span>
            </div>
          </div>
          <div class="score-name">Correspondance Poste</div>
          <div class="score-hint">Adéquation avec l'offre renseignée.</div>
        </div>
      </div>

      <div class="stats-charts-grid">
        <div class="card stats-chart-card">
          <h3 class="dash-section-title" style="margin-bottom: 0.5rem;">Analyse Dimensionnelle</h3>
          <div class="chart-wrap">
            <canvas id="chart-radar-${i}" width="300" height="300"></canvas>
          </div>
          <div class="chart-legend" id="radar-legend-${i}"></div>
        </div>

        <div class="card stats-chart-card">
          <h3 class="dash-section-title" style="margin-bottom: 0.5rem;">Répartition des Scores</h3>
          <div class="chart-wrap">
            <canvas id="chart-bars-${i}" width="300" height="300"></canvas>
          </div>
        </div>

        <div class="card stats-chart-card stats-chart-full">
          <h3 class="dash-section-title" style="margin-bottom: 0.5rem;">Top Compétences & Mots-clés</h3>
          <div class="skill-bars-wrap" id="skill-bars-wrap-${i}"></div>
        </div>

        <div class="stats-kpi-row">
          <div class="stats-kpi-card">
            <div class="stats-kpi-icon" style="background:rgba(59,130,246,0.1);color:#3b82f6">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </div>
            <div class="stats-kpi-val" id="kpi-read-time-${i}">—</div>
            <div class="stats-kpi-label">Temps de lecture estimé</div>
          </div>

          <div class="stats-kpi-card">
            <div class="stats-kpi-icon" style="background:rgba(16,185,129,0.1);color:#10b981">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
            </div>
            <div class="stats-kpi-val" id="kpi-skills-count-${i}">—</div>
            <div class="stats-kpi-label">Compétences clés</div>
          </div>

          <div class="stats-kpi-card">
            <div class="stats-kpi-icon" style="background:rgba(249,115,22,0.1);color:#f97316">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <polygon
                  points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
              </svg>
            </div>
            <div class="stats-kpi-val" id="kpi-impact-score-${i}">—</div>
            <div class="stats-kpi-label">Score d'impact</div>
          </div>

          <div class="stats-kpi-card">
            <div class="stats-kpi-icon" style="background:rgba(139,92,246,0.1);color:#8b5cf6">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
            </div>
            <div class="stats-kpi-val" id="kpi-ats-rank-${i}">—</div>
            <div class="stats-kpi-label">Classement estimé</div>
          </div>
        </div>
      </div>

      <div class="profile-grid">
        <div class="profile-summary-section">
          <h3 class="dash-section-title" style="margin-bottom: 1rem;">Résumé du Profil</h3>
          <p id="profile-summary-${i}" class="summary-text">Analyse du profil en cours...</p>
        </div>
        <div class="profile-skills-section">
          <h3 class="dash-section-title" style="margin-bottom: 1rem;">Compétences clés</h3>
          <div id="skills-list-${i}" class="skills-wrap"></div>
        </div>
      </div>

      <div class="profile-grid">
        <div class="card">
          <div class="swot-section-title green">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
            Points forts
          </div>
          <ul id="list-strengths-${i}" class="swot-list"></ul>

          <div class="swot-section-title red" style="margin-top: 1.5rem;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <circle cx="12" cy="12" r="10" />
              <line x1="15" y1="9" x2="9" y2="15" />
              <line x1="9" y1="9" x2="15" y2="15" />
            </svg>
            Points faibles
          </div>
          <ul id="list-weaknesses-${i}" class="swot-list"></ul>
        </div>

        <div class="card">
          <h3 class="dash-section-title" style="margin-bottom: 1rem;">Recommandations d'optimisation</h3>
          <ul id="list-reco-${i}" class="reco-list"></ul>
        </div>
      </div>
    </div>
  `;
}

// Affiche une carte détaillée par CV analysé, chacune identifiée par son propre nom de fichier.
// `results` : [{ fileName, data }, ...]
function renderAllResults(results) {
  const container = $('results-cards');
  if (!container || !results || !results.length) return;

  const nameOf = r => r.data?.candidate_name || r.fileName;

  container.innerHTML = results.map((r, i) => buildResultCardHTML(i, nameOf(r))).join('');

  results.forEach((r, i) => renderResultCard(i, r.fileName, r.data));

  const area = $('results-area');
  if (area) {
    area.style.display = '';
    if ($('export-file-label')) {
      $('export-file-label').textContent = results.length > 1
        ? `Rapport d'analyse — ${results.length} CV`
        : (nameOf(results[0]) || "Rapport d'analyse");
    }
    setTimeout(() => area.scrollIntoView({ behavior: 'smooth' }), 150);
  }
}

// Remplit une carte de résultat individuelle (index i) avec les données du CV.
function renderResultCard(i, fileName, data) {
  const ats = data.ats_score ?? data.scores?.ats ?? 0;
  const match = data.scores?.match ?? null;

  if ($(`ats-num-${i}`)) $(`ats-num-${i}`).textContent = ats;
  animateRing($(`ring-ats-${i}`), ats, 100);
  if ($(`badge-ats-${i}`)) $(`badge-ats-${i}`).textContent = `ATS ${ats}/100`;

  if (match !== null) {
    if ($(`match-num-${i}`)) $(`match-num-${i}`).textContent = match;
    animateRing($(`ring-match-${i}`), match, 100);
    if ($(`score-match-card-${i}`)) $(`score-match-card-${i}`).style.display = '';
    if ($(`badge-match-${i}`)) { $(`badge-match-${i}`).textContent = `Match ${match}%`; $(`badge-match-${i}`).style.display = ''; }
  } else {
    if ($(`score-match-card-${i}`)) $(`score-match-card-${i}`).style.display = 'none';
    if ($(`badge-match-${i}`)) $(`badge-match-${i}`).style.display = 'none';
  }

  const profile = data.candidate_profile || {};
  if ($(`profile-summary-${i}`)) $(`profile-summary-${i}`).textContent = profile.summary || '—';
  if ($(`skills-list-${i}`)) $(`skills-list-${i}`).innerHTML = (profile.skills || [])
    .map(s => `<span class="skill-tag">${esc(s)}</span>`).join('');

  if ($(`list-strengths-${i}`)) $(`list-strengths-${i}`).innerHTML = (data.strengths || [])
    .map(t => `<li>${esc(t)}</li>`).join('');
  if ($(`list-weaknesses-${i}`)) $(`list-weaknesses-${i}`).innerHTML = (data.weaknesses || [])
    .map(t => `<li>${esc(t)}</li>`).join('');
  if ($(`list-reco-${i}`)) $(`list-reco-${i}`).innerHTML = (data.recommendations || [])
    .map((r, ri) => `<li><span class="reco-num">${ri + 1}</span>${esc(r)}</li>`).join('');

  setTimeout(() => renderStatistics(i, data), 100);
}

function animateRing(ring, value, max) {
  if (!ring) return;
  const r = 60, circ = 2 * Math.PI * r;
  ring.style.strokeDasharray = `${circ}`;
  ring.style.strokeDashoffset = `${circ * (1 - Math.min(value / max, 1))}`;
}

// ═══════════════════════════════════════════════════════
//  CHART UTILITIES
// ═══════════════════════════════════════════════════════
function destroyChart(id) {
  if (state.charts[id]) {
    state.charts[id].destroy();
    delete state.charts[id];
  }
}

// ═══════════════════════════════════════════════════════
//  STATISTICS CHARTS — Nouvelle analyse (radar + barres + canvas natif)
//  Chaque graphique est identifié par l'index `i` de la carte CV concernée.
// ═══════════════════════════════════════════════════════
function renderStatistics(i, data) {
  const ats = data.ats_score ?? data.scores?.ats ?? 0;
  const match = data.scores?.match ?? null;
  const skills = data.candidate_profile?.skills || [];
  const nStr = (data.strengths || []).length;

  const dims = {
    'ATS Global': ats,
    'Compétences': Math.min(100, 50 + skills.length * 7),
    'Structure': Math.min(100, ats * 0.9 + 5),
    'Impact': Math.min(100, nStr * 18 + ats * 0.3),
    'Lisibilité': Math.min(100, ats * 0.85 + 8),
    'Matching': match !== null ? match : Math.round(ats * 0.88),
  };

  drawRadar(dims, i);
  drawBarChart(dims, i);
  drawSkillBars(skills, i);
  renderKPIs(ats, match, skills, i);
}

function drawRadar(dims, i) {
  const canvas = $(`chart-radar-${i}`);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 28;
  const labels = Object.keys(dims);
  const values = Object.values(dims);
  const n = labels.length;
  const step = (2 * Math.PI) / n, start = -Math.PI / 2;
  const colors = ['#f97316', '#fb923c', '#8b5cf6', '#10b981', '#3b82f6', '#f59e0b'];

  for (let ring = 1; ring <= 5; ring++) {
    const rr = R * ring / 5;
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const a = start + i * step;
      i === 0 ? ctx.moveTo(cx + rr * Math.cos(a), cy + rr * Math.sin(a))
        : ctx.lineTo(cx + rr * Math.cos(a), cy + rr * Math.sin(a));
    }
    ctx.closePath();
    ctx.strokeStyle = 'rgba(249,115,22,0.12)'; ctx.lineWidth = 1; ctx.stroke();
  }
  for (let i = 0; i < n; i++) {
    const a = start + i * step;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a));
    ctx.strokeStyle = 'rgba(249,115,22,0.15)'; ctx.lineWidth = 1; ctx.stroke();
  }
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const a = start + i * step, v = (values[i] / 100) * R;
    i === 0 ? ctx.moveTo(cx + v * Math.cos(a), cy + v * Math.sin(a))
      : ctx.lineTo(cx + v * Math.cos(a), cy + v * Math.sin(a));
  }
  ctx.closePath();
  ctx.fillStyle = 'rgba(249,115,22,0.18)'; ctx.fill();
  ctx.strokeStyle = '#f97316'; ctx.lineWidth = 2; ctx.stroke();
  for (let i = 0; i < n; i++) {
    const a = start + i * step, v = (values[i] / 100) * R;
    ctx.beginPath(); ctx.arc(cx + v * Math.cos(a), cy + v * Math.sin(a), 4, 0, 2 * Math.PI);
    ctx.fillStyle = colors[i]; ctx.fill();
  }
  ctx.font = '600 11px Inter, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  for (let i = 0; i < n; i++) {
    const a = start + i * step;
    ctx.fillStyle = '#78614a';
    ctx.fillText(labels[i], cx + (R + 18) * Math.cos(a), cy + (R + 18) * Math.sin(a));
  }
  const leg = $(`radar-legend-${i}`);
  if (leg) leg.innerHTML = labels.map((l, li) =>
    `<div class="legend-item"><div class="legend-dot" style="background:${colors[li]}"></div>
     <span>${l}: <strong>${Math.round(values[li])}</strong></span></div>`).join('');
}

function drawBarChart(dims, i) {
  const canvas = $(`chart-bars-${i}`);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const labels = Object.keys(dims), values = Object.values(dims), n = labels.length;
  const pL = 12, pR = 12, pT = 16, pB = 44;
  const cW = W - pL - pR, cH = H - pT - pB;
  const gap = cW / n, bW = gap * 0.6;
  const colors = ['#f97316', '#fb923c', '#8b5cf6', '#10b981', '#3b82f6', '#f59e0b'];

  ctx.beginPath(); ctx.moveTo(pL, pT + cH); ctx.lineTo(pL + cW, pT + cH);
  ctx.strokeStyle = 'rgba(249,115,22,0.2)'; ctx.lineWidth = 1.5; ctx.stroke();
  for (let g = 0; g <= 4; g++) {
    const y = pT + cH - cH * g / 4;
    ctx.beginPath(); ctx.moveTo(pL, y); ctx.lineTo(pL + cW, y);
    ctx.strokeStyle = 'rgba(249,115,22,0.07)'; ctx.lineWidth = 1; ctx.stroke();
    ctx.font = '10px Inter,sans-serif'; ctx.fillStyle = '#b8a08a'; ctx.textAlign = 'right';
    ctx.fillText(g * 25, pL - 2, y + 4);
  }
  values.forEach((val, i) => {
    const x = pL + gap * i + (gap - bW) / 2;
    const bH = (val / 100) * cH, y = pT + cH - bH;
    const grad = ctx.createLinearGradient(0, y, 0, y + bH);
    grad.addColorStop(0, colors[i]); grad.addColorStop(1, colors[i] + '55');
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, bW, bH, 4); else ctx.rect(x, y, bW, bH);
    ctx.fillStyle = grad; ctx.fill();
    ctx.font = '600 11px Inter,sans-serif'; ctx.fillStyle = '#1a1007'; ctx.textAlign = 'center';
    ctx.fillText(Math.round(val), x + bW / 2, y - 5);
    ctx.font = '10px Inter,sans-serif'; ctx.fillStyle = '#78614a';
    const lbl = labels[i].length > 8 ? labels[i].slice(0, 7) + '…' : labels[i];
    ctx.fillText(lbl, x + bW / 2, pT + cH + 16);
  });
}

function drawSkillBars(skills, i) {
  const wrap = $(`skill-bars-wrap-${i}`);
  if (!wrap) return;
  if (!skills || skills.length === 0) {
    wrap.innerHTML = '<p style="color:var(--text-3);font-size:.8rem">Aucune compétence détectée.</p>'; return;
  }
  const scored = skills.map((skill, i) => {
    const seed = skill.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
    return { skill, pct: 60 + ((seed + i * 17) % 38) };
  }).sort((a, b) => b.pct - a.pct).slice(0, 10);

  wrap.innerHTML = scored.map(({ skill, pct }) =>
    `<div class="skill-bar-row">
       <div class="skill-bar-label" title="${esc(skill)}">${esc(skill)}</div>
       <div class="skill-bar-track"><div class="skill-bar-fill" data-pct="${pct}" style="width:0%"></div></div>
       <div class="skill-bar-pct">${pct}%</div>
     </div>`).join('');

  requestAnimationFrame(() => {
    wrap.querySelectorAll('.skill-bar-fill').forEach(el => { el.style.width = el.dataset.pct + '%'; });
  });
}

function renderKPIs(ats, match, skills, i) {
  const readMin = ats >= 75 ? '2 min' : ats >= 50 ? '3 min' : '4 min';
  const impact = match !== null ? Math.round(ats * 0.6 + match * 0.4) : Math.round(ats * 0.85);
  const rank = ats >= 80 ? 'Top 15%' : ats >= 65 ? 'Top 35%' : ats >= 50 ? 'Top 55%' : 'Top 75%';
  if ($(`kpi-read-time-${i}`)) $(`kpi-read-time-${i}`).textContent = readMin;
  if ($(`kpi-skills-count-${i}`)) $(`kpi-skills-count-${i}`).textContent = skills.length || '—';
  if ($(`kpi-impact-score-${i}`)) $(`kpi-impact-score-${i}`).textContent = impact + '/100';
  if ($(`kpi-ats-rank-${i}`)) $(`kpi-ats-rank-${i}`).textContent = rank;
}

// ═══════════════════════════════════════════════════════
//  OVERVIEW CHARTS — Vue d'ensemble (Chart.js)
// ═══════════════════════════════════════════════════════
function renderOverviewCharts() {
  const chartsGrid = $('dash-overview-charts-grid');
  const emptyState = $('overview-charts-empty');

  if (!state.history || state.history.length === 0) {
    if (chartsGrid) chartsGrid.style.display = 'none';
    if (emptyState) emptyState.style.display = 'flex';
    return;
  }
  if (chartsGrid) chartsGrid.style.display = 'grid';
  if (emptyState) emptyState.style.display = 'none';

  if (typeof Chart === 'undefined') {
    console.warn('Chart.js non chargé — impossible d\'afficher les graphiques.');
    return;
  }

  // Historique dans l'ordre chronologique (le plus ancien en premier)
  const hist = [...state.history].reverse();

  drawTrendChart(hist);
  drawDistributionChart(hist);
  drawMatchQualityChart(hist);
}

function cleanEmail(email) {
  if (!email) return '';
  email = email.trim();
  const match = email.match(/([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
  if (!match) return email;
  let username = match[1];
  let domain = match[2];

  const phoneMatch = username.match(/\d{8,}/);
  if (phoneMatch) {
    const idx = username.indexOf(phoneMatch[0]) + phoneMatch[0].length;
    username = username.slice(idx);
  }
  username = username.replace(/^[^a-zA-Z0-9]+/, '');

  // Strip common noise words that PDF extraction concatenates before the real email
  const noiseWords = [
    'maroc', 'morocco', 'casablanca', 'rabat', 'tanger', 'tangier', 'fes', 'fez',
    'agadir', 'meknes', 'oujda', 'kenitra', 'tetouan', 'safi', 'nador', 'settat',
    'khouribga', 'mohammedia', 'eljadida', 'benimlal', 'benimellal', 'taza',
    'france', 'paris', 'lyon', 'marseille', 'toulouse', 'bordeaux', 'lille',
    'canada', 'belgique', 'tunisie', 'algerie', 'suisse', 'espagne', 'spain'
  ];
  const userLower = username.toLowerCase();
  for (const word of noiseWords) {
    if (userLower.startsWith(word) && username.length > word.length) {
      const rest = username.slice(word.length).replace(/^[^a-zA-Z0-9]+/, '');
      if (rest) { username = rest; break; }
    }
  }

  // Clean domain suffix
  const domainMatch = domain.match(/^([a-zA-Z0-9.-]+\.(?:com|fr|ma|net|org|io|co|edu|gov|info|mil))([a-zA-Z]*)$/i);
  if (domainMatch) {
    domain = domainMatch[1];
  }
  return `${username}@${domain}`.toLowerCase();
}

// ═══════════════════════════════════════════════════════
//  CARTES DE PROFIL CV — Vue d'ensemble, un aperçu par CV
//  analysé (nom, résumé, compétences), dans le style d'une
//  carte d'offre d'emploi.
// ═══════════════════════════════════════════════════════
function renderCvProfileCards() {
  const section = $('cv-profile-section');
  const grid = $('cv-profile-cards-grid');
  if (!section || !grid) return;

  if (!state.history || state.history.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  const clockIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;
  const gradIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 10L12 5 2 10l10 5 10-5v6"/><path d="M6 12v5c0 1.5 2.5 3 6 3s6-1.5 6-3v-5"/></svg>`;
  const peopleIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`;
  const mailIcon = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v16H4z" opacity="0"/><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 6l-10 7L2 6"/></svg>`;
  const phoneIcon = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`;
  const pinIcon = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`;

  const truncateAtWord = (text, maxLen) => {
    if (text.length <= maxLen) return text;
    const cut = text.slice(0, maxLen);
    const lastSpace = cut.lastIndexOf(' ');
    return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut) + '…';
  };

  grid.innerHTML = state.history.map(h => {
    const name = esc(h.candidateName || h.fileName);
    const summary = esc(truncateAtWord(h.data?.summary || 'Analyse en cours ou résumé indisponible.', 90));
    const offerSkills = getJobOfferSkills(h, null);
    const skills = (h.data?.skills || []).slice(0, 4);
    const extraSkillsCount = (h.data?.skills || []).length - skills.length;
    const tagsHtml = skills.map(s => renderSkillChip(s, offerSkills, 'cv-profile-tag')).join('')
      + (extraSkillsCount > 0 ? `<span class="cv-profile-tag">+${extraSkillsCount}</span>` : '');
    const atsColor = h.atsScore >= 75 ? '#059669' : h.atsScore >= 50 ? '#ea580c' : '#dc2626';

    const contactLines = [
      h.email ? `<span style="display:inline-flex;align-items:center;gap:0.25rem;word-break:break-all">${mailIcon}${esc(cleanEmail(h.email))}</span>` : '',
      h.phone ? `<span>${phoneIcon}${esc(h.phone)}</span>` : '',
      h.city ? `<span>${pinIcon}${esc(h.city)}</span>` : '',
    ].filter(Boolean).join('');
    const contactBlock = contactLines
      ? `<div style="display:flex;flex-wrap:wrap;gap:0.6rem;font-size:0.74rem;color:var(--text-3,#78614a);margin-top:-0.2rem">${contactLines}</div>`
      : '';

    return `
      <div class="cv-profile-card" onclick="openFullAnalysisModal(${h.id})">
        <div class="cv-profile-card-top">
          <span class="cv-profile-status"><span class="dot"></span>ACTIVE</span>
          <button class="cv-profile-delete" title="Supprimer" onclick="event.stopPropagation(); deleteHistoryEntry(${h.id})">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
        ${h.job_reference ? `
        <div style="font-family:'Inter',sans-serif;font-size:0.65rem;font-weight:700;color:var(--orange,#ea9d73ff);letter-spacing:0.07em;text-transform:uppercase;margin:0.25rem 0 0.15rem 0;">
          Réf : ${esc(h.job_reference)}
        </div>
        ` : ''}
        <h4 class="cv-profile-title" style="margin-top:0;">${name}</h4>
        ${contactBlock}
        <p class="cv-profile-desc">${summary}</p>
        <div class="cv-profile-meta">
          <span>${clockIcon}${esc(h.date)}</span>
        </div>
        <div class="cv-profile-tags">${tagsHtml || '<span class="cv-profile-tag">Aucune compétence détectée</span>'}</div>
        <div class="cv-profile-footer">
          <span class="cv-profile-score" style="color:${atsColor}">${peopleIcon}ATS ${h.atsScore}/100</span>
          <span class="cv-profile-link">Accéder →</span>
        </div>
      </div>
    `;
  }).join('');
}

function drawTrendChart(hist) {
  destroyChart('chart-overview-trend');
  const ctx = $('chart-overview-trend')?.getContext('2d');
  if (!ctx) return;

  const shortName = n => n.length > 16 ? n.slice(0, 15) + '…' : n;
  const displayName = h => h.candidateName || h.fileName;

  state.charts['chart-overview-trend'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: hist.map(h => shortName(displayName(h))),
      datasets: [{
        data: hist.map(h => h.atsScore),
        borderColor: '#f97316',
        backgroundColor: 'rgba(249,115,22,0.1)',
        fill: true,
        tension: 0.35,
        borderWidth: 2,
        pointRadius: 3,
        pointHoverRadius: 6,
        pointHitRadius: 10,
        pointBackgroundColor: '#f97316'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      onHover: (event, elements) => {
        event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
      },
      onClick: (event, elements) => {
        if (!elements.length) return;
        const entry = hist[elements[0].index];
        if (!entry) return;
        switchPanel('resumes');
        if (typeof renderOverviewTable === 'function') renderOverviewTable();
        // Laisse le panneau Mes CV finir de s'afficher avant d'ouvrir le détail
        setTimeout(() => { if (typeof openDrawer === 'function') openDrawer(entry.id); }, 50);
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => displayName(hist[items[0].dataIndex]),
            label: item => `Score ATS : ${item.raw}/100`,
            afterLabel: item => `Date : ${hist[item.dataIndex].date}\nCliquez pour voir le détail`
          }
        }
      },
      scales: {
        y: { min: 0, max: 100, ticks: { stepSize: 20 } },
        x: { ticks: { autoSkip: true, maxRotation: 45 } }
      }
    }
  });
}

function drawDistributionChart(hist) {
  destroyChart('chart-overview-distribution');
  const ctx = $('chart-overview-distribution')?.getContext('2d');
  if (!ctx) return;

  const buckets = { '0-40': [], '40-60': [], '60-80': [], '80-100': [] };
  hist.forEach(h => {
    const s = h.atsScore;
    const name = h.candidateName || h.fileName;
    if (s < 40) buckets['0-40'].push(name);
    else if (s < 60) buckets['40-60'].push(name);
    else if (s < 80) buckets['60-80'].push(name);
    else buckets['80-100'].push(name);
  });
  const bucketKeys = Object.keys(buckets);

  state.charts['chart-overview-distribution'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: bucketKeys,
      datasets: [{
        data: bucketKeys.map(k => buckets[k].length),
        backgroundColor: '#f97316',
        borderRadius: 4,
        maxBarThickness: 28
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: item => `${item.raw} CV`,
            footer: items => {
              const names = buckets[bucketKeys[items[0].dataIndex]];
              return names.length ? names.join('\n') : '';
            }
          }
        }
      },
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1 } },
        x: { grid: { display: false } }
      }
    }
  });
}

function drawMatchQualityChart(hist) {
  destroyChart('chart-overview-match');
  const ctx = $('chart-overview-match')?.getContext('2d');
  if (!ctx) return;

  const withMatch = hist.filter(h => h.matchScore !== null && h.matchScore !== undefined);

  if (withMatch.length === 0) {
    const wrap = ctx.canvas.parentElement;
    wrap.innerHTML = '<p style="font-size:13px;color:var(--text-3);text-align:center;padding-top:60px;">Aucune analyse avec offre d\'emploi renseignée.</p>';
    if ($('overview-match-legend')) $('overview-match-legend').innerHTML = '';
    return;
  }

  let high = [], medium = [], low = [];
  withMatch.forEach(h => {
    const name = h.candidateName || h.fileName;
    if (h.matchScore >= 75) high.push(name);
    else if (h.matchScore >= 50) medium.push(name);
    else low.push(name);
  });
  const groups = [high, medium, low];
  const total = withMatch.length;

  state.charts['chart-overview-match'] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Élevé', 'Moyen', 'Faible'],
      datasets: [{
        data: [high.length, medium.length, low.length],
        backgroundColor: ['#10b981', '#f59e0b', '#ef4444'],
        borderColor: '#fff',
        borderWidth: 2
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: item => `${item.label} : ${item.raw} CV`,
            footer: items => {
              const names = groups[items[0].dataIndex];
              return names.length ? names.join('\n') : '';
            }
          }
        }
      },
      cutout: '65%'
    }
  });

  const pct = n => Math.round((n / total) * 100);
  if ($('overview-match-legend')) {
    $('overview-match-legend').innerHTML = `
      <span class="legend-item"><span class="legend-dot" style="background:#10b981"></span>Élevé ${pct(high.length)}%</span>
      <span class="legend-item"><span class="legend-dot" style="background:#f59e0b"></span>Moyen ${pct(medium.length)}%</span>
      <span class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>Faible ${pct(low.length)}%</span>
    `;
  }
}

// ═══════════════════════════════════════════════════════
//  STATS & HISTORY
// ═══════════════════════════════════════════════════════
async function refreshStats() {
  try {
    let kpis = { total_resumes: 0, analyzed_resumes: 0, average_ats_score: 0, average_match_score: 0 };
    let history = [];

    if (typeof ApiService !== 'undefined') {
      try {
        kpis = await ApiService.getDashboardKPIs();
        history = await ApiService.getHistory();
        state.history = history
          .filter(h => !pendingDeletions[h.id])
          .map(h => ({
            id: h.id,
            fileName: h.filename,
            candidateName: h.candidate_name || h.filename,
            date: new Date(h.created_at).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }),
            atsScore: h.overall_score,
            matchScore: h.match_score,
            status: h.status,
            reviewStatus: h.review_status || 'a_etudier',
            email: h.email || '',
            phone: h.phone || '',
            city: h.city || '',
            jobTitleAbbr: state.sessionJobTitleAbbrs[h.filename] || '',
            jobSkills: state.sessionJobSkills[h.filename] || [],
            job_title: h.job_title,
            job_reference: h.job_reference || '',
            data: { summary: h.summary, skills: h.skills || [], education: h.education || [], education_level: h.education_level || '', job_description_match: h.job_description_match }
          }));
        state.backendOnline = true;
      } catch (apiErr) {
        console.warn('refreshStats: backend unreachable, using local fallback:', apiErr);
        kpis = {
          total_resumes: state.history.length,
          analyzed_resumes: state.history.length,
          average_ats_score: state.history.length ? Math.round(state.history.reduce((s, h) => s + h.atsScore, 0) / state.history.length) : 0,
          average_match_score: 0
        };
      }
    }

    if ($('stat-total')) $('stat-total').textContent = kpis.total_resumes;
    if ($('stat-analyzed')) $('stat-analyzed').textContent = kpis.analyzed_resumes;
    if ($('stat-avg-ats')) $('stat-avg-ats').textContent = kpis.average_ats_score > 0 ? kpis.average_ats_score + '/100' : '—';
    if ($('stat-avg-match')) $('stat-avg-match').textContent = kpis.average_match_score > 0 ? kpis.average_match_score + '%' : '—';

    renderOverviewTable();
    renderOverviewCharts();
    renderCvProfileCards();

  } catch (error) {
    console.error("Failed to refresh stats:", error);
  }
}

function getCandidateDiploma(h) {
  // Priorité 1 : niveau normalisé stocké par l'IA (ex: "Bac+5", "Licence", "Master")
  const eduLevel = (h.data?.education_level || '').trim();
  if (eduLevel) return eduLevel;

  // Priorité 2 : niveau détecté via la vérification de conformité diplôme (si offre présente)
  const dc = h.data?.job_description_match?.diploma_check;
  if (dc?.detected) return dc.detected;

  // Priorité 3 : premier diplôme de la liste education[]
  const eduList = h.data?.education || [];
  if (Array.isArray(eduList) && eduList.length > 0) {
    const first = eduList[0];
    if (typeof first === 'string' && first.trim()) return first.trim();
    if (first && typeof first === 'object') {
      const degree = (first.degree || first.title || first.diploma || '').trim();
      if (degree) return degree;
    }
  }

  return 'Non spécifié';
}

function renderOverviewTable() {
  const body = $('overview-table-body');
  const empty = $('overview-history-empty');
  const table = $('overview-table');
  const pagination = $('saas-pagination-controls');

  if (!body) return;

  const displayed = applyResumesToolbar(state.history);

  if (state.history.length === 0) {
    if (empty) empty.style.display = 'flex';
    if (table) table.style.display = 'none';
    if (pagination) pagination.style.display = 'none';
    return;
  }

  if (empty) empty.style.display = 'none';
  if (table) table.style.display = 'table';
  if (pagination) pagination.style.display = 'flex';

  if (displayed.length === 0) {
    body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-3);padding:2rem 1rem">Aucun CV ne correspond à votre recherche ou à ce filtre.</td></tr>`;
    return;
  }

  const REVIEW_STATUS_LABELS = { a_etudier: 'À étudier', en_entretien: 'En entretien', refuse: 'Refusé' };

  body.innerHTML = displayed.map((h, i) => {
    const diplomaCheck = h.data?.job_description_match?.diploma_check;
    const nonConforme = diplomaCheck && !diplomaCheck.conforms;

    const diplomaBadge = diplomaCheck
      ? (diplomaCheck.conforms
        ? `<div style="display:inline-flex;align-items:center;gap:0.3rem;font-size:0.7rem;font-weight:700;color:#059669;background:rgba(5,150,105,0.1);padding:0.2rem 0.5rem;border-radius:6px;margin-top:0.25rem;width:fit-content">✓ DIPLÔME CONFORME</div>`
        : `<div style="display:inline-flex;align-items:center;gap:0.3rem;font-size:0.7rem;font-weight:700;color:#dc2626;background:rgba(220,38,38,0.1);padding:0.2rem 0.5rem;border-radius:6px;margin-top:0.25rem;width:fit-content">⚠️ DIPLÔME NON CONFORME</div>
             <div style="font-size:0.65rem;color:var(--text-3);font-style:italic;margin-top:0.15rem">Estimation automatique, à vérifier manuellement</div>`)
      : '';

    const detectedDiploma = getCandidateDiploma(h);
    const diplomaCell = `<div style="font-size:0.8rem;font-weight:600;color:var(--text-1);max-width:180px;line-height:1.3" title="${esc(detectedDiploma)}">🎓 ${esc(detectedDiploma)}</div>`;

    // Signal = pourcentage de match si disponible, sinon score ATS
    const signalValue = h.matchScore !== null && h.matchScore !== undefined ? h.matchScore : h.atsScore;
    const signalIsLow = signalValue < 50;
    const signalColor = signalIsLow ? '#dc2626' : '#059669';
    const signalBg = signalIsLow ? 'rgba(220,38,38,0.08)' : 'rgba(5,150,105,0.08)';
    const barsHtml = `<svg viewBox="0 0 20 16" width="16" height="13" fill="${signalColor}"><rect x="0" y="9" width="3" height="7" rx="1"/><rect x="6" y="5" width="3" height="11" rx="1"/><rect x="12" y="2" width="3" height="14" rx="1"/><rect x="17" y="7" width="3" height="9" rx="1" opacity="${signalIsLow ? '0.3' : '1'}"/></svg>`;
    const signalCell = `<span style="display:inline-flex;align-items:center;gap:0.4rem;font-weight:700;color:${signalColor};background:${signalBg};border:1.5px solid ${signalColor}33;padding:0.3rem 0.65rem;border-radius:99px;font-size:0.82rem">${barsHtml}${signalValue}%</span>`;

    // Recommandation : le message de diplôme prime s'il existe, sinon un texte générique basé sur le score
    let recoText, recoColor, recoIcon;
    if (diplomaCheck) {
      recoText = diplomaCheck.message;
      recoColor = diplomaCheck.conforms ? '#059669' : '#dc2626';
      recoIcon = diplomaCheck.conforms ? '✓' : '⚠️';
    } else if (signalValue >= 70) {
      recoText = 'Profil très intéressant, à contacter en priorité';
      recoColor = '#059669'; recoIcon = '✓';
    } else if (signalValue >= 50) {
      recoText = 'Profil intéressant, à évaluer';
      recoColor = '#44372a'; recoIcon = 'ℹ️';
    } else {
      recoText = 'Profil peu adapté au poste';
      recoColor = '#dc2626'; recoIcon = '⚠️';
    }
    const recoCell = `<div style="font-size:0.82rem;color:${recoColor};max-width:280px">${recoIcon} ${esc(recoText)}</div>${diplomaCheck ? '<div style="font-size:0.65rem;color:var(--text-3);font-style:italic;margin-top:0.15rem">Estimation automatique, à vérifier manuellement</div>' : ''}`;

    // Skills : vraies compétences détectées sur le CV du candidat
    const candidateSkills = h.data?.skills || [];
    const offerSkills = getJobOfferSkills(h, null);
    const skillsCell = candidateSkills.length
      ? candidateSkills.slice(0, 3).map(s => renderSkillChip(s, offerSkills)).join('')
      + (candidateSkills.length > 3 ? `<span style="font-size:0.72rem;color:var(--text-3)">+${candidateSkills.length - 3}</span>` : '')
      : '<span style="color:var(--text-3);font-size:0.78rem">—</span>';

    const currentReviewStatus = h.reviewStatus || 'a_etudier';
    const statusOptions = Object.entries(REVIEW_STATUS_LABELS)
      .map(([val, label]) => `<option value="${val}" ${val === currentReviewStatus ? 'selected' : ''}>${label}</option>`).join('');

    return `
      <tr onclick="openFullAnalysisModal(${h.id})" style="${nonConforme ? 'background:rgba(220,38,38,0.04)' : ''}">
        <td>
          <span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:8px;background:${nonConforme ? '#dc2626' : '#1a1007'};color:#fff;font-weight:700;font-size:0.8rem">#${i + 1}</span>
        </td>
        <td>
          <strong style="color:var(--text-1);display:block">${esc(h.candidateName || h.fileName)}</strong>
          ${diplomaBadge}
          <div style="font-size:0.72rem;color:var(--text-3);margin-top:0.2rem;display:flex;align-items:center;gap:0.25rem">
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/></svg>
            uploads\\${esc(h.fileName)}
          </div>
        </td>
        <td>${diplomaCell}</td>
        <td>${signalCell}</td>
        <td>${recoCell}</td>
        <td>${skillsCell}</td>
        <td onclick="event.stopPropagation()">
          <div style="display:flex;align-items:center;gap:0.5rem">
            <select onchange="updateReviewStatus(${h.id}, this.value)"
              style="padding:0.4rem 0.6rem;border:1.5px solid #e7e0d8;border-radius:8px;font-size:0.78rem;font-weight:600;font-family:'Inter',sans-serif;background:#fff;cursor:pointer">
              ${statusOptions}
            </select>
            <span id="status-msg-${h.id}" style="font-size:0.72rem;font-weight:700;display:inline;"></span>
          </div>
        </td>
        <td style="text-align:right" onclick="event.stopPropagation()">
          <div style="display:flex;gap:0.4rem;justify-content:flex-end">
            <button class="secondary-btn" style="padding:0.4rem 0.6rem" title="Voir la fiche" onclick="openFullAnalysisModal(${h.id})">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
            <button class="secondary-btn" style="padding:0.4rem 0.6rem;color:#dc2626;border-color:rgba(220,38,38,0.3)" title="Supprimer" onclick="deleteHistoryEntry(${h.id})">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  // Appliquer le style initial des messages de statut
  state.history.forEach(h => {
    updateStatusMessageUI(h.id, h.review_status || 'a_etudier');
  });
}

function updateStatusMessageUI(id, status) {
  const msgEl = $(`status-msg-${id}`);
  if (!msgEl) return;
  const config = {
    refuse: { text: 'Refusé', color: '#dc2626' },
    retenu: { text: 'Retenu', color: '#16a34a' },
    a_etudier: { text: 'À étudier', color: '#ea9d73ff' },
    en_entretien: { text: 'En entretien', color: '#2563eb' }
  }[status] || { text: '', color: 'transparent' };

  msgEl.textContent = config.text;
  msgEl.style.color = config.color;
  msgEl.style.display = 'inline';
}

// Met à jour le statut de suivi RH (À étudier / Retenu / Refusé / En entretien) d'une candidature.
async function updateReviewStatus(id, newStatus) {
  const entry = state.history.find(h => h.id === id);
  if (entry) entry.reviewStatus = newStatus; // mise à jour optimiste de l'affichage

  updateStatusMessageUI(id, newStatus);

  if (state.backendOnline) {
    try {
      const res = await fetch(`${BACKEND_URL}/api/history/${id}/status`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ review_status: newStatus })
      });
      const data = await res.json();
      if (newStatus === 'refuse') {
        if (data.email_sent) {
          toast('Statut mis à jour : email de refus envoyé au candidat.', 's');
        } else if (data.email_error) {
          toast(`Statut refusé enregistré (${data.email_error}).`, 'w');
        } else {
          toast('Statut mis à jour.', 's');
        }
      } else {
        toast('Statut mis à jour.', 's');
      }

      // Si le statut est "En entretien", on ouvre la modale de planification
      if (newStatus === 'en_entretien') {
        openInterviewModal(id);
      }
    } catch (e) {
      console.error(e);
      toast('Erreur lors de la mise à jour du statut.', 'e');
    }
  }
}

async function openDrawer(id) {
  const drawer = $('saas-cv-drawer');
  const overlay = $('saas-drawer-overlay');
  if (!drawer || !overlay) return;

  const entry = state.history.find(h => h.id === id);
  if (!entry) return;

  $('drawer-candidate-name').textContent = entry.candidateName || entry.fileName;

  const scoreAtsEl = $('drawer-score-ats');
  const atsVal = parseInt(entry.atsScore, 10) || 0;
  scoreAtsEl.textContent = atsVal + '/100';
  scoreAtsEl.className = atsVal >= 75 ? 'score-green' : (atsVal >= 50 ? 'score-orange' : 'score-rose');

  const scoreMatchEl = $('drawer-score-match');
  if (entry.matchScore !== null && entry.matchScore !== undefined) {
    const matchVal = parseInt(entry.matchScore, 10) || 0;
    scoreMatchEl.textContent = matchVal + '%';
    scoreMatchEl.className = matchVal >= 75 ? 'score-green' : (matchVal >= 50 ? 'score-orange' : 'score-rose');
  } else {
    scoreMatchEl.textContent = '—';
    scoreMatchEl.className = 'text-muted';
  }

  if (state.backendOnline && typeof ApiService !== 'undefined') {
    try {
      const details = await ApiService.getAnalysisDetail(id);

      const summaryEl = $('drawer-summary');
      if (summaryEl) {
        summaryEl.textContent = details.summary || 'Aucun résumé disponible.';
        summaryEl.className = 'drawer-summary-card';
      }

      const offerSkills = getJobOfferSkills(null, details);
      const skillsHtml = (details.skills || []).map(s => renderSkillChip(s, offerSkills)).filter(Boolean).join('');
      $('drawer-skills').innerHTML = skillsHtml || '<span style="color:var(--text-3)">Non spécifié</span>';

      const projHtml = (details.projects || []).map(p => {
        const title = cleanFieldText(p.title || p.role);
        const tech = cleanFieldText(p.technologies);
        const desc = cleanFieldText(p.description);
        if (!title && !desc) return '';
        return `
          <div class="drawer-item-card">
            <div style="font-weight:700;color:var(--text-1);font-size:0.9rem">💻 ${esc(title || 'Projet')}</div>
            ${tech ? `<div style="color:var(--orange);font-weight:600;font-size:0.8rem;margin-top:2px">Techs : ${esc(tech)}</div>` : ''}
            ${desc ? `<div style="margin-top:0.35rem;font-size:0.83rem;line-height:1.45;color:var(--text-2)">${esc(desc)}</div>` : ''}
          </div>`;
      }).filter(Boolean).join('');
      if ($('drawer-projects')) $('drawer-projects').innerHTML = projHtml;
      if ($('drawer-section-projects')) $('drawer-section-projects').style.display = projHtml ? '' : 'none';

      const expHtml = (details.experience || []).map(e => {
        const role = cleanFieldText(e.role);
        const company = cleanFieldText(e.company);
        const duration = cleanFieldText(e.duration);
        const desc = cleanFieldText(e.description);
        if (!role && !desc) return '';
        return `
          <div class="drawer-item-card">
            <div style="font-weight:700;color:var(--text-1);font-size:0.9rem">💼 ${esc(role || 'Poste')}</div>
            ${company ? `<div style="color:var(--orange);font-weight:600;font-size:0.82rem;margin-top:2px">${esc(company)}</div>` : ''}
            ${duration ? `<div style="font-size:0.78rem;color:var(--text-3);margin-top:2px">${esc(duration)}</div>` : ''}
            ${desc ? `<div style="margin-top:0.4rem;font-size:0.83rem;line-height:1.45;color:var(--text-2)">${esc(desc)}</div>` : ''}
          </div>`;
      }).filter(Boolean).join('');
      $('drawer-experience').innerHTML = expHtml;
      if ($('drawer-section-experience')) $('drawer-section-experience').style.display = expHtml ? '' : 'none';

      const eduHtml = (details.education || []).map(ed => {
        const degree = cleanFieldText(ed.degree);
        const school = cleanFieldText(ed.school);
        const duration = cleanFieldText(ed.duration);
        if (!degree && !school) return '';
        return `
          <div class="drawer-item-card">
            <div style="font-weight:700;color:var(--text-1);font-size:0.9rem">🎓 ${esc(degree || 'Diplôme')}</div>
            ${school ? `<div style="color:var(--text-2);font-weight:600;font-size:0.82rem;margin-top:2px">${esc(school)}</div>` : ''}
            ${duration ? `<div style="font-size:0.78rem;color:var(--text-3);margin-top:2px">${esc(duration)}</div>` : ''}
          </div>`;
      }).filter(Boolean).join('');
      $('drawer-education').innerHTML = eduHtml;
      if ($('drawer-section-education')) $('drawer-section-education').style.display = eduHtml ? '' : 'none';

      const certsHtml = (details.certifications || []).map(c => {
        const v = cleanFieldText(typeof c === 'string' ? c : c.name || c.title || '');
        return v ? `<div class="drawer-item-card"><div style="font-weight:600;color:var(--text-1);font-size:0.85rem">📜 ${esc(v)}</div></div>` : '';
      }).filter(Boolean).join('');
      if ($('drawer-certifications')) $('drawer-certifications').innerHTML = certsHtml;
      if ($('drawer-section-certifications')) $('drawer-section-certifications').style.display = certsHtml ? '' : 'none';

      const langsHtml = (details.languages || []).map(l => {
        const v = cleanFieldText(typeof l === 'string' ? l : l.name || '');
        return v ? `<span class="skill-chip" style="background:rgba(16,185,129,0.08);color:#059669;border-color:rgba(16,185,129,0.2)">🌐 ${esc(v)}</span>` : '';
      }).filter(Boolean).join('');
      if ($('drawer-languages')) $('drawer-languages').innerHTML = langsHtml;
      if ($('drawer-section-languages')) $('drawer-section-languages').style.display = langsHtml ? '' : 'none';

      const recsHtml = (details.recommendations || []).map(r => {
        const v = cleanFieldText(r);
        return v ? `
          <li class="insight-item">
            <svg class="insight-icon" viewBox="0 0 24 24" fill="none" stroke="var(--orange)" stroke-width="2">
              <polyline points="9 11 12 14 22 4" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
            <span>${esc(v)}</span>
          </li>` : '';
      }).filter(Boolean).join('');
      $('drawer-recommendations').innerHTML = recsHtml || '<span style="color:var(--text-3)">Aucune recommandation requise.</span>';

    } catch (e) {
      console.error(e);
      $('drawer-summary').textContent = "Erreur de chargement des détails.";
    }
  }

  drawer.classList.add('active');
  overlay.classList.add('active');

  const closeBtn = $('btn-close-drawer');
  closeBtn.onclick = () => { drawer.classList.remove('active'); overlay.classList.remove('active'); };
  overlay.onclick = () => { drawer.classList.remove('active'); overlay.classList.remove('active'); };

  const downloadBtn = $('drawer-btn-download');
  if (downloadBtn) downloadBtn.onclick = () => downloadHistoryEntryPDF(id);

  $('drawer-btn-full').onclick = () => { viewHistoryEntry(id); };
}

// Génère un rapport imprimable (PDF via la boîte d'impression du navigateur)
// pour une analyse donnée, à partir de ses données déjà stockées en base.
async function downloadHistoryEntryPDF(id) {
  if (!state.backendOnline || typeof ApiService === 'undefined') {
    toast('Le backend doit être connecté pour télécharger un rapport.', 'e');
    return;
  }

  let details;
  try {
    details = await ApiService.getAnalysisDetail(id);
  } catch (e) {
    console.error(e);
    toast('Impossible de récupérer les détails de cette analyse.', 'e');
    return;
  }

  const skillsHtml = (details.skills || []).map(s => `<span class="pdf-skill">${esc(s)}</span>`).join('');
  const expHtml = (details.experience || []).map(e => `
    <div class="pdf-block">
      <strong>${esc(e.role || 'Poste non précisé')}</strong>${e.company ? ' — ' + esc(e.company) : ''}
      ${e.duration ? `<div class="pdf-sub">${esc(e.duration)}</div>` : ''}
      ${e.description ? `<div>${esc(e.description)}</div>` : ''}
    </div>`).join('') || '<p class="pdf-muted">Aucune expérience détectée.</p>';
  const eduHtml = (details.education || []).map(ed => `
    <div class="pdf-block">
      <strong>${esc(ed.degree || 'Diplôme non précisé')}</strong>${ed.school ? ' — ' + esc(ed.school) : ''}
      ${ed.duration ? `<div class="pdf-sub">${esc(ed.duration)}</div>` : ''}
    </div>`).join('') || '<p class="pdf-muted">Aucune formation détectée.</p>';
  const strengthsHtml = (details.strengths || []).map(s => `<li>${esc(s)}</li>`).join('') || '<li class="pdf-muted">—</li>';
  const weaknessesHtml = (details.weaknesses || []).map(s => `<li>${esc(s)}</li>`).join('') || '<li class="pdf-muted">—</li>';
  const recoHtml = (details.recommendations || []).map(s => `<li>${esc(s)}</li>`).join('') || '<li class="pdf-muted">—</li>';
  const matchScore = details.job_description_match?.match_score;

  const reportHtml = `
    <html>
    <head>
      <meta charset="utf-8">
      <title>Rapport d'analyse — ${esc(details.resume?.filename || 'CV')}</title>
      <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; color: #1a1007; padding: 2.5rem; max-width: 780px; margin: auto; }
        h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
        .pdf-sub-header { color: #78614a; font-size: 0.9rem; margin-bottom: 1.5rem; }
        .pdf-scores { display: flex; gap: 1.5rem; margin-bottom: 1.5rem; }
        .pdf-score-box { border: 1px solid #e7e0d8; border-radius: 10px; padding: 0.9rem 1.2rem; }
        .pdf-score-box span { display: block; font-size: 0.75rem; color: #78614a; text-transform: uppercase; }
        .pdf-score-box strong { font-size: 1.4rem; color: #ea580c; }
        h2 { font-size: 1.05rem; border-bottom: 2px solid #f97316; padding-bottom: 0.3rem; margin-top: 1.8rem; }
        .pdf-skill { display: inline-block; background: rgba(249,115,22,0.1); color: #ea580c; padding: 0.25rem 0.6rem; border-radius: 99px; font-size: 0.8rem; margin: 0 6px 6px 0; }
        .pdf-block { margin-bottom: 0.8rem; }
        .pdf-sub { font-size: 0.8rem; color: #78614a; }
        .pdf-muted { color: #b8a08a; }
        ul { padding-left: 1.2rem; }
        @media print { body { padding: 1rem; } }
      </style>
    </head>
    <body>
      <h1>${esc(details.resume?.filename || 'Rapport d\'analyse')}</h1>
      <div class="pdf-sub-header">Analysé le ${esc(details.created_at || '—')}</div>

      <div class="pdf-scores">
        <div class="pdf-score-box"><span>Score ATS</span><strong>${details.overall_score ?? 0}/100</strong></div>
        ${matchScore != null ? `<div class="pdf-score-box"><span>Matching</span><strong>${matchScore}%</strong></div>` : ''}
      </div>

      <h2>Résumé</h2>
      <p>${esc(details.summary || 'Aucun résumé disponible.')}</p>

      <h2>Compétences détectées</h2>
      <p>${skillsHtml || '<span class="pdf-muted">Aucune compétence détectée.</span>'}</p>

      <h2>Expérience</h2>
      ${expHtml}

      <h2>Formation</h2>
      ${eduHtml}

      <h2>Points forts</h2>
      <ul>${strengthsHtml}</ul>

      <h2>Points faibles</h2>
      <ul>${weaknessesHtml}</ul>

      <h2>Recommandations</h2>
      <ul>${recoHtml}</ul>
    </body>
    </html>
  `;

  const w = window.open('', '_blank', 'width=850,height=700');
  if (!w) {
    toast('Le navigateur a bloqué la fenêtre. Autorisez les pop-ups pour ce site.', 'e');
    return;
  }
  w.document.write(reportHtml);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 300);
}

// ═══════════════════════════════════════════════════════
//  SUPPRESSION AVEC ANNULATION — la suppression réelle (côté serveur)
//  n'a lieu qu'après un délai, pendant lequel on peut cliquer "Annuler".
// ═══════════════════════════════════════════════════════
const pendingDeletions = {}; // { id: { entry, index, timeoutId } }
const UNDO_DELAY_MS = 10000;

async function deleteHistoryEntry(id) {
  // Si déjà en cours de suppression (pendant les 6s), ignorer les clics répétés
  if (pendingDeletions[id]) return;

  const entryIndex = state.history.findIndex(h => h.id === id);
  if (entryIndex === -1) return;
  const entry = state.history[entryIndex];

  // Retire immédiatement de l'affichage (mais PAS encore du serveur)
  state.history.splice(entryIndex, 1);
  refreshStats();

  const timeoutId = setTimeout(() => finalizeDeletion(id), UNDO_DELAY_MS);
  pendingDeletions[id] = { entry, index: entryIndex, timeoutId };

  showUndoBar(`Candidature "${entry.candidateName || entry.fileName}" supprimée.`);
}

// Appelée une fois le délai écoulé : supprime pour de vrai, côté serveur.
async function finalizeDeletion(id) {
  const pending = pendingDeletions[id];
  if (!pending) return;
  delete pendingDeletions[id];

  if (state.backendOnline && typeof ApiService !== 'undefined') {
    try {
      await ApiService.deleteAnalysis(id);
    } catch (e) {
      console.error('Erreur lors de la suppression définitive :', e);
      const msg = e ? String(e.message || e) : '';
      if (!msg.includes('404') && !msg.includes('introuvable') && !msg.includes('supprimée')) {
        toast('Erreur lors de la suppression sur le serveur.', 'e');
        state.history.splice(pending.index, 0, pending.entry);
        refreshStats();
      }
    }
  }
  hideUndoBar();
}

function showUndoBar(message) {
  const container = $('undo-container');
  const msgEl = $('undo-message');
  if (msgEl) msgEl.textContent = message;
  if (container) container.style.display = 'block';
}

function hideUndoBar() {
  // Ne masque que s'il n'y a plus aucune suppression en attente
  if (Object.keys(pendingDeletions).length === 0) {
    const container = $('undo-container');
    if (container) container.style.display = 'none';
  }
}

function handleUndo() {
  const ids = Object.keys(pendingDeletions);
  if (ids.length === 0) return;
  // Annule la suppression la plus récente
  const id = ids[ids.length - 1];
  const pending = pendingDeletions[id];
  clearTimeout(pending.timeoutId);
  delete pendingDeletions[id];

  // Remet la candidature à sa place dans la liste affichée
  state.history.splice(pending.index, 0, pending.entry);
  refreshStats();
  toast('Suppression annulée.', 's');
  hideUndoBar();
}

document.addEventListener('DOMContentLoaded', () => {
  const undoBtn = $('undo-btn');
  if (undoBtn) undoBtn.addEventListener('click', handleUndo);
});

function renderHistoryGrid() {
  const grid = $('history-grid');
  const empty = $('resumes-empty');
  if (!grid) return;
  if (state.history.length === 0) {
    if (empty) empty.style.display = '';
    grid.innerHTML = ''; return;
  }
  if (empty) empty.style.display = 'none';
  grid.innerHTML = state.history.map(h => {
    const cls = h.atsScore >= 75 ? 'high' : h.atsScore >= 50 ? 'mid' : 'low';
    const matchPill = h.matchScore !== null ? `<div class="history-score-pill match">🎯 Match ${h.matchScore}%</div>` : '';
    return `<div class="history-card">
      <div class="history-card-top">
        <div>
          <div class="history-file-name">${esc(h.fileName)}</div>
          <div class="history-card-date">${esc(h.date)}</div>
        </div>
      </div>
      <div class="history-scores">
        <div class="history-score-pill ats">📄 ATS ${h.atsScore}/100</div>${matchPill}
      </div>
      <div class="history-card-actions">
        <button class="history-card-btn" onclick="viewHistoryEntry(${h.id})">👁 Voir</button>
        <button class="history-card-btn" onclick="chatAboutEntry(${h.id})">💬 Chatter</button>
      </div>
    </div>`;
  }).join('');
}

function cleanFieldText(str) {
  if (!str) return '';
  let s = String(str).trim();
  // Strip leading bullet/dash symbols
  s = s.replace(/^[+\u2022*\-\u2013\u2014>\s]+/, '').trim();
  // Remove empty/placeholder values
  if (s === '--' || s === '-' || s === 'None' || s === 'null' || s === 'Non specifie' || s === 'Non spécifié') return '';
  return s;
}

function getJobOfferSkills(entry, details) {
  const skillsSet = new Set();

  // 1. Compétences spécifiquement saisies dans le formulaire de l'offre d'emploi
  const rawInput = ($('jd-competences')?.value || '').trim();
  if (rawInput) {
    rawInput.split(/[,;\n]/).forEach(s => {
      const clean = s.trim();
      if (clean) skillsSet.add(clean);
    });
  }

  // 2. Compétences d'offre associées spécifiquement à cette analyse / ce fichier
  if (entry) {
    const sessionList = entry.jobSkills || state.sessionJobSkills[entry.fileName] || state.sessionJobSkills[entry.candidateName] || [];
    if (Array.isArray(sessionList)) {
      sessionList.forEach(s => { if (s && s.trim()) skillsSet.add(s.trim()); });
    }
  }

  return Array.from(skillsSet);
}

function isSkillMatchingOffer(candidateSkill, offerSkills) {
  if (!candidateSkill || !offerSkills || !offerSkills.length) return false;
  const candNorm = candidateSkill.toLowerCase().trim();
  if (!candNorm) return false;
  const cClean = candNorm.replace(/[^a-z0-9+#]/g, '');
  if (!cClean) return false;

  return offerSkills.some(offerSkill => {
    const offerNorm = offerSkill.toLowerCase().trim();
    if (!offerNorm) return false;
    const oClean = offerNorm.replace(/[^a-z0-9+#]/g, '');
    return cClean === oClean;
  });
}

function renderSkillChip(skillText, offerSkills, className = 'skill-chip') {
  const cleaned = cleanFieldText(skillText);
  if (!cleaned) return '';
  const isMatched = isSkillMatchingOffer(cleaned, offerSkills);
  if (isMatched) {
    return `<span class="${className} matched" style="background:#f97316 !important;color:#ffffff !important;border-color:#ea580c !important;font-weight:700 !important;box-shadow:0 2px 6px rgba(249,115,22,0.35);">${esc(cleaned)}</span>`;
  }
  return `<span class="${className}">${esc(cleaned)}</span>`;
}

async function openFullAnalysisModal(id) {
  const modal = $('full-analysis-modal');
  const content = $('full-modal-body-content');
  if (!modal || !content) return;

  const entry = state.history.find(h => h.id === id);
  if (!entry) return;

  $('full-modal-title').textContent = `Analyse complète — ${entry.candidateName || entry.fileName}`;
  $('full-modal-sub').textContent = `Analysé le ${entry.date}`;

  content.innerHTML = `<div style="text-align:center;padding:3rem;color:var(--text-3)">Chargement du rapport complet…</div>`;
  modal.classList.add('active');

  const closeBtn = $('btn-close-full-modal');
  const closeFooter = $('full-modal-btn-download');
  const closeBtn2 = $('full-modal-btn-close');

  const closeModal = () => modal.classList.remove('active');
  if (closeBtn) closeBtn.onclick = closeModal;
  if (closeBtn2) closeBtn2.onclick = closeModal;
  if (closeFooter) closeFooter.onclick = () => downloadHistoryEntryPDF(id);

  try {
    let details = entry.data || {};
    if (state.backendOnline && typeof ApiService !== 'undefined') {
      details = await ApiService.getAnalysisDetail(id);
    }

    const atsVal = parseInt(entry.atsScore, 10) || 0;
    const atsClass = atsVal >= 75 ? 'score-green' : (atsVal >= 50 ? 'score-orange' : 'score-rose');

    const matchVal = entry.matchScore !== null && entry.matchScore !== undefined ? parseInt(entry.matchScore, 10) : null;
    const matchClass = matchVal !== null ? (matchVal >= 75 ? 'score-green' : (matchVal >= 50 ? 'score-orange' : 'score-rose')) : '';

    const offerSkills = getJobOfferSkills(entry, details);
    const skillsChips = (details.skills || []).map(s => renderSkillChip(s, offerSkills)).filter(Boolean).join('') || '<span class="text-muted">Aucune compétence spécifiée</span>';
    const langsChips = (details.languages || []).map(l => {
      const val = cleanFieldText(typeof l === 'string' ? l : l.name || JSON.stringify(l));
      return val ? `<span class="skill-chip" style="background:rgba(16,185,129,0.08);color:#059669;border-color:rgba(16,185,129,0.2)">🌐 ${esc(val)}</span>` : '';
    }).filter(Boolean).join('');

    const certsList = (details.certifications || []).map(c => {
      const val = cleanFieldText(typeof c === 'string' ? c : c.name || c.title || JSON.stringify(c));
      return val ? `<div class="drawer-item-card" style="font-weight:600">📜 ${esc(val)}</div>` : '';
    }).filter(Boolean).join('');

    const projectsList = (details.projects || []).map(p => {
      const title = cleanFieldText(p.title || p.role || 'Projet');
      const tech = cleanFieldText(p.technologies);
      const desc = cleanFieldText(p.description);
      if (!title && !desc) return '';

      return `
        <div class="drawer-item-card">
          <div style="font-weight:700;color:var(--text-1);font-size:0.95rem">💻 ${esc(title)}</div>
          ${tech ? `<div style="color:var(--orange);font-weight:600;font-size:0.83rem;margin-top:3px">Techs : ${esc(tech)}</div>` : ''}
          ${desc ? `<div style="margin-top:0.4rem;font-size:0.85rem;line-height:1.5;color:var(--text-2)">${esc(desc)}</div>` : ''}
        </div>
      `;
    }).filter(Boolean).join('');

    const expList = (details.experience || []).map(e => {
      const role = cleanFieldText(e.role);
      const company = cleanFieldText(e.company);
      const duration = cleanFieldText(e.duration);
      const desc = cleanFieldText(e.description);
      if (!role && !desc) return '';

      return `
        <div class="drawer-item-card">
          <div style="font-weight:700;color:var(--text-1);font-size:0.95rem">💼 ${esc(role || 'Poste')}</div>
          ${company ? `<div style="color:var(--orange);font-weight:600;font-size:0.83rem;margin-top:3px">${esc(company)}</div>` : ''}
          ${duration ? `<div style="font-size:0.8rem;color:var(--text-3);margin-top:2px">${esc(duration)}</div>` : ''}
          ${desc ? `<div style="margin-top:0.4rem;font-size:0.85rem;line-height:1.5;color:var(--text-2)">${esc(desc)}</div>` : ''}
        </div>
      `;
    }).filter(Boolean).join('');

    const eduList = (details.education || []).map(ed => {
      const degree = cleanFieldText(ed.degree);
      const school = cleanFieldText(ed.school);
      const duration = cleanFieldText(ed.duration);
      if (!degree && !school) return '';

      return `
        <div class="drawer-item-card">
          <div style="font-weight:700;color:var(--text-1);font-size:0.95rem">🎓 ${esc(degree || 'Diplôme')}</div>
          ${school ? `<div style="color:var(--text-2);font-weight:600;font-size:0.83rem;margin-top:3px">${esc(school)}</div>` : ''}
          ${duration ? `<div style="font-size:0.8rem;color:var(--text-3);margin-top:2px">${esc(duration)}</div>` : ''}
        </div>
      `;
    }).filter(Boolean).join('');

    const recsList = (details.recommendations || []).map(r => {
      const val = cleanFieldText(r);
      return val ? `
        <li class="insight-item">
          <svg class="insight-icon" viewBox="0 0 24 24" fill="none" stroke="var(--orange)" stroke-width="2">
            <polyline points="9 11 12 14 22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
          <span>${esc(val)}</span>
        </li>
      ` : '';
    }).filter(Boolean).join('') || '<li class="insight-item"><span>Profil conforme et prêt pour le recrutement.</span></li>';

    content.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
        <div class="drawer-item-card" style="text-align:center;padding:1.25rem">
          <div style="font-size:0.85rem;color:var(--text-3);font-weight:600;margin-bottom:0.35rem">Score ATS Global</div>
          <div style="font-size:2.2rem;font-weight:800" class="${atsClass}">${atsVal}/100</div>
        </div>
        <div class="drawer-item-card" style="text-align:center;padding:1.25rem">
          <div style="font-size:0.85rem;color:var(--text-3);font-weight:600;margin-bottom:0.35rem">Matching Poste</div>
          <div style="font-size:2.2rem;font-weight:800" class="${matchClass}">${matchVal !== null ? matchVal + '%' : '—'}</div>
        </div>
      </div>

      <div>
        <div class="drawer-section-title">📌 Résumé Exécutif IA</div>
        <div class="drawer-summary-card">${esc(cleanFieldText(details.summary) || 'Aucun résumé disponible.')}</div>
      </div>

      <div>
        <div class="drawer-section-title">🛠️ Compétences Détectées</div>
        <div class="skills-tags-container">${skillsChips}</div>
      </div>

      ${projectsList ? `
      <div>
        <div class="drawer-section-title">💻 Projets Académiques & Réalisations</div>
        <div>${projectsList}</div>
      </div>
      ` : ''}

      ${expList ? `
      <div>
        <div class="drawer-section-title">💼 Expérience Professionnelle</div>
        <div>${expList}</div>
      </div>
      ` : ''}

      ${eduList ? `
      <div>
        <div class="drawer-section-title">🎓 Formation & Diplômes</div>
        <div>${eduList}</div>
      </div>
      ` : ''}

      ${certsList ? `
      <div>
        <div class="drawer-section-title">📜 Certifications & Formations</div>
        <div>${certsList}</div>
      </div>
      ` : ''}

      ${langsChips ? `
      <div>
        <div class="drawer-section-title">🌐 Langues Parlées</div>
        <div class="skills-tags-container">${langsChips}</div>
      </div>
      ` : ''}

      <div>
        <div class="drawer-section-title">💡 Recommandations & Conseils Recruteur</div>
        <ul class="insight-list">${recsList}</ul>
      </div>
    `;

  } catch (err) {
    console.error("Failed to load full analysis modal details:", err);
    content.innerHTML = `<div style="color:var(--rose);padding:2rem;text-align:center">Erreur lors de la récupération du rapport d'analyse complet.</div>`;
  }
}

function viewHistoryEntry(id) {
  openFullAnalysisModal(id);
}

function chatAboutEntry(id) {
  const entry = state.history.find(h => h.id === id);
  if (!entry) return;
  switchPanel('chat');
  setTimeout(() => {
    const select = $('chat-cv-select');
    if (select) select.value = String(id);
    selectChatCv(id);
  }, 0);
}

// ═══════════════════════════════════════════════════════
//  AI CHAT
// ═══════════════════════════════════════════════════════
function setupChat() {
  $('chat-form')?.addEventListener('submit', e => {
    e.preventDefault();
    const q = $('chat-input')?.value.trim();
    if (!q) return;
    if (!state.selectedChatId) {
      toast('Sélectionnez d\'abord un CV en haut de cette page.', 'e');
      return;
    }
    $('chat-input').value = '';
    handleChat(q);
  });

  $('chat-cv-select')?.addEventListener('change', e => {
    const id = e.target.value ? parseInt(e.target.value, 10) : null;
    selectChatCv(id);
  });
}

// Remplit le menu déroulant avec tous les CV déjà analysés, et sélectionne
// automatiquement le plus récent si aucun n'est encore choisi.
function populateChatCvSelect() {
  const select = $('chat-cv-select');
  if (!select) return;

  const current = select.value;
  select.innerHTML = '<option value="">Sélectionnez un CV analysé…</option>' +
    state.history.map(h => `<option value="${h.id}">${esc(h.candidateName || h.fileName)} — ${h.atsScore}/100</option>`).join('');

  if (current && state.history.some(h => String(h.id) === current)) {
    select.value = current;
  } else if (state.history.length > 0) {
    select.value = String(state.history[0].id); // le plus récent (unshift à chaque analyse)
    selectChatCv(state.history[0].id);
    return;
  }

  if (!state.history.length) {
    state.selectedChatId = null;
  }
}

// Change le CV actuellement discuté : charge son historique de conversation
// déjà enregistré côté serveur, ou démarre une conversation vide.
async function selectChatCv(id) {
  state.selectedChatId = id;
  const msgs = $('chat-msgs');
  if (!msgs) return;

  if (!id) {
    msgs.innerHTML = '<div class="chat-empty-hint" id="chat-empty-hint">Analysez d\'abord un CV dans "Nouvelle analyse", puis posez vos questions ici.</div>';
    return;
  }

  msgs.innerHTML = '<div class="chat-empty-hint">Chargement de la conversation…</div>';

  if (state.backendOnline && typeof ApiService !== 'undefined') {
    try {
      const details = await ApiService.getAnalysisDetail(id);
      const history = details.chat_history || [];
      if (!history.length) {
        msgs.innerHTML = '<div class="chat-empty-hint">Posez votre première question sur ce CV.</div>';
      } else {
        msgs.innerHTML = '';
        history.forEach(m => addChatBubble(m.sender === 'ai' ? 'ai' : 'user', m.message));
      }
      return;
    } catch (e) {
      console.error('Erreur chargement historique chat:', e);
    }
  }
  msgs.innerHTML = '<div class="chat-empty-hint">Posez votre première question sur ce CV.</div>';
}

async function handleChat(question) {
  const msgs = $('chat-msgs');
  // Retire le message d'accueil/placeholder avant d'ajouter la vraie conversation
  const hint = msgs?.querySelector('.chat-empty-hint');
  if (hint) hint.remove();

  addChatBubble('user', question);
  state.chatQCount++;
  if ($('stat-chat')) $('stat-chat').textContent = state.chatQCount;
  const typing = addChatBubble('ai', '…');
  try {
    let reply;
    const cvId = state.selectedChatId;
    if (state.backendOnline && cvId) {
      const res = await fetch(`${BACKEND_URL}/api/history/${cvId}/chat`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ message: question }),
      });
      if (!res.ok) throw new Error('API ' + res.status);
      const d = await res.json();
      reply = d.response || d.reply || 'Pas de réponse.';
    } else {
      await delay(1200);
      reply = generateMockChatReply(question);
    }
    typing.textContent = reply;
  } catch (err) {
    console.error('Erreur chat:', err);
    typing.textContent = 'Erreur de connexion à l\'IA. Vérifiez que le serveur et Ollama tournent bien.';
  }
}

function generateMockChatReply(q) {
  const ql = q.toLowerCase();
  if (ql.includes('ats') || ql.includes('score'))
    return 'Pour améliorer le score ATS, intégrez des mots-clés de l\'offre dans les sections "Expérience" et "Compétences", et utilisez des titres standards.';
  if (ql.includes('compétence') || ql.includes('skill'))
    return 'Les compétences semblent solides. Ajoutez des certifications récentes et mentionnez les outils utilisés dans chaque mission.';
  if (ql.includes('améliorer') || ql.includes('optimis'))
    return '(1) Quantifiez les réalisations (+30% productivité), (2) adaptez le vocabulaire à l\'offre, (3) max 2 pages, (4) ajoutez un résumé exécutif.';
  return `Concernant "${q}", analysez les exigences du poste et alignez chaque section du CV. Posez une question plus spécifique si besoin.`;
}

function addChatBubble(role, text) {
  const el = document.createElement('div');
  el.className = `chat-bubble ${role}`;
  el.textContent = text;
  const msgs = $('chat-msgs');
  if (msgs) { msgs.appendChild(el); msgs.scrollTop = msgs.scrollHeight; }
  return el;
}

// ═══════════════════════════════════════════════════════
//  LOADER
// ═══════════════════════════════════════════════════════
function showLoader(title = 'Chargement…', sub = '') {
  const ldr = $('loader');
  if (!ldr) return;
  const t = $('loader-title'), s = $('loader-sub');
  if (t) t.textContent = title;
  if (s) s.textContent = sub;
  ldr.classList.add('active');
}
function hideLoader() { $('loader')?.classList.remove('active'); }

// ═══════════════════════════════════════════════════════
//  EXPORT BUTTONS
// ═══════════════════════════════════════════════════════
function bindExportButtons() {
  $('btn-export-pdf')?.addEventListener('click', exportPDF);
  $('btn-export-json')?.addEventListener('click', exportJSON);
  $('btn-copy-summary')?.addEventListener('click', copySummary);
}

function exportPDF() {
  const area = $('results-area');
  if (!area) return;
  const w = window.open('', '_blank', 'width=800,height=600');
  const styles = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
    .map(l => `<link rel="stylesheet" href="${l.href}">`).join('');
  w.document.write(`<html><head><title>Rapport</title>${styles}</head><body>${area.innerHTML}</body></html>`);
  w.document.close(); w.focus(); w.print();
}

function exportJSON() {
  if (!state.lastResults || !state.lastResults.length) { toast('Aucun résultat à exporter.', 'e'); return; }
  const payload = state.lastResults.map(r => ({ fileName: r.fileName, ...r.data }));
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `rapport_analyse.json`;
  a.click();
}

function copySummary() {
  if (!state.lastResults || !state.lastResults.length) { toast('Aucun résumé à copier.', 'e'); return; }
  const s = state.lastResults.map((r, i) => {
    const summary = $(`profile-summary-${i}`)?.textContent.trim() || '—';
    return `${r.fileName} :\n${summary}`;
  }).join('\n\n');
  navigator.clipboard.writeText(s).then(() => toast('Résumé(s) copié(s).', 's'))
    .catch(() => toast('Échec de la copie.', 'e'));
}

// ═══════════════════════════════════════════════════════
//  NETWORK BACKGROUND (Canvas)
// ═══════════════════════════════════════════════════════
function initNetworkBackground() {
  const canvas = $('network-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W = canvas.width = window.innerWidth;
  let H = canvas.height = window.innerHeight;
  window.addEventListener('resize', () => { W = canvas.width = window.innerWidth; H = canvas.height = window.innerHeight; });
  let mouse = { x: null, y: null };
  window.addEventListener('mousemove', e => { mouse.x = e.clientX; mouse.y = e.clientY; });
  window.addEventListener('mouseleave', () => { mouse.x = null; mouse.y = null; });

  const props = { color: 'rgba(249,115,22,0.22)', r: 2.5, count: 55, maxV: 0.4, lineLen: 140 };
  class Particle {
    constructor() { this.reset(); }
    reset() { this.x = Math.random() * W; this.y = Math.random() * H; this.vx = (Math.random() - .5) * props.maxV; this.vy = (Math.random() - .5) * props.maxV; }
    move() { this.x += this.vx; this.y += this.vy; if (this.x < 0 || this.x > W) this.vx *= -1; if (this.y < 0 || this.y > H) this.vy *= -1; }
    draw() { ctx.beginPath(); ctx.arc(this.x, this.y, props.r, 0, 2 * Math.PI); ctx.fillStyle = props.color; ctx.fill(); }
  }
  const particles = Array.from({ length: props.count }, () => new Particle());
  function loop() {
    ctx.clearRect(0, 0, W, H);
    particles.forEach(p => { p.move(); p.draw(); });
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const d = Math.hypot(particles[i].x - particles[j].x, particles[i].y - particles[j].y);
        if (d < props.lineLen) {
          ctx.beginPath(); ctx.moveTo(particles[i].x, particles[i].y); ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(249,115,22,${(1 - d / props.lineLen) * 0.12})`; ctx.lineWidth = 1; ctx.stroke();
        }
      }
      if (mouse.x !== null) {
        const dm = Math.hypot(particles[i].x - mouse.x, particles[i].y - mouse.y);
        if (dm < 180) { ctx.beginPath(); ctx.moveTo(particles[i].x, particles[i].y); ctx.lineTo(mouse.x, mouse.y); ctx.strokeStyle = `rgba(249,115,22,${(1 - dm / 180) * 0.18})`; ctx.lineWidth = 1.2; ctx.stroke(); }
      }
    }
    requestAnimationFrame(loop);
  }
  loop();
}

// ═══════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════
function toast(msg, type = 'i') {
  const icons = {
    s: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
    e: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    i: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  };
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="toast-icon">${icons[type] || icons.i}</span><span class="toast-msg">${esc(msg)}</span>`;
  const toasts = $('toasts');
  if (toasts) toasts.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 300); }, 4000);
}

function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// ═══════════════════════════════════════════════════════
//  ENTRETIENS & CALENDRIER
// ═══════════════════════════════════════════════════════

function openInterviewModal(analysisId) {
  $('interview-analysis-id').value = analysisId;
  $('interview-form').reset();

  // Mettre la date d'aujourd'hui par défaut
  const today = new Date().toISOString().split('T')[0];
  $('interview-date').value = today;
  $('interview-time').value = "10:00";

  $('interview-modal').style.display = 'flex';
}

function closeInterviewModal() {
  $('interview-modal').style.display = 'none';
}

$('btn-close-interview-modal')?.addEventListener('click', closeInterviewModal);
$('btn-cancel-interview-modal')?.addEventListener('click', closeInterviewModal);

$('interview-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();

  const analysis_id = $('interview-analysis-id').value;
  const payload = {
    interview_date: $('interview-date').value,
    interview_time: $('interview-time').value,
    interview_format: $('interview-format').value,
    interview_location: $('interview-link').value,
    interview_notes: $('interview-notes').value
  };

  try {
    const res = await fetch(`${BACKEND_URL}/api/history/${analysis_id}/schedule-interview`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Erreur API: ' + res.status);

    const data = await res.json();
    console.log('Réponse serveur schedule-interview :', data); // pour déboguer si besoin

    // IMPORTANT : on vérifie la vraie réponse du serveur avant d'afficher
    // un message de succès, au lieu d'afficher "succès" dès que la requête aboutit.
    if (data.email_sent === true) {
      toast('Entretien planifié et convocation envoyée par email !', 's');
    } else if (data.email_error) {
      toast(`Entretien enregistré, mais EMAIL NON ENVOYÉ : ${data.email_error}`, 'e');
    } else {
      toast('Entretien planifié avec succès !', 's');
    }

    closeInterviewModal();

    // Rafraichir le calendrier si on y est
    if ($('panel-calendar').classList.contains('active')) {
      loadAndDisplayCalendar();
    }
  } catch (err) {
    console.error(err);
    toast("Erreur lors de la planification de l'entretien.", 'e');
  }
})
async function loadAndDisplayCalendar() {
  const grid = $('calendar-grid');
  if (!grid) return;
  grid.innerHTML = `
    <div style="grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; padding: 3rem; color: var(--text-3); font-size: 0.9rem; gap: 8px;">
      <svg class="spinner" viewBox="0 0 50 50" style="width: 20px; height: 20px; animation: spin 1s linear infinite; stroke: currentColor;"><circle class="path" cx="25" cy="25" r="20" fill="none" stroke-width="5"></circle></svg>
      <span>Chargement des entretiens...</span>
    </div>
  `;

  if (!state.backendOnline) {
    grid.innerHTML = `
      <div class="calendar-empty-state" style="border-color: rgba(244, 63, 94, 0.2);">
        <div class="calendar-empty-icon" style="background: rgba(244, 63, 94, 0.06); color: var(--rose); border-color: rgba(244, 63, 94, 0.12);">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="28" height="28"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </div>
        <h4 style="color: var(--rose);">Serveur hors ligne</h4>
        <p>Le serveur de base de données est actuellement inaccessible. Veuillez vérifier la connexion du service.</p>
      </div>
    `;
    return;
  }

  try {
    const res = await fetch(`${BACKEND_URL}/api/interviews`, { headers: authHeaders() });
    if (!res.ok) throw new Error('Erreur API');
    const interviews = await res.json();

    if (interviews.length === 0) {
      grid.innerHTML = `
        <div class="calendar-empty-state">
          <div class="calendar-empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="16" y1="2" x2="16" y2="6"></line>
              <line x1="8" y1="2" x2="8" y2="6"></line>
              <line x1="3" y1="10" x2="21" y2="10"></line>
            </svg>
          </div>
          <h4>Aucun entretien planifié</h4>
          <p>Pour planifier un entretien, changez le statut d'un candidat en "En entretien" dans l'onglet "Mes CV".</p>
        </div>
      `;
      return;
    }

    const months = ['JANV.', 'FÉVR.', 'MARS', 'AVR.', 'MAI', 'JUIN', 'JUIL.', 'AOÛT', 'SEPT.', 'OCT.', 'NOV.', 'DÉC.'];

    grid.innerHTML = interviews.map(intv => {
      const months = ['JANV.', 'FÉVR.', 'MARS', 'AVR.', 'MAI', 'JUIN', 'JUIL.', 'AOÛT', 'SEPT.', 'OCT.', 'NOV.', 'DÉC.'];
      const dateParts = intv.interview_date.split('-'); // YYYY-MM-DD
      const day = dateParts[2];
      const monthIdx = parseInt(dateParts[1], 10) - 1;
      const monthStr = months[monthIdx] || '';

      const formatIcon = intv.format.toLowerCase().includes('visio')
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polygon points="23 7 16 12 23 17 23 7"></polygon><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>';

      const refVal = intv.job_reference || intv.reference || '';
      const jobTitleVal = intv.job_title || 'Poste non spécifié';

      return `
        <div class="calendar-card">
          <div class="calendar-card-header">
            <div class="calendar-date-box">
              <div class="calendar-date-day">${day}</div>
              <div class="calendar-date-month">${monthStr}</div>
            </div>
            <div class="calendar-candidate-info">
              ${refVal ? `<span style="font-size:0.68rem;font-weight:700;color:#ea580c;background:rgba(249,115,22,0.1);padding:2px 8px;border-radius:6px;display:inline-block;margin-bottom:4px;">Réf : ${esc(refVal)}</span>` : ''}
              <div class="calendar-candidate-name">${esc(intv.candidate_name || 'Candidat Anonyme')}</div>
              <div class="calendar-job-title">${esc(jobTitleVal)}</div>
            </div>
            <button class="icon-btn" onclick="deleteInterview(${intv.id})" title="Supprimer cet entretien" style="background:transparent; border:none; color:var(--text-3); cursor:pointer;">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </button>
          </div>
          
          <div class="calendar-card-meta">
            <div class="calendar-meta-item">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
              <span>${intv.interview_time}</span>
            </div>
            <div class="calendar-meta-format">
              ${formatIcon}
              <span>${intv.format}</span>
            </div>
          </div>
          
          ${intv.location_link ? `
          <div class="calendar-link">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
            <span>${intv.location_link}</span>
          </div>
          ` : ''}
          
          <button class="primary-btn" onclick="viewCandidate(${intv.analysis_id})" style="width:100%;">Voir le candidat</button>


          ${intv.notes ? `
          <div class="calendar-notes">
            ${intv.notes}
          </div>
          ` : ''}
        </div>
      `;
    }).join('');

  } catch (err) {
    console.error(err);
    grid.innerHTML = '<p style="color:red; font-size: 0.9rem;">Erreur lors du chargement des entretiens.</p>';
  }
}

// ═══════════════════════════════════════════════════════
//  SUPPRESSION ENTRETIEN AVEC ANNULATION
// ═══════════════════════════════════════════════════════
const pendingInterviewDeletions = {}; // { id: { data, cardEl, timeoutId } }

async function deleteInterview(id) {
  // Évite les double-clics
  if (pendingInterviewDeletions[id]) return;

  // Cherche et cache la carte immédiatement sans appeler le serveur
  const grid = $('calendar-grid');
  const cards = grid ? grid.querySelectorAll('.calendar-card') : [];
  let targetCard = null;
  // On stocke l'objet entretien depuis le DOM via l'id encodé dans le bouton
  // et on retire visuellement la carte
  cards.forEach(card => {
    const btn = card.querySelector(`button[onclick="deleteInterview(${id})"]`);
    if (btn) targetCard = card;
  });

  if (targetCard) {
    targetCard.style.transition = 'opacity 0.25s, transform 0.25s';
    targetCard.style.opacity = '0';
    targetCard.style.transform = 'scale(0.96)';
    setTimeout(() => { if (targetCard) targetCard.style.display = 'none'; }, 250);
  }

  const timeoutId = setTimeout(() => finalizeInterviewDeletion(id), UNDO_DELAY_MS);
  pendingInterviewDeletions[id] = { cardEl: targetCard, timeoutId };

  showUndoInterviewBar('Entretien supprimé.');
}

async function finalizeInterviewDeletion(id) {
  const pending = pendingInterviewDeletions[id];
  if (!pending) return;
  delete pendingInterviewDeletions[id];

  try {
    const res = await fetch(`${BACKEND_URL}/api/interviews/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (!res.ok) throw new Error('API Error');
  } catch (e) {
    console.error('Erreur suppression entretien :', e);
    // Reaffiche la carte en cas d'erreur
    if (pending.cardEl) {
      pending.cardEl.style.display = '';
      pending.cardEl.style.opacity = '1';
      pending.cardEl.style.transform = '';
    }
    toast('Erreur lors de la suppression de l\'entretien.', 'e');
  }

  hideUndoInterviewBar();
  // Recharge pour garantir la cohérence si d'autres suppressions sont en cours
  if (Object.keys(pendingInterviewDeletions).length === 0) {
    loadAndDisplayCalendar();
  }
}

function showUndoInterviewBar(message) {
  const container = $('undo-interview-container');
  const msgEl = $('undo-interview-message');
  if (msgEl) msgEl.textContent = message;
  if (container) container.style.display = 'block';
}

function hideUndoInterviewBar() {
  if (Object.keys(pendingInterviewDeletions).length === 0) {
    const container = $('undo-interview-container');
    if (container) container.style.display = 'none';
  }
}

function handleUndoInterview() {
  const ids = Object.keys(pendingInterviewDeletions);
  if (ids.length === 0) return;
  const id = ids[ids.length - 1];
  const pending = pendingInterviewDeletions[id];
  clearTimeout(pending.timeoutId);
  delete pendingInterviewDeletions[id];

  // Reaffiche la carte annulée
  if (pending.cardEl) {
    pending.cardEl.style.display = '';
    requestAnimationFrame(() => {
      pending.cardEl.style.opacity = '1';
      pending.cardEl.style.transform = '';
    });
  }

  toast('Suppression annulée.', 's');
  hideUndoInterviewBar();
}

document.addEventListener('DOMContentLoaded', () => {
  const undoInterviewBtn = $('undo-interview-btn');
  if (undoInterviewBtn) undoInterviewBtn.addEventListener('click', handleUndoInterview);
});

function viewCandidate(analysisId) {
  closeInterviewModal();
  switchPanel('resumes');
  setTimeout(() => openDrawer(analysisId), 100);
}