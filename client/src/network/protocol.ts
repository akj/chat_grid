import { z } from 'zod';

export const facingDegSchema = z.union([
  z.literal(0),
  z.literal(45),
  z.literal(90),
  z.literal(135),
  z.literal(180),
  z.literal(225),
  z.literal(270),
  z.literal(315),
]);

export type FacingDeg = z.infer<typeof facingDegSchema>;

const actionMetadataSchema = z.object({
  id: z.string(),
  label: z.string(),
  tooltip: z.string().optional(),
});

export const itemSchema = z.object({
  id: z.string(),
  type: z.string().min(1),
  title: z.string(),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  createdBy: z.string(),
  updatedBy: z.string(),
  createdAt: z.number().int(),
  updatedAt: z.number().int(),
  version: z.number().int(),
  capabilities: z.array(z.string()),
  useSound: z.string().optional(),
  emitSound: z.string().optional(),
  params: z.record(z.string(), z.unknown()),
  carrierId: z.string().nullable().optional(),
  display: z.record(z.string(), z.string()).optional(),
  occupiedOffsets: z.array(
    z.object({ x: z.number().int(), y: z.number().int() }),
  ),
});

export const wallStructureSchema = z.object({
  id: z.string(),
  floorZ: z.number().int(),
  startX: z.number().int(),
  startY: z.number().int(),
  orientation: z.enum(['horizontal', 'vertical']),
  length: z.number().int().positive(),
  title: z.string(),
  movementBlocked: z.boolean(),
  soundTransmission: z.number().min(0).max(1),
  occlusionLowpassHz: z.number().int().min(20).max(20_000),
  height: z.number().int().nonnegative(),
  preset: z.string(),
  contactSound: z.string(),
});

const structurePresetSchema = z.object({
  id: z.string(),
  title: z.string(),
  movementBlocked: z.boolean(),
  soundTransmission: z.number().min(0).max(1),
  occlusionLowpassHz: z.number().int().min(20).max(20_000),
  height: z.number().int().nonnegative(),
  contactSound: z.string(),
});

export const welcomeMessageSchema = z.object({
  type: z.literal('welcome'),
  id: z.string(),
  player: z.object({
    id: z.string(),
    userId: z.string().nullable().optional(),
    nickname: z.string(),
    x: z.number().int(),
    y: z.number().int(),
    z: z.number().int(),
    facingDeg: facingDegSchema,
    acousticZoneId: z.string().min(1),
  }),
  users: z.array(
    z.object({
      id: z.string(),
      userId: z.string().nullable().optional(),
      nickname: z.string(),
      x: z.number().int(),
      y: z.number().int(),
      z: z.number().int(),
      facingDeg: facingDegSchema,
      acousticZoneId: z.string().min(1),
    }),
  ),
  items: z.array(itemSchema).optional(),
  structures: z.array(wallStructureSchema).optional(),
  worldConfig: z
    .object({
      gridSize: z.number().int().positive(),
      movementTickMs: z.number().int().positive().optional(),
      movementMaxStepsPerTick: z.number().int().positive().optional(),
      floors: z.array(
        z.object({
          id: z.string().min(1),
          name: z.string().min(1),
          z: z.number().int(),
        }),
      ),
      structurePresets: z.array(structurePresetSchema).optional(),
    })
    .optional(),
  serverInfo: z
    .object({
      instanceId: z.string(),
      releaseVersion: z.string().optional(),
      serverVersion: z.string().optional(),
      expectedClientRevision: z.string().optional(),
      gridName: z.string().optional(),
      welcomeMessage: z.string().optional(),
    })
    .optional(),
  auth: z
    .object({
      authenticated: z.boolean(),
      userId: z.string().nullable().optional(),
      username: z.string().nullable().optional(),
      role: z.string().nullable().optional(),
      permissions: z.array(z.string()).optional(),
      adminMenuActions: z.array(actionMetadataSchema).optional(),
      policy: z
        .object({
          usernameMinLength: z.number().int().positive(),
          usernameMaxLength: z.number().int().positive(),
          passwordMinLength: z.number().int().positive(),
          passwordMaxLength: z.number().int().positive(),
        })
        .optional(),
    })
    .optional(),
  uiDefinitions: z
    .object({
      itemTypeOrder: z.array(z.string().min(1)),
      itemTypes: z.array(
        z.object({
          type: z.string().min(1),
          label: z.string().optional(),
          tooltip: z.string().optional(),
          editableProperties: z.array(z.string()),
          capabilities: z.array(z.string()).optional(),
          propertyMetadata: z
            .record(
              z.string(),
              z.object({
                valueType: z.enum(['boolean', 'text', 'number', 'list', 'sound']).optional(),
                label: z.string().optional(),
                tooltip: z.string().optional(),
                maxLength: z.number().int().positive().optional(),
                options: z.array(z.string()).optional(),
                visibleWhen: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])).optional(),
                range: z
                  .object({
                    min: z.number(),
                    max: z.number(),
                    step: z.number().optional(),
                  })
                  .optional(),
              }),
            )
            .optional(),
          globalProperties: z.record(z.string(), z.unknown()).optional(),
        }),
      ),
      adminMenu: z
        .object({
          actions: z.array(actionMetadataSchema),
        })
        .optional(),
      commandMetadata: z
        .object({
          mainModeActions: z.array(actionMetadataSchema).optional(),
        })
        .optional(),
      itemManagement: z
        .object({
          actions: z.array(
            actionMetadataSchema.extend({
              anyPermission: z.string().optional(),
              ownPermission: z.string().optional(),
            }),
          ),
        })
        .optional(),
    })
    .optional(),
});

