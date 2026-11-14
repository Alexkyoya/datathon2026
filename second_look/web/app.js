'use strict';
let context, current;
const $ = id => document.getElementById(id);
const pct = value => `${(value * 100).toFixed(1)}%`;
const labels = {cash:'Cash',fixed_income:'Fixed income',equities:'Equities',alternatives:'Alternatives'};
function text(tag, value, className) { const node=document.createElement(tag); node.textContent=value; if(className)node.className=className; return node; }
async function api(path, payload) {
  const response = await fetch(path, payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || 'Unable to complete simulation');
  return data;
}
function error(err) { $('error').textContent=err.message; $('error').hidden=false; }
function drawSpectrum(classification) {
  const target=context.client.profile;
  const track=text('div','', 'track');
  context.references.forEach((ref,i)=> {const node=text('div',ref.category,`node${ref.category===target?' target':''}`);node.style.left=`${i*25}%`;track.append(node);});
  if(classification) ['before','after'].forEach(key=>{const marker=text('div',key==='before'?'Before':'After',`marker ${key}`);marker.style.left=`${classification[key].normalized_score*100}%`;track.append(marker);});
  $('spectrum').replaceChildren(track);
}
function membership(projection) {
  return Object.entries(projection.membership_weights).filter(([,weight])=>weight>1e-10).map(([name,weight])=>`${pct(weight)} ${name}`).join(' / ');
}
function render(record) {
  current=record; const r=record.result, c=r.classification;
  drawSpectrum(c);
  $('positions').replaceChildren(...['before','after'].map(key=>{
    const block=text('div','', 'position');block.append(text('span',key==='before'?'Before the trade':'After the trade','eyebrow'),text('strong',membership(c[key])),text('span',`Score ${c[key].score.toFixed(3)} · ${Math.abs(c[key].score-c.target_score).toFixed(3)} from stored target`,'muted'));return block;
  }));
  const outcome=$('outcome');outcome.className=`panel status ${r.verdict || 'manual'}`;outcome.replaceChildren(text('p',r.verdict || 'Manual review','status-label'),text('h2',({green:'Simulation can continue.',yellow:'Review before continuing.',red:'Pause and take a second look.'})[r.verdict] || 'Required information is missing.'));
  const reasons=text('ul','');
  r.alerts.forEach(check=>{
    const parts=check.rule.split('.');
    const name=({allocation:`${labels[parts[1]] || parts[1]} allocation`,single_stock:`${parts[1]} position`,crypto:'Crypto allocation',hhi:'Position concentration (HHI)',excluded_ticker:`Excluded asset ${parts[1]}`,excluded_sector:`Excluded sector ${parts[1]}`,sector:`${parts[1]} sector`,liquid_cash_reserve:'Liquid cash reserve'})[parts[0]] || check.rule;
    const format=check.unit==='hhi_index'?value=>value.toFixed(3):pct;
    reasons.append(text('li',`${name}: ${format(check.before)} before → ${format(check.after)} after; ${check.bound==='max'?'maximum':'minimum'} ${format(check.limit)}. ${check.gap_after>0?'A breach remains.':'Within the warning buffer.'}`));
  });
  if(r.verdict==='green' && r.effect==='reduces') reasons.prepend(text('li','An existing breach improves without creating or worsening another. Remaining breaches are shown below.'));
  r.coverage.unresolved.forEach(reason=>reasons.append(text('li',reason)));
  if(!reasons.children.length) reasons.append(text('li','All evaluated limits are respected, with no near-limit warning.'));
  outcome.append(reasons);
  const stageLabels={simulation_continued:'Simulation continued. No real order was placed.',awaiting_confirmation:'Confirmation is required to continue this simulation.',awaiting_reason:'Explain your reason to request an agent review.',awaiting_agents:({dispatched:'Agent review requested from the configured service.',not_configured:'Agent handoff saved. No agent service is configured locally; review remains pending.',dispatch_failed:'Agent service could not be reached. Review remains pending.'})[record.agent_status],cancelled:'Simulation cancelled.',manual_review:'Manual review is required. This result cannot continue automatically.'};
  outcome.append(text('p',stageLabels[record.stage] || record.stage));
  if(record.stage==='awaiting_reason') {
    const form=document.createElement('form');const label=text('label','Why do you want to make this trade?');label.htmlFor='reason';const input=document.createElement('textarea');input.id='reason';input.required=true;input.maxLength=5000;const button=text('button','Request agent review');button.type='submit';form.append(label,input,button);form.addEventListener('submit',event=>{event.preventDefault();decide('explain',input.value);});outcome.append(form);
  }
  if(['awaiting_confirmation','awaiting_reason'].includes(record.stage)) {
    const row=text('div','', 'action-row');
    if(record.stage==='awaiting_confirmation'){const confirm=text('button','Confirm simulation');confirm.addEventListener('click',()=>decide('confirm'));row.append(confirm);}
    const cancel=text('button','Cancel simulation');cancel.addEventListener('click',()=>decide('cancel'));row.append(cancel);outcome.append(row);
  }
  $('mismatch').hidden=false;$('distance').textContent=`Distance before: ${c.before.distance.toFixed(4)} · after: ${c.after.distance.toFixed(4)}. Spectrum movement: ${c.movement.score_delta>=0?'+':''}${c.movement.score_delta.toFixed(3)}; change in distance from stored target: ${c.movement.change_in_distance_to_target>=0?'+':''}${c.movement.change_in_distance_to_target.toFixed(3)}.`;
  $('class-rows').replaceChildren(...c.class_order.map(cls=>{const row=document.createElement('tr');[labels[cls],pct(r.weights_before[cls]),pct(r.weights_after[cls]),pct(c.after.projected_class_weights[cls]),pct(c.after.residual_class_weights[cls])].forEach(v=>row.append(text('td',v)));return row;}));
  $('audit').hidden=false;$('audit-json').textContent=JSON.stringify(record,null,2);
}
async function evaluate() {
  $('error').hidden=true;$('evaluate').disabled=true;
  // An invalid new proposal must not leave an old green outcome visible.
  drawSpectrum();$('positions').replaceChildren(text('p','Waiting for the current proposal.','muted'));$('outcome').className='panel';$('outcome').replaceChildren(text('h2','Checking this proposal…'));$('mismatch').hidden=true;$('audit').hidden=true;current=null;
  try {render(await api('/api/evaluate',{client_id:context.client.client_id,decision_date:context.client.holdings_as_of,ticker:$('ticker').value,side:$('side').value,amount_cad:Number($('amount').value)}));}
  catch(err){$('outcome').replaceChildren(text('h2','Proposal could not be evaluated.'),text('p','No verdict was produced. Correct the issue before continuing.'));error(err);}
  finally {$('evaluate').disabled=false;}
}
async function decide(decision,reason) { try { $('error').hidden=true;render(await api(`/api/simulations/${current.simulation_id}/decision`,{decision,...(reason!==undefined?{reason}:{})})); }catch(err){error(err);} }
async function start() {
  try {
    context=await api('/api/context');const client=context.client;
    $('profile').textContent=`Target: ${client.profile}`;$('client-context').textContent=`${client.horizon_years}-year horizon · ${client.objectives.join(', ').replaceAll('_',' ')}. Horizon and loss tolerance are context, not a new profile score.`;$('cash').textContent=`Available cash: ${client.cash_cad.toLocaleString('en-CA',{style:'currency',currency:'CAD'})}`;$('asof').textContent=`Holdings / decision date: ${client.holdings_as_of} · 00:00 UTC daily snapshot`;
    context.tickers.forEach(ticker=>{const option=text('option',ticker);option.value=ticker;$('ticker').append(option);});$('ticker').value='SPY';drawSpectrum();
    $('reference-source').textContent=`${context.reference_config.source.title}, page 1, recommended allocations. Version ${context.reference_config.version}.`;
    $('reference-rows').replaceChildren(...context.references.map(ref=>{const row=document.createElement('tr');[ref.category,...['ZST.TO','XBB.TO','XIU.TO','SPY','GLD'].map(t=>pct(ref.ticker_weights[t]))].forEach(v=>row.append(text('td',v)));return row;}));
  }catch(err){error(err);$('evaluate').disabled=true;}
}
$('trade-form').addEventListener('submit',event=>{event.preventDefault();evaluate();});
document.querySelectorAll('[data-demo]').forEach(button=>button.addEventListener('click',()=>{if(!context)return;const [ticker,amount]=({green:['SPY',4000],yellow:['GLD',14000],red:['TSLA',12000]})[button.dataset.demo];$('ticker').value=ticker;$('amount').value=amount;$('side').value='buy';evaluate();}));
start();
