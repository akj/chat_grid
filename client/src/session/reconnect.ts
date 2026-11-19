/** Total connection attempts before reconnect gives up and asks the user to press Connect. */
export const RECONNECT_MAX_ATTEMPTS = 8;

const BASE_DELAY_MS = 5_000;
const MAX_DELAY_MS = 30_000;
const JITTER_MIN = 0.8;
const JITTER_SPAN = 0.4;

type ReconnectOptions = {
  signal: AbortSignal;
  connect: () => Promise<boolean>;
  /** Called after a failed attempt that will be retried, with the delay before the next attempt. */
  onRetry: (attempt: number, delayMs: number) => void;
  /** Returns a value in [0, 1); defaults to Math.random. */
  random?: () => number;
};

/** Delay before 1-based attempt n: min(30 s, 5 s * 2^(n-1)), scaled by a jitter factor in [0.8, 1.2]. */
function retryDelayMs(attempt: number, random: () => number): number {
  const nominal = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** (attempt - 1));
  return nominal * (JITTER_MIN + JITTER_SPAN * random());
}

/** Wait between attempts, ending promptly when the user cancels reconnect. */
function waitForRetry(signal: AbortSignal, delayMs: number): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      document.removeEventListener('visibilitychange', retryWhenVisible);
      resolve(!signal.aborted);
    };
    const retryWhenVisible = () => {
      if (document.visibilityState === 'visible') finish();
    };
    const timer = window.setTimeout(finish, delayMs);
    document.addEventListener('visibilitychange', retryWhenVisible);
    signal.addEventListener('abort', finish, { once: true });
  });
}

/** Try up to RECONNECT_MAX_ATTEMPTS complete connections, backing off with jitter before each attempt. */
export async function runReconnectAttempts(options: ReconnectOptions): Promise<boolean> {
  const random = options.random ?? Math.random;
  let delayMs = retryDelayMs(1, random);
  for (let attempt = 1; attempt <= RECONNECT_MAX_ATTEMPTS; attempt += 1) {
    if (!await waitForRetry(options.signal, delayMs)) return false;
    const connected = await options.connect();
    if (options.signal.aborted) return false;
    if (connected) return true;
    if (attempt < RECONNECT_MAX_ATTEMPTS) {
      delayMs = retryDelayMs(attempt + 1, random);
      options.onRetry(attempt, delayMs);
    }
  }
  return false;
}
