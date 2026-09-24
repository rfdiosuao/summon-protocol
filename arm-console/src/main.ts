import './styles.css';
import { ArmScene, type SafetyReport } from './scene.ts';
import type { ApiErrorBody, ArmState, ConsoleConfig, JointSpec, JointTelemetry } from './types.ts';

const byId = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing element #${id}`);
  return element as T;
};

const canvas = byId<HTMLCanvasElement>('arm-canvas');
const loading = byId<HTMLDivElement>('model-loading');
const loadingLabel = byId<HTMLSpanElement>('model-loading-label');
let config: ConsoleConfig | null = null;
let state: ArmState | null = null;
let socket: WebSocket | null = null;
let retryTimer = 0;
let actionBusy = false;
const pendingTargets = new Map<number, number>();
const stagedTargets = new Set<number>();
const committedTargets = new Set<number>();
let pendingGrip = 60;
let stagedGrip = false;
let committedGrip = false;
let lastCollisionToastAt = 0;
let collisionStopLatched = false;
let lastSafety: SafetyReport = { safe: true, severity: 'safe', violations: [], clearanceMm: 999 };
const viewer = new ArmScene(
  canvas,
  point => { byId('tcp-position').textContent = `X ${point.x.toFixed(3)} · Y ${point.y.toFixed(3)} · Z ${point.z.toFixed(3)} m`; },
  handleHolographicJointInput,
  renderSafety,
);

const errorLabels: Record<string, string> = {
  HARDWARE_NOT_ALLOWED: '后端未使用 --allow-hardware 启动，实机写入保持关闭。',
  MOTOR_NOT_ENABLED: '请先将该伺服上锁。',
  TARGET_OUT_OF_RANGE: '目标超过当前模式允许的安全范围。',
  LOAD_AXIS_RELEASE_BLOCKED: '承重轴尚未回到折叠支撑区，不能直接解锁。',
  WRIST_SUPPORT_REQUIRED: '请先使能 J4 并保持腕部，再移动 J2/J3。',
  WRIST_SUPPORT_RELEASE_BLOCKED: 'J2/J3 正在承重或运动；请先支撑机械臂，再解除 J4 使能。',
  J6_INTERLOCK: 'J6 只有在 J2/J3 上锁并离开折叠止点后才能上锁。',
  STARTUP_SEQUENCE: '折叠态必须先让 J3 上锁并移动到 −5°以下，再上锁 J2。',
  GRIPPER_NOT_CALIBRATED: 'J7 尚未标定电机角与夹爪行程，已阻止实机开合。',
  SERIAL_BUSY: 'COM6 被其他程序占用。',
  FEEDBACK_TIMEOUT: '串口已打开，但电机反馈不完整；请检查通讯连接后重试。',
  HARDWARE_FAULT: '电机存在故障状态，已拒绝指令。',
  ACTIVE_FAULT_LOCK: '存在活动故障，全部使能和运动操作已锁止；请先清除故障。',
  GRAVITY_ACTIVE: '请先退出重力补偿，再执行位置目标。',
  GRAVITY_REQUIRES_ALL_AXES: '重力补偿需要 J1–J6 全部上锁。',
  GRAVITY_UNAVAILABLE: '本机 Pinocchio 动力学模型不可用。',
  GRAVITY_SUSPENDED: '实机 MIT 重力控制已暂停，需先分析超速记录并重新标定。',
  CONFIRMATION_REQUIRED: '切换重力补偿需要现场确认。',
  HIGH_SPEED_CONFIRMATION_REQUIRED: '速度超过 10°/s，本次动作必须再次确认。',
};

async function api<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    init.method = 'POST';
    (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const data = await response.json() as T & ApiErrorBody;
  if (!response.ok) {
    const code = data.error?.code ?? 'REQUEST_FAILED';
    throw new Error(errorLabels[code] ?? data.error?.message ?? code);
  }
  return data;
}

