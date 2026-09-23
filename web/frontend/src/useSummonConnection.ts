import { useEffect, useState } from 'react';
import {
  EMPTY_CONNECTION_STATUS,
  type ConnectionStatus,
  type LinkState,
} from './connectionStatus';
import {
  fetchCatalog,
  isConnectingStatus,
  isPcClientShell,
  type CatalogResponse,
} from './summonApi';

/** 目录轮询间隔（毫秒） */
const POLL_MS = 8000;

/**
 * @param status Agent 状态
 * @returns 是否已在线可用（不含连接中）
 */
function agentOnline(status: string): boolean {
  return status === 'ONLINE' || status === 'BUSY';
}

/**
 * @param state 设备状态
 * @returns 是否离线
 */
function shellDown(state: string): boolean {
  return ['OFFLINE', 'FAULT', 'ESTOP'].includes(state);
}

/**
 * 将 Hub catalog 映射为顶部状态栏数据
 * @param catalog 公开目录
 * @returns 连接快照
 */
export function catalogToConnectionStatus(
  catalog: CatalogResponse,
): ConnectionStatus {
  const agents = catalog.agents ?? [];
  const shells = catalog.shells ?? [];
  const onlineAgents = agents.filter((a) => agentOnline(a.status));
  const agentsConnecting = agents.some((a) => isConnectingStatus(a.status));

  let agentsState: LinkState = 'offline';
  if (onlineAgents.length > 0) agentsState = 'online';
  else if (agentsConnecting) agentsState = 'connecting';

  const pc = shells.find(isPcClientShell);
  let clientState: LinkState = 'offline';
  if (pc) {
    if (isConnectingStatus(pc.state)) clientState = 'connecting';
    else if (!shellDown(pc.state)) clientState = 'online';
  } else if (shells.some((s) => isConnectingStatus(s.state))) {
    clientState = 'connecting';
  }

  return {
    agentCount: agents.length,
    deviceCount: shells.length,
    agentsState,
    clientState,
  };
}

/**
 * 轮询 Hub 公开目录，驱动顶部链接状态
 * @param enabled 是否轮询
 * @returns 连接快照与原始目录
 */
export function useSummonConnection(enabled = true): {
  status: ConnectionStatus;
  catalog: CatalogResponse | null;
  error: string | null;
} {
  const [status, setStatus] = useState<ConnectionStatus>(EMPTY_CONNECTION_STATUS);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timer = 0;

    /**
     * 拉一次目录并更新状态
     */
    const tick = async () => {
      try {
        const data = await fetchCatalog();
        if (cancelled) return;
        setCatalog(data);
        setStatus(catalogToConnectionStatus(data));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'catalog failed');
      }
    };

    void tick();
    timer = window.setInterval(() => {
      void tick();
    }, POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled]);

  return { status, catalog, error };
}
