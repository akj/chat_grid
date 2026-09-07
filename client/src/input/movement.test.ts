import { describe, expect, it } from 'vitest';
import { resolveArrowMovement } from './movement';

describe('facing-relative arrows', () => {
  it.each([
    [0, 0, 1], [45, 1, 1], [90, 1, 0], [135, 1, -1],
    [180, 0, -1], [225, -1, -1], [270, -1, 0], [315, -1, 1],
  ])('moves forward at heading %i to (%i, %i)', (heading, dx, dy) => {
    expect(resolveArrowMovement({ ArrowUp: true }, heading, true)).toEqual({ dx, dy });
  });

  it('moves backward and sideways while facing east', () => {
    expect(resolveArrowMovement({ ArrowDown: true }, 90, true)).toEqual({ dx: -1, dy: 0 });
    expect(resolveArrowMovement({ ArrowLeft: true }, 90, true)).toEqual({ dx: 0, dy: 1 });
    expect(resolveArrowMovement({ ArrowRight: true }, 90, true)).toEqual({ dx: 0, dy: -1 });
  });

  it('keeps diagonal combinations to one tile per axis at diagonal headings', () => {
    expect(resolveArrowMovement({ ArrowUp: true, ArrowRight: true }, 45, true)).toEqual({ dx: 1, dy: 0 });
    expect(resolveArrowMovement({ ArrowUp: true, ArrowLeft: true }, 315, true)).toEqual({ dx: -1, dy: 0 });
  });

  it('keeps standard-mode movement compass based regardless of facing', () => {
    expect(resolveArrowMovement({ ArrowUp: true }, 180, false)).toEqual({ dx: 0, dy: 1 });
    expect(resolveArrowMovement({ ArrowRight: true }, 315, false)).toEqual({ dx: 1, dy: 0 });
    expect(resolveArrowMovement({}, 45, true)).toEqual({ dx: 0, dy: 0 });
  });
});
