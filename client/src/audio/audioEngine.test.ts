import { afterEach, describe, expect, it, vi } from 'vitest';
import { AudioEngine } from './audioEngine';

class FakeAudioContext {
  state = 'running';
  destination = {};
  currentTime = 0;
  listener = {
    forwardX: { setTargetAtTime: vi.fn() },
    forwardY: { setTargetAtTime: vi.fn() },
    forwardZ: { setTargetAtTime: vi.fn() },
    upX: { value: 0 }, upY: { value: 1 }, upZ: { value: 0 },
  };
  setSinkId = vi.fn(async (_id: string) => undefined);
  createGain() {
    return { gain: { value: 1 }, connect: vi.fn() };
  }
}

afterEach(() => vi.unstubAllGlobals());

describe('AudioEngine output device', () => {
  it('routes an existing context to the selected device and back to default', async () => {
    vi.stubGlobal('window', { AudioContext: FakeAudioContext });
    const audio = new AudioEngine();
    await audio.ensureContext();
    const context = audio.context as unknown as FakeAudioContext;

    await audio.setOutputDevice('speakers');
    expect(context.setSinkId).toHaveBeenLastCalledWith('speakers');
    await audio.setOutputDevice('');
    expect(context.setSinkId).toHaveBeenLastCalledWith('');
  });

  it.each(['headset', ''])('stores selection %j without creating a context, then applies it on creation', async (deviceId) => {
    vi.stubGlobal('window', { AudioContext: FakeAudioContext });
    const audio = new AudioEngine();
    await audio.setOutputDevice('old-speakers');
    await audio.setOutputDevice(deviceId);
    expect(audio.context).toBeNull();

    await audio.ensureContext();
    const context = audio.context as unknown as FakeAudioContext;
    expect(context.setSinkId).toHaveBeenCalledWith(deviceId);
    await audio.ensureContext();
    expect(context.setSinkId).toHaveBeenCalledTimes(1);
  });

  it('keeps context setup usable if a saved speaker is unavailable', async () => {
    class UnavailableSinkAudioContext extends FakeAudioContext {
      setSinkId = vi.fn(async (_id: string) => { throw new Error('Device unavailable'); });
    }
    vi.stubGlobal('window', { AudioContext: UnavailableSinkAudioContext });
    const audio = new AudioEngine();
    await audio.setOutputDevice('unplugged-speakers');
    await expect(audio.ensureContext()).resolves.toBeUndefined();
    expect(audio.getOutputDestinationNode()).not.toBeNull();
  });

  it('works without browser support for context sink selection', async () => {
    class UnsupportedAudioContext {
      state = 'running';
      destination = {};
      createGain = FakeAudioContext.prototype.createGain;
    }
    vi.stubGlobal('window', { AudioContext: UnsupportedAudioContext });
    const audio = new AudioEngine();
    await audio.setOutputDevice('headset');
    await expect(audio.ensureContext()).resolves.toBeUndefined();
    await expect(audio.setOutputDevice('')).resolves.toBeUndefined();
  });
});

describe('AudioEngine spatial preferences', () => {
  it('remembers standard-mode turns without rotating audio, then applies them in HRTF', async () => {
    vi.stubGlobal('window', { AudioContext: FakeAudioContext });
    const audio = new AudioEngine();
    await audio.ensureContext();
    const context = audio.context as unknown as FakeAudioContext;
    const forwardX = context.listener.forwardX.setTargetAtTime;

    audio.setListenerFacing(90);
    expect(forwardX).not.toHaveBeenCalled();
    audio.setSpatialMode('hrtf');
    expect(forwardX).toHaveBeenLastCalledWith(1, 0, expect.any(Number));
    audio.setSpatialMode('standard');
    expect(forwardX).toHaveBeenLastCalledWith(0, 0, expect.any(Number));
    forwardX.mockClear();
    audio.setListenerFacing(225);
    expect(forwardX).not.toHaveBeenCalled();
    audio.setSpatialMode('hrtf');
    expect(forwardX).toHaveBeenLastCalledWith(Math.sin(225 * Math.PI / 180), 0, expect.any(Number));
  });

  it('loads saved HRTF and keeps accepting facing changes with a method-only listener', async () => {
    const setOrientation = vi.fn();
    class MethodOnlyAudioContext extends FakeAudioContext {
      constructor() {
        super();
        Object.defineProperty(this, 'listener', { value: { setOrientation } });
      }
    }
    vi.stubGlobal('window', { AudioContext: MethodOnlyAudioContext });
    const audio = new AudioEngine();
    audio.setSpatialMode('hrtf');
    await expect(audio.ensureContext()).resolves.toBeUndefined();
    expect(audio.getOutputDestinationNode()).not.toBeNull();

    for (const heading of [90, 180, 270, 0, 45]) {
      expect(() => audio.setListenerFacing(heading)).not.toThrow();
    }
    expect(() => audio.setSpatialMode('standard')).not.toThrow();
    expect(() => audio.setSpatialMode('hrtf')).not.toThrow();
    expect(audio.getSpatialMode()).toBe('hrtf');
    expect(setOrientation).toHaveBeenLastCalledWith(
      Math.sin(Math.PI / 4), 0, -Math.cos(Math.PI / 4), 0, 1, 0,
    );
  });

  it('applies settings chosen before context creation and retains HRTF through mono', async () => {
    vi.stubGlobal('window', { AudioContext: FakeAudioContext });
    const audio = new AudioEngine();
    audio.setSpatialMode('hrtf');
    audio.setListenerFacing(90);
    audio.setOutputMode('mono');
    expect(audio.context).toBeNull();
    await audio.ensureContext();
    const context = audio.context as unknown as FakeAudioContext;
    expect(context.listener.forwardX.setTargetAtTime).toHaveBeenCalledWith(1, 0, expect.any(Number));
    expect(audio.toggleOutputMode()).toBe('stereo');
    expect(audio.getSpatialMode()).toBe('hrtf');
  });
});
