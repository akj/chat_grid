import { describe, expect, it } from 'vitest';
import { resolveMainModeCommand } from './mainCommandRouter';
import { getAvailableMainModeCommands, type MainModeCommandAvailabilityContext } from './mainModeCommands';

const availability: MainModeCommandAvailabilityContext = {
  voiceSendAllowed: true, mainHelpAvailable: true,
  hasAdminActions: false, hasWorldBuilder: false, itemTypeCount: 0,
  visibleItemCount: 0, userCount: 0, chatMessageCount: 0, hasCarriedItem: false,
  squareItemCount: 0, usableItemCount: 0, manageableItemCount: 0,
  hasEditableItemTarget: false, hasInspectableItemTarget: false,
};

describe('turning and effects shortcuts', () => {
  it('binds Shift+E to effects independently of the audio mode', () => {
    expect(resolveMainModeCommand('KeyE', true)).toBe('openEffectSelect');
    const commands = getAvailableMainModeCommands(availability);
    expect(commands.find((command) => command.id === 'openEffectSelect')?.shortcut).toBe('Shift+E');
  });

  it('always binds Q/E and exposes both turning commands', () => {
    expect(resolveMainModeCommand('KeyQ', false)).toBe('turnLeft');
    expect(resolveMainModeCommand('KeyE', false)).toBe('turnRight');
    expect(resolveMainModeCommand('KeyQ', true)).toBeNull();
    const commands = getAvailableMainModeCommands(availability);
    expect(commands.find((command) => command.id === 'turnLeft')?.shortcut).toBe('Q');
    expect(commands.find((command) => command.id === 'turnRight')?.shortcut).toBe('E');
  });
});
