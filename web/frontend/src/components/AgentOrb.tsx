import { useEffect, useRef } from 'react';
import { ASSETS } from '../assets';

type AgentOrbProps = {
  /** 可选节点标注（Figma） */
  nodeId?: string;
  /** 球体尺寸：默认 / 等待页大号 */
  size?: 'md' | 'lg';
};

/** 近距感应半径相对球体尺寸的倍数 */
const PROXIMITY_SCALE = 1.35;
/** 朝鼠标方向最大位移（px） */
const MAX_PULL = 30;
/** 跟随阻尼（越大越黏） */
const FOLLOW = 0.14;
/** 回弹阻尼 */
const RETURN = 0.1;

/**
 * Agent 球体：呼吸浮动 + 鼠标靠近时朝指针方向轻拉约 30px，离开后回弹。
 */
export default function AgentOrb({ nodeId, size = 'md' }: AgentOrbProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const floatRef = useRef<HTMLDivElement>(null);
  const sizeClass = size === 'lg' ? ' flow__orb--lg' : '';

  useEffect(() => {
    const root = rootRef.current;
    const floatEl = floatRef.current;
    if (!root || !floatEl) return;

    /** 是否具备精确指针（跳过触控） */
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    let enabled = finePointer.matches;

    let raf = 0;
    let pointerX = 0;
    let pointerY = 0;
    let hasPointer = false;
    let currentX = 0;
    let currentY = 0;
    let targetX = 0;
    let targetY = 0;

    /**
     * 根据指针与球体中心的关系更新目标位移。
     */
    const updateTarget = () => {
      if (!enabled || !hasPointer) {
        targetX = 0;
        targetY = 0;
        return;
      }

      const rect = root.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const dx = pointerX - cx;
      const dy = pointerY - cy;
      const dist = Math.hypot(dx, dy);
      const radius = Math.max(rect.width, rect.height) * PROXIMITY_SCALE;

      if (dist < radius && dist > 0.5) {
        const strength = 1 - dist / radius;
        const pull = MAX_PULL * strength;
        targetX = (dx / dist) * pull;
        targetY = (dy / dist) * pull;
      } else {
        targetX = 0;
        targetY = 0;
      }
    };

    /**
     * 每帧插值并写入 transform。
     */
    const tick = () => {
      updateTarget();
      const easing =
        Math.abs(targetX) + Math.abs(targetY) > 0.01 ? FOLLOW : RETURN;
      currentX += (targetX - currentX) * easing;
      currentY += (targetY - currentY) * easing;

      if (Math.abs(currentX) < 0.05 && Math.abs(targetX) === 0) currentX = 0;
      if (Math.abs(currentY) < 0.05 && Math.abs(targetY) === 0) currentY = 0;

      floatEl.style.transform = `translate(${currentX.toFixed(2)}px, ${currentY.toFixed(2)}px)`;
      raf = requestAnimationFrame(tick);
    };

    /**
     * @param event 指针移动事件
     */
    const onPointerMove = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      hasPointer = true;
    };

    const onPointerLeave = () => {
      hasPointer = false;
      targetX = 0;
      targetY = 0;
    };

    /**
     * @param event media query 变更
     */
    const onFineChange = (event: MediaQueryListEvent) => {
      enabled = event.matches;
      if (!enabled) {
        hasPointer = false;
        targetX = 0;
        targetY = 0;
      }
    };

    window.addEventListener('pointermove', onPointerMove, { passive: true });
    document.documentElement.addEventListener('pointerleave', onPointerLeave);
    finePointer.addEventListener('change', onFineChange);
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('pointermove', onPointerMove);
      document.documentElement.removeEventListener('pointerleave', onPointerLeave);
      finePointer.removeEventListener('change', onFineChange);
      floatEl.style.transform = '';
    };
  }, []);

  return (
    <div
      ref={rootRef}
      className={`flow__orb${sizeClass}`}
      data-node-id={nodeId}
      aria-hidden="true"
    >
      <div ref={floatRef} className="flow__orb-magnet">
        <img src={ASSETS.agentOrb} alt="" />
      </div>
    </div>
  );
}
