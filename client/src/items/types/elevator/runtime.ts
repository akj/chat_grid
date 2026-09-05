import { HEARING_RADIUS, type WorldItem } from '../../../state/gameState';
import { AudioEngine } from '../../../audio/audioEngine';
import { applySpatialMixToNodes, resolveSpatialMix } from '../../../audio/spatial';
import { applyAcousticLowpass, normalizeAcousticMix, type AcousticMix } from '../../../audio/acoustics';
import { ElevatorCar } from './car';

type ElevatorOutput = {
  element: HTMLAudioElement;
  source: MediaElementAudioSourceNode;
  gain: GainNode;
  occlusionFilter: BiquadFilterNode;
  panner: StereoPannerNode | null;
  onLoadedMetadata: () => void;
  onCanPlay: () => void;
  ready: boolean;
};

type ElevatorSpatialConfig = {
  range: number;
};

const CABIN_SOUND_PATH = '/sounds/elevator_inside.ogg';
const SUBSCRIBE_PRELOAD_SQUARES = 5;

/** Chooses a valid nonzero starting point within the cabin ambience file. */
export function resolveElevatorAmbienceStart(
  durationSeconds: number,
  randomValue = Math.random(),
): number {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) return 0;
  const minimumOffset = Math.min(0.01, durationSeconds / 2);
  const ratio = Math.max(0, Math.min(0.999999, randomValue));
  return minimumOffset + (durationSeconds - minimumOffset) * ratio;
}

/** Returns whether one elevator ambience source is relevant to this listener. */
export function shouldPlayElevatorAmbience(
  item: WorldItem,
  listenerPosition: { x: number; y: number; z: number },
  occupiedElevatorId: string | null,
  range: number,
): boolean {
  const car = ElevatorCar.from(item);
  if (!car) return false;
  if (occupiedElevatorId === item.id) return true;
  if (!car.doorPassesSound || !car.isAtLanding(listenerPosition.z)) return false;
  return Math.hypot(item.x - listenerPosition.x, item.y - listenerPosition.y)
    <= Math.max(1, range || HEARING_RADIUS) + SUBSCRIBE_PRELOAD_SQUARES;
}

/** Plays the built-in elevator cabin ambience for riders and open nearby cars. */
export class ElevatorAudioRuntime {
  private readonly outputs = new Map<string, ElevatorOutput>();
  private layerEnabled = true;

  constructor(
    private readonly audio: AudioEngine,
    private readonly resolveSoundUrl: (soundPath: string) => string,
    private readonly getSpatialConfig: (item: WorldItem) => ElevatorSpatialConfig,
    private readonly getOccupiedElevatorId: () => string | null,
    private readonly getDoorAcousticMix: (item: WorldItem) => AcousticMix,
  ) {}

  cleanup(itemId: string): void {
    const output = this.outputs.get(itemId);
    if (!output) return;
    output.element.pause();
    output.element.removeEventListener('loadedmetadata', output.onLoadedMetadata);
    output.element.removeEventListener('canplay', output.onCanPlay);
    output.element.src = '';
    output.source.disconnect();
    output.gain.disconnect();
    output.occlusionFilter.disconnect();
    output.panner?.disconnect();
    this.outputs.delete(itemId);
  }

  cleanupAll(): void {
    for (const itemId of Array.from(this.outputs.keys())) {
      this.cleanup(itemId);
    }
  }

  async setLayerEnabled(
    enabled: boolean,
    items: Iterable<WorldItem>,
    listenerPosition: { x: number; y: number; z: number },
  ): Promise<void> {
    this.layerEnabled = enabled;
    if (!enabled) {
      this.cleanupAll();
      return;
    }
    await this.sync(items, listenerPosition);
  }

