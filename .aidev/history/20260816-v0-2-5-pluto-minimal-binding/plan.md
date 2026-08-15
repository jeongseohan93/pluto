# v0.2.5 — Pluto 최소 바인딩 구현 계획

## 0. 실제 코드에서 확인한 사실 (추측 아님)

| 사실 | 위치 |
| --- | --- |
| slice 상태 디렉터리는 `slices_root(repo) = <repo>/.aidev/slices`, epic은 `<repo>/.aidev/epics` | `aidev/pipeline.py:322-323`, `aidev/epic.py:59-60` |
| slice `state.json` 키: `schema` / `slice_id` / `status` / `repo` / `gates` / `stage_order` / `stages{name:{status,attempts,session_id,failures,run_id,approval,approval_reason,verdict}}` / `quota` / `commits` / `created_at` / `updated_at` / (옵션) `workspace` `setup` `epic` `mutated` `test_verdict` `reason` | `aidev/pipeline.py:515-541`, 실제 파일 `.aidev/slices/20260815-v0-3-workspace-isolation/state.json` |
| slice status 값: `pending` / `running:<stage>` / `waiting_approval:<stage>` / `quota_wait` / `done` / `failed` / `rejected` / `merged` / `discarded` | `aidev/pipeline.py:118-124`, `1227`, `1533` |
| **stages 키 집합은 고정이 아니다.** reader는 모르는 stage를 그대로 표시해야 한다 | `README.md:422-426`, `aidev/pipeline.py:2548-2553` (`--list`가 실제로 그렇게 한다) |
| 승인 대기 진입점은 `await_approval()`. 파일이 없으면 `APPROVAL_TEMPLATE`(전부 `#` 주석)을 쓰고 `cfg.poll_interval`(기본 3초)로 `read_decision(path)`를 폴링한다 | `aidev/pipeline.py:1512-1550` |
| **`read_decision`의 규약**: BOM 제거 → 줄 단위로 첫 번째 "빈 줄도 `#`도 `<!--`도 아닌 줄"이 결정이다. `:` 앞 단어를 lower + `.!` 제거 후 `approved`/`approve` → APPROVED, `rejected`/`reject` → REJECTED(뒤가 사유). **그 외 문자열이면 PENDING을 반환하고 즉시 끝낸다** (뒤 줄을 더 보지 않는다) | `aidev/pipeline.py:299-316` |
| 읽기 실패(파일 없음/잠김)는 `OSError`를 삼키고 PENDING. 인코딩은 `utf-8, errors="replace"` | `aidev/pipeline.py:301-304` |
| `read_json_tolerant`: 파일 없음 / 잠김 / 잘림 / JSON 아님 / **dict가 아님** 전부 `None`. 절대 raise 안 함 | `aidev/storage.py:286-301` |
| 원자적 쓰기 규약: `<name>.tmp`에 쓰고 `os.replace`. Windows에서 reader가 열고 있으면 `PermissionError`가 나므로 **20회 × 0.05초** 재시도 후 raise | `aidev/storage.py:255-283` |
| STALE 규약: `live.json`의 `status == RUNNING`인데 마지막 갱신이 `STALE_AFTER_S = 10.0`초를 넘으면 STALE | `aidev/reporter.py:177-195` |
| `live.json` 위치는 run 디렉터리. slice → run 연결은 `runs.json`의 `runs[].run_dir` | `aidev/pipeline.py:1290-1308`, `storage.py:163-164` |
| epic `state.json`: `epic_id` / `status`(`running:decompose`/`waiting_approval:decompose`/`running:slice`/…) / `current` / `slices[]{index,title,requirement,slice_id,branch,start,status}` / `gates:["decompose"]`. **`slices[].status`는 투영이고 authoritative는 각 slice의 state.json** | `aidev/epic.py:152-172`, `332-338`, `README.md:363-370` |
| epic 산출물은 `slices.md`, 승인 파일은 `approvals/decompose.md` | `aidev/epic.py:90-91`, `115-116` |
| desktop preload는 `AidevBridge` 인터페이스 하나를 `contextBridge.exposeInMainWorld('aidev', …)`로 노출. 채널 이름은 `src/shared/ide.ts`의 `IPC` 상수 하나에 모여 있다 | `desktop/src/preload/index.ts:11-22`, `desktop/src/shared/ide.ts:166-176` |
| `sandbox: true` / `contextIsolation: true` / `nodeIntegration: false` | `desktop/src/main/index.ts:22-29` |
| main의 IPC 핸들러는 `registerIdeHandlers()` 한 곳. 전부 `mock-data`를 그대로 반환한다 | `desktop/src/main/index.ts:90-101` |
| `@shared` alias는 **renderer 전용**. main/preload는 상대경로 `../shared/ide`를 쓴다 | `desktop/electron.vite.config.ts:9-17`, `desktop/src/main/mock-data.ts:14` |
| **desktop에 테스트 러너가 없다.** `package.json`의 scripts는 format/lint/typecheck/start/dev/build/postinstall/build:* 뿐 | `desktop/package.json:8-22` |
| `package.json`에 `"type"` 필드가 없다 → `.js`는 CommonJS | `desktop/package.json` |
| 이 slice의 front matter: `approval: plan`, **`setup: npm ci --prefix desktop`** | `tasks/v0.2.5-pluto-minimal-binding.md:1-4` |
| setup은 implement 직전 **1회만** 실행된다. 성공하면 `state.setup.status = done`으로 남아 재실행되지 않는다 | `aidev/pipeline.py:1318-1327` |
| implement/test에 허용되는 Bash 규칙 = `TEST_COMMAND_TOOLS` + `setup_tool_rules(setup)` = `npm test` / `npm test:*` / `npm run test:*` / `node --test` / `node --test:*` / `pytest` / `pytest:*` / `python -m pytest:*` / `npm ci --prefix desktop` / `npm ci:*` | `aidev/pipeline.py:82-95`, `261-275`, `968-983` |
| test 단계 프롬프트는 "명령을 **하나의 평범한 명령**으로 실행하라. `&&`나 `cd x && …`로 엮지 마라"고 못박는다 | `aidev/pipeline.py:846-848` |
| pytest 설정: `testpaths = ["tests"]`, `addopts = "-q"` | `pyproject.toml:23-25` |

