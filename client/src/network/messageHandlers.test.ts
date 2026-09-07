import { incomingMessageSchema } from './protocol';
import { describe, expect, it, vi } from 'vitest';
import { createInitialState } from '../state/gameState';
import { createOnMessageHandler } from './messageHandlers';

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
    facingDeg: 0,
    acousticZoneId: 'floor:0',
  });
  const playWorldSound = vi.fn();
  const updateStatus = vi.fn();
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
    updateStatus,
  };
  const deps = new Proxy(provided, {
    get(target, property, receiver) {
      return Reflect.has(target, property) ? Reflect.get(target, property, receiver) : vi.fn();
    },
  }) as unknown as Parameters<typeof createOnMessageHandler>[0];

  return {
    handler: createOnMessageHandler(deps),
    playWorldSound,
    updateStatus,
    state,
  };
}

describe('local facing announcements', () => {
  it('announces a confirmed local facing change as the compass direction', async () => {
    const { handler, updateStatus } = setupRemoteMovement();

    await handler({
      type: 'update_position',
      id: 'self',
      x: 4,
      y: 4,
      z: 0,
      facingDeg: 90,
      acousticZoneId: 'elevator:car-1',
    });

    expect(updateStatus).toHaveBeenCalledWith('east');
  });

  it('does not announce unchanged local facing or remote facing updates', async () => {
    const { handler, updateStatus } = setupRemoteMovement();

    await handler({
      type: 'update_position',
      id: 'self',
      x: 4,
      y: 4,
      z: 0,
      facingDeg: 0,
      acousticZoneId: 'elevator:car-1',
    });
    await handler({
      type: 'update_position',
      id: 'peer-1',
      x: 4,
      y: 4,
      z: 0,
      facingDeg: 90,
      acousticZoneId: 'floor:0',
    });

    expect(updateStatus).not.toHaveBeenCalled();
  });
});

describe('remote movement audio', () => {
  it('routes footsteps with the authoritative peer acoustic zone', async () => {
    const { handler, playWorldSound, state } = setupRemoteMovement();

    await handler({
      type: 'update_position',
      id: 'peer-1',
      x: 5,
      y: 4,
      z: 0,
      facingDeg: 90,
      acousticZoneId: 'floor:0',
    });

    expect(playWorldSound).toHaveBeenCalledWith('/sounds/step-1.ogg', {
      x: 5,
      y: 4,
      z: 0,
      acousticZoneId: 'floor:0',
      gain: 0.7,
    });
    expect(state.peers.get('peer-1')?.facingDeg).toBe(90);
  });

  it('does not play a footstep for a facing-only presence update', async () => {
    const { handler, playWorldSound } = setupRemoteMovement();

    await handler({
      type: 'update_position',
      id: 'peer-1',
      x: 4,
      y: 4,
      z: 0,
      facingDeg: 135,
      acousticZoneId: 'floor:0',
    });

    expect(playWorldSound).not.toHaveBeenCalled();
  });
});

describe('item target message routing', () => {
  it('routes hand target responses separately from ownership transfer targets', async () => {
    const handleItemTransferTargets = vi.fn();
    const handleItemHandTargets = vi.fn();
    const provided = {
      state: createInitialState(),
      handleItemTransferTargets,
      handleItemHandTargets,
    };
    const deps = new Proxy(provided, {
      get(target, property, receiver) {
        return Reflect.has(target, property) ? Reflect.get(target, property, receiver) : vi.fn();
      },
    }) as unknown as Parameters<typeof createOnMessageHandler>[0];
    const handler = createOnMessageHandler(deps);

    await handler({
      type: 'item_hand_targets',
      itemId: 'held-item',
      targets: [{ userId: 'nearby-user', username: 'Nearby user', online: true }],
    });

    expect(handleItemHandTargets).toHaveBeenCalledOnce();
    expect(handleItemTransferTargets).not.toHaveBeenCalled();
  });
});

it('validates and forwards each fresh LiveKit token to the voice connection', async () => {
  const connectToLiveKit = vi.fn();
  const handler = createOnMessageHandler({
    connectToLiveKit,
  } as unknown as Parameters<typeof createOnMessageHandler>[0]);

  for (const token of ['initial-token', 'fresh-token']) {
    await handler(incomingMessageSchema.parse({ type: 'livekit_token', url: 'wss://voice.test', token }));
  }

  expect(connectToLiveKit.mock.calls).toEqual([
    ['wss://voice.test', 'initial-token'],
    ['wss://voice.test', 'fresh-token'],
  ]);
});
