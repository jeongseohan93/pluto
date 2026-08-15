/**
 * v0.0.1 mock data. Owned by the main process so the renderer can only reach it
 * through the preload bridge — the same path real `aidev` data will take later.
 */
import type {
  ChangeSummary,
  CodeGraph,
  FileNode,
  ProjectInfo,
  SessionLogs,
  TelemetrySnapshot,
  TestCase,
  WorkspaceSummary
} from '../shared/ide'

export const project: ProjectInfo = {
  name: 'joker',
  root: 'C:\\work\\joker-online',
  branch: 'feature/night-resolution',
  dirtyFiles: 4
}

export const workspaces: WorkspaceSummary[] = [
  {
    id: 'ws-night-role',
    title: 'Night role resolution',
    slice: 'v0.4.2 · slice 3',
    agent: 'Claude Code',
    state: 'running',
    approval: 'pending',
    changedFiles: 4,
    testsPassed: 11,
    testsFailed: 1
  },
  {
    id: 'ws-doctor',
    title: 'Doctor protection',
    slice: 'v0.4.2 · slice 2',
    agent: 'Claude Code',
    state: 'awaiting-approval',
    approval: 'pending',
    changedFiles: 2,
    testsPassed: 7,
    testsFailed: 0
  },
  {
    id: 'ws-guard',
    title: 'Guard block ordering',
    slice: 'v0.4.1 · slice 5',
    agent: 'Claude Code',
    state: 'passed',
    approval: 'approved',
    changedFiles: 3,
    testsPassed: 9,
    testsFailed: 0
  },
  {
    id: 'ws-victory',
    title: 'Victory condition audit',
    slice: 'backlog',
    agent: '—',
    state: 'idle',
    approval: 'not-requested',
    changedFiles: 0,
    testsPassed: 0,
    testsFailed: 0
  }
]

export const projectTree: FileNode[] = [
  {
    path: 'server/services',
    name: 'services',
    kind: 'dir',
    children: [
      {
        path: 'server/services/gameSessionService.js',
        name: 'gameSessionService.js',
        kind: 'file',
        change: 'modified'
      },
      {
        path: 'server/services/nightActionService.js',
        name: 'nightActionService.js',
        kind: 'file',
        change: 'modified'
      },
      {
        path: 'server/services/roleAbilityRegistry.js',
        name: 'roleAbilityRegistry.js',
        kind: 'file'
      }
    ]
  },
  {
    path: 'server/sockets',
    name: 'sockets',
    kind: 'dir',
    children: [
      { path: 'server/sockets/gameSocket.js', name: 'gameSocket.js', kind: 'file' },
      { path: 'server/sockets/lobbySocket.js', name: 'lobbySocket.js', kind: 'file' }
    ]
  },
  {
    path: 'server/tests',
    name: 'tests',
    kind: 'dir',
    children: [
      {
        path: 'server/tests/nightResolution.test.js',
        name: 'nightResolution.test.js',
        kind: 'file',
        change: 'modified'
      },
      {
        path: 'server/tests/doctorProtection.test.js',
        name: 'doctorProtection.test.js',
        kind: 'file',
        change: 'added'
      },
      {
        path: 'server/tests/victoryCondition.test.js',
        name: 'victoryCondition.test.js',
        kind: 'file'
      }
    ]
  }
]

