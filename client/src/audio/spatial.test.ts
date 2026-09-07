import { describe, expect, it, vi } from 'vitest';
import { configureSpatialAudio, createSpatialPanner, disconnectSpatialPanner, resolveSpatialMix, updateSpatialPanner } from './spatial';

function param(initial = 0) {
  return { value: initial, setTargetAtTime: vi.fn(function (this: { value: number }, value: number) { this.value = value; }) };
}
function context() {
  const ctx = {
    currentTime: 0,
    listener: {
      forwardX: param(), forwardY: param(), forwardZ: param(-1),
      upX: param(), upY: param(1), upZ: param(),
    },
    createPanner: () => ({
      context: ctx, positionX: param(), positionY: param(), positionZ: param(),
      disconnect: vi.fn(),
    }),
  };
  return ctx as unknown as AudioContext;
}

describe('shared spatial renderer', () => {
  it('switches existing and future sources without browser distance attenuation', () => {
    const ctx = context();
    const panner = createSpatialPanner(ctx);
    updateSpatialPanner(panner, resolveSpatialMix({ dx: 4, dy: 2, dz: 3, range: 15 }));
    expect(panner.panningModel).toBe('equalpower');
    expect(panner.rolloffFactor).toBe(0);
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 0);
    expect(panner.panningModel).toBe('HRTF');
    expect(createSpatialPanner(ctx).panningModel).toBe('HRTF');
    expect([panner.positionX.value, panner.positionY.value, panner.positionZ.value]).toEqual([4, 3, -2]);
  });

  it('rotates only the HRTF listener and resets standard audio to north', () => {
    const ctx = context();
    const panner = createSpatialPanner(ctx);
    updateSpatialPanner(panner, resolveSpatialMix({ dx: 5, dy: 0, range: 15 }));
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 90);
    expect(ctx.listener.forwardX.value).toBeCloseTo(1);
    expect(ctx.listener.forwardZ.value).toBeCloseTo(0);
    expect(panner.positionX.value).toBe(5);
    configureSpatialAudio(ctx, 'standard', 'stereo', 270);
    expect(ctx.listener.forwardX.value).toBeCloseTo(0);
    expect(ctx.listener.forwardZ.value).toBeCloseTo(-1);
    expect(panner.positionX.value).toBe(5);
  });

  it('centers co-located sounds through turns and restores spatial audio after mono', () => {
    const ctx = context();
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 90);
    const held = createSpatialPanner(ctx);
    updateSpatialPanner(held, resolveSpatialMix({ dx: 0, dy: 0, range: 15 }));
    expect(held.positionX.value).toBeCloseTo(1);
    expect(held.positionZ.value).toBeCloseTo(0);
    const remote = createSpatialPanner(ctx);
    updateSpatialPanner(remote, resolveSpatialMix({ dx: -4, dy: -6, range: 15 }));
    configureSpatialAudio(ctx, 'hrtf', 'mono', 90);
    expect(remote.panningModel).toBe('equalpower');
    expect(remote.channelCount).toBe(1);
    expect(remote.positionX.value).toBeCloseTo(1);
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 90);
    expect(remote.panningModel).toBe('HRTF');
    expect(remote.channelCount).toBe(2);
    expect(remote.positionX.value).toBe(-4);
    expect(remote.positionZ.value).toBe(6);
  });

  it('supports turning and HRTF changes when the listener only exposes setOrientation', () => {
    const ctx = context();
    const setOrientation = vi.fn();
    Object.defineProperty(ctx, 'listener', { value: { setOrientation } });
    const panner = createSpatialPanner(ctx);
    updateSpatialPanner(panner, resolveSpatialMix({ dx: 5, dy: 0, range: 15 }));

    for (const heading of [0, 90, 180, 270, 45]) {
      expect(() => configureSpatialAudio(ctx, 'hrtf', 'stereo', heading)).not.toThrow();
    }
    expect(setOrientation).toHaveBeenLastCalledWith(
      Math.sin(Math.PI / 4), 0, -Math.cos(Math.PI / 4), 0, 1, 0,
    );
    expect(() => configureSpatialAudio(ctx, 'hrtf', 'stereo', 45)).not.toThrow();
    expect(panner.panningModel).toBe('HRTF');
    expect(createSpatialPanner(ctx).panningModel).toBe('HRTF');
    configureSpatialAudio(ctx, 'standard', 'stereo', 45);
    expect(panner.panningModel).toBe('equalpower');
  });

  it('stops updating disposed sources', () => {
    const ctx = context();
    const panner = createSpatialPanner(ctx);
    disconnectSpatialPanner(panner);
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 180);
    expect(panner.disconnect).toHaveBeenCalledOnce();
    expect(panner.panningModel).toBe('equalpower');
  });

  it('includes height in range and preserves horizontal distances', () => {
    expect(resolveSpatialMix({ dx: 0, dy: 0, dz: 40, range: 15 })).toBeNull();
    expect(resolveSpatialMix({ dx: 3, dy: 4, range: 15 })!.distance).toBe(5);
  });
});