  async sync(
    items: Iterable<WorldItem>,
    listenerPosition: { x: number; y: number; z: number },
  ): Promise<void> {
    if (!this.layerEnabled) {
      this.cleanupAll();
      return;
    }
    const validIds = new Set<string>();
    let audioCtx = this.audio.context;

    for (const item of items) {
      if (!this.shouldPlay(item, listenerPosition)) {
        this.cleanup(item.id);
        continue;
      }
      validIds.add(item.id);
      if (this.outputs.has(item.id)) continue;
      if (!audioCtx) {
        await this.audio.ensureContext();
        audioCtx = this.audio.context;
      }
      if (!audioCtx) continue;

      const element = new Audio(this.resolveSoundUrl(CABIN_SOUND_PATH));
      element.loop = true;
      element.preload = 'metadata';
      element.crossOrigin = 'anonymous';
      const source = audioCtx.createMediaElementSource(element);
      const gain = audioCtx.createGain();
      const occlusionFilter = audioCtx.createBiquadFilter();
      occlusionFilter.type = 'lowpass';
      occlusionFilter.frequency.value = 20_000;
      gain.gain.value = 0;
      let panner: StereoPannerNode | null = null;
      const destination = this.audio.getOutputDestinationNode() ?? audioCtx.destination;
      if (this.audio.supportsStereoPanner()) {
        panner = audioCtx.createStereoPanner();
        source.connect(gain).connect(occlusionFilter).connect(panner).connect(destination);
      } else {
        source.connect(gain).connect(occlusionFilter).connect(destination);
      }
      const startAtRandomOffset = (): boolean => {
        const startSeconds = resolveElevatorAmbienceStart(element.duration);
        if (startSeconds <= 0) return false;
        try {
          element.currentTime = startSeconds;
        } catch {
          return false;
        }
        const output = this.outputs.get(item.id);
        if (output) output.ready = true;
        void element.play().catch(() => {
          // A later sync can retry after browser autoplay permission is available.
        });
        return true;
      };
      const onCanPlay = () => {
        startAtRandomOffset();
      };
      const onLoadedMetadata = () => {
        if (!startAtRandomOffset()) {
          element.addEventListener('canplay', onCanPlay, { once: true });
        }
      };
      element.addEventListener('loadedmetadata', onLoadedMetadata, { once: true });
      this.outputs.set(item.id, {
        element,
        source,
        gain,
        occlusionFilter,
        panner,
        onLoadedMetadata,
        onCanPlay,
        ready: false,
      });
      element.load();
    }

    for (const itemId of Array.from(this.outputs.keys())) {
      if (!validIds.has(itemId)) this.cleanup(itemId);
    }
  }

  updateSpatialAudio(
    items: Map<string, WorldItem>,
    playerPosition: { x: number; y: number; z: number },
  ): void {
    if (!this.layerEnabled) return;
    const audioCtx = this.audio.context;
    if (!audioCtx) return;
    const occupiedElevatorId = this.getOccupiedElevatorId();

    for (const [itemId, output] of this.outputs.entries()) {
      const item = items.get(itemId);
      if (!item) {
        this.cleanup(itemId);
        continue;
      }
      const inside = occupiedElevatorId === item.id;
      const sameLanding = ElevatorCar.from(item)?.isAtLanding(playerPosition.z) ?? false;
      const spatialConfig = this.getSpatialConfig(item);
      const mix = inside
        ? resolveSpatialMix({ dx: 0, dy: 0, range: 1, nearFieldDistance: 1, nearFieldCenterPan: true })
        : sameLanding
          ? resolveSpatialMix({
              dx: item.x - playerPosition.x,
              dy: item.y - playerPosition.y,
              range: Math.max(1, spatialConfig.range || HEARING_RADIUS),
              nearFieldDistance: 1,
              nearFieldCenterPan: true,
            })
          : null;
      const acoustic = inside ? { gain: 1, lowpassHz: 20_000 } : normalizeAcousticMix(this.getDoorAcousticMix(item));
      applyAcousticLowpass(audioCtx, output.occlusionFilter, acoustic.lowpassHz);
      const transmittedMix = mix && !inside
        ? { ...mix, gain: mix.gain * acoustic.gain }
        : mix;
      applySpatialMixToNodes({
        audioCtx,
        gainNode: output.gain,
        pannerNode: output.panner,
        mix: transmittedMix,
        outputMode: this.audio.getOutputMode(),
        transition: 'target',
      });
      if (output.ready && output.element.paused) {
        void output.element.play().catch(() => {
          // Retry on a later animation frame after browser autoplay permission.
        });
      }
    }
  }

  private shouldPlay(
    item: WorldItem,
    listenerPosition: { x: number; y: number; z: number },
  ): boolean {
    const range = Math.max(1, this.getSpatialConfig(item).range || HEARING_RADIUS);
    return shouldPlayElevatorAmbience(
      item,
      listenerPosition,
      this.getOccupiedElevatorId(),
      range,
    );
  }
}
