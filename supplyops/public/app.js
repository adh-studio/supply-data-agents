// SupplyOps v1 : SPA vanilla (zero framework), router par hash
'use strict';
const $ = s => document.querySelector(s);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
const esc = s => String(s ?? '').replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
const api = async (path, opts) => (await fetch('api' + path, opts ? { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined } : undefined)).json();
const toast = m => { const t = $('#toast'); t.innerHTML = m; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 3200); };
const nfr = (n, d = 0) => Number(n ?? 0).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
const mga = n => nfr(n) + ' MGA';
const aujourdhui = () => new Date().toISOString().slice(0, 10);

const ICONS = {
  produit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17V9l8-5 8 5v8l-8 5z"/><path d="M4 9l8 5 8-5M12 22v-8"/></svg>',
  inventaire: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11h6M9 15h4"/><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
  vente: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="20" r="1.6"/><circle cx="18" cy="20" r="1.6"/><path d="M2 3h3l2.6 12.4A2 2 0 0 0 9.6 17H18a2 2 0 0 0 2-1.6L21.6 8H6"/></svg>',
  lot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 7h14v10H1zM15 10h4l4 4v3h-8z"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/></svg>',
  publier: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/><path d="M5 21h14"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m4 12.5 5 5L20 6.5"/></svg>',
  croix: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  crayon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 5v14M5 12h14"/></svg>',
};

const S = {
  etat: null, refs: null, produits: null, inventaire: null, ventes: null, lots: null,
  page: 'produits', q: '', filtreFamille: '', pubSondage: null, pubApresLancement: false,
};

const fmtStatut = { en_cours: 'en cours', partielle: 'partielle', recue: 'recue' };
const fmtAction = {
  produit_cree: 'Produit cree', produit_modifie: 'Produit modifie', produit_retire: 'Produit retire',
  inventaire_saisi: 'Inventaire', vente_saisie: 'Vente', lot_cree: 'Lot d\'import',
  reception_enregistree: 'Reception',
};

// ---------------- composants ----------------
const card = (title, cap, opts = {}) => {
  const c = el('div', 'card');
  c.appendChild(el('div', 'chead', `<div><h2>${title}</h2><div class="cap">${cap || ''}</div></div>${opts.head || ''}`));
  const b = el('div', 'cbody' + (opts.flush ? ' flush' : ''));
  c.appendChild(b);
  return [c, b];
};

function champ(label, inner, aide) {
  return `<div class="field"><label>${label}</label>${inner}${aide ? `<span class="aide">${aide}</span>` : ''}</div>`;
}
function optionsSelect(liste, valCle, libCle, selection, placeholder) {
  return (placeholder ? `<option value="">${esc(placeholder)}</option>` : '') +
    liste.map(x => `<option value="${esc(x[valCle])}" ${x[valCle] === selection ? 'selected' : ''}>${esc(x[libCle])}</option>`).join('');
}

// panneau lateral
function ouvrirPanneau(titre, cap, corps, pied) {
  const p = $('#panneau');
  p.innerHTML = `<div class="phead"><div style="flex:1"><h2>${titre}</h2><div class="cap">${cap || ''}</div></div>
    <button class="iconbtn" id="pClose" title="Fermer">${ICONS.croix}</button></div>`;
  const body = el('div', 'pbody'); body.appendChild(corps); p.appendChild(body);
  if (pied) { const f = el('div', 'pfoot'); f.appendChild(pied); p.appendChild(f); }
  p.classList.add('on'); $('#voile').classList.add('on');
  $('#pClose').onclick = fermerPanneau;
}
function fermerPanneau() { $('#panneau').classList.remove('on'); $('#voile').classList.remove('on'); }
$('#voile').onclick = fermerPanneau;

