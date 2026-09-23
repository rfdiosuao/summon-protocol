import { useState } from 'react';
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

const isOnline = (state: string) => ['ONLINE', 'IDLE', 'ACTIVE', 'BUSY'].includes(state);

export default function AgentDashboardPage({ sceneBg, profile, onHome, onHardware }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const online = isOnline(profile.agent.status);
  const devices = profile.devices ?? [];
  const hardwareAngles = [-135, 135, 180, -90, 90, -60, 60];
  const visible = devices.slice(0, 8).map((device, index) => {
    const angle = index === 0 ? 0 : hardwareAngles[index - 1];
    const radians = angle * Math.PI / 180;
    return { ...device, x: 50 + 33 * Math.cos(radians), y: 50 + 34 * Math.sin(radians) };
  });
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
          <div className="flow__starmap-canvas" aria-label={`${profile.agent.name} 已适配 ${Math.max(0, devices.length - 1)} 台其他设备`}>
            <div className="flow__starmap-orbit flow__starmap-orbit--inner" aria-hidden="true" />
            <div className="flow__starmap-orbit flow__starmap-orbit--outer" aria-hidden="true" />
            <svg className="flow__starmap-lines" viewBox="0 0 1000 480" preserveAspectRatio="none" aria-hidden="true">
              {visible.map((device) => <line key={device.shell_id} x1="500" y1="240"
                x2={device.x * 10} y2={device.y * 4.8}
                className={isOnline(device.state) && device.authorized ? 'is-active' : ''} />)}
            </svg>
            <div className={online ? 'flow__starmap-core is-online' : 'flow__starmap-core'}>
              <span className="flow__starmap-core-glow" aria-hidden="true">✦</span>
              <strong>{profile.agent.name}</strong>
              <small>AGENT · {online ? 'ONLINE' : 'OFFLINE'}</small>
            </div>
            {visible.map((device) => <button key={device.shell_id} type="button"
              className={`flow__starmap-node ${isOnline(device.state) ? 'is-online' : ''} ${selectedId === device.shell_id ? 'is-selected' : ''}`}
              style={{ left: `${device.x}%`, top: `${device.y}%` }}
              onClick={() => setSelectedId(device.shell_id)}
              aria-label={`${device.label}，${isOnline(device.state) ? '在线' : '离线'}，${device.authorized ? '已授权' : '待授权'}`}>
              <span className="flow__starmap-node-star" aria-hidden="true">{device.kind === 'computer' ? '⌘' : '✦'}</span>
              <span className="flow__starmap-node-label">{device.label}</span>
              <span className="flow__starmap-node-state">{isOnline(device.state) ? '在线' : '离线'} · {device.authorized ? '已授权' : '待授权'}</span>
            </button>)}
          </div>
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
          {devices.length > 8 && <p className="flow__starmap-overflow">另有 {devices.length - 8} 台设备已适配。</p>}
        </section>
      </div>
    </main>
  );
}
