// SupplySense v1 : SPA vanilla (zero framework), router par hash
'use strict';
const $ = s => document.querySelector(s);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
const esc = s => String(s ?? '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
const api = async (path, opts) => (await fetch('api' + path, opts ? { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined } : undefined)).json();
const toast = m => { const t = $('#toast'); t.innerHTML = m; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 3000); };
const nfr = (n, d = 0) => Number(n ?? 0).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });

const ICONS = {
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.6 8.6 0 0 1-3.3-.7L3 21l1.8-5.7A8.4 8.4 0 1 1 21 11.5z"/></svg>',
  alerte: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4m0 4h.01"/></svg>',
  fiche: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>',
  sante: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
  rapport: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M7 17V9m5 8V5m5 12v-6"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m4 12.5 5 5L20 6.5"/></svg>',
  box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17V9l8-5 8 5v8l-8 5z"/><path d="M4 9l8 5 8-5M12 22v-8"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  truck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 7h14v10H1zM15 10h4l4 4v3h-8z"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/></svg>',
  coin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.6-1-1.7-1.5-3-1.5-1.8 0-3 1-3 2.3 0 3.3 6.2 1.3 6.2 4.4 0 1.3-1.4 2.3-3.2 2.3-1.5 0-2.6-.6-3.2-1.6M12 6.5v11"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>',
};

const S = { etat: null, kpis: null, ruptures: null, fiches: null, sante: null, rapport: null,
  chat: [], journal: [], page: 'dashboard', q: '', scenario: 'casse', dernierOutbox: null, enCours: false };

const SUGGESTIONS = [
  'Quelle famille a la pire rotation ?',
  'Quelles familles ont les meilleures marges ?',
  'Quels produits sont en rupture de stock ?',
  'Quelle est la couverture de stock moyenne ?',
  'Quels sont les produits top en chiffre d\'affaires ?',
  'Quels sont les produits flop ?',
  'Quels fournisseurs ont le pire taux OTIF ?',
  'Quel est le chiffre d\'affaires par famille ?',
];

const STATUT_COUV = {
  rupture: ['Rupture', '#ef4444'], sain: ['Sain', '#10b981'],
  surstock: ['Surstock', '#e8a23d'], dormant: ['Dormant', '#8b5cf6'],
};

// ---------------- composants ----------------
const card = (title, cap, opts = {}) => {
  const c = el('div', 'card');
  c.appendChild(el('div', 'chead', `<div><h2>${title}</h2><div class="cap">${cap || ''}</div></div>${opts.head || ''}`));
  const b = el('div', 'cbody' + (opts.flush ? ' flush' : ''));
  c.appendChild(b);
  return [c, b];
};

