import { useEffect, useRef, useState } from 'react';
import type { AgentDashboardProfile } from '../summonApi';

type Device = AgentDashboardProfile['devices'][number];
type Props = {
  agentName: string;
  devices: Device[];
  online: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
};

const angles = [0, -135, 135, 180, -90, 90, -60, 60];
const isOnline = (state: string) => ['ONLINE', 'IDLE', 'ACTIVE', 'BUSY'].includes(state);

export default function StarMapScene3D({ agentName, devices, online, selectedId, onSelect }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const buttonRefs = useRef(new Map<string, HTMLButtonElement>());
  const devicesRef = useRef(devices);
  const onlineRef = useRef(online);
  const [ready, setReady] = useState(false);
  const visible = devices.slice(0, 8);
  const deviceIds = visible.map((device) => device.shell_id).join('|');
  devicesRef.current = devices;
  onlineRef.current = online;

  useEffect(() => {
    let cancelled = false;
    let clean = () => {};
    setReady(false);
    void import('three').then((T) => {
      const host = hostRef.current;
      if (cancelled || !host) return;
      let renderer: InstanceType<typeof T.WebGLRenderer>;
      try {
        renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
      } catch {
        return; // CSS constellation remains usable without WebGL 2.
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.outputColorSpace = T.SRGBColorSpace;
      renderer.toneMapping = T.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.5;
      renderer.domElement.className = 'flow__starmap-webgl';
      renderer.domElement.setAttribute('aria-hidden', 'true');
      host.prepend(renderer.domElement);

      const scene = new T.Scene();
      const camera = new T.PerspectiveCamera(45, 1, 0.1, 60);
      camera.position.z = 7;
      scene.add(new T.AmbientLight(0xb7d4dc, 1.3));
      const keyLight = new T.PointLight(0xd9f7ff, 75);
      keyLight.position.set(-2, 2, 4);
      scene.add(keyLight);
      const rimLight = new T.PointLight(0xb9ed08, 32);
      rimLight.position.set(2, -1, -2);
      scene.add(rimLight);

      const glowCanvas = document.createElement('canvas');
      glowCanvas.width = glowCanvas.height = 128;
      const context = glowCanvas.getContext('2d');
      if (context) {
        const glow = context.createRadialGradient(64, 64, 4, 64, 64, 64);
        glow.addColorStop(0, 'rgba(217,250,255,.58)');
        glow.addColorStop(.3, 'rgba(153,218,231,.2)');
        glow.addColorStop(1, 'rgba(153,218,231,0)');
        context.fillStyle = glow;
        context.fillRect(0, 0, 128, 128);
      }
      const glowTexture = new T.CanvasTexture(glowCanvas);
      const glowMaterial = new T.SpriteMaterial({ map: glowTexture, transparent: true, depthWrite: false, blending: T.AdditiveBlending });
      const computerTexture = new T.TextureLoader().load('/assets/computer-3d.png');
      computerTexture.colorSpace = T.SRGBColorSpace;
      const computerMaterial = new T.SpriteMaterial({ map: computerTexture, transparent: true, depthWrite: false, toneMapped: false });
      const iconCanvas = document.createElement('canvas');
      iconCanvas.width = 512;
      iconCanvas.height = 256;
      const iconContext = iconCanvas.getContext('2d');
      if (iconContext) {
        // The front of SphereGeometry is U=.25; print the mark on the surface, not in HTML.
        iconContext.fillStyle = '#264b58';
        iconContext.beginPath();
        iconContext.moveTo(128, 91);
        iconContext.quadraticCurveTo(136, 119, 165, 128);
        iconContext.quadraticCurveTo(136, 136, 128, 165);
        iconContext.quadraticCurveTo(120, 136, 91, 128);
        iconContext.quadraticCurveTo(120, 119, 128, 91);
        iconContext.fill();
      }
      const iconTexture = new T.CanvasTexture(iconCanvas);
      const iconMaterial = new T.MeshBasicMaterial({ map: iconTexture, transparent: true, depthWrite: false });
      const core = new T.Mesh(
        new T.IcosahedronGeometry(.82, 5),
        new T.MeshPhysicalMaterial({ color: 0xc9e3eb, metalness: .28, roughness: .26, clearcoat: .7, emissive: 0x244b53, emissiveIntensity: .32 }),
      );
      scene.add(core);
      const coreGlow = new T.Sprite(glowMaterial);
      coreGlow.scale.set(2.9, 2.9, 1);
      scene.add(coreGlow);

      const ringPoints = Array.from({ length: 145 }, (_, index) => {
        const angle = index / 144 * Math.PI * 2;
        return new T.Vector3(Math.cos(angle), Math.sin(angle), Math.sin(angle) * .32);
      });
      const rings = [1].map((factor) => {
        const ring = new T.LineLoop(
          new T.BufferGeometry().setFromPoints(ringPoints),
          new T.LineBasicMaterial({ color: 0x9bc0c6, transparent: true, opacity: .13 }),
        );
        ring.rotation.x = .35;
        ring.userData.factor = factor;
        scene.add(ring);
        return ring;
      });

      let seed = 163;
      const random = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
      const particleCount = window.innerWidth < 600 ? 90 : 180;
      const particlePositions = new Float32Array(particleCount * 3);
      for (let index = 0; index < particleCount; index++) {
        particlePositions[index * 3] = (random() - .5) * 16;
        particlePositions[index * 3 + 1] = (random() - .5) * 8;
        particlePositions[index * 3 + 2] = (random() - .5) * 5 - 1;
      }
      const particlesGeometry = new T.BufferGeometry();
      particlesGeometry.setAttribute('position', new T.BufferAttribute(particlePositions, 3));
      const particles = new T.Points(particlesGeometry, new T.PointsMaterial({ color: 0xc7f0ef, size: .025, transparent: true, opacity: .6, sizeAttenuation: true }));
      scene.add(particles);

      const objects = visible.map((device, index) => {
        const body = index === 0
          ? new T.Sprite(computerMaterial)
          : new T.Mesh(
              new T.SphereGeometry(.31, 48, 32),
              new T.MeshPhysicalMaterial({ color: 0xd8edf1, metalness: .28, roughness: .3, clearcoat: .65, emissive: 0x254954, emissiveIntensity: .34 }),
            );
        if (index === 0) body.scale.set(1.55, 1.55, 1);
        else {
          const printedMark = new T.Mesh(new T.SphereGeometry(.319, 48, 32), iconMaterial);
          body.add(printedMark);
        }
        scene.add(body);
        const aura = new T.Sprite(glowMaterial);
        aura.scale.setScalar(index === 0 ? 1.15 : 1.05);
        scene.add(aura);
        const geometry = new T.BufferGeometry();
        geometry.setAttribute('position', new T.BufferAttribute(new Float32Array(6), 3));
        const line = new T.Line(geometry, new T.LineBasicMaterial({ color: 0x87b7bf, transparent: true, opacity: .45 }));
        scene.add(line);
        return { id: device.shell_id, index, body, aura, line };
      });

      let radiusX = 3;
      const resize = () => {
        const width = host.clientWidth;
        const height = host.clientHeight;
        if (!width || !height) return;
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height, false);
        const worldWidth = 2 * 7 * Math.tan(T.MathUtils.degToRad(45 / 2)) * camera.aspect;
        radiusX = Math.min(3.65, worldWidth * .33);
        rings.forEach((ring) => ring.scale.set(radiusX * Number(ring.userData.factor), 1.75 * Number(ring.userData.factor), 1));
        objects.forEach(({ id, index }) => {
          const angle = T.MathUtils.degToRad(angles[index]);
          const button = buttonRefs.current.get(id);
          if (!button) return;
          const position = new T.Vector3(radiusX * Math.cos(angle), 1.72 * Math.sin(angle), Math.sin(angle) * .44).project(camera);
          button.style.left = `${(position.x * .5 + .5) * width}px`;
          button.style.top = `${(-position.y * .5 + .5) * height}px`;
        });
      };
      const observer = new ResizeObserver(resize);
      observer.observe(host);
      resize();
      const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const clock = new T.Clock();
      let targetX = 0;
      let targetY = 0;
      const onMove = (event: PointerEvent) => {
        if (prefersReducedMotion) return;
        const box = host.getBoundingClientRect();
        targetX = ((event.clientX - box.left) / box.width - .5) * .48;
        targetY = ((event.clientY - box.top) / box.height - .5) * -.3;
      };
      const onLeave = () => { targetX = 0; targetY = 0; };
      host.addEventListener('pointermove', onMove);
      host.addEventListener('pointerleave', onLeave);

      const animate = () => {
        if (document.hidden) return;
        const time = prefersReducedMotion ? 0 : clock.getElapsedTime();
        camera.position.x += (targetX - camera.position.x) * .035;
        camera.position.y += (targetY - camera.position.y) * .035;
        camera.lookAt(0, 0, 0);
        core.rotation.y = time * .12;
        core.rotation.x = time * .055;
        (core.material as InstanceType<typeof T.MeshPhysicalMaterial>).emissiveIntensity = onlineRef.current ? .32 : .08;
        particles.rotation.y = time * .002;
        rings.forEach((ring) => { ring.rotation.z = Math.sin(time * .14) * .025; });
        objects.forEach(({ id, index, body, aura, line }) => {
          const device = devicesRef.current.find((item) => item.shell_id === id);
          const base = T.MathUtils.degToRad(angles[index]);
          const angle = base + Math.sin(time * .28 + index * 1.7) * .055;
          const bob = Math.sin(time * .86 + index * 1.8) * .09;
          body.position.set(radiusX * Math.cos(angle), 1.72 * Math.sin(angle) + bob, Math.sin(angle + time * .15) * .44);
          if (index > 0) body.rotation.y = Math.sin(time * .25 + index) * .09;
          aura.position.copy(body.position);
          const active = Boolean(device && isOnline(device.state));
          if (body instanceof T.Mesh) {
            const material = body.material as InstanceType<typeof T.MeshPhysicalMaterial>;
            material.color.setHex(active ? 0xe5f9ea : 0xa6c4ce);
            material.emissive.setHex(active ? 0x405216 : 0x254954);
          }
          (line.material as InstanceType<typeof T.LineBasicMaterial>).opacity = active && device?.authorized ? .78 : .29;
          const positions = line.geometry.getAttribute('position') as InstanceType<typeof T.BufferAttribute>;
          positions.setXYZ(1, body.position.x, body.position.y, body.position.z);
          positions.needsUpdate = true;
        });
        renderer.render(scene, camera);
      };
      renderer.setAnimationLoop(animate);
      setReady(true);
      clean = () => {
        renderer.setAnimationLoop(null);
        observer.disconnect();
        host.removeEventListener('pointermove', onMove);
        host.removeEventListener('pointerleave', onLeave);
        scene.traverse((object) => {
          if (object instanceof T.Mesh || object instanceof T.Line || object instanceof T.LineSegments || object instanceof T.Points) {
            object.geometry.dispose();
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.forEach((material) => material.dispose());
          }
        });
        glowMaterial.dispose();
        glowTexture.dispose();
        computerMaterial.dispose();
        computerTexture.dispose();
        iconMaterial.dispose();
        iconTexture.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    }).catch(() => { /* Keep the accessible CSS fallback. */ });
    return () => { cancelled = true; clean(); };
  }, [deviceIds]);

  return <div className={`flow__starmap-canvas flow__starmap-canvas--3d ${ready ? 'is-ready' : 'is-fallback'}`}
    ref={hostRef} aria-label={`${agentName} 已适配 ${Math.max(0, devices.length - 1)} 台其他设备`}>
    <div className="flow__starmap-fallback-ring" aria-hidden="true" />
    <div className="flow__starmap-agent-label"><strong>{agentName}</strong><small>AGENT · {online ? 'ONLINE' : 'OFFLINE'}</small></div>
    {visible.map((device, index) => {
      const angle = TAngle(index);
      return <button key={device.shell_id} type="button"
        ref={(element) => { if (element) buttonRefs.current.set(device.shell_id, element); else buttonRefs.current.delete(device.shell_id); }}
        className={`flow__starmap-node flow__starmap-node--3d ${device.kind === 'computer' ? 'flow__starmap-node--computer' : ''} ${isOnline(device.state) ? 'is-online' : ''} ${selectedId === device.shell_id ? 'is-selected' : ''}`}
        style={{ left: `${50 + 33 * Math.cos(angle)}%`, top: `${50 + 34 * Math.sin(angle)}%` }}
        onClick={() => onSelect(device.shell_id)}
        aria-label={`${device.label}，${isOnline(device.state) ? '在线' : '离线'}，${device.authorized ? '已授权' : '待授权'}`}>
        <span className="flow__starmap-node-star" aria-hidden="true">{device.kind === 'computer' ? <img src="/assets/computer-3d.png" alt="" /> : '✦'}</span>
        <span className="flow__starmap-node-label">{device.label}</span>
        <span className="flow__starmap-node-state">{isOnline(device.state) ? '在线' : '离线'} · {device.authorized ? '已授权' : '待授权'}</span>
      </button>;
    })}
  </div>;
}

function TAngle(index: number) {
  return angles[index] * Math.PI / 180;
}
