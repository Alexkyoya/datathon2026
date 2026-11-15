'use strict';

const $ = id => document.getElementById(id);
// Bilingual interface: French (default) or English, from ?lang= or the last choice.
const LANG = (() => {
  const asked = new URLSearchParams(location.search).get('lang');
  if (asked === 'en' || asked === 'fr') { try { localStorage.setItem('second-look:lang', asked); } catch {} return asked; }
  try { return localStorage.getItem('second-look:lang') === 'en' ? 'en' : 'fr'; } catch { return 'fr'; }
})();
const L = (fr, en) => LANG === 'en' ? en : fr;
const LOCALE = L('fr-CA', 'en-CA');
const state = {
  context: null, selectedAsset: 'CNR.TO', side: 'buy', record: null,
  evaluation: 0, accountLoad: 0, evaluating: false, deciding: false, reviewing: false,
  evaluationController: null, reviewController: null, timer: null, autoEvaluate: false,
  returnFocus: null, tab: 'overview', chat: null, pendingMessage: null, evidence: []
};
const DEMOS = {
  robert: [['SPY',30000],['NVDA',360000],['XBB.TO',340000]],
  mateo: [['ESGV',12000],['XOM',3000],['XIU.TO',5000]],
  amira: [['QQQ',5000],['ETH-USD',3000],['NVDA',5000]],
  demo_balanced: [['SPY',4000],['GLD',14000],['TSLA',12000]]
};
const classes = {cash:L('Liquidités','Cash-like'), fixed_income:L('Revenu fixe','Fixed income'), equities:L('Actions','Equities'), alternatives:L('Placements alternatifs','Alternatives')};
const NA = L('Non disponible','Unavailable');
const profileFr = profile => LANG==='en' ? profile : ({Income:'Revenu',Conservative:'Conservateur',Balanced:'Équilibré',Growth:'Croissance','Maximum growth':'Croissance maximale'})[profile] || profile;
const money = value => new Intl.NumberFormat(LOCALE,{style:'currency',currency:'CAD',maximumFractionDigits:2}).format(value);
const decimal = (value, digits=2) => Number.isFinite(value) ? new Intl.NumberFormat(LOCALE,{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(value) : NA;
const percent = value => Number.isFinite(value) ? L(`${(value*100).toFixed(1).replace('.',',')}\u00a0%`,`${(value*100).toFixed(1)}%`) : NA;
const dateLabel = value => value ? new Intl.DateTimeFormat(LOCALE,{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(value.slice(0,10)+'T00:00:00Z')) : NA;
const node = (tag, value='', className='') => {
  const result=document.createElement(tag); result.textContent=value;
  if(className) result.className=className;
  return result;
};
const finite = value => typeof value==='number' && Number.isFinite(value);
const isTerminal = record => record && ['simulation_continued','cancelled'].includes(record.stage);
const friendly = message => {
  if(/Insufficient cash/i.test(message)) return L('Ce montant dépasse votre encaisse disponible. Aucune autre position n’est vendue automatiquement.','This amount exceeds your available cash. No other holding is sold automatically.');
  if(/Insufficient holdings/i.test(message)) return L('Ce montant dépasse la valeur des unités que vous détenez. Les ventes à découvert ne sont pas prises en charge.','This amount exceeds the value of the units you hold. Short sales are not supported.');
  if(/stale|Future|Unusable|Missing asset or price/i.test(message)) return L('Aucun cours historique utilisable n’est disponible pour cette transaction. Elle n’a pas été évaluée.','A usable historical quote is unavailable for this trade. It has not been evaluated.');
  return message;
};
function announce(message) { $('announcer').textContent=message; }
function inlineError(id, message) { $(id).textContent=message; $(id).hidden=!message; }

async function api(path, payload, signal, timeout=20000) {
  const controller=new AbortController(); let expired=false;
  const abort=()=>controller.abort();
  signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(()=>{expired=true;controller.abort();},timeout);
  try {
    const response=await fetch(path,{...(payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {}),signal:controller.signal});
    let data;
    try { data=await response.json(); } catch { throw new Error(L('Le service a renvoyé une réponse illisible. Veuillez réessayer.','The service returned an unreadable response. Please try again.')); }
    if(!response.ok) throw new Error(data.error || L('La demande n’a pas pu être traitée. Veuillez réessayer.','The request could not be completed. Please try again.'));
    return data;
  } catch(error) {
    if(expired) throw new Error(L('La demande a expiré. Aucune confirmation n’a été enregistrée dans cette vue. Veuillez réessayer.','The request timed out. No confirmation was recorded in this view. Please retry.'));
    if(error.name==='TypeError') throw new Error(L('Le service est inaccessible. Vérifiez la connexion et réessayez.','The service could not be reached. Check the connection and try again.'));
    throw error;
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}

function resolveAsset(value) {
  const text=value.trim().toLowerCase();
  return state.context?.tickers.find(t=>t.toLowerCase()===text || state.context.assets[t].name.toLowerCase()===text);
}
function fillPair(parent,label,value) {
  if(value===null || value===undefined) return;
  const pair=node('div','','info-pair');pair.append(node('span',label,'small-label'),node('strong',value));parent.append(pair);
}
function infoGroups(container, groups) {
  container.replaceChildren(...groups.map(pairs=>{const group=node('div','','info-group');pairs.forEach(([label,value])=>fillPair(group,label,value));return group;}));
}
function changeText(quote) {
  if(!finite(quote?.change_native) || !finite(quote?.change_fraction)) return NA;
  const sign=quote.change_native>=0?'+':'';
  return `${sign}${decimal(quote.change_native)} (${sign}${percent(quote.change_fraction)})`;
}
function setChange(element,quote) {
  element.textContent=changeText(quote);
  element.className=finite(quote?.change_native)?(quote.change_native>=0?'positive':'negative'):'';
}
function makeTable(headers, rows) {
  const table=node('table'),thead=node('thead'),head=node('tr'),tbody=node('tbody');
  headers.forEach(label=>{const th=node('th',label);th.scope='col';head.append(th);});thead.append(head);
  rows.forEach(cells=>{const row=node('tr');cells.forEach(value=>{const cell=node('td');cell.append(value instanceof Node?value:document.createTextNode(String(value)));row.append(cell);});tbody.append(row);});
  table.append(thead,tbody);return table;
}

async function loadContext(clientId) {
  const token=++state.accountLoad;
  invalidate(false); if($('trade-dialog').open) $('trade-dialog').close();
  $('review-panel').hidden=true;setInvitation(false);
  state.context=null;
  $('client-select').disabled=true;$('market-search').disabled=true;$('scenario-select').disabled=true;$('scenario-cards').replaceChildren();state.recent=[];
  $('account-name').textContent=L('Chargement du compte…','Loading account…');
  ['account-total','account-invested','account-cash'].forEach(id=>$(id).textContent='—');
  document.querySelectorAll('[data-open-side]').forEach(button=>button.disabled=true);
  inlineError('page-error-text','');$('page-error').hidden=true;
  try {
    const context=await api('/api/context?client_id='+encodeURIComponent(clientId));
    if(token!==state.accountLoad)return;
    state.context=context;
    $('account-name').textContent=context.client.name || L('Démonstration équilibrée','Balanced demonstration');
    $('account-profile').textContent=L('Profil : ',' profile')+profileFr(context.client.profile);
    $('account-total').textContent=money(context.account.total_cad);
    $('account-invested').textContent=money(context.account.invested_cad);
    $('account-cash').textContent=money(context.account.cash_cad);
    $('snapshot-label').textContent=L('Instantané du compte · ','Account snapshot · ')+dateLabel(context.client.holdings_as_of)+' · 00:00 UTC';
    $('user-avatar').textContent=(context.first_name || 'S').slice(0,1);
    $('client-select').replaceChildren(...context.clients.map(id=>{const option=node('option',id==='demo_balanced'?L('Démo équilibrée','Balanced demo'):id[0].toUpperCase()+id.slice(1));option.value=id;return option;}));
    $('client-select').value=context.client.client_id;$('client-select').disabled=false;
    $('scenario-select').replaceChildren(node('option',L('Choisir un scénario','Select a scenario')),...(DEMOS[context.client.client_id]||[]).map(([ticker,amount],i)=>{const option=node('option',L(`Achat ${ticker} · ${money(amount)}`,`Buy ${ticker} · ${money(amount)}`));option.value=String(i);return option;}));
    $('scenario-select').options[0].value='';$('scenario-select').value='';$('scenario-select').disabled=false;renderScenarioCards();
    $('market-search').disabled=false;
    if(!context.assets[state.selectedAsset])state.selectedAsset=context.tickers[0];
    renderHoldings();renderQuote();await restoreConversation(token);
  } catch(error) {
    if(token!==state.accountLoad)return;
    inlineError('page-error-text',error.message);$('page-error').hidden=false;
    $('client-select').disabled=false;
    announce(L('Les données du compte n’ont pas pu être chargées.','Account data could not be loaded.'));
  }
}

function renderHoldings() {
  const ctx=state.context;
  const rows=ctx.holdings.map(h=>{
    const button=node('button',h.ticker,'text-button');button.type='button';button.setAttribute('aria-label',L(`Voir la cote de ${h.name}`,`View ${h.name} quote`));
    button.addEventListener('click',()=>{state.selectedAsset=h.ticker;showPage('markets');renderQuote();$('asset-name').scrollIntoView({block:'nearest'});});
    return [button,h.name,decimal(h.units,4),h.currency,money(h.value_cad),percent(h.weight)];
  });
  rows.push([L('Encaisse','Cash'),L('Solde en espèces CAD','CAD cash ledger'),'—','CAD',money(ctx.account.cash_cad),percent(ctx.account.cash_cad/ctx.account.total_cad)]);
  $('holdings-table').replaceChildren(makeTable([L('Titre','Asset'),L('Nom','Name'),L('Unités','Units'),L('Devise de cotation','Listing currency'),L('Valeur (CAD)','Value (CAD)'),L('Poids au portefeuille','Portfolio weight')],rows));
  const profile=node('div');profile.append(node('p',L(`${profileFr(ctx.client.profile)} · horizon de ${ctx.client.horizon_years} ans`,`${ctx.client.profile} · ${ctx.client.horizon_years}-year horizon`)),node('p',L('Objectifs : ','Objectives: ')+ctx.client.objectives.join(', ').replaceAll('_',' ')));
  const goals=node('div');
  (ctx.client.goals||[]).forEach(goal=>goals.append(node('p',goal.name+' · '+(goal.type==='one_off'?money(goal.amount_cad)+L(' d’ici le ',' by ')+dateLabel(goal.target_date):money(goal.amount_cad_per_year)+L('/an à compter du ','/year from ')+dateLabel(goal.start_date)))));
  if(!goals.children.length)goals.append(node('p',L('Aucun objectif daté n’est enregistré pour ce compte de démonstration.','No dated goals are stored for this demo account.')));
  const esg=ctx.client.preferences.esg;
  if(esg?.excluded_sectors?.length)profile.append(node('p',L('Secteurs exclus : ','Excluded sectors: ')+esg.excluded_sectors.join(', ')));
  $('profile-details').replaceChildren(profile,goals);
}
function showPage(page) {
  $('market-view').hidden=page!=='markets';$('holdings-view').hidden=page!=='holdings';
  $('page-title').textContent=page==='markets'?L('Cote','Market quote'):L('Mes avoirs','My holdings');
  ['markets','holdings'].forEach(key=>{const button=$('nav-'+key);button.classList.toggle('active',key===page);if(key===page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
  if(page==='markets')renderChart();
}
function renderQuote() {
  const ctx=state.context,t=state.selectedAsset,a=ctx.assets[t],q=ctx.quotes[t],holding=ctx.holdings.find(h=>h.ticker===t);
  const history=(ctx.price_history[t]||[]).filter(p=>finite(p.close_cad)),closes=history.map(p=>p.close_cad);
  $('market-search').value=t;
  $('asset-name').textContent=`${t} ${a.name}`+(a.market?` (${a.market})`:'');
  $('asset-ticker').textContent=`${a.currency} · ${classes[a.asset_class]}`;
  $('quote-currency').textContent=q?'· '+q.currency:'';
  $('quote-price').textContent=q?decimal(q.close_native):NA;setChange($('quote-change'),q);
  const volume=finite(q?.volume)?new Intl.NumberFormat(LOCALE).format(q.volume):NA;
  $('quote-volume').textContent=volume;
  $('quote-date').textContent=dateLabel(q?.price_source_date);
  document.querySelectorAll('[data-open-side]').forEach(button=>{button.disabled=!q || (button.dataset.openSide==='sell' && !(holding?.units>0));});
  infoGroups($('quote-information'),[
    [[L('Ouverture','Open'),finite(q?.open_native)?decimal(q.open_native)+' '+a.currency:null],[L('Fermeture précédente','Previous close'),finite(q?.previous_close_native)?decimal(q.previous_close_native)+' '+a.currency:null],[L('Bas/Haut (jour)','Day low / high'),finite(q?.low_native)&&finite(q?.high_native)?`${decimal(q.low_native)} - ${decimal(q.high_native)}`:null]],
    [['Volume',volume],[L(`Bas/Haut (${closes.length} obs., CAD)`,`Low / high (${closes.length} obs., CAD)`),closes.length>1?`${decimal(Math.min(...closes))} - ${decimal(Math.max(...closes))}`:null],[L('Devise de cotation','Listing currency'),a.currency]],
    [[L('Catégorie d’actif','Asset class'),classes[a.asset_class]],[L('Marché','Market'),a.market||NA],[L('Secteur','Sector'),a.sector||NA]],
    [[L('Valeur par unité en CAD','Value per unit in CAD'),q?money(q.close_cad):NA],[L('Taux de change','FX to CAD'),q?decimal(q.fx_to_cad,4)+L(' CAD / ',' CAD per ')+a.currency:NA],[L('Date du taux de change','FX source date'),a.currency==='CAD'?L('Non requis (CAD)','Not required (CAD)'):dateLabel(q?.fx_source_date)]]
  ]);
  infoGroups($('position-information'),[
    [[L('Unités détenues','Units held'),decimal(holding?.units||0,4)],[L('Valeur marchande (CAD)','Market value (CAD)'),money(holding?.value_cad||0)],[L('Poids au portefeuille','Portfolio weight'),percent(holding?.weight||0)]],
    [[L('Encaisse disponible','Available cash'),money(ctx.account.cash_cad)],[L('Profil','Profile'),profileFr(ctx.client.profile)],[L('Avoirs en date du','Holdings as of'),dateLabel(ctx.client.holdings_as_of)]]
  ]);
  renderPerformance(history);
  $('history-table').replaceChildren(history.length?makeTable([L('Date d’observation','Observation date'),L('Cours de clôture (CAD)','Closing price (CAD)')],history.slice(-12).reverse().map(p=>[dateLabel(p.date),money(p.close_cad)])):node('p',L('L’historique des cours n’est pas disponible dans cet instantané.','Price history is unavailable in this snapshot.'),'empty-state'));
  $('quote-source').textContent=L('Source : jeu de données AlphaForge · cours Yahoo Finance · taux de change de la Banque du Canada. ','Source: AlphaForge starter dataset · Yahoo Finance prices · Bank of Canada FX. ')+(q?.price_age_days>0?L('Le dernier cours disponible est reporté. ','The last available price is carried forward. '):'')+L('Aucun cours acheteur/vendeur, aucune recommandation d’analyste ni cote d’exécution ne sont fournis.','No bid/ask, analyst ratings or execution quotes are supplied.');
  renderChart();
}
// Returns over completed source observations only; periods the history cannot cover are left out.
function renderPerformance(points) {
  const box=$('performance-list'),last=points.at(-1)?.close_cad,spans=[[L('5J','5D'),5],['1M',21],['3M',63]];
  const rows=spans.filter(([,n])=>points.length>n).map(([label,n])=>[label,last/points[points.length-1-n].close_cad-1]);
  if(points.length>1&&!spans.some(([,n])=>n===points.length-1))rows.push([`${points.length} obs.`,last/points[0].close_cad-1]);
  if(!rows.length){box.replaceChildren(node('p',L('Le rendement n’est pas disponible dans cet instantané.','Performance is unavailable in this snapshot.'),'empty-state'));return;}
  const scale=Math.max(...rows.map(row=>Math.abs(row[1])),1e-4);
  box.replaceChildren(...rows.map(([label,value])=>{
    const row=node('div','','performance-row'),track=node('span','','performance-bar'),bar=node('i','',value>=0?'up':'down');
    bar.style.width=Math.max(4,Math.abs(value)/scale*100)+'%';track.append(bar);
    row.append(node('span',label),node('strong',(value>=0?'+':'')+percent(value),value>=0?'positive':'negative'),track);return row;
  }));
}
function renderChart() {
  if(!state.context)return;
  const points=(state.context.price_history[state.selectedAsset]||[]).filter(p=>finite(p.close_cad));
  if(points.length<2){$('price-chart').replaceChildren(node('p',L('Le graphique n’est pas disponible dans cet instantané historique.','A price chart is unavailable in this historical snapshot.'),'empty-state'));$('chart-period').textContent='';return;}
  const width=Math.max(300,$('price-chart').clientWidth||900),height=200,left=64,right=15,top=12,bottom=27;
  const prices=points.map(p=>p.close_cad),lo=Math.min(...prices),hi=Math.max(...prices),pad=Math.max((hi-lo)*.12,.01);
  const min=lo-pad,max=hi+pad,x=i=>left+i*(width-left-right)/(points.length-1),y=p=>top+(max-p)*(height-top-bottom)/(max-min);
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.classList.add('price-svg');svg.setAttribute('role','img');svg.setAttribute('aria-label',L(`Cours de clôture historiques en CAD de ${state.selectedAsset}, du ${points[0].date} au ${points.at(-1).date}. Plus bas ${money(lo)}, plus haut ${money(hi)}.`,`${state.selectedAsset} historical CAD closing prices from ${points[0].date} to ${points.at(-1).date}. Lowest ${money(lo)}, highest ${money(hi)}.`));
  const part=(tag,attrs,value)=>{const el=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);if(value!==undefined)el.textContent=value;svg.append(el);return el;};
  [0,.5,1].forEach(r=>{const value=max-r*(max-min),at=y(value);part('line',{x1:left,x2:width-right,y1:at,y2:at,stroke:'#e6e6e6'});part('text',{x:left-10,y:at+4,'text-anchor':'end'},decimal(value));});
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)} ${y(p.close_cad).toFixed(2)}`).join(' ');
  part('path',{d:path+` L ${x(points.length-1)} ${height-bottom} L ${left} ${height-bottom} Z`,fill:'#fdeced'});
  part('path',{d:path,fill:'none',stroke:'#e41c23','stroke-width':'2','stroke-linejoin':'round'});
  part('circle',{cx:x(points.length-1),cy:y(points.at(-1).close_cad),r:3.5,fill:'#e41c23'});
  part('text',{x:left,y:height-5},dateLabel(points[0].date));part('text',{x:width-right,y:height-5,'text-anchor':'end'},dateLabel(points.at(-1).date));
  $('price-chart').replaceChildren(svg);$('chart-period').textContent=`${points.length} observations · ${dateLabel(points[0].date)} – ${dateLabel(points.at(-1).date)}`;
}
function selectTab(tab) {
  state.tab=tab;
  ['overview','history'].forEach(key=>{const selected=key===tab;$(key+'-panel').hidden=!selected;$('tab-'+key).setAttribute('aria-selected',String(selected));$('tab-'+key).tabIndex=selected?0:-1;});
  renderChart();
}

function setInvitation(visible) {
  const show=visible && state.record?.result.verdict==='red' && !isTerminal(state.record);
  $('agent-invite').hidden=!show;$('dialog-layout').classList.toggle('with-invitation',show);
}
function invalidate(forget=true) {
  if(forget && state.context)sessionStorage.removeItem('second-look:'+state.context.client.client_id);
  clearTimeout(state.timer);state.evaluation++;state.evaluationController?.abort();state.reviewController?.abort();
  state.record=null;state.chat=null;state.pendingMessage=null;state.evidence=[];state.evaluating=false;state.reviewing=false;state.autoEvaluate=false;
  $('reason').value='';
  setInvitation(false);$('review-panel').hidden=true;$('risk-checkbox').checked=false;
  inlineError('ticket-error','');inlineError('review-error','');
  $('ticket-status').hidden=true;syncButtons();
}
function draftRequest() {
  const ticker=resolveAsset($('ticket-asset').value),amount=Number($('amount').value);
  if(!ticker)throw new Error(L('Choisissez un titre dans la liste fournie.','Choose an asset from the provided ticker list.'));
  if(!finite(amount)||amount<=0)throw new Error(L('Entrez un montant en CAD supérieur à zéro.','Enter a CAD amount greater than zero.'));
  const q=state.context.quotes[ticker];
  if(!q)throw new Error(L('Aucun cours historique utilisable n’est disponible pour ce titre.','No usable historical quote is available for this asset.'));
  const available=state.side==='buy'?state.context.account.cash_cad:(state.context.holdings.find(h=>h.ticker===ticker)?.value_cad||0);
  if(amount>available+1e-8)throw new Error(state.side==='buy'?L(`Cette transaction coûte ${money(amount)}, mais l’encaisse disponible est de ${money(available)}.`,`This trade costs ${money(amount)}, but only ${money(available)} is available in cash.`):L(`Votre position vaut ${money(available)}. Vous ne pouvez pas vendre plus que ce que vous détenez.`,`Your holding is worth ${money(available)}. You cannot sell more than you hold.`));
  return {client_id:state.context.client.client_id,decision_date:state.context.client.holdings_as_of,ticker,side:state.side,amount_cad:amount};
}
function openTicket(side=state.side, ticker=state.selectedAsset, amount='', resume=false) {
  if(!state.context)return;
  state.returnFocus=document.activeElement;
  if(!resume){invalidate();state.side=side;$('ticket-asset').value=ticker;$('amount').value=amount;}
  $('ticket-review-slot').append($('review-panel'));
  $('ticket-account-name').textContent=(state.context.client.name||L('Démo équilibrée','Balanced demo'))+L(' · Simulation CAD',' · CAD simulation');
  $('ticket-cash').textContent=money(state.context.account.cash_cad);
  updateTicketQuote();
  if(!$('trade-dialog').open)$('trade-dialog').showModal();
  if(resume && state.record?.result.verdict==='red')revealReview(false);
  $('amount').focus();
}
function closeTicket() {
  setInvitation(false);
  if($('trade-dialog').open)$('trade-dialog').close();
  $('page-review-slot').append($('review-panel'));
  if(state.record?.result.verdict==='red'){revealReview(false);}
  state.returnFocus?.focus?.();
}
function updateTicketQuote() {
  if(!state.context)return;
  const ticker=resolveAsset($('ticket-asset').value),a=state.context.assets[ticker],q=state.context.quotes[ticker];
  $('ticket-title').textContent=state.side==='buy'?L('Achat','Buy'):L('Vente','Sell');
  $('side-buy').setAttribute('aria-pressed',String(state.side==='buy'));$('side-sell').setAttribute('aria-pressed',String(state.side==='sell'));
  $('ticket-title').style.color=state.side==='sell'?'#1a1a1a':'var(--blue)';
  $('ticket-asset-name').textContent=a?a.name:L('Choisissez un symbole ou une société dans la liste.','Choose a ticker or company from the list.');
  $('ticket-category').textContent=a?classes[a.asset_class]:L('Univers d’actifs fourni','Provided asset universe');
  $('ticket-quote-symbol').textContent=a?`${ticker} · ${a.currency}`:L('Cote non disponible','Quote unavailable');
  $('ticket-quote-price').textContent=q?decimal(q.close_native)+' '+q.currency:NA;setChange($('ticket-quote-change'),q);
  const details=[];
  if(q){details.push([L('Valeur CAD / unité','CAD value / unit'),money(q.close_cad)]);if(finite(q.volume))details.push(['Volume',new Intl.NumberFormat(LOCALE).format(q.volume)]);if(finite(q.low_native)&&finite(q.high_native))details.push([L('Bas/Haut (jour)','Day low / high'),decimal(q.low_native)+' / '+decimal(q.high_native)]);details.push([L('Taux de change','FX to CAD'),decimal(q.fx_to_cad,4)]);const holding=state.context.holdings.find(h=>h.ticker===ticker);details.push([L('Unités détenues','Units held'),decimal(holding?.units||0,4)]);if(state.side==='sell')details.push([L('Valeur de la position','Holding value'),money(holding?.value_cad||0)]);}
  $('ticket-quote-details').replaceChildren(...details.map(([label,value])=>{const group=node('div');group.append(node('dt',label),node('dd',value));return group;}));
  $('ticket-quote-date').textContent=q?L('Clôture historique · ','Historical close · ')+dateLabel(q.price_source_date):L('Un cours daté utilisable est requis','A usable dated quote is required');
  const amount=Number($('amount').value);
  $('unit-estimate').textContent=q&&amount>0?L(`Unités estimées : ${decimal(amount/q.close_cad,6)} · à ${money(q.close_cad)} CAD l’unité.`,`Estimated units: ${decimal(amount/q.close_cad,6)} · valued at ${money(q.close_cad)} CAD per unit.`):L('Entrez un montant en CAD pour voir le nombre d’unités estimé.','Enter a CAD amount to see estimated units.');
  syncButtons();
}
function syncButtons() {
  const record=state.record,busy=state.evaluating||state.deciding;
  $('submit-trade').disabled=busy||!!record;
  $('submit-trade').textContent=state.evaluating?L('Évaluation…','Evaluating…'):record?(record.result.verdict==='red'?L('Transaction suspendue','Trade paused'):isTerminal(record)?L('Ordre terminé','Order complete'):L('Révision requise','Review required')):L('Soumettre','Submit');
  $('submit-trade').className='button '+(state.side==='sell'?'red':'blue');
  $('cancel-trade').disabled=state.deciding;$('cancel-trade').textContent=isTerminal(state.record)?L('Fermer','Close'):L('Annuler','Cancel');
  ['amount','ticket-asset','side-buy','side-sell'].forEach(id=>$(id).disabled=state.deciding);
  $('send-reason').disabled=state.reviewing||state.deciding||!record||isTerminal(record)||!!state.pendingMessage||state.chat?.messages.filter(m=>m.role==='user').at(-1)?.status==='failed';
  $('send-reason').textContent=state.reviewing?L('Réponse en cours…','Replying…'):L('Envoyer','Send');
  $('reason').disabled=state.reviewing||state.deciding||isTerminal(record)||!!state.pendingMessage;
  $('risk-checkbox').disabled=state.deciding||isTerminal(record);
  $('proceed-red').disabled=!$('risk-checkbox').checked||state.deciding||isTerminal(record)||!record;
  $('cancel-review').disabled=state.deciding||isTerminal(record);
  $('revise-trade').disabled=state.deciding;
}
function ticketEdited() {
  const shouldRefresh=!!state.record||state.evaluating||state.autoEvaluate;
  invalidate();state.autoEvaluate=shouldRefresh;updateTicketQuote();
  if(shouldRefresh){showTicketStatus(L('Vérification de la transaction révisée…','Checking revised trade…'),L('Votre résultat précédent n’est plus valide.','Your previous result is no longer valid.'),'');state.timer=setTimeout(()=>{try{draftRequest();evaluateTrade();}catch(error){inlineError('ticket-error',error.message);$('ticket-status').hidden=true;}},500);}
}
function showTicketStatus(title,message,color) {
  $('ticket-status').className='ticket-status '+color;$('ticket-status').replaceChildren(node('strong',title),node('span',message));$('ticket-status').hidden=false;
}
async function evaluateTrade() {
  if(state.evaluating||state.deciding)return;
  let request;
  try{request=draftRequest();}catch(error){inlineError('ticket-error',error.message);return;}
  inlineError('ticket-error','');
  const token=++state.evaluation;state.autoEvaluate=false;state.evaluating=true;state.record=null;
  state.evaluationController=new AbortController();setInvitation(false);$('review-panel').hidden=true;
  showTicketStatus(L('Évaluation de la transaction…','Evaluating this trade…'),L('Vérification de votre profil, de vos objectifs et de vos préférences enregistrés.','Checking your stored profile, goals and preferences.'),'');syncButtons();
  try {
    const record=await api('/api/evaluate',request,state.evaluationController.signal);
    if(token!==state.evaluation)return;
    state.record=record;state.evaluating=false;rememberTrade();renderResult();
    if(record.result.verdict==='red'){
      const first=state.context.first_name;$('invite-title').textContent=first?L(`Bonjour, ${first}!`,`Hi, ${first}!`):L('Bonjour!','Hi there!');
      $('invite-message').textContent=L('Cette transaction semble mal alignée avec votre profil d’investisseur. Voulez-vous en discuter?','This trade appears misaligned with your investor profile. Would you like to talk about it?');
      if($('trade-dialog').open){
        setInvitation(true);announce(L('Transaction suspendue. Une invitation Second Look est disponible.','Trade paused. A Second Look invitation is available.'));
      }else{
        $('page-review-slot').append($('review-panel'));revealReview(false);
        announce(L('La transaction en attente est signalée en rouge. L’explication est affichée dans la page.','The pending trade was flagged red. Its explanation is available in the page.'));
      }
    }
  }catch(error){
    if(token!==state.evaluation||error.name==='AbortError')return;
    state.evaluating=false;state.record=null;
    showTicketStatus(L('Transaction non évaluée','Trade not evaluated'),L('Aucune approbation ni confirmation n’a été enregistrée. Veuillez réessayer.','No approval or confirmation was recorded. Please try again.'),'');
    inlineError('ticket-error',friendly(error.message));
  }finally{if(token===state.evaluation){state.evaluating=false;syncButtons();}}
}
function renderResult() {
  const record=state.record;if(!record)return;
  const r=record.result;
  if(record.stage==='cancelled')showTicketStatus(L('Ordre annulé','Order cancelled'),L('Aucun ordre n’a été passé.','No order was placed.'),'');
  else if(record.stage==='simulation_continued')showTicketStatus(r.verdict==='red'?L('Ordre soumis · risque reconnu','Order submitted · risk acknowledged'):L('✓ Ordre soumis','✓ Order submitted'),r.verdict==='red'?L('Le résultat de la vérification reste rouge. Démo seulement : aucun ordre réel n’a été passé.','The check result stays red. Demo only: no real order was placed.'):(r.verdict==='yellow'?L('Vous avez pris connaissance de l’avis et confirmé.','You reviewed the heads-up and confirmed.'):L('Second Look a vérifié cette transaction par rapport à votre profil et n’a rien relevé.','Second Look checked this trade against your profile and found nothing to flag.'))+L(' Démo seulement : aucun ordre réel n’a été passé et vos avoirs restent inchangés.',' Demo only: no real order was placed and your holdings stay the same.'),r.verdict||'');
  else if(r.verdict==='red')showTicketStatus(L('Rouge · transaction suspendue','Red · trade paused'),L('Examinez les risques signalés avant de décider de la suite.','Review the flagged risks before deciding how to continue.'),'red');
  else if(r.verdict==='yellow'){
    showTicketStatus(L('Jaune · confirmation requise','Yellow · confirmation required'),r.reasons.join(' '),'yellow');
    const confirm=node('button',state.deciding?L('Confirmation…','Confirming…'):L('Confirmer l’ordre','Confirm order'),'button blue compact');confirm.type='button';confirm.id='confirm-yellow';confirm.disabled=state.deciding;confirm.addEventListener('click',()=>decision('confirm'));
    $('ticket-status').append(node('div','','confirmation-action'));$('ticket-status').lastChild.append(confirm);
    setInvitation(false);$('review-panel').hidden=true;
  }else{showTicketStatus(L('Révision manuelle requise','Manual review required'),r.reasons.join(' '),'');setInvitation(false);$('review-panel').hidden=true;}
  if(r.verdict==='red'&&!$('review-panel').hidden)renderReview();
  syncButtons();
}
function exposureName(check) {
  const part=check.rule.split('.');
  if(check.kind==='goal')return check.goal+' · '+dateLabel(check.date);
  return ({single_stock:L(`Position ${part.slice(1).join('.')}`,`${part.slice(1).join('.')} position`),crypto:L('Répartition en crypto','Crypto allocation'),hhi:L('Concentration des positions (HHI)','Position concentration (HHI)'),allocation:L(`Répartition – ${classes[part[1]]||part[1]}`,`${classes[part[1]]||part[1]} allocation`),sector:L(`Secteur ${part.slice(1).join('.')}`,`${part.slice(1).join('.')} sector`),liquid_cash_reserve:L('Réserve de liquidités','Liquid cash reserve')})[part[0]]||check.ticker||L('Exposition ESG','ESG exposure');
}
function revealReview(focus) {
  if(state.record?.result.verdict!=='red')return;
  setInvitation(false);$('review-panel').hidden=false;renderReview();
  if(focus){$('reason').scrollIntoView({block:'nearest',behavior:'auto'});$('reason').focus();}
}
function renderReview() {
  const record=state.record,r=record.result,request=record.request;
  if(r.verdict!=='red'){ $('review-panel').hidden=true;return; }
  const checks=r.alerts.filter(c=>c.severity==='red');
  const primary=checks[0];
  if(primary?.kind==='limit'){
    const format=primary.unit==='hhi_index'?v=>decimal(v,3):percent;
    $('red-summary').textContent=L(`${exposureName(primary)} : ${format(primary.before)} avant → ${format(primary.after)} après, par rapport à un ${primary.bound==='min'?'minimum':'maximum'} de ${format(primary.limit)}.`,`${exposureName(primary)}: ${format(primary.before)} before → ${format(primary.after)} after, compared with a ${primary.bound==='min'?'minimum':'maximum'} of ${format(primary.limit)}.`);
  }else $('red-summary').textContent=primary?.message||r.reasons[0];
  $('pending-trade-summary').textContent=L(`${request.side==='buy'?'Achat':'Vente'} ${request.ticker} · ${money(request.amount_cad)} · ${dateLabel(request.decision_date)}. Verdict initial : rouge.`,`${request.side==='buy'?'Buy':'Sell'} ${request.ticker} · ${money(request.amount_cad)} CAD · ${dateLabel(request.decision_date)}. Original verdict: Red.`);
  const rows=checks.filter(c=>finite(c.before)&&finite(c.after)&&finite(c.limit)).map(c=>{const format=c.unit==='hhi_index'?v=>decimal(v,3):percent;return [exposureName(c),format(c.before),format(c.after),(c.bound==='min'?'Minimum ':'Maximum ')+format(c.limit)];});
  $('exposure-table').replaceChildren(makeTable([L('Mesure signalée','Flagged measure'),L('Avant','Before'),L('Après','After'),L('Limite','Limit')],rows));
  renderSpectrum(r.classification);
  $('reason-form').hidden=isTerminal(record);
  renderConversation(record);
  const terminal=isTerminal(record);
  document.querySelector('.review-kicker').textContent=terminal?'SECOND LOOK · '+(record.stage==='cancelled'?L('SIMULATION ANNULÉE','SIMULATION CANCELLED'):L('RISQUE RECONNU','RISK ACKNOWLEDGED')):L('SECOND LOOK · TRANSACTION SUSPENDUE','SECOND LOOK · TRADE PAUSED');
  $('risk-actions').hidden=terminal;$('review-terminal').hidden=!terminal;
  $('review-terminal').textContent=record.stage==='cancelled'?L('Ordre annulé. Vos avoirs sont inchangés.','Order cancelled. Your holdings are unchanged.'):L('Vous avez reconnu les risques et soumis l’ordre. Le résultat de la vérification reste rouge. Démo seulement : aucun ordre réel n’a été passé.','You acknowledged the risks and submitted the order. The check result stays red. Demo only: no real order was placed.');
  syncButtons();
}
function renderSpectrum(c) {
  const root=$('review-spectrum');root.hidden=!c;if(!c)return;
  const steps=['Income','Conservative','Balanced','Growth','Maximum growth'];
  const target=c.target_profile, tScore=c.target_score;
  const where=p=>{
    const w=Object.entries(p.membership_weights).filter(([,v])=>v>0.02).sort((x,y)=>y[1]-x[1]);
    if(w.length===1)return L(`exactement à ${profileFr(w[0][0])}`,`right at ${w[0][0]}`);
    return L(`entre ${steps.filter(n=>w.some(([k])=>k===n)).map(profileFr).join(' et ')}, plus près de ${profileFr(w[0][0])}`,`between ${steps.filter(n=>w.some(([k])=>k===n)).join(' and ')}, closer to ${w[0][0]}`);
  };
  const vsAim=score=>{
    const d=score-tScore, size=Math.abs(d);
    if(size<0.1)return L('pile à votre cible','right at your aim');
    if(LANG==='fr')return `${size<0.5?'un peu ':size<1?'':'beaucoup '}plus ${d<0?'prudent':'audacieux'} que votre cible`;
    const how=size<0.5?'a bit ':size<1?'':'much ';
    return `${how}more ${d<0?'cautious':'adventurous'} than your aim`;
  };
  const title=node('h4',L('Où se situe votre répartition','Where your mix sits'),'spectrum-title');
  const intro=node('p','','spectrum-intro');
  intro.append(document.createTextNode(L('Voyez cette ligne comme un cadran allant de la répartition la plus prudente (à gauche) à la plus audacieuse (à droite). Les cinq points sont des portefeuilles modèles de la Financière Banque Nationale (automne 2026). Votre profil vise ','Think of this line as a dial from the most cautious mix (left) to the most adventurous (right). The five dots are model mixes from National Bank Financial (Fall 2026). Your profile aims for ')),node('strong',profileFr(target)),document.createTextNode(L('. Les repères montrent où se situe votre portefeuille, uniquement selon sa répartition entre liquidités, obligations, actions et placements alternatifs.','. The pins show where your portfolio sits, based only on how it is split between cash, bonds, stocks and alternatives.')));
  const track=node('div','','spectrum-line');
  steps.forEach((label,i)=>{const tick=node('span','','spectrum-step'+(label===target?' aim':''));tick.style.left=(i*25)+'%';tick.append(node('i'),node('span',profileFr(label)));if(label===target)tick.append(node('small',L('Votre cible','Your aim')));track.append(tick);});
  const b=c.before.normalized_score*100, a=c.after.normalized_score*100, same=Math.abs(a-b)<1.5, close=Math.abs(a-b)<16;
  if(!same){const band=node('span','','spectrum-move');band.style.left=Math.min(a,b)+'%';band.style.width=Math.abs(a-b)+'%';track.append(band);}
  const pins=same?[['same',a,L('Vous : avant = après','You: before = after')]]:[['before',b,L('Vous avant','You before')],['after',a,L('Vous après','You after')]];
  pins.forEach(([k,pos,label],i)=>{
    const pin=node('span','','spectrum-pin '+k+(close&&!same&&i===0?' high':''));pin.style.left=pos+'%';
    pin.append(node('span',label,'pin-label'),node('span','','pin-stem'),node('span','','pin-dot'));track.append(pin);
  });
  const ends=node('div','','spectrum-ends');ends.append(node('span',L('← Plus prudent','← More cautious')),node('span',L('Plus audacieux →','More adventurous →')));
  // What actually changes in the mix: the dial can stay still while the split moves.
  const r=state.record?.result, req=state.record?.request;
  const mix=node('div','','mix-change');
  if(r?.weights_before&&r?.weights_after){
    mix.append(node('span',L('Ce qui change dans votre répartition','What changes in your mix'),'small-label'));
    const row=node('div','','mix-chips');
    [['cash',L('Liquidités','Cash')],['fixed_income',L('Obligations','Bonds')],['equities',L('Actions','Stocks')],['alternatives',L('Placements alternatifs','Alternatives')]].forEach(([k,label])=>{
      const before=r.weights_before[k]||0, after=r.weights_after[k]||0, d=(after-before)*100;
      const chip=node('span','','mix-chip'+(Math.abs(d)>=0.05?(d>0?' up':' down'):''));
      chip.append(node('strong',label),node('span',`${percent(before)} → ${percent(after)}`));
      if(Math.abs(d)>=0.05)chip.append(node('em',L(`${d>0?'+':'−'}${Math.abs(d).toFixed(1).replace('.',',')} pts`,`${d>0?'+':'−'}${Math.abs(d).toFixed(1)} pts`)));
      chip.setAttribute('aria-label',L(`${label} : ${percent(before)} avant, ${percent(after)} après`,`${label}: ${(before*100).toFixed(1)} percent before, ${(after*100).toFixed(1)} percent after`));
      row.append(chip);
    });
    mix.append(row);
  }
  const reading=node('ul','','spectrum-reading');
  if(same){
    reading.append(node('li',L(`Votre répartition se situe ${where(c.after)} : ${vsAim(c.after.score)}.`,`Your mix sits ${where(c.after)}: ${vsAim(c.after.score)}.`)));
    const share=req&&r?.portfolio_value_cad?req.amount_cad/r.portfolio_value_cad:null;
    let why=L(`Votre repère ne bouge pas sur le cadran`,`Your pin doesn't move on the dial`);
    if(finite(share))why+=L(` : cette transaction représente environ ${(share*100).toFixed(share<0.1?1:0).replace('.',',')} % de votre portefeuille de ${money(r.portfolio_value_cad)}`,`: this trade is about ${(share*100).toFixed(share<0.1?1:0)}% of your ${money(r.portfolio_value_cad)} portfolio`);
    const sideways=c.after.distance-c.before.distance;
    if(sideways>0.005)why+=L(`, et elle déplace de l’argent entre des parties de votre répartition situées au même point du cadran (voir les changements ci-dessus). Votre répartition correspond un peu moins bien aux portefeuilles modèles`,`, and it shifts money between parts of your mix that sit at the same point on the dial (see the changes above). It makes your mix match the model mixes a little less well`);
    else if(sideways<-0.005)why+=L(`, mais votre répartition correspond un peu mieux aux portefeuilles modèles`,`, but it makes your mix match the model mixes a little better`);
    reading.append(node('li',why+'.','strong'));
  } else {
    reading.append(node('li',L(`Avant : ${where(c.before)} (${vsAim(c.before.score)}).`,`Before: ${where(c.before)} (${vsAim(c.before.score)}).`)),node('li',L(`Après : ${where(c.after)} (${vsAim(c.after.score)}).`,`After: ${where(c.after)} (${vsAim(c.after.score)}).`)));
    const move=c.after.score-c.before.score, gapB=Math.abs(c.before.score-tScore), gapA=Math.abs(c.after.score-tScore);
    reading.append(node('li',L(`Cette transaction déplace votre répartition globale vers le côté ${move>0?'audacieux':'prudent'}, ${gapA<gapB-0.01?`plus près de votre cible ${profileFr(target)}`:gapA>gapB+0.01?`plus loin de votre cible ${profileFr(target)}`:`à peu près aussi près de votre cible ${profileFr(target)} qu’avant`}.`,`This trade moves your overall mix toward the ${move>0?'adventurous':'cautious'} side, ${gapA<gapB-0.01?`closer to your ${target} aim`:gapA>gapB+0.01?`further from your ${target} aim`:`about as close to your ${target} aim as before`}.`),'strong'));
  }
  const why=node('p','','spectrum-note');
  why.append(node('strong',L('Pourquoi tout peut-il sembler correct alors que la transaction est signalée? ','Why can this look fine when a trade is flagged? ')),document.createTextNode(L('Ce cadran ne regarde que la répartition globale. Les limites (part d’une seule société, d’un secteur ou de la crypto), votre liste d’exclusion et l’argent nécessaire à vos objectifs sont vérifiés séparément. Ce sont ces vérifications qui déterminent la couleur.','This dial only looks at the broad mix. Limits such as how much one company, one sector or crypto can be, your avoid list, and money you need for your goals are checked separately. Those checks decide the colour.')));
  const details=node('details','','spectrum-details');
  details.append(node('summary',L('Chiffres exacts (pour les évaluateurs)','Exact numbers (for reviewers)')));
  ['before','after'].forEach(key=>{
    const m=Object.entries(c[key].membership_weights).filter(([,w])=>w>1e-10).map(([label,w])=>percent(w)+' '+label).join(' / ');
    details.append(node('p',L(`${key==='before'?'Avant':'Après'} : ${m} · position ${c[key].score.toFixed(2)} sur un cadran de 0 à 4 (votre cible : ${tScore}) · distance du portefeuille modèle le plus proche ${c[key].distance.toFixed(3)}`,`${key==='before'?'Before':'After'}: ${m} · position ${c[key].score.toFixed(2)} on a 0–4 dial (your aim: ${tScore}) · distance from the nearest model mix ${c[key].distance.toFixed(3)}`)));
  });
  details.append(node('p',L('Les pourcentages indiquent où se situe le portefeuille entre deux portefeuilles modèles voisins. Ce ne sont ni des probabilités ni une mesure complète du risque. La distance indique à quel point la répartition diffère de ce point du cadran (0 = identique).','Percentages say how far the portfolio is between two neighbouring model mixes. They are not probabilities or a full measure of risk. Distance is how different the mix is from that point on the dial (0 = identical).'),'muted'));
  root.replaceChildren(title,intro,track,ends,mix,reading,why,details);
}
function rememberTrade() {
  if(state.record?.result.verdict==='red')sessionStorage.setItem('second-look:'+state.record.request.client_id,JSON.stringify({id:state.record.simulation_id,version:state.record.assessment_input_version}));
}
async function restoreConversation(accountToken) {
  const saved=sessionStorage.getItem('second-look:'+state.context.client.client_id);
  if(!saved)return;
  try {
    const {id,version}=JSON.parse(saved),client=state.context.client.client_id;
    const response=await api(`/api/simulations/${id}/conversation?client_id=${encodeURIComponent(client)}&input_version=${encodeURIComponent(version)}`);
    if(accountToken!==state.accountLoad)return;
    state.record=response.record;state.chat=response.conversation;state.evidence=response.evidence;
    state.side=state.record.request.side;$('ticket-asset').value=state.record.request.ticker;$('amount').value=state.record.request.amount_cad;
    $('page-review-slot').append($('review-panel'));revealReview(false);renderResult();
  }catch(error){if(accountToken===state.accountLoad)inlineError('page-error-text',L('La conversation enregistrée n’a pas pu être restaurée. ','The saved conversation could not be restored. ')+error.message);}
}
function renderConversation(record) {
  const container=$('conversation-result');container.replaceChildren();
  const messages=state.chat?.messages||[];
  messages.forEach(message=>{
    const article=node('article','',message.role==='assistant'?'agent-answer':'conversation-message');
    article.append(node('span',message.role==='assistant'?'Second Look':L('Vous','You'),'small-label'),node('p',message.text));
    if(message.error)article.append(node('p',message.error,'backend-unavailable'));
    if(message.diagnostic){
      const detail=node('details');detail.append(node('summary',L('Référence de diagnostic','Diagnostic reference')));
      detail.append(node('p',L(`${message.diagnostic.component} · ${message.diagnostic.code} · requête ${message.diagnostic.request_id}`,`${message.diagnostic.component} · ${message.diagnostic.code} · request ${message.diagnostic.request_id}`)));
      if(message.diagnostic.aws_request_id)detail.append(node('p',L('Requête AWS ','AWS request ')+message.diagnostic.aws_request_id));
      article.append(detail);
    }
    if(message.evidence_refs?.length){
      const detail=node('details');detail.append(node('summary',L('Preuves et dates de calcul','Evidence and calculation dates')));
      message.evidence_refs.forEach(ref=>{
        const item=state.evidence.find(e=>e.tool_call_id===ref);if(!item)return;
        detail.append(node('p',L(`${item.source_or_method} · en date du ${dateLabel(item.as_of)} · ${item.data_status.replaceAll('_',' ')}`,`${item.source_or_method} · as of ${dateLabel(item.as_of)} · ${item.data_status.replaceAll('_',' ')}`)));
        // URLs come only from stored tool evidence, never from model text.
        const data=item.data;
        for(const source of (data.items||[]))if(/^https?:\/\//.test(source.url||'')){
          const link=node('a',L(`${source.source_name||source.title||'Source officielle'} · ${source.published_date}`,`${source.source_name||source.title||'Official source'} · ${source.published_date}`));link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';detail.append(link);
        }
      });article.append(detail);
    }
    container.append(article);
  });
  if(state.pendingMessage&&!messages.some(m=>m.id===state.pendingMessage.message_id)){
    const pending=node('article','','conversation-message');pending.append(node('span',L('Vous','You'),'small-label'),node('p',state.pendingMessage.message));container.append(pending);
  }
  if(state.reviewing){container.append(node('p',state.chat?.messages.length?L('Préparation de la réponse…','Preparing a reply…'):L('Consultation des spécialistes Profil, Nouvelles et Données financières, puis préparation de votre réponse…','Consulting the Profile, News and Financial Data specialists, then preparing your reply…'),'backend-unavailable'));container.setAttribute('aria-busy','true');}
  else container.removeAttribute('aria-busy');
  const lastUser=messages.filter(m=>m.role==='user').at(-1);
  const retry=lastUser&&lastUser.status!=='completed';
  $('retry-reply').hidden=state.reviewing||isTerminal(record)||(!retry&&!state.pendingMessage);
  $('refresh-conversation').hidden=!state.chat&&!state.pendingMessage;
  $('reason-label').textContent=messages.length?L('Posez une question ou précisez votre raisonnement','Ask a question or add to your reasoning'):L('Qu’est-ce qui vous a intéressé dans cette transaction?','What made you interested in this trade?');
  $('reason').placeholder=messages.length?L('Demandez à Second Look…','Ask Second Look…'):L('Dites-nous ce que vous aviez en tête…','Tell us what you had in mind…');
  const scenarios=$('conversation-scenarios');scenarios.replaceChildren();
  (state.chat?.scenarios||[]).forEach(scenario=>{
    const section=node('section','','scenario-assessment');
    section.append(node('h3',scenario.kind==='future_contribution'?L('Évaluation hypothétique après la cotisation','Hypothetical assessment after contribution'):scenario.kind==='market_shock'?L('Scénario de marché hypothétique','Hypothetical market scenario'):L('Évaluation distincte de votre transaction révisée','Separate assessment of your revised trade')));
    const r=scenario.result;
    section.append(node('p',L(`Verdict de la transaction : ${({green:'vert',yellow:'jaune',red:'rouge'})[r.verdict]||'révision manuelle'} · Toutes les limites applicables respectées : ${r.portfolio_within_limits===true?'Oui':r.portfolio_within_limits===false?'Non':'Non vérifié'}.`,`Trade verdict: ${r.verdict||'manual review'} · All applicable limits met: ${r.portfolio_within_limits===true?'Yes':r.portfolio_within_limits===false?'No':'Unverified'}.`)));
    section.append(makeTable([L('Répartition','Allocation'),L('Part hypothétique','Hypothetical share')],Object.entries(r.weights_after).map(([key,value])=>[classes[key],percent(value)])));
    const breaches=r.residual_breaches||[];
    if(breaches.length){const list=node('ul');breaches.forEach(c=>list.append(node('li',`${c.rule}: ${c.unit==='hhi_index'?decimal(c.after,3):percent(c.after)}; ${c.bound==='min'?'minimum':'maximum'} ${c.unit==='hhi_index'?decimal(c.limit,3):percent(c.limit)}.`)));section.append(list);}
    if(r.compliance_unresolved?.length)section.append(node('p',L('Certaines limites n’ont pas pu être vérifiées.','Some limits could not be verified.'),'backend-unavailable'));
    section.append(node('p',L(`${scenario.kind==='market_shock'?'Chocs hypothétiques précisés par le client appliqués; autres cours et taux de change maintenus':'Cours et taux de change maintenus'} au ${dateLabel(scenario.as_of)}; frais et impôts exclus. Il s’agit d’une comparaison conditionnelle, sans financement ni exécution.`,`${scenario.kind==='market_shock'?'Client-specified hypothetical shocks applied; other prices and FX held':'Prices and FX held'} at ${dateLabel(scenario.as_of)}; fees and taxes excluded. This is a conditional comparison, with no funding or execution.`),'muted'));
    if(scenario.kind==='market_shock')section.append(makeTable([L('Choc hypothétique','Hypothetical shock'),L('Rendement supposé','Assumed return')],Object.entries(scenario.assumptions.shocks).map(([key,value])=>[key,percent(value)])));
    if(scenario.kind==='future_contribution')section.append(node('p',L(`Moment prévu (selon vous) : ${scenario.plan.expected_timing}. Certitude : ${scenario.plan.certainty}. D’ici l’arrivée des fonds, ou en cas de retard, l’évaluation actuelle s’applique toujours. L’horizon des objectifs reste fixé à la date de décision initiale.`,`Expected timing (your statement): ${scenario.plan.expected_timing}. Certainty: ${scenario.plan.certainty}. Until arrival, or if delayed, the current trade assessment still applies. Goal runway is held at the original decision date.`),'muted'));
    if(scenario.kind==='future_contribution'){
      section.append(node('p',L(`Votre cotisation déclarée : ${decimal(scenario.plan.amount)} ${scenario.plan.currency}. Au taux de change de l’instantané : ${money(scenario.contribution_cad)}.`,`Your stated contribution: ${decimal(scenario.plan.amount)} ${scenario.plan.currency}. At the snapshot FX rate: ${money(scenario.contribution_cad)} CAD.`)));
      if(scenario.plan.purchases.length)section.append(makeTable([L('Achat prévu déclaré par le client','Client-stated planned purchase'),L('Montant (CAD)','Amount (CAD)')],scenario.plan.purchases.map(p=>[p.ticker,money(p.amount_cad)])));
    }
    if(scenario.kind==='future_contribution'&&!isTerminal(record)&&!record.superseded_by){
      const label=node('label','','checkbox-label'),check=node('input');check.type='checkbox';
      label.append(check,node('span',L('Dans ce compte fictif, confirmez que ces fonds sont arrivés à la date de l’instantané historique. Aucun achat prévu ne sera exécuté.','In this fictional account, confirm that this cash has arrived at the historical snapshot date. No planned purchases will execute.')));
      const button=node('button',L('Confirmer l’arrivée simulée des fonds et revérifier','Confirm simulated cash arrival and recheck'),'button secondary compact');button.type='button';button.disabled=true;
      check.addEventListener('change',()=>{button.disabled=!check.checked||state.reviewing||state.deciding;});
      const eventId=crypto.randomUUID();button.addEventListener('click',()=>confirmContribution(scenario.scenario_id,eventId));
      section.append(label,button);
    }
    scenarios.append(section);
  });
  container.scrollTop=container.scrollHeight;
}
async function confirmContribution(scenarioId,eventId) {
  if(state.deciding||state.reviewing||!state.record)return;
  const record=state.record,token=state.evaluation;state.deciding=true;syncButtons();
  try{
    const response=await api(`/api/simulations/${record.simulation_id}/confirm-contribution`,{
      client_id:record.request.client_id,input_version:record.assessment_input_version,scenario_id:scenarioId,event_id:eventId,confirmed:true});
    if(token!==state.evaluation||state.record?.simulation_id!==record.simulation_id||isTerminal(state.record))return;
    state.record=response.record;state.chat=null;state.pendingMessage=null;state.evidence=[];rememberTrade();
    // Reload account figures from the newly confirmed simulated state.
    const context=await api('/api/context?client_id='+encodeURIComponent(record.request.client_id));
    if(token!==state.evaluation)return;
    state.context=context;state.record=response.record;renderHoldings();renderQuote();
    $('account-total').textContent=money(context.account.total_cad);$('account-invested').textContent=money(context.account.invested_cad);$('account-cash').textContent=money(context.account.cash_cad);
    $('ticket-cash').textContent=money(context.account.cash_cad);renderResult();
    if(state.record.result.verdict==='red')revealReview(false);else $('review-panel').hidden=true;
    announce(L('Arrivée simulée des fonds confirmée. Une nouvelle évaluation a été calculée; aucun achat n’a été exécuté.','Simulated cash arrival confirmed. A new assessment was calculated; no purchases executed.'));
  }catch(error){inlineError('review-error',error.message);}
  finally{state.deciding=false;syncButtons();}
}
async function refreshConversation() {
  if(!state.record)return;
  const id=state.record.simulation_id,token=state.evaluation;
  try{
    const response=await api(`/api/simulations/${id}/conversation?client_id=${encodeURIComponent(state.record.request.client_id)}&input_version=${state.record.assessment_input_version}`);
    if(token!==state.evaluation||state.record?.simulation_id!==id)return;
    state.record=response.record;state.chat=response.conversation;state.evidence=response.evidence;
    if(state.pendingMessage&&state.chat.messages.some(m=>m.id===state.pendingMessage.message_id&&m.status==='completed'))state.pendingMessage=null;
    renderReview();
  }catch(error){inlineError('review-error',error.message);}
}
async function submitReason(event, retry=false) {
  event?.preventDefault();if(state.reviewing||state.deciding||!state.record||isTerminal(state.record))return;
  let payload=state.pendingMessage;
  if(retry && !payload){const last=state.chat?.messages.filter(m=>m.role==='user').at(-1);if(last)payload={message_id:last.id,message:last.text};}
  if(!retry){
    const message=$('reason').value.trim();if(!message){inlineError('review-error',L('Veuillez entrer un message.','Please enter a message.'));$('reason').focus();return;}
    payload={message_id:crypto.randomUUID(),message};
  }
  if(!payload)return;
  const id=state.record.simulation_id,token=state.evaluation;
  payload={...payload,language:LANG,client_id:state.record.request.client_id,input_version:state.record.assessment_input_version,expected_revision:state.chat?.revision||0};
  state.pendingMessage=payload;state.reviewing=true;state.reviewController=new AbortController();inlineError('review-error','');renderReview();
  try {
    const response=await api(`/api/simulations/${id}/conversation`,payload,state.reviewController.signal,600000);
    if(token!==state.evaluation||state.record?.simulation_id!==id||isTerminal(state.record))return;
    state.record=response.record;state.chat=response.conversation;state.evidence=response.evidence;state.pendingMessage=null;$('reason').value='';rememberTrade();
  }catch(error){if(token===state.evaluation&&error.name!=='AbortError')inlineError('review-error',error.message);}
  finally{if(token===state.evaluation){state.reviewing=false;renderReview();}}
}
async function decision(action) {
  if(state.deciding||!state.record)return;
  if(action==='proceed'&&!$('risk-checkbox').checked)return;
  const id=state.record.simulation_id,token=state.evaluation;
  state.deciding=true;inlineError('review-error','');syncButtons();
  if($('confirm-yellow'))$('confirm-yellow').disabled=true;
  try {
    const record=await api(`/api/simulations/${id}/decision`,{decision:action,...(action==='proceed'?{risk_acknowledged:true}:{})});
    if(token!==state.evaluation||state.record?.simulation_id!==id)return;
    state.record=record;setInvitation(false);renderResult();
    announce(record.stage==='cancelled'?L('Ordre annulé.','Order cancelled.'):L('Ordre soumis. Démo seulement : aucun ordre réel n’a été passé.','Order submitted. Demo only: no real order was placed.'));
    if(action==='cancel')closeTicket();
  }catch(error){inlineError($('review-panel').hidden?'ticket-error':'review-error',error.message);}
  finally{state.deciding=false;syncButtons();if($('confirm-yellow'))$('confirm-yellow').disabled=false;}
}
function reviseTrade() {
  const request=state.record?.request;
  if(!request)return;
  openTicket(request.side,request.ticker,request.amount_cad);
  state.autoEvaluate=true;
  announce(L('Révisez la transaction en attente. Une nouvelle évaluation est requise.','Revise the pending trade. A new evaluation is required.'));
}

// ---------------------------------------------------------------- asset picker
// One accessible combobox for the market search and the ticket's asset field.
// Shows only data the context already returns: names, markets, sectors, historical
// closes, 90-day history, stored drawdown history and stored preferences. It never
// predicts a verdict; the rule engine decides after "Submit".
const PICKER_GROUPS = [
  ['all',L('Tous','All')],['ca',L('Actions canadiennes','Canadian stocks')],['us',L('Actions américaines','U.S. stocks')],['funds',L('Fonds (FNB)','Funds (ETFs)')],
  ['bonds',L('Obligations','Bonds')],['gold',L('Or','Gold')],['crypto','Crypto']
];
state.recent = [];
function assetGroupKey(a) {
  if(!a) return 'all';
  if(a.instrument_type==='crypto' || a.is_crypto) return 'crypto';
  if(a.asset_class==='fixed_income') return 'bonds';
  if(a.asset_class==='alternatives') return 'gold';
  if(a.asset_class==='cash') return 'bonds';
  if(String(a.instrument_type||'').startsWith('etf')) return 'funds';
  return a.currency==='CAD' ? 'ca' : 'us';
}
const assetTypeLabel = a => ({crypto:'Crypto',bonds:a?.asset_class==='cash'?L('Fonds de liquidités','Cash-like fund'):L('Fonds d’obligations','Bond fund'),gold:L('Or','Gold'),funds:L('Fonds d’actions','Stock fund'),ca:L('Action','Stock'),us:L('Action','Stock')})[assetGroupKey(a)] || '';
function pushRecent(ticker) {
  if(!ticker) return;
  state.recent=[ticker,...state.recent.filter(t=>t!==ticker)].slice(0,5);
}
function initials(name, ticker, a) {
  if(a && !a.is_single_stock) return ticker.split(/[.-]/)[0].slice(0,3).toUpperCase(); // funds, crypto: XBB, GLD, BTC
  const words=String(name||ticker).replace(/[^A-Za-z0-9 &]/g,' ').split(/\s+/).filter(w=>w&&!/^(the|of|and|&|inc|corp|etf|trust|index|class)$/i.test(w));
  return ((words[0]?.[0]||ticker[0])+(words[1]?.[0]||ticker[1]||'')).toUpperCase();
}
function highlight(textValue, query) {
  const span=node('span');
  const i=query?textValue.toLowerCase().indexOf(query):-1;
  if(i<0){span.textContent=textValue;return span;}
  span.append(document.createTextNode(textValue.slice(0,i)),node('mark',textValue.slice(i,i+query.length)),document.createTextNode(textValue.slice(i+query.length)));
  return span;
}
function sparkline(points) {
  const values=(points||[]).map(p=>p.close_cad).filter(finite);
  if(values.length<2) return null;
  const w=72,h=24,min=Math.min(...values),max=Math.max(...values),span=max-min||1;
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('width',w);svg.setAttribute('height',h);svg.setAttribute('class','spark');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(ns,'path');
  path.setAttribute('d',values.map((v,i)=>`${i?'L':'M'}${(i/(values.length-1)*w).toFixed(1)} ${(h-2-(v-min)/span*(h-4)).toFixed(1)}`).join(' '));
  path.setAttribute('class',values.at(-1)>=values[0]?'up':'down');
  svg.append(path);return svg;
}
function assetHeadsUps(ticker, a) {
  const ctx=state.context, esg=ctx.client.preferences?.esg||{}, limits=ctx.profile_limits||{}, tags=[];
  if((esg.excluded_tickers||[]).includes(ticker)) tags.push([L('Sur votre liste d’exclusion','On your avoid list'),'warn']);
  else if(a.sector && (esg.excluded_sectors||[]).includes(a.sector)) tags.push([L('Secteur que vous souhaitez éviter','Sector you asked to avoid'),'warn']);
  if(a.instrument_type==='crypto' || a.is_crypto) {
    if(finite(limits.crypto_max)) tags.push([limits.crypto_max===0?L('Crypto · hors de votre profil','Crypto · not part of your profile'):L(`Crypto · max. ${percent(limits.crypto_max)} de votre portefeuille`,`Crypto · max ${percent(limits.crypto_max)} of your portfolio`),'warn']);
    else tags.push([L('Crypto · limite distincte dans votre profil','Crypto · has its own limit in your profile'),'info']);
  }
  return tags;
}
function worstDropText(a) {
  const hz=a.horizon;
  if(!hz || !finite(hz.max_drawdown) || hz.max_drawdown>-0.005) return '';
  const drop=L(`Pire baisse depuis nov. 2021 : ${(hz.max_drawdown*100).toFixed(0)}\u00a0%`,`Worst drop since Nov 2021: ${(hz.max_drawdown*100).toFixed(0)}%`);
  if(hz.recovered && finite(hz.recovery_months)) return L(`${drop} · retour au sommet après ${Math.round(hz.recovery_months)} mois`,`${drop} · back to its high after ${Math.round(hz.recovery_months)} months`);
  if(hz.recovered===false) return L(`${drop} · pas encore revenu au sommet`,`${drop} · not yet back to its high`);
  return drop;
}
function scoreAsset(ticker, a, query) {
  if(!query) return 1;
  const t=ticker.toLowerCase(), n=(a.name||'').toLowerCase(), s=(a.sector||'').toLowerCase(), m=(a.market||'').toLowerCase();
  if(t===query || t.split(/[.-]/)[0]===query) return 100;
  if(t.startsWith(query)) return 80;
  if(n.startsWith(query)) return 70;
  if(n.split(/\s+/).some(w=>w.startsWith(query))) return 60;
  if(n.includes(query) || t.includes(query)) return 40;
  if(s.includes(query) || assetTypeLabel(a).toLowerCase().includes(query) || m===query) return 20;
  return 0;
}
function attachPicker(input, onPick) {
  const listbox=node('div','','picker');listbox.id=input.id+'-listbox';listbox.setAttribute('role','listbox');listbox.setAttribute('aria-label',L('Titres','Assets'));listbox.hidden=true;
  const chips=node('div','','picker-chips');chips.setAttribute('role','group');chips.setAttribute('aria-label',L('Filtrer par type','Filter by type'));
  const results=node('div','','picker-results');
  const close=node('button',L('Fermer','Close'),'picker-close text-button');close.type='button';
  const head=node('div','','picker-head');head.append(chips,close);
  const foot=node('p','','picker-foot');
  listbox.append(head,results,foot);
  // Outside the search field's own styles; inside the dialog when the field is (top layer).
  (input.closest('dialog')||document.body).append(listbox);
  let filter='all', active=-1, options=[], typed=false;
  const keep=event=>event.preventDefault(); // keep focus in the input while clicking inside the list
  listbox.addEventListener('mousedown',keep);
  close.addEventListener('click',()=>hide(true));
  function place() {
    const r=input.getBoundingClientRect(), narrow=innerWidth<700;
    listbox.classList.toggle('sheet',narrow);
    if(narrow){listbox.style.left='0px';listbox.style.right='0px';listbox.style.width='';listbox.style.top=Math.max(8,r.bottom+6)+'px';listbox.style.maxHeight='';return;}
    const width=Math.min(Math.max(r.width,640),innerWidth-24), left=Math.min(Math.max(12,r.left),innerWidth-width-12);
    listbox.style.left=left+'px';listbox.style.right='';listbox.style.width=width+'px';
    const below=innerHeight-r.bottom-16;
    listbox.style.top=(r.bottom+6)+'px';listbox.style.maxHeight=Math.max(240,Math.min(560,below))+'px';
  }
  function setActive(index, scroll=true) {
    options.forEach(o=>o.setAttribute('aria-selected','false'));
    active=options.length?Math.max(0,Math.min(index,options.length-1)):-1;
    if(active>=0){options[active].setAttribute('aria-selected','true');input.setAttribute('aria-activedescendant',options[active].id);if(scroll)options[active].scrollIntoView({block:'nearest'});}
    else input.removeAttribute('aria-activedescendant');
  }
  function row(ticker, query) {
    const ctx=state.context, a=ctx.assets[ticker], q=ctx.quotes[ticker], held=ctx.holdings.find(h=>h.ticker===ticker);
    const option=node('div','','picker-option');option.id=`${input.id}-opt-${ticker.replace(/[^A-Za-z0-9]/g,'_')}`;option.setAttribute('role','option');option.dataset.ticker=ticker;
    const badge=node('span',initials(a.name,ticker,a),'asset-badge g-'+assetGroupKey(a));badge.setAttribute('aria-hidden','true');
    const main=node('div','','picker-main');
    const title=node('div','','picker-title');title.append(highlight(a.name||ticker,query));
    const meta=node('div','','picker-meta');
    const tick=node('strong');tick.append(highlight(ticker,query));meta.append(tick);
    if(a.market)meta.append(node('span',a.market,'market-chip'));
    meta.append(node('span',[a.sector,assetTypeLabel(a)].filter(Boolean).join(' · ')));
    main.append(title,meta);
    const tags=node('div','','picker-tags');
    if(held)tags.append(node('span',L('En portefeuille · ','You own · ')+money(held.value_cad),'tag own'));
    assetHeadsUps(ticker,a).forEach(([label,kind])=>tags.append(node('span',label,'tag '+kind)));
    const drop=worstDropText(a);if(drop)tags.append(node('span',drop,'tag history'));
    if(tags.children.length)main.append(tags);
    const price=node('div','','picker-price');
    if(q){
      price.append(node('strong',decimal(q.close_native)+' '+q.currency));
      if(finite(q.change_fraction)){const up=q.change_fraction>=0;price.append(node('span',`${up?'▲ +':'▼ '}${percent(q.change_fraction)}`,'pill '+(up?'up':'down')));}
    } else price.append(node('span',L('Aucun cours utilisable','No usable quote'),'muted'));
    const spark=sparkline(ctx.price_history?.[ticker]);
    const label=[a.name,ticker,a.market,a.sector,q?L(`dernière clôture ${decimal(q.close_native)} ${q.currency}`,`last close ${decimal(q.close_native)} ${q.currency}`):L('aucun cours utilisable','no usable quote'),finite(q?.change_fraction)?L(`variation ${percent(q.change_fraction)}`,`change ${percent(q.change_fraction)}`):'',held?L('en portefeuille ','you own ')+money(held.value_cad):'',...assetHeadsUps(ticker,a).map(t=>t[0]),drop].filter(Boolean).join(', ');
    option.setAttribute('aria-label',label);
    option.append(badge,main,spark||node('span','','spark'),price);
    option.addEventListener('click',()=>pick(ticker));
    option.addEventListener('mousemove',()=>{const i=options.indexOf(option);if(i!==active)setActive(i,false);});
    return option;
  }
  function render() {
    const ctx=state.context;if(!ctx)return;
    const query=typed?input.value.trim().toLowerCase():'';
    const counts={all:ctx.tickers.length};ctx.tickers.forEach(t=>{const k=assetGroupKey(ctx.assets[t]);counts[k]=(counts[k]||0)+1;});
    chips.replaceChildren(...PICKER_GROUPS.filter(([k])=>counts[k]).map(([k,label])=>{const b=node('button',`${label} ${counts[k]}`,'chip');b.type='button';b.setAttribute('aria-pressed',String(filter===k));b.addEventListener('click',()=>{filter=k;render();});return b;}));
    let pool=ctx.tickers.filter(t=>filter==='all'||assetGroupKey(ctx.assets[t])===filter);
    const sections=[];
    if(query){
      const hits=pool.map(t=>[t,scoreAsset(t,ctx.assets[t],query)]).filter(([,s])=>s>0).sort((x,y)=>y[1]-x[1]||ctx.assets[x[0]].name.localeCompare(ctx.assets[y[0]].name)).map(([t])=>t);
      sections.push([L(`${hits.length} résultat${hits.length===1?'':'s'}`,`${hits.length} result${hits.length===1?'':'s'}`),hits]);
    } else {
      const owned=ctx.holdings.map(h=>h.ticker).filter(t=>pool.includes(t));
      const recent=state.recent.filter(t=>pool.includes(t)&&!owned.includes(t));
      const rest=pool.filter(t=>!owned.includes(t)&&!recent.includes(t)).sort((x,y)=>ctx.assets[x].name.localeCompare(ctx.assets[y].name));
      if(owned.length)sections.push([L('En portefeuille','You own'),owned]);
      if(recent.length)sections.push([L('Consultés récemment','Recently viewed'),recent]);
      sections.push([filter==='all'?L('Tous les titres','All assets'):PICKER_GROUPS.find(g=>g[0]===filter)[1],rest]);
    }
    results.replaceChildren();options=[];
    sections.forEach(([title,tickers])=>{
      if(!tickers.length)return;
      const group=node('div','','picker-group');group.setAttribute('role','group');
      const heading=node('p',title,'picker-section');heading.id=`${input.id}-sec-${title.replace(/[^\p{L}\p{N}]+/gu,'_')}`;group.setAttribute('aria-labelledby',heading.id);
      group.append(heading);
      tickers.forEach(t=>{const o=row(t,query);options.push(o);group.append(o);});
      results.append(group);
    });
    if(!options.length){
      const empty=node('div','','picker-empty');
      empty.append(node('strong',query?L(`Aucun résultat pour « ${input.value.trim()} ».`,`No match for “${input.value.trim()}”.`):L('Aucun titre dans ce groupe.','No assets in this group.')),node('span',L(`Cette démo couvre les ${ctx.tickers.length} titres des données fournies. Essayez un symbole (NVDA), une société (Royal Bank) ou un secteur (Energy).`,`This demo covers the ${ctx.tickers.length} assets in the provided data. Try a ticker (NVDA), a company (Royal Bank) or a sector (Energy).`)));
      results.append(empty);
    }
    foot.textContent=L('Clôtures historiques · ','Historical closes · ')+dateLabel(Object.values(ctx.quotes)[0]?.price_source_date)+L(' · cours non en temps réel. C’est la vérification, et non cette liste, qui détermine vert/jaune/rouge.',' · not real-time quotes. The check, not this list, decides green/yellow/red.');
    setActive(query?0:-1,false);
    announce(options.length?L(`${options.length} titres affichés.`,`${options.length} assets listed.`):L('Aucun titre correspondant.','No matching assets.'));
  }
  function show() {
    if(!state.context||input.disabled)return;
    listbox.hidden=false;input.setAttribute('aria-expanded','true');place();render();
    if(listbox.classList.contains('sheet'))input.scrollIntoView({block:'start'});
  }
  function hide(refocus=false) {
    if(listbox.hidden)return;
    listbox.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');typed=false;
    if(refocus)input.focus();
  }
  function pick(ticker) {
    input.value=ticker;hide();pushRecent(ticker);onPick(ticker);
  }
  input.addEventListener('focus',()=>{typed=false;show();input.select?.();});
  input.addEventListener('click',()=>{if(listbox.hidden)show();});
  input.addEventListener('input',()=>{typed=true;if(listbox.hidden)show();else render();});
  input.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();if(listbox.hidden)show();setActive(active+1);}
    else if(event.key==='ArrowUp'){event.preventDefault();setActive(active<=0?options.length-1:active-1);}
    else if(event.key==='Home'&&!listbox.hidden&&event.ctrlKey){event.preventDefault();setActive(0);}
    else if(event.key==='End'&&!listbox.hidden&&event.ctrlKey){event.preventDefault();setActive(options.length-1);}
    else if(event.key==='Enter'){
      if(!listbox.hidden&&active>=0){event.preventDefault();pick(options[active].dataset.ticker);}
      else{const t=resolveAsset(input.value);if(t){event.preventDefault();pick(t);}}
    }
    else if(event.key==='Escape'&&!listbox.hidden){event.preventDefault();event.stopPropagation();hide();}
    else if(event.key==='Tab')hide();
  });
  input.addEventListener('blur',()=>setTimeout(()=>{if(document.activeElement!==input&&!listbox.contains(document.activeElement))hide();},120));
  addEventListener('resize',()=>{if(!listbox.hidden)place();});
  addEventListener('scroll',()=>{if(!listbox.hidden)place();},true);
  return {hide};
}
function renderScenarioCards() {
  const ctx=state.context,box=$('scenario-cards');
  const demos=(DEMOS[ctx.client.client_id]||[]).filter(([t])=>ctx.assets[t]);
  box.replaceChildren(...demos.map(([ticker,amount])=>{
    const a=ctx.assets[ticker],button=node('button','','scenario-card');button.type='button';
    button.append(node('span',initials(a.name,ticker,a),'asset-badge small g-'+assetGroupKey(a)),node('span','',''));
    button.lastChild.append(node('strong',L(`Achat ${ticker}`,`Buy ${ticker}`)),node('small',money(amount)));
    button.setAttribute('aria-label',L(`Démo : achat de ${a.name} (${ticker}) pour ${money(amount)}. Ouvre un billet d’ordre prérempli.`,`Demo: buy ${a.name} (${ticker}) for ${money(amount)}. Opens a filled trade ticket.`));
    button.addEventListener('click',()=>{state.selectedAsset=ticker;pushRecent(ticker);renderQuote();openTicket('buy',ticker,amount);});
    return button;
  }));
}
const marketPicker=attachPicker($('market-search'),ticker=>{$('market-search').dispatchEvent(new Event('change',{bubbles:true}));});
const ticketPicker=attachPicker($('ticket-asset'),ticker=>{$('ticket-asset').dispatchEvent(new Event('input',{bubbles:true}));$('amount').focus();});