// barres horizontales SVG faites main, valeur au survol (title) + a droite
function hbar(items, opts = {}) {
  const unit = opts.unit || '';
  const dec = opts.dec ?? 1;
  const max = Math.max(...items.map(i => i.valeur), 0.0001);
  const lw = opts.labelW || 132, vw = 64, rh = 26, pad = 4;
  const w = 520, bw = w - lw - vw - 16;
  const h = items.length * rh + pad * 2;
  let rows = '';
  items.forEach((it, i) => {
    const y = pad + i * rh;
    const len = Math.max(3, it.valeur / max * bw);
    const tip = `${it.label} : ${nfr(it.valeur, dec)}${unit}${it.extra ? ' · ' + it.extra : ''}`;
    rows += `<g class="bar"><title>${esc(tip)}</title>
      <text class="blbl" x="${lw - 8}" y="${y + rh / 2 + 4}" text-anchor="end">${esc(it.label.length > 19 ? it.label.slice(0, 18) + '…' : it.label)}</text>
      <rect x="${lw}" y="${y + 5}" width="${bw}" height="${rh - 10}" rx="7" fill="var(--bg2)" stroke="var(--line)"/>
      <rect x="${lw}" y="${y + 5}" width="${len.toFixed(1)}" height="${rh - 10}" rx="7" fill="${it.couleur || 'url(#gradBar)'}"/>
      <text class="bval" x="${lw + bw + 8}" y="${y + rh / 2 + 4}">${nfr(it.valeur, dec)}${unit}</text></g>`;
  });
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">
    <defs><linearGradient id="gradBar" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="var(--t500)"/><stop offset="1" stop-color="var(--amber)"/></linearGradient></defs>${rows}</svg>`;
}

// donut SVG fait main : segments avec valeur au survol + legende
function donut(items) {
  const total = items.reduce((t, i) => t + i.nb, 0) || 1;
  const R = 54, C = 2 * Math.PI * R;
  let off = 0, segs = '';
  for (const it of items) {
    const frac = it.nb / total;
    segs += `<circle class="seg" cx="70" cy="70" r="${R}" fill="none" stroke="${it.couleur}" stroke-width="20"
      stroke-dasharray="${(frac * C).toFixed(2)} ${C.toFixed(2)}" stroke-dashoffset="${(-off * C).toFixed(2)}"
      transform="rotate(-90 70 70)"><title>${esc(it.label)} : ${it.nb} produit(s), ${Math.round(frac * 100)} %</title></circle>`;
    off += frac;
  }
  const legende = items.map(it => `<div class="lrow"><i style="background:${it.couleur}"></i>${esc(it.label)}<b>${it.nb}</b></div>`).join('');
  return `<div class="donutwrap"><svg class="donut" width="140" height="140" viewBox="0 0 140 140" role="img">${segs}
    <text x="70" y="66" text-anchor="middle" style="font-size:24px;font-weight:800;fill:var(--ink)">${total}</text>
    <text x="70" y="84" text-anchor="middle" style="font-size:9.5px;fill:var(--mut);font-weight:700;letter-spacing:.5px">LIGNES STOCK</text></svg>
    <div class="legende">${legende}</div></div>`;
}

// ---------------- vues ----------------
function vDashboard() {
  const v = el('div');
  const d = S.kpis;
  if (!d) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }
  const k = d.kpis;
  v.appendChild(el('div', 'kpis', `
    <div class="kpi"><div class="top"><span class="ico red">${ICONS.alerte}</span><span class="lbl">Taux de rupture</span></div>
      <div class="val">${nfr(k.taux_rupture_global_pct, 2)} %</div><div class="hint">global, 90 jours d'observations</div></div>
    <div class="kpi"><div class="top"><span class="ico blue">${ICONS.clock}</span><span class="lbl">Couverture stock</span></div>
      <div class="val">${nfr(k.couverture_mediane_jours, 0)} j</div><div class="hint">mediane · moyenne ${nfr(k.couverture_moyenne_jours, 0)} j</div></div>
    <div class="kpi"><div class="top"><span class="ico green">${ICONS.truck}</span><span class="lbl">OTIF moyen</span></div>
      <div class="val">${nfr(k.otif_moyen_pct, 1)} %</div><div class="hint">fiabilite des fournisseurs</div></div>
    <div class="kpi"><div class="top"><span class="ico amber">${ICONS.coin}</span><span class="lbl">Marge moyenne</span></div>
      <div class="val">${nfr(k.marge_moyenne_pct, 1)} %</div><div class="hint">toutes familles confondues</div></div>
    <div class="kpi"><div class="top"><span class="ico terra">${ICONS.box}</span><span class="lbl">En rupture</span></div>
      <div class="val">${k.nb_ruptures}</div><div class="hint"><a class="linkmore" style="margin:0" href="#alertes">voir les alertes</a></div></div>
    <div class="kpi"><div class="top"><span class="ico violet">${ICONS.moon}</span><span class="lbl">Dormants</span></div>
      <div class="val">${k.nb_dormants}</div><div class="hint">stock qui ne tourne plus</div></div>`));

  const g = el('div', 'grid2');
  const left = el('div', 'gcol');
  const [c1, b1] = card('Marge par famille', 'Taux de marge brute, du plus haut au plus bas (survoler pour le detail)');
  b1.innerHTML = hbar(d.series.marge_par_famille.map(x => ({
    label: x.famille, valeur: x.taux_marge_pct, extra: nfr(x.chiffre_affaires, 0) + ' EUR de CA',
  })), { unit: ' %' });
  left.appendChild(c1);
  const [c2, b2] = card('Taux de rupture par entrepot', 'Part des observations en rupture sur 90 jours');
  b2.innerHTML = hbar(d.series.rupture_par_entrepot.map(x => ({
    label: x.entrepot, valeur: x.taux_pct, extra: `${x.nb_ruptures} ruptures / ${x.nb_observations} observations`,
    couleur: x.taux_pct > 5 ? 'var(--bad)' : undefined,
  })), { unit: ' %', dec: 2 });
  left.appendChild(c2);
  const [c3, b3] = card('Rotation par famille', 'Nombre de fois ou le stock tourne sur la periode');
  b3.innerHTML = hbar([...d.series.rotation_par_famille].sort((a, b) => b.rotation_periode - a.rotation_periode)
    .map(x => ({ label: x.famille, valeur: x.rotation_periode, couleur: x.rotation_periode < 1 ? 'var(--warn)' : undefined })), { unit: '', dec: 1 });
  left.appendChild(c3);
  g.appendChild(left);

  const right = el('div', 'gcol');
  const [c4, b4] = card('Statuts de couverture', 'Repartition des lignes produit x entrepot');
  b4.innerHTML = donut(d.series.statuts_couverture.map(s => ({
    label: (STATUT_COUV[s.statut] || [s.statut])[0], nb: s.nb, couleur: (STATUT_COUV[s.statut] || [0, '#999'])[1],
  })));
  right.appendChild(c4);
  const [c5, b5] = card('Produits dormants', 'Du stock immobilise qui ne se vend plus', { flush: true });
  if (!d.dormants.length) b5.innerHTML = '<div class="empty" style="padding:18px"><span class="big">✅</span>Aucun produit dormant</div>';
  else {
    const t = el('table');
    t.innerHTML = '<tbody>' + d.dormants.map(p => `<tr>
      <td><span class="cellmain"><span class="initials">${esc((p.nom_produit || '?').slice(0, 2).toUpperCase())}</span>
        <span class="txt"><b>${esc(p.nom_produit)}</b><span class="sub2">${esc(p.famille)} · ${esc(p.nom_entrepot)}</span></span></span></td>
      <td style="text-align:right"><span class="pill terra">${p.quantite_disponible} u. en stock</span></td></tr>`).join('') + '</tbody>';
    b5.appendChild(t);
  }
  right.appendChild(c5);
  const [c6, b6] = card('Pires rotations', 'Les familles qui tournent le moins vite');
  const maxR = Math.max(...S.kpis.pires_rotations.map(x => x.rotation_periode), 1);
  for (const r of S.kpis.pires_rotations)
    b6.appendChild(el('div', 'sectrow', `<span style="font-weight:620">${esc(r.famille)}</span>
      <span class="minibar"><span style="width:${Math.max(4, r.rotation_periode / maxR * 100)}%"></span></span>
      <span class="sub2 mono" style="text-align:right">${nfr(r.rotation_periode, 1)}</span>`));
  right.appendChild(c6);
  g.appendChild(right);
  v.appendChild(g);
  return v;
}

function bigValue(lignes) {
  if (!Array.isArray(lignes) || !lignes.length) return null;
  if (lignes.length === 1) {
    for (const val of Object.values(lignes[0]))
      if (typeof val === 'number' || (typeof val === 'string' && val !== '' && !isNaN(Number(val)))) {
        const n = Number(val);
        return nfr(n, Number.isInteger(n) ? 0 : (Math.abs(n) < 1 ? 2 : 1));
      }
    return null;
  }
  return String(lignes.length);
}

function vAssistant() {
  const v = el('div', 'chatwrap');
  const left = el('div', 'gcol');
  const [c1, b1] = card('Assistant metier', 'Question en francais, SQL garde (SELECT seul, 5 marts), reponse chiffree');
  const log = el('div', 'chatlog');
  if (!S.chat.length) log.appendChild(el('div', 'empty', '<span class="big">💬</span>Pose une question, ou clique une suggestion ci-dessous.<br>Chaque reponse montre le SQL execute : transparence totale.'));
  for (const m of S.chat) {
    if (m.role === 'q') log.appendChild(el('div', 'bulle q', esc(m.texte)));
    else if (m.role === 'err') log.appendChild(el('div', 'bulle err', '⚠️ ' + esc(m.texte)));
    else {
      const big = bigValue(m.lignes);
      log.appendChild(el('div', 'bulle r', `
        ${big !== null ? `<span class="bigval">${big}${m.lignes.length > 1 ? ' <span style="font-size:13px;font-weight:650;color:var(--mut)">resultats</span>' : ''}</span>` : ''}
        ${esc(m.texte)}
        <details><summary>SQL execute · ${nfr(m.duree_ms, 0)} ms · mode ${esc(m.mode)}</summary><pre>${esc(m.sql)}</pre></details>`));
    }
  }
  b1.appendChild(log);
  const sugg = el('div', 'sugg');
  for (const q of SUGGESTIONS) {
    const b = el('button', '', esc(q));
    b.onclick = () => poserQuestion(q);
    sugg.appendChild(b);
  }
  b1.appendChild(sugg);
  const form = el('form', 'chatform', `<input id="qaInput" placeholder="Exemple : quelle famille a la pire rotation ?" autocomplete="off">
    <button class="btn primary" type="submit" ${S.enCours ? 'disabled' : ''}>${S.enCours ? 'En cours' : 'Demander'}</button>`);
  form.onsubmit = e => { e.preventDefault(); const q = form.querySelector('#qaInput').value.trim(); if (q) poserQuestion(q); };
  b1.appendChild(form);
  left.appendChild(c1);
  v.appendChild(left);

  const right = el('div', 'gcol');
  const [c2, b2] = card('Journal d\'audit', 'Qui a demande quoi, quel SQL a tourne (journal_qa.jsonl)', { flush: true });
  if (!S.journal.length) b2.innerHTML = '<div class="empty" style="padding:20px"><span class="big">📒</span>Journal vide pour l\'instant</div>';
  else {
    const t = el('table');
    t.innerHTML = '<tbody>' + S.journal.slice(0, 10).map(j => `<tr>
      <td><b style="font-weight:680;font-size:12.3px">${esc(j.question)}</b>
        <div class="sub2">${esc((j.horodatage || '').slice(0, 16).replace('T', ' '))} · ${esc(j.utilisateur || '')}</div></td>
      <td style="text-align:right">${j.statut === 'ok' ? '<span class="chip ok">ok</span>' : '<span class="chip bad">erreur</span>'}
        <div class="sub2" style="margin-top:3px">${esc(j.mode || '')}</div></td></tr>`).join('') + '</tbody>';
    b2.appendChild(t);
  }
  right.appendChild(c2);
  v.appendChild(right);
  queueMicrotask(() => { const l = $('.chatlog'); if (l) l.scrollTop = l.scrollHeight; });
  return v;
}

async function poserQuestion(question) {
  if (S.enCours) return;
  S.enCours = true;
  S.chat.push({ role: 'q', texte: question });
  render();
  const r = await api('/qa', { method: 'POST', body: { question } });
  if (r.ok) S.chat.push({ role: 'r', texte: r.reponse, sql: r.sql, lignes: r.lignes, duree_ms: r.duree_ms, mode: r.mode });
  else S.chat.push({ role: 'err', texte: r.erreur || 'echec de la requete' });
  S.enCours = false;
  const j = await api('/qa/journal');
  if (j.ok) S.journal = j.journal;
  render();
}

function vAlertes() {
  const v = el('div');
  const liste = (S.ruptures || []).filter(p => !S.q || (p.nom_produit + ' ' + p.nom_entrepot + ' ' + p.famille).toLowerCase().includes(S.q));
  const c = el('div', 'card');
  const head = el('div', 'chead', `<div><h2>Produits en rupture</h2>
    <div class="cap">${liste.length} produit(s) · reappro suggere = 30 jours de vente moyenne</div></div>
    <button class="btn primary" style="margin-left:auto" id="btnAlerte">🚨 Generer l'alerte</button>`);
  c.appendChild(head);
  head.querySelector('#btnAlerte').onclick = async function () {
    if (this.dataset.confirm !== '1') {
      this.dataset.confirm = '1';
      this.textContent = 'Confirmer : ecrire dans outbox/';
      return;
    }
    const r = await api('/ruptures/envoyer', { method: 'POST', body: {} });
    if (r.ok) {
      S.dernierOutbox = r.fichier_outbox;
      toast(`${ICONS.check} Alerte generee : ${r.nb_ruptures} rupture(s)`);
    } else toast('⚠️ ' + esc(r.erreur || 'echec'));
    render();
  };
  const wrap = el('div', 'tablewrap');
  if (!liste.length) {
    wrap.appendChild(el('div', 'empty', S.q
      ? '<span class="big">🔎</span>Aucune rupture ne correspond au filtre'
      : '<span class="big">✅</span>Aucune rupture detectee : rien a signaler aujourd\'hui.'));
  } else {
    const t = el('table', '', `<thead><tr><th>Produit</th><th>Entrepot</th><th>Couverture</th><th>Vente moy. / jour</th><th style="text-align:right">Reappro suggere</th></tr></thead>`);
    const tb = el('tbody');
    for (const p of liste) tb.appendChild(el('tr', '', `
      <td><span class="cellmain"><span class="initials">${esc((p.nom_produit || '?').slice(0, 2).toUpperCase())}</span>
        <span class="txt"><b>${esc(p.nom_produit)}</b><span class="sub2">${esc(p.famille)} · ${esc(p.id_produit)}</span></span></span></td>
      <td>${esc(p.nom_entrepot)}<div class="sub2">${esc(p.id_entrepot)}</div></td>
      <td><span class="chip bad"><span class="dot2" style="background:var(--bad)"></span>${nfr(p.couverture_jours ?? 0, 1)} j</span></td>
      <td class="mono">${nfr(p.vente_moyenne_jour, Number(p.vente_moyenne_jour) < 0.1 ? 2 : 1)} u.</td>
      <td style="text-align:right">${p.quantite_reappro > 0
        ? `<span class="pill reappro">commander ${nfr(p.quantite_reappro)} unites</span>`
        : '<span class="pill">ventes quasi nulles : a arbitrer</span>'}</td>`));
    t.appendChild(tb); wrap.appendChild(t);
  }
  c.appendChild(wrap);
  v.appendChild(c);
  if (S.dernierOutbox) {
    const [c2, b2] = card('Alerte deposee dans l\'outbox', 'Message Slack pret a poster, archive en local');
    b2.innerHTML = `<div class="outinfo">📬 ${esc(S.dernierOutbox)}</div>`;
    c2.style.marginTop = '16px';
    v.appendChild(c2);
  }
  return v;
}