// ---------------- formulaire produit (panneau lateral) ----------------
function formulaireProduit(produit) {
  const edition = !!produit;
  const corps = el('div');
  corps.innerHTML = `
    <div class="erreurbox" id="fpErr" style="display:none"></div>
    <div class="avertbox" id="fpWarn" style="display:none"></div>
    ${champ('Nom du produit', `<input id="fpNom" value="${esc(produit?.nom_produit || '')}" placeholder="Ex. Casque bluetooth ANC">`)}
    ${champ('Famille', `<select id="fpFamille">${optionsSelect(S.refs.familles.map(f => ({ v: f })), 'v', 'v', produit?.famille, 'Choisir une famille du contrat')}</select>`,
      'Liste imposee par le contrat dbt (models/staging/schema.yml)')}
    ${champ('Fournisseur', `<select id="fpFour">${optionsSelect(S.refs.fournisseurs, 'id_fournisseur', 'nom_fournisseur', produit?.id_fournisseur, 'Choisir un fournisseur')}</select>`)}
    <div class="frow2">
      ${champ('Prix d\'achat (MGA)', `<input id="fpAchat" type="number" min="1" value="${esc(produit?.prix_achat || '')}" placeholder="250000">`)}
      ${champ('Prix de vente (MGA)', `<input id="fpVente" type="number" min="1" value="${esc(produit?.prix_vente || '')}" placeholder="420000">`)}
    </div>
    <div class="margebox"><span class="lbl">Marge unitaire</span><span class="val" id="fpMarge">-</span></div>`;

  const majMarge = () => {
    const a = Number(corps.querySelector('#fpAchat').value), v = Number(corps.querySelector('#fpVente').value);
    const node = corps.querySelector('#fpMarge');
    if (!a || !v) { node.textContent = '-'; node.className = 'val'; return; }
    const m = v - a, pct = v ? (100 * m / v) : 0;
    node.textContent = `${mga(m)} · ${nfr(pct, 1)} %`;
    node.className = 'val' + (m <= 0 ? ' neg' : '');
  };
  corps.querySelector('#fpAchat').oninput = majMarge;
  corps.querySelector('#fpVente').oninput = majMarge;
  majMarge();

  const pied = el('div', 'row', '');
  pied.style.cssText = 'display:flex;gap:9px;width:100%';
  const bEnvoi = el('button', 'btn ambre', (edition ? ICONS.check + ' Enregistrer' : ICONS.plus + ' Creer le produit'));
  bEnvoi.style.flex = '1'; bEnvoi.style.justifyContent = 'center';
  pied.appendChild(bEnvoi);
  if (edition) {
    const bDel = el('button', 'btn danger', 'Retirer');
    bDel.onclick = async function () {
      if (this.dataset.confirm !== '1') { this.dataset.confirm = '1'; this.textContent = 'Confirmer le retrait'; return; }
      const r = await fetch('api/produits/' + produit.id_produit, { method: 'DELETE' }).then(x => x.json());
      if (r.ok) { toast(`${ICONS.check} Produit ${esc(produit.id_produit)} retire du catalogue`); fermerPanneau(); await rafraichir(['produits', 'inventaire', 'etat']); render(); }
      else { const e = corps.querySelector('#fpErr'); e.style.display = ''; e.textContent = r.erreur || 'echec'; this.dataset.confirm = ''; this.textContent = 'Retirer'; }
    };
    pied.appendChild(bDel);
  }
  bEnvoi.onclick = async () => {
    bEnvoi.disabled = true;
    const donnees = {
      nom_produit: corps.querySelector('#fpNom').value.trim(),
      famille: corps.querySelector('#fpFamille').value,
      id_fournisseur: corps.querySelector('#fpFour').value,
      prix_achat: corps.querySelector('#fpAchat').value,
      prix_vente: corps.querySelector('#fpVente').value,
    };
    const r = edition
      ? await api('/produits/' + produit.id_produit, { method: 'POST', body: donnees })
      : await api('/produits', { method: 'POST', body: donnees });
    bEnvoi.disabled = false;
    const err = corps.querySelector('#fpErr'), warn = corps.querySelector('#fpWarn');
    if (!r.ok) { err.style.display = ''; err.textContent = r.erreur || 'echec'; warn.style.display = 'none'; return; }
    if (r.avertissements && r.avertissements.length) { toast('⚠ ' + esc(r.avertissements[0])); }
    toast(`${ICONS.check} ${edition ? 'Produit mis a jour' : 'Produit ' + esc(r.produit.id_produit) + ' cree'} · en attente de publication`);
    fermerPanneau();
    await rafraichir(['produits', 'etat']); render();
  };

  ouvrirPanneau(
    edition ? 'Modifier ' + esc(produit.id_produit) : 'Nouveau produit',
    edition ? esc(produit.nom_produit) : 'La famille vient du contrat, la saisie est validee avant ecriture',
    corps, pied
  );
}