const graph: CodeGraph = {
  width: 980,
  height: 342,
  files: [
    {
      id: 'f:gameSocket',
      path: 'server/sockets/gameSocket.js',
      x: 16,
      y: 112,
      width: 190,
      changed: false,
      scope: 'context',
      symbols: [
        {
          id: 's:handleConnection',
          name: 'handleConnection',
          kind: 'function',
          signature: 'handleConnection(socket)',
          startLine: 18,
          endLine: 46,
          changed: false,
          summary: 'Registers per-socket game event handlers on connect.',
          code: [
            'function handleConnection(socket) {',
            "  socket.on('night:action', (payload) =>",
            '    submitNightAction(socket, payload)',
            '  )',
            "  socket.on('disconnect', () => releaseSeat(socket))",
            '}'
          ].join('\n')
        },
        {
          id: 's:submitNightAction',
          name: 'submitNightAction',
          kind: 'function',
          signature: 'submitNightAction(socket, payload)',
          startLine: 52,
          endLine: 74,
          changed: false,
          summary: 'Socket entry point for a night action submitted by a player.',
          code: [
            'async function submitNightAction(socket, payload) {',
            '  const session = getSession(socket.data.sessionId)',
            '  const error = validateNightAction(session, payload)',
            "  if (error) return socket.emit('night:rejected', error)",
            '',
            '  await recordNightAction(session, payload)',
            '}'
          ].join('\n')
        }
      ]
    },
    {
      id: 'f:nightActionService',
      path: 'server/services/nightActionService.js',
      x: 240,
      y: 40,
      width: 214,
      changed: true,
      scope: 'focus',
      symbols: [
        {
          id: 's:validateNightAction',
          name: 'validateNightAction',
          kind: 'function',
          signature: 'validateNightAction(session, payload)',
          startLine: 12,
          endLine: 38,
          changed: false,
          summary: 'Rejects actions from dead, silenced, or out-of-turn players.',
          code: [
            'function validateNightAction(session, payload) {',
            "  if (session.phase !== 'NIGHT') return 'WRONG_PHASE'",
            '  const actor = session.players[payload.actorId]',
            "  if (!actor?.alive) return 'ACTOR_DEAD'",
            '  return null',
            '}'
          ].join('\n')
        },
        {
          id: 's:recordNightAction',
          name: 'recordNightAction',
          kind: 'function',
          signature: 'recordNightAction(session, payload)',
          startLine: 44,
          endLine: 71,
          changed: true,
          summary: 'Stores the action and advances the turn once every role has acted.',
          code: [
            'async function recordNightAction(session, payload) {',
            '  session.nightActions[payload.actorId] = payload',
            '  await persist(session)',
            '',
            '  if (allRolesActed(session)) {',
            '    return advanceNightTurn(session)',
            '  }',
            '}'
          ].join('\n')
        },
        {
          id: 's:advanceNightTurn',
          name: 'advanceNightTurn',
          kind: 'function',
          signature: 'advanceNightTurn(session)',
          startLine: 77,
          endLine: 104,
          changed: true,
          summary: 'Closes the night window and hands control to resolution.',
          code: [
            'async function advanceNightTurn(session) {',
            "  session.phase = 'NIGHT_RESOLVING'",
            '  const outcome = await resolveNight(session)',
            '',
            "  emit(session, 'game_state_updated', outcome)",
            '  return outcome',
            '}'
          ].join('\n')
        }
      ]
    },
    {
      id: 'f:gameSessionService',
      path: 'server/services/gameSessionService.js',
      x: 490,
      y: 150,
      width: 224,
      changed: true,
      scope: 'focus',
      symbols: [
        {
          id: 's:resolveNight',
          name: 'resolveNight',
          kind: 'function',
          signature: 'resolveNight(session)',
          startLine: 132,
          endLine: 178,
          changed: true,
          summary:
            'Applies every queued night action in priority order, then evaluates the victory condition.',
          code: [
            'async function resolveNight(session) {',
            '  const attacks = resolveJokerAttack(session)',
            '  const saved = resolveDoctorProtection(session, attacks)',
            '',
            '  for (const target of attacks) {',
            '    if (saved.has(target)) continue',
            '    session.players[target].alive = false',
            '  }',
            '',
            '  return checkVictoryCondition(session)',
            '}'
          ].join('\n')
        },
        {
          id: 's:resolveJokerAttack',
          name: 'resolveJokerAttack',
          kind: 'function',
          signature: 'resolveJokerAttack(session)',
          startLine: 184,
          endLine: 203,
          changed: false,
          summary: 'Collects the joker target for the current night.',
          code: [
            'function resolveJokerAttack(session) {',
            "  return actionsOf(session, 'JOKER').map((a) => a.targetId)",
            '}'
          ].join('\n')
        },
        {
          id: 's:resolveDoctorProtection',
          name: 'resolveDoctorProtection',
          kind: 'function',
          signature: 'resolveDoctorProtection(session, attacks)',
          startLine: 209,
          endLine: 241,
          changed: true,
          summary:
            'Returns the set of targets shielded this night. Self-heal is limited to once per game.',
          code: [
            'function resolveDoctorProtection(session, attacks) {',
            '  const saved = new Set()',
            "  for (const a of actionsOf(session, 'DOCTOR')) {",
            '    if (a.actorId === a.targetId && session.selfHealUsed) continue',
            '    if (a.actorId === a.targetId) session.selfHealUsed = true',
            '    if (attacks.includes(a.targetId)) saved.add(a.targetId)',
            '  }',
            '  return saved',
            '}'
          ].join('\n')
        },
        {
          id: 's:checkVictoryCondition',
          name: 'checkVictoryCondition',
          kind: 'function',
          signature: 'checkVictoryCondition(session)',
          startLine: 247,
          endLine: 279,
          changed: false,
          summary: 'Decides whether the game ends after the night is applied.',
          code: [
            'function checkVictoryCondition(session) {',
            '  const alive = Object.values(session.players).filter((p) => p.alive)',
            "  if (alive.every((p) => p.team === 'CITIZEN')) return win('CITIZEN')",
            '  return null',
            '}'
          ].join('\n')
        }
      ]
    },
    {
      id: 'f:nightResolutionTest',
      path: 'server/tests/nightResolution.test.js',
      x: 764,
      y: 40,
      width: 200,
      changed: true,
      scope: 'related',
      symbols: [
        {
          id: 's:testNightOrder',
          name: 'applies actions in priority order',
          kind: 'test',
          signature: "it('applies actions in priority order')",
          startLine: 22,
          endLine: 48,
          changed: true,
          summary: 'Joker attack must be resolved before doctor protection.',
          code: [
            "it('applies actions in priority order', async () => {",
            '  const session = seedNight({ joker: 2, doctor: 2 })',
            '  await resolveNight(session)',
            '  expect(session.players[2].alive).toBe(true)',
            '})'
          ].join('\n')
        },
        {
          id: 's:testVictoryAfterNight',
          name: 'ends the game when only citizens remain',
          kind: 'test',
          signature: "it('ends the game when only citizens remain')",
          startLine: 54,
          endLine: 72,
          changed: false,
          summary: 'Guards the victory check that runs at the end of resolution.',
          code: [
            "it('ends the game when only citizens remain', async () => {",
            '  const session = seedNight({ joker: 3 })',
            '  const result = await resolveNight(session)',
            "  expect(result.winner).toBe('CITIZEN')",
            '})'
          ].join('\n')
        }
      ]
    },
    {
      id: 'f:doctorProtectionTest',
      path: 'server/tests/doctorProtection.test.js',
      x: 764,
      y: 250,
      width: 200,
      changed: true,
      scope: 'related',
      symbols: [
        {
          id: 's:testProtectsTarget',
          name: 'protects the attacked target',
          kind: 'test',
          signature: "it('protects the attacked target')",
          startLine: 14,
          endLine: 34,
          changed: true,
          summary: 'A doctor covering the joker target keeps that player alive.',
          code: [
            "it('protects the attacked target', () => {",
            '  const saved = resolveDoctorProtection(session, [4])',
            '  expect(saved.has(4)).toBe(true)',
            '})'
          ].join('\n')
        },
        {
          id: 's:testSelfHealOnce',
          name: 'allows self heal only once',
          kind: 'test',
          signature: "it('allows self heal only once')",
          startLine: 40,
          endLine: 63,
          changed: true,
          summary: 'Currently failing: the second self heal is still granted.',
          code: [
            "it('allows self heal only once', () => {",
            '  resolveDoctorProtection(session, [1])',
            '  const second = resolveDoctorProtection(session, [1])',
            '  expect(second.has(1)).toBe(false)',
            '})'
          ].join('\n')
        }
      ]
    }
  ],
  edges: [
    { id: 'e1', from: 's:handleConnection', to: 's:submitNightAction', kind: 'calls' },
    { id: 'e2', from: 's:submitNightAction', to: 's:validateNightAction', kind: 'calls' },
    { id: 'e3', from: 's:submitNightAction', to: 's:recordNightAction', kind: 'calls' },
    { id: 'e4', from: 's:recordNightAction', to: 's:advanceNightTurn', kind: 'calls' },
    { id: 'e5', from: 's:advanceNightTurn', to: 's:resolveNight', kind: 'calls' },
    { id: 'e6', from: 's:resolveNight', to: 's:resolveJokerAttack', kind: 'calls' },
    { id: 'e7', from: 's:resolveNight', to: 's:resolveDoctorProtection', kind: 'calls' },
    { id: 'e8', from: 's:resolveNight', to: 's:checkVictoryCondition', kind: 'calls' },
    { id: 'e9', from: 's:resolveNight', to: 's:testNightOrder', kind: 'tested-by' },
    {
      id: 'e10',
      from: 's:checkVictoryCondition',
      to: 's:testVictoryAfterNight',
      kind: 'tested-by'
    },
    { id: 'e11', from: 's:resolveDoctorProtection', to: 's:testProtectsTarget', kind: 'tested-by' },
    { id: 'e12', from: 's:resolveDoctorProtection', to: 's:testSelfHealOnce', kind: 'tested-by' }
  ]
}

