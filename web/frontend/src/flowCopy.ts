/**
 * 流程页共用文案与剪贴板指令
 * 文案对齐 summon-protocol/web/index.html
 */

/** Agent 接入适配器提示词 */
export const AGENT_LINK_PROMPT =
  '请阅读 https://summon.entermodetwo.com/assets/agent.md，为我当前运行的 Agent 完成 SUMMON 接入。先检查运行环境和私有配置中是否已有注册邀请凭证；如果没有，在本地向我索取，不要把凭证发给网站、写入代码或公开日志。完成注册后保存 Agent token，自动获取并展示服务端分配的固定铭牌，连接 WSS 并完成 hello/welcome，再用自己的 token 认领本网页接入会话。保持连接运行，直到网页显示在线和铭牌；不要只给我接入方案或示例代码。';

/** 硬件 / 令牌接入提示词 */
export const HARDWARE_TOKEN_PROMPT =
  'Read https://summon.entermodetwo.com/skill.md to assess my hardware and connect it to SUMMON.';

/** Agent 指令复制成功提示 */
export const AGENT_COPY_HINT = '已复制，把这段话粘贴给你的 Agent 即可。';

/** 硬件指令复制成功提示 */
export const HARDWARE_COPY_HINT =
  '已复制。把提示词交给能操作硬件所在电脑的 Agent';

/**
 * 写入系统剪贴板
 * @param text 要复制的文本
 * @returns 是否成功
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fallback below */
  }

  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
