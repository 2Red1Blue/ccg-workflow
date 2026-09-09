const $ = id => document.getElementById(id);
const labels = {running:'执行中', succeeded:'执行成功', failed:'执行失败', timed_out:'执行超时', cancelled:'已取消', interrupted:'进程已中断', unknown:'状态未知', starting:'启动中', thinking:'处理中', answering:'生成报告', completed:'报告已收到', analyzed:'分析已完成', approved:'双路通过', changes:'需要修改', incomplete:'结果未齐', pending:'等待结论', partial:'部分报告', APPROVE:'通过', REQUEST_CHANGES:'需要修改'};
// Discard credentials left by the previous token-based viewer, if any.
try { sessionStorage.removeItem('ccg-review-token'); } catch { /* Storage may be disabled. */ }
if (new URLSearchParams(location.hash.slice(1)).has('token')) history.replaceState(null, '', location.pathname + location.search);
let runs = [], selected = null, shown = 40, busy = false, lastDetail = '', lastList = '', selectedDetailSignature = '', collapsedReports = new Set();
function el(tag, cls, text) { const node=document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; }
const date = value => value ? new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const duration = value => { value=Math.max(0,Number(value)||0);return value>=60 ? `${Math.floor(value/60)}分${Math.floor(value%60)}秒` : `${Math.floor(value)}秒`; };
const project = row => row.workdir.split(/[\\/]/).filter(Boolean).pop() || '未记录项目';
function badge(value) { const tone=['changes','REQUEST_CHANGES'].includes(value)?'changes':['incomplete','failed','timed_out','cancelled','interrupted','unknown'].includes(value)?'incomplete':'';return el('span',`badge ${tone}`,labels[value] || value); }
function syncFilterButtons(value) { document.querySelectorAll('[data-filter]').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.filter===value))); }
function applyFilter(value) { $('filter').value=value;shown=40;syncFilterButtons(value);lastList='';renderList(); document.querySelector('.history')?.scrollIntoView({behavior:'smooth',block:'start'}); }
function clearDetail() { $('detail').replaceChildren(el('div','empty',undefined)); const empty=$('detail').firstElementChild;empty.append(el('span','empty-mark','[ … ]'),el('h2','', '选择一条审查'),el('p','', '历史记录将在这里呈现，两路报告各自保留。')); }
async function api(path) { try { const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(8000)}); const data=await response.json(); if(!response.ok) throw Error(data.error || '读取失败'); return data; } catch(error) { if(error.name==='TimeoutError') throw Error('审查数据请求超时，请稍后重试。'); throw error; } }
function renderList() {
  const query=$('search').value.trim().toLowerCase(), filter=$('filter').value;
  const filtered=runs.filter(row => `${row.workdir} ${row.id}`.toLowerCase().includes(query) && (filter==='all' || (filter==='running'?row.state==='running':row.verdict===filter)));
  const signature=JSON.stringify([filtered.map(row=>({id:row.id,state:row.state,verdict:row.verdict,finished:row.finished})),selected,shown,query,filter]);
  if(signature===lastList)return;lastList=signature;
  const scrollTop=$('runs').scrollTop;
  const focusedId=document.activeElement?.dataset?.runId;
  $('count').textContent=`${filtered.length} 条`; $('runs').replaceChildren();
  for(const row of filtered.slice(0,shown)) {
    const button=el('button','run'); button.setAttribute('aria-current',String(row.id===selected));button.dataset.runId=row.id;
    button.append(el('div','run-title',project(row)));
    const meta=el('div','run-meta'); meta.append(el('span','',date(row.started)),el('span','',labels[row.verdict])); button.append(meta,el('div','run-id',`${row.id.slice(0,8)} · ${labels[row.state]}`));
    button.addEventListener('click',()=>select(row.id)); $('runs').append(button);
  }
  if(!filtered.length) $('runs').append(el('p','empty',runs.length?'没有匹配记录。清除搜索或调整筛选。':'暂无保留的双模型审查。触发一次 supervisor review 后会自动出现。'));
  $('more').hidden=filtered.length<=shown;
  if(focusedId)for(const button of $('runs').children)if(button.dataset.runId===focusedId)button.focus({preventScroll:true});
  $('runs').scrollTop=scrollTop;
}
async function select(id) { selected=id; lastDetail=''; selectedDetailSignature=''; renderList(); await detail(); }
async function detail() {
  if(!selected) return false;
  const id=selected;
  try {
    const row=await api(`/api/runs/${id}`);
    if(selected!==id) return;
    const signature=JSON.stringify(row); if(signature===lastDetail) return; lastDetail=signature;
    const container=$('detail');
    const offsets=[...container.querySelectorAll('.report-body')].map(node=>node.scrollTop);
    container.replaceChildren();
    const head=el('div','detail-head'); head.append(el('p','eyebrow','REVIEW / '+id.slice(0,8)),el('h2','',project(row)),el('p','path',row.workdir));
    const badges=el('div','badges'); badges.append(badge(row.state));if(row.verdict)badges.append(badge(row.verdict));head.append(badges);
    const meta=el('div','metadata'); meta.append(el('span','',`开始 ${date(row.started)}`),el('span','',row.finished?`总耗时 ${duration(row.finished-row.started)}`:'尚无结束记录'),el('span','',`差异 ${(row.patch_bytes/1024).toFixed(1)} KB`));head.append(meta,el('div','path',id));
    if(row.retry_of) {const retry=el('button','',`查看此前尝试 ${row.retry_of.slice(0,8)} ↗`);retry.addEventListener('click',()=>select(row.retry_of));head.append(retry);}
    if(row.error)head.append(el('p','note',row.error));
    if(row.state==='interrupted')head.append(el('p','note','执行锁已释放，但没有最终状态记录。此任务可能异常退出；这里不将它标记为仍在运行或已完成。'));
    container.append(head);
    const reports=el('div','reports');
    for(const name of ['codex','claude']) {
      const result=row.backends[name],card=el('article','report-card'),header=el('div','report-head'),line=el('div','report-line');
      const copy=el('button','','复制报告');copy.disabled=!result.report; copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(result.report);copy.textContent='已复制';}catch{copy.textContent='请手动选择复制';}});
      const actions=el('div','report-actions');const bodyId=`report-${name}-${id}`;const isCollapsed=collapsedReports.has(bodyId);const toggle=el('button','',isCollapsed?'展开':'收起');toggle.setAttribute('aria-expanded',String(!isCollapsed));toggle.setAttribute('aria-controls',bodyId);actions.append(copy,toggle);line.append(el('h3','',name==='codex'?'Codex':'Claude Code'),actions);header.append(line);
      header.append(el('p','model',result.models.length?`实际模型 · ${result.models.join(', ')}`:result.requested_model?`请求模型 · ${result.requested_model}（未回传实际模型）`:'实际模型 · 未记录'));
      const status=el('div','badges'); status.append(badge(result.state));if(result.verdict)status.append(badge(result.verdict)); if(result.partial)status.append(badge('partial')); header.append(status);
      if(result.duration)header.append(el('div','model',`耗时 ${duration(result.duration)}`));
      if(result.last_event)header.append(el('div','model',`最近事件 ${date(result.last_event)}`));
      if(result.termination)header.append(el('p','note',`终止原因：${result.termination}`));
      if(result.format_error)header.append(el('p','note',`报告格式：${result.format_error}`));
      if(result.truncated)header.append(el('p','note','报告达到保存上限，内容可能截断。'));
      const body=el('pre','report-body',result.report || (row.state==='running'?'等待报告。页面会自动刷新。':'没有可用报告；可能未生成、已清理或超过读取上限。'));body.id=bodyId;body.hidden=isCollapsed;toggle.addEventListener('click',()=>{body.hidden=!body.hidden;toggle.setAttribute('aria-expanded',String(!body.hidden));toggle.textContent=body.hidden?'展开':'收起';if(body.hidden)collapsedReports.add(bodyId);else collapsedReports.delete(bodyId);});card.append(header,body);reports.append(card);
    }
    container.append(reports);
    if(container.dataset.runId===id)[...container.querySelectorAll('.report-body')].forEach((node,index)=>{node.scrollTop=offsets[index]||0;});
    container.dataset.runId=id;
    return true;
  } catch(error) { if(selected===id){lastDetail='';$('detail').dataset.runId='';$('detail').replaceChildren(el('p','note',error.message));} return false; }
}
async function refresh() {
  if(busy)return;busy=true;$('refresh').disabled=true;
  try { const data=await api('/api/runs');runs=data.runs;
    if(selected&&!runs.some(row=>row.id===selected)){selected=runs[0]?.id||null;selectedDetailSignature='';lastDetail='';if(!selected)clearDetail();}
    $('error').hidden=!data.capped;$('error').textContent=data.capped?'目录达到扫描上限，当前只展示部分历史。':'';
    $('connection').textContent='本机已连接 · '+new Date().toLocaleTimeString('zh-CN');
    $('total').textContent=runs.length;$('running').textContent=runs.filter(r=>r.state==='running').length;$('changes').textContent=runs.filter(r=>r.verdict==='changes').length;$('incomplete').textContent=runs.filter(r=>r.verdict==='incomplete').length;
    if(!selected&&runs.length){selected=runs[0].id;selectedDetailSignature='';}syncFilterButtons($('filter').value);renderList();
    const selectedRow=runs.find(row=>row.id===selected);const nextDetailSignature=JSON.stringify(selectedRow||null);
    if(nextDetailSignature!==selectedDetailSignature||selectedRow?.state==='running'){const detailLoaded=await detail();if(detailLoaded&&selected===selectedRow?.id)selectedDetailSignature=nextDetailSignature;}
  } catch(error) {$('connection').textContent='连接已断开';$('error').hidden=false;$('error').textContent=error.message;}
  finally{busy=false;$('refresh').disabled=false;}
}
$('search').addEventListener('input',()=>{shown=40;renderList();});$('filter').addEventListener('change',()=>{shown=40;renderList();});$('more').addEventListener('click',()=>{shown+=40;renderList();});$('refresh').addEventListener('click',refresh);
document.querySelectorAll('[data-filter]').forEach(node=>node.addEventListener('click',()=>applyFilter(node.dataset.filter)));$('filter').addEventListener('change',()=>syncFilterButtons($('filter').value));syncFilterButtons($('filter').value);
const poster=$('poster-toggle');poster.addEventListener('click',()=>{const focused=poster.classList.toggle('is-focused');poster.setAttribute('aria-pressed',String(focused));});
document.addEventListener('keydown',event=>{const typing=['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName);if(event.key==='/'&&!typing&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!event.shiftKey){event.preventDefault();$('search').focus();}else if(event.key.toLowerCase()==='r'&&!typing&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!event.shiftKey)refresh();else if(event.key==='Escape'&&document.activeElement===$('search')){$('search').value='';shown=40;lastList='';renderList();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});setInterval(()=>{if(!document.hidden)refresh();},3000);refresh();