// ---------------- vues ----------------
function vProduits() {
  const v = el('div');
  if (!S.produits || !S.refs) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }

  const familles = [...new Set(S.produits.map(p => p.famille))].sort();
  const bar = el('div', 'filters');
  const bNew = el('button', 'btn ambre', ICONS.plus + ' Nouveau produit');
  bNew.onclick = () => formulaireProduit(null);
  bar.appendChild(bNew);
  const mkChip = (label, val) => {
    const b = el('button', 'fchip' + (S.filtreFamille === val ? ' on' : ''), label);
    b.onclick = () => { S.filtreFamille = S.filtreFamille === val ? '' : val; render(); };
    return b;
  };
  for (const f of familles) bar.appendChild(mkChip(f, f));
  v.appendChild(bar);

  const liste = S.produits.filter(p =>
    (!S.filtreFamille || p.famille === S.filtreFamille) &&
    (!S.q || (p.nom_produit + ' ' + p.id_produit + ' ' + p.famille + ' ' + p.nom_fournisseur).toLowerCase().includes(S.q)));

  const c = el('div', 'card');
  c.appendChild(el('div', 'chead', `<div><h2>Catalogue produits</h2>
    <div class="cap">${liste.length} produit(s) · clic sur une ligne pour modifier · prix en ariary (MGA)</div></div>`));
  const wrap = el('div', 'tablewrap');
  if (!liste.length) wrap.appendChild(el('div', 'empty', '<span class="big">🔎</span>Aucun produit ne correspond au filtre'));
  else {
    const t = el('table', '', `<thead><tr><th>Produit</th><th>Famille</th><th>Fournisseur</th>
      <th style="text-align:right">Prix achat</th><th style="text-align:right">Prix vente</th><th style="text-align:right">Marge</th><th style="text-align:right">Ventes</th></tr></thead>`);
    const tb = el('tbody');
    for (const p of liste) {
      const tr = el('tr', 'clic', `
        <td><span class="cellmain"><span class="initials">${esc(p.nom_produit.slice(0, 2).toUpperCase())}</span>
          <span class="txt"><b>${esc(p.nom_produit)}</b><span class="sub2">${esc(p.id_produit)}</span></span></span></td>
        <td><span class="pill terra">${esc(p.famille)}</span></td>
        <td>${esc(p.nom_fournisseur)}<div class="sub2">${esc(p.id_fournisseur)}</div></td>
        <td class="mono" style="text-align:right">${mga(p.prix_achat)}</td>
        <td class="mono" style="text-align:right"><b style="font-weight:700">${mga(p.prix_vente)}</b></td>
        <td style="text-align:right"><span class="chip ${p.marge_unitaire <= 0 ? 'bad' : (p.taux_marge_pct < 20 ? 'warn' : 'ok')}">${nfr(p.taux_marge_pct, 1)} %</span></td>
        <td class="mono" style="text-align:right"><span class="pill">${p.nb_ventes}</span></td>`);
      tr.onclick = () => formulaireProduit(p);
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t);
  }
  c.appendChild(wrap);
  v.appendChild(c);
  return v;
}

function vInventaire() {
  const v = el('div', 'grid2');
  if (!S.inventaire || !S.refs) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }

  const left = el('div', 'gcol');
  const liste = S.inventaire.filter(l => !S.q || (l.nom_produit + ' ' + l.nom_entrepot + ' ' + l.famille).toLowerCase().includes(S.q));
  const [c1] = card('Inventaire courant', `${liste.length} ligne(s) produit x entrepot · derniere photographie connue`, { flush: true });
  const wrap = el('div', 'tablewrap');
  if (!liste.length) wrap.appendChild(el('div', 'empty', '<span class="big">📦</span>Aucune ligne d\'inventaire'));
  else {
    const t = el('table', '', `<thead><tr><th>Produit</th><th>Entrepot</th><th>Date releve</th><th style="text-align:right">Quantite</th></tr></thead>`);
    const tb = el('tbody');
    for (const l of liste) tb.appendChild(el('tr', '', `
      <td><span class="cellmain"><span class="initials">${esc(l.nom_produit.slice(0, 2).toUpperCase())}</span>
        <span class="txt"><b>${esc(l.nom_produit)}</b><span class="sub2">${esc(l.famille)} · ${esc(l.id_produit)}</span></span></span></td>
      <td>${esc(l.nom_entrepot)}<div class="sub2">${esc(l.id_entrepot)}</div></td>
      <td class="mono sub2">${esc(l.date_inventaire)}</td>
      <td style="text-align:right">${Number(l.quantite_disponible) === 0
        ? '<span class="chip bad">rupture · 0 u.</span>'
        : `<span class="pill terra">${nfr(l.quantite_disponible)} u.</span>`}</td>`));
    t.appendChild(tb); wrap.appendChild(t);
  }
  c1.appendChild(wrap);
  left.appendChild(c1);
  v.appendChild(left);

  const right = el('div', 'gcol');
  const [c2, b2] = card('Saisie rapide', 'Photographie de stock a la date du jour (' + aujourdhui() + ')');
  b2.innerHTML = `
    <div class="erreurbox" id="invErr" style="display:none"></div>
    ${champ('Produit', `<select id="invProd">${optionsSelect(S.produits || [], 'id_produit', 'nom_produit', '', 'Choisir un produit')}</select>`)}
    ${champ('Entrepot', `<select id="invEnt">${optionsSelect(S.refs.entrepots, 'id_entrepot', 'nom_entrepot', '', 'Choisir un entrepot')}</select>`)}
    ${champ('Quantite disponible', `<input id="invQte" type="number" min="0" placeholder="0 = rupture constatee">`, 'Une re-saisie du meme jour remplace la precedente')}
    <button class="btn ambre" id="invBtn" style="width:100%;justify-content:center">${ICONS.check} Enregistrer l'inventaire</button>`;
  b2.querySelector('#invBtn').onclick = async function () {
    this.disabled = true;
    const r = await api('/inventaire', { method: 'POST', body: {
      id_produit: b2.querySelector('#invProd').value,
      id_entrepot: b2.querySelector('#invEnt').value,
      quantite: b2.querySelector('#invQte').value,
    } });
    this.disabled = false;
    const err = b2.querySelector('#invErr');
    if (!r.ok) { err.style.display = ''; err.textContent = r.erreur || 'echec'; return; }
    err.style.display = 'none';
    toast(`${ICONS.check} Inventaire ${r.remplacee ? 'remplace' : 'enregistre'} · en attente de publication`);
    await rafraichir(['inventaire', 'etat']); render();
  };
  right.appendChild(c2);
  v.appendChild(right);
  return v;
}

