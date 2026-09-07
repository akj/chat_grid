# Controls Reference

This document is the authoritative keymap for the client.

## Touch Controls

- Touch controls appear automatically on coarse-pointer devices and can be shown or hidden from Settings.
- Tap a direction for one movement step; press and hold to continue moving.
- The directional buttons remain Up, Down, Left, and Right; menu screens may use those same directions for navigation.
- `Use` performs the current primary action; in menus it becomes `Select`.
- `Back` cancels or exits the current menu.
- `Chat` opens a native mobile text field and `Commands` opens a visual popup list of available commands.
- `Mute` and `Unmute` show and change the current microphone state.
- The control dock can be collapsed without covering the grid.

## Normal Mode

### Movement
- `Arrow Keys`: In standard mode, move using compass directions. In HRTF mode, `ArrowUp` moves forward, `ArrowDown` moves backward, and `ArrowLeft`/`ArrowRight` strafe relative to your facing; moves advance at most one square per axis. Arrows do not change facing.
- `Q` / `E`: Turn facing left/right by 45 degrees in standard or HRTF mode
- `Shift+K`, `Applications`, or `Shift+F10`: Open the command palette in supported modes
- `?`: Open help viewer
- `C`: Speak coordinates and any walls bordering the current square
- `F`: Announce your facing direction, one of eight compass directions
- `Escape`: Press once for disconnect prompt, press again to disconnect

### Users, Nickname, Chat
- `L`: Locate nearest user
- `Shift+L`: List users alphabetically with current presence; `Enter` teleports to selected user; `ArrowLeft`/`ArrowRight` adjust selected user volume
- `U`: Speak connected users
- `N`: Edit nickname
- `/`: Start chat
- In chat, commands are supported when `/` is the first character:
  - `/me <action>`: Send action text without `name:`
  - `/up`: Show server uptime (self only)
  - `/version`: Show server version (self only)
- `Shift+Z`: Users menu (available to the built-in user role and above)
- `,` / `.`: Previous/next message
- `<` / `>`: First/last message

### Items
- `H`: What are you holding?
- `I`: Locate nearest item
- `Shift+I`: List items and teleport to selected item with `Enter`
- `A`: Add item
- `O`: Edit item properties
- `Shift+O`: Inspect all item properties
- `D`: Pick up or drop item
- `Z`: Manage items
- `Hand to user`: Select an eligible nearby user to receive a held item without changing ownership.
- `Transfer ownership`: Change an item’s owner without moving it or changing who holds it.
- `Space` in item management menu: Read tooltip/help for the selected action
- `Enter`: Use item
- `Shift+Enter`: Secondary item action
- For an elevator, `Enter` calls or starts opening it, enters or exits only after the door is fully open, and has no boarding effect while the door is opening or closing.

### World Builder

- `W`: Open World Builder when the account has `world.structure.edit`.
- `Add wall`: Select a server-configured type, then select the north, south, east, or west edge of the current square. Successful creation opens the new wall directly in the shared wall editor.
- `Edit walls`: List current-floor walls nearest first, then change type, orientation, occupied edge anchors, slide, detailed properties, or delete the complete wall.
- `Set start edge` / `Set end edge`: Each entry shows its current `x, y, z` unit-edge anchor. Start and end are inclusive, so a one-square wall reports the same coordinate for both. `ArrowLeft` decreases that anchor's run-axis coordinate by one; `ArrowRight` increases it. The result announces only the authoritative new coordinate.
- `Slide vertically` / `Slide horizontally`: `ArrowLeft` or `ArrowRight` moves a horizontal run along y or a vertical run along x without changing its length.
- `Orientation`: `ArrowLeft`/`ArrowRight` cycles horizontal and vertical directly; `Enter` opens the shared option selector. Rotation keeps the start coordinate fixed and is rejected if the rotated run is outside the world or overlaps another wall.
- `Type`: This is the first wall action. `ArrowLeft`/`ArrowRight` cycles presets directly; `Enter` opens the shared option selector. Choosing a preset resets title, movement behavior, height, transmission, low-pass, and contact sound to that preset's defaults.
- `Edit properties`: Change sound transmission (`0..1`), occlusion low-pass (`20..20000` hertz), or the contact-sound URL.
- On transmission or low-pass, `ArrowLeft`/`ArrowRight` changes one numeric step and `PageDown`/`PageUp` changes ten steps. Inside the numeric editor, use `ArrowDown`/`ArrowUp` and the corresponding page keys.
- `Space`: Read specific help for the selected World Builder action, preset, direction, or wall. Property help uses shared metadata to report its type, options or numeric range, and editability.
- Walls are edited live. Other connected clients receive and render changes immediately.
- Wall deletion uses the same default-No confirmation flow as item deletion; confirmation choices do not have tooltips.
- Brick, glass, and fence walls block movement. Curtains may be crossed; wall impacts and curtain crossings play the structure's contact sound locally and spatially for nearby users.
- New curtain, glass, and fence walls use their matching contact sounds; brick uses the standard wall sound. Builders may edit any wall's sound URL.

