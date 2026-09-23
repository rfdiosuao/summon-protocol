import { useEffect, useState } from 'react';
import { ASSETS } from '../assets';
import { HARDWARE_TOKEN_PROMPT, copyText } from '../flowCopy';
import AgentOrb from './AgentOrb';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type HardwarePageProps = {
  /** 流程背景 */
  sceneBg: string;
  /** 返回上一页 */
  onBack: () => void;
  /** 复制成功后进入下一页（等待 agent 接入） */
  onCopied?: () => void;
};

/**
 * 硬件接入指令页：对齐 Figma Frame 32
 * 复制 skill.md 提示词；在 agent 链路中为第二段复制内容。
 */
export default function HardwarePage({
  sceneBg,
  onBack,
  onCopied,
}: HardwarePageProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => {
      setCopied(false);
      onCopied?.();
    }, onCopied ? 900 : 4200);
    return () => window.clearTimeout(timer);
  }, [copied, onCopied]);

  /**
   * 复制硬件接入指令并显示 Toast
   */
  const handleCopy = async () => {
    const ok = await copyText(HARDWARE_TOKEN_PROMPT);
    if (ok) {
      setCopied(true);
    }
  };

  return (
    <main className="flow flow--scene flow--connect" data-node-id="57:2886">
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
          data-node-id="57:2907"
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

        <div className="flow__logo flow__logo--center" data-node-id="57:2904">
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

        <p className="flow__heading flow__heading--dark" data-node-id="57:2906">
          让你的硬件成为下一具身体
        </p>

        <AgentOrb nodeId="57:2910" />

        <div className="flow__connect-stack">
          <p className="flow__hint flow__hint--hardware" data-node-id="57:2898">
            把下面这句话发给你的 Agent。它会读取接入指南，先评估硬件，再开发适配器或固件。
            {' '}
            <a
              className="flow__textlink"
              href={ASSETS.skillGuide}
              target="_blank"
              rel="noopener noreferrer"
            >
              在线阅读 Skill ↗
            </a>
          </p>

          <div className="flow__prompt flow__prompt--compact" data-node-id="57:2890">
            <span className="flow__field-corner flow__field-corner--tl" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--tr" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--bl" aria-hidden="true" />
            <span className="flow__field-corner flow__field-corner--br" aria-hidden="true" />
            <p className="flow__prompt-text" data-node-id="57:2899">
              {HARDWARE_TOKEN_PROMPT}
            </p>
          </div>

          <p className="flow__footnote">
            设备客户端已有配对码？{' '}
            <a className="flow__textlink" href="/assets/device.html">打开设备授权</a>
          </p>

          <button
            type="button"
            className="flow__cta flow__cta--copy"
            data-node-id="57:2900"
            onClick={handleCopy}
          >
            复制接入指令
          </button>

          {copied && (
            <div className="flow__toast" data-node-id="57:2896" role="status">
              已复制。把提示词交给能操作硬件所在电脑的 Agent
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
