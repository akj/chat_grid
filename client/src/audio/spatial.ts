export type SpatialMixOptions = {
  dx: number;
  dy: number;
  dz?: number;
  range: number;
  baseGain?: number;
  nearFieldDistance?: number;
  nearFieldGain?: number;
  nearFieldCenterPan?: boolean;
  directional?: {
    enabled: boolean;
    facingDeg: number;
    coneDeg?: number;
    rearGain?: number;
  };
};

export type SpatialMixResult = {
  dx: number;
  dy: number;
  dz: number;
  distance: number;
  gain: number;
};

export const SPATIAL_RAMP_SECONDS = 0.2;
export const SPATIAL_TIME_CONSTANT_SECONDS = SPATIAL_RAMP_SECONDS / 3;

export type SpatialMode = 'standard' | 'hrtf';
type SpatialOutputMode = 'stereo' | 'mono';
type SpatialScene = {
  mode: SpatialMode;
  outputMode: SpatialOutputMode;
  facingDeg: number;
  panners: Map<PannerNode, SpatialMixResult | null>;
};
const scenes = new WeakMap<BaseAudioContext, SpatialScene>();

function sceneFor(context: BaseAudioContext): SpatialScene {
  let scene = scenes.get(context);
  if (!scene) {
    scene = { mode: 'standard', outputMode: 'stereo', facingDeg: 0, panners: new Map() };
    scenes.set(context, scene);
  }
  return scene;
}

/** One renderer for all world audio. Distance and transmission remain application-owned. */
export function createSpatialPanner(context: AudioContext): PannerNode {
  const panner = context.createPanner();
  panner.rolloffFactor = 0;
  panner.coneInnerAngle = 360;
  panner.coneOuterAngle = 360;
  panner.channelCountMode = 'clamped-max';
  sceneFor(context).panners.set(panner, null);
  updateSpatialPanner(panner, null);
  return panner;
}

export function disconnectSpatialPanner(panner: PannerNode | null | undefined): void {
  if (!panner) return;
  sceneFor(panner.context).panners.delete(panner);
  panner.disconnect();
}

/** Sources are listener-relative, in compass coordinates: +y north, +z up. */
export function updateSpatialPanner(panner: PannerNode, mix: SpatialMixResult | null): void {
  const scene = sceneFor(panner.context);
  scene.panners.set(panner, mix);
  const model = scene.outputMode === 'stereo' && scene.mode === 'hrtf' ? 'HRTF' : 'equalpower';
  if (panner.panningModel !== model) panner.panningModel = model;
  // Mono also downmixes stereo media before positioning, while retaining the HRTF preference.
  const channelCount = scene.outputMode === 'mono' ? 1 : 2;
  if (panner.channelCount !== channelCount) panner.channelCount = channelCount;
  const angle = scene.facingDeg * Math.PI / 180;
  const centered = !mix || (mix.dx === 0 && mix.dy === 0 && mix.dz === 0) || scene.outputMode === 'mono';
  // Keep the native listener fixed and rotate sources into its frame. This uses
  // the same smoothed AudioParams in every browser, including Firefox.
  const x = centered ? 0 : mix.dx * Math.cos(angle) - mix.dy * Math.sin(angle);
  const y = centered ? 0 : mix.dz;
  const z = centered ? -1 : -(mix.dx * Math.sin(angle) + mix.dy * Math.cos(angle));
  const now = panner.context.currentTime;
  panner.positionX.setTargetAtTime(x, now, SPATIAL_TIME_CONSTANT_SECONDS);
  panner.positionY.setTargetAtTime(y, now, SPATIAL_TIME_CONSTANT_SECONDS);
  panner.positionZ.setTargetAtTime(z, now, SPATIAL_TIME_CONSTANT_SECONDS);
}

/** Updates existing sources as well as the defaults for sources created later. */
export function configureSpatialAudio(
  context: AudioContext, mode: SpatialMode, outputMode: SpatialOutputMode, facingDeg: number,
): void {
  const scene = sceneFor(context);
  // Standard audio stays compass-aligned; the caller retains the actual player heading.
  const heading = mode === 'hrtf' ? normalizeDegrees(facingDeg) : 0;
  if (scene.mode === mode && scene.outputMode === outputMode && scene.facingDeg === heading) return;
  scene.mode = mode;
  scene.outputMode = outputMode;
  scene.facingDeg = heading;
  for (const [panner, mix] of scene.panners) updateSpatialPanner(panner, mix);
}