export const authRequiredSchema = z.object({
  type: z.literal('auth_required'),
  message: z.string(),
  gridName: z.string().optional(),
  welcomeMessage: z.string().optional(),
  releaseVersion: z.string().optional(),
  expectedClientRevision: z.string().optional(),
  serverVersion: z.string().optional(),
  authPolicy: z
    .object({
      usernameMinLength: z.number().int().positive(),
      usernameMaxLength: z.number().int().positive(),
      passwordMinLength: z.number().int().positive(),
      passwordMaxLength: z.number().int().positive(),
    })
    .optional(),
});

export const authResultSchema = z.object({
  type: z.literal('auth_result'),
  ok: z.boolean(),
  message: z.string(),
  sessionToken: z.string().optional(),
  username: z.string().optional(),
  role: z.string().optional(),
  permissions: z.array(z.string()).optional(),
  adminMenuActions: z.array(actionMetadataSchema).optional(),
  nickname: z.string().optional(),
  authPolicy: z
    .object({
      usernameMinLength: z.number().int().positive(),
      usernameMaxLength: z.number().int().positive(),
      passwordMinLength: z.number().int().positive(),
      passwordMaxLength: z.number().int().positive(),
    })
    .optional(),
});

export const livekitTokenSchema = z.object({
  type: z.literal('livekit_token'),
  token: z.string().min(1),
  url: z.string().url(),
});

export const updatePositionSchema = z.object({
  type: z.literal('update_position'),
  id: z.string(),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  facingDeg: facingDegSchema,
  acousticZoneId: z.string().min(1),
});

export const teleportCompleteSchema = z.object({
  type: z.literal('teleport_complete'),
  id: z.string(),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  facingDeg: facingDegSchema,
  acousticZoneId: z.string().min(1),
});

export const updateNicknameSchema = z.object({
  type: z.literal('update_nickname'),
  id: z.string(),
  nickname: z.string().min(1).max(32),
});

export const userLeftSchema = z.object({
  type: z.literal('user_left'),
  id: z.string(),
});

export const chatMessageSchema = z.object({
  type: z.literal('chat_message'),
  message: z.string(),
  senderId: z.string().optional(),
  senderNickname: z.string().optional(),
  system: z.boolean().optional(),
  action: z.boolean().optional(),
});

export const pongSchema = z.object({
  type: z.literal('pong'),
  clientSentAt: z.number().int(),
});

export const nicknameResultSchema = z.object({
  type: z.literal('nickname_result'),
  accepted: z.boolean(),
  requestedNickname: z.string(),
  effectiveNickname: z.string(),
  reason: z.string().optional(),
});

export const itemUpsertSchema = z.object({
  type: z.literal('item_upsert'),
  item: itemSchema,
});

export const itemRemoveSchema = z.object({
  type: z.literal('item_remove'),
  itemId: z.string(),
});

export const itemActionResultSchema = z.object({
  type: z.literal('item_action_result'),
  ok: z.boolean(),
  action: z.enum(['add', 'pickup', 'drop', 'delete', 'transfer', 'hand', 'use', 'secondary_use', 'update']),
  message: z.string(),
  itemId: z.string().optional(),
});