// Functional navigation and keyboard behavior; no decorative product controls.
$('nav-markets').addEventListener('click',()=>showPage('markets'));
$('nav-quote').addEventListener('click',()=>{showPage('markets');$('market-search').focus();$('market-search').select();});
$('nav-holdings').addEventListener('click',()=>showPage('holdings'));
$('back-to-markets').addEventListener('click',()=>showPage('markets'));
$('client-select').addEventListener('change',()=>{const id=$('client-select').value;const url=new URL(location.href);url.searchParams.set('client',id);history.replaceState(null,'',url);loadContext(id);});
$('retry-context').addEventListener('click',()=>loadContext(new URLSearchParams(location.search).get('client')||'robert'));
$('market-search').addEventListener('change',()=>{const ticker=resolveAsset($('market-search').value);if(ticker){state.selectedAsset=ticker;renderQuote();}else{announce(L('Choisissez un symbole dans la liste fournie.','Choose a ticker from the provided list.'));$('market-search').value=state.selectedAsset;}});
$('scenario-select').addEventListener('change',()=>{const index=$('scenario-select').value;if(index==='')return;const[ticker,amount]=DEMOS[state.context.client.client_id][Number(index)];state.selectedAsset=ticker;renderQuote();openTicket('buy',ticker,amount);$('scenario-select').value='';});
document.querySelectorAll('[data-open-side]').forEach(button=>button.addEventListener('click',()=>openTicket(button.dataset.openSide)));
$('close-ticket').addEventListener('click',closeTicket);
$('trade-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTicket();});
$('cancel-trade').addEventListener('click',()=>{if(state.record&&!isTerminal(state.record))decision('cancel');else{invalidate();closeTicket();}});
$('trade-form').addEventListener('submit',event=>{event.preventDefault();evaluateTrade();});
$('amount').addEventListener('input',ticketEdited);$('ticket-asset').addEventListener('input',ticketEdited);
$('side-buy').addEventListener('click',()=>{if(state.side!=='buy'){state.side='buy';ticketEdited();}});
$('side-sell').addEventListener('click',()=>{if(state.side!=='sell'){state.side='sell';ticketEdited();}});
$('lets-talk').addEventListener('click',()=>revealReview(true));
$('dismiss-invite').addEventListener('click',()=>{revealReview(false);$('review-title').focus({preventScroll:true});});
$('revise-trade').addEventListener('click',reviseTrade);
$('reason-form').addEventListener('submit',submitReason);
$('reason').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send-reason').disabled)$('reason-form').requestSubmit();}});
$('retry-reply').addEventListener('click',event=>submitReason(event,true));
$('refresh-conversation').addEventListener('click',refreshConversation);
$('risk-checkbox').addEventListener('change',syncButtons);
$('proceed-red').addEventListener('click',()=>decision('proceed'));
$('cancel-review').addEventListener('click',()=>decision('cancel'));
const tabs=['overview','history'];
tabs.forEach((tab,index)=>{
  $('tab-'+tab).addEventListener('click',()=>selectTab(tab));
  $('tab-'+tab).addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;if(next!==undefined){event.preventDefault();selectTab(tabs[next]);$('tab-'+tabs[next]).focus();}});
});
let resizeFrame;
window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(renderChart);});