type ApplySpatialNodeOptions = {
  audioCtx: AudioContext;
  gainNode: GainNode;
  pannerNode: PannerNode | null;
  mix: SpatialMixResult | null;
  transition: 'linear' | 'target';
};

/** Applies shared gain smoothing and 3D positioning without a second attenuation curve. */
export function applySpatialMixToNodes(options: ApplySpatialNodeOptions): void {
  const { audioCtx, gainNode, pannerNode, mix, transition } = options;
  const gainValue = mix?.gain ?? 0;
  if (transition === 'linear') {
    gainNode.gain.cancelScheduledValues(audioCtx.currentTime);
    gainNode.gain.linearRampToValueAtTime(gainValue, audioCtx.currentTime + SPATIAL_RAMP_SECONDS);
  } else {
    gainNode.gain.setTargetAtTime(gainValue, audioCtx.currentTime, SPATIAL_TIME_CONSTANT_SECONDS);
  }
  if (pannerNode) updateSpatialPanner(pannerNode, mix);
}

type DirectionalProfile = {
  attenuationFactor: number;
  offAxisRatio: number;
};

export function resolveSpatialMix(options: SpatialMixOptions): SpatialMixResult | null {
  const {
    dx,
    dy,
    dz = 0,
    range,
    baseGain = 1,
    nearFieldDistance,
    nearFieldGain = 1,
    nearFieldCenterPan = false,
  } = options;
  if (!(range > 0)) {
    return null;
  }

  const distance = Math.hypot(dx, dy, dz);
  let effectiveRange = range;
  if (options.directional?.enabled) {
    const directionalProfile = resolveDirectionalProfile(dx, dy, options.directional);
    effectiveRange = Math.max(0.01, range * directionalProfile.attenuationFactor);
  }

  if (distance > effectiveRange) {
    return null;
  }

  const volumeRatio = Math.max(0, 1 - distance / effectiveRange);
  const shapedVolume = volumeRatio * volumeRatio * (3 - 2 * volumeRatio);
  let gain = baseGain * shapedVolume;

  if (nearFieldDistance !== undefined && distance < nearFieldDistance) {
    gain = baseGain * nearFieldGain;
  }

  const centered = nearFieldCenterPan && nearFieldDistance !== undefined && distance < nearFieldDistance;
  return { distance, gain, dx: centered ? 0 : dx, dy: centered ? 0 : dy, dz: centered ? 0 : dz };
}

export function resolveDirectionalMuffleRatio(
  dx: number,
  dy: number,
  directional: SpatialMixOptions['directional'],
): number {
  if (!directional?.enabled) return 0;
  return resolveDirectionalProfile(dx, dy, directional).offAxisRatio;
}

export function normalizeDegrees(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const wrapped = value % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

/** Computes compass bearing from source to listener where 0 is north and 90 is east. */
function bearingFromSourceToListener(dx: number, dy: number): number {
  // 0 degrees is north (+y), 90 is east (+x), matching screen-reader compass wording.
  const degrees = Math.atan2(dx, dy) * (180 / Math.PI);
  return normalizeDegrees(degrees);
}

/** Returns shortest absolute angular difference in degrees on a circle. */
function angularDifferenceDeg(a: number, b: number): number {
  const raw = Math.abs(normalizeDegrees(a) - normalizeDegrees(b));
  return raw > 180 ? 360 - raw : raw;
}

/** Computes directional attenuation profile based on listener angle vs source facing. */
function resolveDirectionalProfile(
  dx: number,
  dy: number,
  directional: NonNullable<SpatialMixOptions['directional']>,
): DirectionalProfile {
  const coneDeg = Math.max(1, Math.min(359, directional.coneDeg ?? 120));
  const rearGain = Math.max(0, Math.min(1, directional.rearGain ?? 0.5));
  const facingDeg = normalizeDegrees(directional.facingDeg);
  // `dx/dy` are listener-relative source coords in current callers, so invert to get source->listener bearing.
  const bearingDeg = bearingFromSourceToListener(-dx, -dy);
  const diff = angularDifferenceDeg(facingDeg, bearingDeg);
  const halfCone = coneDeg / 2;
  if (diff <= halfCone) {
    return { attenuationFactor: 1, offAxisRatio: 0 };
  }
  const span = Math.max(1, 180 - halfCone);
  const linearRatio = Math.max(0, Math.min(1, (diff - halfCone) / span));
  const offAxisRatio = linearRatio * linearRatio * (3 - 2 * linearRatio);
  return {
    attenuationFactor: 1 - offAxisRatio * (1 - rearGain),
    offAxisRatio,
  };
}
