# desktop

An Electron application with React and TypeScript

## Recommended IDE Setup

- [VSCode](https://code.visualstudio.com/) + [ESLint](https://marketplace.visualstudio.com/items?itemName=dbaeumer.vscode-eslint) + [Prettier](https://marketplace.visualstudio.com/items?itemName=esbenp.prettier-vscode)

## Project Setup

### Install

```bash
$ npm install
```

### Development

```bash
$ npm run dev
```

### Build

```bash
# For windows
$ npm run build:win

# For macOS
$ npm run build:mac

# For Linux
$ npm run build:linux
```

## Pipeline binding (v0.2.5)

Pluto reads `<repo>/.aidev/slices/*/state.json` (and `.aidev/epics/*/state.json`)
and writes exactly one kind of file back:

```
<repo>/.aidev/slices/<slice-id>/approvals/<stage>.md
```

Judgement stays in the Python core. `writeApproval` is the only capability in
`src/preload/index.ts` that writes into a target repository; everything else the
renderer can reach is a read or a main-owned command. Every other screen
(Graph / Code / Diff / Test / Browser) is still mock data, and none of them is
reachable from the tab bar any more: the files stay, the routing is gone. The
tab bar is Plan and Graph — and Graph is the Function DB, read from
`.aidev/graph/graph.db`.

### Unit tests

```bash
$ npm test --prefix desktop
```

That is `typecheck` + `tsc -p tsconfig.test.json` + `node --test`. The modules
under test (`src/main/aidev-store.ts`, `src/main/recent-repos.ts`) import no
Electron, which is what lets them run headless.

### Manual verification

Approving from the UI has to actually move the pipeline, and no unit test can
show that. Do this once per change to the binding:

1. Terminal A: `aidev pipeline --repo <repo> --requirement tasks/<x>.md`
   Wait for `[pipeline] waiting for approval of 'plan'`.
2. Terminal B: `cd desktop && npm run dev`
3. Activity bar → **Pipeline** → **Open…** → pick `<repo>`.
   The slice list shows `<slice-id>` with `waiting_approval:plan` highlighted.
4. Select that slice. The **Plan** surface shows `plan.md` in full.
5. Click **Approve**.
   - Within ~2s the row turns into `running:implement`.
   - Terminal A prints `[pipeline] gate 'plan': approved` and carries on.
6. Rejection (use a second slice): type a reason, click **Reject**.
   - `approvals/plan.md`'s first non-comment line is `rejected: <reason>`.
   - `state.json` moves to `rejected` and keeps the reason.

Windows note: a VS Code integrated terminal passes `ELECTRON_RUN_AS_NODE=1`
down, which makes `npm run dev` start Electron as plain Node and die. Clear the
variable in that terminal, or use a normal one.
