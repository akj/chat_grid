import { describe, expect, it, vi } from 'vitest';
import { configureSpatialAudio, createSpatialPanner, disconnectSpatialPanner, resolveSpatialMix, SPATIAL_TIME_CONSTANT_SECONDS, updateSpatialPanner } from './spatial';

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

  it.each([0, 45, 90, 135, 180, 225, 270, 315])('rotates an east source smoothly for heading %s', (heading) => {
    const ctx = context();
    const panner = createSpatialPanner(ctx);
    const mix = resolveSpatialMix({ dx: 5, dy: 0, range: 15 })!;
    updateSpatialPanner(panner, mix);
    configureSpatialAudio(ctx, 'hrtf', 'stereo', heading);
    const angle = heading * Math.PI / 180;
    expect(panner.positionX.value).toBeCloseTo(5 * Math.cos(angle));
    expect(panner.positionZ.value).toBeCloseTo(-5 * Math.sin(angle));
    expect(panner.positionX.setTargetAtTime).toHaveBeenLastCalledWith(
      expect.any(Number), ctx.currentTime, SPATIAL_TIME_CONSTANT_SECONDS,
    );
    expect(ctx.listener.forwardX.setTargetAtTime).not.toHaveBeenCalled();
    configureSpatialAudio(ctx, 'standard', 'stereo', heading);
    expect(panner.positionX.value).toBe(5);
    expect(panner.positionZ.value).toBeCloseTo(0);
  });

  it('keeps co-located sounds centered throughout turns and restores spatial audio after mono', () => {
    const ctx = context();
    const held = createSpatialPanner(ctx);
    updateSpatialPanner(held, resolveSpatialMix({ dx: 0, dy: 0, range: 15 }));
    for (const heading of [45, 90, 135, 180, 225, 270, 315, 0]) {
      configureSpatialAudio(ctx, 'hrtf', 'stereo', heading);
      expect(held.positionX.value).toBe(0);
      expect(held.positionZ.value).toBe(-1);
    }
    const remote = createSpatialPanner(ctx);
    updateSpatialPanner(remote, resolveSpatialMix({ dx: -4, dy: -6, range: 15 }));
    configureSpatialAudio(ctx, 'hrtf', 'mono', 90);
    expect(remote.panningModel).toBe('equalpower');
    expect(remote.channelCount).toBe(1);
    expect(remote.positionX.value).toBe(0);
    expect(remote.positionZ.value).toBe(-1);
    configureSpatialAudio(ctx, 'hrtf', 'stereo', 90);
    expect(remote.panningModel).toBe('HRTF');
    expect(remote.channelCount).toBe(2);
    expect(remote.positionX.value).toBeCloseTo(6);
    expect(remote.positionZ.value).toBeCloseTo(4);
  });

  it('smooths turns without requiring listener orientation AudioParams', () => {
    const ctx = context();
    const setOrientation = vi.fn();
    Object.defineProperty(ctx, 'listener', { value: { setOrientation } });
    const panner = createSpatialPanner(ctx);
    updateSpatialPanner(panner, resolveSpatialMix({ dx: 5, dy: 0, range: 15 }));
    for (const heading of [0, 90, 180, 270, 45]) {
      expect(() => configureSpatialAudio(ctx, 'hrtf', 'stereo', heading)).not.toThrow();
    }
    expect(setOrientation).not.toHaveBeenCalled();
    expect(panner.positionX.value).toBeCloseTo(5 / Math.sqrt(2));
    expect(panner.positionZ.value).toBeCloseTo(-5 / Math.sqrt(2));
    expect(panner.panningModel).toBe('HRTF');
    expect(createSpatialPanner(ctx).panningModel).toBe('HRTF');
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
