import { ASSETS } from '../assets';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type SuccessPageProps = {
  /** 铭牌上展示的名字 */
  agentName?: string;
  /** 铭牌编号 */
  plateCode?: string;
  /** 返回上一页 */
  onBack: () => void;
  /** 返回主页 */
  onHome: () => void;
};

/**
 * 铭牌展示名：去掉 SUMMON 前缀/片段，避免卡片宽度溢出
 * @param raw 原始 Agent 名
 */
function plateDisplayName(raw: string): string {
  const cleaned = raw
    .replace(/summon/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned || 'Agent';
}

/**
 * 链接成功页：对齐 Figma Frame 6
 * 接入成功后展示 OK 徽标、铭牌卡与返回主页。
 */
export default function SuccessPage({
  agentName = 'Agent',
  plateCode = '001',
  onBack,
  onHome,
}: SuccessPageProps) {
  const displayName = plateDisplayName(agentName);

  return (
    <main className="flow flow--success" data-node-id="24:220">
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
          data-node-id="31:1238"
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

        <div className="flow__logo flow__logo--center" data-node-id="24:223">
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

        <FlowStatusBar />

        <div className="flow__ok-badge" data-node-id="24:248" role="status">
          <span data-node-id="24:247">OK</span>
          <i className="flow__status-dot flow__status-dot--live" aria-hidden="true" />
        </div>

        <article className="flow__plate" data-node-id="36:2135" aria-label={`${displayName} 铭牌`}>
          <div className="flow__plate-flip">
            <img
              className="flow__plate-frame"
              src={ASSETS.nameplateFrame}
              alt=""
              aria-hidden="true"
            />
            <p className="flow__plate-name" data-node-id="67:3146">
              {displayName}
            </p>
            <div className="flow__plate-loader" data-node-id="36:2158" aria-hidden="true">
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
              <span className="flow__loader-col">
                <i /><i /><i /><i />
              </span>
            </div>
            <div className="flow__plate-orb" data-node-id="36:2205">
              <img src={ASSETS.agentOrb} alt="" />
            </div>
            <div className="flow__plate-bars" data-node-id="36:2139" aria-hidden="true">
              <span /><span /><span />
            </div>
            <p className="flow__plate-code" data-node-id="36:2204">
              {plateCode}
            </p>
          </div>
        </article>

        <button
          type="button"
          className="flow__cta flow__cta--home"
          data-node-id="26:845"
          onClick={onHome}
        >
          返回主页
        </button>
      </div>
    </main>
  );
}
