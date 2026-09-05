import { isItemOnFloor, type WorldItem } from '../state/gameState';
import { ElevatorCar, type ElevatorDoorClips, type ElevatorPhase } from '../items/types/elevator/car';

type Transition = {
  phase: ElevatorPhase;
  startedAtMs: number;
};

/** Returns the acoustic zone occupied by a user standing on a floor. */
export function floorAcousticZoneId(z: number): string {
  return `floor:${z}`;
}

/** Returns the acoustic zone occupied by a passenger in an elevator cabin. */
export function elevatorAcousticZoneId(itemId: string): string {
  return `elevator:${itemId}`;
}

/** Resolve a floor-bound item source against the listener's connected landing. */
export function worldItemAcousticZoneId(
  item: WorldItem,
  listenerZoneId: string,
  items: Map<string, WorldItem>,
): string {
  const listenerFloor = parseFloorZone(listenerZoneId);
  const listenerElevatorId = parseElevatorZone(listenerZoneId);
  const elevator = listenerElevatorId ? items.get(listenerElevatorId) : null;
  const car = elevator ? ElevatorCar.from(elevator) : null;
  const connectedFloor = listenerFloor ?? car?.landing ?? null;
  if (connectedFloor !== null && Number.isInteger(connectedFloor) && isItemOnFloor(item, connectedFloor)) {
    return floorAcousticZoneId(connectedFloor);
  }
  return floorAcousticZoneId(item.z);
}

function parseFloorZone(zoneId: string): number | null {
  if (!zoneId.startsWith('floor:')) return null;
  const z = Number(zoneId.slice('floor:'.length));
  return Number.isInteger(z) ? z : null;
}

function parseElevatorZone(zoneId: string): string | null {
  return zoneId.startsWith('elevator:') ? zoneId.slice('elevator:'.length) || null : null;
}

/** Tracks local door progress and resolves transmission between acoustic zones. */
export class AcousticZoneRuntime {
  private readonly transitions = new Map<string, Transition>();
  private doorClips: ElevatorDoorClips = { openSeconds: 0, closeSeconds: 0 };

  setDoorClips(clips: ElevatorDoorClips): void {
    this.doorClips = clips;
  }

  sync(items: Iterable<WorldItem>, nowMs = performance.now()): void {
    const validIds = new Set<string>();
    for (const item of items) {
      const car = ElevatorCar.from(item);
      if (!car) continue;
      validIds.add(item.id);
      const phase = car.phase;
      const previous = this.transitions.get(item.id);
      if (!previous || previous.phase !== phase) {
        this.transitions.set(item.id, { phase, startedAtMs: nowMs });
      }
    }
    for (const itemId of this.transitions.keys()) {
      if (!validIds.has(itemId)) this.transitions.delete(itemId);
    }
  }

  doorTransmission(item: WorldItem, nowMs = performance.now()): number {
    const car = ElevatorCar.from(item);
    if (!car) return 0;
    const transition = this.transitions.get(item.id);
    const elapsedSeconds = Math.max(0, (nowMs - (transition?.startedAtMs ?? nowMs)) / 1000);
    return car.doorTransmission(elapsedSeconds, this.doorClips);
  }

  transmission(
    listenerZoneId: string,
    sourceZoneId: string,
    items: Map<string, WorldItem>,
    nowMs = performance.now(),
  ): number {
    if (listenerZoneId === sourceZoneId) return 1;
    const elevator = this.connectedElevator(listenerZoneId, sourceZoneId, items);
    if (!elevator) return 0;
    return this.doorTransmission(elevator.item, nowMs);
  }

  /** Return whether a one-shot can transmit now or during the active door transition. */
  canTransmit(
    listenerZoneId: string,
    sourceZoneId: string,
    items: Map<string, WorldItem>,
  ): boolean {
    if (listenerZoneId === sourceZoneId) return true;
    const elevator = this.connectedElevator(listenerZoneId, sourceZoneId, items);
    if (!elevator) return false;
    return elevator.doorPassesSound;
  }

  couldConnect(listenerZoneId: string, sourceZoneId: string, items: Map<string, WorldItem>): boolean {
    if (listenerZoneId === sourceZoneId) return true;
    const elevator = this.connectedElevator(listenerZoneId, sourceZoneId, items);
    return !!elevator && !elevator.isMoving;
  }

  private connectedElevator(
    listenerZoneId: string,
    sourceZoneId: string,
    items: Map<string, WorldItem>,
  ): ElevatorCar | null {
    const listenerFloor = parseFloorZone(listenerZoneId);
    const sourceFloor = parseFloorZone(sourceZoneId);
    const elevatorId = parseElevatorZone(listenerZoneId) ?? parseElevatorZone(sourceZoneId);
    const floorZ = listenerFloor ?? sourceFloor;
    if (!elevatorId || floorZ === null) return null;
    const elevator = items.get(elevatorId);
    const car = elevator ? ElevatorCar.from(elevator) : null;
    return car?.isAtLanding(floorZ) ? car : null;
  }
}
