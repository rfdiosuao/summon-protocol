import { useEffect, useId, useRef, useState } from 'react';
import {
  getOverallLinkState,
  type ConnectionStatus,
  type LinkState,
} from '../connectionStatus';
import { useSummonStatus } from '../SummonStatusContext';
import '../styles/flow.css';

type FlowStatusBarProps = {
  /**
   * 覆盖实时快照；默认使用全局 Hub catalog。
   */
  status?: ConnectionStatus;
  /** 外观：流程页浅底 / 主页深底 */
  tone?: 'flow' | 'home';
  /**
   * 等待接入中：未在线项按「连接中」白点呼吸展示
   */
  awaiting?: boolean;
};

/**
 * @param state 连接态
 * @returns 状态点 className
 */
function dotClass(state: LinkState): string {
  if (state === 'online') return 'flow__status-dot flow__status-dot--live';
  if (state === 'connecting') {
    return 'flow__status-dot flow__status-dot--breathing';
  }
  if (state === 'unknown') return 'flow__status-dot flow__status-dot--unknown';
  return 'flow__status-dot';
}

/**
 * 等待接入时，未在线 → 连接中（呼吸）
 * @param state 原始连接态
 * @param awaiting 是否处于等待接入
 */
function resolveState(state: LinkState, awaiting: boolean): LinkState {
  if (!awaiting) return state;
  if (state === 'online') return 'online';
  return 'connecting';
}

/**
 * 顶部设备链接状态（对齐 summon-protocol 星图计数）：
 * - 桌面：两枚胶囊常显
 * - 手机：右上角圆点按钮，点击展开明细
 * - 连接中白点呼吸；在线黄点
 */
export default function FlowStatusBar({
  status: statusProp,
  tone = 'flow',
  awaiting = false,
}: FlowStatusBarProps) {
  const live = useSummonStatus();
  const snap = statusProp ?? live.status;
  const agentsState = resolveState(snap.agentsState, awaiting);
  const clientState = resolveState(snap.clientState, awaiting);
  const agentsLabel = `${snap.agentCount} Agent · ${snap.deviceCount}设备`;
  const overall = getOverallLinkState({
    ...snap,
    agentsState,
    clientState,
  });
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;

    /**
     * @param event 指针事件：点外部关闭
     */
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (root && !root.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    /**
     * @param event 键盘：Esc 关闭
     */
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const toneClass = tone === 'home' ? ' flow__status-bar--home' : '';
  const openClass = open ? ' is-open' : '';

  return (
    <div
      ref={rootRef}
      className={`flow__status-bar${toneClass}${openClass}`}
      data-node-id="54:2759"
    >
      <button
        type="button"
        className="flow__status-trigger"
        data-node-id="64:3025"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`设备链接状态，${overall === 'online' ? '已连接' : overall === 'connecting' ? '连接中' : '未连接'}`}
        onClick={() => setOpen((v) => !v)}
      >
        <i className={dotClass(overall)} aria-hidden="true" />
      </button>

      <div
        id={panelId}
        className="flow__status-panel"
        data-node-id="64:3016"
        role="status"
        aria-label={`连接状态：${agentsLabel}，电脑客户端 ${clientState}`}
      >
        <div className="flow__status-pill" data-node-id="54:2763">
          <span>电脑客户端</span>
          <i className={dotClass(clientState)} aria-hidden="true" />
        </div>
        <div className="flow__status-pill" data-node-id="54:2759">
          <span>{agentsLabel}</span>
          <i className={dotClass(agentsState)} aria-hidden="true" />
        </div>
      </div>

      <div className="flow__status-desktop">
        <div className="flow__status-pill" data-node-id="57:2933">
          <span>{agentsLabel}</span>
          <i className={dotClass(agentsState)} aria-hidden="true" />
        </div>
        <div className="flow__status-pill" data-node-id="57:2936">
          <span>电脑客户端</span>
          <i className={dotClass(clientState)} aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}
