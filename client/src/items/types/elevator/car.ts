import type { WorldItem } from '../../../state/gameState';

export type ElevatorPhase = 'idle' | 'opening' | 'arriving' | 'door_open' | 'closing' | 'moving';
export type ElevatorDoorClips = { openSeconds: number; closeSeconds: number };

/** Read-only mirror of one elevator item's server-owned condition. */
export class ElevatorCar {
  /** Returns a car for elevator items, null for anything else. */
  static from(item: WorldItem): ElevatorCar | null {
    return item.type === 'elevator' ? new ElevatorCar(item) : null;
  }

  constructor(readonly item: WorldItem) {}

  get phase(): ElevatorPhase {
    const phase = this.item.params.state;
    switch (phase) {
      case 'opening':
      case 'arriving':
      case 'door_open':
      case 'closing':
      case 'moving':
        return phase;
      default:
        return 'idle';
    }
  }

  get landing(): number {
    return Number(this.item.params.currentZ);
  }

  get doorOpen(): boolean {
    return this.item.params.doorOpen === true;
  }

  get isMoving(): boolean {
    return this.phase === 'moving';
  }

  get doorPassesSound(): boolean {
    const phase = this.phase;
    return phase === 'opening' || phase === 'arriving' || phase === 'door_open' || phase === 'closing';
  }

  isAtLanding(z: number): boolean {
    const landing = this.landing;
    return Number.isFinite(landing) && landing === z;
  }

  /** Resolves the door transmission ramp using server-provided clip lengths. */
  doorTransmission(elapsedSeconds: number, clips: ElevatorDoorClips): number {
    const elapsed = Math.max(0, elapsedSeconds);
    switch (this.phase) {
      case 'door_open':
        return 1;
      case 'opening':
      case 'arriving':
        return clips.openSeconds <= 0 ? 1 : Math.min(1, elapsed / clips.openSeconds);
      case 'closing':
        return clips.closeSeconds <= 0 ? 0 : Math.max(0, 1 - elapsed / clips.closeSeconds);
      default:
        return 0;
    }
  }
}