// Static page text is written in French; in English each known string is swapped in place.
const EN_TEXT = {
 'Courtage direct · Simulation':'Direct Brokerage · Simulation','Aller à l’information de marché':'Skip to market information',
 'Banque Nationale Courtage direct · accueil de la simulation':'National Bank Direct Brokerage · simulation home',
 'BANQUE':'NATIONAL','NATIONALE':'BANK','COURTAGE DIRECT':'DIRECT BROKERAGE','Investisseur fictif':'Fictional investor','Chargement…':'Loading…',
 'Navigation principale':'Main navigation','Marchés':'Markets','Mes avoirs':'My holdings','Achat':'Buy','Vente':'Sell','Cote':'Quote',
 'Chargement de l’instantané historique…':'Loading historical snapshot…','Données historiques · pas en temps réel':'Historical data · not real time',
 'Réessayer':'Try again','Cote du titre sélectionné':'Selected asset quote',
 'Rechercher un symbole, une société ou un secteur':'Search ticker, company or sector','Essayer une transaction démo':'Try a demo trade',
 'Choisir un scénario':'Select a scenario','Chargement des données de marché…':'Loading your market data…','Cote quotidienne historique':'Historical daily quote',
 'Dernier prix':'Last price','Variation du jour':'Change','Vol.':'Vol.','Date du cours':'Price date',
 'Information clé sur la cote':'Key quote information','Ma position':'My position','Information sur la cote':'Quote information',
 'Sommaire':'Summary','Historique des cours':'Price history','Rendement':'Performance','Calculé à partir des cours de clôture en CAD.':'Calculated from CAD closing prices.',
 'Graphique des cours historiques':'Historical price chart','Graphique':'Chart','Chargement du graphique historique.':'Historical chart data is loading.',
 'Observations complètes en CAD. Aucune observation future ni estimation d’analyste n’est incluse.':'Completed source observations in CAD. No future observations or analyst estimates are included.',
 'Source : jeu de données AlphaForge · cours Yahoo Finance · taux de change de la Banque du Canada.':'Source: AlphaForge starter dataset · Yahoo Finance prices · Bank of Canada FX.',
 'Sommaire du compte':'Account summary','Compte de simulation CAD':'CAD simulation account','Profil d’investisseur enregistré':'Stored investor profile',
 'Valeur totale du compte':'Total account value','Valeur investie':'Invested value','Encaisse disponible':'Available cash','Vos avoirs':'Your holdings',
 'Positions enregistrées, évaluées en CAD à la date de l’instantané.':'Stored positions, valued in CAD at the account snapshot.',
 'Retour à la cote →':'Back to quote →','Profil, objectifs et préférences enregistrés':'Stored profile, goals and preferences',
 'Second Look · Démonstration hackathon':'Second Look · Hackathon demonstration',
 'Simulation non officielle. Comptes fictifs. Aucun ordre réel n’est passé.':'Unofficial simulation. Fictional accounts. No real orders are placed.',
 'ORDRE · DÉMO':'ORDER · DEMO','Fermer le billet d’ordre':'Close trade ticket','Compte':'Account','CAD · simulation au comptant':'CAD · cash-funded simulation',
 'Encaisse':L('Liquidités','Cash'),'Sens de la transaction':'Trade direction','Univers d’actifs fourni':'Provided asset universe','Titre':'Asset','· symbole ou société':'· ticker or company',
 'Montant de la transaction':'Trade amount','Entrez un montant en CAD pour voir le nombre d’unités estimé.':'Enter a CAD amount to see estimated units.',
 'Type de prix':'Order type','Au marché':'Market','Exécuté au dernier cours de clôture (démo)':'Filled at the last closing price (demo)',
 'Payé à même l’encaisse. Unités fractionnaires; sans frais ni marge dans cette démo.':'Paid from cash. Fractional units; no fees or margin in this demo.',
 'Sommaire de la cote':'Trade quote summary','Annuler':'Cancel','Soumettre':'Submit',
 'Fermer l’invitation':'Dismiss agent invitation',
 'Un guide Second Look souriant qui salue':'A friendly human Second Look guide smiling and waving',
 'Un deuxième regard?':'A second look?',
 'Parlons-en':'Let’s talk',
 'Votre transaction reste suspendue.':'Your trade remains paused.',
 'Regardons ce qui change.':'Let’s look at what changes.',
 'Réviser la transaction':'Revise trade',
 'Évaluation actuelle de la transaction':'Current trade assessment',
 'Discuter avec Second Look':'Talk with Second Look',
 'Conversation avec Second Look':'Conversation with Second Look',
 'Entrée pour envoyer · Maj+Entrée pour un saut de ligne. La discussion n’exécute jamais de transaction.':'Enter to send · Shift+Enter for a new line. Chat never executes a trade.',
 'Réessayer le message enregistré':'Retry saved message',
 'Actualiser la conversation':'Refresh conversation',
 'Je comprends les risques signalés et souhaite soumettre cet ordre quand même.':'I understand the flagged risks and want to submit this order anyway.',
 'Annuler la simulation':'Cancel simulation',
 'Poursuivre la simulation':'Proceed with simulation',
};
function translateStatic() {
  document.documentElement.lang = LANG;
  if (LANG !== 'en') return;
  document.title = EN_TEXT[document.title] || document.title;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    const key = text.nodeValue.trim(), value = EN_TEXT[key];
    if (value) text.nodeValue = text.nodeValue.replace(key, value);
  }
  document.querySelectorAll('[placeholder],[aria-label],[title]').forEach(element => {
    ['placeholder','aria-label','title'].forEach(name => { const value = EN_TEXT[element.getAttribute(name)]; if (value) element.setAttribute(name, value); });
  });
}
translateStatic();
$('lang-toggle').textContent = L('English', 'Français');
$('lang-toggle').lang = L('en', 'fr');
$('lang-toggle').addEventListener('click', () => {
  const url = new URL(location.href); url.searchParams.set('lang', L('en', 'fr')); location.assign(url);
});
loadContext(new URLSearchParams(location.search).get('client')||'robert');