function ficheCard(f, zone) {
  const d = el('div', 'fiche');
  d.innerHTML = `
    <div class="frow" style="margin-bottom:6px"><span class="pill terra">${esc(f.id_produit)}</span>
      <span class="pill">${esc(f.mode === 'llm' ? 'LLM' : 'gabarit')}</span>
      ${zone === 'validees' ? `<span class="chip ok" style="margin-left:auto">validee ${esc(f.approuvee_le || '')}</span>` : ''}
      ${zone === 'rejetees' ? `<span class="chip bad" style="margin-left:auto">rejetee ${esc(f.rejetee_le || '')}</span>` : ''}</div>
    <h4>${esc(f.titre || f.nom_produit)}</h4>
    <p class="fdesc">${esc(f.description)}</p>
    <ul>${(f.points_forts || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul>`;
  if (zone === 'rejetees' && f.motif_rejet) d.appendChild(el('div', 'motif', '✕ Motif : ' + esc(f.motif_rejet)));
  if (zone === 'en_attente') {
    const row = el('div', 'frow', `<button class="btn sm ok">${ICONS.check} Approuver</button><button class="btn sm danger">✕ Rejeter</button>`);
    const [bApp, bRej] = row.querySelectorAll('button');
    bApp.onclick = async () => {
      const r = await api('/fiches/' + f.id_produit + '/approuver', { method: 'POST', body: {} });
      toast(r.ok ? `${ICONS.check} Fiche ${esc(f.id_produit)} validee : c'est un geste humain qui publie` : '⚠️ ' + esc(r.erreur || 'echec'));
      await chargerFiches(); render();
    };
    bRej.onclick = () => {
      if (d.querySelector('.rejform')) return;
      const form = el('div', 'rejform', `<input placeholder="Motif du rejet (obligatoire)"><button class="btn sm danger">Confirmer</button>`);
      form.querySelector('button').onclick = async () => {
        const motif = form.querySelector('input').value.trim();
        if (!motif) { toast('⚠️ Le motif de rejet est obligatoire'); return; }
        const r = await api('/fiches/' + f.id_produit + '/rejeter', { method: 'POST', body: { motif } });
        toast(r.ok ? `✕ Fiche ${esc(f.id_produit)} rejetee` : '⚠️ ' + esc(r.erreur || 'echec'));
        await chargerFiches(); render();
      };
      d.appendChild(form);
      form.querySelector('input').focus();
    };
    d.appendChild(row);
  }
  return d;
}

