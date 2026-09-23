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

      <a
        className="flow__github-link"
        href="https://github.com/rfdiosuao/summon-protocol"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="在 GitHub 查看 SUMMON 仓库"
        title="GitHub 仓库"
      >
        <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82A7.65 7.65 0 0 1 8 4.17c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
        </svg>
        <span>GitHub</span>
      </a>
    </div>
  );
}
