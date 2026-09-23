/**
 * 实时连接状态（右上角胶囊）
 * TODO: 接入 WebSocket / 轮询后，由 useConnectionStatus 推送真实数据。
 */

/** 单项连接态：离线 / 连接中（呼吸） / 已连接（黄点） */
export type LinkState = 'offline' | 'connecting' | 'online' | 'unknown';

export type ConnectionStatus = {
  /** 已连接 Agent 数 */
  agentCount: number;
  /** 已连接设备数 */
  deviceCount: number;
  /** Agent / 设备侧连接态 */
  agentsState: LinkState;
  /** 电脑客户端连接态 */
  clientState: LinkState;
};

/** 尚无后端时的占位：未连接，胶囊仍展示结构 */
export const EMPTY_CONNECTION_STATUS: ConnectionStatus = {
  agentCount: 0,
  deviceCount: 0,
  agentsState: 'offline',
  clientState: 'offline',
};

/** 等待接入中：状态点呼吸发亮 */
export const CONNECTING_STATUS: ConnectionStatus = {
  agentCount: 0,
  deviceCount: 0,
  agentsState: 'connecting',
  clientState: 'connecting',
};

/**
 * 读取当前连接状态。
 * 目前返回空状态；接入实时通道后在此订阅并更新。
 * @returns 连接快照
 */
export function getConnectionStatus(): ConnectionStatus {
  return EMPTY_CONNECTION_STATUS;
}

/**
 * 汇总总览点状态（手机折叠按钮上的单点）
 * @param status 连接快照
 * @returns 总览连接态
 */
export function getOverallLinkState(status: ConnectionStatus): LinkState {
  const states = [status.clientState, status.agentsState];
  if (states.includes('connecting')) return 'connecting';
  if (states.includes('online')) return 'online';
  if (states.every((s) => s === 'unknown')) return 'unknown';
  return 'offline';
}
