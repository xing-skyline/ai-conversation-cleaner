"use strict";
const $ = id => document.getElementById(id);
const fromHash = new URLSearchParams(location.hash.slice(1)).get('token');
if (fromHash) { sessionStorage.setItem('cleanerToken', fromHash); history.replaceState(null, '', '/'); }
const token = sessionStorage.getItem('cleanerToken') || '';
const browserClient = crypto.randomUUID();
let browserSequence = 0, browserTimer = null, quitting = false;
async function browserEvent(event) {
  const result = await fetch('/api/browser', {method:'POST', keepalive:true,
    headers:{'X-Cleaner-Token':token,'Content-Type':'application/json'},
    body:JSON.stringify({client_id:browserClient,event,sequence:++browserSequence})});
  if (!result.ok) throw new Error('清理器后台已退出，请重新打开 AI 会话清理器。');
}
function heartbeat() { if(!quitting)browserEvent('heartbeat').catch(()=>{}); }
addEventListener('pageshow',heartbeat);
addEventListener('pagehide',()=>{if(!quitting)browserEvent('close').catch(()=>{});});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)heartbeat();});
let data = null, selected = new Set(), plan = null, busy = false, visible = [], app = 'codex', loadSequence = 0;
const size = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`;
const showError = e => { $('error').textContent = e.message || String(e); $('error').hidden = false; };
async function api(path, body) {
  if(body !== undefined) body = {...body, app};
  else if(path !== 'apps') path += (path.includes('?') ? '&' : '?') + 'app=' + app;
  const result = await fetch('/api/' + path, {method:body === undefined ? 'GET':'POST',headers:{'X-Cleaner-Token':token,'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
  const value = await result.json(); if (!result.ok) throw new Error(value.error || '操作失败'); return value;
}
function node(tag, text, className) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text;if(className)n.className=className;return n; }
function backupChoice(){return {mode:$('backup-mode').value,directory:$('backup-directory').value.trim()};}
function joinPath(root,...parts){const sep=data?.path_separator || '/';return root.replace(/[\\/]+$/,'')+sep+parts.join(sep);}
function backupLocation(choice){return choice.mode==='custom'?joinPath(choice.directory,'AIConversationCleaner',app):choice.directory;}
function updateBackupUI(){
  const choice=backupChoice(), none=choice.mode==='none';
  $('custom-backup').hidden=choice.mode!=='custom';$('backup-note').classList.toggle('warning',none);$('backups').disabled=none;
  $('backup-note').textContent=none?'本次不复制会话或数据库。删除后无法通过本工具恢复；中途失败也无法自动回滚。已有备份不会删除。':
    choice.mode==='custom'?(choice.directory?`备份保存在：${joinPath(backupLocation(choice),'时间戳')}。不会覆盖文件夹内的其他文件。`:'选择文件夹，或直接输入完整路径；不要放入应用的会话数据目录。'):
    `默认备份目录：${data?.backup_root || '加载中'}。每次删除单独保存一份。`;
}
function render() {
  if (!data) return;
  const term=$('search').value.trim().toLowerCase(), filter=$('filter').value;
  visible=data.rows.filter(r => `${r.title} ${r.cwd} ${r.id}`.toLowerCase().includes(term) && (filter==='all' || (filter==='archived' && r.archived) || (filter==='normal' && !r.archived) || (filter==='broken' && r.status!=='正常' && r.status!=='已归档')));
  $('rows').replaceChildren();
  for (const r of visible) {
    const tr=node('tr');tr.classList.toggle('chosen',selected.has(r.id));
    const c=node('td',undefined,'check'), checkbox=node('input');checkbox.type='checkbox';checkbox.checked=selected.has(r.id);checkbox.setAttribute('aria-label','选择 '+r.title);checkbox.disabled=busy;checkbox.addEventListener('change',()=>{checkbox.checked?selected.add(r.id):selected.delete(r.id);render();});c.append(checkbox);tr.append(c);
    const t=node('td');t.append(node('div',r.title,'task-title'),node('div',r.cwd || r.id,'path'));t.title=r.title+'\n'+r.id;tr.append(t);
    const status=node('td');status.append(node('span',r.status,'badge'+(r.archived?' archived':r.status!=='正常'?' bad':'')));tr.append(status);
    const date=r.updated?new Date(r.updated*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';tr.append(node('td',date,'date'));
    tr.append(node('td',`${r.files} 个 · ${size(r.bytes)}`,'right size'));
    const a=node('td'), btn=node('button','详情','text-button');btn.addEventListener('click',()=>detail(r.id));a.append(btn);tr.append(a);$('rows').append(tr);
  }
  $('empty').hidden=visible.length>0;
  $('selected').textContent=selected.size;
  $('shown').textContent=`显示 ${visible.length} / ${data.rows.length}`;
  $('preview').disabled=!selected.size || busy;
  $('select-all').checked=visible.length>0 && visible.every(r=>selected.has(r.id));
  $('select-all').indeterminate=visible.some(r=>selected.has(r.id)) && !$('select-all').checked;
}
async function refresh(preserveError=false) {
  const sequence=++loadSequence;
  $('refresh').disabled=true;if(!preserveError)$('error').hidden=true;
  try {
    const loaded=await api('inventory');if(sequence!==loadSequence)return;data=loaded;
    selected=new Set([...selected].filter(id=>data.rows.some(r=>r.id===id)));
    $('total').textContent=data.rows.length;$('archived').textContent=data.rows.filter(r=>r.archived).length;
    $('broken').textContent=data.rows.filter(r=>r.status!=='正常' && r.status!=='已归档').length;
    $('home').textContent='数据目录：'+data.home;$('demo').hidden=!data.demo;
    $('process-note').textContent=data.processes.length ? `${data.label} 正在运行 · 现在可以查看和选择会话，执行删除前请退出对应应用。` : '对应应用已退出 · 可以执行删除，完成后会核对结果。';
    if(data.warnings.length)$('process-note').textContent+=' '+data.warnings.join('；');
    render();updateBackupUI();
  } catch(e) { showError(e);$('process-note').textContent='读取未完成，请检查数据目录后刷新。'; }
  finally {$('refresh').disabled=false;}
}
async function detail(id) {
  try {
    const d=await api('detail?id='+encodeURIComponent(id));$('detail-title').textContent=d.row.title;
    $('detail-meta').textContent=`ID：${d.row.id}\n项目：${d.row.cwd || '无'}\n状态：${d.row.status}\n会话文件：${d.files.length}\n${d.files.join('\n')}`;
    $('messages').replaceChildren();
    if(!d.messages.length)$('messages').append(node('div',d.row.preview || '没有可读取的消息摘要。仍可选择删除这个任务。','message'));
    for(const m of d.messages){const box=node('div',undefined,'message');box.append(node('b',m.role),document.createTextNode(m.text));$('messages').append(box);}
    $('detail-dialog').showModal();
  }catch(e){showError(e);}
}
$('search').addEventListener('input',render);$('filter').addEventListener('change',render);
$('refresh').addEventListener('click',()=>refresh());
$('select-all').addEventListener('change',()=>{visible.forEach(r=>$('select-all').checked?selected.add(r.id):selected.delete(r.id));render();});
$('clear').addEventListener('click',()=>{selected.clear();render();});
$('backup-mode').addEventListener('change',updateBackupUI);
$('backup-directory').addEventListener('input',updateBackupUI);
$('choose-backup').addEventListener('click',async()=>{
  const button=$('choose-backup');button.disabled=true;button.textContent='请在弹出的窗口选择…';
  try{const result=await api('choose-backup-directory',{initial:$('backup-directory').value.trim()});if(result.directory){$('backup-directory').value=result.directory;updateBackupUI();}}
  catch(e){showError(e);}finally{button.disabled=false;button.textContent='选择文件夹…';}
});
$('preview').addEventListener('click',async()=>{
  $('error').hidden=true;
  try{
    plan=await api('preview',{ids:[...selected],backup:backupChoice()});$('plan-summary').textContent=`将删除 ${plan.rows.length} 个任务、${plan.file_count} 个会话文件（${size(plan.bytes)}）。`;
    const noBackup=plan.backup.mode==='none';
    $('confirm-note').classList.toggle('error',noBackup);
    $('confirm-note').textContent=`请先退出 ${data.label}${app==='codex'?' 桌面端与 Codex CLI':''}。\n`+(noBackup?'不备份，直接删除。误删或中途失败后无法自动恢复；已有备份保留。':`先备份再删除。备份位置：${joinPath(backupLocation(plan.backup),'时间戳')}`);
    $('execute').textContent=noBackup?'直接删除（不备份）':'备份并删除';
    $('plan-list').replaceChildren();for(const r of plan.rows){const li=node('li',r.title);li.append(node('small',r.id));$('plan-list').append(li);}
    $('execute').disabled=false;$('confirm-dialog').showModal();
  }catch(e){showError(e);}
});
$('execute').addEventListener('click',async()=>{
  if(busy)return;busy=true;$('execute').disabled=true;$('execute').textContent=plan.backup.mode==='none'?'正在直接删除…':'正在备份并删除…';$('error').hidden=true;
  try{
    const r=await api('delete',{plan_token:plan.plan_token});
    $('success').textContent=`${data.label} 已删除 ${r.deleted} 个会话，保留 ${r.remaining} 个。验证通过。${app==='codex'?`ChatGPT 目录 ${r.chatgpt_before} → ${r.chatgpt_after}。`:''}${r.backup_dir?'备份：'+r.backup_dir:'本次未创建备份。'}`;$('success').hidden=false;selected.clear();$('confirm-dialog').close();
  }catch(e){$('confirm-dialog').close();showError(e);}
  finally{busy=false;$('execute').textContent='备份并删除';await refresh(true);}
});
$('confirm-dialog').addEventListener('cancel',e=>{if(busy)e.preventDefault();});
$('confirm-dialog').querySelector('form').addEventListener('submit',e=>{if(busy)e.preventDefault();});
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());
$('backups').addEventListener('click',async()=>{try{await api('open-backups',{backup:backupChoice()});}catch(e){showError(e);}});
$('quit').addEventListener('click',async()=>{if(busy || quitting)return;try{const r=await api('quit',{});quitting=true;clearInterval(browserTimer);document.body.replaceChildren(node('div',r.pending?'正在退出：等待当前后台操作完成。可以关闭此页面。':'工具已退出，可以关闭此页面。','notice'));}catch(e){showError(e);}});
async function boot(){
  try{
    await browserEvent('heartbeat');browserTimer=setInterval(heartbeat,15000);
    const apps=await api('apps');if(!apps.some(a=>a.id===app))app=apps[0].id;
    for(const item of apps){const button=node('button',item.label);button.classList.toggle('active',item.id===app);button.addEventListener('click',async()=>{if(busy)return;app=item.id;data=null;selected.clear();$('rows').replaceChildren();$('success').hidden=true;$('preview').disabled=true;$('selected').textContent='0';for(const b of $('apps').children)b.classList.toggle('active',b===button);await refresh();});$('apps').append(button);}
    await refresh();
  }catch(e){showError(e);}
}
boot();
