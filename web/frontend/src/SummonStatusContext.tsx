import {
  createContext,
  useContext,
  type ReactNode,
} from 'react';
import { EMPTY_CONNECTION_STATUS, type ConnectionStatus } from './connectionStatus';
import type { CatalogResponse } from './summonApi';
import { useSummonConnection } from './useSummonConnection';

type SummonStatusValue = {
  /** 顶部状态快照 */
  status: ConnectionStatus;
  /** 原始目录；未拉到时为 null */
  catalog: CatalogResponse | null;
  /** 最近一次错误 */
  error: string | null;
};

const SummonStatusContext = createContext<SummonStatusValue>({
  status: EMPTY_CONNECTION_STATUS,
  catalog: null,
  error: null,
});

type SummonStatusProviderProps = {
  children: ReactNode;
};

/**
 * 全局注入 Hub catalog 实时状态
 * @param props.children 子树
 */
export function SummonStatusProvider({ children }: SummonStatusProviderProps) {
  const value = useSummonConnection(true);
  return (
    <SummonStatusContext.Provider value={value}>
      {children}
    </SummonStatusContext.Provider>
  );
}

/**
 * 读取全局 SUMMON 连接状态
 * @returns 状态快照与目录
 */
export function useSummonStatus(): SummonStatusValue {
  return useContext(SummonStatusContext);
}
