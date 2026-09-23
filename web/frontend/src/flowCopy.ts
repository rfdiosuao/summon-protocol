/**
 * 流程页共用文案与剪贴板指令
 * 文案对齐 summon-protocol/web/index.html
 */

/** Agent 接入适配器提示词 */
export const AGENT_LINK_PROMPT =
  '请阅读 https://summon.entermodetwo.com/assets/agent.md，按说明为我的 Agent 实现 SUMMON 接入适配器。先核对运行环境与能力；注册需要部署者提供的邀请凭证，凭证不要写入代码或公开日志。注册成功后自动获取并展示服务端分配的固定铭牌，重启复用原身份与铭牌；握手确认后再报告在线。';

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
