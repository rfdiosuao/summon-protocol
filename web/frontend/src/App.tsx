import { useEffect, useState } from 'react';
import { ASSETS } from './assets';
import { AGENT_LINK_PROMPT } from './flowCopy';
import {
  fetchAgentDashboardMe, fetchPublicNameplate,
  type AgentDashboardProfile, type Nameplate,
} from './summonApi';
import AgentCodePage from './components/AgentCodePage';
import AgentDashboardPage from './components/AgentDashboardPage';
import AgentLinkPage from './components/AgentLinkPage';
import ChoicePage from './components/ChoicePage';
import HardwarePage from './components/HardwarePage';
import HomePage from './components/HomePage';
import type { HomeEntry } from './components/HomePage';
import NamePage from './components/NamePage';

type View = 'home' | 'choice' | 'name' | 'agentLink' | 'agentCode' | 'dashboard' | 'hardware';

export default function App() {
  const [view, setView] = useState<View>('home');
  const [sceneBg, setSceneBg] = useState<string>(ASSETS.flowBg);
  const [homeEntry, setHomeEntry] = useState<HomeEntry>('fresh');
  const [plateInput, setPlateInput] = useState('');
  const [selectedPlate, setSelectedPlate] = useState<Nameplate | null>(null);
  const [codeSource, setCodeSource] = useState<'prompt' | 'nameplate'>('prompt');
  const [profile, setProfile] = useState<AgentDashboardProfile | null>(null);

  useEffect(() => {
    const plate = new URLSearchParams(window.location.search).get('plate');
    if (plate) {
      void fetchPublicNameplate(plate).then((result) => {
        setSelectedPlate(result);
        setPlateInput(result.code);
        setCodeSource('nameplate');
        setView('agentCode');
      }).catch(() => {
        window.history.replaceState(null, '', '/');
      });
      return;
    }
    void fetchAgentDashboardMe().then((result) => {
      setProfile(result);
      setView('dashboard');
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (view !== 'dashboard') return;
    const timer = window.setInterval(() => {
      void fetchAgentDashboardMe().then(setProfile).catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [view]);

  const backToHome = () => {
    setHomeEntry('return');
    setView('home');
  };

  const prepareNameplate = async () => {
    if (!plateInput.trim()) return;
    try {
      const plate = await fetchPublicNameplate(plateInput.trim());
      setSelectedPlate(plate);
      setCodeSource('nameplate');
      setView('agentCode');
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '找不到这个铭牌');
    }
  };

  if (view === 'choice') {
    return <ChoicePage sceneBg={sceneBg} onBack={backToHome}
      onAgentLink={() => { setSelectedPlate(null); setCodeSource('prompt'); setView('agentLink'); }}
      onWriteToken={() => setView('name')} />;
  }

  if (view === 'name') {
    return <NamePage name={plateInput} sceneBg={sceneBg} onNameChange={setPlateInput}
      onBack={() => setView('choice')} onConfirm={() => void prepareNameplate()} />;
  }

  if (view === 'agentLink') {
    return <AgentLinkPage sceneBg={sceneBg} onBack={() => setView('choice')}
      onCopied={() => setView('agentCode')} prompt={AGENT_LINK_PROMPT} />;
  }

  if (view === 'agentCode') {
    return <AgentCodePage sceneBg={sceneBg} expectedNameplate={selectedPlate?.code}
      agentName={selectedPlate?.agent.name}
      onBack={() => setView(codeSource === 'prompt' ? 'agentLink' : 'name')}
      onVerified={(result) => {
        setProfile(result);
        window.history.replaceState(null, '', '/');
        setView('dashboard');
      }} />;
  }

  if (view === 'dashboard' && profile) {
    return <AgentDashboardPage sceneBg={sceneBg} profile={profile}
      onHome={backToHome} onHardware={() => setView('hardware')} />;
  }

  if (view === 'hardware') {
    return <HardwarePage sceneBg={sceneBg} onBack={() => setView('dashboard')} />;
  }

  return <HomePage entryMode={homeEntry} onStart={(bg) => {
    setSceneBg(bg || ASSETS.flowBg);
    setView('choice');
  }} />;
}
