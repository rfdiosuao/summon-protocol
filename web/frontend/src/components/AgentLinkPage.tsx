import { ASSETS } from '../assets';
import { copyText } from '../flowCopy';
import AgentOrb from './AgentOrb';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type AgentLinkPageProps = {
  /** 流程背景 */
  sceneBg: string;
  /** 返回选择页 */
  onBack: () => void;
  /** 复制成功后进入下一段复制内容 */
  onCopied: () => void;
  prompt: string;
};

/**
 * 自身 Agent 链接页：对齐 Figma Frame 31
 * 复制适配器接入指令，再进入硬件 skill 复制页。
 */
export default function AgentLinkPage({
  sceneBg,
  onBack,
  onCopied,
  prompt,
}: AgentLinkPageProps) {
  /**
   * 复制接入指令并进入下一段
   */
  const handleCopy = async () => {
    const ok = await copyText(prompt);
    if (ok) {
      onCopied();
    }
  };

  return (
    <main className="flow flow--scene flow--connect" data-node-id="57:2856">
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
          data-node-id="57:2876"
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

        <div className="flow__logo flow__logo--center" data-node-id="57:2873">
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

        <p className="flow__heading flow__heading--dark" data-node-id="57:2875">
          把你的 Agent 接进来
        </p>

        <AgentOrb nodeId="57:2879" />

        <div className="flow__connect-stack">
          <p className="flow__hint" data-node-id="57:2866">
            复制下面这段话，交给能读取网页和编写代码的 Agent。
            {' '}
            <a
              className="flow__textlink"
              href={ASSETS.agentGuide}
              target="_blank"
              rel="noopener noreferrer"
            >
              阅读接入说明
            </a>
          </p>

          <div className="flow__prompt" data-node-id="57:2860">
            <span className="flow__field-corner flow__field-corner--tl" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--tr" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--bl" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--br" aria-hidden="true" />
            <p className="flow__prompt-text" data-node-id="57:2867">
              {prompt}
            </p>
          </div>

          <p className="flow__footnote" data-node-id="57:2868">
            当前为邀请制开发接入，需要实现适配器。控制台访问码与 Agent
            注册凭证不同；复制指令不会自动注册或控制设备。
          </p>

          <button
            type="button"
            className="flow__cta flow__cta--copy"
            data-node-id="57:2869"
            onClick={handleCopy}
          >
            复制接入指令
          </button>
        </div>
      </div>
    </main>
  );
}
