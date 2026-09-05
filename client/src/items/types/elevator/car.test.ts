import { describe, expect, it } from 'vitest';
import type { WorldItem } from '../../../state/gameState';
import { ElevatorCar, type ElevatorPhase } from './car';

function elevator(params: Record<string, unknown>): WorldItem {
  return {
    id: 'car-1',
    type: 'elevator',
    title: 'Elevator',
    x: 10,
    y: 10,
    z: 0,
    createdBy: 'user-1',
    updatedBy: 'user-1',
    createdAt: 1,
    updatedAt: 1,
    version: 1,
    capabilities: [],
    params,
    occupiedOffsets: [{ x: 0, y: 0 }],
  };
}

const clips = { openSeconds: 2.563107, closeSeconds: 3.765601 };

describe('ElevatorCar', () => {
  it('only wraps elevator items and reflects condition updates', () => {
    const item = elevator({ state: 'idle', doorOpen: false });
    const car = ElevatorCar.from(item);
    expect(car?.item).toBe(item);
    expect(car?.doorOpen).toBe(false);
    item.params.state = 'door_open';
    item.params.doorOpen = true;
    expect(car?.phase).toBe('door_open');
    expect(car?.doorOpen).toBe(true);
    expect(ElevatorCar.from({ ...item, type: 'radio' })).toBeNull();
  });

  it.each([undefined, null, 'unknown', 42])('falls back to idle for state %s', (state) => {
    expect(new ElevatorCar(elevator({ state })).phase).toBe('idle');
  });

  it.each<[ElevatorPhase, boolean]>([
    ['idle', false],
    ['opening', true],
    ['arriving', true],
    ['door_open', true],
    ['closing', true],
    ['moving', false],
  ])('resolves sound passage during %s', (phase, passesSound) => {
    const car = new ElevatorCar(elevator({ state: phase }));
    expect(car.phase).toBe(phase);
    expect(car.doorPassesSound).toBe(passesSound);
    expect(car.isMoving).toBe(phase === 'moving');
  });

  it('coerces the landing and rejects non-finite landings', () => {
    const car = new ElevatorCar(elevator({ currentZ: '40' }));
    expect(car.landing).toBe(40);
    expect(car.isAtLanding(40)).toBe(true);
    expect(car.isAtLanding(0)).toBe(false);
    const malformed = new ElevatorCar(elevator({ currentZ: 'invalid' }));
    expect(malformed.landing).toBeNaN();
    expect(malformed.isAtLanding(0)).toBe(false);
    expect(malformed.isAtLanding(NaN)).toBe(false);
    expect(new ElevatorCar(elevator({ currentZ: Infinity })).isAtLanding(Infinity)).toBe(false);
  });

  it.each(['opening', 'arriving'])('ramps transmission across the open clip during %s', (state) => {
    const car = new ElevatorCar(elevator({ state }));
    expect(car.doorTransmission(-1, clips)).toBe(0);
    expect(car.doorTransmission(0, clips)).toBe(0);
    expect(car.doorTransmission(clips.openSeconds / 2, clips)).toBe(0.5);
    expect(car.doorTransmission(clips.openSeconds + 1, clips)).toBe(1);
  });

  it('ramps transmission across the close clip', () => {
    const car = new ElevatorCar(elevator({ state: 'closing' }));
    expect(car.doorTransmission(-1, clips)).toBe(1);
    expect(car.doorTransmission(0, clips)).toBe(1);
    expect(car.doorTransmission(clips.closeSeconds / 2, clips)).toBe(0.5);
    expect(car.doorTransmission(clips.closeSeconds + 1, clips)).toBe(0);
  });

  it.each([['door_open', 1], ['idle', 0], ['moving', 0]] as const)(
    'keeps transmission constant during %s', (state, transmission) => {
      const car = new ElevatorCar(elevator({ state }));
      for (const elapsed of [0, 1, 10]) {
        expect(car.doorTransmission(elapsed, clips)).toBe(transmission);
      }
    },
  );
});
