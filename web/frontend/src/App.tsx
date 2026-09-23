import { useEffect, useRef, useState } from 'react';
import { ASSETS } from './assets';
import { AGENT_LINK_PROMPT } from './flowCopy';
import {
  ApiError, createOnboardingIntent, fetchNameplates, fetchPublicNameplate,
  loginOperator, type Nameplate,
} from './summonApi';
import AgentLinkPage from './components/AgentLinkPage';
import AgentWaitPage from './components/AgentWaitPage';
import ChoicePage from './components/ChoicePage';
import HardwarePage from './components/HardwarePage';
import HomePage from './components/HomePage';
import type { HomeEntry } from './components/HomePage';
import NamePage from './components/NamePage';
import SuccessPage from './components/SuccessPage';
import WaitPage from './components/WaitPage';
import WritePage from './components/WritePage';

/**
 * 官网视图：
 * 主页 → 功能选择
 *   ├─ 提示词接入硬件与 Agent → 复制接入指令 → 等待接入 → 链接成功
 *   └─ 找到自己的 Agent → 查询铭牌 → 写入名牌 → NFC 贴卡等待 → 链接成功
 */
type View =
  | 'home'
  | 'choice'
  | 'name'
  | 'write'
  | 'wait'
  | 'agentLink'
  | 'hardware'
  | 'agentWait'
  | 'success';

/**
 * 唤名官方站入口
 * 首页入口先进功能选择，再按分支分流。
 */