function vFiches() {
  const v = el('div');
  v.appendChild(el('div', 'notice', `<b>La publication reste un geste humain.</b> Les brouillons sont generes avec garde-fous
    (80 a 120 mots, 3 points forts, aucun prix ni superlatif invente) puis attendent ton Approuver ou Rejeter. Rien n'est publie sans ce clic.`));
  const bar = el('div', 'filters');
  const bGen = el('button', 'btn primary', '✨ Generer 2 fiches');
  bGen.onclick = async () => {
    bGen.disabled = true;
    const r = await api('/fiches/generer', { method: 'POST', body: { limite: 2 } });
    if (r.ok) {
      const deposees = r.resultats.filter(x => x.statut === 'en_attente').length;
      const rejetees = r.resultats.length - deposees;
      toast(`${ICONS.check} ${deposees} fiche(s) deposee(s) en attente${rejetees ? ', ' + rejetees + ' refusee(s) par les garde-fous' : ''}`);
    } else toast('⚠️ ' + esc(r.erreur || 'echec'));
    await chargerFiches(); render();
  };
  bar.appendChild(bGen);
  v.appendChild(bar);

  const f = S.fiches || { en_attente: [], validees: [], rejetees: [] };
  const filtre = liste => liste.filter(x => !S.q || (x.titre + ' ' + x.nom_produit + ' ' + x.id_produit).toLowerCase().includes(S.q));
  const cols = el('div', 'fichecols');
  const defs = [
    ['en_attente', 'En attente de validation', 'var(--warn)', filtre(f.en_attente), 'Genere 2 fiches pour alimenter la file'],
    ['validees', 'Validees', 'var(--ok)', filtre(f.validees), 'Aucune fiche validee pour l\'instant'],
    ['rejetees', 'Rejetees', 'var(--bad)', filtre(f.rejetees), 'Aucune fiche rejetee'],
  ];
  for (const [zone, label, colr, liste, vide] of defs) {
    const kcol = el('div', 'fcol');
    kcol.innerHTML = `<div class="fhead"><i style="background:${colr}"></i><h3>${label}</h3><span class="n">${liste.length}</span></div>`;
    const body = el('div', 'fbody');
    if (!liste.length) body.appendChild(el('div', 'fempty', vide));
    for (const fi of liste) body.appendChild(ficheCard(fi, zone));
    kcol.appendChild(body);
    cols.appendChild(kcol);
  }
  v.appendChild(cols);
  return v;
}