---

## 1. 이 계획이 내린 결정들

### 1.1 새 의존성을 **한 개도** 추가하지 않는다 (vitest 금지)

가장 중요한 제약이다. `setup`은 implement **직전에 1회** 돌고 끝난다. 여기서 `vitest`를 `package.json`에 넣으면:

- setup은 이미 끝났으므로 vitest가 설치되지 않는다.
- 다시 설치하려면 `npm install`이 필요한데 **allowedTools에 없다** (`npm ci:*`만 있다).
- `npm ci`는 lock과 package.json이 어긋나면 실패한다. vitest의 전체 의존 트리를 손으로 `package-lock.json`에 써넣는 것은 불가능하다.

→ **결론: 이미 있는 `typescript` + Node 내장 `node:test`만 쓴다.**

`package.json`에 스크립트 한 줄을 추가한다:

```json
"test": "npm run typecheck && tsc -p tsconfig.test.json && node --test out-test"
```

- 에이전트가 치는 명령은 `npm test --prefix desktop` **하나**다 → `Bash(npm test:*)`에 매치되고, "체인 금지" 규약도 지킨다 (체인은 npm script 내부에 있지 에이전트의 Bash 명령줄에 있지 않다).
- `npm run typecheck`을 안에 넣는 이유: `Bash(npm run test:*)`는 `test`로 시작하는 스크립트만 허용하므로 test 단계가 typecheck를 따로 못 돌린다. 유일하게 열려 있는 문으로 통과시킨다.
- `node --test <디렉터리>`는 그 아래 `*.test.js`를 자동 발견한다. 만약 사용 중인 Node에서 디렉터리 인자 발견이 동작하지 않으면 `node --test out-test/*.test.js`로 바꾼다 (여전히 `Bash(node --test:*)` 안이다).

### 1.2 기존 mock 화면은 **한 줄도 대체하지 않는다.** 새 activity를 추가한다

요구사항의 "기존 mock 중 이번 세 기능에 해당하는 부분만 실데이터로 교체"를 코드에 비추면, 가장 가까운 것은 Sidebar의 `WorkspacesView`다. 그런데 그 목록의 `ws.id`는 `mock.graphFor(id)` / `changeSummaryFor(id)` / `testsFor(id)`의 **조회 키**다 (`desktop/src/main/index.ts:94-98`). 여기를 실제 slice id로 바꾸면 graph/diff/test mock 화면 세 개가 동시에 깨진다 — 요구사항이 명시적으로 금지한 "나머지 mock 화면을 건드리는" 결과다.

→ **결정: ActivityBar에 `pipeline` 항목을 하나 추가하고, 세 기능은 전부 그 안에서 산다.** mock `workspaces` activity와 graph/code/test/diff/browser surface는 그대로 둔다. 세 기능은 원래 존재하지 않던 화면이라 "교체할 mock"이 없다는 것이 정확한 사실이다.

### 1.3 preload 신규 API: 쓰기는 `writeApproval` **하나뿐**

요구사항 Scope는 "preload에 **write capability**는 이것 하나만 추가한다"이고 Done Criteria는 그것을 줄여 쓴 것이다. 읽기 capability 없이는 기능 1·2가 성립하지 않으므로, 다음과 같이 읽는다:

**대상 repo에 쓰는 preload 멤버는 `writeApproval` 정확히 하나.** 나머지는 읽기/명령이다.

```ts
getRepoState(): Promise<RepoState>                                  // 읽기 (폴링 대상)
openRepoDialog(): Promise<RepoState>                                // 명령 (main이 폴더 다이얼로그를 연다)
selectRepo(root: string): Promise<RepoState>                        // 명령 (최근 목록에서 고르기)
getStageArtifact(sliceId: string, stage: string): Promise<StageArtifact>  // 읽기
writeApproval(input: ApprovalInput): Promise<ApprovalResult>        // ★ 유일한 쓰기
```

`openRepoDialog`/`selectRepo`가 갱신하는 최근 목록 파일은 `app.getPath('userData')` 아래에 있고 **대상 repo 안이 아니다.** 이 사실을 `shared/ide.ts`의 주석에 명시한다.

### 1.4 epic은 **표시만** 한다. 승인 버튼은 slice 전용

요구사항의 기능 1은 epic을 "함께 표시"라고 했고, 기능 3과 Done Criteria는 slice만 말한다. epic의 `decompose` 게이트 승인은 코드상 거의 같지만(같은 `approvals/<stage>.md` 규약), 이번 slice가 요구한 범위 밖이다. → **epic 행은 읽기 전용 상태 표시.** store 함수는 `approvals` 디렉터리 경로를 인자로 받게 짜므로, 나중에 preload API를 하나도 늘리지 않고 epic을 붙일 수 있다.

### 1.5 STALE은 `live.json`에 대해서만, 원래 규약 그대로 적용한다

`state.json`은 heartbeat가 아니라 **단계 전환 시점에만** 갱신된다. 여기에 10초 규칙을 적용하면 정상 실행 중인 slice가 전부 STALE로 뜬다 — 규약을 존중하는 게 아니라 오해하는 것이다.

