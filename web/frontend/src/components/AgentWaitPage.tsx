import { useEffect, useRef, useState } from 'react';
import { ASSETS } from '../assets';
import { useSummonStatus } from '../SummonStatusContext';
import { fetchOnboardingStatus } from '../summonApi';
import AgentOrb from './AgentOrb';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type AgentWaitPageProps = {
  /** 返回上一页 */
  onBack: () => void;
  /**
   * 接入成功（网络就绪）后进入链接成功页
   * @param agentName 展示用 Agent 名
   */
  onSuccess: (agentName: string, nameplate: string) => void;
  token: string;
  /** 底部解析状态文案；无内容时用实时目录生成 */
  statusText?: string;
};

/**
 * 等待 Agent 接入页：对齐 Figma Frame 30 + summon-protocol 等待接入提示
 * 仅当前接入会话被该 Agent 认领并完成在线握手后进入成功页。
 */
export default function AgentWaitPage({
  onBack,
  onSuccess,
  token,
  statusText,
}: AgentWaitPageProps) {
  const { status, error } = useSummonStatus();
  const [joinState, setJoinState] = useState('等待当前 Agent 认领接入会话');
  const firedRef = useRef(false);
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const result = await fetchOnboardingStatus(token);
        if (cancelled || firedRef.current) return;
        if (result.state === 'ONLINE' && result.agent && result.nameplate) {
          firedRef.current = true;
          onSuccessRef.current(result.agent.name, result.nameplate);
        } else {
          setJoinState(result.state === 'CLAIMED'
            ? `已认领 ${result.agent?.name || 'Agent'} · 等待在线握手`
            : '等待当前 Agent 认领接入会话');
        }
      } catch (err) {
        if (!cancelled) setJoinState(err instanceof Error ? err.message : '接入状态暂不可用');
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [token]);

  const liveLine =
    statusText?.trim() ||
    (error ? `◉ 节点目录暂不可用 · ${error}` :
      `◉ ${joinState} · 网络中 ${status.agentCount} Agent / ${status.deviceCount} 设备`);

  return (
    <main className="flow flow--scene flow--agent-wait" data-node-id="57:2818">
      <div className="flow__stage">
        <div className="flow__glow flow__glow--2" aria-hidden="true">
          <img src={ASSETS.ellipse2} alt="" />
        </div>
        <div className="flow__glow flow__glow--1" aria-hidden="true">
          <img src={ASSETS.ellipse1} alt="" />
        </div>

        <button
          type="button"
          className="flow__back"
          aria-label="返回"
          data-node-id="57:2824"
          onClick={onBack}
        >
          <img
            className="flow__back-desktop"
            src={ASSETS.iconBack}
            alt=""
            width={40}
            height={40}
          />
          <img
            className="flow__back-mobile"
            src={ASSETS.iconBackMobile}
            alt=""
            width={24}
            height={24}
          />
        </button>

        <div className="flow__logo flow__logo--center" data-node-id="57:2821">
          <img
            className="flow__logo-desktop"
            src={ASSETS.logo}
            alt="SUMMON"
            width={60}
            height={60}
          />
          <img
            className="flow__logo-mobile"
            src={ASSETS.logoMobile}
            alt="SUMMON"
            width={40}
            height={40}
          />
        </div>

        <FlowStatusBar awaiting />

        <p className="flow__heading flow__heading--dark" data-node-id="57:2823">
          等待agent接入.....
        </p>

        <AgentOrb nodeId="57:2827" size="lg" />

        <div className="flow__loader flow__loader--dark" data-node-id="57:2828" aria-hidden="true">
          <span className="flow__loader-col">
            <i /><i /><i /><i />
          </span>
          <span className="flow__loader-col">
            <i /><i /><i /><i />
          </span>
          <span className="flow__loader-col">
            <i /><i /><i /><i />
          </span>
          <span className="flow__loader-col">
            <i /><i /><i /><i />
          </span>
        </div>

        {liveLine ? (
          <p className="flow__status flow__status--mono" data-node-id="57:2849">
            {liveLine}
          </p>
        ) : null}
      </div>
    </main>
  );
}
