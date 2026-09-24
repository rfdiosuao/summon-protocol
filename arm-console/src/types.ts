export type ArmMode = 'simulation' | 'hardware';

export interface JointSpec {
  id: number;
  modelJoint: string | null;
  label: string;
  shortLabel: string;
  description: string;
  lowerDeg: number;
  upperDeg: number;
  safeLowerDeg: number;
  safeUpperDeg: number;
  maxTorqueNm: number;
}

export interface JointTelemetry {
  id: number;
  actualDeg: number;
  targetDeg: number;
  commandedDeg: number;
  velocityDps: number;
  torqueNm: number;
  gravityTorqueNm: number;
  gravityAssistDeg: number;
  gravityFeedforwardNm: number;
  stressRatio: number;
  mosTempC: number | null;
  rotorTempC: number | null;
  enabled: boolean;
  moving: boolean;
  statusCode: number;
  fault: string | null;
}

export interface GripperState {
  widthMm: number;
  targetMm: number;
  calibrated: boolean;
  motorDegrees: number;
}

export interface ArmState {
  mode: ArmMode;
  connected: boolean;
  hardwareAllowed: boolean;
  port: string;
  baud: number;
  sequence: number;
  timestamp: string;
  fault: string | null;
  feedbackHz: number;
  feedbackLatencyMs: number;
  sampleAgeMs: number;
  gravityCompensation: {
    available: boolean;
    active: boolean;
    transitionProgress: number;
    message: string | null;
  };
  motionGravityAssist: { available: boolean; enabled: boolean; active: boolean; maxBiasDeg: number; maxFeedforwardNm: number; preparedAxes: number[] };
  faultDiagnostics?: { feedforwardInhibited: boolean; samples: Array<Record<string, unknown>> };
  joints: JointTelemetry[];
  gripper: GripperState;
}

export interface ConsoleConfig {
  jointSpecs: JointSpec[];
  poses: Record<string, { joints: Record<string, number>; gripperMm: number }>;
  defaultSpeedDps: number;
  modelUrl: string;
}

export interface ApiErrorBody {
  error?: { code?: string; message?: string };
}
