/**
 * SUMMON Hub 公开接口（对齐 summon-protocol web/app.js）
 * 开发环境经 Vite 代理；生产同域或配置 VITE_SUMMON_ORIGIN。
 */

const ORIGIN = (import.meta.env.VITE_SUMMON_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? '';

export type CatalogAgent = {
  agent_id: string;
  name: string;
  bio?: string;
  status: string;
  capabilities?: string[];
  summons?: number;
};

export type CatalogShell = {
  shell_id: string;
  label: string;
  state: string;
  capabilities?: string[];
};

export type CatalogResponse = {
  v?: number;
  agents: CatalogAgent[];
  shells: CatalogShell[];
};

export type HealthResponse = {
  status: string;
  mode?: string;
  contract?: string;
};

/**
 * @param path 以 / 开头的路径
 * @returns 绝对或同源 URL
 */
function url(path: string): string {
  return `${ORIGIN}${path}`;
}

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function json<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(url(path), {
    method: body ? 'POST' : 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) {
    throw new ApiError(result?.error?.message || `请求失败 (${response.status})`, response.status);
  }
  return result as T;
}

export type Nameplate = {
  code: string;
  agent: { agent_id: string; name: string; status: string };
};

export type OnboardingStatus = {
  state: 'WAITING' | 'CLAIMED' | 'ONLINE';
  agent?: { agent_id: string; name: string };
  nameplate?: string;
};

export type AgentDashboardProfile = {
  agent: { agent_id: string; name: string; status: string };
  nameplate: string;
};

export const redeemAgentLoginCode = (code: string, nameplate?: string) =>
  json<AgentDashboardProfile>('/v1/agent-login/redeem',
    nameplate ? { code, nameplate } : { code });

export const fetchAgentDashboardMe = () =>
  json<AgentDashboardProfile>('/v1/agent-dashboard/me');

export const createOnboardingIntent = () =>
  json<{ token: string; expires_at: string }>('/v1/onboarding/intents', {});

export const fetchOnboardingStatus = (token: string) =>
  json<OnboardingStatus>('/v1/onboarding/status', { token });

export const fetchPublicNameplate = (code: string) =>
  json<Nameplate>(`/v1/nameplates/public/${encodeURIComponent(code)}`);

/**
 * 拉取公开节点目录
 * @returns Agent / 设备列表
 */
export async function fetchCatalog(): Promise<CatalogResponse> {
  const response = await fetch(url('/v1/catalog'), {
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`catalog ${response.status}`);
  }
  return response.json() as Promise<CatalogResponse>;
}

/**
 * 拉取运行模式
 * @returns 健康信息
 */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(url('/healthz'), {
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`healthz ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}

/**
 * @param shell 设备节点
 * @returns 是否为电脑客户端
 */
export function isPcClientShell(shell: CatalogShell): boolean {
  return (
    shell.shell_id.includes('pc') ||
    shell.label.includes('电脑客户端') ||
    /电脑/.test(shell.label)
  );
}

/**
 * @param status Agent / 设备状态字
 * @returns 是否处于连接中
 */
export function isConnectingStatus(status: string): boolean {
  return status === 'CONNECTING' || status === 'RELEASING';
}
