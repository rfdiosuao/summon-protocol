/**
 * 流程页共用文案与剪贴板指令
 * 文案对齐 summon-protocol/web/index.html
 */

/** Agent 接入适配器提示词 */
export const AGENT_LINK_PROMPT =
  '请阅读 https://summon.entermodetwo.com/assets/agent.md，为我当前运行的 Agent 完成 SUMMON 接入。先检查运行环境和私有配置；如无注册邀请，请在本地向我索取。凭证不要发给网页、写入代码或公开日志。注册后保存 Agent token，获取固定铭牌，建立常驻 WSS 连接并完成 hello/welcome。在线后用自己的 Agent token 请求 POST https://summon.entermodetwo.com/v1/agent-login/codes，JSON 为 {}，把返回的五分钟一次性连接码展示给我，让我在网页黑色输入框填写。不要把 Agent token 给网页，也不要只给我方案或示例代码。';

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
