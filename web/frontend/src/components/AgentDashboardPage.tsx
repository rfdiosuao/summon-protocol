import { useState } from 'react';
import { ASSETS } from '../assets';
import type { AgentDashboardProfile } from '../summonApi';
import FlowStatusBar from './FlowStatusBar';
import StarMapScene3D from './StarMapScene3D';
import '../styles/flow.css';

type Props = {
  sceneBg: string;
  profile: AgentDashboardProfile;
  onHome: () => void;
  onHardware: () => void;
};

const isOnline = (state: string) => ['ONLINE', 'IDLE', 'ACTIVE', 'BUSY'].includes(state);

export default function AgentDashboardPage({ sceneBg, profile, onHome, onHardware }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const online = isOnline(profile.agent.status);
  const devices = profile.devices ?? [];
  const selected = devices.find((device) => device.shell_id === selectedId);

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
        <section className="flow__starmap" aria-label="我的 Agent 星图">
          <header className="flow__starmap-header">
            <div>
              <p className="flow__starmap-eyebrow">MY CONSTELLATION <span> / 我的星图</span></p>
              <h1>一个 Agent，连接你的设备。</h1>
              <p className="flow__starmap-intro">每颗星是一台设备。连接线呈现它与当前 Agent 的关系，亮起的节点表示设备在线。</p>
            </div>
            <div className="flow__starmap-identity">
              <span className={online ? 'flow__dashboard-dot is-online' : 'flow__dashboard-dot'} aria-hidden="true" />
              <span>{online ? 'Agent 在线' : 'Agent 离线'}</span>
              <strong>{profile.nameplate}</strong>
            </div>
          </header>
          <StarMapScene3D agentName={profile.agent.name} devices={devices} online={online}
            selectedId={selectedId} onSelect={setSelectedId} />
          <footer className="flow__starmap-footer">
            <div className="flow__starmap-detail" aria-live="polite">
              {selected ? <>
                <strong>{selected.label}</strong>
                <span>{selected.session_state ? `交接中 · ${selected.session_state}` : isOnline(selected.state) ? '设备在线' : '设备离线'} · {selected.authorized ? '已授权' : '需要配对授权'}</span>
                {selected.capabilities.length > 0 && <small>可调用：{selected.capabilities.join(' · ')}</small>}
              </> : <>
                <strong>{devices.length} 颗设备星</strong>
                <span>默认显示电脑；完成新设备配对后，它会出现在这里。</span>
              </>}
            </div>
            <button type="button" className="flow__starmap-add" onClick={onHardware}>＋ 配置硬件</button>
          </footer>
          {devices.length > 8 && <div className="flow__starmap-more" aria-label="更多已适配设备">
            {devices.slice(8).map((device) => <button key={device.shell_id} type="button"
              className={selectedId === device.shell_id ? 'is-selected' : ''}
              onClick={() => setSelectedId(device.shell_id)}>
              <span aria-hidden="true">✦</span> {device.label} · {isOnline(device.state) ? '在线' : '离线'}
            </button>)}
          </div>}
        </section>
      </div>
    </main>
  );
}
