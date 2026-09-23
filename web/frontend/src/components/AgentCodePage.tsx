import { useState, type FormEvent } from 'react';
import { ASSETS } from '../assets';
import { copyText } from '../flowCopy';
import { redeemAgentLoginCode, type AgentDashboardProfile } from '../summonApi';
import AgentOrb from './AgentOrb';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type Props = {
  sceneBg: string;
  expectedNameplate?: string;
  agentName?: string;
  onBack: () => void;
  onVerified: (profile: AgentDashboardProfile) => void;
};

const CODE_PROMPT =
  '请让你当前在线的 SUMMON Agent 使用自己的私有 Agent token 请求 POST https://summon.entermodetwo.com/v1/agent-login/codes，JSON 为 {}，把返回的一次性连接码告诉我；不要发送 Agent token。';

export default function AgentCodePage({ sceneBg, expectedNameplate, agentName, onBack, onVerified }: Props) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      onVerified(await redeemAgentLoginCode(code.trim(), expectedNameplate));
    } catch (err) {
      setError(err instanceof Error ? err.message : '连接码验证失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flow flow--scene flow--connect flow--agent-code">
      <div className="flow__stage">
        <div className="flow__bg flow__bg--blur" aria-hidden="true">
          <img src={sceneBg} alt="" className="flow__bg-image" />
        </div>
        <div className="flow__glow flow__glow--2" aria-hidden="true"><img src={ASSETS.ellipse2} alt="" /></div>
        <div className="flow__glow flow__glow--1" aria-hidden="true"><img src={ASSETS.ellipse1} alt="" /></div>
        <button type="button" className="flow__back" aria-label="返回" onClick={onBack}>
          <img className="flow__back-desktop" src={ASSETS.iconBack} alt="" width={40} height={40} />
          <img className="flow__back-mobile" src={ASSETS.iconBackMobile} alt="" width={24} height={24} />
        </button>
        <div className="flow__logo flow__logo--center">
          <img className="flow__logo-desktop" src={ASSETS.logo} alt="SUMMON" width={60} height={60} />
          <img className="flow__logo-mobile" src={ASSETS.logoMobile} alt="SUMMON" width={40} height={40} />
        </div>
        <FlowStatusBar awaiting />
        <p className="flow__heading flow__heading--dark">输入连接码</p>
        <AgentOrb />
        <form className="flow__code-card" onSubmit={(event) => void submit(event)}>
          {expectedNameplate && <p className="flow__code-target">{agentName || 'Agent'} · {expectedNameplate}</p>}
          <p className="flow__code-help">让 Agent 生成一次性连接码。连接码有效期为 5 分钟。</p>
          <label className="flow__code-label" htmlFor="agent-login-code">连接码</label>
          <input
            id="agent-login-code"
            className="flow__code-input"
            type="password"
            value={code}
            maxLength={11}
            autoComplete="one-time-code"
            autoCapitalize="characters"
            placeholder="XXXXX-XXXXX"
            onChange={(event) => setCode(event.target.value)}
          />
          <button className="flow__code-submit" type="submit" disabled={busy || !code.trim()}>
            {busy ? '正在验证' : '进入我的 Agent 后台'}
          </button>
          <button className="flow__code-copy" type="button" onClick={() => void copyText(CODE_PROMPT).then(setCopied)}>
            {copied ? '已复制给 Agent 的指令' : '复制获取连接码的指令'}
          </button>
          {error && <p className="flow__code-error" role="alert">{error}</p>}
        </form>
      </div>
    </main>
  );
}
