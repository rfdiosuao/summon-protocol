import { ASSETS } from '../assets';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type WaitPageProps = {
  /** 返回写入页 */
  onBack: () => void;
  /** 真实 NFC 写入正在等待贴卡 */
  statusText?: string;
};

/**
 * 等待写入页：对齐 Figma Frame 5（桌面）与 Frame 19（手机）
 * 对应飞书逻辑：把实体名牌贴近读卡区，完成 NFC 写入。
 */
export default function WaitPage({ onBack, statusText }: WaitPageProps) {
  return (
    <main className="flow flow--wait" data-node-id="18:199">
      <div className="flow__stage">
        <div className="flow__glow flow__glow--2" aria-hidden="true">
          <img src={ASSETS.ellipse2} alt="" />
        </div>
        <div className="flow__glow flow__glow--1" aria-hidden="true">
          <img src={ASSETS.ellipse1} alt="" />
        </div>

        <FlowStatusBar awaiting />

        <button
          type="button"
          className="flow__back"
          aria-label="返回"
          data-node-id="29:1134"
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

        <div className="flow__logo flow__logo--center" data-node-id="18:204">
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

        <p className="flow__heading flow__heading--dark" data-node-id="18:206">
          把实体名牌贴近页面的读卡区
        </p>

        <div className="flow__reader" data-node-id="18:207" aria-hidden="true">
          <img className="flow__reader-ring" src={ASSETS.waitRing} alt="" />
          <img className="flow__reader-bracket flow__reader-bracket--a" src={ASSETS.waitBracket1} alt="" />
          <img className="flow__reader-bracket flow__reader-bracket--b" src={ASSETS.waitBracket2} alt="" />
          <img className="flow__reader-dot" src={ASSETS.waitDot} alt="" />
        </div>

        <div className="flow__loader" data-node-id="34:1954" aria-hidden="true">
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

        <p className="flow__status" data-node-id="34:1953">
          {statusText || '正在等待名牌写入'}
        </p>
      </div>
    </main>
  );
}
