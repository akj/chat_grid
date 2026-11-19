// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RECONNECT_MAX_ATTEMPTS, runReconnectAttempts } from './reconnect';

/** Jitter factor of exactly 1.0, so delays equal the nominal schedule. */
const noJitter = () => 0.5;

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('runReconnectAttempts', () => {
  it('backs off 5, 10, 20, then 30 seconds across exactly eight attempts', async () => {
    const controller = new AbortController();
    const connect = vi.fn(async () => false);
    const onRetry = vi.fn();
    const result = runReconnectAttempts({ signal: controller.signal, connect, onRetry, random: noJitter });

    const schedule = [5_000, 10_000, 20_000, 30_000, 30_000, 30_000, 30_000, 30_000];
    expect(RECONNECT_MAX_ATTEMPTS).toBe(schedule.length);
    for (const [index, delayMs] of schedule.entries()) {
      await vi.advanceTimersByTimeAsync(delayMs - 1);
      expect(connect).toHaveBeenCalledTimes(index);
      await vi.advanceTimersByTimeAsync(1);
      expect(connect).toHaveBeenCalledTimes(index + 1);
    }

    await expect(result).resolves.toBe(false);
    expect(onRetry.mock.calls).toEqual([
      [1, 10_000],
      [2, 20_000],
      [3, 30_000],
      [4, 30_000],
      [5, 30_000],
      [6, 30_000],
      [7, 30_000],
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('scales each delay by a jitter factor between 0.8 and 1.2', async () => {
    const delaysFor = async (random: () => number): Promise<number[]> => {
      const onRetry = vi.fn();
      const result = runReconnectAttempts({
        signal: new AbortController().signal,
        connect: async () => false,
        onRetry,
        random,
      });
      await vi.advanceTimersByTimeAsync(300_000);
      await expect(result).resolves.toBe(false);
      return onRetry.mock.calls.map(([, delayMs]) => delayMs as number);
    };

    expect(await delaysFor(() => 0)).toEqual([8_000, 16_000, 24_000, 24_000, 24_000, 24_000, 24_000]);
    const highest = await delaysFor(() => 0.999_999);
    const nominal = [10_000, 20_000, 30_000, 30_000, 30_000, 30_000, 30_000];
    highest.forEach((delayMs, index) => {
      expect(delayMs).toBeLessThan(nominal[index] * 1.2);
      expect(delayMs).toBeGreaterThan(nominal[index] * 1.199);
    });

    const connect = vi.fn(async () => true);
    const result = runReconnectAttempts({
      signal: new AbortController().signal,
      connect,
      onRetry: vi.fn(),
      random: () => 0,
    });
    await vi.advanceTimersByTimeAsync(3_999);
    expect(connect).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toBe(true);
  });

  it('stops immediately after a successful attempt', async () => {
    const controller = new AbortController();
    const connect = vi.fn(async () => true);
    const onRetry = vi.fn();
    const result = runReconnectAttempts({ signal: controller.signal, connect, onRetry, random: noJitter });

    await vi.advanceTimersByTimeAsync(5_000);
    await expect(result).resolves.toBe(true);
    expect(connect).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(20_000);
    expect(connect).toHaveBeenCalledOnce();
  });

  it('aborts during the initial delay without an attempt or retry message', async () => {
    const controller = new AbortController();
    const connect = vi.fn(async () => false);
    const onRetry = vi.fn();
    const result = runReconnectAttempts({ signal: controller.signal, connect, onRetry, random: noJitter });

    await vi.advanceTimersByTimeAsync(2_000);
    controller.abort();

    await expect(result).resolves.toBe(false);
    expect(connect).not.toHaveBeenCalled();
    expect(onRetry).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(20_000);
    expect(connect).not.toHaveBeenCalled();
  });

  it('aborts during an in-flight attempt without starting another attempt or announcing a retry', async () => {
    const controller = new AbortController();
    let resolveConnect!: (connected: boolean) => void;
    const connect = vi.fn(
      () => new Promise<boolean>((resolve) => {
        resolveConnect = resolve;
      }),
    );
    const onRetry = vi.fn();
    const result = runReconnectAttempts({ signal: controller.signal, connect, onRetry, random: noJitter });

    await vi.advanceTimersByTimeAsync(5_000);
    expect(connect).toHaveBeenCalledOnce();

    controller.abort();
    resolveConnect(false);
    await flushMicrotasks();
    await expect(result).resolves.toBe(false);
    expect(connect).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(20_000);
    expect(connect).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();
  });
});

it('wakes a waiting retry on visibility and removes the listener after cancellation', async () => {
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  const controller = new AbortController();
  const connect = vi.fn(async () => false);
  const result = runReconnectAttempts({ signal: controller.signal, connect, onRetry: vi.fn() });
  document.dispatchEvent(new Event('visibilitychange'));
  expect(connect).not.toHaveBeenCalled();
  visibility.mockReturnValue('visible');
  document.dispatchEvent(new Event('visibilitychange'));
  await flushMicrotasks();
  expect(connect).toHaveBeenCalledOnce();
  controller.abort();
  await expect(result).resolves.toBe(false);
  document.dispatchEvent(new Event('visibilitychange'));
  await vi.advanceTimersByTimeAsync(20_000);
  expect(connect).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});
