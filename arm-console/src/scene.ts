import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import URDFLoader, { type URDFRobot } from 'urdf-loader';
import { MeshBVH, type HitPointInfo } from 'three-mesh-bvh';
import type { JointSpec } from './types.ts';

type ViewName = 'iso' | 'front' | 'side' | 'top';

export interface JointDisplayState {
  id: number;
  actualDeg: number;
  targetDeg: number;
  enabled: boolean;
}

export interface SafetyReport {
  safe: boolean;
  severity: 'safe' | 'warning' | 'blocked';
  violations: string[];
  clearanceMm: number;
  source?: 'current' | 'target';
  collisionLinks?: string[];
  contact?: {
    from: [number, number, number];
    to: [number, number, number];
    label: string;
  };
}

interface JointGizmo {
  id: number;
  group: THREE.Group;
  joint: THREE.Object3D;
  actualNeedle: THREE.Line;
  targetNeedle: THREE.Line;
  targetHandle: THREE.Mesh;
  hitArea: THREE.Mesh;
  label: THREE.Sprite;
  labelCanvas: HTMLCanvasElement;
  labelTexture: THREE.CanvasTexture;
  labelText: string;
  radius: number;
}

interface DragState {
  id: number;
  pointerId: number;
  startX: number;
  startDegrees: number;
  moved: boolean;
}

interface GripperGizmo {
  group: THREE.Group;
  measure: THREE.LineSegments;
  label: THREE.Sprite;
  canvas: HTMLCanvasElement;
  texture: THREE.CanvasTexture;
  text: string;
}

interface CollisionBody {
  linkName: string;
  order: number;
  mesh: THREE.Mesh;
  bvh: MeshBVH;
}

interface MaterialBaseline {
  color?: THREE.Color;
  emissive?: THREE.Color;
  emissiveIntensity?: number;
  opacity: number;
  transparent: boolean;
}

const LOCAL_RING_AXIS = new THREE.Vector3(0, 0, 1);

export class ArmScene {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
  private readonly controls: OrbitControls;
  private readonly resizeObserver: ResizeObserver;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly targetCamera = new THREE.Vector3(0.82, 0.62, 0.84);
  private readonly targetLook = new THREE.Vector3(0, 0.25, 0);
  private readonly hologramRoot = new THREE.Group();
  private readonly riskOverlay = new THREE.Group();
  private readonly gizmos = new Map<number, JointGizmo>();
  private readonly linkMeshes = new Map<string, THREE.Mesh[]>();
  private readonly previewLinkMeshes = new Map<string, THREE.Mesh[]>();
  private readonly materialBaselines = new Map<THREE.Material, MaterialBaseline>();
  private readonly previewMaterialBaselines = new Map<THREE.Material, MaterialBaseline>();
  private readonly stressByJoint = new Map<number, number>();
  private readonly labelVisibleUntil = new Map<number, number>();
  private readonly jointState = new Map<number, JointDisplayState>();
  private readonly displayedAngles: Record<number, number> = {};
  private readonly previewAngles: Record<number, number> = {};
  private readonly collisionBodies: CollisionBody[] = [];
  private readonly onTcp: (value: THREE.Vector3) => void;
  private readonly onJointInput: (id: number, degrees: number, final: boolean) => boolean;
  private readonly onSafety: (report: SafetyReport) => void;
  private specs = new Map<number, JointSpec>();
  private robot: URDFRobot | null = null;
  private previewRobot: URDFRobot | null = null;
  private endLink: THREE.Object3D | null = null;
  private gripperGizmo: GripperGizmo | null = null;
  private animationFrame = 0;
  private cameraTween = true;
  private hardwareLimits = false;
  private drag: DragState | null = null;
  private hoveredJoint: number | null = null;
  private activeAxisView: number | null = null;
  private riskVisible = true;
  private scanRing: THREE.Mesh | null = null;
  private riskLine: THREE.Line | null = null;
  private riskPoints: [THREE.Mesh, THREE.Mesh] | null = null;
  private riskBurst: THREE.Sprite | null = null;
  private collisionPreviewHoldUntil = 0;
  private lastSafetyAt = 0;
  private safetyDirty = true;
  private previewGripMm = 60;
  private displayedGripMm = 60;
  private latestSafety: SafetyReport = { safe: true, severity: 'safe', violations: [], clearanceMm: 999 };
  private currentSafety: SafetyReport = { safe: true, severity: 'safe', violations: [], clearanceMm: 999, source: 'current' };
  private targetSafety: SafetyReport = { safe: true, severity: 'safe', violations: [], clearanceMm: 999, source: 'target' };