→ status가 `running:`으로 시작하는 slice에 한해 `runs.json`의 **마지막 항목의 `run_dir`**을 읽고, 거기 `live.json`을 관용적으로 읽어 `reporter`와 **같은 규칙**(`status === 'running' && age > 10s`)으로 STALE을 판정한다. `run_dir`은 도구의 `data/`를 가리키는 절대경로라 이 머신에 없을 수 있는데, 없으면 `null`이고 표시하지 않는다 (관용 규약 그대로).

### 1.6 폴링 주기

renderer가 `getRepoState()`를 **2000ms** 간격으로 부른다 (파이프라인의 승인 폴링 3초와 같은 자릿수, 사람이 버튼을 누른 뒤 "곧" 반응하는 감각). 이전 호출이 끝나기 전에는 다음 호출을 내지 않는다(in-flight 가드). 승인 쓰기가 성공하면 즉시 한 번 갱신한다.

---

## 2. 파일별 변경

### 2.1 신규: `desktop/src/main/aidev-store.ts` — 단위 테스트의 대상

**Electron을 import하지 않는다.** `node:fs`, `node:path`, `node:os`와 `../shared/ide`의 **타입만**(`import type`) 쓴다. 이것이 `tsc -p tsconfig.test.json`으로 뽑아 `node --test`로 돌릴 수 있게 하는 조건이다.

```ts
// 관용적 읽기 — aidev/storage.py read_json_tolerant의 이식
export function readJsonTolerant(file: string): Record<string, unknown> | null
// 없음 / 디렉터리 / 잠김 / 잘림 / JSON 아님 / dict 아님 → 전부 null. 절대 throw 안 함.

export function readTextTolerant(file: string): string | null

// 결정 파서 — aidev/pipeline.py read_decision의 이식 (BOM, '#', '<!--', ':' 분해,
// '.!' 제거, approve/approved · reject/rejected, 그 외 첫 줄이면 pending)
export function readDecision(file: string): { verdict: 'pending'|'approved'|'rejected'; reason: string }

export function listSlices(repoRoot: string): SliceState[]
export function listEpics(repoRoot: string): EpicState[]
export function readStageArtifact(repoRoot: string, sliceId: string, stage: string): StageArtifact
export function writeApproval(repoRoot: string, input: ApprovalInput): ApprovalResult

// 내부
function safeSegment(value: string): string   // 검증 실패 시 throw
function writeTextAtomic(file: string, text: string): void
function liveStatusFor(sliceDir: string): LiveStatus | null
```

세부 규칙:

- **`listSlices`**: `<root>/.aidev/slices`가 없으면 `[]`. 하위 **디렉터리만** 순회(`.lock`, `*.tmp` 같은 파일 무시). 각 디렉터리마다 `state.json`을 `readJsonTolerant`로 읽는다.
  - `null`이면 `{ id, dir, status: '(unreadable)', unreadable: true, stages: [], gates: [], waitingStage: null, updatedAt: null }`.
  - stage 순서는 `stage_order`가 배열이면 그것, 아니면 `stages`의 키 순서. **`stage_order`에 없는 `stages` 키도 뒤에 붙여서 전부 표시한다** (README:422-426의 규약).
  - `waitingStage` = `status`가 `waiting_approval:`로 시작하면 그 뒤 문자열, 아니면 `null`.
  - 정렬: `updated_at` 내림차순(파싱 실패 시 0), 동률이면 id 내림차순.
- **`readStageArtifact`**: stage → 파일 매핑을 상수 하나로 둔다. `plan` → `plan.md`. 그 외 stage는 `plan.md`가 있으면 `plan.md`, 없으면 `requirement.md`. 반환은 `{ stage, path, text, exists }`이고 파일이 없어도 throw하지 않는다(`exists: false`, `text: ''`, `path`는 채운다 — 사람이 경로를 볼 수 있어야 한다).
- **`writeApproval(repoRoot, { sliceId, stage, decision, reason })`**:
  1. `safeSegment(sliceId)`, `safeSegment(stage)` — `/^[A-Za-z0-9][A-Za-z0-9._-]*$/`만 통과, `.`/`..` 거부. 그리고 최종 `path.resolve` 결과가 `path.resolve(repoRoot, '.aidev')` 아래인지 재확인.
  2. `approvals/` 디렉터리를 `mkdirSync(..., {recursive:true})`.
  3. 기존 파일을 `readDecision`으로 먼저 읽는다. verdict가 `pending`이 **아니면** 아무것도 쓰지 않고 `{ ok:false, conflict: <기존 verdict>, path }`를 돌려준다 (이미 결정된 게이트를 UI가 덮어쓰지 않는다).
  4. 결정 줄을 만든다: `approved` 또는 `rejected: <사유>`. 사유는 `\r?\n`과 연속 공백을 공백 하나로 접고 trim한다 — 규약이 **한 줄**이기 때문이다. 사유가 비면 `rejected:`.
  5. 기존 내용(있으면, 전부 주석인 템플릿) + 필요하면 `\n` + 결정 줄 + `\n`을 **`<file>.tmp`에 쓰고 `renameSync`**. Windows에서 파이썬이 그 파일을 읽는 중이면 `EPERM`/`EACCES`/`EBUSY`가 나므로 **20회 × 50ms 재시도**(`storage.write_json_atomic`과 같은 상수), 그래도 안 되면 `{ ok:false, error }`. 인코딩 utf-8, **BOM 없음**, 개행 `\n` 고정.
  6. 쓴 뒤 `readDecision`으로 **다시 읽어** 파이썬이 실제로 읽게 될 결정을 확인하고 `{ ok:true, effective, path }`로 돌려준다. 사람이 미리 이상한 첫 줄을 써놨으면(예: `approve me later` → `read_decision`이 그 줄에서 pending으로 끝난다) 여기서 `ok:false, effective:'pending'`으로 잡혀 UI가 "파일을 직접 확인하라 + 경로"를 띄운다.