### Audio

The number-key audio toggles act once per press; holding a key does not toggle repeatedly.

- `P`: Ping server
- `V`: Set microphone gain
- `Shift+V`: Microphone calibration
- `M`: Mute/unmute local microphone
- `Shift+M`: Toggle stereo/mono output
- `Shift+1` (`!`): Toggle loopback monitor
- `1`: Toggle voice layer
- `2`: Toggle item layer (emit sounds)
- `3`: Toggle media layer (radio)
- `4`: Toggle world layer (other-user world sounds)
- `Shift+4`: Toggle HRTF spatial audio (saved per browser; standard mode is the default)
- `Shift+E`: Effect select menu in standard or HRTF mode
- `-` / `=`: Lower/raise master volume
- `_` / `+` (`Shift+-` / `Shift+=`): Lower/raise active effect value

## Text Entry Modes (`nickname`, `chat`, `itemPropertyEdit`, `worldBuilderPropertyEdit`)

- `Enter`: Confirm
- `Escape`: Cancel
- `ArrowLeft` / `ArrowRight`: Move cursor by character
- `Ctrl+ArrowLeft` / `Ctrl+ArrowRight`: Move cursor by word (notepad-style)
- `Home` / `End`: Move to start/end
- `Backspace`: Delete previous character
- `Delete`: Delete current character
- `Ctrl+A`: Select all (replace-on-next-type)
- `Ctrl+C`: Copy current text
- `Ctrl+X`: Cut current text
- `Ctrl+V`: Paste
- `Cmd+A` / `Cmd+C` / `Cmd+X` / `Cmd+V` (macOS): same behavior as `Ctrl` shortcuts above

## Numeric Edit Fields

- `ArrowUp` / `ArrowDown`: Step value
- `PageUp` / `PageDown`: Step by 10 increments

## Menu/List Navigation Modes

Applies to effect select, user/item list modes, item selection, item property list, and the shared option selector.

- `ArrowUp` / `ArrowDown`: Move selection
- `PageUp` / `PageDown` in item property list: Jump 10 values for left/right-editable option fields
- `PageUp` / `PageDown` in the option selector: Jump 10 options backward/forward
- `ArrowLeft` / `ArrowRight` in user list: Lower/raise selected user listen volume (`0.5..4.0`)
- `Enter`: Confirm selection
- `Escape`: Exit/cancel
- `Space`: Read tooltip/help for current option (where metadata is available)
- First-letter navigation: jump to next matching entry

## Command Palette

- Available in `normal` mode and `pianoUse` mode
- Opens with `Shift+K`, `Applications`, or `Shift+F10`
- Shows only commands available in the current mode/context
- `ArrowUp` / `ArrowDown`: Move selection
- `Enter`: Run selected command
- `Escape`: Close palette and return to prior mode
- `Space`: Read tooltip/help for selected command
- First-letter navigation: jump to next matching command

## Yes/No Confirmation Menu

- `ArrowUp` / `ArrowDown`: Move between `No` and `Yes`
- `Enter`: Confirm current choice (default selection is `No`)
- `Escape`: Cancel

## Users Menu

- `Shift+Z`: Open users
- `Space` on root actions: Read tooltip/help for the selected action
- Menu options are permission-gated and include:
  - list registered users with role, account status, and online/last-seen presence (`user.list`; built-in user, editor, and admin roles)
  - role management
  - change user role
  - ban user
  - unban user
  - delete account
- In admin role management:
  - role list includes role user-counts
  - `Enter` on role opens permission toggles
  - `Enter` on `Add role` opens role name editor
  - role delete prompts replacement role selection

## Piano Use Mode

- `1-9` (and `0` for the 10th slot): Switch instrument preset quickly
- `A S D F G H J K L ; '`: Play white keys (C major from C4 upward)
- `W E T Y U O P ]`: Play sharps
- Multiple keys can be held/played at once
- Shifted note keys are ignored
- `?`: Open piano-mode help viewer
- `-` / `=`: Shift octave down/up
- `Z`: Start, pause, or resume recording on this piano (max 30s recorded time)
- `X`: Play back saved recording on this piano (stops demo first)
- `Enter`: Play demo melody (press again to restart; stops recording playback first)
- `C`: Stop demo, recording playback, and active recording
- `Escape`: Exit piano mode

## Help Viewer Mode

- `ArrowUp` / `ArrowDown`: Previous/next help line
- `Home` / `End`: First/last help line
- `Escape`: Exit help viewer
- No first-letter navigation in this mode
