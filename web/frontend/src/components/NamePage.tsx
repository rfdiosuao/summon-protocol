import { useId } from 'react';
import { ASSETS } from '../assets';
import AgentOrb from './AgentOrb';
import '../styles/flow.css';

type NamePageProps = {
  /** 当前输入的 Agent 名字 */
  name: string;
  /** 流程背景（下拍结束帧） */
  sceneBg: string;
  /** 名字变更 */
  onNameChange: (value: string) => void;
  /** 返回主页 */
  onBack: () => void;
  /** 确认名字 */
  onConfirm: () => void;
};

/**
 * 取名页：对齐 Figma Frame 2（桌面）与 Frame 15（手机）
 * 对应飞书逻辑：给 Agent 一个可寻址的名字，再写入实体名牌。
 */
export default function NamePage({
  name,
  sceneBg,
  onNameChange,
  onBack,
  onConfirm,
}: NamePageProps) {
  const inputId = useId();
  const canConfirm = name.trim().length > 0;

  return (
    <main className="flow flow--name flow--rise-in flow--scene" data-node-id="16:98">
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
          data-node-id="29:1142"
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

        <div className="flow__logo" data-node-id="16:105">
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

        <div className="flow__name-center">
          <p className="flow__heading" data-node-id="16:109">
            给agent取个名字
          </p>

          <AgentOrb nodeId="36:2355" />

          <div className="flow__field" data-node-id="18:198">
            <label className="flow__field-frame" htmlFor={inputId}>
              <span className="flow__field-corner flow__field-corner--tl" aria-hidden="true" />
              <span className="flow__field-corner flow__field-corner--tr" aria-hidden="true" />
              <span className="flow__field-corner flow__field-corner--bl" aria-hidden="true" />
              <span className="flow__field-corner flow__field-corner--br" aria-hidden="true" />
              {!name && (
                <span className="flow__field-caret" aria-hidden="true" />
              )}
              <input
                id={inputId}
                className="flow__field-input"
                type="text"
                value={name}
                maxLength={24}
                autoComplete="off"
                autoFocus
                placeholder=""
                onChange={(event) => onNameChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && canConfirm) {
                    onConfirm();
                  }
                }}
              />
            </label>
          </div>

          <button
            type="button"
            className="flow__cta"
            data-node-id="16:99"
            disabled={!canConfirm}
            onClick={onConfirm}
          >
            确定名字
          </button>
        </div>
      </div>
    </main>
  );
}