- **`liveStatusFor`**: `runs.json`을 `readJsonTolerant` → `runs` 배열의 마지막 항목 → `run_dir` → `<run_dir>/live.json` → `{ status, updated_at }`. `status === 'running' && (Date.now()/1000 - updated_at) > 10` 이면 `stale: true`. 어느 단계든 실패하면 `null`.

### 2.2 신규: `desktop/src/main/recent-repos.ts`

역시 Electron 무의존. `readRecents(file): string[]` / `addRecent(file, root): string[]`. JSON `{ version: 1, recent: string[] }`, 관용적 읽기, 중복 제거(대소문자 무시 — Windows), **최대 8개**, 최신이 앞. 쓰기는 `writeTextAtomic` 재사용.

### 2.3 신규: `desktop/src/main/aidev-store.test.ts`, `desktop/src/main/recent-repos.test.ts`

`node:test` + `node:assert/strict`. `fs.mkdtempSync(path.join(os.tmpdir(), 'pluto-'))`로 fixture repo를 만들고 `after`에서 `rmSync(..., {recursive:true, force:true})`.

케이스 (요구사항이 지정한 세 축 = 깨진 state.json / 없는 폴더 / 승인 파일 형식):

| # | 대상 | 기대 |
| --- | --- | --- |
| 1 | `listSlices`, `.aidev` 자체가 없는 폴더 | `[]`, throw 없음 |
| 2 | `listSlices`, `.aidev/slices`는 있지만 비어 있음 | `[]` |
| 3 | `listSlices`, 실제 형태의 state.json (문서의 v0.3 slice 그대로) | id/status/gates/updatedAt, stages 3개가 `stage_order` 순서 |
| 4 | `listSlices`, state.json이 잘린 JSON (`{"status":`) | 항목은 존재, `unreadable: true`, throw 없음 |
| 5 | `listSlices`, state.json이 배열(`[1,2]`) | `unreadable: true` (dict 아님 = `read_json_tolerant`와 동일) |
| 6 | `listSlices`, state.json 없음 | `unreadable: true` |
| 7 | `waitingStage` | `waiting_approval:plan` → `'plan'`; `running:implement`/`done` → `null` |
| 8 | `stages`에 `stage_order`에 없는 키(`browser`) | 그 stage도 목록에 남는다 |
| 9 | `listSlices`, `schema: 1` (workspace/commits 없음) | 정상 파싱 |
| 10 | 정렬 | `updated_at` 최신이 앞 |
| 11 | `readStageArtifact`, plan.md 존재 | 내용 일치, `exists: true` |
| 12 | `readStageArtifact`, plan.md 없음 | `exists:false`, `text:''`, `path`는 채워짐 |
| 13 | 경로 탈출: sliceId `..`, `../../x`, `a/b`, `C:\x`, stage `..` | throw 또는 `ok:false`, **파일 시스템 변화 0** |
| 14 | `writeApproval` approved, 파일 없음 | 파일 내용이 `approved\n`, `readDecision` → approved |
| 15 | `writeApproval` rejected + 사유 | `rejected: 사유\n`, `readDecision` → `{rejected, '사유'}` |
| 16 | 사유에 개행/탭 포함 | 한 줄로 접힘, 파일에 개행은 마지막 하나뿐 |
| 17 | 한글 사유 | utf-8 왕복 일치, BOM 없음(`readFileSync`의 첫 바이트 확인) |
| 18 | 템플릿(`APPROVAL_TEMPLATE`과 같은 전부-`#` 파일)에 approved | 템플릿 보존 + 마지막 줄이 결정, `readDecision` → approved |
| 19 | 이미 `approved`가 적힌 파일에 rejected | `ok:false`, `conflict:'approved'`, **파일 내용 불변** |
| 20 | 첫 비주석 줄이 헛소리(`hold on`)인 파일에 approved | `ok:false`, `effective:'pending'` (파이썬이 읽을 결과와 일치) |
| 21 | `approvals/` 디렉터리가 없을 때 | 생성 후 기록 성공 |
| 22 | 개행 코드 | 파일에 `\r\n`이 없다 |
| 23 | `readJsonTolerant`(디렉터리 경로 / 없는 경로) | `null` |
| 24 | `liveStatusFor` | 신선한 running → `stale:false`; `updated_at`이 60초 전 → `stale:true`; live.json 없음/runs.json 깨짐 → `null` |
| 25 | `recent-repos` | 추가/중복제거/8개 상한/최신 우선, 깨진 파일 → `[]` |

18/19/20이 요구사항의 "승인 파일 기록 형식"을 실제 파이썬 파서 규약으로 검증하는 핵심이다. `readDecision`이 `pipeline.read_decision`의 이식이라는 사실 자체가 이 검증의 근거이므로, 이식 함수의 주석에 원본 위치(`aidev/pipeline.py:299-316`)를 적는다.

### 2.4 신규: `desktop/tsconfig.test.json`

