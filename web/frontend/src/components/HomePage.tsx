import { useEffect, useRef, useState } from 'react';
import {
  ASSETS,
  INTRO_HANDOFF,
  INTRO_PREARM,
} from '../assets';
import CursorGrid from './CursorGrid';
import FlowStatusBar from './FlowStatusBar';
import '../styles/home.css';

/** 进入主页方式：首次开场 / 从二级页返回倒放 */
export type HomeEntry = 'fresh' | 'return';

type HomePageProps = {
  /** 进入方式：首次开场 / 二级页返回倒放 */
  entryMode?: HomeEntry;
  /**
   * 点击「开始召唤」：手部下拍结束后进入后续流程
   * @param sceneBg 下拍结束帧，作为后续流程背景
   */
  onStart: (sceneBg: string) => void;
};

type Phase = 'intro' | 'title' | 'returning' | 'ready' | 'exiting';

type FrameManifest = {
  count: number;
  halfMs?: number;
  durationMs?: number;
  files: string[];
};

/** SUMMON 慢揭示时长 */
const TITLE_REVEAL_MS = 2200;
/** 慢网络下也要及时开放首页操作。 */
const MAX_FIRST_VISIT_WAIT_MS = 5000;

/**
 * 加载图片
 */
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`fail ${src}`));
    img.src = src;
  });
}

/**
 * 官方主页
 * 首次：开场 → 呼吸浮动 → 下拍帧序列
 * 返回：下拍帧倒放 → 呼吸浮动（不重播开场）
 */
