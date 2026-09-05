import { describe, expect, it, vi } from 'vitest';
import { createInitialState } from '../state/gameState';
import { createOnMessageHandler } from './messageHandlers';
import { welcomeMessageSchema } from './protocol';

function setupRemoteMovement() {
  const state = createInitialState();
  state.player.id = 'self';
  state.player.acousticZoneId = 'elevator:car-1';
  state.peers.set('peer-1', {
    id: 'peer-1',
    nickname: 'Other user',
    x: 4,
    y: 4,
    z: 0,
    acousticZoneId: 'floor:0',
  });
  const playWorldSound = vi.fn();
  const peerManager = {
    ensurePeer: vi.fn(),
    setPeerPosition: vi.fn(),
  };
  const provided = {
    state,
    peerManager,
    refreshAcousticModel: vi.fn(),
    randomFootstepUrl: () => '/sounds/step-1.ogg',
    playWorldSound,
  };
  const deps = new Proxy(provided, {
    get(target, property, receiver) {
      return Reflect.has(target, property) ? Reflect.get(target, property, receiver) : vi.fn();
    },
  }) as unknown as Parameters<typeof createOnMessageHandler>[0];

  return {
    handler: createOnMessageHandler(deps),
    playWorldSound,
  };
}

describe('remote movement audio', () => {
  it('routes footsteps with the authoritative peer acoustic zone', async () => {
    const { handler, playWorldSound } = setupRemoteMovement();

    await handler({
      type: 'update_position',
      id: 'peer-1',
      x: 5,
      y: 4,
      z: 0,
      acousticZoneId: 'floor:0',
    });

    expect(playWorldSound).toHaveBeenCalledWith('/sounds/step-1.ogg', {
      x: 5,
      y: 4,
      z: 0,
      acousticZoneId: 'floor:0',
      gain: 0.7,
    });
  });
});

describe('welcome door clips', () => {
  const welcome = {
    type: 'welcome',
    id: 'self',
    player: { id: 'self', nickname: 'Self', x: 0, y: 0, z: 0, acousticZoneId: 'floor:0' },
    users: [],
    worldConfig: {
      gridSize: 41,
      floors: [{ id: 'ground', name: 'Ground', z: 0 }],
      elevatorDoorClipSeconds: { open: 4, close: 6 },
    },
  };

  it('requires positive open and close clip lengths in world config', () => {
    expect(welcomeMessageSchema.safeParse(welcome).success).toBe(true);
    for (const clips of [undefined, { open: 0, close: 6 }, { open: 4, close: -1 }]) {
      expect(welcomeMessageSchema.safeParse({
        ...welcome,
        worldConfig: { ...welcome.worldConfig, elevatorDoorClipSeconds: clips },
      }).success).toBe(false);
    }
  });

  it('applies server clip lengths before refreshing acoustics', async () => {
    const calls: string[] = [];
    const setElevatorDoorClips = vi.fn(() => calls.push('clips'));
    const refreshAcousticModel = vi.fn(() => calls.push('refresh'));
    const element = { classList: { add: vi.fn(), remove: vi.fn() }, focus: vi.fn() };
    const provided = {
      state: createInitialState(),
      getWorldGridSize: () => 41,
      setElevatorDoorClips,
      refreshAcousticModel,
      peerManager: { setListenerFloor: vi.fn() },
      dom: { connectButton: element, disconnectButton: element, focusGridButton: element, canvas: element, instructions: element },
    };
    const deps = new Proxy(provided, {
      get(target, property, receiver) {
        return Reflect.has(target, property) ? Reflect.get(target, property, receiver) : vi.fn();
      },
    }) as unknown as Parameters<typeof createOnMessageHandler>[0];

    await createOnMessageHandler(deps)(welcomeMessageSchema.parse(welcome));

    expect(setElevatorDoorClips).toHaveBeenCalledWith({ openSeconds: 4, closeSeconds: 6 });
    expect(calls).toEqual(['clips', 'refresh']);
  });
});