  constructor(
    canvas: HTMLCanvasElement,
    onTcp: (value: THREE.Vector3) => void,
    onJointInput: (id: number, degrees: number, final: boolean) => boolean,
    onSafety: (report: SafetyReport) => void,
  ) {
    this.canvas = canvas;
    this.onTcp = onTcp;
    this.onJointInput = onJointInput;
    this.onSafety = onSafety;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.62;
    this.renderer.shadowMap.enabled = false;
    this.scene.background = new THREE.Color('#02090d');
    this.scene.fog = new THREE.FogExp2('#02090d', 0.72);

    this.camera.position.copy(this.targetCamera);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.065;
    this.controls.target.copy(this.targetLook);
    this.controls.minDistance = 0.22;
    this.controls.maxDistance = 3.5;
    this.controls.minPolarAngle = 0.02;
    this.controls.maxPolarAngle = Math.PI - 0.02;
    this.controls.enablePan = true;
    this.controls.screenSpacePanning = true;
    this.controls.zoomToCursor = true;
    this.controls.addEventListener('start', () => { this.cameraTween = false; });

    this.buildLighting();
    this.buildHologramStage();
    this.scene.add(this.hologramRoot, this.riskOverlay);

    this.canvas.addEventListener('pointerdown', this.handlePointerDown);
    this.canvas.addEventListener('pointermove', this.handlePointerMove);
    this.canvas.addEventListener('pointerup', this.handlePointerUp);
    this.canvas.addEventListener('pointercancel', this.handlePointerUp);
    this.canvas.addEventListener('dblclick', this.handleDoubleClick);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement ?? canvas);
    this.resize();
    this.animate();
  }

  configureJoints(specs: JointSpec[]): void {
    this.specs = new Map(specs.filter(item => item.id <= 6).map(item => [item.id, item]));
    if (this.robot) this.buildJointGizmos();
  }

  setHardwareLimits(enabled: boolean): void {
    this.hardwareLimits = enabled;
    this.safetyDirty = true;
  }

  load(url: string, onProgress: (percent: number) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      const manager = new THREE.LoadingManager();
      const loader = new URDFLoader(manager);
      let parsedRobot: URDFRobot | null = null;
      let completed = false;
      let managerFinished = false;
      const finish = (): void => {
        if (!parsedRobot || completed) return;
        completed = true;
        this.prepareRobotMaterials(parsedRobot);
        this.collectLinkMeshes(parsedRobot);
        const preview = parsedRobot.clone(true) as URDFRobot;
        preview.name = 'target-pose-ghost';
        this.previewRobot = preview;
        this.preparePreviewMaterials(preview);
        this.collectVisualLinkMeshes(preview, this.previewLinkMeshes);
        this.scene.add(preview);
        this.applyPreviewPose();
        this.buildJointGizmos();
        this.buildGripperGizmo();
        this.fit();
        this.safetyDirty = true;
        resolve();
      };
      manager.onProgress = (_item, loaded, total) => {
        onProgress((loaded / Math.max(1, total)) * 100);
        if (loaded >= total) {
          managerFinished = true;
          queueMicrotask(finish);
        }
      };
      manager.onLoad = () => {
        managerFinished = true;
        finish();
      };
      loader.parseVisual = true;
      loader.parseCollision = true;
      loader.load(
        url,
        robot => {
          this.robot = robot;
          parsedRobot = robot;
          robot.rotation.x = -Math.PI / 2;
          this.scene.add(robot);
          this.endLink = robot.links.end_link ?? robot.getObjectByName('end_link');
          if (managerFinished) finish();
          window.setTimeout(finish, 10_000);
        },
        undefined,
        reject,
      );
    });
  }

  setJoints(states: JointDisplayState[], gripperWidthMm: number): void {
    if (!this.robot) return;
    for (const state of states) {
      this.jointState.set(state.id, state);
      this.displayedAngles[state.id] = state.actualDeg;
      if (!(state.id in this.previewAngles)) this.previewAngles[state.id] = state.targetDeg;
      this.robot.setJointValue(`joint${state.id}`, THREE.MathUtils.degToRad(state.actualDeg));
    }
    this.displayedGripMm = gripperWidthMm;
    this.setGripper(gripperWidthMm);
    this.robot.updateMatrixWorld(true);
    this.safetyDirty = true;
    this.updateGizmos();
    this.updatePreviewVisibility();
  }

  setPreviewTargets(angles: Record<number, number>, gripperWidthMm: number): void {
    Object.assign(this.previewAngles, angles);
    for (const [idText, targetDeg] of Object.entries(angles)) {
      const id = Number(idText);
      const current = this.jointState.get(id);
      if (current) this.jointState.set(id, { ...current, targetDeg });
    }
    this.previewGripMm = gripperWidthMm;
    this.applyPreviewPose();
    this.safetyDirty = true;
    this.updateGizmos();
  }

  setStress(states: Array<{ id: number; stressRatio: number }>): void {
    for (const state of states) this.stressByJoint.set(state.id, THREE.MathUtils.clamp(state.stressRatio, 0, 2));
    this.refreshMaterialEffects();
  }

  checkPose(angles: Record<number, number>, gripperWidthMm: number): SafetyReport {
    const report = this.evaluatePose(angles, gripperWidthMm);
    this.restoreDisplayedPose();
    if (report.severity === 'blocked') {
      this.setPreviewRobotPose(angles, gripperWidthMm);
      this.targetSafety = { ...report, source: 'target' };
      this.latestSafety = { ...report, source: 'target', violations: report.violations.map(item => `目标姿态 · ${item}`) };
      this.collisionPreviewHoldUntil = performance.now() + 900;
      this.onSafety(this.latestSafety);
      this.updateRiskOverlay(this.latestSafety);
      this.refreshMaterialEffects();
    } else {
      this.collisionPreviewHoldUntil = 0;
    }
    return report;
  }

  validateTrajectory(from: Record<number, number>, to: Record<number, number>, gripperWidthMm: number, samples = 18): SafetyReport {
    if (!this.robot) return this.latestSafety;
    const start = this.evaluatePose(from, gripperWidthMm);
    if (start.severity === 'blocked') {
      const destination = this.evaluatePose(to, gripperWidthMm);
      this.restoreDisplayedPose();
      return destination;
    }
    const maxDelta = Math.max(...Array.from({ length: 6 }, (_, index) => Math.abs((to[index + 1] ?? from[index + 1] ?? 0) - (from[index + 1] ?? 0))));
    samples = Math.min(240, Math.max(samples, Math.ceil(maxDelta)));
    let worst: SafetyReport = { safe: true, severity: 'safe', violations: [], clearanceMm: 999 };
    for (let step = 1; step <= samples; step += 1) {
      const fraction = step / samples;
      const pose: Record<number, number> = {};
      for (let id = 1; id <= 6; id += 1) pose[id] = (from[id] ?? 0) + ((to[id] ?? from[id] ?? 0) - (from[id] ?? 0)) * fraction;
      const report = this.evaluatePose(pose, gripperWidthMm);
      if (report.severity === 'blocked') {
        this.restoreDisplayedPose();
        return report;
      }
      if (report.severity === 'warning') worst = report;
    }
    this.restoreDisplayedPose();
    return worst;
  }

  setView(name: ViewName): void {
    this.leaveAxisView();
    const views: Record<ViewName, THREE.Vector3> = {
      iso: new THREE.Vector3(0.82, 0.62, 0.84),
      front: new THREE.Vector3(0.98, 0.34, 0),
      side: new THREE.Vector3(0, 0.34, 0.98),
      top: new THREE.Vector3(0.01, 1.22, 0.01),
    };
    const distance = Math.max(0.8, this.camera.position.distanceTo(this.controls.target));
    this.targetLook.copy(this.controls.target);
    this.targetCamera.copy(this.targetLook).add(views[name].normalize().multiplyScalar(distance));
    this.cameraTween = true;
  }

  focusJoint(id: number): void {
    const gizmo = this.gizmos.get(id);
    if (!gizmo) return;
    gizmo.joint.getWorldPosition(this.targetLook);
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this.targetCamera.copy(this.targetLook).add(direction.multiplyScalar(0.52));
    this.cameraTween = true;
  }

  focusAxis(id: number): void {
    const gizmo = this.gizmos.get(id);
    if (!gizmo) return;
    this.activeAxisView = id;
    const center = gizmo.joint.getWorldPosition(new THREE.Vector3());
    const axis = ((gizmo.joint as THREE.Object3D & { axis?: THREE.Vector3 }).axis ?? LOCAL_RING_AXIS).clone();
    axis.applyQuaternion(gizmo.joint.getWorldQuaternion(new THREE.Quaternion())).normalize();
    const radius = id <= 3 ? 0.42 : 0.31;
    this.targetLook.copy(center);
    this.targetCamera.copy(center).add(axis.multiplyScalar(radius));
    this.camera.up.set(0, 1, 0);
    if (Math.abs(axis.dot(this.camera.up)) > 0.94) this.camera.up.set(0, 0, 1);
    this.cameraTween = true;
    this.updateGizmos();
  }

  fit(): void {
    this.leaveAxisView();
    if (!this.robot) {
      this.targetLook.set(0, 0.25, 0);
      this.targetCamera.set(0.82, 0.62, 0.84);
      this.cameraTween = true;
      return;
    }
    const box = new THREE.Box3().setFromObject(this.robot);
    const center = box.getCenter(new THREE.Vector3());
    const dimensions = box.getSize(new THREE.Vector3());
    const maxDimension = Math.max(dimensions.x, dimensions.y, dimensions.z);
    const fitDistance = (maxDimension / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)))) * 1.75;
    this.targetLook.set(center.x, Math.max(0.2, center.y), center.z);
    this.targetCamera.copy(this.targetLook).add(new THREE.Vector3(0.68, 0.52, 0.72).normalize().multiplyScalar(Math.max(0.82, fitDistance)));
    this.cameraTween = true;
  }

  toggleRiskOverlay(): boolean {
    this.riskVisible = !this.riskVisible;
    this.updateRiskOverlay(this.latestSafety);
    this.refreshMaterialEffects();
    return this.riskVisible;
  }

  dispose(): void {
    cancelAnimationFrame(this.animationFrame);
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener('pointerdown', this.handlePointerDown);
    this.canvas.removeEventListener('pointermove', this.handlePointerMove);
    this.canvas.removeEventListener('pointerup', this.handlePointerUp);
    this.canvas.removeEventListener('pointercancel', this.handlePointerUp);
    this.canvas.removeEventListener('dblclick', this.handleDoubleClick);
    this.controls.dispose();
    this.renderer.dispose();
  }

  private buildLighting(): void {
    this.scene.add(new THREE.AmbientLight('#dffcff', 3.4));
    this.scene.add(new THREE.HemisphereLight('#c7ffff', '#10252c', 4.2));
    const key = new THREE.DirectionalLight('#fffaf0', 5.8);
    key.position.set(0.8, 1.25, 0.85);
    this.scene.add(key);
    const frontFill = new THREE.DirectionalLight('#75eeff', 4.4);
    frontFill.position.set(-0.55, 0.5, 1.1);
    this.scene.add(frontFill);
    const rearFill = new THREE.DirectionalLight('#8dffcf', 3.1);
    rearFill.position.set(0.25, 0.72, -1.1);
    this.scene.add(rearFill);
    const baseGlow = new THREE.PointLight('#25dfff', 12, 1.4, 2);
    baseGlow.position.set(0, 0.16, 0.08);
    this.scene.add(baseGlow);
  }

  private buildHologramStage(): void {
    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(0.9, 96),
      new THREE.MeshStandardMaterial({ color: '#06151b', emissive: '#06252c', emissiveIntensity: 0.8, roughness: 0.72, metalness: 0.28 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    floor.position.y = -0.003;
    this.hologramRoot.add(floor);
    for (const radius of [0.22, 0.44, 0.68]) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(radius, 0.0012, 4, 128),
        new THREE.MeshBasicMaterial({ color: '#47eaff', transparent: true, opacity: 0.16, depthWrite: false }),
      );
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 0.004;
      this.hologramRoot.add(ring);
    }
    this.scanRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.68, 0.0015, 5, 128),
      new THREE.MeshBasicMaterial({ color: '#6ffaff', transparent: true, opacity: 0.25, depthWrite: false }),
    );
    this.scanRing.rotation.x = Math.PI / 2;
    this.scanRing.renderOrder = 8;
    this.hologramRoot.add(this.scanRing);

    this.riskLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color: '#ffc857', transparent: true, opacity: 0.95, depthTest: false, depthWrite: false }),
    );
    this.riskLine.renderOrder = 30;
    const pointMaterial = new THREE.MeshBasicMaterial({ color: '#ffc857', transparent: true, opacity: 0.96, depthTest: false, depthWrite: false });
    this.riskPoints = [
      new THREE.Mesh(new THREE.SphereGeometry(0.009, 16, 10), pointMaterial),
      new THREE.Mesh(new THREE.SphereGeometry(0.009, 16, 10), pointMaterial.clone()),
    ];
    for (const point of this.riskPoints) point.renderOrder = 31;
    const burstCanvas = document.createElement('canvas');
    burstCanvas.width = burstCanvas.height = 256;
    const burstContext = burstCanvas.getContext('2d');
    if (burstContext) {
      const gradient = burstContext.createRadialGradient(128, 128, 2, 128, 128, 118);
      gradient.addColorStop(0, 'rgba(255,255,255,1)');
      gradient.addColorStop(0.08, 'rgba(255,55,82,1)');
      gradient.addColorStop(0.32, 'rgba(255,30,65,0.38)');
      gradient.addColorStop(1, 'rgba(255,30,65,0)');
      burstContext.fillStyle = gradient;
      burstContext.fillRect(0, 0, 256, 256);
      burstContext.translate(128, 128);
      burstContext.strokeStyle = 'rgba(255,92,112,0.72)';
      burstContext.lineWidth = 2;
      for (let index = 0; index < 18; index += 1) {
        const angle = index / 18 * Math.PI * 2;
        burstContext.beginPath();
        burstContext.moveTo(Math.cos(angle) * 28, Math.sin(angle) * 28);
        burstContext.lineTo(Math.cos(angle) * (82 + (index % 3) * 16), Math.sin(angle) * (82 + (index % 3) * 16));
        burstContext.stroke();
      }
    }
    const burstTexture = new THREE.CanvasTexture(burstCanvas);
    burstTexture.colorSpace = THREE.SRGBColorSpace;
    this.riskBurst = new THREE.Sprite(new THREE.SpriteMaterial({ map: burstTexture, color: '#ff4968', transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.riskBurst.scale.set(0.12, 0.12, 1);
    this.riskBurst.renderOrder = 32;
    this.riskOverlay.add(this.riskLine, ...this.riskPoints, this.riskBurst);
    this.riskOverlay.visible = false;
  }

  private prepareRobotMaterials(robot: URDFRobot): void {
    robot.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      let ancestor: THREE.Object3D | null = child.parent;
      while (ancestor) {
        if ((ancestor as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) {
          child.visible = false;
          return;
        }
        ancestor = ancestor.parent;
      }
      child.castShadow = false;
      child.receiveShadow = false;
      const originals = Array.isArray(child.material) ? child.material : [child.material];
      const tuned = originals.map(source => {
        const material = source.clone();
        if (material instanceof THREE.MeshStandardMaterial) {
          material.roughness = Math.min(0.68, Math.max(0.34, material.roughness));
          material.metalness = Math.max(0.16, material.metalness);
          material.emissive.copy(material.color).lerp(new THREE.Color('#143941'), 0.2);
          material.emissiveIntensity = 0.48;
        } else if (material instanceof THREE.MeshPhongMaterial) {
          material.shininess = Math.max(38, material.shininess);
          material.emissive.copy(material.color).lerp(new THREE.Color('#123a42'), 0.24);
          material.emissiveIntensity = 0.58;
        }
        return material;
      });
      child.material = Array.isArray(child.material) ? tuned : tuned[0];
      for (const material of tuned) this.rememberMaterial(material, this.materialBaselines);
    });
  }

  private preparePreviewMaterials(robot: URDFRobot): void {
    robot.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      let ancestor: THREE.Object3D | null = child.parent;
      while (ancestor) {
        if ((ancestor as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) {
          child.visible = false;
          return;
        }
        ancestor = ancestor.parent;
      }
      const ghost = new THREE.MeshStandardMaterial({
        color: '#34eaff', emissive: '#079fbd', emissiveIntensity: 1.35,
        transparent: true, opacity: 0.2, roughness: 0.28, metalness: 0.08,
        depthWrite: false, side: THREE.DoubleSide,
      });
      child.material = ghost;
      child.renderOrder = 7;
      this.rememberMaterial(ghost, this.previewMaterialBaselines);
    });
  }

  private rememberMaterial(material: THREE.Material, target: Map<THREE.Material, MaterialBaseline>): void {
    const colored = material as THREE.Material & { color?: THREE.Color; emissive?: THREE.Color; emissiveIntensity?: number };
    target.set(material, {
      color: colored.color?.clone(),
      emissive: colored.emissive?.clone(),
      emissiveIntensity: colored.emissiveIntensity,
      opacity: material.opacity,
      transparent: material.transparent,
    });
  }

  private collectVisualLinkMeshes(robot: URDFRobot, target: Map<string, THREE.Mesh[]>): void {
    target.clear();
    const linkNames = new Map<THREE.Object3D, string>();
    for (const [name, link] of Object.entries(robot.links)) linkNames.set(link, name);
    robot.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      let owner: THREE.Object3D | null = child;
      let collision = false;
      while (owner && !linkNames.has(owner)) {
        if ((owner as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) collision = true;
        owner = owner.parent;
      }
      if (!owner || collision) return;
      const name = linkNames.get(owner)!;
      const bucket = target.get(name) ?? [];
      bucket.push(child);
      target.set(name, bucket);
    });
  }

  private collectLinkMeshes(robot: URDFRobot): void {
    this.linkMeshes.clear();
    this.collisionBodies.length = 0;
    const linkNames = new Map<THREE.Object3D, string>();
    for (const [name, link] of Object.entries(robot.links)) linkNames.set(link, name);
    robot.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      let owner: THREE.Object3D | null = child;
      let collision = false;
      while (owner && !linkNames.has(owner)) {
        if ((owner as THREE.Object3D & { isURDFCollider?: boolean }).isURDFCollider) collision = true;
        owner = owner.parent;
      }
      if (!owner) return;
      const name = linkNames.get(owner)!;
      if (collision) {
        child.visible = false;
        child.geometry.computeBoundingBox();
        const bvh = new MeshBVH(child.geometry, { indirect: true, verbose: false });
        (child.geometry as THREE.BufferGeometry & { boundsTree?: MeshBVH }).boundsTree = bvh;
        this.collisionBodies.push({
          linkName: name,
          order: this.linkOrder(name),
          mesh: child,
          bvh,
        });
        return;
      }
      const bucket = this.linkMeshes.get(name) ?? [];
      const axisJointId = this.linkOrder(name);
      if (axisJointId >= 1 && axisJointId <= 6) child.userData.axisJointId = axisJointId;
      bucket.push(child);
      this.linkMeshes.set(name, bucket);
    });
  }

  private setPreviewRobotPose(angles: Record<number, number>, gripperWidthMm: number): void {
    if (!this.previewRobot) return;
    for (let id = 1; id <= 6; id += 1) this.previewRobot.setJointValue(`joint${id}`, THREE.MathUtils.degToRad(angles[id] ?? 0));
    this.setRobotGripper(this.previewRobot, gripperWidthMm);
    this.previewRobot.updateMatrixWorld(true);
    this.previewRobot.visible = true;
  }

  private applyPreviewPose(): void {
    this.setPreviewRobotPose(this.previewAngles, this.previewGripMm);
    this.updatePreviewVisibility();
  }

  private updatePreviewVisibility(): void {
    if (!this.previewRobot) return;
    const delta = Math.max(
      Math.abs(this.previewGripMm - this.displayedGripMm) / 10,
      ...Array.from({ length: 6 }, (_, index) => Math.abs((this.previewAngles[index + 1] ?? 0) - (this.displayedAngles[index + 1] ?? 0))),
    );
    this.previewRobot.visible = delta > 0.05 || (this.targetSafety.severity === 'blocked' && performance.now() < this.collisionPreviewHoldUntil);
  }

  private linkOrder(name: string): number {
    if (name === 'base_link') return 0;
    const match = /^link(\d+)$/.exec(name);
    if (match) return Number(match[1]);
    // These links form one attached tool assembly. Their intended mating
    // surfaces overlap or run within manufacturing clearance in the URDF.
    if (name === 'end_link' || name.startsWith('finger_')) return 6;
    return 99;
  }

  private buildJointGizmos(): void {
    if (!this.robot || this.specs.size === 0) return;
    for (const gizmo of this.gizmos.values()) {
      this.hologramRoot.remove(gizmo.group);
      gizmo.labelTexture.dispose();
    }
    this.gizmos.clear();
    for (let id = 1; id <= 6; id += 1) {
      const spec = this.specs.get(id);
      const joint = this.robot.joints[`joint${id}`] as THREE.Object3D | undefined;
      if (!spec || !joint) continue;
      const radius = id <= 3 ? 0.071 : 0.052;
      const group = new THREE.Group();
      group.renderOrder = 12;
      group.add(
        this.makeArc(spec.lowerDeg, spec.upperDeg, radius, '#217987', 0.5),
        this.makeArc(spec.safeLowerDeg, spec.safeUpperDeg, radius * 1.04, '#70f7ff', 0.9),
      );
      const actualNeedle = this.makeNeedle(radius * 0.84, '#f4fbff', 0.85);
      const targetNeedle = this.makeNeedle(radius, '#b9ff45', 1);
      group.add(actualNeedle, targetNeedle);
      const targetHandle = new THREE.Mesh(
        new THREE.SphereGeometry(id <= 3 ? 0.008 : 0.0065, 16, 10),
        new THREE.MeshBasicMaterial({ color: '#b9ff45', transparent: true, opacity: 0.95, depthTest: false }),
      );
      targetHandle.renderOrder = 15;
      group.add(targetHandle);
      const hitArea = new THREE.Mesh(
        new THREE.TorusGeometry(radius, id <= 3 ? 0.018 : 0.014, 6, 64),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.002, depthWrite: false, depthTest: false }),
      );
      hitArea.userData.jointId = id;
      hitArea.renderOrder = 20;
      group.add(hitArea);
      const labelCanvas = document.createElement('canvas');
      labelCanvas.width = 512;
      labelCanvas.height = 112;
      const labelTexture = new THREE.CanvasTexture(labelCanvas);
      labelTexture.colorSpace = THREE.SRGBColorSpace;
      labelTexture.minFilter = THREE.LinearFilter;
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: labelTexture, transparent: true, depthTest: false, depthWrite: false }));
      label.scale.set(0.17, 0.037, 1);
      label.position.set(0, radius + 0.045, 0);
      label.renderOrder = 16;
      group.add(label);
      const gizmo: JointGizmo = { id, group, joint, actualNeedle, targetNeedle, targetHandle, hitArea, label, labelCanvas, labelTexture, labelText: '', radius };
      this.gizmos.set(id, gizmo);
      this.hologramRoot.add(group);
    }
    this.updateGizmos();
  }

  private buildGripperGizmo(): void {
    if (!this.endLink || this.gripperGizmo) return;
    const group = new THREE.Group();
    const measure = new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(Array.from({ length: 6 }, () => new THREE.Vector3())),
      new THREE.LineBasicMaterial({ color: '#b9ff45', transparent: true, opacity: 0.9, depthTest: false, depthWrite: false }),
    );
    measure.renderOrder = 15;
    group.add(measure);
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 112;
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter;
    const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false }));
    label.scale.set(0.18, 0.039, 1);
    label.position.set(0.065, 0, 0);
    label.renderOrder = 16;
    group.add(label);
    this.gripperGizmo = { group, measure, label, canvas, texture, text: '' };
    this.hologramRoot.add(group);
    this.updateGripperGizmo();
  }

  private makeArc(fromDeg: number, toDeg: number, radius: number, color: string, opacity: number): THREE.Line {
    const steps = Math.max(24, Math.ceil(Math.abs(toDeg - fromDeg) / 3));
    const points: THREE.Vector3[] = [];
    for (let index = 0; index <= steps; index += 1) {
      const angle = THREE.MathUtils.degToRad(fromDeg + (toDeg - fromDeg) * index / steps);
      points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0));
    }
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false }),
    );
    line.renderOrder = 13;
    return line;
  }

  private makeNeedle(radius: number, color: string, opacity: number): THREE.Line {
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(radius, 0, 0)]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false }),
    );
    line.renderOrder = 14;
    return line;
  }

  private setNeedle(line: THREE.Line, degrees: number, radius: number): void {
    const position = line.geometry.getAttribute('position') as THREE.BufferAttribute;
    const angle = THREE.MathUtils.degToRad(degrees);
    position.setXYZ(1, Math.cos(angle) * radius, Math.sin(angle) * radius, 0);
    position.needsUpdate = true;
  }

  private updateGizmos(): void {
    if (!this.robot) return;
    this.robot.updateMatrixWorld(true);
    for (const [id, gizmo] of this.gizmos) {
      gizmo.group.visible = this.activeAxisView === null || this.activeAxisView === id;
      if (!gizmo.group.visible) continue;
      const state = this.jointState.get(id) ?? { id, actualDeg: this.displayedAngles[id] ?? 0, targetDeg: this.previewAngles[id] ?? 0, enabled: false };
      gizmo.joint.getWorldPosition(gizmo.group.position);
      const axis = ((gizmo.joint as THREE.Object3D & { axis?: THREE.Vector3 }).axis ?? LOCAL_RING_AXIS).clone();
      axis.applyQuaternion(gizmo.joint.getWorldQuaternion(new THREE.Quaternion())).normalize();
      gizmo.group.quaternion.setFromUnitVectors(LOCAL_RING_AXIS, axis);
      this.setNeedle(gizmo.actualNeedle, state.actualDeg, gizmo.radius * 0.82);
      this.setNeedle(gizmo.targetNeedle, state.targetDeg, gizmo.radius);
      const targetAngle = THREE.MathUtils.degToRad(state.targetDeg);
      gizmo.targetHandle.position.set(Math.cos(targetAngle) * gizmo.radius, Math.sin(targetAngle) * gizmo.radius, 0);
      const active = state.enabled || this.hoveredJoint === id || this.drag?.id === id;
      (gizmo.targetNeedle.material as THREE.LineBasicMaterial).opacity = active ? 1 : 0.58;
      (gizmo.targetHandle.material as THREE.MeshBasicMaterial).opacity = active ? 1 : 0.68;
      gizmo.targetHandle.scale.setScalar(this.drag?.id === id ? 1.55 : this.hoveredJoint === id ? 1.28 : 1);
      this.updateLabel(gizmo, `J${id}  ${state.actualDeg.toFixed(1)}°  →  ${state.targetDeg.toFixed(1)}°`, active);
      gizmo.label.visible = this.drag?.id === id || performance.now() < (this.labelVisibleUntil.get(id) ?? 0);
    }
    this.updateGripperGizmo();
  }

  private updateGripperGizmo(): void {
    if (!this.gripperGizmo || !this.endLink) return;
    const gizmo = this.gripperGizmo;
    this.endLink.getWorldPosition(gizmo.group.position);
    gizmo.group.quaternion.copy(this.endLink.getWorldQuaternion(new THREE.Quaternion()));
    const targetHalf = Math.max(0.006, this.previewGripMm / 2000);
    const position = gizmo.measure.geometry.getAttribute('position') as THREE.BufferAttribute;
    position.setXYZ(0, 0, -targetHalf, 0);
    position.setXYZ(1, 0, targetHalf, 0);
    position.setXYZ(2, -0.012, -targetHalf, 0);
    position.setXYZ(3, 0.012, -targetHalf, 0);
    position.setXYZ(4, -0.012, targetHalf, 0);
    position.setXYZ(5, 0.012, targetHalf, 0);
    position.needsUpdate = true;
    const text = `J7  ${this.displayedGripMm.toFixed(0)} mm  →  ${this.previewGripMm.toFixed(0)} mm`;
    if (gizmo.text === text) return;
    gizmo.text = text;
    const context = gizmo.canvas.getContext('2d');
    if (!context) return;
    context.clearRect(0, 0, gizmo.canvas.width, gizmo.canvas.height);
    context.fillStyle = 'rgba(4, 20, 26, 0.9)';
    context.strokeStyle = '#b9ff45';
    context.lineWidth = 3;
    context.fillRect(2, 2, 508, 108);
    context.strokeRect(2, 2, 508, 108);
    context.fillStyle = '#efffd5';
    context.font = '600 36px ui-monospace, monospace';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(text, 256, 58);
    gizmo.texture.needsUpdate = true;
  }

  private updateLabel(gizmo: JointGizmo, text: string, active: boolean): void {
    const key = `${text}:${active}`;
    if (gizmo.labelText === key) return;
    gizmo.labelText = key;
    const context = gizmo.labelCanvas.getContext('2d');
    if (!context) return;
    context.clearRect(0, 0, gizmo.labelCanvas.width, gizmo.labelCanvas.height);
    context.fillStyle = active ? 'rgba(4, 20, 26, 0.92)' : 'rgba(4, 15, 20, 0.76)';
    context.strokeStyle = active ? '#b9ff45' : '#42cfe0';
    context.lineWidth = 3;
    context.fillRect(2, 2, 508, 108);
    context.strokeRect(2, 2, 508, 108);
    context.fillStyle = active ? '#efffd5' : '#b9f7ff';
    context.font = '600 38px ui-monospace, monospace';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(text, 256, 58);
    gizmo.labelTexture.needsUpdate = true;
  }

  private setGripper(widthMm: number): void {
    if (!this.robot) return;
    this.setRobotGripper(this.robot, widthMm);
  }

  private setRobotGripper(robot: URDFRobot, widthMm: number): void {
    const halfWidthMetres = THREE.MathUtils.clamp(widthMm / 2000, 0, 0.05);
    robot.setJointValue('finger_left', halfWidthMetres);
    robot.setJointValue('finger_right', -halfWidthMetres);
  }

  private evaluatePose(angles: Record<number, number>, gripperWidthMm: number): SafetyReport {
    if (!this.robot) return this.latestSafety;
    for (let id = 1; id <= 6; id += 1) this.robot.setJointValue(`joint${id}`, THREE.MathUtils.degToRad(angles[id] ?? 0));
    this.setGripper(gripperWidthMm);
    this.robot.updateMatrixWorld(true);

    const boxes = new Map<string, THREE.Box3>();
    for (const [name, meshes] of this.linkMeshes) {
      const box = new THREE.Box3();
      for (const mesh of meshes) box.union(new THREE.Box3().setFromObject(mesh));
      if (!box.isEmpty()) boxes.set(name, box);
    }

    let minimumClearance = Number.POSITIVE_INFINITY;
    let closestContact: SafetyReport['contact'];
    let closestLabel = '';
    let closestLinks: string[] = [];
    let exactOverlap = false;

    collisionPairs: for (let leftIndex = 0; leftIndex < this.collisionBodies.length; leftIndex += 1) {
      const left = this.collisionBodies[leftIndex];
      const leftBox = new THREE.Box3().setFromObject(left.mesh);
      for (let rightIndex = leftIndex + 1; rightIndex < this.collisionBodies.length; rightIndex += 1) {
        const right = this.collisionBodies[rightIndex];
        if (!this.shouldCheckCollisionPair(left, right)) continue;
        const rightBox = new THREE.Box3().setFromObject(right.mesh);
        const broadDistance = this.boxDistance(leftBox, rightBox);
        if (broadDistance > 0) {
          if (broadDistance < minimumClearance) {
            minimumClearance = broadDistance;
            closestLabel = `${this.linkLabel(left.linkName)} / ${this.linkLabel(right.linkName)}`;
            closestLinks = [left.linkName, right.linkName];
            closestContact = this.boxContact(leftBox, rightBox, closestLabel);
          }
          continue;
        }

        const rightToLeft = left.mesh.matrixWorld.clone().invert().multiply(right.mesh.matrixWorld);
        const overlaps = left.bvh.intersectsGeometry(right.mesh.geometry, rightToLeft);
        if (!overlaps) continue;
        const leftHit = {} as HitPointInfo;
        const rightHit = {} as HitPointInfo;
        left.bvh.closestPointToGeometry(right.mesh.geometry, rightToLeft, leftHit, rightHit, 0, 0.01);
        minimumClearance = -0.001;
        exactOverlap = true;
        closestLabel = `${this.linkLabel(left.linkName)} / ${this.linkLabel(right.linkName)}`;
        closestLinks = [left.linkName, right.linkName];
        const leftPoint = leftHit.point?.clone().applyMatrix4(left.mesh.matrixWorld) ?? leftBox.getCenter(new THREE.Vector3());
        const rightPoint = rightHit.point?.clone().applyMatrix4(right.mesh.matrixWorld) ?? rightBox.getCenter(new THREE.Vector3());
        closestContact = {
          from: leftPoint.toArray() as [number, number, number],
          to: rightPoint.toArray() as [number, number, number],
          label: closestLabel,
        };
        break collisionPairs;
      }
    }

    let minFloor = Number.POSITIVE_INFINITY;
    let floorPoint = new THREE.Vector3();
    const movingNames = ['link2', 'link3', 'link4', 'link5', 'link6', 'end_link', 'finger_left_link', 'finger_right_link'];
    for (const name of movingNames) {
      const box = boxes.get(name);
      if (!box) continue;
      if (box.min.y < minFloor) {
        minFloor = box.min.y;
        floorPoint = new THREE.Vector3((box.min.x + box.max.x) / 2, box.min.y, (box.min.z + box.max.z) / 2);
      }
    }

    const floorClearance = minFloor - 0.006;
    if (!exactOverlap && floorClearance < minimumClearance) {
      minimumClearance = floorClearance;
      closestLabel = '机械臂 / 桌面';
      const floorLink = movingNames.find(name => boxes.get(name)?.min.y === minFloor);
      closestLinks = floorLink ? [floorLink] : [];
      closestContact = {
        from: floorPoint.toArray() as [number, number, number],
        to: [floorPoint.x, 0, floorPoint.z],
        label: closestLabel,
      };
    }

    const clearanceMm = Number.isFinite(minimumClearance) ? Math.round(minimumClearance * 1000) : 999;
    const tableOverlap = !exactOverlap && floorClearance <= 0;
    const severity: SafetyReport['severity'] = exactOverlap || tableOverlap ? 'blocked' : clearanceMm < 20 ? 'warning' : 'safe';
    const violations = severity === 'blocked'
      ? [exactOverlap ? `模型实体重合：${closestLabel}` : `模型触及桌面：${closestLabel}`]
      : severity === 'warning'
        ? [`碰撞接近：${closestLabel} 净空 ${clearanceMm} mm`]
        : [];
    return {
      safe: severity !== 'blocked',
      severity,
      violations,
      clearanceMm,
      collisionLinks: closestLinks,
      contact: closestContact,
    };
  }

  private shouldCheckCollisionPair(left: CollisionBody, right: CollisionBody): boolean {
    if (left.linkName === right.linkName) return false;
    if (left.order === 99 || right.order === 99) return false;
    if (left.order === 8 && right.order === 8) return false;
    if (Math.abs(left.order - right.order) <= 1) return false;
    return true;
  }

  private linkLabel(name: string): string {
    const labels: Record<string, string> = {
      base_link: '底座', link1: '底座关节', link2: '上臂', link3: '前臂', link4: '腕部 A',
      link5: '腕部 B', link6: '腕部 C', end_link: '夹爪座', finger_left_link: '左夹指', finger_right_link: '右夹指',
    };
    return labels[name] ?? name;
  }

  private boxDistance(left: THREE.Box3, right: THREE.Box3): number {
    const dx = Math.max(0, left.min.x - right.max.x, right.min.x - left.max.x);
    const dy = Math.max(0, left.min.y - right.max.y, right.min.y - left.max.y);
    const dz = Math.max(0, left.min.z - right.max.z, right.min.z - left.max.z);
    return Math.hypot(dx, dy, dz);
  }

  private boxContact(left: THREE.Box3, right: THREE.Box3, label: string): NonNullable<SafetyReport['contact']> {
    const from = new THREE.Vector3();
    const to = new THREE.Vector3();
    for (const axis of ['x', 'y', 'z'] as const) {
      if (left.max[axis] < right.min[axis]) {
        from[axis] = left.max[axis];
        to[axis] = right.min[axis];
      } else if (right.max[axis] < left.min[axis]) {
        from[axis] = left.min[axis];
        to[axis] = right.max[axis];
      } else {
        const middle = (Math.max(left.min[axis], right.min[axis]) + Math.min(left.max[axis], right.max[axis])) / 2;
        from[axis] = middle;
        to[axis] = middle;
      }
    }
    return {
      from: from.toArray() as [number, number, number],
      to: to.toArray() as [number, number, number],
      label,
    };
  }

  private restoreDisplayedPose(): void {
    if (!this.robot) return;
    for (let id = 1; id <= 6; id += 1) this.robot.setJointValue(`joint${id}`, THREE.MathUtils.degToRad(this.displayedAngles[id] ?? 0));
    this.setGripper(this.displayedGripMm);
    this.robot.updateMatrixWorld(true);
    this.updateGizmos();
    this.applyPreviewPose();
  }

  private updateSafety(): void {
    if (!this.safetyDirty || !this.robot) return;
    const now = performance.now();
    if (now < this.collisionPreviewHoldUntil) return;
    if (now - this.lastSafetyAt < 120) return;
    this.lastSafetyAt = now;
    this.safetyDirty = false;
    const current = this.evaluatePose(this.displayedAngles, this.displayedGripMm);
    const target = this.evaluatePose(this.previewAngles, this.previewGripMm);
    this.restoreDisplayedPose();
    this.currentSafety = { ...current, source: 'current' };
    this.targetSafety = { ...target, source: 'target' };
    const rank = { safe: 0, warning: 1, blocked: 2 } as const;
    const selected = rank[current.severity] >= rank[target.severity] ? current : target;
    const source = selected === current ? '实时姿态' : '目标姿态';
    this.latestSafety = {
      ...selected,
      source: selected === current ? 'current' : 'target',
      clearanceMm: Math.min(current.clearanceMm, target.clearanceMm),
      violations: selected.violations.map(item => `${source} · ${item}`),
    };
    this.onSafety(this.latestSafety);
    this.updateRiskOverlay(this.latestSafety);
    this.refreshMaterialEffects();
  }

  private updateRiskOverlay(report: SafetyReport): void {
    void report;
    // Collision feedback is rendered directly on the affected link meshes.
    this.riskOverlay.visible = false;
  }

  private refreshMaterialEffects(): void {
    const pulse = Math.sin(performance.now() * 0.011) * 0.5 + 0.5;
    const actualRisk = new Set(this.currentSafety.collisionLinks ?? []);
    const targetRisk = new Set(this.targetSafety.collisionLinks ?? []);
    for (const [linkName, meshes] of this.linkMeshes) {
      const jointId = this.linkOrder(linkName);
      const stress = jointId >= 1 && jointId <= 6 ? (this.stressByJoint.get(jointId) ?? 0) : 0;
      const collision = this.riskVisible && actualRisk.has(linkName) ? this.currentSafety.severity : 'safe';
      for (const mesh of meshes) this.tintMesh(mesh, collision, stress, false, pulse);
    }
    for (const [linkName, meshes] of this.previewLinkMeshes) {
      const collision = this.riskVisible && targetRisk.has(linkName) ? this.targetSafety.severity : 'safe';
      for (const mesh of meshes) this.tintMesh(mesh, collision, 0, true, pulse);
    }
  }

  private tintMesh(mesh: THREE.Mesh, risk: SafetyReport['severity'], stress: number, preview: boolean, pulse: number): void {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const baselines = preview ? this.previewMaterialBaselines : this.materialBaselines;
    for (const material of materials) {
      const baseline = baselines.get(material);
      if (!baseline) continue;
      const colored = material as THREE.Material & { color?: THREE.Color; emissive?: THREE.Color; emissiveIntensity?: number };
      if (baseline.color && colored.color) colored.color.copy(baseline.color);
      if (baseline.emissive && colored.emissive) colored.emissive.copy(baseline.emissive);
      colored.emissiveIntensity = baseline.emissiveIntensity;
      material.opacity = baseline.opacity;
      material.transparent = baseline.transparent;
      if (risk !== 'safe') {
        const color = new THREE.Color(risk === 'blocked' ? '#ff174f' : '#ffb020');
        colored.color?.lerp(color, risk === 'blocked' ? 0.9 : 0.62);
        if (colored.emissive) colored.emissive.copy(color);
        colored.emissiveIntensity = (risk === 'blocked' ? 2.7 : 1.5) + pulse * 1.6;
        material.opacity = preview ? 0.48 + pulse * 0.18 : 1;
        material.transparent = preview;
      } else if (!preview && stress > 0.22) {
        const strength = THREE.MathUtils.clamp((stress - 0.22) / 0.78, 0, 1);
        const heat = new THREE.Color().lerpColors(new THREE.Color('#ffc857'), new THREE.Color('#ff3158'), strength);
        colored.color?.lerp(heat, 0.18 + strength * 0.54);
        if (colored.emissive) colored.emissive.lerp(heat, 0.42 + strength * 0.45);
        colored.emissiveIntensity = (baseline.emissiveIntensity ?? 0) + strength * 1.7;
      }
      material.needsUpdate = true;
    }
  }

  private pointerFromEvent(event: PointerEvent | MouseEvent): void {
    const bounds = this.canvas.getBoundingClientRect();
    this.pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
  }

  private pickJoint(event: PointerEvent): number | null {
    this.pointerFromEvent(event);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.gizmos.values()].map(item => item.hitArea), false);
    return hits.length ? Number(hits[0].object.userData.jointId) : null;
  }

  private pickAxisForView(event: PointerEvent | MouseEvent): number | null {
    this.pointerFromEvent(event);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const gizmoHits = this.raycaster.intersectObjects([...this.gizmos.values()].map(item => item.hitArea), false);
    if (gizmoHits.length) return Number(gizmoHits[0].object.userData.jointId);
    const meshes = [...this.linkMeshes.values()].flat();
    const modelHits = this.raycaster.intersectObjects(meshes, false);
    return modelHits.length ? Number(modelHits[0].object.userData.axisJointId) || null : null;
  }

  private handlePointerDown = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    const id = this.pickJoint(event);
    if (!id) return;
    const state = this.jointState.get(id);
    if (!state) return;
    event.preventDefault();
    this.canvas.setPointerCapture(event.pointerId);
    this.controls.enabled = false;
    this.drag = { id, pointerId: event.pointerId, startX: event.clientX, startDegrees: state.targetDeg, moved: false };
    this.labelVisibleUntil.set(id, Number.POSITIVE_INFINITY);
    this.hoveredJoint = id;
    this.updateGizmos();
  };

  private handlePointerMove = (event: PointerEvent): void => {
    if (this.drag) {
      if (event.pointerId !== this.drag.pointerId) return;
      const spec = this.specs.get(this.drag.id);
      if (!spec) return;
      const lower = this.hardwareLimits ? spec.safeLowerDeg : spec.lowerDeg;
      const upper = this.hardwareLimits ? spec.safeUpperDeg : spec.upperDeg;
      const span = upper - lower;
      const delta = (event.clientX - this.drag.startX) / Math.max(260, this.canvas.clientWidth) * span * 1.75;
      const degrees = THREE.MathUtils.clamp(this.drag.startDegrees + delta, lower, upper);
      this.drag.moved ||= Math.abs(event.clientX - this.drag.startX) > 2;
      if (!this.onJointInput(this.drag.id, degrees, false)) return;
      this.previewAngles[this.drag.id] = degrees;
      const state = this.jointState.get(this.drag.id);
      if (state) this.jointState.set(this.drag.id, { ...state, targetDeg: degrees });
      this.safetyDirty = true;
      this.updateGizmos();
      return;
    }
    const hovered = this.pickJoint(event);
    if (hovered !== this.hoveredJoint) {
      this.hoveredJoint = hovered;
      this.canvas.style.cursor = hovered ? 'ew-resize' : 'grab';
      this.updateGizmos();
    }
  };

  private handlePointerUp = (event: PointerEvent): void => {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    const { id, moved } = this.drag;
    const degrees = this.previewAngles[id] ?? this.drag.startDegrees;
    this.drag = null;
    this.labelVisibleUntil.set(id, moved ? performance.now() + 1500 : 0);
    this.controls.enabled = true;
    if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
    if (!moved) this.focusJoint(id);
    else this.onJointInput(id, degrees, true);
    this.updateGizmos();
  };

  private handleDoubleClick = (event: MouseEvent): void => {
    const id = this.pickAxisForView(event);
    if (id) this.focusAxis(id);
    else this.fit();
  };

  private leaveAxisView(): void {
    if (this.activeAxisView === null) return;
    this.activeAxisView = null;
    for (const gizmo of this.gizmos.values()) gizmo.group.visible = true;
  }

  private resize(): void {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const width = Math.max(1, parent.clientWidth);
    const height = Math.max(1, parent.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  private animate = (): void => {
    this.animationFrame = requestAnimationFrame(this.animate);
    if (this.cameraTween) {
      this.camera.position.lerp(this.targetCamera, 0.075);
      this.controls.target.lerp(this.targetLook, 0.075);
      if (this.camera.position.distanceTo(this.targetCamera) < 0.0015 && this.controls.target.distanceTo(this.targetLook) < 0.0015) this.cameraTween = false;
    }
    this.controls.update();
    if (this.endLink) {
      const position = this.endLink.getWorldPosition(new THREE.Vector3());
      this.onTcp(new THREE.Vector3(position.x, -position.z, position.y));
    }
    this.updateGizmos();
    this.updateSafety();
    this.updatePreviewVisibility();
    if (this.currentSafety.severity !== 'safe' || this.targetSafety.severity !== 'safe' || [...this.stressByJoint.values()].some(value => value > 0.22)) {
      this.refreshMaterialEffects();
    }
    if (this.scanRing) {
      const phase = (performance.now() * 0.00019) % 1;
      this.scanRing.position.y = 0.025 + phase * 0.81;
      const material = this.scanRing.material as THREE.MeshBasicMaterial;
      material.opacity = 0.16 + Math.sin(phase * Math.PI) * 0.34;
      material.color.set(this.latestSafety.severity === 'blocked' ? '#ff4968' : '#6ffaff');
    }
    if (this.riskBurst && this.riskOverlay.visible) {
      const pulse = 0.105 + (Math.sin(performance.now() * 0.012) * 0.5 + 0.5) * 0.045;
      this.riskBurst.scale.set(pulse, pulse, 1);
      (this.riskBurst.material as THREE.SpriteMaterial).rotation += 0.004;
    }
    this.renderer.render(this.scene, this.camera);
  };
}
