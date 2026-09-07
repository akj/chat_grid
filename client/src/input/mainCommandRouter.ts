/**
 * Declarative command ids for the primary gameplay input mode.
 */
export type MainModeCommand =
  | 'editNickname'
  | 'toggleMute'
  | 'toggleOutputMode'
  | 'toggleHrtf'
  | 'turnLeft'
  | 'turnRight'
  | 'toggleLoopback'
  | 'toggleVoiceLayer'
  | 'toggleItemLayer'
  | 'toggleMediaLayer'
  | 'toggleWorldLayer'
  | 'masterVolumeUp'
  | 'masterVolumeDown'
  | 'openEffectSelect'
  | 'effectValueUp'
  | 'effectValueDown'
  | 'speakCoordinates'
  | 'speakFacing'
  | 'openMicGainEdit'
  | 'calibrateMicrophone'
  | 'useItem'
  | 'secondaryUseItem'
  | 'speakUsers'
  | 'addItem'
  | 'locateNearestItem'
  | 'listItems'
  | 'pickupDropItem'
  | 'speakHeldItems'
  | 'openItemManagement'
  | 'editItem'
  | 'inspectItem'
  | 'pingServer'
  | 'locateNearestUser'
  | 'listUsers'
  | 'openHelp'
  | 'openChat'
  | 'openAdminMenu'
  | 'openWorldBuilder'
  | 'chatPrev'
  | 'chatNext'
  | 'chatFirst'
  | 'chatLast'
  | 'escape';

/**
 * Maps raw key events to a semantic command for main mode handling.
 */
export function resolveMainModeCommand(code: string, shiftKey: boolean): MainModeCommand | null {
  if (code === 'KeyN') return shiftKey ? null : 'editNickname';
  if (code === 'KeyM') return shiftKey ? 'toggleOutputMode' : 'toggleMute';
  if (code === 'Digit1') return shiftKey ? 'toggleLoopback' : 'toggleVoiceLayer';
  if (code === 'Digit2') return shiftKey ? null : 'toggleItemLayer';
  if (code === 'Digit3') return shiftKey ? null : 'toggleMediaLayer';
  if (code === 'Digit4') return shiftKey ? 'toggleHrtf' : 'toggleWorldLayer';
  if (code === 'KeyQ') return shiftKey ? null : 'turnLeft';
  if (code === 'KeyE') return shiftKey ? 'openEffectSelect' : 'turnRight';
  if (code === 'Equal') return shiftKey ? 'effectValueUp' : 'masterVolumeUp';
  if (code === 'Minus') return shiftKey ? 'effectValueDown' : 'masterVolumeDown';
  if (code === 'NumpadAdd') return 'masterVolumeUp';
  if (code === 'NumpadSubtract') return 'masterVolumeDown';
  if (code === 'KeyF') return shiftKey ? null : 'speakFacing';
  if (code === 'KeyC') return shiftKey ? null : 'speakCoordinates';
  if (code === 'KeyV') return shiftKey ? 'calibrateMicrophone' : 'openMicGainEdit';
  if (code === 'Enter') return shiftKey ? 'secondaryUseItem' : 'useItem';
  if (code === 'KeyU') return shiftKey ? null : 'speakUsers';
  if (code === 'KeyA') return shiftKey ? null : 'addItem';
  if (code === 'KeyI') return shiftKey ? 'listItems' : 'locateNearestItem';
  if (code === 'KeyD') return shiftKey ? null : 'pickupDropItem';
  if (code === 'KeyH') return shiftKey ? null : 'speakHeldItems';
  if (code === 'KeyO') return shiftKey ? 'inspectItem' : 'editItem';
  if (code === 'KeyP') return shiftKey ? null : 'pingServer';
  if (code === 'KeyL') return shiftKey ? 'listUsers' : 'locateNearestUser';
  if (code === 'Slash') return shiftKey ? 'openHelp' : 'openChat';
  if (code === 'KeyZ') return shiftKey ? 'openAdminMenu' : 'openItemManagement';
  if (code === 'KeyW') return shiftKey ? null : 'openWorldBuilder';
  if (code === 'Comma') return shiftKey ? 'chatFirst' : 'chatPrev';
  if (code === 'Period') return shiftKey ? 'chatLast' : 'chatNext';
  if (code === 'Escape') return shiftKey ? null : 'escape';
  return null;
}
