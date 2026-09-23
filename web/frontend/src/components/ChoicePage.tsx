import { ASSETS } from '../assets';
import AgentOrb from './AgentOrb';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type ChoicePageProps = {
  /** 流程背景 */
  sceneBg: string;
  /** 返回主页 */
  onBack: () => void;
  /** 进入 Agent 与硬件提示词接入 */
  onAgentLink: () => void;
  /** 查找已有 Agent 并进入 NFC 名牌链路 */
  onWriteToken: () => void;
};

/**
 * 功能选择页：对齐 Figma Frame 33
 * 首页入口后的功能选择：提示词接入硬件与 Agent / 查找已有 Agent
 */
export default function ChoicePage({
  sceneBg,
  onBack,
  onAgentLink,
  onWriteToken,
}: ChoicePageProps) {
  return (
    <main className="flow flow--scene flow--choice flow--rise-in" data-node-id="57:2917">
      <div className="flow__stage">
        <div className="flow__bg flow__bg--blur" aria-hidden="true">
          <img src={sceneBg} alt="" className="flow__bg-image" />
        </div>

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
          data-node-id="57:2929"
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

        <div className="flow__logo flow__logo--center" data-node-id="57:2927">
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

        <AgentOrb nodeId="57:2932" />

        <div className="flow__choice-actions">
          <button
            type="button"
            className="flow__cta flow__cta--choice"
            data-node-id="57:2921"
            onClick={onAgentLink}
          >
            提示词接入硬件与 Agent
          </button>
          <button
            type="button"
            className="flow__cta flow__cta--choice"
            data-node-id="57:2923"
            onClick={onWriteToken}
          >
            找到自己的 Agent
          </button>
        </div>
      </div>
    </main>
  );
}