function vVentes() {
  const v = el('div', 'grid2');
  if (!S.ventes || !S.refs) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }

  const left = el('div', 'gcol');
  const liste = S.ventes.filter(x => !S.q || (x.nom_produit + ' ' + x.id_vente + ' ' + x.canal + ' ' + x.nom_entrepot).toLowerCase().includes(S.q));
  const [c1] = card('Journal des dernieres ventes', `${liste.length} ligne(s) · montants en ariary (MGA)`, { flush: true });
  const wrap = el('div', 'tablewrap');
  if (!liste.length) wrap.appendChild(el('div', 'empty', '<span class="big">🧾</span>Aucune vente dans le journal'));
  else {
    const t = el('table', '', `<thead><tr><th>Vente</th><th>Produit</th><th>Entrepot</th><th>Canal</th><th style="text-align:right">Montant</th></tr></thead>`);
    const tb = el('tbody');
    for (const x of liste) tb.appendChild(el('tr', '', `
      <td><b style="font-weight:700">${esc(x.id_vente)}</b><div class="sub2 mono">${esc(x.date_vente)}</div></td>
      <td><b style="font-weight:650">${esc(x.nom_produit)}</b><div class="sub2">${esc(x.id_produit)}</div></td>
      <td>${esc(x.nom_entrepot)}</td>
      <td><span class="chip ${x.canal === 'web' ? 'info' : 'neutre'}">${esc(x.canal)}</span></td>
      <td class="mono" style="text-align:right"><b style="font-weight:700">${mga(x.montant_ligne)}</b>
        <div class="sub2 mono">${nfr(x.quantite)} x ${mga(x.prix_unitaire)}</div></td>`));
    t.appendChild(tb); wrap.appendChild(t);
  }
  c1.appendChild(wrap);
  left.appendChild(c1);
  v.appendChild(left);

  const right = el('div', 'gcol');
  const [c2, b2] = card('Saisie rapide', 'Vente du jour (' + aujourdhui() + ') · prix pre-rempli au prix catalogue');
  b2.innerHTML = `
    <div class="erreurbox" id="vteErr" style="display:none"></div>
    ${champ('Produit', `<select id="vteProd">${optionsSelect(S.produits || [], 'id_produit', 'nom_produit', '', 'Choisir un produit')}</select>`)}
    ${champ('Entrepot', `<select id="vteEnt">${optionsSelect(S.refs.entrepots, 'id_entrepot', 'nom_entrepot', '', 'Choisir un entrepot')}</select>`)}
    <div class="field"><label>Canal</label><span class="seg" id="vteCanal">
      <button type="button" data-v="magasin" class="on">Magasin</button><button type="button" data-v="web">Web</button></span></div>
    <div class="frow2">
      ${champ('Quantite', `<input id="vteQte" type="number" min="1" value="1">`)}
      ${champ('Prix unitaire (MGA)', `<input id="vtePrix" type="number" min="1" placeholder="prix catalogue">`)}
    </div>
    <button class="btn ambre" id="vteBtn" style="width:100%;justify-content:center">${ICONS.check} Enregistrer la vente</button>`;
  b2.querySelector('#vteProd').onchange = e => {
    const p = (S.produits || []).find(x => x.id_produit === e.target.value);
    if (p) b2.querySelector('#vtePrix').value = p.prix_vente;
  };
  b2.querySelector('#vteCanal').onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    b2.querySelectorAll('#vteCanal button').forEach(x => x.classList.toggle('on', x === b));
  };
  b2.querySelector('#vteBtn').onclick = async function () {
    this.disabled = true;
    const r = await api('/ventes', { method: 'POST', body: {
      id_produit: b2.querySelector('#vteProd').value,
      id_entrepot: b2.querySelector('#vteEnt').value,
      canal: b2.querySelector('#vteCanal .on').dataset.v,
      quantite: b2.querySelector('#vteQte').value,
      prix_unitaire: b2.querySelector('#vtePrix').value || null,
    } });
    this.disabled = false;
    const err = b2.querySelector('#vteErr');
    if (!r.ok) { err.style.display = ''; err.textContent = r.erreur || 'echec'; return; }
    err.style.display = 'none';
    toast(`${ICONS.check} Vente ${esc(r.vente.id_vente)} enregistree · en attente de publication`);
    await rafraichir(['ventes', 'etat']); render();
  };
  right.appendChild(c2);
  v.appendChild(right);
  return v;
}

