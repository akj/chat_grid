const COMPASS_STEPS = [
  { dx: 0, dy: 1 },
  { dx: 1, dy: 1 },
  { dx: 1, dy: 0 },
  { dx: 1, dy: -1 },
  { dx: 0, dy: -1 },
  { dx: -1, dy: -1 },
  { dx: -1, dy: 0 },
  { dx: -1, dy: 1 },
] as const;

/** Maps arrow input to one grid step without changing the player's heading. */
export function resolveArrowMovement(
  keysPressed: Record<string, boolean>, facingDeg: number, relative: boolean,
): { dx: number; dy: number } {
  let dx = 0;
  let dy = 0;
  if (keysPressed.ArrowUp) dy = 1;
  if (keysPressed.ArrowDown) dy = -1;
  if (keysPressed.ArrowLeft) dx = -1;
  if (keysPressed.ArrowRight) dx = 1;
  if (!relative || (dx === 0 && dy === 0)) return { dx, dy };
  const inputOctant = Math.round(Math.atan2(dx, dy) / (Math.PI / 4));
  const octant = ((inputOctant + facingDeg / 45) % 8 + 8) % 8;
  return COMPASS_STEPS[octant];
}
