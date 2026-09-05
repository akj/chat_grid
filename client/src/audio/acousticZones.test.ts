import { describe, expect, it } from 'vitest';
import type { WorldItem } from '../state/gameState';
import {
  AcousticZoneRuntime,
  elevatorAcousticZoneId,
  floorAcousticZoneId,
  worldItemAcousticZoneId,
} from './acousticZones';

function elevator(state: string, currentZ = 0): WorldItem {
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
    params: { state, currentZ },
    occupiedOffsets: [{ x: 0, y: 0 }],
  };
}

describe('AcousticZoneRuntime', () => {
  it.each([['opening', 1], ['arriving', 1], ['closing', 0]] as const)(
    'uses zero-length clips before welcome during %s', (phase, transmission) => {
      const runtime = new AcousticZoneRuntime();
      const item = elevator(phase);
      runtime.sync([item], 0);
      expect(runtime.doorTransmission(item, 0)).toBe(transmission);
      expect(runtime.doorTransmission(item, 1000)).toBe(transmission);
    },
  );

  it('keeps floors isolated while cabin occupants hear one another', () => {
    const runtime = new AcousticZoneRuntime();
    const items = new Map([['car-1', elevator('moving', 0)]]);
    runtime.sync(items.values(), 0);

    expect(runtime.transmission(floorAcousticZoneId(0), floorAcousticZoneId(40), items, 1000)).toBe(0);
    expect(runtime.transmission(elevatorAcousticZoneId('car-1'), elevatorAcousticZoneId('car-1'), items, 1000)).toBe(1);
  });

  it('ramps transmission in while opening and out while closing', () => {
    const runtime = new AcousticZoneRuntime();
    runtime.setDoorClips({ openSeconds: 2.563107, closeSeconds: 3.765601 });
    const opening = elevator('opening');
    const items = new Map([['car-1', opening]]);
    runtime.sync(items.values(), 0);

    const cabin = elevatorAcousticZoneId('car-1');
    const floor = floorAcousticZoneId(0);
    expect(runtime.transmission(cabin, floor, items, 0)).toBe(0);
    expect(runtime.transmission(cabin, floor, items, 1300)).toBeGreaterThan(0.5);

    opening.params.state = 'closing';
    runtime.sync(items.values(), 3000);
    expect(runtime.transmission(cabin, floor, items, 3000)).toBe(1);
    expect(runtime.transmission(cabin, floor, items, 7000)).toBe(0);
  });

  it('keeps a closed landed cabin subscribed but disconnects it while moving', () => {
    const runtime = new AcousticZoneRuntime();
    const item = elevator('idle', 40);
    const items = new Map([['car-1', item]]);
    const cabin = elevatorAcousticZoneId('car-1');

    expect(runtime.couldConnect(cabin, floorAcousticZoneId(40), items)).toBe(true);
    item.params.state = 'moving';
    expect(runtime.couldConnect(cabin, floorAcousticZoneId(40), items)).toBe(false);
  });

  it('admits one-shots during door transitions but not while landed closed', () => {
    const runtime = new AcousticZoneRuntime();
    const item = elevator('idle');
    const items = new Map([['car-1', item]]);
    const cabin = elevatorAcousticZoneId('car-1');
    const floor = floorAcousticZoneId(0);

    expect(runtime.canTransmit(cabin, floor, items)).toBe(false);
    item.params.state = 'opening';
    expect(runtime.canTransmit(cabin, floor, items)).toBe(true);
    item.params.state = 'moving';
    expect(runtime.canTransmit(cabin, floor, items)).toBe(false);
  });

  it('places a multi-floor item on the listener-connected landing', () => {
    const car = elevator('door_open', 40);
    car.params.floorZs = [0, 40];
    const items = new Map([['car-1', car]]);

    expect(worldItemAcousticZoneId(car, floorAcousticZoneId(40), items)).toBe('floor:40');
    expect(worldItemAcousticZoneId(car, elevatorAcousticZoneId('car-1'), items)).toBe('floor:40');
  });
});
