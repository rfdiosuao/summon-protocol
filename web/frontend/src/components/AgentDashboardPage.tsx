import { ASSETS } from '../assets';
import type { AgentDashboardProfile } from '../summonApi';
import FlowStatusBar from './FlowStatusBar';
import '../styles/flow.css';

type Props = {
  sceneBg: string;
  profile: AgentDashboardProfile;
  onHome: () => void;
  onHardware: () => void;
};

export default function AgentDashboardPage({ sceneBg, profile, onHome, onHardware }: Props) {
  const online = profile.agent.status === 'ONLINE' || profile.agent.status === 'BUSY';

  return (
    <main className="flow flow--scene flow--dashboard">
      <div className="flow__stage">
        <div className="flow__bg flow__bg--blur" aria-hidden="true">
          <img src={sceneBg} alt="" className="flow__bg-image" />
        </div>
        <div className="flow__glow flow__glow--2" aria-hidden="true"><img src={ASSETS.ellipse2} alt="" /></div>
        <div className="flow__glow flow__glow--1" aria-hidden="true"><img src={ASSETS.ellipse1} alt="" /></div>
        <button type="button" className="flow__back" aria-label="返回主页" onClick={onHome}>
          <img className="flow__back-desktop" src={ASSETS.iconBack} alt="" width={40} height={40} />
          <img className="flow__back-mobile" src={ASSETS.iconBackMobile} alt="" width={24} height={24} />
        </button>
        <div className="flow__logo flow__logo--center">
          <img className="flow__logo-desktop" src={ASSETS.logo} alt="SUMMON" width={60} height={60} />
          <img className="flow__logo-mobile" src={ASSETS.logoMobile} alt="SUMMON" width={40} height={40} />
        </div>
        <FlowStatusBar />
        <section className="flow__dashboard-panel" aria-label="我的 Agent">
          <p className="flow__dashboard-label">我的 Agent</p>
          <h1 className="flow__dashboard-name">{profile.agent.name}</h1>
          <p className="flow__dashboard-code">{profile.nameplate}</p>
          <p className="flow__dashboard-state">
            <span className={online ? 'flow__dashboard-dot is-online' : 'flow__dashboard-dot'} aria-hidden="true" />
            {online ? '云端在线' : '当前离线'}
          </p>
          <button type="button" className="flow__dashboard-action" onClick={onHardware}>
            配置硬件
          </button>
          <p className="flow__dashboard-note">配置硬件时，再把硬件接入提示词交给能操作该设备的 Agent。</p>
        </section>
      </div>
    </main>
  );
}