const itemTargetSummarySchema = z.object({
  userId: z.string(),
  username: z.string(),
  online: z.boolean(),
});

export const itemTransferTargetsSchema = z.object({
  type: z.literal('item_transfer_targets'),
  itemId: z.string(),
  targets: z.array(itemTargetSummarySchema),
});

export const itemHandTargetsSchema = z.object({
  type: z.literal('item_hand_targets'),
  itemId: z.string(),
  targets: z.array(itemTargetSummarySchema),
});

export const itemUseSoundSchema = z.object({
  type: z.literal('item_use_sound'),
  itemId: z.string(),
  sound: z.string(),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  acousticZoneId: z.string().min(1),
  range: z.number().int().positive().optional(),
});

export const itemClockAnnounceSchema = z.object({
  type: z.literal('item_clock_announce'),
  itemId: z.string(),
  sounds: z.array(z.string()),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  acousticZoneId: z.string().min(1),
  range: z.number().int().positive().optional(),
});

export const itemPianoNoteSchema = z.object({
  type: z.literal('item_piano_note'),
  itemId: z.string(),
  senderId: z.string(),
  keyId: z.string(),
  midi: z.number().int().min(0).max(127),
  on: z.boolean(),
  instrument: z.string(),
  voiceMode: z.enum(['mono', 'poly']),
  octave: z.number().int().min(-2).max(2),
  attack: z.number().int().min(0).max(100),
  decay: z.number().int().min(0).max(100),
  release: z.number().int().min(0).max(100),
  brightness: z.number().int().min(0).max(100),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  emitRange: z.number().int().min(1),
});

export const itemPianoStatusSchema = z.object({
  type: z.literal('item_piano_status'),
  itemId: z.string(),
  event: z.enum([
    'use_mode_entered',
    'record_started',
    'record_paused',
    'record_resumed',
    'record_stopped',
    'playback_started',
    'playback_stopped',
  ]),
  recordingState: z.enum(['idle', 'recording', 'paused', 'playback']).optional(),
});

export const itemElevatorStatusSchema = z.object({
  type: z.literal('item_elevator_status'),
  itemId: z.string(),
  event: z.enum(['entered', 'moving', 'arrived', 'exited']),
  z: z.number().int(),
  message: z.string().optional(),
});

export const authPermissionsSchema = z.object({
  type: z.literal('auth_permissions'),
  role: z.string(),
  permissions: z.array(z.string()),
  adminMenuActions: z.array(actionMetadataSchema).optional(),
});

const adminRoleSummarySchema = z.object({
  id: z.number().int(),
  name: z.string(),
  isSystem: z.boolean(),
  userCount: z.number().int().nonnegative(),
  permissions: z.array(z.string()),
});

export const adminRolesListSchema = z.object({
  type: z.literal('admin_roles_list'),
  roles: z.array(adminRoleSummarySchema),
  permissionKeys: z.array(z.string()),
  permissionTooltips: z.record(z.string(), z.string()).optional(),
});

export const adminUsersListSchema = z.object({
  type: z.literal('admin_users_list'),
  users: z.array(
    z.object({
      id: z.string(),
      username: z.string(),
      role: z.string(),
      status: z.enum(['active', 'disabled']),
      lastSeenAt: z.number().int().nonnegative(),
      online: z.boolean(),
    }),
  ),
});

export const adminActionResultSchema = z.object({
  type: z.literal('admin_action_result'),
  ok: z.boolean(),
  action: z.enum([
    'role_create',
    'role_update_permissions',
    'role_delete',
    'user_list',
    'user_set_role',
    'user_ban',
    'user_unban',
    'user_delete',
  ]),
  message: z.string(),
});

export const structureUpsertSchema = z.object({
  type: z.literal('structure_upsert'),
  structure: wallStructureSchema,
});

export const structureRemoveSchema = z.object({
  type: z.literal('structure_remove'),
  structureId: z.string(),
});

export const structureActionResultSchema = z.object({
  type: z.literal('structure_action_result'),
  ok: z.boolean(),
  action: z.enum(['add', 'resize', 'slide', 'rotate', 'update', 'delete']),
  message: z.string(),
  structureId: z.string().optional(),
});

