'use strict';
const $ = id => document.getElementById(id);
const requestId = () => crypto.randomUUID().replaceAll('-', '');
let busy = false;

async function api(path, body) {
  const options = {credentials: 'same-origin'};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type': 'application/json'};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || value.error?.code || `HTTP ${response.status}`);
  return value;
}

function showLogin() {
  $('login').hidden = false;
  $('workspace').hidden = true;
}

function showWorkspace() {
  $('login').hidden = true;
  $('workspace').hidden = false;
}

function renderStatus(value) {
  $('model').textContent = `LIVE · ${value.model}`;
  $('hardware').textContent = value.fault ? `故障 · ${value.fault}` :
    value.connected ? `COM6 已连接 · 反馈 ${Math.round(value.sample_age_ms || 0)} ms` : '未连接';
  $('dot').classList.toggle('off', !value.connected || Boolean(value.fault));
  $('joints').replaceChildren();
  for (const joint of value.joints.filter(item => item.id <= 6)) {
    const row = document.createElement('div');
    row.className = 'joint';
    const axis = document.createElement('b'); axis.textContent = `J${joint.id}`;
    const angle = document.createElement('span'); angle.textContent = `${Number(joint.actualDeg).toFixed(2)}°`;
    const state = document.createElement('small');
    state.textContent = `${joint.enabled ? '保持' : '未使能'} · 应力 ${Math.round((joint.stressRatio || 0) * 100)}%`;
    row.append(axis, angle, state); $('joints').append(row);
  }
  $('presets').replaceChildren();
  for (const gesture of value.gestures) {
    const row = document.createElement('div'); row.className = 'preset';
    const title = document.createElement('strong'); title.textContent = `${gesture.name} · ${gesture.speed_dps}°/s`;
    const description = document.createElement('small'); description.textContent = gesture.description;
    row.append(title, description); $('presets').append(row);
  }
  const windows = Object.entries(value.motion_windows || {});
  $('windows').textContent = windows.length ?
    `当前开放：${windows.map(([axis, [low, high]]) => `${axis} ${low}°～${high}°`).join('，')}` :
    '当前未开放模型自由规划动作。';
}

function renderTrace(items) {
  const box = $('trace');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  box.replaceChildren();
  for (const item of items.slice(-40)) {
    const row = document.createElement('div'); row.className = `event ${item.role || 'system'}`;
    const time = document.createElement('div'); time.className = 'time';
    time.textContent = `${item.at || ''} · ${{user: '用户', agent: 'Agent', system: '系统'}[item.role] || '系统'}`;
    const message = document.createElement('div'); message.className = 'text'; message.textContent = item.text || '';
    row.append(time, message); box.append(row);
  }
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

async function refresh() {
  if ($('workspace').hidden) return;
  try {
    const [status, trace] = await Promise.all([api('/demo/status'), api('/demo/trace')]);
    renderStatus(status); renderTrace(trace.items);
  } catch (error) {
    $('notice').textContent = `状态读取失败：${error.message}`;
  }
}

async function activeSession() {
  const state = await api('/v1/state');
  const existing = state.sessions.find(item => item.shell_id === 'arm_demo' &&
    ['ACTIVE', 'CONNECTING', 'RELEASING'].includes(item.state));
  if (existing?.state === 'ACTIVE') return existing;
  if (existing) throw new Error('上一会话正在连接或释放，请稍后重试');
  const catalog = await api('/v1/catalog');
  const agent = catalog.agents.find(item => item.capabilities.includes('arm.gesture'));
  if (!agent) throw new Error('机械臂 Agent 尚未上线');
  const created = await api('/v1/sessions', {request_id: requestId(), agent_id: agent.agent_id, shell_id: 'arm_demo'});
  for (let i = 0; i < 20; i++) {
    await new Promise(resolve => setTimeout(resolve, 250));
    const snapshot = await api('/v1/state');
    const session = snapshot.sessions.find(item => item.session_id === created.session.session_id);
    if (session?.state === 'ACTIVE') return session;
    if (session && ['FAILED', 'RELEASED'].includes(session.state)) break;
  }
  throw new Error('Agent 与机械臂尚未完成会话接入');
}

$('loginButton').onclick = async () => {
  try {
    await api('/v1/operator-session', {access_code: $('access').value});
    $('access').value = ''; showWorkspace(); await refresh();
  } catch (error) { $('loginMessage').textContent = error.message; }
};

$('send').onclick = async () => {
  if (busy || !$('prompt').value.trim()) return;
  busy = true; $('send').disabled = true; $('notice').textContent = '正在连接 Agent 并提交请求…';
  try {
    const session = await activeSession();
    await api(`/v1/sessions/${session.session_id}/inputs`,
      {request_id: requestId(), text: $('prompt').value.trim()});
    $('notice').textContent = '请求已提交；等待实机观测、模型规划、Gateway 校验和控制器回执。';
    $('prompt').value = '';
  } catch (error) { $('notice').textContent = `未提交动作：${error.message}`; }
  finally { busy = false; $('send').disabled = false; }
};

$('release').onclick = async () => {
  if (busy) return;
  try {
    const state = await api('/v1/state');
    const session = state.sessions.find(item => item.shell_id === 'arm_demo' &&
      ['ACTIVE', 'CONNECTING'].includes(item.state));
    if (!session) { $('notice').textContent = '当前没有待释放的会话。'; return; }
    await api(`/v1/sessions/${session.session_id}/release`, {request_id: requestId()});
    $('notice').textContent = '会话释放中，Gateway 正在确认停止保持。';
  } catch (error) { $('notice').textContent = error.message; }
};

api('/demo/status').then(() => {showWorkspace(); refresh();}).catch(() => showLogin());
setInterval(refresh, 700);
