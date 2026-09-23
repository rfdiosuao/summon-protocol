/**
 * 静态资源路径
 */
export const ASSETS = {
  hero: '/assets/hero-bg.png',
  heroHq: '/assets/hero-bg-hq.png',
  /** 原开场动画（手心出光前交接） */
  intro: '/assets/intro.mp4',
  /** 呼吸浮动帧序列（rAF 乒乓，避免视频 loop 卡顿） */
  introBreathManifest: '/assets/intro-breath/manifest.json',
  introBreathBase: '/assets/intro-breath/',
  /** 下拍正放 / 返回倒放帧序列 */
  introSlapManifest: '/assets/intro-slap/manifest.json?v=2',
  introSlapBase: '/assets/intro-slap/',
  /** 手部下拍转场（备用） */
  introHand: '/assets/intro-hand.mp4',
  /** 下拍结束后的流程页背景 */
  flowBg: '/assets/flow-bg.jpg',
  summon: '/assets/summon-logo.svg',
  logo: '/assets/logo-mark.svg',
  logoMobile: '/assets/logo-mark-mobile.svg',
  ellipse1: '/assets/ellipse-1.svg',
  ellipse2: '/assets/ellipse-2.svg',
  line: '/assets/line-1.svg',
  agentOrb: '/assets/agent-orb.webp?v=5',
  /** Agent 接入说明（对齐 summon-protocol/web/agent.md） */
  agentGuide: '/assets/agent.md',
  /** 硬件 Skill（对齐 summon-protocol/web/skill.md） */
  skillGuide: '/skill.md',
  /** 链接成功铭牌外框 */
  nameplateFrame: '/assets/nameplate-frame.svg',
  nameInput: '/assets/name-input.svg',
  iconBack: '/assets/icon-back.svg',
  iconBackMobile: '/assets/icon-back-mobile.svg',
  waitRing: '/assets/wait-ring.svg',
  waitBracket1: '/assets/wait-bracket-1.svg',
  waitBracket2: '/assets/wait-bracket-2.svg',
  waitDot: '/assets/wait-dot.svg',
  waitLoader: '/assets/wait-loader.svg',
} as const;

/** 原开场与手部浮动交接时刻（出光前，对齐 hand≈0.30s 手位） */
export const INTRO_HANDOFF = 3.15;
/** 提前预埋呼吸层首帧 */
export const INTRO_PREARM = 2.85;
/** 下拍起点（秒，相对 intro-hand；真正下坠开始） */
export const HAND_SLAP_START = 2.85;
/** 下拍切页 / 返回倒放终点（秒） */
export const HAND_SLAP_END = 3.22;