const emptyGraph: CodeGraph = { files: [], edges: [], width: 0, height: 0 }

export function graphFor(workspaceId: string): CodeGraph {
  return workspaceId === 'ws-victory' ? emptyGraph : graph
}

const changeSummary: ChangeSummary = {
  filesChanged: 4,
  functionsModified: 6,
  functionsAdded: 2,
  testsAdded: 7,
  affected: ['Night resolution', 'Doctor protection', 'game_state_updated'],
  files: [
    {
      path: 'server/services/gameSessionService.js',
      change: 'modified',
      added: 42,
      removed: 8,
      symbols: ['resolveNight', 'resolveDoctorProtection'],
      hunkHeader: '@@ -209,12 +209,33 @@ resolveDoctorProtection',
      lines: [
        { type: 'context', text: 'function resolveDoctorProtection(session, attacks) {' },
        { type: 'del', text: '  const saved = new Set()' },
        { type: 'del', text: "  for (const a of actionsOf(session, 'DOCTOR')) {" },
        { type: 'del', text: '    if (attacks.includes(a.targetId)) saved.add(a.targetId)' },
        { type: 'add', text: '  const saved = new Set()' },
        { type: 'add', text: "  for (const a of actionsOf(session, 'DOCTOR')) {" },
        { type: 'add', text: '    if (a.actorId === a.targetId && session.selfHealUsed) continue' },
        { type: 'add', text: '    if (a.actorId === a.targetId) session.selfHealUsed = true' },
        { type: 'add', text: '    if (attacks.includes(a.targetId)) saved.add(a.targetId)' },
        { type: 'context', text: '  }' },
        { type: 'context', text: '  return saved' },
        { type: 'context', text: '}' }
      ]
    },
    {
      path: 'server/services/nightActionService.js',
      change: 'modified',
      added: 18,
      removed: 2,
      symbols: ['recordNightAction', 'advanceNightTurn'],
      hunkHeader: '@@ -77,6 +77,14 @@ advanceNightTurn',
      lines: [
        { type: 'context', text: 'async function advanceNightTurn(session) {' },
        { type: 'del', text: "  session.phase = 'DAY'" },
        { type: 'add', text: "  session.phase = 'NIGHT_RESOLVING'" },
        { type: 'add', text: '  const outcome = await resolveNight(session)' },
        { type: 'add', text: '' },
        { type: 'add', text: "  emit(session, 'game_state_updated', outcome)" },
        { type: 'add', text: '  return outcome' },
        { type: 'context', text: '}' }
      ]
    },
    {
      path: 'server/tests/doctorProtection.test.js',
      change: 'added',
      added: 74,
      removed: 0,
      symbols: ['protects the attacked target', 'allows self heal only once'],
      hunkHeader: '@@ -0,0 +1,74 @@',
      lines: [
        { type: 'add', text: "describe('doctor protection', () => {" },
        { type: 'add', text: "  it('protects the attacked target', () => {" },
        { type: 'add', text: '    const saved = resolveDoctorProtection(session, [4])' },
        { type: 'add', text: '    expect(saved.has(4)).toBe(true)' },
        { type: 'add', text: '  })' },
        { type: 'add', text: '})' }
      ]
    },
    {
      path: 'server/tests/nightResolution.test.js',
      change: 'modified',
      added: 22,
      removed: 3,
      symbols: ['applies actions in priority order'],
      hunkHeader: '@@ -22,4 +22,23 @@ applies actions in priority order',
      lines: [
        { type: 'context', text: "it('applies actions in priority order', async () => {" },
        { type: 'del', text: '  const session = seedNight({ joker: 2 })' },
        { type: 'add', text: '  const session = seedNight({ joker: 2, doctor: 2 })' },
        { type: 'add', text: '  await resolveNight(session)' },
        { type: 'add', text: '  expect(session.players[2].alive).toBe(true)' },
        { type: 'context', text: '})' }
      ]
    }
  ]
}