function toast(message: string, kind: 'success' | 'error' | 'info' = 'info'): void {
  const item = document.createElement('div');
  item.className = `toast ${kind}`;
  item.textContent = message;
  byId('toast-region').append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function rangeFill(input: HTMLInputElement): void {
  const min = Number(input.min);
  const max = Number(input.max);
  const value = Number(input.value);
  const percent = ((value - min) / Math.max(0.0001, max - min)) * 100;
  input.style.setProperty('--range-fill', `${percent}%`);
}

function speedAuthorization(action: string): { speedDps: number; highSpeedConfirmed: boolean } | null {
  const speedDps = Number(byId<HTMLInputElement>('global-speed').value);
  if (!Number.isFinite(speedDps) || speedDps < 0.2 || speedDps > 25) throw new Error('速度必须在 0.2–25°/s 范围内。');
  if (speedDps <= 10) return { speedDps, highSpeedConfirmed: false };
  const confirmed = window.confirm(`高风险速度 ${speedDps.toFixed(1)}°/s（超过 10°/s）\n\n即将${action}。请再次确认现场无人、无障碍并可立即急停。`);
  return confirmed ? { speedDps, highSpeedConfirmed: true } : null;
}

function renderSpeedRisk(): void {
  const input = byId<HTMLInputElement>('global-speed');
  const speed = Number(input.value);
  const risky = Number.isFinite(speed) && speed > 10;
  input.classList.toggle('high-risk', risky);
  byId('speed-warning').textContent = risky
    ? `高风险速度 ${speed.toFixed(1)}°/s · 每次执行都需要二次确认`
    : '确认时自动使能，完成后保持扭矩锁定';
  byId('speed-warning').classList.toggle('warning', risky);
}

function specFor(id: number): JointSpec {
  const spec = config?.jointSpecs.find(item => item.id === id);
  if (!spec) throw new Error(`Unknown joint ${id}`);
  return spec;
}

function jointFor(id: number): JointTelemetry | undefined {
  return state?.joints.find(item => item.id === id);
}

function currentAngles(): Record<number, number> {
  return Object.fromEntries((state?.joints ?? []).filter(item => item.id <= 6).map(item => [item.id, item.actualDeg]));
}

function targetAngles(): Record<number, number> {
  return Object.fromEntries((state?.joints ?? []).filter(item => item.id <= 6).map(item => [item.id, pendingTargets.get(item.id) ?? item.targetDeg]));
}

function renderSafety(report: SafetyReport): void {
  lastSafety = report;
  const hud = byId('safety-hud');
  hud.className = `safety-hud ${report.severity}`;
  hud.classList.toggle('clearance-suppressed', report.severity === 'warning');
  byId('safety-hud-state').textContent = report.severity === 'blocked' ? '检测到模型重合' : report.severity === 'warning' ? '碰撞净空不足' : '实时模型无重合';
  byId('safety-hud-detail').textContent = report.violations.join(' · ') || `实体未重合 · 接近预警估算 ${Math.max(0, report.clearanceMm)} mm`;
  const fenceState = byId('fence-state');
  const fenceCard = fenceState.closest('article');
  if (fenceCard) fenceCard.className = `fence-card ${report.severity}`;
  fenceState.textContent = report.severity === 'blocked' ? '碰撞 / 阻止下发' : report.severity === 'warning' ? '净空不足' : '实时状态正常';
  byId('fence-detail').textContent = report.violations.join(' / ') || `实体未重合 · 包围盒净空估算 ${Math.max(0, report.clearanceMm)} mm`;
  const liveCollision = report.severity === 'blocked' && (report.source === 'current' || report.violations.some(item => item.startsWith('实时姿态')));
  if (!liveCollision) collisionStopLatched = false;
  if (liveCollision && !collisionStopLatched && state?.mode === 'hardware' && state.connected && state.joints.some(item => item.enabled && item.moving)) {
    collisionStopLatched = true;
    void api('/api/motion/stop', {})
      .then(() => toast('检测到实时模型重合，所有运动已停止并保持。', 'error'))
      .catch(error => toast(error instanceof Error ? error.message : '碰撞停止指令失败', 'error'));
  }
  updateConfirmButton();
}

function updateConfirmButton(): void {
  const button = byId<HTMLButtonElement>('confirm-targets');
  const changed = [...stagedTargets].filter(id => Math.abs((pendingTargets.get(id) ?? jointFor(id)?.actualDeg ?? 0) - (jointFor(id)?.actualDeg ?? 0)) > 0.05);
  const faultFree = changed.every(id => (jointFor(id)?.statusCode ?? 99) <= 1 && !jointFor(id)?.fault);
  button.disabled = actionBusy || changed.length === 0 || !faultFree || Boolean(state?.fault) || !lastSafety.safe || Boolean(state?.gravityCompensation.active);
  button.textContent = changed.length ? `确认并执行 ${changed.length} 个目标` : '确认并执行目标';
}

function tryJointPreview(id: number, degrees: number, notify: boolean): boolean {
  const candidate = targetAngles();
  candidate[id] = degrees;
  const report = viewer.checkPose(candidate, pendingGrip);
  const card = document.querySelector<HTMLElement>(`.joint-card[data-joint="${id}"]`);
  card?.classList.toggle('collision-blocked', !report.safe);
  if (!report.safe) {
    const now = performance.now();
    if (notify || now - lastCollisionToastAt > 1400) {
      toast(`J${id} 已冻结：${report.violations.join('；')}`, 'error');
      lastCollisionToastAt = now;
    }
    return false;
  }
  card?.classList.remove('collision-blocked');
  return true;
}

function handleHolographicJointInput(id: number, degrees: number, final: boolean): boolean {
  if (!tryJointPreview(id, degrees, final)) return false;
  pendingTargets.set(id, degrees);
  stagedTargets.add(id);
  committedTargets.delete(id);
  const card = document.querySelector<HTMLElement>(`.joint-card[data-joint="${id}"]`);
  const slider = card?.querySelector<HTMLInputElement>('[data-role="slider"]');
  if (slider) {
    slider.value = String(degrees);
    rangeFill(slider);
  }
  const label = card?.querySelector<HTMLElement>('[data-role="target"]');
  if (label) label.textContent = `${degrees.toFixed(1)}°`;
  previewTargets();
  if (final) toast(`J${id} 虚像目标设为 ${degrees.toFixed(1)}°；碰撞预演通过后点击确认。`, 'info');
  updateConfirmButton();
  return true;
}

function assertSafeTrajectory(to: Record<number, number>, gripMm = pendingGrip): void {
  const report = viewer.validateTrajectory(currentAngles(), to, gripMm);
  renderSafety(report);
  if (!report.safe) throw new Error(`模型碰撞边界已阻止：${report.violations.join('；')}`);
}

function jointCard(spec: JointSpec): HTMLElement {
  const article = document.createElement('article');
  article.className = 'joint-card';
  article.dataset.joint = String(spec.id);
  article.innerHTML = `
    <div class="joint-topline">
      <div><span class="joint-index">J${spec.id}</span><strong>${spec.label}</strong><small>${spec.description}</small></div>
      <div class="joint-readout"><small data-role="servo-state">未使能 / 待命</small><span class="joint-value" data-role="value">0.0°</span></div>
    </div>
    <div class="slider-row">
      <span>${Math.round(spec.lowerDeg)}</span>
      <input data-role="slider" aria-label="${spec.label}目标角度" type="range" min="${spec.lowerDeg}" max="${spec.upperDeg}" step="0.1" value="0" />
      <span>${Math.round(spec.upperDeg)}</span>
    </div>
    <div class="joint-meta">
      <span>目标 <b data-role="target">0.0°</b></span>
      <span>扭矩 <b data-role="torque">0.00 Nm</b></span>
      <span>温度 <b data-role="temp">--°C</b></span>
    </div>
    <div class="stress-row"><span>应力反馈</span><i><b data-role="stress-bar"></b></i><strong data-role="stress">0%</strong></div>
    <div class="joint-actions">
      <button data-role="lock" class="lock-button"><span></span>上锁</button>
      <button data-role="apply" class="apply-button">确认此轴</button>
    </div>`;
  const slider = article.querySelector<HTMLInputElement>('[data-role="slider"]')!;
  slider.addEventListener('input', () => {
    const degrees = Number(slider.value);
    if (!tryJointPreview(spec.id, degrees, false)) {
      slider.value = String(pendingTargets.get(spec.id) ?? jointFor(spec.id)?.actualDeg ?? 0);
      rangeFill(slider);
      return;
    }
    pendingTargets.set(spec.id, degrees);
    stagedTargets.add(spec.id);
    committedTargets.delete(spec.id);
    article.querySelector<HTMLElement>('[data-role="target"]')!.textContent = `${degrees.toFixed(1)}°`;
    rangeFill(slider);
    previewTargets();
  });
  slider.addEventListener('change', () => {
    if (article.classList.contains('collision-blocked')) toast(`J${spec.id} 已停在最后一个无重合角度。`, 'error');
  });
  article.querySelector<HTMLElement>('.joint-index')!.addEventListener('click', () => viewer.focusJoint(spec.id));
  article.querySelector<HTMLButtonElement>('[data-role="lock"]')!.addEventListener('click', () => perform(async () => {
    const joint = jointFor(spec.id);
    if (!joint) return;
    if (joint.fault || joint.statusCode > 1) {
      await api(`/api/joints/${spec.id}/clear-fault`, {});
      toast(joint.statusCode > 1
        ? `J${spec.id} 电机故障已确认，请检查实时状态后再操作。`
        : `J${spec.id} 软件保护已确认，电机${joint.enabled ? '仍保持使能' : '仍未使能'}。`, 'success');
      return;
    }
    await api(`/api/joints/${spec.id}/enabled`, {
      enabled: !joint.enabled,
      targetDegrees: joint.actualDeg,
      confirmSupported: byId<HTMLInputElement>('support-confirm').checked,
    });
    toast(joint.enabled ? `J${spec.id} 已解锁` : `J${spec.id} 已使能并保持当前位置`, 'success');
  }));
  article.querySelector<HTMLButtonElement>('[data-role="apply"]')!.addEventListener('click', () => perform(async () => {
    const degrees = pendingTargets.get(spec.id) ?? Number(slider.value);
    const authorization = speedAuthorization(`执行 J${spec.id} → ${degrees.toFixed(1)}°`);
    if (!authorization) return;
    const pose = currentAngles();
    pose[spec.id] = degrees;
    assertSafeTrajectory(pose);
    await api(`/api/joints/${spec.id}/target`, { degrees, ...authorization, autoEnable: true });
    committedTargets.add(spec.id);
    toast(`J${spec.id} 已自动使能并执行 → ${degrees.toFixed(1)}°；到达后保持锁定`, 'success');
  }));
  rangeFill(slider);
  return article;
}

function buildControls(): void {
  if (!config) return;
  const list = byId('joint-list');
  list.replaceChildren(...config.jointSpecs.filter(item => item.id <= 6).map(jointCard));
  const grip = byId<HTMLInputElement>('gripper-slider');
  grip.addEventListener('input', () => {
    pendingGrip = Number(grip.value);
    stagedGrip = true;
    committedGrip = false;
    renderGripper(pendingGrip);
    previewTargets();
  });
  byId<HTMLButtonElement>('gripper-lock').addEventListener('click', () => perform(async () => {
    const joint = jointFor(7);
    if (!joint) return;
    if (joint.fault || joint.statusCode > 1) {
      await api('/api/joints/7/clear-fault', {});
      toast('J7 故障已清除，电机保持未使能', 'success');
      return;
    }
    await api('/api/joints/7/enabled', { enabled: !joint.enabled, targetDegrees: joint.actualDeg, confirmSupported: false });
    toast(joint.enabled ? 'J7 已解锁' : 'J7 已上锁', 'success');
  }));
  byId<HTMLButtonElement>('gripper-apply').addEventListener('click', () => perform(async () => {
    await api('/api/gripper/target', { widthMm: pendingGrip, speedMmS: 12 });
    committedGrip = true;
    toast(`夹爪开合 → ${pendingGrip.toFixed(0)} mm`, 'success');
  }));
  rangeFill(grip);
  byId<HTMLButtonElement>('confirm-targets').addEventListener('click', () => perform(async () => {
    const targets = Object.fromEntries([...stagedTargets]
      .filter(id => Math.abs((pendingTargets.get(id) ?? jointFor(id)?.actualDeg ?? 0) - (jointFor(id)?.actualDeg ?? 0)) > 0.05)
      .map(id => [id, pendingTargets.get(id)!]));
    if (!Object.keys(targets).length) return;
    assertSafeTrajectory(targetAngles());
    const authorization = speedAuthorization(`执行 ${Object.keys(targets).length} 个关节目标`);
    if (!authorization) return;
    await api('/api/joints/targets', { targets, ...authorization, autoEnable: true });
    for (const id of Object.keys(targets).map(Number)) committedTargets.add(id);
    toast(`已自动使能并执行 ${Object.keys(targets).length} 个目标；到达后保持锁定。`, 'success');
  }));
}

function renderGripper(width: number): void {
  byId('gripper-value').textContent = `${width.toFixed(1)} mm`;
  const grip = byId<HTMLInputElement>('gripper-slider');
  if (document.activeElement !== grip) grip.value = String(width);
  rangeFill(grip);
  const edge = 46 - width * 0.34;
  document.querySelector<HTMLElement>('.gripper-visual')?.style.setProperty('--grip-edge', `${edge}%`);
}

function previewTargets(): void {
  if (!state) return;
  viewer.setPreviewTargets(targetAngles(), pendingGrip);
}

function updateJointCard(joint: JointTelemetry): void {
  const card = document.querySelector<HTMLElement>(`.joint-card[data-joint="${joint.id}"]`);
  if (!card) return;
  card.classList.toggle('enabled', joint.enabled);
  card.classList.toggle('fault', Boolean(joint.fault) || joint.statusCode > 1);
  const servoState = card.querySelector<HTMLElement>('[data-role="servo-state"]')!;
  const protectedStop = joint.statusCode <= 1 && Boolean(joint.fault?.startsWith('motion stopped:'));
  servoState.textContent = protectedStop
    ? `保护停机 / ${joint.fault!.replace('motion stopped: no position progress', '位置未前进')}`
    : joint.statusCode > 1 || joint.fault
    ? `故障 / ${joint.fault ?? `CODE ${joint.statusCode}`}`
    : joint.enabled ? '已使能 / 保持' : '未使能 / 待命';
  servoState.className = joint.statusCode > 1 || joint.fault ? 'fault' : joint.enabled ? 'enabled' : '';
  card.querySelector<HTMLElement>('[data-role="value"]')!.textContent = `${joint.actualDeg.toFixed(1)}°`;
  card.querySelector<HTMLElement>('[data-role="torque"]')!.textContent = `${joint.torqueNm.toFixed(2)} Nm`;
  const stress = Math.min(2, Math.max(0, joint.stressRatio));
  card.style.setProperty('--stress', `${Math.min(100, stress * 100)}%`);
  card.classList.toggle('stress-high', stress >= 0.7);
  card.classList.toggle('stress-critical', stress >= 1.0);
  card.querySelector<HTMLElement>('[data-role="stress"]')!.textContent = `${Math.round(stress * 100)}%`;
  card.querySelector<HTMLElement>('[data-role="stress-bar"]')!.style.width = `${Math.min(100, stress * 100)}%`;
  card.querySelector<HTMLElement>('[data-role="torque"]')!.title = `重力模型预测 ${joint.gravityTorqueNm.toFixed(2)} Nm / 额定上限 ${specFor(joint.id).maxTorqueNm.toFixed(1)} Nm`;
  const hottest = Math.max(joint.mosTempC ?? 0, joint.rotorTempC ?? 0);
  card.querySelector<HTMLElement>('[data-role="temp"]')!.textContent = hottest ? `${hottest.toFixed(0)}°C` : '--°C';
  const lock = card.querySelector<HTMLButtonElement>('[data-role="lock"]')!;
  lock.classList.toggle('enabled', joint.enabled);
  const faulted = Boolean(joint.fault) || joint.statusCode > 1;
  lock.innerHTML = `<span></span>${protectedStop ? '确认保护停机' : faulted ? '清除故障' : joint.enabled ? '解除使能 / 解锁' : '使能 / 上锁'}`;
  lock.disabled = actionBusy || (state?.mode === 'hardware' && !state.connected) || (Boolean(state?.fault) && !faulted && !joint.enabled);
  card.querySelector<HTMLButtonElement>('[data-role="apply"]')!.disabled = actionBusy || Boolean(state?.fault) || faulted || Boolean(state?.gravityCompensation.active);

  const slider = card.querySelector<HTMLInputElement>('[data-role="slider"]')!;
  const spec = specFor(joint.id);
  slider.min = String(state?.mode === 'hardware' ? spec.safeLowerDeg : spec.lowerDeg);
  slider.max = String(state?.mode === 'hardware' ? spec.safeUpperDeg : spec.upperDeg);
  if (document.activeElement !== slider) {
    const value = stagedTargets.has(joint.id) ? pendingTargets.get(joint.id)! : joint.actualDeg;
    slider.value = String(Math.min(Number(slider.max), Math.max(Number(slider.min), value)));
    pendingTargets.set(joint.id, Number(slider.value));
    card.querySelector<HTMLElement>('[data-role="target"]')!.textContent = `${Number(slider.value).toFixed(1)}°`;
    rangeFill(slider);
  }
}

function renderState(next: ArmState): void {
  const previousMode = state?.mode;
  state = next;
  viewer.setHardwareLimits(next.mode === 'hardware');
  if (previousMode && previousMode !== next.mode) {
    pendingTargets.clear();
    stagedTargets.clear();
    committedTargets.clear();
    for (const joint of next.joints.filter(item => item.id <= 6)) {
      const spec = specFor(joint.id);
      const lower = next.mode === 'hardware' ? spec.safeLowerDeg : spec.lowerDeg;
      const upper = next.mode === 'hardware' ? spec.safeUpperDeg : spec.upperDeg;
      pendingTargets.set(joint.id, Math.min(upper, Math.max(lower, joint.actualDeg)));
    }
    pendingGrip = next.gripper.widthMm;
    stagedGrip = false;
    committedGrip = false;
  }
  for (const joint of next.joints.filter(item => item.id <= 6)) {
    const pending = pendingTargets.get(joint.id);
    if (stagedTargets.has(joint.id) && committedTargets.has(joint.id) && pending !== undefined && Math.abs(joint.actualDeg - pending) <= 0.2 && !joint.moving) {
      stagedTargets.delete(joint.id);
      committedTargets.delete(joint.id);
    }
    if (!stagedTargets.has(joint.id)) pendingTargets.set(joint.id, joint.actualDeg);
  }
  if (stagedGrip && committedGrip && Math.abs(next.gripper.widthMm - pendingGrip) <= 0.5) {
    stagedGrip = false;
    committedGrip = false;
  }
  if (!stagedGrip) pendingGrip = next.gripper.widthMm;
  const streamDot = byId('stream-dot');
  streamDot.className = `status-dot ${next.fault ? 'fault' : 'online'}`;
  byId('stream-label').textContent = next.connected ? `${next.mode === 'hardware' ? '实机遥测' : '仿真时钟'}已连接` : '控制器未连接';
  byId('mode-label').textContent = next.mode === 'hardware' ? '实机模式' : '仿真模式';
  byId('mode-indicator').className = `mode-indicator ${next.fault ? 'fault' : 'online'}`;
  byId('port-label').textContent = next.mode === 'hardware' ? `${next.port} / ${next.baud}` : 'LOCAL SIM';
  byId('mode-detail').textContent = next.mode === 'hardware'
    ? '确认目标时自动使能，完成后保持扭矩；故障时全部运动锁止。'
    : '关节指令只驱动数字模型，不会写入串口。';
  const hardwareButton = byId<HTMLButtonElement>('hardware-toggle');
  hardwareButton.textContent = next.mode === 'hardware' ? '断开实机' : (next.hardwareAllowed ? `连接 ${next.port} 实机` : '实机写入未授权');
  hardwareButton.disabled = actionBusy || (!next.hardwareAllowed && next.mode !== 'hardware');
  byId<HTMLButtonElement>('resync-button').disabled = actionBusy || next.mode !== 'hardware';

  const enabled = next.joints.filter(item => item.enabled).length;
  const maxStress = Math.max(0, ...next.joints.map(item => item.stressRatio));
  const temps = next.joints.flatMap(item => [item.mosTempC, item.rotorTempC]).filter((value): value is number => value !== null);
  byId('enabled-count').textContent = `${enabled} / 7`;
  byId('max-stress').textContent = `${Math.round(maxStress * 100)}%`;
  byId('max-temp').textContent = temps.length ? `${Math.max(...temps).toFixed(0)} °C` : '-- °C';
  byId('feedback-rate').textContent = `${next.feedbackHz.toFixed(1)} Hz`;
  byId('serial-state').textContent = next.mode === 'hardware' && next.connected ? '已连接' : '未占用';
  byId('serial-detail').textContent = `${next.port} / ${next.baud}`;
  byId('write-state').textContent = next.hardwareAllowed ? '已授权' : '关闭';
  byId('fault-state').textContent = next.fault ? '需要处理' : '正常';
  byId('fault-detail').textContent = next.fault ?? '无活动故障';
  byId('feedback-state').textContent = `${next.feedbackHz.toFixed(1)} Hz`;
  byId('feedback-detail').textContent = `轮询 ${next.feedbackLatencyMs.toFixed(1)} ms · 样本 ${next.sampleAgeMs.toFixed(0)} ms 前`;
  byId('gravity-state').textContent = !next.gravityCompensation.available ? '不可用' : next.gravityCompensation.active ? '运行中' : '待机';
  byId('gravity-detail').textContent = next.gravityCompensation.active
    ? `软切换 ${Math.round(next.gravityCompensation.transitionProgress * 100)}% · 手动拖动实时回传`
    : !next.gravityCompensation.available && next.mode === 'hardware' && next.motionGravityAssist.available
      ? '实机 MIT 手动引导暂停，等待重力参数标定'
    : (next.gravityCompensation.message ?? '全轴 MIT 柔顺控制 · 与轨迹前馈独立');
  const assist = next.motionGravityAssist;
  const assisted = next.joints.filter(joint => Math.abs(joint.gravityFeedforwardNm) > 0.1);
  const wristReady = next.joints.some(joint => joint.id === 4 && joint.enabled && joint.statusCode === 1);
  const upstreamReady = next.joints.some(joint => (joint.id === 2 || joint.id === 3) && joint.enabled);
  byId('motion-assist-state').textContent = !assist.available ? '模型不可用' : !assist.enabled ? '前馈暂停' : upstreamReady && !wristReady ? 'J4 未保持' : assist.active ? '力矩前馈中' : assist.preparedAxes.length ? 'MIT 已就绪' : '待机';
  byId('motion-assist-detail').textContent = !assist.available
    ? '缺少动力学模型，未施加前馈'
    : !assist.enabled
      ? next.faultDiagnostics?.feedforwardInhibited
        ? 'MIT 轴超速后已锁止前馈；需分析故障采样并重新标定'
        : '实机前馈待标定，默认关闭；J2/J3 动作仍先保持 J4'
    : upstreamReady && !wristReady
      ? 'J2/J3 动作前需先使能 J4 保持腕部；当前不施加轨迹前馈'
    : assisted.length
      ? `${assisted.map(joint => `J${joint.id} ${joint.gravityFeedforwardNm.toFixed(2)} Nm`).join(' · ')} · 逐步施加`
      : assist.preparedAxes.length
        ? `已准备 J${assist.preparedAxes.join(' / J')} · 检测到同向负载后渐进施加`
        : `J2/J3/J4 未使能时进入 MIT · 前馈上限 ${assist.maxFeedforwardNm.toFixed(1)} Nm`;
  const gravityButton = byId<HTMLButtonElement>('gravity-toggle');
  const armAxesReady = next.joints.filter(item => item.id <= 6).every(item => item.enabled && item.statusCode === 1 && !item.fault);
  gravityButton.textContent = next.gravityCompensation.active ? '退出手动引导并保持' : armAxesReady ? '开启手动引导' : '手动引导需先上锁 J1–J6';
  gravityButton.disabled = actionBusy || next.mode !== 'hardware' || !next.connected || !next.gravityCompensation.available || (!next.gravityCompensation.active && !armAxesReady);

  for (const joint of next.joints.filter(item => item.id <= 6)) updateJointCard(joint);
  const gripperJoint = next.joints.find(item => item.id === 7);
  if (gripperJoint) {
    const lock = byId<HTMLButtonElement>('gripper-lock');
    lock.classList.toggle('enabled', gripperJoint.enabled);
    lock.innerHTML = `<span></span>${gripperJoint.enabled ? '解除使能 / 解锁 J7' : '使能 / 上锁 J7'}`;
    const gripperFaulted = Boolean(gripperJoint.fault) || gripperJoint.statusCode > 1;
    lock.innerHTML = `<span></span>${gripperFaulted ? '清除故障 J7' : gripperJoint.enabled ? '解除使能 / 解锁 J7' : '使能 / 上锁 J7'}`;
    lock.disabled = actionBusy || (next.mode === 'hardware' && !next.connected) || (Boolean(next.fault) && !gripperFaulted && !gripperJoint.enabled);
    byId<HTMLButtonElement>('gripper-apply').disabled = actionBusy || Boolean(next.fault) || !gripperJoint.enabled || (next.mode === 'hardware' && !next.gripper.calibrated);
  }
  byId('gripper-subtitle').textContent = next.mode === 'hardware'
    ? (next.gripper.calibrated ? `J7 ${next.gripper.motorDegrees.toFixed(1)}°` : '实机传动待标定')
    : '模型开合';
  if (document.activeElement !== byId('gripper-slider')) {
    renderGripper(next.gripper.widthMm);
  }
  byId<HTMLButtonElement>('stop-motion').disabled = actionBusy;
  byId<HTMLButtonElement>('unlock-all').disabled = actionBusy;

  viewer.setJoints(
    next.joints.filter(item => item.id <= 6).map(item => ({
      id: item.id,
      actualDeg: item.actualDeg,
      targetDeg: pendingTargets.get(item.id) ?? item.targetDeg,
      enabled: item.enabled,
    })),
    next.gripper.widthMm,
  );
  viewer.setPreviewTargets(targetAngles(), pendingGrip);
  viewer.setStress(next.joints.filter(item => item.id <= 6).map(item => ({ id: item.id, stressRatio: item.stressRatio })));
  updateConfirmButton();
}

async function perform(action: () => Promise<void>): Promise<void> {
  if (actionBusy) return;
  actionBusy = true;
  if (state) renderState(state);
  try {
    await action();
    renderState(await api<ArmState>('/api/state'));
  } catch (error) {
    toast(error instanceof Error ? error.message : '操作失败', 'error');
  } finally {
    actionBusy = false;
    if (state) renderState(state);
  }
}

function connectSocket(): void {
  if (socket) socket.close();
  window.clearTimeout(retryTimer);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/api/ws`);
  socket.onmessage = event => {
    try { renderState(JSON.parse(event.data) as ArmState); } catch { /* ignore malformed local frame */ }
  };
  socket.onclose = () => {
    byId('stream-dot').className = 'status-dot';
    byId('stream-label').textContent = '正在重连本地控制器';
    retryTimer = window.setTimeout(connectSocket, 1000);
  };
  socket.onerror = () => socket?.close();
}

function bindStaticActions(): void {
  byId<HTMLInputElement>('global-speed').addEventListener('input', renderSpeedRisk);
  renderSpeedRisk();
  document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button => {
    button.addEventListener('click', () => viewer.setView(button.dataset.view as 'iso' | 'front' | 'side' | 'top'));
  });
  byId('fit-view').addEventListener('click', () => viewer.fit());
  byId('risk-toggle').addEventListener('click', () => {
    const visible = viewer.toggleRiskOverlay();
    byId('risk-toggle').classList.toggle('active', visible);
    toast(visible ? '碰撞部件变色已显示' : '碰撞部件变色已隐藏；实时计算仍继续', 'info');
  });
  document.querySelectorAll<HTMLButtonElement>('.panel-tabs button').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.panel-tabs button').forEach(item => item.classList.toggle('active', item === button));
      document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.toggle('active', panel.id === `${button.dataset.tab}-panel`));
    });
  });
  document.querySelectorAll<HTMLButtonElement>('[data-pose]').forEach(button => {
    button.addEventListener('click', () => perform(async () => {
      if (state?.mode === 'hardware') throw new Error('预设姿态只用于仿真，实机必须逐轴执行。');
      const pose = config?.poses[String(button.dataset.pose)];
      if (pose) {
        const targets = Object.fromEntries(Object.entries(pose.joints).map(([id, value]) => [Number(id), value]));
        assertSafeTrajectory(targets, pose.gripperMm);
        for (const [id, degrees] of Object.entries(targets)) {
          pendingTargets.set(Number(id), degrees);
          stagedTargets.add(Number(id));
          committedTargets.add(Number(id));
        }
        pendingGrip = pose.gripperMm;
        stagedGrip = true;
        committedGrip = true;
        previewTargets();
      }
      const authorization = speedAuthorization(`执行“${button.textContent?.trim() || '预设'}”姿态`);
      if (!authorization) return;
      await api('/api/pose', { name: button.dataset.pose, ...authorization });
      document.querySelectorAll('[data-pose]').forEach(item => item.classList.toggle('active', item === button));
      window.setTimeout(() => viewer.fit(), 1200);
    }));
  });
  byId('hardware-toggle').addEventListener('click', () => perform(async () => {
    if (state?.mode === 'hardware') {
      await api('/api/hardware/disconnect', {});
      toast('已断开实机，返回仿真模式', 'success');
    } else {
      await api('/api/hardware/connect', { port: state?.port ?? 'COM6' });
      toast('COM6 已连接；尚未使能任何电机。', 'success');
    }
  }));
  byId('resync-button').addEventListener('click', () => perform(async () => {
    renderState(await api<ArmState>('/api/state?refresh=1'));
    toast('已读取最新实机状态', 'success');
  }));
  byId('stop-motion').addEventListener('click', () => perform(async () => {
    await api('/api/motion/stop', {});
    stagedTargets.clear();
    committedTargets.clear();
    stagedGrip = false;
    committedGrip = false;
    toast('所有轨迹已停止，已上锁关节保持当前位置。', 'success');
  }));
  byId('gravity-toggle').addEventListener('click', () => perform(async () => {
    const active = Boolean(state?.gravityCompensation.active);
    if (!active && !window.confirm('开启手动引导后，J1–J6 会进入柔顺控制，可用手缓慢引导机械臂。确认现场持续有人看护并可急停？')) return;
    await api('/api/gravity-compensation', { enabled: !active, confirmed: !active });
    stagedTargets.clear();
    committedTargets.clear();
    toast(active ? '已退出重力补偿，机械臂保持当前姿态。' : '重力补偿已开启；实像将按实机反馈实时更新。', 'success');
  }));
  byId('unlock-all').addEventListener('click', () => perform(async () => {
    const supported = byId<HTMLInputElement>('support-confirm').checked;
    if (state?.mode === 'hardware' && !supported) throw new Error('实机全轴解锁前必须确认机械臂已受到支撑。');
    if (!window.confirm('解除全部伺服上锁？承重关节可能在重力下移动。')) return;
    await api('/api/joints/disable-all', { confirmSupported: supported });
    toast('全轴已解锁', 'success');
  }));
}

async function start(): Promise<void> {
  bindStaticActions();
  window.setInterval(() => { byId('clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false }); }, 500);
  config = await api<ConsoleConfig>('/api/config');
  viewer.configureJoints(config.jointSpecs);
  buildControls();
  await viewer.load(config.modelUrl, percent => { loadingLabel.textContent = `正在载入官方 URDF · ${Math.round(percent)}%`; });
  loading.classList.add('done');
  loading.style.display = 'none';
  state = await api<ArmState>('/api/state');
  for (const joint of state.joints) pendingTargets.set(joint.id, joint.targetDeg);
  pendingGrip = state.gripper.targetMm;
  renderState(state);
  viewer.fit();
  connectSocket();
}

start().catch(error => {
  loadingLabel.textContent = error instanceof Error ? error.message : '控制台启动失败';
  toast(loadingLabel.textContent, 'error');
});

window.addEventListener('beforeunload', () => {
  socket?.close();
  viewer.dispose();
});