export default function App() {
  const [view, setView] = useState<View>('home');
  const [agentName, setAgentName] = useState('');
  const [plateCode, setPlateCode] = useState('001');
  const [intentToken, setIntentToken] = useState('');
  const [selectedPlate, setSelectedPlate] = useState<Nameplate | null>(null);
  const [writeStatus, setWriteStatus] = useState('正在等待名牌写入');
  const writeAbort = useRef<AbortController | null>(null);
  const [successBack, setSuccessBack] = useState<View>('agentWait');
  const [sceneBg, setSceneBg] = useState<string>(ASSETS.flowBg);
  const [homeEntry, setHomeEntry] = useState<HomeEntry>('fresh');

  /**
   * 回主页：倒放下拍，不重播开场
   */
  const backToHome = () => {
    setHomeEntry('return');
    setView('home');
  };

  /**
   * 进入链接成功页（Frame 6）
   * @param name 铭牌展示名
   * @param back 成功页返回目标
   */
  const goSuccess = (name: string, code: string, back: View) => {
    setAgentName(name.trim() || 'Agent');
    setPlateCode(code);
    setSuccessBack(back);
    setView('success');
  };

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('plate');
    if (!code) return;
    void fetchPublicNameplate(code).then((plate) => {
      goSuccess(plate.agent.name, plate.code, 'home');
    }).catch(() => {
      window.history.replaceState(null, '', '/');
    });
  // Only resolve a scanned tag on initial page load.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const beginAgentLink = async () => {
    try {
      const intent = await createOnboardingIntent();
      setIntentToken(intent.token);
      setView('agentLink');
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '暂时无法创建接入会话');
    }
  };

  const prepareNameplate = async () => {
    if (!agentName.trim()) return;
    try {
      let directory;
      try {
        directory = await fetchNameplates();
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 401) throw err;
        const code = window.prompt('请输入团队访问码，以读取已有 Agent 的铭牌');
        if (!code) return;
        await loginOperator(code);
        directory = await fetchNameplates();
      }
      const input = agentName.trim().toLocaleLowerCase();
      const matches = directory.items.filter((plate) =>
        plate.code.toLocaleLowerCase() === input ||
        plate.agent.name.trim().toLocaleLowerCase() === input);
      if (matches.length !== 1) {
        window.alert(matches.length ? '找到多个同名 Agent，请输入完整铭牌号。' :
          '未找到这个已注册的 Agent。请先完成 Agent 接入，再输入它的名字或铭牌号。');
        return;
      }
      setSelectedPlate(matches[0]);
      setView('write');
    } catch (err) {
      window.alert(err instanceof Error ? err.message : '铭牌查询失败');
    }
  };

  const writeNameplate = () => {
    if (!selectedPlate) return;
    type NfcWriter = { write: (message: object, options?: { signal: AbortSignal }) => Promise<void> };
    const NFC = (window as unknown as { NDEFReader?: new () => NfcWriter }).NDEFReader;
    if (!NFC) {
      window.alert('此浏览器不支持 Web NFC 写卡。请使用支持 Web NFC 的 Android Chrome 手机。');
      return;
    }
    const abort = new AbortController();
    writeAbort.current = abort;
    const code = selectedPlate.code;
    const target = new URL(`/?plate=${encodeURIComponent(code)}`, window.location.origin).href;
    // Call write directly from the click gesture; it waits for a physical tag.
    let operation: Promise<void>;
    try {
      operation = new NFC().write({ records: [
        { recordType: 'url', data: target },
        { recordType: 'text', data: code },
      ] }, { signal: abort.signal });
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'NFC 写入未能启动');
      return;
    }
    setWriteStatus('请把 NFC 名牌贴近手机读卡区');
    setView('wait');
    void operation.then(() => {
      if (abort.signal.aborted) return;
      writeAbort.current = null;
      goSuccess(selectedPlate.agent.name, code, 'wait');
    }).catch((err) => {
      if (abort.signal.aborted) return;
      writeAbort.current = null;
      setView('write');
      window.alert(err instanceof Error ? `NFC 写入失败：${err.message}` : 'NFC 写入失败');
    });
  };

  if (view === 'choice') {
    return (
      <ChoicePage
        sceneBg={sceneBg}
        onBack={backToHome}
        onAgentLink={() => void beginAgentLink()}
        onWriteToken={() => setView('name')}
      />
    );
  }

  if (view === 'name') {
    return (
      <NamePage
        name={agentName}
        sceneBg={sceneBg}
        onNameChange={setAgentName}
        onBack={() => setView('choice')}
        onConfirm={() => void prepareNameplate()}
      />
    );
  }

  if (view === 'write') {
    return (
      <WritePage
        name={agentName}
        sceneBg={sceneBg}
        onBack={() => setView('name')}
        onWrite={writeNameplate}
      />
    );
  }

  if (view === 'wait') {
    return (
      <WaitPage
        onBack={() => { writeAbort.current?.abort(); writeAbort.current = null; setView('write'); }}
        statusText={writeStatus}
      />
    );
  }

  if (view === 'agentLink') {
    return (
      <AgentLinkPage
        sceneBg={sceneBg}
        onBack={() => setView('choice')}
        onCopied={() => setView('hardware')}
        prompt={`${AGENT_LINK_PROMPT}\n\n完成注册和在线握手后，用你自己的 Agent token（不要发给网页）请求 POST https://summon.entermodetwo.com/v1/onboarding/claim，Authorization: Bearer <agent_token>，JSON: {"token":"${intentToken}"}。认领成功后，这个网页才会显示你真实的铭牌。接入会话 30 分钟内有效。`}
      />
    );
  }

  if (view === 'hardware') {
    return (
      <HardwarePage
        sceneBg={sceneBg}
        onBack={() => setView('agentLink')}
        onCopied={() => setView('agentWait')}
      />
    );
  }

  if (view === 'agentWait') {
    return (
      <AgentWaitPage
        onBack={() => setView('hardware')}
        token={intentToken}
        onSuccess={(name, code) => goSuccess(name, code, 'agentWait')}
      />
    );
  }

  if (view === 'success') {
    return (
      <SuccessPage
        agentName={agentName}
        plateCode={plateCode}
        onBack={() => setView(successBack)}
        onHome={backToHome}
      />
    );
  }

  return (
    <HomePage
      entryMode={homeEntry}
      onStart={(bg) => {
        setSceneBg(bg || ASSETS.flowBg);
        setView('choice');
      }}
    />
  );
}