`@electron-toolkit/tsconfig`를 **상속하지 않는다** (bundler resolution / noEmit 성향이라 emit에 부적합).

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "CommonJS",
    "moduleResolution": "Node",
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true,
    "types": ["node"],
    "rootDir": "src",
    "outDir": "out-test"
  },
  "include": ["src/main/aidev-store.ts", "src/main/recent-repos.ts",
              "src/main/*.test.ts", "src/shared/**/*"]
}
```

`package.json`에 `"type"`이 없으므로 emit된 `.js`는 CommonJS로 실행된다. `src/shared/ide.ts`는 타입 + `IPC` 상수뿐이라 emit해도 문제없다.

### 2.5 `desktop/src/shared/ide.ts` (수정)

기존 mock 타입은 **손대지 않는다.** 파일 하단에 새 섹션을 추가한다.

```ts
// ---------------------------------------------------------------- v0.2.5 바인딩
export type SliceStageStatus = string   // pending/running/done/failed + 미래의 값
export interface SliceStage {
  name: string; status: string
  approval?: string; approvalReason?: string; runId?: string
  attempts?: number; verdict?: string
}
export interface LiveStatus { status: string; updatedAt: number; ageSeconds: number; stale: boolean }
export interface SliceState {
  id: string; dir: string; status: string
  waitingStage: string | null
  stages: SliceStage[]; gates: string[]
  updatedAt: string | null; reason: string | null
  testVerdict: string | null
  epic: { epic_id?: string; index?: number } | null
  live: LiveStatus | null
  unreadable: boolean
}
export interface EpicSliceRef { index: number | null; title: string; sliceId: string | null; status: string }
export interface EpicState {
  id: string; status: string; current: number | null
  slices: EpicSliceRef[]; updatedAt: string | null; unreadable: boolean
}
export interface RepoState {
  root: string | null; name: string | null
  isAidevRepo: boolean            // <root>/.aidev 가 디렉터리인가
  recent: string[]                // main 소유. userData에 저장되며 대상 repo에 쓰지 않는다
  slices: SliceState[]; epics: EpicState[]
  readAt: number
}
export interface StageArtifact { stage: string; path: string; text: string; exists: boolean }
export type ApprovalDecision = 'approved' | 'rejected'
export interface ApprovalInput { sliceId: string; stage: string; decision: ApprovalDecision; reason?: string }
export interface ApprovalResult {
  ok: boolean; path: string
  effective?: 'pending' | 'approved' | 'rejected'
  conflict?: 'approved' | 'rejected'
  error?: string
}
```

`AidevBridge`에 §1.3의 다섯 멤버를 추가하고, `writeApproval` 위에 **"대상 repo에 쓰는 유일한 capability"**라는 주석을 단다. `IPC` 상수에 다섯 채널 추가:

```
repoState: 'aidev:get-repo-state'
openRepo:  'aidev:open-repo-dialog'
selectRepo:'aidev:select-repo'
artifact:  'aidev:get-stage-artifact'
approve:   'aidev:write-approval'
```

### 2.6 `desktop/src/preload/index.ts` (수정)

기존 8개 아래에 5개를 그대로 `ipcRenderer.invoke` 위임으로 추가한다. **`fs`도 `path`도 import하지 않는다** — sandbox 유지.

### 2.7 `desktop/src/main/index.ts` (수정)

`registerIdeHandlers()`는 그대로 두고 `registerRepoHandlers()`를 추가해 `app.whenReady()`에서 함께 부른다.

```ts
let repoRoot: string | null = null
const recentsFile = () => join(app.getPath('userData'), 'pluto-repos.json')
```

- 기동 시: `readRecents()`의 첫 항목이 아직 실존하는 디렉터리면 `repoRoot`로 채택. 아니면 `null`.
- `IPC.repoState` → `snapshot()`: `repoRoot`가 `null`이면 빈 `RepoState`(+recent). 아니면 `listSlices`/`listEpics` 결과를 담아 반환. **핸들러 전체를 try/catch로 감싸** 어떤 예외도 renderer로 throw되지 않게 하고, 실패 시 빈 목록 + `readAt`만 돌려준다.
- `IPC.openRepo` → `dialog.showOpenDialog(mainWindow, { properties:['openDirectory'], title:'Select an aidev repository' })`. 취소면 현재 상태를 그대로 반환. 선택되면 `repoRoot` 갱신 + `addRecent` 후 `snapshot()`.
- `IPC.selectRepo` → **인자가 최근 목록에 있는 경로일 때만** 채택한다 (renderer가 임의 경로를 지정해 읽게 하지 않는다). 아니면 현재 상태 그대로.
- `IPC.artifact` / `IPC.approve` → `repoRoot`가 없으면 `{ok:false, error:'no repository selected'}`. 있으면 store 함수 호출, 예외는 잡아서 `{ok:false, error: String(err)}`로 변환.

`createWindow()`가 `mainWindow`를 지역 변수로만 갖고 있으므로, dialog의 부모로 쓰기 위해 모듈 스코프에 `let mainWindow: BrowserWindow | null`을 두거나 `BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]`을 쓴다. 후자가 기존 코드 변경이 더 작다 — 후자를 택한다.

### 2.8 `desktop/src/renderer/src/components/Icon.tsx` (수정)

`PATHS`에 `pipeline` 한 개 추가. 기존 톤(1.2 stroke, 16 viewBox) 유지: 세 개의 단계 노드를 잇는 선 — 예 `'M3 4h4M3 8h4M3 12h4M9 4h4M9 8h4M9 12h4'` 계열의 단순 stroke. (아이콘은 `PATHS`의 키 하나 추가일 뿐 `IconName`이 자동 확장된다.)

### 2.9 `desktop/src/renderer/src/features/activitybar/ActivityBar.tsx` (수정)

`ActivityId`에 `'pipeline'` 추가, `ITEMS` **맨 앞**에 `{ id:'pipeline', icon:'pipeline', label:'Pipeline' }`. 나머지 항목/스타일 불변.

### 2.10 신규: `desktop/src/renderer/src/features/pipeline/PipelineSidebar.tsx`

Sidebar 안에서 렌더되는 뷰. 기존 `SectionLabel`/`EmptyState`/`PanelHeader` 톤을 그대로 쓴다.

- **repo 헤더**: repo 이름(없으면 "No repository") + `Open…` 버튼(→ `openRepoDialog`) + 최근 목록(`<select>` 대신 기존 톤에 맞는 작은 버튼 리스트; 클릭 시 `selectRepo`). 경로 전체는 `title` 속성으로.
- `isAidevRepo === false` 이면 `EmptyState title="No .aidev/ here" hint="이 폴더는 대상 repository가 아닌 것 같다"` — `_repo_hint`(`aidev/pipeline.py:2487-2494`)의 UI판.
- **Epics 섹션** (epic이 하나라도 있을 때만): epic id / status / `1=done 2=running:test 3=pending` 형태의 요약 — `epic.list_epics`(`aidev/epic.py:803-825`)와 같은 정보, 읽기 전용.
- **Slices 섹션**: 행마다
  - slice id (truncate, `title`에 전체)
  - status 배지. `waiting_approval:*`은 accent 색 + 삼각형 마크로 **강조**(기존 `RunStateBadge`의 `awaiting-approval` 톤과 동일한 시각 언어), `failed`/`rejected`는 bad, `done`/`merged`는 ok, `running:*`은 warn, 그 외 mute.
  - stage 칩: `plan=done implement=running test=pending` (모르는 stage도 그대로)
  - `live.stale`이면 `STALE` 라벨(bad)
  - `unreadable`이면 `(unreadable)`
  - 선택 시 좌측 accent 바 — `WorkspacesView`와 같은 패턴
- 배지는 `features/pipeline/SliceStatusBadge.tsx`로 분리한다. **`components/primitives.tsx`는 건드리지 않는다** (mock 화면 다섯 개가 공유하는 파일이라 회귀 위험).

### 2.11 신규: `desktop/src/renderer/src/features/pipeline/PlanSurface.tsx`

메인 pane의 새 surface.

- slice 미선택 → `EmptyState`.
- 선택됨 → 상단에 slice id / 산출물 경로(mono, `title`에 전체 경로), 본문은 `<pre>`에 `text` 그대로. `body { user-select:none }`이므로 `select-text whitespace-pre-wrap break-words font-mono text-tiny` 클래스를 붙여 **복사 가능**하게 한다. 마크다운 렌더링은 하지 않는다(요구사항이 명시적으로 요구하지 않음).
- 하단 승인 바: `waitingStage !== null` 일 때만 활성.
  - `Approve` 버튼 → `writeApproval({sliceId, stage: waitingStage, decision:'approved'})`
  - 사유 입력 `<input>` + `Reject` 버튼 → 사유가 비면 Reject 비활성 (규약상 빈 사유도 유효하지만, 사유 없는 반려는 `state.json`에 `(no reason given)`으로 남으므로 UI에서 막는다)
  - 클릭 중 `pending` 상태로 버튼 비활성 → 중복 클릭으로 두 번 쓰지 않는다.
  - 결과 표시: `ok:true`면 "approved — `<path>`", `conflict`면 "이미 `<conflict>`로 결정된 게이트다", `effective:'pending'`이면 "파일의 첫 줄이 결정으로 읽히지 않는다 — `<path>`를 직접 확인하라", `error`면 원문 그대로.
  - 성공 후 `onApproved()` 콜백으로 즉시 폴링 1회.

### 2.12 `desktop/src/renderer/src/features/sidebar/Sidebar.tsx` (수정)

`props`에 `pipeline: PipelineSidebarProps`를 추가하고, `titles`에 `pipeline: 'Pipeline'`, 분기 맨 앞에 `props.activity === 'pipeline' ? <PipelineSidebar {...props.pipeline}/> : …`. 기존 뷰 다섯 개의 코드는 불변.

### 2.13 `desktop/src/renderer/src/features/workspace/SurfacePane.tsx` (수정)

- `SurfaceId`에 `'plan'` 추가, `tabs` 배열 맨 앞에 `{ id:'plan', label:'Plan', badge: 대기중 개수 }`.
- `SurfaceData`에 `pipeline: { slice: SliceState|null; artifact: StageArtifact|null; onDecide: (…) => Promise<ApprovalResult>; }`를 추가.
- 분기에 `surface === 'plan' ? <PlanSurface …/> : …` 추가.

기존 다섯 surface의 렌더 로직은 불변.

### 2.14 `desktop/src/renderer/src/lib/useIdeData.ts` (수정)

기존 두 훅은 불변. 아래를 추가:

```ts
export function useRepoState(pollMs = 2000): {
  state: RepoState | null
  refresh: () => void
  open: () => void
  select: (root: string) => void
}
```

- `useEffect`에서 즉시 1회 + `setInterval(pollMs)`. `inFlight` ref로 중복 호출 방지, `alive` 플래그로 unmount 후 setState 방지 (기존 훅의 패턴과 동일).
- `useStageArtifact(sliceId | null, stage | null)`: 두 값이 바뀔 때 1회 로드 + 승인 후 refresh. 로딩 중 이전 slice의 내용이 보이지 않도록 기존 `useWorkspaceData`의 "loaded key를 payload 옆에 둔다" 패턴을 그대로 쓴다.

### 2.15 `desktop/src/renderer/src/App.tsx` (수정)

- `ACTIVITY_SURFACE`에 `pipeline: 'plan'` 추가 → Pipeline 아이콘 클릭 시 Plan surface가 열린다.
- `const [pipelineSliceId, setPipelineSliceId] = useState<string|null>(null)`, `const repo = useRepoState()`.
- repo가 바뀌거나 선택된 slice가 목록에서 사라지면 선택 해제. 초기 선택은 **`waiting_approval`인 첫 slice**, 없으면 목록의 첫 항목 (승인이 이 화면의 목적이므로).
- `surfaceData`에 `pipeline` 필드 추가, `Sidebar`에 `pipeline` prop 전달.
- `Booting` 조건은 그대로 `!global` (mock 로딩). repo가 없어도 셸은 뜬다.

### 2.16 `desktop/package.json` (수정)

`scripts`에 한 줄:
```json
"test": "npm run typecheck && tsc -p tsconfig.test.json && node --test out-test"
```
**`dependencies` / `devDependencies`는 한 글자도 바꾸지 않는다.** `package-lock.json`도 그대로 둔다 (§1.1).

### 2.17 ignore 파일 3종 (수정)

- `desktop/.gitignore`: `out-test` 추가
- `desktop/.prettierignore`: `out-test` 추가
- `desktop/eslint.config.mjs`: `ignores` 배열에 `'**/out-test'` 추가

### 2.18 `desktop/README.md` (수정) — 수동 검증 절차

현재 템플릿 문구뿐이므로 아래를 추가한다.

```
## Pipeline binding (v0.2.5)

