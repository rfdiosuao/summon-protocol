'use strict';
const $=id=>document.getElementById(id);
$('copyAgentPrompt').onclick=async()=>{try{await navigator.clipboard.writeText($('agentPrompt').textContent);$('copyAgentStatus').textContent='已复制，把这段话粘贴给你的 Agent 即可。'}catch{const selection=window.getSelection(),range=document.createRange();range.selectNodeContents($('agentPrompt'));selection.removeAllRanges();selection.addRange(range);$('copyAgentStatus').textContent='浏览器未允许复制，已选中指令，请手动复制。'}};
$('copyDevicePrompt').onclick=async()=>{try{await navigator.clipboard.writeText($('devicePrompt').textContent);$('copyDeviceStatus').textContent='已复制。把提示词交给能操作硬件所在电脑的 Agent。'}catch{const selection=window.getSelection(),range=document.createRange();range.selectNodeContents($('devicePrompt'));selection.removeAllRanges();selection.addRange(range);$('copyDeviceStatus').textContent='浏览器未允许复制，已选中提示词，请手动复制。'}};
let state=null,session=null,stream=null,connected=false,busy=false,memoryVersion=0,taskBusy=false;
const labels={OFFLINE:'离线',IDLE:'就绪',CONNECTING:'正在接入',ACTIVE:'已接入',RELEASING:'正在释放',RELEASED:'已释放',FAILED:'失败',FAULT:'待核对',ESTOP:'已急停',ONLINE:'在线',BUSY:'使用中'};
const errors={UNAUTHORIZED:'访问码无效或登录已过期',FORBIDDEN:'没有操作权限，请检查入口或访问码',AGENT_BUSY:'Agent 正在使用另一台设备',SHELL_BUSY:'设备正在使用中',SESSION_NOT_ACTIVE:'会话已结束，请重新连接',MEMORY_CONFLICT:'偏好版本已变化，请刷新后再保存',TASK_BUSY:'上一个任务还未结束',SHELL_OFFLINE:'设备离线',AGENT_OFFLINE:'Agent 离线',HANDOFF_BLOCKED:'旧设备尚未确认停止，交接已阻止',RATE_LIMITED:'请求过于频繁，请稍后再试'};
function notice(text){$('notice').textContent=text}
async function api(path,body){const options={credentials:'same-origin'};if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body)}const r=await fetch(path,options);const result=await r.json();if(!r.ok){if(r.status===401){$('console').hidden=true;$('login').hidden=false;connected=false;controls()}throw new Error(errors[result.error?.code]||result.error?.code||'请求失败')}return result}
function rid(){return crypto.randomUUID().replaceAll('-','')}
function controls(){const active=connected&&!busy&&session?.state==='ACTIVE';for(const id of ['send','remember','handoff'])$(id).disabled=!active||(id==='send'&&taskBusy);$('release').disabled=!connected||busy||!session||!['ACTIVE','CONNECTING'].includes(session.state);document.querySelectorAll('[data-connect]').forEach(b=>b.disabled=!connected||busy||Boolean(session)||b.dataset.ready!=='true')}
function render(){if(!state)return;const chosen=$('agent').value;$('agent').replaceChildren();for(const a of state.agents){const o=document.createElement('option');o.value=a.agent_id;o.textContent=a.name+' · '+(labels[a.status]||a.status);$('agent').append(o)}if(state.agents.some(a=>a.agent_id===chosen))$('agent').value=chosen;$('agentInfo').textContent=state.agents.find(a=>a.agent_id===$('agent').value)?.bio||'还没有 Agent 接入';
session=[...state.sessions].reverse().find(s=>['ACTIVE','CONNECTING','RELEASING'].includes(s.state))||null;
$('shells').replaceChildren();for(const s of state.shells){const box=document.createElement('div');box.className='shell';const info=document.createElement('div');const title=document.createElement('b');title.textContent=s.label;const detail=document.createElement('small');detail.textContent=(labels[s.state]||s.state)+' · '+s.shell_id;info.append(title,detail);const b=document.createElement('button');b.textContent=session?.shell_id===s.shell_id?'当前设备':'连接';b.dataset.connect=s.shell_id;b.dataset.ready=String(s.state==='IDLE'&&s.enabled);b.onclick=()=>action(async()=>{await api('/v1/sessions',{request_id:rid(),agent_id:$('agent').value,shell_id:s.shell_id});notice('正在等待 Agent 和设备确认接入')});box.append(info,b);$('shells').append(box)}
$('session').textContent=session?(labels[session.state]||session.state)+' · '+(state.shells.find(s=>s.shell_id===session.shell_id)?.label||session.shell_id):'尚未连接设备';
if(session){memoryVersion=session.memory_version;$('memory').textContent='记忆版本 '+memoryVersion+' · 保存后交接仍然有效'}
const latest=state.commands[state.commands.length-1];if(latest){$('resultStatus').textContent=latest.outcome.status;$('output').textContent=latest.outcome.result||latest.outcome.error?.error?.code||'等待设备回执';taskBusy=['ACCEPTED','EXECUTING'].includes(latest.outcome.status)}
renderNetwork(state);controls();lease();}