function vLots() {
  const v = el('div', 'grid2');
  if (!S.lots || !S.refs) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }

  const left = el('div', 'gcol');
  const filtres = S.lots.filter(l => !S.q || (l.id_commande + ' ' + l.nom_produit + ' ' + l.nom_fournisseur).toLowerCase().includes(S.q));
  const enCours = filtres.filter(l => l.statut !== 'recue');
  const recues = filtres.filter(l => l.statut === 'recue').slice(0, 15);

  const [c1] = card('Lots en cours', `${enCours.length} commande(s) en attente d'arrivee a Tana`, { flush: true });
  const wrap1 = el('div', 'tablewrap');
  if (!enCours.length) wrap1.appendChild(el('div', 'empty', '<span class="big">🚚</span>Aucun lot en cours : tout est receptionne'));
  else {
    const t = el('table', '', `<thead><tr><th>Lot</th><th>Produit</th><th>Fournisseur</th><th style="text-align:right">Quantite</th><th>Statut</th><th></th></tr></thead>`);
    const tb = el('tbody');
    for (const l of enCours) {
      const tr = el('tr', '', `
        <td><b style="font-weight:700">${esc(l.id_commande)}</b><div class="sub2 mono">${esc(l.date_commande)}</div></td>
        <td><b style="font-weight:650">${esc(l.nom_produit)}</b><div class="sub2 mono">${esc(l.id_produit)} → ${esc(l.id_entrepot)}</div></td>
        <td>${esc(l.nom_fournisseur)}<div class="sub2 mono">${esc(l.mode_fret)} · ${esc(l.delai_promis_jours)} j promis</div></td>
        <td class="mono" style="text-align:right">${nfr(l.quantite_commandee)} u.${l.quantite_recue_totale ? `<div class="sub2">${nfr(l.quantite_recue_totale)} deja recue(s)</div>` : ''}</td>
        <td><span class="chip ${l.statut === 'partielle' ? 'warn' : 'info'}">${esc(fmtStatut[l.statut] || l.statut)}</span></td>
        <td style="text-align:right"><button class="btn sm ok" title="Enregistrer l'arrivee du lot a la date du jour">${ICONS.check} Recevoir</button></td>`);
      tr.querySelector('button').onclick = async function () {
        this.disabled = true;
        const r = await api('/lots/' + l.id_commande + '/reception', { method: 'POST', body: {} });
        if (r.ok) toast(`${ICONS.check} Reception ${esc(r.reception.id_reception)} : lot ${esc(l.id_commande)} ${esc(r.statut_lot)}`);
        else toast('⚠ ' + esc(r.erreur || 'echec'));
        await rafraichir(['lots', 'etat']); render();
      };
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap1.appendChild(t);
  }
  c1.appendChild(wrap1);
  left.appendChild(c1);

  const [c2] = card('Lots receptionnes', 'Les 15 plus recents arrives a Tana', { flush: true });
  const wrap2 = el('div', 'tablewrap');
  if (!recues.length) wrap2.appendChild(el('div', 'empty', '<span class="big">📭</span>Aucun lot receptionne'));
  else {
    const t = el('table', '', `<thead><tr><th>Lot</th><th>Produit</th><th style="text-align:right">Recue</th><th>Arrivee</th></tr></thead>`);
    const tb = el('tbody');
    for (const l of recues) tb.appendChild(el('tr', '', `
      <td><b style="font-weight:700">${esc(l.id_commande)}</b><div class="sub2 mono">${esc(l.date_commande)}</div></td>
      <td><b style="font-weight:650">${esc(l.nom_produit)}</b><div class="sub2">${esc(l.nom_fournisseur)} · ${esc(l.mode_fret)}</div></td>
      <td class="mono" style="text-align:right">${nfr(l.quantite_recue_totale)} / ${nfr(l.quantite_commandee)} u.</td>
      <td class="mono sub2">${esc((l.receptions.slice(-1)[0] || {}).date_reception || '')}</td>`));
    t.appendChild(tb); wrap2.appendChild(t);
  }
  c2.appendChild(wrap2);
  left.appendChild(c2);
  v.appendChild(left);

  const right = el('div', 'gcol');
  const [c3, b3] = card('Nouveau lot d\'import', 'Une commande d\'achat par produit · mode et delai venant du contrat fournisseur');
  b3.innerHTML = `
    <div class="erreurbox" id="lotErr" style="display:none"></div>
    ${champ('Fournisseur', `<select id="lotFour">${optionsSelect(S.refs.fournisseurs, 'id_fournisseur', 'nom_fournisseur', '', 'Choisir un fournisseur')}</select>`,
      '<span id="lotFret"></span>')}
    ${champ('Entrepot de livraison', `<select id="lotEnt">${optionsSelect(S.refs.entrepots, 'id_entrepot', 'nom_entrepot', '', 'Choisir un entrepot')}</select>`)}
    <div class="field"><label>Produits du lot</label><div id="lotLignes"></div>
      <button class="btn sm ghost" id="lotAdd" type="button">${ICONS.plus} Ajouter un produit</button></div>
    ${champ('Date de commande', `<input id="lotDate" type="date" value="${aujourdhui()}">`)}
    <button class="btn ambre" id="lotBtn" style="width:100%;justify-content:center">${ICONS.check} Creer le lot</button>`;
  const majFret = () => {
    const f = S.refs.fournisseurs.find(x => x.id_fournisseur === b3.querySelector('#lotFour').value);
    b3.querySelector('#lotFret').textContent = f ? `Fret ${f.mode_fret} · ${f.delai_contractuel_jours} jours promis (contrat fournisseur)` : '';
  };
  b3.querySelector('#lotFour').onchange = majFret;
  const ajouterLigne = () => {
    const row = el('div', 'frow2');
    row.style.marginBottom = '8px';
    row.innerHTML = `<select class="lp" style="font:inherit;padding:9.5px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink)">
        ${optionsSelect(S.produits || [], 'id_produit', 'nom_produit', '', 'Produit')}</select>
      <input class="lq" type="number" min="1" placeholder="quantite" style="font:inherit;padding:9.5px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink)">`;
    b3.querySelector('#lotLignes').appendChild(row);
  };
  b3.querySelector('#lotAdd').onclick = ajouterLigne;
  ajouterLigne();
  b3.querySelector('#lotBtn').onclick = async function () {
    const lignes = [...b3.querySelectorAll('#lotLignes .frow2')]
      .map(r => ({ id_produit: r.querySelector('.lp').value, quantite: r.querySelector('.lq').value }))
      .filter(x => x.id_produit || x.quantite);
    this.disabled = true;
    const r = await api('/lots', { method: 'POST', body: {
      id_fournisseur: b3.querySelector('#lotFour').value,
      id_entrepot: b3.querySelector('#lotEnt').value,
      lignes, date_commande: b3.querySelector('#lotDate').value,
    } });
    this.disabled = false;
    const err = b3.querySelector('#lotErr');
    if (!r.ok) { err.style.display = ''; err.textContent = r.erreur || 'echec'; return; }
    err.style.display = 'none';
    toast(`${ICONS.check} Lot cree : ${r.commandes.map(c => esc(c.id_commande)).join(', ')} (fret ${esc(r.mode_fret)})`);
    await rafraichir(['lots', 'etat']); render();
  };
  right.appendChild(c3);
  v.appendChild(right);
  return v;
}