Pluto reads <repo>/.aidev/slices/*/state.json and writes exactly one file:
<repo>/.aidev/slices/<id>/approvals/<stage>.md.

### Unit tests
npm test --prefix desktop      # typecheck + tsc -p tsconfig.test.json + node --test

### Manual verification (승인이 실제 파이프라인을 진행시키는지)
1. 터미널 A:  aidev pipeline --repo <repo> --requirement tasks/<x>.md
   "[pipeline] waiting for approval of 'plan'" 이 뜰 때까지 기다린다.
2. 터미널 B:  cd desktop && npm run dev
3. Activity bar → Pipeline → "Open…" → <repo> 선택.
   → slice 목록에 <slice-id>가 waiting_approval:plan 강조로 뜬다.        [기준 1]
4. 그 slice를 선택 → Plan surface에 plan.md 전문이 뜬다.                  [기준 2]
5. Approve 클릭.
   → 2초 안에 행이 running:implement로 바뀐다.
   → 터미널 A가 "[pipeline] gate 'plan': approved"를 찍고 진행한다.      [기준 3]
6. 반려 확인(별도 slice로): 사유를 입력하고 Reject.
   → approvals/plan.md의 첫 비주석 줄이 "rejected: <사유>"
   → state.json의 status가 rejected, reason에 사유가 남는다.            [기준 4]

Windows: VS Code 내장 터미널은 ELECTRON_RUN_AS_NODE=1을 상속시켜 Electron이
plain Node로 떠서 죽는다. 그 터미널에서는 변수를 지우고 실행한다.
```

### 2.19 `README.md` (루트, 수정)

"Pluto IDE — 데스크톱 셸" 절에 3~5줄만 덧붙인다: 세 기능이 실데이터로 붙었다는 것, **대상 repo에 쓰는 것은 `approvals/<stage>.md` 하나뿐**이라는 것, 나머지 화면은 여전히 mock이라는 것, 그리고 `npm test --prefix desktop`. 로드맵의 `v0.6 Pluto IDE 바인딩` 줄에 `(v0.2.5에서 상태/plan/승인 3종 선행)`을 붙인다. 그 이상의 문서 개편은 하지 않는다.

### 2.20 Python: 변경 없음

`aidev/**` 와 `tests/**` 는 **한 파일도 건드리지 않는다.** 이것이 "Python 쪽 무변경 증명"의 실체이고, `git status`로 확인 가능하다.

---

## 3. 검증

test 단계는 **세 개의 평범한 명령**을 각각 따로 실행한다 (체인 금지 규약 준수, 셋 다 allowedTools 안):

```
pytest                       # 기존 Python 전량 — 무손상 증명
npm test --prefix desktop    # typecheck + 새 단위 테스트
git status --porcelain       # (참고) aidev/ tests/ 에 변경 0인지
```

세 번째는 `git`이 allowedTools에 없으므로 **실행하지 않는다** — 대신 test 단계 보고문에 "Python 파일을 수정하지 않았다"를 명시하고, `pytest` 전량 통과가 그 증거다.

기대:
- `pytest` — 기존과 동일한 통과 수. **하나라도 늘거나 줄면 실패로 보고한다** (Python을 안 건드렸으므로 변할 이유가 없다).
- `npm test --prefix desktop` — typecheck 통과 + §2.3의 25개 케이스 전부 통과.
- Done Criteria 중 3번(실제 파이프라인 진행)은 자동 검증 불가 → `desktop/README.md`의 수동 절차가 그 자리를 채운다. **test 단계가 이것을 "확인했다"고 주장해서는 안 된다.**

---

## 4. 무엇이 잘못될 수 있는가

| 위험 | 왜 생기는가 | 대응 |
| --- | --- | --- |
| **새 npm 의존성을 넣어 test 단계가 통째로 죽는다** | setup(`npm ci --prefix desktop`)은 implement 전에 1회만 돌고, `npm install`은 allowedTools에 없다 | §1.1. `package.json`의 dependencies/devDependencies와 `package-lock.json`을 **절대 수정하지 않는다.** 이 계획에서 가장 지키기 어려운 제약이자 가장 중요한 것 |
| `node --test out-test`가 테스트 파일을 못 찾는다 | Node 버전별 디렉터리 발견 동작 차이 | 파일명을 `*.test.js`로 맞춘다(발견 패턴 기본값). 실패 시 `node --test out-test/*.test.js`로 바꾼다 — 여전히 `Bash(node --test:*)` 안 |
| `tsc -p tsconfig.test.json`이 ESM을 뱉어 `node --test`가 죽는다 | `module` 설정 실수 | `package.json`에 `"type"`이 없어 `.js`는 CJS. tsconfig.test.json에 `module: CommonJS` / `moduleResolution: Node`를 **명시**. `aidev-store.ts`는 Electron·`?asset`·`@shared` alias를 **쓰지 않는다** (main/preload는 원래 상대경로를 쓴다) |
| `npm run typecheck`가 새 테스트 파일에서 실패 | `tsconfig.node.json`의 include가 `src/main/**/*`라 `*.test.ts`도 잡힌다 | 그대로 둔다 — 오히려 타입 검사 범위가 넓어져 이득이다. `@types/node`가 있으므로 `node:test`/`node:assert` 타입은 해결된다. 만약 `@electron-toolkit/tsconfig`의 `types` 설정이 `node`를 빼서 실패하면, tsconfig.node.json의 `types`에 `"node"`를 추가한다 |
| **승인 파일 쓰기가 파이썬 폴링과 충돌** | Windows에서 reader가 열고 있으면 rename이 EPERM | `storage.write_json_atomic`과 같은 20×50ms 재시도. 실패해도 `{ok:false,error}`로 UI에 보이고 조용히 삼키지 않는다. 파이썬 쪽은 읽기 실패를 PENDING으로 처리하고 3초 뒤 재시도하므로 우리 쪽 순간 충돌은 파이프라인을 깨지 않는다 |
| **UI가 쓴 결정을 파이썬이 다르게 읽는다** | `read_decision`은 첫 비주석 줄만 보고, 그게 approve/reject가 아니면 영구 PENDING | `readDecision`을 `pipeline.py:299-316`의 정확한 이식으로 만들고, 쓰기 **후에 다시 읽어** 실제 판정을 UI에 보고한다. 테스트 18/19/20이 이 계약을 고정한다 |
| **이미 결정된 게이트를 UI가 덮어쓴다** | 재승인/오클릭 | 쓰기 전에 `readDecision`으로 확인하고 pending이 아니면 아무것도 쓰지 않는다(테스트 19) |
| renderer가 임의 경로를 읽거나 쓴다 | `sliceId`/`stage`/`root`가 renderer에서 온다 | `safeSegment` 정규식 + `path.resolve`가 `<root>/.aidev` 아래인지 재확인 + `selectRepo`는 **최근 목록에 있는 경로만** 수락. 테스트 13 |
| 폴링이 파일 잠금을 유발해 파이프라인의 `os.replace`를 죽인다 | Windows의 `open()`이 delete 공유를 주지 않는다 | Node의 `readFileSync`는 열고 즉시 닫는다. 그래도 `write_json_atomic`이 1초까지 재시도하므로 파이프라인은 견딘다. 주기를 2초 이하로 더 줄이지 않는다 |
| 폴링이 겹쳐 쌓인다 | 느린 디스크 / 큰 repo | in-flight 가드 |
| STALE 오탐 | `state.json`은 heartbeat가 아니다 | §1.5. `state.json`에는 STALE을 적용하지 않고 `live.json`에만 원 규칙(10초)을 적용한다 |
| mock 화면 회귀 | 공유 파일(`primitives.tsx`, `mock-data.ts`, `IPC` 기존 키) 수정 | `primitives.tsx`와 `mock-data.ts`는 **읽기만** 한다. `shared/ide.ts`는 하단 추가만, 기존 타입/채널 이름 불변. `SurfacePane`은 탭 하나와 분기 하나만 추가 |
| `dialog.showOpenDialog`가 취소를 반환 | 정상 흐름 | `canceled` 확인 후 현재 상태 그대로 반환 |
| 선택한 폴더에 `.aidev`가 없다 | 사람이 엉뚱한 폴더를 고름 | `isAidevRepo:false` + `_repo_hint`와 같은 문구의 EmptyState. throw 하지 않는다 |
| Windows 경로 구분자가 UI에서 깨져 보인다 | `\`와 `/` 혼용 | main이 `path.join`으로 만든 값을 **그대로** 넘기고 renderer는 표시만 한다. 경로 문자열을 renderer에서 조작하지 않는다 |
| IPC 직렬화 실패 | 클래스 인스턴스/함수 반환 | 모든 반환값은 plain object / string / number / boolean / null 뿐. `Date`는 쓰지 않고 ISO 문자열 또는 epoch 숫자만 넘긴다 |
| plan 텍스트가 매우 크다 | 이 plan.md 자체가 그렇다 | `<pre>`에 그대로 넣되 컨테이너에 `overflow-auto`. 잘라내지 않는다 (사람이 승인 전에 전문을 읽어야 한다) |

---

## 5. 구현 순서

1. `aidev-store.ts` + `recent-repos.ts` (Electron 무의존, 순수 fs)
2. `tsconfig.test.json` + `package.json`의 `test` 스크립트 + ignore 3종
3. `*.test.ts` 25 케이스 → `npm test --prefix desktop`이 초록이 될 때까지
4. `shared/ide.ts` 타입/채널 → `preload/index.ts` → `main/index.ts` 핸들러
5. `Icon` → `ActivityBar` → `PipelineSidebar` → `PlanSurface` → `Sidebar`/`SurfacePane`/`App`/`useIdeData` 배선
6. `desktop/README.md` 수동 절차, 루트 `README.md` 3~5줄
7. `pytest` + `npm test --prefix desktop` 최종 확인

1~3이 먼저인 이유: 이 slice의 자동 검증 가능한 전부가 거기 있고, UI 배선은 그 위에 얹히는 얇은 층이다.