function renderNetwork(data){
const map=$('networkMap');map.replaceChildren();
const agents=data.agents||[],shells=data.shells||[];
$('networkCount').textContent=agents.length+' 个 Agent · '+shells.length+' 台设备';
function group(items,kind){const col=document.createElement('div');col.className='node-group';for(const item of items){const b=document.createElement('button');b.className='network-node secondary';const status=item.status||item.state;b.dataset.online=String(!['OFFLINE','FAULT','ESTOP'].includes(status));const name=item.name||item.label;b.textContent=name+' · '+(labels[status]||status);b.onclick=()=>{$('nodeDetail').textContent=kind+' / '+name+' / '+(item.agent_id||item.shell_id)+' / 能力：'+(item.capabilities||[]).join('、');map.querySelectorAll('button').forEach(n=>n.setAttribute('aria-pressed',String(n===b)))};b.setAttribute('aria-pressed','false');col.append(b)}if(!items.length){const p=document.createElement('p');p.className='sub';p.textContent='暂无'+kind;col.append(p)}return col}
const hub=document.createElement('div');hub.className='hub-node';hub.textContent='SUMMON Hub';map.append(group(agents,'Agent'),hub,group(shells,'设备'));
if(!$('nodeDetail').dataset.initialized){$('nodeDetail').textContent='点击一个节点，查看其身份与声明能力。';$('nodeDetail').dataset.initialized='true'}
}
let experienceData=null,experienceRequest=0;
function renderExperience(){
if(!experienceData)return;
const device=$('experienceDevice').value,capability=$('experienceCapability').value;
const matches=x=>(!device||x.shell_id===device)&&(!capability||x.capability===capability);
const groups=experienceData.groups.filter(matches),items=experienceData.items.filter(matches);
const label=id=>state.shells.find(s=>s.shell_id===id)?.label||id;
$('experienceCount').textContent=groups.reduce((n,g)=>n+g.total,0)+' 条证据 · Agent 已检索 '+experienceData.retrievals+' 次';
$('experienceNote').textContent=(state.mode==='SIMULATED'?'模拟联调经验，不代表实物执行。':'经验来自设备网关回执。')+'已持久保存；按同设备、同版本与能力提供统计建议。检索次数不等于成功复用次数。';
const summary=$('experienceSummary');summary.replaceChildren();
for(const g of groups){const p=document.createElement('article');p.className='experience-card';const title=document.createElement('h3');title.textContent=label(g.shell_id)+' / '+g.capability;const tag=document.createElement('p');tag.className='sub';tag.textContent=(g.mode==='SIMULATED'?'模拟':'网关回报')+' · 固件 '+g.profile.firmware;const numbers=document.createElement('p');numbers.textContent=g.completed+' 完成 / '+g.failed+' 失败 / '+g.unknown+' 未知';const detail=document.createElement('p');detail.className='sub';detail.textContent='平均回执耗时 '+g.average_duration_ms+' ms · '+g.total+' 次观测';const guidance=document.createElement('p');guidance.textContent=g.guidance;p.append(title,tag,numbers,detail,guidance);summary.append(p)}
const list=$('experienceList');list.replaceChildren();if(!items.length){const p=document.createElement('p');p.className='sub';p.textContent='暂无符合条件的经验。新版上线后完成一次设备任务即可积累；旧日志不会补造版本和证据。';list.append(p)}
for(const c of items.slice(0,12)){const p=document.createElement('p');p.className='experience-row';p.textContent=label(c.shell_id)+' · '+c.capability+' · '+({COMPLETED:'已完成',FAILED:'失败',UNKNOWN:'结果未知'}[c.status])+' · '+c.duration_ms+' ms';const id=document.createElement('small');id.textContent=new Date(c.created_at).toLocaleString()+' / '+c.source+' / '+c.evidence+' / '+c.command_id;p.append(id);list.append(p)}
}
async function loadExperiences(){const seq=++experienceRequest;try{const data=await api('/v1/experiences');if(seq!==experienceRequest)return;experienceData=data;for(const [id,key,title] of [['experienceDevice','shell_id','全部设备'],['experienceCapability','capability','全部能力']]){const select=$(id),selected=select.value;select.replaceChildren(new Option(title,''));for(const value of [...new Set(data.groups.map(g=>g[key]))])select.add(new Option(key==='shell_id'?(state.shells.find(s=>s.shell_id===value)?.label||value):value,value));select.value=[...select.options].some(o=>o.value===selected)?selected:''}renderExperience()}catch(e){$('experienceNote').textContent='经验库暂不可用：'+e.message}}
$('experienceDevice').onchange=renderExperience;$('experienceCapability').onchange=renderExperience;
function lease(){ $('lease').textContent=session?'本次授权剩余 '+Math.max(0,Math.ceil((Date.parse(session.expires_at)-Date.now())/1000))+' 秒；到期需重新连接。':'';}
async function snapshot(){state=await api('/v1/state');$('console').hidden=false;$('login').hidden=true;render();await loadExperiences()}
async function connect(){if(stream)stream.close();connected=false;controls();await snapshot();stream=new EventSource('/v1/events?after='+encodeURIComponent(state.cursor));stream.onopen=()=>{connected=true;$('connection').textContent='实时连接';controls()};stream.onmessage=async event=>{const data=JSON.parse(event.data);if(data.type==='stream.reset'){stream.close();setTimeout(()=>connect().catch(e=>notice(e.message)),500);return}const li=document.createElement('li');li.textContent=new Date(data.at).toLocaleTimeString()+' · '+data.type;$('events').prepend(li);while($('events').children.length>40)$('events').lastChild.remove();if(data.type==='input.finished')taskBusy=false;try{await snapshot()}catch(e){notice(e.message)}};stream.onerror=()=>{connected=false;$('connection').textContent='连接中断';controls();stream.close();setTimeout(()=>connect().catch(e=>notice(e.message)),2000)}}
async function action(fn){if(busy)return;busy=true;controls();try{await fn();await snapshot()}catch(e){notice(e.message)}finally{busy=false;controls()}}
$('loginForm').onsubmit=e=>{e.preventDefault();action(async()=>{await api('/v1/operator-session',{access_code:$('access').value});$('access').value='';notice('已进入控制台');await connect()})};
$('inputForm').onsubmit=e=>{e.preventDefault();if(!session)return;action(async()=>{await api('/v1/sessions/'+session.session_id+'/inputs',{request_id:rid(),text:$('input').value});taskBusy=true;notice('已提交，等待执行结果')})};
$('remember').onclick=()=>action(async()=>{const m=await api('/v1/sessions/'+session.session_id+'/feedback',{request_id:rid(),update_id:rid(),expected_version:memoryVersion,patch:{response_style:$('preference').value}});memoryVersion=m.memory_version;notice('偏好已保存，记忆版本 '+m.memory_version)});
$('release').onclick=()=>action(async()=>{await api('/v1/sessions/'+session.session_id+'/release',{request_id:rid()});notice('正在等待设备停止确认')});
$('handoff').onclick=()=>action(async()=>{const target=state.shells.find(s=>s.shell_id!==session.shell_id&&s.state==='IDLE'&&s.enabled);if(!target)throw new Error('没有就绪的目标设备');await api('/v1/sessions/'+session.session_id+'/handoff',{request_id:rid(),target_shell_id:target.shell_id});notice('正在停止旧设备，再接入 '+target.label)});
$('refresh').onclick=()=>action(connect);$('agent').onchange=render;setInterval(lease,1000);
fetch('/healthz').then(r=>r.json()).then(h=>{$('mode').textContent=h.mode;$('modeNote').textContent=h.mode==='SIMULATED'?'当前为模拟联调：响应来自规则程序，未连接大模型或实物。':'实时服务：实际能力以在线 Agent 和设备为准。'}).catch(()=>notice('无法连接服务器'));
async function enterDemoLink(){
  const fragment=new URLSearchParams(location.hash.slice(1));
  const code=fragment.get('demo-access');
  if(!code){connect().catch(()=>{$('connection').textContent='等待登录';controls()});return}
  // The code is only in the fragment, so it is not sent in an HTTP URL or referrer.
  // Remove it from browser history before making the normal operator login request.
  history.replaceState(null,'',location.pathname+location.search);
  try{
    await api('/v1/operator-session',{access_code:code});
    await connect();
    $('console').scrollIntoView({block:'start'});
    notice('演示控制台已登录：先连接笔记本显示屏，再交接到机械臂。');
  }catch(e){$('connection').textContent='登录失败';$('login').scrollIntoView();notice(e.message)}
}
enterDemoLink();
document.querySelector('nav a[href="#experience"]').addEventListener('click',e=>{if($('console').hidden){e.preventDefault();$('login').scrollIntoView();$('access').focus();notice('登录后查看你有权访问的设备经验')}});
async function publicNetwork(){try{const r=await fetch('/v1/catalog');if(!r.ok)throw new Error();const data=await r.json();if(!state)renderNetwork(data)}catch{if(!state)$('networkCount').textContent='节点数据暂不可用'}}
publicNetwork();setInterval(()=>{if(!state)publicNetwork()},15000);