function vPublication() {
  const v = el('div');
  if (!S.etat) { v.appendChild(el('div', 'empty', 'Chargement en cours')); return v; }
  const e = S.etat;
  const n = e.modifications_en_attente || 0;
  const build = e.build || {};
  const enCours = e.publication_en_cours || build.statut === 'en_cours';

  v.appendChild(el('div', 'notice', `<b>L'operationnel ecrit, l'analytique lit.</b> Les saisies modifient les seeds CSV du repo dbt.
    Publier lance le build complet de l'entrepot (modeles + controles qualite) : SupplySense ne voit les saisies qu'apres un build vert.`));

  const g = el('div', 'pubgrid');
  const left = el('div', 'gcol');
  const [c1, b1] = card('Publication vers l\'entrepot', 'dbt build + controles qualite, puis SupplySense est a jour');
  let verdict = '';
  if (enCours) {
    verdict = `<div class="buildbar"><span class="anim"></span></div>
      <div class="sub2">Build en cours (declencheur ${esc(build.declencheur || 'supplyops')})... la page se met a jour toute seule.</div>`;
  } else if (S.pubApresLancement || build.statut === 'succes' || build.statut === 'echec') {
    if (build.statut === 'succes') {
      verdict = `<div class="verdict ok"><span class="vico">${ICONS.check}</span><div style="flex:1">
        <b>Build vert : ${nfr(build.pass)} / ${nfr(build.total)} modeles et controles passes</b>
        <div class="sub2">${esc(build.horodatage || '')} · ${nfr(build.duree_s, 1)} s · declencheur ${esc(build.declencheur || '')}</div></div>
        <a class="btn" href="${esc(e.url_supplysense)}" target="_blank" rel="noopener">Voir dans SupplySense ↗</a></div>`;
    } else if (build.statut === 'echec') {
      verdict = `<div class="verdict bad"><span class="vico">${ICONS.croix}</span><div style="flex:1">
        <b>Build en echec : ${nfr(build.fail)} controle(s) sur ${nfr(build.total)} ont casse</b>
        <div class="sub2">Le gardien de qualite a depose un diagnostic dans outbox/ · les saisies restent en attente</div></div></div>`;
    }
  }
  b1.innerHTML = `
    <div class="pubhero">
      <div class="pubnum ${n ? '' : 'zero'}">${n}</div>
      <div><div class="t">${n ? `modification${n > 1 ? 's' : ''} en attente de publication` : 'Aucune modification en attente'}</div>
        <div class="s">${n ? 'Les saisies sont ecrites dans les seeds mais pas encore dans l\'entrepot analytique.'
          : 'Les seeds et l\'entrepot sont alignes' + (e.derniere_publication ? ' · derniere publication ' + esc(e.derniere_publication) : '') + '.'}</div></div>
    </div>
    <button class="btn ambre" id="pubBtn" ${enCours ? 'disabled' : ''} style="width:100%;justify-content:center;font-size:13.5px;padding:11px">
      ${ICONS.publier} ${enCours ? 'Publication en cours...' : 'Publier vers l\'entrepot'}</button>
    ${verdict}`;
  b1.querySelector('#pubBtn').onclick = async function () {
    this.disabled = true;
    const r = await fetch('api/publier', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(x => ({ code: x.status, corps: x }));
    const corps = await r.corps.json();
    if (r.code === 409) { toast('⚠ ' + esc(corps.erreur || 'publication deja en cours')); this.disabled = false; return; }
    if (!corps.ok) { toast('⚠ ' + esc(corps.erreur || 'echec du lancement')); this.disabled = false; return; }
    S.pubApresLancement = true;
    toast(`${ICONS.publier} Publication lancee : dbt build en cours`);
    demarrerSondage();
    await rafraichir(['etat']); render();
  };
  left.appendChild(c1);

  if ((build.historique || []).length) {
    const [c2, b2] = card('Historique des builds', 'Les 20 derniers, du plus recent au plus ancien');
    b2.innerHTML = `<div class="histbuild">${build.historique.map(h =>
      `<i class="${h.statut === 'succes' ? 's' : 'e'}" title="${esc(h.horodatage)} · ${esc(h.statut)} · ${h.pass}/${h.total} (${esc(h.declencheur || '')})"></i>`).join('')}</div>
      <div class="sub2" style="margin-top:9px">${build.historique.filter(h => h.statut === 'succes').length} succes ·
      ${build.historique.filter(h => h.statut !== 'succes').length} echec(s) sur ${build.historique.length} build(s)</div>`;
    left.appendChild(c2);
  }
  g.appendChild(left);

  const right = el('div', 'gcol');
  const [c3, b3] = card('Journal des saisies', 'Ce qui attend la prochaine publication', { flush: false });
  const journal = (e.journal || []).slice(0, 14);
  if (!journal.length) b3.innerHTML = '<div class="empty" style="padding:20px"><span class="big">📝</span>Aucune saisie enregistree pour l\'instant</div>';
  else b3.innerHTML = `<div class="jrnl">${journal.map(j => `<div class="jrow">
    <span class="jh">${esc((j.horodatage || '').slice(5, 16).replace('T', ' '))}</span>
    <span class="ja">${esc(fmtAction[j.action] || j.action)}</span>
    <span style="min-width:0">${esc(j.detail)}</span></div>`).join('')}</div>`;
  right.appendChild(c3);
  g.appendChild(right);
  v.appendChild(g);
  return v;
}

// sondage pendant la publication
function demarrerSondage() {
  if (S.pubSondage) return;
  S.pubSondage = setInterval(async () => {
    await rafraichir(['etat']);
    const e = S.etat || {};
    const enCours = e.publication_en_cours || (e.build || {}).statut === 'en_cours';
    if (!enCours) {
      clearInterval(S.pubSondage); S.pubSondage = null;
      const b = e.build || {};
      toast(b.statut === 'succes'
        ? `${ICONS.check} Build vert : ${nfr(b.pass)}/${nfr(b.total)} controles passes, SupplySense est a jour`
        : `⚠ Build en echec : ${nfr(b.fail)} controle(s) casse(s), diagnostic dans outbox/`);
    }
    if (S.page === 'publication') render();
  }, 1500);
}

// ---------------- chargement ----------------
async function chargerEtat() {
  try {
    const e = await api('/etat');
    S.etat = e;
    const enCours = e.publication_en_cours || (e.build || {}).statut === 'en_cours';
    $('#apiDot').className = 'dotlive ' + (enCours ? 'warn' : 'ok');
    $('#sideSeeds').textContent = (e.seeds || '').split('/').slice(-3).join('/');
    $('#sidePending').textContent = (e.modifications_en_attente || 0) + ' saisie(s) en attente';
    if (enCours && !S.pubSondage) demarrerSondage();
  } catch (err) {
    $('#apiDot').className = 'dotlive bad';
    $('#sideSeeds').textContent = 'API injoignable';
  }
}

const CHARGEURS = {
  etat: chargerEtat,
  refs: async () => { const r = await api('/referentiels'); if (r.ok) S.refs = r; },
  produits: async () => { const r = await api('/produits'); if (r.ok) S.produits = r.produits; },
  inventaire: async () => { const r = await api('/inventaire'); if (r.ok) S.inventaire = r.inventaire; },
  ventes: async () => { const r = await api('/ventes'); if (r.ok) S.ventes = r.ventes; },
  lots: async () => { const r = await api('/lots'); if (r.ok) S.lots = r.lots; },
};
async function rafraichir(cles) { await Promise.all(cles.map(c => CHARGEURS[c]())); }

// ---------------- router / shell ----------------
const PAGES = [
  ['produits', 'Produits', ICONS.produit, 1],
  ['inventaire', 'Inventaire', ICONS.inventaire, 1],
  ['ventes', 'Ventes', ICONS.vente, 1],
  ['lots', 'Lots d\'import', ICONS.lot, 1],
  ['publication', 'Publication', ICONS.publier, 2],
];
const BESOINS = {
  produits: ['refs', 'produits'],
  inventaire: ['refs', 'produits', 'inventaire'],
  ventes: ['refs', 'produits', 'ventes'],
  lots: ['refs', 'produits', 'lots'],
  publication: ['etat'],
};
const SUBS = {
  produits: () => 'Catalogue : creation, prix, familles du contrat',
  inventaire: () => 'Photographies de stock par produit et entrepot',
  ventes: () => 'Saisie rapide et journal des ventes',
  lots: () => 'Commandes fournisseurs AIR / SEA et receptions',
  publication: () => 'Envoyer les saisies vers l\'entrepot analytique',
};
const VIEWS = { produits: vProduits, inventaire: vInventaire, ventes: vVentes, lots: vLots, publication: vPublication };

function nav() {
  const counts = { publication: S.etat ? (S.etat.modifications_en_attente || 0) : 0 };
  const lotsEnCours = S.lots ? S.lots.filter(l => l.statut !== 'recue').length : 0;
  if (lotsEnCours) counts.lots = lotsEnCours;
  const n1 = $('#nav1'), n2 = $('#nav2');
  n1.innerHTML = ''; n2.innerHTML = '';
  for (const [id, label, icon, grp] of PAGES) {
    const a = el('a', S.page === id ? 'on' : '', icon + `<span class="txt">${label}</span>` +
      (counts[id] ? `<span class="count ${id === 'publication' ? 'hot' : ''}">${counts[id]}</span>` : ''));
    a.href = '#' + id;
    (grp === 1 ? n1 : n2).appendChild(a);
  }
}

function render() {
  nav();
  const meta = PAGES.find(x => x[0] === S.page) || PAGES[0];
  $('#pageTitle').textContent = meta[1];
  $('#crumb').childNodes[0].textContent = 'SupplyOps / ' + (SUBS[S.page] ? SUBS[S.page]() : '');
  const view = $('#view');
  view.innerHTML = '';
  const node = (VIEWS[S.page] || vProduits)();
  node.classList.add('view-enter');
  view.appendChild(node);
}

async function aller(page) {
  S.page = VIEWS[page] ? page : 'produits';
  render();
  await rafraichir(BESOINS[S.page] || []);
  render();
}

window.addEventListener('hashchange', () => { S.q = ''; S.filtreFamille = ''; $('#q').value = ''; fermerPanneau(); aller(location.hash.slice(1) || 'produits'); });
$('#q').addEventListener('input', e => { S.q = e.target.value.toLowerCase(); render(); });

// theme : persiste, forcable par ?theme=dark|light au chargement
const SUN = '<path d="M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v3m0 16v3M4.2 4.2l2.1 2.1m11.4 11.4 2.1 2.1M1 12h3m16 0h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>';
const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>';
function applyTheme(t, persist = true) {
  document.documentElement.dataset.theme = t;
  $('#themeIco').innerHTML = t === 'dark' ? SUN : MOON;
  if (persist) localStorage.setItem('so-theme', t);
}
$('#themeBtn').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
const params = new URLSearchParams(location.search);
const themeForce = params.get('theme');
if (themeForce === 'dark' || themeForce === 'light') applyTheme(themeForce, false);
else applyTheme(localStorage.getItem('so-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'), false);
// ?motion=0 : coupe animations et transitions (captures et tests visuels)
if (params.get('motion') === '0') {
  const st = document.createElement('style');
  st.textContent = '*{animation:none!important;transition:none!important}';
  document.head.appendChild(st);
}
// ?panneau=produit : ouvre le panneau de creation (captures)
const panneauAuto = params.get('panneau');

// skeleton de premier chargement puis boot
$('#view').innerHTML = `<div class="kpis">${'<div class="kpi"><div class="skel" style="height:14px;width:60%"></div><div class="skel" style="height:34px;width:40%;margin-top:10px"></div></div>'.repeat(4)}</div>
  <div class="grid2"><div class="card"><div class="cbody"><div class="skel" style="height:180px"></div></div></div>
  <div class="card"><div class="cbody"><div class="skel" style="height:180px"></div></div></div></div>`;
(async () => {
  await chargerEtat();
  setInterval(chargerEtat, 15000);
  await rafraichir(['refs', 'produits']);
  await aller(location.hash.slice(1) || 'produits');
  if (panneauAuto === 'produit' && S.refs) formulaireProduit(null);
})();
