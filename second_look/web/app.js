'use strict';

const $ = id => document.getElementById(id);
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
const classes = {cash:'Cash-like', fixed_income:'Fixed income', equities:'Equities', alternatives:'Alternatives'};
const money = value => new Intl.NumberFormat('en-CA',{style:'currency',currency:'CAD',maximumFractionDigits:2}).format(value);
const decimal = (value, digits=2) => Number.isFinite(value) ? new Intl.NumberFormat('en-CA',{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(value) : 'Unavailable';
const percent = value => Number.isFinite(value) ? `${(value*100).toFixed(1)}%` : 'Unavailable';
const dateLabel = value => value ? new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(value.slice(0,10)+'T00:00:00Z')) : 'Unavailable';
const node = (tag, value='', className='') => {
  const result=document.createElement(tag); result.textContent=value;
  if(className) result.className=className;
  return result;
};
const finite = value => typeof value==='number' && Number.isFinite(value);
const isTerminal = record => record && ['simulation_continued','cancelled'].includes(record.stage);
const friendly = message => {
  if(/Insufficient cash/i.test(message)) return 'This amount exceeds your available cash. No other holding is sold automatically.';
  if(/Insufficient holdings/i.test(message)) return 'This amount exceeds the value of the units you hold. Short sales are not supported.';
  if(/stale|Future|Unusable|Missing asset or price/i.test(message)) return 'A usable historical quote is unavailable for this trade. It has not been evaluated.';
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
    try { data=await response.json(); } catch { throw new Error('The service returned an unreadable response. Please try again.'); }
    if(!response.ok) throw new Error(data.error || 'The request could not be completed. Please try again.');
    return data;
  } catch(error) {
    if(expired) throw new Error('The request timed out. No confirmation was recorded in this view. Please retry.');
    if(error.name==='TypeError') throw new Error('The service could not be reached. Check the connection and try again.');
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
  if(!finite(quote?.change_native) || !finite(quote?.change_fraction)) return 'Unavailable';
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
  $('client-select').disabled=true;$('market-search').disabled=true;$('scenario-select').disabled=true;
  $('account-name').textContent='Loading account…';
  ['account-total','account-invested','account-cash'].forEach(id=>$(id).textContent='—');
  document.querySelectorAll('[data-open-side]').forEach(button=>button.disabled=true);
  inlineError('page-error-text','');$('page-error').hidden=true;
  try {
    const context=await api('/api/context?client_id='+encodeURIComponent(clientId));
    if(token!==state.accountLoad)return;
    state.context=context;
    $('account-name').textContent=context.client.name || 'Balanced demonstration';
    $('account-profile').textContent=context.client.profile+' profile';
    $('account-total').textContent=money(context.account.total_cad);
    $('account-invested').textContent=money(context.account.invested_cad);
    $('account-cash').textContent=money(context.account.cash_cad);
    $('snapshot-label').textContent='Account snapshot · '+dateLabel(context.client.holdings_as_of)+' · 00:00 UTC';
    $('user-avatar').textContent=(context.first_name || 'S').slice(0,1);
    $('client-select').replaceChildren(...context.clients.map(id=>{const option=node('option',id==='demo_balanced'?'Balanced demo':id[0].toUpperCase()+id.slice(1));option.value=id;return option;}));
    $('client-select').value=context.client.client_id;$('client-select').disabled=false;
    $('asset-options').replaceChildren(...context.tickers.map(t=>{const option=node('option');option.value=t;option.label=context.assets[t].name;return option;}));
    $('scenario-select').replaceChildren(node('option','Select a scenario'),...(DEMOS[context.client.client_id]||[]).map(([ticker,amount],i)=>{const option=node('option',`Buy ${ticker} · ${money(amount)}`);option.value=String(i);return option;}));
    $('scenario-select').options[0].value='';$('scenario-select').value='';$('scenario-select').disabled=false;
    $('market-search').disabled=false;
    if(!context.assets[state.selectedAsset])state.selectedAsset=context.tickers[0];
    renderHoldings();renderQuote();await restoreConversation(token);
  } catch(error) {
    if(token!==state.accountLoad)return;
    inlineError('page-error-text',error.message);$('page-error').hidden=false;
    $('client-select').disabled=false;
    announce('Account data could not be loaded.');
  }
}

function renderHoldings() {
  const ctx=state.context;
  const rows=ctx.holdings.map(h=>{
    const button=node('button',h.ticker,'text-button');button.type='button';button.setAttribute('aria-label',`View ${h.name} quote`);
    button.addEventListener('click',()=>{state.selectedAsset=h.ticker;showPage('markets');renderQuote();$('asset-name').scrollIntoView({block:'nearest'});});
    return [button,h.name,decimal(h.units,4),h.currency,money(h.value_cad),percent(h.weight)];
  });
  rows.push(['Cash','CAD cash ledger','—','CAD',money(ctx.account.cash_cad),percent(ctx.account.cash_cad/ctx.account.total_cad)]);
  $('holdings-table').replaceChildren(makeTable(['Asset','Name','Units','Listing currency','Value (CAD)','Portfolio weight'],rows));
  const profile=node('div');profile.append(node('p',`${ctx.client.profile} · ${ctx.client.horizon_years}-year horizon`),node('p','Objectives: '+ctx.client.objectives.join(', ').replaceAll('_',' ')));
  const goals=node('div');
  (ctx.client.goals||[]).forEach(goal=>goals.append(node('p',goal.name+' · '+(goal.type==='one_off'?money(goal.amount_cad)+' by '+dateLabel(goal.target_date):money(goal.amount_cad_per_year)+'/year from '+dateLabel(goal.start_date)))));
  if(!goals.children.length)goals.append(node('p','No dated goals are stored for this demo account.'));
  const esg=ctx.client.preferences.esg;
  if(esg?.excluded_sectors?.length)profile.append(node('p','Excluded sectors: '+esg.excluded_sectors.join(', ')));
  $('profile-details').replaceChildren(profile,goals);
}
function showPage(page) {
  $('market-view').hidden=page!=='markets';$('holdings-view').hidden=page!=='holdings';
  $('page-title').textContent=page==='markets'?'Market quote':'My holdings';
  ['markets','holdings'].forEach(key=>{const button=$('nav-'+key);button.classList.toggle('active',key===page);if(key===page)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
  if(page==='markets')renderChart();
}
function renderQuote() {
  const ctx=state.context,t=state.selectedAsset,a=ctx.assets[t],q=ctx.quotes[t],holding=ctx.holdings.find(h=>h.ticker===t);
  $('market-search').value=t;
  $('asset-name').textContent=a.name;$('asset-ticker').textContent=`${t} · ${a.currency} · ${classes[a.asset_class]}`;
  $('asset-market').textContent=a.market || a.currency;
  $('quote-currency').textContent=q?'· '+q.currency:'';
  $('quote-price').textContent=q?decimal(q.close_native):'Unavailable';setChange($('quote-change'),q);
  $('quote-volume').textContent=finite(q?.volume)?new Intl.NumberFormat('en-CA').format(q.volume):'Unavailable';
  $('quote-date').textContent=dateLabel(q?.price_source_date);
  document.querySelectorAll('[data-open-side]').forEach(button=>{button.disabled=!q || (button.dataset.openSide==='sell' && !(holding?.units>0));});
  infoGroups($('quote-information'),[
    [['Open',finite(q?.open_native)?decimal(q.open_native)+' '+a.currency:null],['Previous observed close',finite(q?.previous_close_native)?decimal(q.previous_close_native)+' '+a.currency:null],['Session low / high',finite(q?.low_native)&&finite(q?.high_native)?`${decimal(q.low_native)} / ${decimal(q.high_native)} ${a.currency}`:null]],
    [['Asset class',classes[a.asset_class]],['Listing / market',a.market||'Unavailable'],['Sector / exposure',a.sector||'Unavailable']],
    [['Value per unit in CAD',q?money(q.close_cad):'Unavailable'],['FX to CAD',q?decimal(q.fx_to_cad,4)+' CAD per '+a.currency:'Unavailable'],['FX source date',a.currency==='CAD'?'Not required · CAD asset':dateLabel(q?.fx_source_date)]]
  ]);
  infoGroups($('position-information'),[
    [['Units held',decimal(holding?.units||0,4)],['Market value (CAD)',money(holding?.value_cad||0)]],
    [['Portfolio weight',percent(holding?.weight||0)],['Available cash',money(ctx.account.cash_cad)]],
    [['Profile',ctx.client.profile],['Holdings as of',dateLabel(ctx.client.holdings_as_of)]]
  ]);
  const history=ctx.price_history[t]||[];
  $('history-table').replaceChildren(history.length?makeTable(['Observation date','Closing price (CAD)'],history.slice(-12).reverse().map(p=>[dateLabel(p.date),money(p.close_cad)])):node('p','Price history is unavailable in this snapshot.','empty-state'));
  $('quote-source').textContent='Source: AlphaForge starter dataset · Yahoo Finance prices · Bank of Canada FX. '+(q?.price_age_days>0?'The last available price is carried forward. ':'')+'No bid/ask, analyst ratings or execution quotes are supplied.';
  renderChart();
}
function renderChart() {
  if(!state.context)return;
  const points=(state.context.price_history[state.selectedAsset]||[]).filter(p=>finite(p.close_cad));
  if(points.length<2){$('price-chart').replaceChildren(node('p','A price chart is unavailable in this historical snapshot.','empty-state'));$('chart-period').textContent='';return;}
  const width=Math.max(300,$('price-chart').clientWidth||900),height=200,left=64,right=15,top=12,bottom=27;
  const prices=points.map(p=>p.close_cad),lo=Math.min(...prices),hi=Math.max(...prices),pad=Math.max((hi-lo)*.12,.01);
  const min=lo-pad,max=hi+pad,x=i=>left+i*(width-left-right)/(points.length-1),y=p=>top+(max-p)*(height-top-bottom)/(max-min);
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.classList.add('price-svg');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${state.selectedAsset} historical CAD closing prices from ${points[0].date} to ${points.at(-1).date}. Lowest ${money(lo)}, highest ${money(hi)}.`);
  const part=(tag,attrs,value)=>{const el=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);if(value!==undefined)el.textContent=value;svg.append(el);return el;};
  [0,.5,1].forEach(r=>{const value=max-r*(max-min),at=y(value);part('line',{x1:left,x2:width-right,y1:at,y2:at,stroke:'#e6eef3'});part('text',{x:left-10,y:at+4,'text-anchor':'end'},decimal(value));});
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)} ${y(p.close_cad).toFixed(2)}`).join(' ');
  part('path',{d:path+` L ${x(points.length-1)} ${height-bottom} L ${left} ${height-bottom} Z`,fill:'#edf6fc'});
  part('path',{d:path,fill:'none',stroke:'#187fac','stroke-width':'2','stroke-linejoin':'round'});
  part('circle',{cx:x(points.length-1),cy:y(points.at(-1).close_cad),r:3.5,fill:'#187fac'});
  part('text',{x:left,y:height-5},dateLabel(points[0].date));part('text',{x:width-right,y:height-5,'text-anchor':'end'},dateLabel(points.at(-1).date));
  $('price-chart').replaceChildren(svg);$('chart-period').textContent=`${points.length} source observations · ${dateLabel(points[0].date)} – ${dateLabel(points.at(-1).date)}`;
}
function selectTab(tab) {
  state.tab=tab;
  ['overview','history','position'].forEach(key=>{const selected=key===tab;$(key+'-panel').hidden=!selected;$('tab-'+key).setAttribute('aria-selected',String(selected));$('tab-'+key).tabIndex=selected?0:-1;});
  $('chart-section').hidden=tab==='position';renderChart();
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
  if(!ticker)throw new Error('Choose an asset from the provided ticker list.');
  if(!finite(amount)||amount<=0)throw new Error('Enter a CAD amount greater than zero.');
  const q=state.context.quotes[ticker];
  if(!q)throw new Error('No usable historical quote is available for this asset.');
  const available=state.side==='buy'?state.context.account.cash_cad:(state.context.holdings.find(h=>h.ticker===ticker)?.value_cad||0);
  if(amount>available+1e-8)throw new Error(state.side==='buy'?`This trade costs ${money(amount)}, but only ${money(available)} is available in cash.`:`Your holding is worth ${money(available)}. You cannot sell more than you hold.`);
  return {client_id:state.context.client.client_id,decision_date:state.context.client.holdings_as_of,ticker,side:state.side,amount_cad:amount};
}
function openTicket(side=state.side, ticker=state.selectedAsset, amount='', resume=false) {
  if(!state.context)return;
  state.returnFocus=document.activeElement;
  if(!resume){invalidate();state.side=side;$('ticket-asset').value=ticker;$('amount').value=amount;}
  $('ticket-review-slot').append($('review-panel'));
  $('ticket-account-name').textContent=(state.context.client.name||'Balanced demo')+' · CAD simulation';
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
  $('ticket-title').textContent=state.side==='buy'?'Buy':'Sell';
  $('side-buy').setAttribute('aria-pressed',String(state.side==='buy'));$('side-sell').setAttribute('aria-pressed',String(state.side==='sell'));
  $('ticket-title').style.color=state.side==='sell'?'var(--red)':'var(--blue-dark)';
  $('ticket-asset-name').textContent=a?a.name:'Choose a ticker or company from the list.';
  $('ticket-category').textContent=a?classes[a.asset_class]:'Provided asset universe';
  $('ticket-quote-symbol').textContent=a?`${ticker} · ${a.currency}`:'Quote unavailable';
  $('ticket-quote-price').textContent=q?decimal(q.close_native)+' '+q.currency:'Unavailable';setChange($('ticket-quote-change'),q);
  const details=[];
  if(q){details.push(['CAD value / unit',money(q.close_cad)]);if(finite(q.volume))details.push(['Source volume',new Intl.NumberFormat('en-CA').format(q.volume)]);if(finite(q.low_native)&&finite(q.high_native))details.push(['Session low / high',decimal(q.low_native)+' / '+decimal(q.high_native)]);details.push(['FX to CAD',decimal(q.fx_to_cad,4)]);const holding=state.context.holdings.find(h=>h.ticker===ticker);details.push(['Units held',decimal(holding?.units||0,4)]);if(state.side==='sell')details.push(['Holding value',money(holding?.value_cad||0)]);}
  $('ticket-quote-details').replaceChildren(...details.map(([label,value])=>{const group=node('div');group.append(node('dt',label),node('dd',value));return group;}));
  $('ticket-quote-date').textContent=q?'Historical close · '+dateLabel(q.price_source_date):'A usable dated quote is required';
  const amount=Number($('amount').value);
  $('unit-estimate').textContent=q&&amount>0?`Estimated units: ${decimal(amount/q.close_cad,6)} · valued at ${money(q.close_cad)} CAD per unit.`:'Enter a CAD amount to see estimated units.';
  syncButtons();
}
function syncButtons() {
  const record=state.record,busy=state.evaluating||state.deciding;
  $('submit-trade').disabled=busy||!!record;
  $('submit-trade').textContent=state.evaluating?'Evaluating…':record?(record.result.verdict==='red'?'Trade paused':isTerminal(record)?'Simulation finished':'Review required'):'Check simulated trade';
  $('submit-trade').className='button '+(state.side==='sell'?'red':'blue');
  $('cancel-trade').disabled=state.deciding;
  ['amount','ticket-asset','side-buy','side-sell'].forEach(id=>$(id).disabled=state.deciding);
  $('send-reason').disabled=state.reviewing||state.deciding||!record||isTerminal(record)||!!state.pendingMessage||state.chat?.messages.filter(m=>m.role==='user').at(-1)?.status==='failed';
  $('send-reason').textContent=state.reviewing?'Replying…':'Send';
  $('reason').disabled=state.reviewing||state.deciding||isTerminal(record)||!!state.pendingMessage;
  $('risk-checkbox').disabled=state.deciding||isTerminal(record);
  $('proceed-red').disabled=!$('risk-checkbox').checked||state.deciding||isTerminal(record)||!record;
  $('cancel-review').disabled=state.deciding||isTerminal(record);
  $('revise-trade').disabled=state.deciding;
}
function ticketEdited() {
  const shouldRefresh=!!state.record||state.evaluating||state.autoEvaluate;
  invalidate();state.autoEvaluate=shouldRefresh;updateTicketQuote();
  if(shouldRefresh){showTicketStatus('Checking revised trade…','Your previous result is no longer valid.','');state.timer=setTimeout(()=>{try{draftRequest();evaluateTrade();}catch(error){inlineError('ticket-error',error.message);$('ticket-status').hidden=true;}},500);}
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
  showTicketStatus('Evaluating this trade…','Checking your stored profile, goals and preferences.','');syncButtons();
  try {
    const record=await api('/api/evaluate',request,state.evaluationController.signal);
    if(token!==state.evaluation)return;
    state.record=record;state.evaluating=false;rememberTrade();renderResult();
    if(record.result.verdict==='red'){
      const first=state.context.first_name;$('invite-title').textContent=first?`Hi, ${first}!`:'Hi there!';
      $('invite-message').textContent='This trade appears misaligned with your investor profile. Would you like to talk about it?';
      if($('trade-dialog').open){
        setInvitation(true);announce('Trade paused. A Second Look invitation is available.');
      }else{
        $('page-review-slot').append($('review-panel'));revealReview(false);
        announce('The pending trade was flagged red. Its explanation is available in the page.');
      }
    }
  }catch(error){
    if(token!==state.evaluation||error.name==='AbortError')return;
    state.evaluating=false;state.record=null;
    showTicketStatus('Trade not evaluated','No approval or confirmation was recorded. Please try again.','');
    inlineError('ticket-error',friendly(error.message));
  }finally{if(token===state.evaluation){state.evaluating=false;syncButtons();}}
}
function renderResult() {
  const record=state.record;if(!record)return;
  const r=record.result;
  if(record.stage==='cancelled')showTicketStatus('Simulation cancelled','No order was placed.','');
  else if(record.stage==='simulation_continued')showTicketStatus(r.verdict==='red'?'Risk acknowledged · simulation continued':'Simulation continued',r.verdict==='red'?'The numerical verdict remains red. No real order was placed.':'This is an independent simulated trade. Your stored holdings have not changed.',r.verdict||'');
  else if(r.verdict==='red')showTicketStatus('Red · trade paused','Review the flagged risks before deciding how to continue.','red');
  else if(r.verdict==='yellow'){
    showTicketStatus('Yellow · confirmation required',r.reasons.join(' '),'yellow');
    const confirm=node('button',state.deciding?'Confirming…':'Confirm simulation','button blue compact');confirm.type='button';confirm.id='confirm-yellow';confirm.disabled=state.deciding;confirm.addEventListener('click',()=>decision('confirm'));
    $('ticket-status').append(node('div','','confirmation-action'));$('ticket-status').lastChild.append(confirm);
    setInvitation(false);$('review-panel').hidden=true;
  }else{showTicketStatus('Manual review required',r.reasons.join(' '),'');setInvitation(false);$('review-panel').hidden=true;}
  if(r.verdict==='red'&&!$('review-panel').hidden)renderReview();
  syncButtons();
}
function exposureName(check) {
  const part=check.rule.split('.');
  if(check.kind==='goal')return check.goal+' · '+dateLabel(check.date);
  return ({single_stock:`${part.slice(1).join('.')} position`,crypto:'Crypto allocation',hhi:'Position concentration (HHI)',allocation:`${classes[part[1]]||part[1]} allocation`,sector:`${part.slice(1).join('.')} sector`,liquid_cash_reserve:'Liquid cash reserve'})[part[0]]||check.ticker||'ESG exposure';
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
    $('red-summary').textContent=`${exposureName(primary)}: ${format(primary.before)} before → ${format(primary.after)} after, compared with a ${primary.bound==='min'?'minimum':'maximum'} of ${format(primary.limit)}.`;
  }else $('red-summary').textContent=primary?.message||r.reasons[0];
  $('pending-trade-summary').textContent=`${request.side==='buy'?'Buy':'Sell'} ${request.ticker} · ${money(request.amount_cad)} CAD · ${dateLabel(request.decision_date)}. Original verdict: Red.`;
  const rows=checks.filter(c=>finite(c.before)&&finite(c.after)&&finite(c.limit)).map(c=>{const format=c.unit==='hhi_index'?v=>decimal(v,3):percent;return [exposureName(c),format(c.before),format(c.after),(c.bound==='min'?'Minimum ':'Maximum ')+format(c.limit)];});
  $('exposure-table').replaceChildren(makeTable(['Flagged measure','Before','After','Limit'],rows));
  renderSpectrum(r.classification);
  $('reason-form').hidden=isTerminal(record);
  renderConversation(record);
  const terminal=isTerminal(record);
  document.querySelector('.review-kicker').textContent=terminal?'SECOND LOOK · '+(record.stage==='cancelled'?'SIMULATION CANCELLED':'RISK ACKNOWLEDGED'):'SECOND LOOK · TRADE PAUSED';
  $('risk-actions').hidden=terminal;$('review-terminal').hidden=!terminal;
  $('review-terminal').textContent=record.stage==='cancelled'?'Simulation cancelled. Your stored holdings are unchanged.':'You explicitly acknowledged the risks and continued the simulation. The rule engine verdict remains red. No real order was placed.';
  syncButtons();
}
function renderSpectrum(c) {
  const root=$('review-spectrum');root.hidden=!c;if(!c)return;
  root.replaceChildren(node('p',`Reference spectrum · stored target: ${c.target_profile}`,'small-label'));
  const axis=node('div','','spectrum-axis');['Income','Conservative','Balanced','Growth','Maximum growth'].forEach(label=>axis.append(node('span',label)));root.append(axis);
  for(const key of ['before','after']){
    const row=node('div','','spectrum-position'),track=node('div','','position-track'),dot=node('span','','position-dot '+key);
    dot.style.left=(c[key].normalized_score*100)+'%';track.append(dot);row.append(node('span',key==='before'?'Before':'After'),track);root.append(row);
    const memberships=Object.entries(c[key].membership_weights).filter(([,w])=>w>1e-10).map(([label,w])=>percent(w)+' '+label).join(' / ');
    root.append(node('p',`${key==='before'?'Before':'After'}: ${memberships} · allocation score ${c[key].score.toFixed(3)} · mismatch distance ${c[key].distance.toFixed(4)}`,'spectrum-membership'));
  }
  root.append(node('p','Memberships describe asset allocation, not probabilities or complete risk. A small mismatch does not override a hard limit.','muted'));
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
  }catch(error){if(accountToken===state.accountLoad)inlineError('page-error-text','The saved conversation could not be restored. '+error.message);}
}
function renderConversation(record) {
  const container=$('conversation-result');container.replaceChildren();
  const messages=state.chat?.messages||[];
  messages.forEach(message=>{
    const article=node('article','',message.role==='assistant'?'agent-answer':'conversation-message');
    article.append(node('span',message.role==='assistant'?'Second Look':'You','small-label'),node('p',message.text));
    if(message.error)article.append(node('p',message.error,'backend-unavailable'));
    if(message.diagnostic){
      const detail=node('details');detail.append(node('summary','Diagnostic reference'));
      detail.append(node('p',`${message.diagnostic.component} · ${message.diagnostic.code} · request ${message.diagnostic.request_id}`));
      if(message.diagnostic.aws_request_id)detail.append(node('p','AWS request '+message.diagnostic.aws_request_id));
      article.append(detail);
    }
    if(message.evidence_refs?.length){
      const detail=node('details');detail.append(node('summary','Evidence and calculation dates'));
      message.evidence_refs.forEach(ref=>{
        const item=state.evidence.find(e=>e.tool_call_id===ref);if(!item)return;
        detail.append(node('p',`${item.source_or_method} · as of ${dateLabel(item.as_of)} · ${item.data_status.replaceAll('_',' ')}`));
        // URLs come only from stored tool evidence, never from model text.
        const data=item.data;
        for(const source of (data.items||[]))if(/^https?:\/\//.test(source.url||'')){
          const link=node('a',`${source.source_name||source.title||'Official source'} · ${source.published_date}`);link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';detail.append(link);
        }
      });article.append(detail);
    }
    container.append(article);
  });
  if(state.pendingMessage&&!messages.some(m=>m.id===state.pendingMessage.message_id)){
    const pending=node('article','','conversation-message');pending.append(node('span','You','small-label'),node('p',state.pendingMessage.message));container.append(pending);
  }
  if(state.reviewing){container.append(node('p',state.chat?.messages.length?'Preparing a reply…':'Consulting the Profile, News and Financial Data specialists, then preparing your reply…','backend-unavailable'));container.setAttribute('aria-busy','true');}
  else container.removeAttribute('aria-busy');
  const lastUser=messages.filter(m=>m.role==='user').at(-1);
  const retry=lastUser&&lastUser.status!=='completed';
  $('retry-reply').hidden=state.reviewing||isTerminal(record)||(!retry&&!state.pendingMessage);
  $('refresh-conversation').hidden=!state.chat&&!state.pendingMessage;
  $('reason-label').textContent=messages.length?'Ask a question or add to your reasoning':'What made you interested in this trade?';
  $('reason').placeholder=messages.length?'Ask Second Look…':'Tell us what you had in mind…';
  const scenarios=$('conversation-scenarios');scenarios.replaceChildren();
  (state.chat?.scenarios||[]).forEach(scenario=>{
    const section=node('section','','scenario-assessment');
    section.append(node('h3',scenario.kind==='future_contribution'?'Hypothetical assessment after contribution':scenario.kind==='market_shock'?'Hypothetical market scenario':'Separate assessment of your revised trade'));
    const r=scenario.result;
    section.append(node('p',`Trade verdict: ${r.verdict||'manual review'} · All applicable limits met: ${r.portfolio_within_limits===true?'Yes':r.portfolio_within_limits===false?'No':'Unverified'}.`));
    section.append(makeTable(['Allocation','Hypothetical share'],Object.entries(r.weights_after).map(([key,value])=>[classes[key],percent(value)])));
    const breaches=r.residual_breaches||[];
    if(breaches.length){const list=node('ul');breaches.forEach(c=>list.append(node('li',`${c.rule}: ${c.unit==='hhi_index'?decimal(c.after,3):percent(c.after)}; ${c.bound==='min'?'minimum':'maximum'} ${c.unit==='hhi_index'?decimal(c.limit,3):percent(c.limit)}.`)));section.append(list);}
    if(r.compliance_unresolved?.length)section.append(node('p','Some limits could not be verified.','backend-unavailable'));
    section.append(node('p',`${scenario.kind==='market_shock'?'Client-specified hypothetical shocks applied; other prices and FX held':'Prices and FX held'} at ${dateLabel(scenario.as_of)}; fees and taxes excluded. This is a conditional comparison, with no funding or execution.`,'muted'));
    if(scenario.kind==='market_shock')section.append(makeTable(['Hypothetical shock','Assumed return'],Object.entries(scenario.assumptions.shocks).map(([key,value])=>[key,percent(value)])));
    if(scenario.kind==='future_contribution')section.append(node('p',`Expected timing (your statement): ${scenario.plan.expected_timing}. Certainty: ${scenario.plan.certainty}. Until arrival, or if delayed, the current trade assessment still applies. Goal runway is held at the original decision date.`,'muted'));
    if(scenario.kind==='future_contribution'){
      section.append(node('p',`Your stated contribution: ${decimal(scenario.plan.amount)} ${scenario.plan.currency}. At the snapshot FX rate: ${money(scenario.contribution_cad)} CAD.`));
      if(scenario.plan.purchases.length)section.append(makeTable(['Client-stated planned purchase','Amount (CAD)'],scenario.plan.purchases.map(p=>[p.ticker,money(p.amount_cad)])));
    }
    if(scenario.kind==='future_contribution'&&!isTerminal(record)&&!record.superseded_by){
      const label=node('label','','checkbox-label'),check=node('input');check.type='checkbox';
      label.append(check,node('span','In this fictional account, confirm that this cash has arrived at the historical snapshot date. No planned purchases will execute.'));
      const button=node('button','Confirm simulated cash arrival and recheck','button secondary compact');button.type='button';button.disabled=true;
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
    announce('Simulated cash arrival confirmed. A new assessment was calculated; no purchases executed.');
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
    const message=$('reason').value.trim();if(!message){inlineError('review-error','Please enter a message.');$('reason').focus();return;}
    payload={message_id:crypto.randomUUID(),message};
  }
  if(!payload)return;
  const id=state.record.simulation_id,token=state.evaluation;
  payload={...payload,client_id:state.record.request.client_id,input_version:state.record.assessment_input_version,expected_revision:state.chat?.revision||0};
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
    announce(record.stage==='cancelled'?'Simulation cancelled.':'Simulation continued. No real order was placed.');
    if(action==='cancel')closeTicket();
  }catch(error){inlineError($('review-panel').hidden?'ticket-error':'review-error',error.message);}
  finally{state.deciding=false;syncButtons();if($('confirm-yellow'))$('confirm-yellow').disabled=false;}
}
function reviseTrade() {
  const request=state.record?.request;
  if(!request)return;
  openTicket(request.side,request.ticker,request.amount_cad);
  state.autoEvaluate=true;
  announce('Revise the pending trade. A new evaluation is required.');
}

// Functional navigation and keyboard behavior; no decorative product controls.
$('nav-markets').addEventListener('click',()=>showPage('markets'));
$('nav-holdings').addEventListener('click',()=>showPage('holdings'));
$('view-holdings').addEventListener('click',()=>showPage('holdings'));
$('back-to-markets').addEventListener('click',()=>showPage('markets'));
$('client-select').addEventListener('change',()=>{const id=$('client-select').value;const url=new URL(location.href);url.searchParams.set('client',id);history.replaceState(null,'',url);loadContext(id);});
$('retry-context').addEventListener('click',()=>loadContext(new URLSearchParams(location.search).get('client')||'robert'));
$('market-search').addEventListener('change',()=>{const ticker=resolveAsset($('market-search').value);if(ticker){state.selectedAsset=ticker;renderQuote();}else{announce('Choose a ticker from the provided list.');$('market-search').value=state.selectedAsset;}});
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
const tabs=['overview','history','position'];
tabs.forEach((tab,index)=>{
  $('tab-'+tab).addEventListener('click',()=>selectTab(tab));
  $('tab-'+tab).addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;if(next!==undefined){event.preventDefault();selectTab(tabs[next]);$('tab-'+tabs[next]).focus();}});
});
let resizeFrame;
window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(renderChart);});
loadContext(new URLSearchParams(location.search).get('client')||'robert');