const emptyChangeSummary: ChangeSummary = {
  filesChanged: 0,
  functionsModified: 0,
  functionsAdded: 0,
  testsAdded: 0,
  affected: [],
  files: []
}

export function changeSummaryFor(workspaceId: string): ChangeSummary {
  return workspaceId === 'ws-victory' ? emptyChangeSummary : changeSummary
}

const tests: TestCase[] = [
  {
    id: 't1',
    name: 'applies actions in priority order',
    file: 'server/tests/nightResolution.test.js',
    state: 'passed',
    durationMs: 34,
    covers: ['resolveNight']
  },
  {
    id: 't2',
    name: 'ends the game when only citizens remain',
    file: 'server/tests/nightResolution.test.js',
    state: 'passed',
    durationMs: 21,
    covers: ['checkVictoryCondition']
  },
  {
    id: 't3',
    name: 'protects the attacked target',
    file: 'server/tests/doctorProtection.test.js',
    state: 'passed',
    durationMs: 12,
    covers: ['resolveDoctorProtection']
  },
  {
    id: 't4',
    name: 'allows self heal only once',
    file: 'server/tests/doctorProtection.test.js',
    state: 'failed',
    durationMs: 15,
    covers: ['resolveDoctorProtection']
  },
  {
    id: 't5',
    name: 'rejects actions from dead players',
    file: 'server/tests/nightResolution.test.js',
    state: 'passed',
    durationMs: 9,
    covers: ['validateNightAction']
  },
  {
    id: 't6',
    name: 'ignores duplicate submissions',
    file: 'server/tests/nightResolution.test.js',
    state: 'skipped',
    durationMs: 0,
    covers: ['recordNightAction']
  }
]