function vSante() {
  const v = el('div');
  if (!S.sante) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }
  const f = el('div', 'filters');
  const mk = (label, val) => {
    const b = el('button', 'fchip' + (S.scenario === val ? ' on' : ''), label);
    b.onclick = () => { S.scenario = val; render(); };
    return b;
  };
  f.appendChild(el('span', 'sub2', 'Scenario de demonstration :')).style.marginRight = '4px';
  f.appendChild(mk('🌙 Nuit verte', 'vert'));
  f.appendChild(mk('⛈ Nuit cassee', 'casse'));
  v.appendChild(f);

  const r = S.sante.scenarios[S.scenario];
  v.appendChild(el('div', 'kpis', `
    <div class="kpi"><div class="top"><span class="ico blue">${ICONS.sante}</span><span class="lbl">Tests examines</span></div>
      <div class="val">${r.nb_tests_examines}</div><div class="hint">run dbt ${esc(r.dbt_version || '')}</div></div>
    <div class="kpi"><div class="top"><span class="ico ${r.nb_echecs ? 'red' : 'green'}">${ICONS.alerte}</span><span class="lbl">Echecs</span></div>
      <div class="val">${r.nb_echecs}</div><div class="hint">${r.statut_global === 'ok' ? 'run de nuit sain' : 'diagnostics ci-dessous'}</div></div>
    <div class="kpi"><div class="top"><span class="ico ${r.nb_bloquants ? 'red' : 'green'}">${ICONS.box}</span><span class="lbl">Bloquants</span></div>
      <div class="val">${r.nb_bloquants}</div><div class="hint">${r.nb_bloquants ? 'publication des marts a bloquer' : 'rien ne bloque la publication'}</div></div>`));

  const diags = r.diagnostics.filter(d => !S.q || (d.nom_test + ' ' + d.categorie + ' ' + (d.table || '')).toLowerCase().includes(S.q));
  const [c, b] = card('Diagnostics du gardien', r.nb_echecs
    ? 'Chaque echec est categorise, grade et accompagne d\'une action proposee, a valider par un humain'
    : 'Verdict du gardien sur le run de nuit');
  if (!diags.length) {
    b.innerHTML = `<div class="empty"><span class="big">✅</span>${r.nb_echecs ? 'Aucun diagnostic ne correspond au filtre'
      : 'Aucun test en echec : le run de nuit est sain, les marts sont publiables.'}</div>`;
  } else {
    const lst = el('div', '', '');
    lst.style.cssText = 'display:flex;flex-direction:column;gap:11px';
    for (const d of diags) {
      const dd = el('div', 'diag ' + d.gravite);
      let exemples = '';
      if (d.lignes_exemples && d.lignes_exemples.length) {
        const colonnes = Object.keys(d.lignes_exemples[0]);
        exemples = `<div class="exwrap"><table><thead><tr>${colonnes.map(cn => `<th>${esc(cn)}</th>`).join('')}</tr></thead>
          <tbody>${d.lignes_exemples.map(l => `<tr>${colonnes.map(cn => `<td>${esc(l[cn])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      }
      dd.innerHTML = `
        <div class="dhead">
          <span class="chip ${d.gravite === 'bloquant' ? 'bad' : 'warn'}"><span class="dot2" style="background:${d.gravite === 'bloquant' ? 'var(--bad)' : 'var(--warn)'}"></span>${esc(d.gravite)}</span>
          <span class="pill terra">${esc(d.categorie)}</span>
          ${d.table ? `<span class="pill">${esc(d.table)}</span>` : ''}
          <b>${esc(d.nom_test)}</b>
        </div>
        <p>${esc(d.ce_qui_a_casse)}</p>
        <p><b>Hypothese :</b> ${esc(d.hypothese_cause)}</p>
        <p><b>Action proposee :</b> ${esc(d.action_proposee)}</p>
        ${exemples}`;
      lst.appendChild(dd);
    }
    b.appendChild(lst);
  }
  v.appendChild(c);
  return v;
}

function vRapport() {
  const v = el('div');
  if (!S.rapport) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }
  const r = S.rapport;
  const k = r.kpi;
  v.appendChild(el('div', 'kpis', `
    <div class="kpi"><div class="top"><span class="ico red">${ICONS.alerte}</span><span class="lbl">Taux de rupture</span></div>
      <div class="val">${nfr(k.rupture.taux_global_pct, 2)} %</div><div class="hint">${k.rupture.nb_ruptures} ruptures / ${k.rupture.nb_observations} obs.</div></div>
    <div class="kpi"><div class="top"><span class="ico green">${ICONS.truck}</span><span class="lbl">OTIF moyen</span></div>
      <div class="val">${nfr(k.otif.moyen_pct, 1)} %</div><div class="hint">${k.otif.nb_fournisseurs} fournisseurs suivis</div></div>
    <div class="kpi"><div class="top"><span class="ico violet">${ICONS.moon}</span><span class="lbl">Dormants</span></div>
      <div class="val">${k.dormants.nb}</div><div class="hint">produits a destocker</div></div>
    <div class="kpi"><div class="top"><span class="ico amber">${ICONS.rapport}</span><span class="lbl">Pire rotation</span></div>
      <div class="val" style="font-size:19px;letter-spacing:-.3px">${esc(k.pire_rotation.famille)}</div>
      <div class="hint">rotation ${nfr(k.pire_rotation.rotation_periode, 2)} sur la periode</div></div>`));

  const g = el('div', 'grid2');
  const left = el('div', 'gcol');
  const [c1, b1] = card('Ce qu\'il faut retenir cette semaine', 'Synthese en 3 puces (LLM si cle presente, regles metier sinon)');
  b1.innerHTML = r.puces.map(p => `<div class="sectrow" style="grid-template-columns:18px 1fr"><span style="color:var(--t600);font-weight:800">●</span><span style="line-height:1.55">${esc(p)}</span></div>`).join('');
  left.appendChild(c1);
  const [c2, b2] = card('Top familles par taux de marge', 'Les 3 familles les plus rentables');
  b2.innerHTML = hbar(k.top_familles_marge.map(x => ({
    label: x.famille, valeur: x.taux_marge, extra: nfr(x.chiffre_affaires, 0) + ' EUR de CA',
  })), { unit: ' %' });
  left.appendChild(c2);
  g.appendChild(left);

  const right = el('div', 'gcol');
  const [c3, b3] = card('Publication', 'rapport.md pour email ou Slack, page HTML autonome');
  b3.innerHTML = `<div class="row" style="gap:9px;margin-bottom:11px">
      <button class="btn primary" id="btnGenRap">📄 Regenerer le rapport</button>
      ${r.html_disponible ? `<a class="btn" href="rapport-hebdo.html" target="_blank" rel="noopener">Ouvrir la page HTML ↗</a>` : ''}</div>
    <p class="sub2" style="margin:0;line-height:1.6">${r.html_disponible
      ? 'Derniere version disponible : docs/rapport-hebdo.html (regenerer pour rafraichir les chiffres).'
      : 'Aucune page HTML generee pour l\'instant : clique sur Regenerer le rapport.'}</p>`;
  b3.querySelector('#btnGenRap').onclick = async function () {
    this.disabled = true;
    const rr = await api('/rapport/generer', { method: 'POST', body: {} });
    toast(rr.ok ? `${ICONS.check} Rapport regenere (markdown + HTML)` : '⚠️ ' + esc(rr.erreur || 'echec'));
    S.rapport = null;
    await chargerRapport(); render();
  };
  right.appendChild(c3);
  const [c4, b4] = card('Fournisseurs a surveiller', 'Les 3 OTIF les plus faibles', { flush: true });
  const t = el('table');
  t.innerHTML = '<tbody>' + k.otif.pires.map(x => `<tr>
    <td><b style="font-weight:680">${esc(x.nom_fournisseur)}</b><div class="sub2">${esc(x.pays)}</div></td>
    <td style="text-align:right"><span class="chip ${x.taux_otif < 85 ? 'bad' : 'warn'}">${nfr(x.taux_otif, 1)} % OTIF</span></td></tr>`).join('') + '</tbody>';
  b4.appendChild(t);
  right.appendChild(c4);
  g.appendChild(right);
  v.appendChild(g);
  return v;
}

// ---------------- chargement ----------------
async function chargerEtat() {
  try {
    const e = await api('/etat');
    S.etat = e;
    $('#apiDot').className = 'dotlive ' + (e.ok ? 'ok' : 'bad');
    $('#sideBase').textContent = e.mode_test ? 'fixtures/entrepot_test.duckdb' : (e.base || '').split('/').slice(-2).join('/');
    $('#footMode').textContent = e.mode_test ? 'MODE TEST' : 'entrepot reel';
    const badge = $('#badgeEnv');
    badge.textContent = e.mode_test ? '⚑ Mode test · Lecture seule' : 'Lecture seule · Entrepot';
    badge.classList.toggle('test', !!e.mode_test);
  } catch (err) {
    $('#apiDot').className = 'dotlive bad';
    $('#sideBase').textContent = 'API injoignable';
  }
}
async function chargerFiches() { const r = await api('/fiches'); if (r.ok) S.fiches = r; }
async function chargerRapport() { const r = await api('/rapport'); if (r.ok) S.rapport = r; }

const CHARGEURS = {
  dashboard: async () => { const r = await api('/kpis'); if (r.ok) S.kpis = r; },
  assistant: async () => { const j = await api('/qa/journal'); if (j.ok) S.journal = j.journal; },
  alertes: async () => { const r = await api('/ruptures'); if (r.ok) S.ruptures = r.ruptures; },
  fiches: chargerFiches,
  sante: async () => { const r = await api('/sante'); if (r.ok) S.sante = r; },
  rapport: chargerRapport,
};

// ---------------- router / shell ----------------
const PAGES = [
  ['dashboard', 'Dashboard', ICONS.dash, 1],
  ['rapport', 'Rapport hebdo', ICONS.rapport, 1],
  ['assistant', 'Assistant', ICONS.chat, 2],
  ['alertes', 'Alertes rupture', ICONS.alerte, 2],
  ['fiches', 'Fiches produits', ICONS.fiche, 2],
  ['sante', 'Sante des donnees', ICONS.sante, 2],
];
const SUBS = {
  dashboard: () => 'Vue d\'ensemble des 5 marts · ' + new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }).format(Date.now()),
  assistant: () => 'Question en francais, SQL garde, reponse chiffree',
  alertes: () => 'Ruptures detectees et reappro suggere',
  fiches: () => 'Brouillons generes, publication par geste humain',
  sante: () => 'Diagnostics du gardien sur les tests dbt',
  rapport: () => 'Les 5 KPI du lundi matin',
};
const VIEWS = { dashboard: vDashboard, assistant: vAssistant, alertes: vAlertes, fiches: vFiches, sante: vSante, rapport: vRapport };

function nav() {
  const counts = {
    alertes: S.kpis ? S.kpis.kpis.nb_ruptures : 0,
    fiches: S.fiches ? S.fiches.en_attente.length : 0,
  };
  const n1 = $('#nav1'), n2 = $('#nav2');
  n1.innerHTML = ''; n2.innerHTML = '';
  for (const [id, label, icon, grp] of PAGES) {
    const a = el('a', S.page === id ? 'on' : '', icon + `<span class="txt">${label}</span>` +
      (counts[id] ? `<span class="count ${id === 'alertes' ? 'hot' : ''}">${counts[id]}</span>` : ''));
    a.href = '#' + id;
    (grp === 1 ? n1 : n2).appendChild(a);
  }
}

function render() {
  nav();
  const meta = PAGES.find(x => x[0] === S.page) || PAGES[0];
  $('#pageTitle').textContent = meta[1];
  $('#crumb').childNodes[0].textContent = 'SupplySense / ' + (SUBS[S.page] ? SUBS[S.page]() : '');
  const view = $('#view');
  view.innerHTML = '';
  const node = (VIEWS[S.page] || vDashboard)();
  node.classList.add('view-enter');
  view.appendChild(node);
}

async function aller(page) {
  S.page = VIEWS[page] ? page : 'dashboard';
  render();
  if (CHARGEURS[S.page]) { await CHARGEURS[S.page](); render(); }
}

window.addEventListener('hashchange', () => { S.q = ''; $('#q').value = ''; aller(location.hash.slice(1) || 'dashboard'); });
$('#q').addEventListener('input', e => { S.q = e.target.value.toLowerCase(); render(); });

// theme : persiste, forcable par ?theme=dark|light au chargement
const SUN = '<path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v3m0 16v3M4.2 4.2l2.1 2.1m11.4 11.4 2.1 2.1M1 12h3m16 0h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>';
const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>';
function applyTheme(t, persist = true) {
  document.documentElement.dataset.theme = t;
  $('#themeIco').innerHTML = t === 'dark' ? SUN : MOON;
  if (persist) localStorage.setItem('ss-theme', t);
}
$('#themeBtn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
const params = new URLSearchParams(location.search);
const themeForce = params.get('theme');
if (themeForce === 'dark' || themeForce === 'light') applyTheme(themeForce, false);
else applyTheme(localStorage.getItem('ss-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'), false);
// ?motion=0 : coupe animations et transitions (captures et tests visuels)
if (params.get('motion') === '0') {
  const st = document.createElement('style');
  st.textContent = '*{animation:none!important;transition:none!important}';
  document.head.appendChild(st);
}

// skeleton de premier chargement puis boot
$('#view').innerHTML = `<div class="kpis">${'<div class="kpi"><div class="skel" style="height:14px;width:60%"></div><div class="skel" style="height:34px;width:40%;margin-top:10px"></div></div>'.repeat(6)}</div>
  <div class="grid2"><div class="card"><div class="cbody"><div class="skel" style="height:180px"></div></div></div>
  <div class="card"><div class="cbody"><div class="skel" style="height:180px"></div></div></div></div>`;
(async () => {
  await chargerEtat();
  setInterval(chargerEtat, 15000);
  await CHARGEURS.dashboard();
  await chargerFiches();
  await aller(location.hash.slice(1) || 'dashboard');
  // ?q=... : pose automatiquement une question a l'assistant (demo, captures)
  const autoQ = params.get('q');
  if (autoQ && S.page === 'assistant') await poserQuestion(autoQ);
})();