export default function HomePage({ entryMode = 'fresh', onStart }: HomePageProps) {
  const introRef = useRef<HTMLVideoElement>(null);
  const breathCanvasRef = useRef<HTMLCanvasElement>(null);
  const breathFramesRef = useRef<HTMLImageElement[]>([]);
  const slapFramesRef = useRef<HTMLImageElement[]>([]);
  const breathHalfMsRef = useRef(1350);
  const slapDurationMsRef = useRef(650);
  const breathPlayingRef = useRef(false);
  const breathRafRef = useRef(0);
  const breathStartTsRef = useRef(0);
  const breathLastIdxRef = useRef(-1);
  const slapRafRef = useRef(0);
  const stopBreathRef = useRef<() => void>(() => undefined);
  const startBreathRef = useRef<() => void>(() => undefined);
  const drawImageRef = useRef<(img: HTMLImageElement) => void>(() => undefined);

  const isReturn = entryMode === 'return';
  const [phase, setPhase] = useState<Phase>(isReturn ? 'returning' : 'intro');
  const [cover, setCover] = useState<'intro' | 'none'>(isReturn ? 'none' : 'intro');
  const [breathArmed, setBreathArmed] = useState(isReturn);
  const phaseRef = useRef<Phase>(isReturn ? 'returning' : 'intro');
  const prearmedRef = useRef(isReturn);
  const handedOffRef = useRef(isReturn);
  const exitStartedRef = useRef(false);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    if (phase !== 'title') {
      return;
    }
    const timer = window.setTimeout(() => setPhase('ready'), TITLE_REVEAL_MS);
    return () => window.clearTimeout(timer);
  }, [phase]);

  useEffect(() => {
    const intro = introRef.current;
    const canvas = breathCanvasRef.current;
    if (!intro || !canvas) {
      return;
    }

    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) {
      return;
    }

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let cancelled = false;
    let releaseRemainingFrames: () => void = () => undefined;
    const videoHandoff = new Promise<void>((resolve) => { releaseRemainingFrames = resolve; });

    /**
     * 画单张图到 canvas
     */
    const drawImage = (img: HTMLImageElement) => {
      if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      ctx.drawImage(img, 0, 0);
    };
    drawImageRef.current = drawImage;

    /**
     * 将指定呼吸帧画到 canvas
     */
    const drawBreathFrame = (index: number) => {
      const frames = breathFramesRef.current;
      if (!frames.length) {
        return;
      }
      const img = frames[Math.max(0, Math.min(frames.length - 1, index))];
      if (!img) {
        return;
      }
      drawImage(img);
      breathLastIdxRef.current = index;
    };

    /**
     * 时间驱动的乒乓呼吸
     */
    const tickBreath = (ts: number) => {
      if (cancelled || !breathPlayingRef.current) {
        return;
      }
      const frames = breathFramesRef.current;
      const last = frames.length - 1;
      if (last < 1) {
        breathRafRef.current = requestAnimationFrame(tickBreath);
        return;
      }

      if (!breathStartTsRef.current) {
        breathStartTsRef.current = ts;
      }

      const half = breathHalfMsRef.current;
      const cycle = half * 2;
      const elapsed = (ts - breathStartTsRef.current) % cycle;
      const tri = elapsed <= half ? elapsed / half : 1 - (elapsed - half) / half;
      const idx = Math.round(tri * last);

      if (idx !== breathLastIdxRef.current) {
        drawBreathFrame(idx);
      }

      breathRafRef.current = requestAnimationFrame(tickBreath);
    };

    /**
     * 停止呼吸 rAF
     */
    const stopBreath = () => {
      breathPlayingRef.current = false;
      breathStartTsRef.current = 0;
      if (breathRafRef.current) {
        cancelAnimationFrame(breathRafRef.current);
        breathRafRef.current = 0;
      }
    };
    stopBreathRef.current = stopBreath;

    /**
     * 开始呼吸循环
     */
    const startBreath = () => {
      if (cancelled || breathPlayingRef.current || !breathFramesRef.current.length) {
        return;
      }
      breathPlayingRef.current = true;
      breathStartTsRef.current = 0;
      breathLastIdxRef.current = -1;
      breathRafRef.current = requestAnimationFrame(tickBreath);
    };
    startBreathRef.current = startBreath;

    /**
     * 播放下拍帧序列（dir: 1 正放 / -1 倒放）
     */
    const playSlapFrames = (dir: 1 | -1, onDone: () => void) => {
      const frames = slapFramesRef.current;
      if (!frames.length) {
        onDone();
        return;
      }

      const last = frames.length - 1;
      const duration = slapDurationMsRef.current;
      let startTs = 0;
      let finished = false;

      const finish = () => {
        if (finished) {
          return;
        }
        finished = true;
        if (slapRafRef.current) {
          cancelAnimationFrame(slapRafRef.current);
          slapRafRef.current = 0;
        }
        // 停在终点帧
        drawImage(dir === 1 ? frames[last] : frames[0]);
        onDone();
      };

      const tick = (ts: number) => {
        if (cancelled) {
          return;
        }
        if (!startTs) {
          startTs = ts;
        }
        const t = Math.min(1, (ts - startTs) / duration);
        const idx =
          dir === 1
            ? Math.round(t * last)
            : Math.round((1 - t) * last);
        drawImage(frames[idx]);
        if (t >= 1) {
          finish();
          return;
        }
        slapRafRef.current = requestAnimationFrame(tick);
      };

      // 先画起始帧，避免闪一下
      drawImage(dir === 1 ? frames[0] : frames[last]);
      setBreathArmed(true);
      slapRafRef.current = requestAnimationFrame(tick);
    };

    /** 先准备交接所需的首帧，交接后再下载其余动画。 */
    const loadFrames = async () => {
      const [breathRes, slapRes] = await Promise.all([
        fetch(ASSETS.introBreathManifest),
        fetch(ASSETS.introSlapManifest),
      ]);
      const breathManifest = (await breathRes.json()) as FrameManifest;
      const slapManifest = (await slapRes.json()) as FrameManifest;

      breathHalfMsRef.current = breathManifest.halfMs || 1350;
      slapDurationMsRef.current = Math.max(slapManifest.durationMs || 450, 450);

      const firstBreath = await loadImage(`${ASSETS.introBreathBase}${breathManifest.files[0]}`);
      if (cancelled) {
        return;
      }
      breathFramesRef.current = [firstBreath];
      drawBreathFrame(0);
      if (reduced) {
        intro.pause();
        prearmBreath();
        setCover('none');
        setPhase('ready');
      } else if (!isReturn && (intro.currentTime >= INTRO_HANDOFF || videoFailed)) {
        handoffToBreath();
        if (videoFailed) {
          setPhase('ready');
        }
      }

      // 首次进入时先让视频拿到带宽；交接到首帧后再加载后续动画。
      if (!isReturn && !reduced) {
        await videoHandoff;
      }
      if (cancelled) {
        return;
      }

      const [breathImgs, slapImgs] = await Promise.all([
        Promise.all(
          breathManifest.files.slice(1).map((file) => loadImage(`${ASSETS.introBreathBase}${file}`)),
        ),
        Promise.all(
          slapManifest.files.map((file) => loadImage(`${ASSETS.introSlapBase}${file}`)),
        ),
      ]);

      if (cancelled) {
        return;
      }
      breathFramesRef.current = [firstBreath, ...breathImgs];
      slapFramesRef.current = slapImgs;
      if (isReturn && !reduced) {
        bootReturn();
      }
    };

    /**
     * 预埋呼吸首帧
     */
    const prearmBreath = () => {
      if (cancelled || prearmedRef.current) {
        return;
      }
      prearmedRef.current = true;
      drawBreathFrame(0);
      setBreathArmed(true);
    };

    /**
     * 揭开并开始浮动
     */
    const handoffToBreath = () => {
      if (cancelled || handedOffRef.current) {
        return;
      }
      if (!breathFramesRef.current.length) {
        intro.pause();
        return;
      }
      handedOffRef.current = true;
      if (!prearmedRef.current) {
        prearmBreath();
      }
      intro.pause();
      setCover('none');
      startBreath();
      releaseRemainingFrames();
      if (phaseRef.current === 'intro') {
        setPhase('title');
      }
    };

    /**
     * 从二级页返回：帧序列倒放下拍 → 呼吸浮动
     */
    const bootReturn = () => {
      intro.pause();
      setCover('none');
      setPhase('returning');
      playSlapFrames(-1, () => {
        if (cancelled) {
          return;
        }
        startBreath();
        setPhase('ready');
      });
    };

    const onTimeUpdate = () => {
      if (cancelled || handedOffRef.current || isReturn) {
        return;
      }
      if (!prearmedRef.current && intro.currentTime >= INTRO_PREARM) {
        prearmBreath();
      }
      if (intro.currentTime >= INTRO_HANDOFF) {
        handoffToBreath();
      }
    };

    intro.addEventListener('timeupdate', onTimeUpdate);
    intro.addEventListener('ended', handoffToBreath);

    let videoFailed = false;
    const bootFresh = () => {
      if (cancelled) {
        return;
      }
      try {
        intro.currentTime = 0;
      } catch {
        // ignore
      }
      intro.playbackRate = 1;
      intro.play()?.catch(() => {
        videoFailed = true;
        if (breathFramesRef.current.length) {
          handoffToBreath();
          setPhase('ready');
        }
      });
    };

    if (!isReturn && !reduced) {
      if (intro.readyState >= 1) {
        bootFresh();
      } else {
        intro.addEventListener('loadedmetadata', bootFresh, { once: true });
      }
    }

    void loadFrames().catch(() => {
      if (!cancelled) {
        intro.pause();
        setCover('none');
        setPhase('ready');
      }
    });

    const firstVisitTimer = !isReturn && !reduced
      ? window.setTimeout(() => {
        if (!cancelled) {
          setPhase((current) => current === 'intro' || current === 'title' ? 'ready' : current);
        }
      }, MAX_FIRST_VISIT_WAIT_MS)
      : 0;

    return () => {
      cancelled = true;
      window.clearTimeout(firstVisitTimer);
      releaseRemainingFrames();
      stopBreath();
      if (slapRafRef.current) {
        cancelAnimationFrame(slapRafRef.current);
      }
      intro.removeEventListener('timeupdate', onTimeUpdate);
      intro.removeEventListener('ended', handoffToBreath);
      intro.removeEventListener('loadedmetadata', bootFresh);
    };
  }, [isReturn]);

  /**
   * 开始召唤：停浮动 → UI 上移 → 下拍帧正放 → 进入下一页
   */
  const handleStart = () => {
    if (phase !== 'ready' || exitStartedRef.current) {
      return;
    }
    exitStartedRef.current = true;
    setPhase('exiting');
    stopBreathRef.current();

    const canvas = breathCanvasRef.current;
    const frames = slapFramesRef.current;
    const breath = breathFramesRef.current;

    // 先落回呼吸首帧再下拍，避免「浮动收尾 + 下拍」叠成两段
    if (breath[0]) {
      drawImageRef.current(breath[0]);
    }

    /**
     * 抓取当前画面作为流程背景
     */
    const captureBg = () => {
      try {
        if (canvas && canvas.width > 0) {
          return canvas.toDataURL('image/jpeg', 0.92);
        }
      } catch {
        // ignore
      }
      return ASSETS.flowBg;
    };

    if (!frames.length) {
      onStart(ASSETS.flowBg);
      return;
    }

    // 用与返回相同的帧序列正放，保证可倒放衔接
    const duration = slapDurationMsRef.current;
    const last = frames.length - 1;
    let startTs = 0;
    let done = false;

    const finish = () => {
      if (done) {
        return;
      }
      done = true;
      if (slapRafRef.current) {
        cancelAnimationFrame(slapRafRef.current);
        slapRafRef.current = 0;
      }
      drawImageRef.current(frames[last]);
      onStart(captureBg());
    };

    const tick = (ts: number) => {
      if (!startTs) {
        startTs = ts;
      }
      const t = Math.min(1, (ts - startTs) / duration);
      const idx = Math.round(t * last);
      drawImageRef.current(frames[idx]);
      if (t >= 1) {
        finish();
        return;
      }
      slapRafRef.current = requestAnimationFrame(tick);
    };

    drawImageRef.current(frames[0]);
    setBreathArmed(true);
    slapRafRef.current = requestAnimationFrame(tick);
  };

  return (
    <main className={`home home--${phase}`} data-node-id="1:2">
      <div className="home__stage">
        <div className="home__bg" data-node-id="26:277" aria-hidden="true">
          <canvas
            ref={breathCanvasRef}
            className={`home__bg-breath${breathArmed ? ' is-armed' : ''}`}
          />
          <video
            ref={introRef}
            className={`home__bg-video home__bg-video--intro${cover === 'intro' ? ' is-cover' : ''}`}
            src={ASSETS.intro}
            muted
            playsInline
            preload="auto"
          />
        </div>

        <CursorGrid
          className="home__cursor-grid"
          cellSize={65}
          color="#e7e7e7"
          radius={140}
          falloff="sharp"
          holdTime={200}
          fadeDuration={550}
          lineWidth={2}
          maxOpacity={1}
          fillOpacity={0.14}
          gridOpacity={0}
          cellRadius={0}
          clickPulse
          pulseSpeed={720}
        />

        <div className="home__glow home__glow--2 home__enter" data-node-id="7:83" aria-hidden="true">
          <img src={ASSETS.ellipse2} alt="" />
        </div>
        <div className="home__glow home__glow--1 home__enter" data-node-id="7:82" aria-hidden="true">
          <img src={ASSETS.ellipse1} alt="" />
        </div>

        <header className="home__header home__ui">
          <p className="home__brand home__enter" data-node-id="7:84">
            「唤名」
          </p>

          <div className="home__logo home__enter" data-node-id="7:89">
            <img
              className="home__logo-desktop"
              src={ASSETS.logo}
              alt="SUMMON"
              width={60}
              height={60}
            />
            <img
              className="home__logo-mobile"
              src={ASSETS.logoMobile}
              alt="SUMMON"
              width={40}
              height={40}
            />
          </div>

          <div className="home__status home__enter">
            <FlowStatusBar tone="home" />
          </div>
        </header>

        <h1 className="home__title home__ui" data-node-id="34:1946">
          <img
            className="home__title-mark"
            src={ASSETS.summon}
            alt="SUMMON"
            width={1127}
            height={92}
          />
          <span className="home__title-text" data-node-id="26:397">
            SUMMON
          </span>
        </h1>

        <p className="home__tagline home__enter home__ui" data-node-id="7:91">
          <span>唤名是一套让</span>
          <span>「任何 Agent 都能住进任何机器」的产品</span>
        </p>

        <div className="home__line home__enter home__ui" data-node-id="9:95" aria-hidden="true">
          <img src={ASSETS.line} alt="" width={71} height={1} />
        </div>

        <div className="home__cta-wrap home__enter home__ui">
          <button
            type="button"
            className="home__cta"
            data-node-id="7:80"
            disabled={phase !== 'ready'}
            onClick={handleStart}
          >
            接入 Agent
          </button>
        </div>
      </div>
    </main>
  );
}