export const worldSoundSchema = z.object({
  type: z.literal('world_sound'),
  sound: z.string(),
  x: z.number().int(),
  y: z.number().int(),
  z: z.number().int(),
  acousticZoneId: z.string().min(1),
  range: z.number().int().positive().optional(),
});

export const incomingMessageSchema = z.discriminatedUnion('type', [
  authRequiredSchema,
  authResultSchema,
  welcomeMessageSchema,
  livekitTokenSchema,
  updatePositionSchema,
  teleportCompleteSchema,
  updateNicknameSchema,
  userLeftSchema,
  chatMessageSchema,
  pongSchema,
  nicknameResultSchema,
  itemUpsertSchema,
  itemRemoveSchema,
  itemActionResultSchema,
  itemTransferTargetsSchema,
  itemHandTargetsSchema,
  itemUseSoundSchema,
  itemClockAnnounceSchema,
  itemPianoNoteSchema,
  itemPianoStatusSchema,
  itemElevatorStatusSchema,
  authPermissionsSchema,
  adminRolesListSchema,
  adminUsersListSchema,
  adminActionResultSchema,
  structureUpsertSchema,
  structureRemoveSchema,
  structureActionResultSchema,
  worldSoundSchema,
]);

export type IncomingMessage = z.infer<typeof incomingMessageSchema>;

export type OutgoingMessage =
  | { type: 'auth_register'; username: string; password: string; email?: string }
  | { type: 'auth_login'; username: string; password: string }
  | { type: 'auth_resume'; sessionToken: string }
  | { type: 'auth_logout' }
  | { type: 'welcome_ready' }
  | { type: 'livekit_token_request' }
  | { type: 'admin_roles_list' }
  | { type: 'admin_role_create'; name: string }
  | { type: 'admin_role_update_permissions'; role: string; permissions: string[] }
  | { type: 'admin_role_delete'; role: string; replacementRole: string }
  | { type: 'admin_users_list'; action?: 'set_role' | 'ban' | 'unban' | 'delete_account' }
  | { type: 'admin_user_set_role'; username: string; role: string }
  | { type: 'admin_user_ban'; username: string }
  | { type: 'admin_user_unban'; username: string }
  | { type: 'admin_user_delete'; username: string }
  | { type: 'update_position'; x: number; y: number; z: number }
  | { type: 'update_facing'; facingDeg: FacingDeg }
  | { type: 'turn'; direction: 'left' | 'right' }
  | { type: 'teleport_complete'; x: number; y: number; z: number }
  | { type: 'update_nickname'; nickname: string }
  | { type: 'chat_message'; message: string }
  | { type: 'ping'; clientSentAt: number }
  | { type: 'item_add'; itemType: string }
  | { type: 'item_pickup'; itemId: string }
  | { type: 'item_drop'; itemId: string; x: number; y: number; z: number }
  | { type: 'item_delete'; itemId: string }
  | { type: 'item_transfer_targets'; itemId: string }
  | { type: 'item_transfer'; itemId: string; targetUserId: string }
  | { type: 'item_hand_targets'; itemId: string }
  | { type: 'item_hand'; itemId: string; targetUserId: string }
  | { type: 'item_use'; itemId: string }
  | { type: 'item_secondary_use'; itemId: string }
  | { type: 'structure_add_wall'; preset: string; direction: 'north' | 'south' | 'east' | 'west' }
  | { type: 'structure_resize_wall'; structureId: string; endpoint: 'start' | 'end'; delta: -1 | 1 }
  | { type: 'structure_slide_wall'; structureId: string; delta: -1 | 1 }
  | { type: 'structure_rotate_wall'; structureId: string; orientation: 'horizontal' | 'vertical' }
  | {
      type: 'structure_update_wall';
      structureId: string;
      preset?: string;
      soundTransmission?: number;
      occlusionLowpassHz?: number;
      contactSound?: string;
    }
  | { type: 'structure_delete'; structureId: string }
  | { type: 'item_piano_note'; itemId: string; keyId: string; midi: number; on: boolean }
  | { type: 'item_piano_recording'; itemId: string; action: 'toggle_record' | 'playback' | 'stop_playback' | 'stop_record' }
  | {
      type: 'item_update';
      itemId: string;
      title?: string;
      params?: Record<string, unknown>;
    };

export type RemoteUser = {
  id: string;
  userId?: string | null;
  nickname: string;
  x: number;
  y: number;
  z: number;
  facingDeg: number;
  acousticZoneId: string;
};
