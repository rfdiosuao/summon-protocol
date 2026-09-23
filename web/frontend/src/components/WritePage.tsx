import { ASSETS } from '../assets';
import AgentOrb from './AgentOrb';
import '../styles/flow.css';

type WritePageProps = {
  /** Agent 名字 */
  name: string;
  /** 流程背景（下拍结束帧） */
  sceneBg: string;
  /** 返回取名页 */
  onBack: () => void;
  /** 写入名牌 */
  onWrite: () => void;
};

/**
 * 写入名牌页：对齐 Figma Frame 4（桌面）与 Frame 18（手机）
 * 对应飞书逻辑：把名字写入 NFC 实体名牌，再贴到机壳胸口完成入住。
 */
export default function WritePage({ name, sceneBg, onBack, onWrite }: WritePageProps) {
  const displayName = name.trim() || 'Agent';

  return (
    <main className="flow flow--scene" data-node-id="18:182">
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
          data-node-id="29:1138"
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

        <div className="flow__logo flow__logo--center" data-node-id="18:187">
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

        <p className="flow__heading flow__heading--desktop" data-node-id="18:189">
          把自己的 Agent 写进 NFC
        </p>

        <AgentOrb nodeId="36:2357" />

        <p className="flow__confirm" data-node-id="29:1066">
          {`确定将 「${displayName}」 作为agent名字并写入名牌？`}
        </p>

        <button
          type="button"
          className="flow__cta flow__cta--write"
          data-node-id="18:183"
          onClick={onWrite}
        >
          写入名牌
        </button>
      </div>
    </main>
  );
}
