import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/** 线上 Hub，本地开发代理到此源 */
const SUMMON_ORIGIN = 'https://summon.entermodetwo.com';

/**
 * Vite 配置：唤名官方站
 * /v1、/healthz 代理到 summon-protocol 公开站，避免浏览器 CORS。
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/v1': {
        target: SUMMON_ORIGIN,
        changeOrigin: true,
        secure: true,
      },
      '/healthz': {
        target: SUMMON_ORIGIN,
        changeOrigin: true,
        secure: true,
      },
    },
  },
});