export function testsFor(workspaceId: string): TestCase[] {
  return workspaceId === 'ws-victory' ? [] : tests
}

export const telemetry: TelemetrySnapshot = {
  agent: 'Claude Code',
  model: 'claude-opus-5',
  state: 'running',
  sessionUsagePct: 61,
  contextTokens: 31_200,
  contextLimit: 200_000,
  turns: 8,
  inputTokens: 4_820,
  cacheCreationTokens: 18_400,
  cacheReadTokens: 171_000,
  outputTokens: 2_940,
  peakContextTokens: 38_600,
  costUsd: 0.42,
  currentTool: 'Edit',
  currentTarget: 'server/services/gameSessionService.js',
  repeatedReads: 3
}

export const sessionLogs: SessionLogs = {
  agent: [
    { ts: '21:04:11', level: 'info', text: 'slice 3 — night role resolution' },
    { ts: '21:04:12', level: 'info', text: 'workspace scope: 4 files, 9 symbols' },
    { ts: '21:04:18', level: 'tool', text: 'Read  server/services/gameSessionService.js:132-178' },
    { ts: '21:04:26', level: 'tool', text: 'Edit  resolveDoctorProtection  +21 -3' },
    { ts: '21:04:31', level: 'tool', text: 'Write server/tests/doctorProtection.test.js' },
    { ts: '21:04:44', level: 'warn', text: 'repeated read of gameSessionService.js (3x)' },
    { ts: '21:04:52', level: 'tool', text: 'Bash  npm test -- doctorProtection' },
    { ts: '21:05:03', level: 'error', text: '1 failing: allows self heal only once' },
    { ts: '21:05:04', level: 'info', text: 'awaiting approval before next edit' }
  ],
  terminal: [
    {
      ts: '21:03:58',
      level: 'info',
      text: '$ aidev run --task tasks/night-resolution.md --agent claude'
    },
    { ts: '21:03:59', level: 'info', text: 'run 20260815-210359-night-resolution' },
    {
      ts: '21:04:00',
      level: 'info',
      text: 'telemetry: data/runs/20260815-210359-night-resolution'
    },
    { ts: '21:04:11', level: 'ok', text: 'agent session started (stream-json)' },
    { ts: '21:05:04', level: 'warn', text: 'paused: approval gate' }
  ],
  test: [
    { ts: '21:04:53', level: 'info', text: '$ npm test -- doctorProtection nightResolution' },
    { ts: '21:04:58', level: 'ok', text: 'PASS server/tests/nightResolution.test.js (2)' },
    { ts: '21:05:02', level: 'error', text: 'FAIL server/tests/doctorProtection.test.js (1/2)' },
    { ts: '21:05:02', level: 'error', text: '  ● allows self heal only once' },
    { ts: '21:05:02', level: 'error', text: '    expected false, received true' },
    { ts: '21:05:03', level: 'info', text: 'Tests: 1 failed, 4 passed, 1 skipped' }
  ]
}
