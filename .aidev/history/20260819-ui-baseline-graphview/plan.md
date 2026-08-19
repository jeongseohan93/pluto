# UI 기초선 + Graph View v0 — 구현 계획

엔진(`aidev/*.py`)은 **읽기 전용**이다. 이 slice가 쓰는 코드는 전부
`desktop/` 안이고, 모든 버튼은 이미 존재하는 CLI 호출 또는 이미 존재하는
파일 읽기의 배선이다.

---

## 0. 읽고 확인한 계약 (이 계획이 지켜야 하는 것들)

| 사실 | 출처 |
|---|---|
| `read_decision`은 첫 비주석 줄만 본다. `approved: <조건>`의 뒷문장이 `Decision.reason`으로 살아 남는다 | `aidev/pipeline.py:891` |
| 현재 앱의 `parseDecision`은 approved의 뒷문장을 **버린다** (`reason: ''`) — A2에서 고쳐야 할 결함 | `desktop/src/main/aidev-store.ts:97` |
| `--merge`는 실패해도(체크아웃이 base가 아님, 더티, 충돌) `--discard`를 막지 않는다. 충돌만 `state["merge"]={"status":"conflict"}`로 남고, 그 외 거절은 **디스크에 아무 흔적도 남기지 않는다** | `pipeline.py:5953, 5974, 6491` |
| `discard_slice`는 merge 여부를 전혀 보지 않는다 → 가드는 UI 레벨이 유일하다 (요구사항 ★와 일치) | `pipeline.py:6491` |
| 사인 분류는 `quota` 우선 → 턴사 → 기타. `quota`는 엔진이 이미 판정해 `runs.json`의 `quota: true`로 저장한다. 턴사 표식은 `telemetry.json`의 `exact.result_subtype ∈ {error_max_turns, max_turns}` 또는 텍스트의 `TURN_MARKERS` | `recovery.py:24-30, 67, 85`, `pipeline.py:3168` |
| 한도 대기: `state["quota"] = {waiting, resume_at, retries, source}`, 이력은 `state["recovery"]["waits"]`, 자동복구 중단은 `state["recovery"]["stopped"] = {pin, detail}` | `pipeline.py:3125, 2811, 2845` |
| 진행 노트는 `<slice>/progress.md` | `pipeline.py:1077` |
| graph.db 스키마: `meta / files / functions / calls / tags`, `GRAPH_SCHEMA = 1`, `has_spec = 1 if summary else 0` | `graph/db.py:26, 39, 221` |
| graph 신선도: `meta.base_commit / branch / built_at / updated_at / dirty`, 그리고 `.aidev/graph/dirty` 마커 파일 | `graph/build.py:275, 306` |
| 파이프라인 출력은 TTY가 아니면 ANSI 없이 한 줄씩 로그로 떨어진다(`LiveReporter._log`), `say()`는 매줄 flush | `reporter.py:217, 280`, `pipeline.py:4427` |
| `data_dir`는 설치된 패키지 위치에서 결정된다 — cwd와 무관하게 터미널과 같은 곳을 본다 | `storage.py:109` |
| 이 slice의 front matter: `approval: plan`, `max_turns: implement=160`, **`setup:` 없음** → worktree에 `desktop/node_modules`가 없다 (실측: 이 worktree에 없음). 선언된 `test_commands`도 없으므로 stage는 `TEST_COMMAND_TOOLS`(npm test / node --test / pytest)를 받는다 | `tasks/ui-baseline-graphview.md`, `pipeline.py:111, 2379` |

마지막 줄이 검증 전략을 결정한다 → §5.

---

## 1. 설계 결정 (이유 포함)

**D1. 신규 npm 의존성 0개.** 렌더 라이브러리를 추가하지 않는다.
LOD 원칙(선택 노드의 이웃만 엣지)을 채택하는 순간 레이아웃 엔진이 필요 없어진다:
배치는 "파일 상자를 경로순으로 열에 채우기"라는 결정적 계산이고, 그리기는
이미 이 repo에 있는 SVG 방식(`GraphSurface.tsx`)이다. d3-force/cytoscape/reactflow는
전부 8669개 엣지를 전제로 한 도구이므로 여기서는 비용만 낸다.

**D2. graph.db는 `node:sqlite`로 읽는다** (Node 22.5+ 내장, Electron 39.8.10의 Node는
22.16+). 새 의존성이 없고 `readOnly: true`가 3조("화면은 비추기만")를 런타임으로 강제한다.
드라이버는 `graph-store.ts` 안 `openReadonly(path)` 한 함수 뒤에 숨긴다 — 만약 이
Electron 빌드에 `node:sqlite`가 없으면 그 함수만 `better-sqlite3`로 갈아끼우면 된다(§6 R1).

**D3. 렌더러에 exec를 주지 않는다.** preload는 지금처럼 이름 붙은 capability만 노출한다
(`launchPipeline / resumeSlice / mergeSlice / discardSlice / buildGraph / stopCommand`).
argv는 **main이** 만든다. 렌더러가 넘기는 것은 slice id, tasks/ 상대경로, 턴 수뿐이고
셋 다 main에서 형식 검증 + 존재 검증을 통과해야 한다. `shell: false`.

**D4. 동시에 도는 aidev 프로세스는 하나.** 일상 동선(발사→승인→관전→merge→discard)은
순차적이다 — merge는 파이프라인이 끝난 뒤에만 의미가 있다. 슬롯이 하나면 "merge 도중
discard" 같은 경합이 구조적으로 불가능해진다. 실행 중에는 모든 실행 버튼이 비활성이고
헤더에 도는 argv가 보인다.

**D5. 가드는 순수 함수로 `src/shared/`에 둔다.** `discardGuard()`가 UI와 테스트의
공통 진실이 된다 (Done Criteria가 요구하는 "가드 동작 확인"이 단위 테스트로 증명 가능해진다).

**D6. mock 화면은 지우지 않는다.** Function DB 뷰는 **새 탭**(`functions`)이고,
기존 mock Graph/Code/Diff/Test/Browser/Workspaces/Telemetry는 그대로 두되 전부
`[DEMO]` 배지를 단다 (A5).

---

## 2. 새 파일 — 순수 로직 (`desktop/src/shared/`)

`tsconfig.test.json`이 `src/shared/**/*`를 이미 포함하므로 자동으로 테스트 대상이 된다.

### `desktop/src/shared/commands.ts` (신규)
CLI 배선의 유일한 진실. 부수효과 없음.

```ts
export type CommandKind = 'pipeline' | 'resume' | 'merge' | 'discard' | 'graph-build'
export type CommandRequest =
  | { kind: 'pipeline'; requirement: string }              // 'tasks/x.md'
  | { kind: 'resume'; sliceId: string; implementTurns?: number }
  | { kind: 'merge'; sliceId: string; push: boolean }
  | { kind: 'discard'; sliceId: string }
  | { kind: 'graph-build' }

export const MAX_STAGE_TURNS = 1000
export function isSafeSegment(v: string): boolean          // ^[A-Za-z0-9][A-Za-z0-9._-]*$, '..' 금지
export function isRequirementPath(v: string): boolean      // ^tasks/[A-Za-z0-9._-]+\.md$
export function commandArgv(repoRoot: string, req: CommandRequest): string[]   // 실패 시 throw
export function commandLabel(req: CommandRequest): string  // 로그 첫 줄용
```

`commandArgv`가 만드는 것 (이게 전부다):

| 버튼 | argv |
|---|---|
| 발사 | `pipeline --repo <root> --requirement tasks/x.md` |
| Resume | `pipeline --repo <root> --resume-slice <id>` (+ 턴 입력 시 `--max-turns-stage implement=<N>`) |
| Merge (+push) | `pipeline --repo <root> --merge <id>` (+`--push`) |
| Discard | `pipeline --repo <root> --discard <id>` |
| Build/Rebuild Graph | `graph build --repo <root>` |

### `desktop/src/shared/recovery.ts` (신규)
`aidev/recovery.py`의 두 함수를 글자 그대로 옮긴다. 새 판단을 만들지 않는다.

```ts
export const CAUSE_QUOTA = 'usage_limit', CAUSE_TURNS = 'turns', CAUSE_OTHER = 'other'
export const TURN_MARKERS = ['error_max_turns', 'max_turns', 'maximum number of turns'] as const
export function isTurnDeath(subtype: string | null, text: string): boolean
export function classifyCause(subtype: string | null, text: string, quota: boolean): DeathCause
```
`quota`는 **재도출하지 않는다** — 엔진이 `runs.json`에 남긴 `quota` 불리언(또는
`state.quota.waiting`)을 그대로 넘긴다. `QUOTA_MARKERS` 사본을 만드는 순간 두 판정이 갈라진다.

### `desktop/src/shared/guards.ts` (신규) — ★ 8/18 사고 방지
```ts
export interface DiscardGuard { allowed: boolean; confirm: boolean; tone: 'ok'|'warn'|'block'; reason: string }
export function discardGuard(slice: SliceState, lastMerge: CommandResult | null): DiscardGuard
```
규칙 (state.json 유래 + 세션 내 실행 결과, 둘 다 봐야 한다 — merge 거절 대부분은
디스크에 흔적을 남기지 않기 때문):

1. `lastMerge` 가 **이 slice**의 merge이고 `exitCode !== 0` → `{allowed:false, tone:'block'}`
   "merge가 실패했다(exit N). 지금 discard하면 머지되지 않은 커밋이 사라진다."
2. `slice.merge?.status === 'conflict'` → `{allowed:false, tone:'block'}` — 충돌 파일 목록 표시.
3. `slice.merge?.status === 'merged'`:
   - `merge.push?.status === 'failed'` → `{allowed:true, confirm:false, tone:'warn'}`
     "머지는 남았고 push만 실패했다"(CLI가 하는 말 그대로).
   - 그 외 → `{allowed:true, confirm:false, tone:'ok'}`.
4. merge 기록이 아예 없음 → `{allowed:true, confirm:true, tone:'warn'}`
   "이 slice는 한 번도 머지된 적이 없다 — 브랜치와 커밋이 지워진다." (버려도 되는
   slice를 못 버리게 만들면 그건 그것대로 틀렸으므로, 금지가 아니라 **명시적 2단계 확인**이다.)

앱을 껐다 켜면 1번의 근거(세션 기억)는 사라지지만 그때는 4번이 받아서 여전히
확인을 요구한다 — 조용히 지워지는 경로는 없다.

### `desktop/src/shared/graph-layout.ts` (신규) — 순수 배치/LOD
```ts
export interface GraphBox { path:string; x:number; y:number; width:number; height:number; ids:number[] }
export interface Anchor { left:number; right:number; y:number; boxIndex:number }
export interface GraphLayout { boxes:GraphBox[]; anchors:Map<number,Anchor>; width:number; height:number }
export const BOX_W=268, HEADER_H=30, ROW_H=18, PAD_B=8, GAP_X=28, GAP_Y=18, COLUMN_H=1400
export function layoutGraph(files: GraphFileGroup[]): GraphLayout   // 경로순 → 열에 그리디 패킹
export function edgePath(from: Anchor, to: Anchor): string          // GraphSurface.tsx:41의 곡선, 여기로 이동
export function matchFunctions(fns: GraphFunction[], query: string, limit?: number): GraphFunction[]
```
`edgePath`는 `GraphSurface.tsx`에서 **잘라내어** 이리로 옮기고 양쪽이 import 한다
(같은 곡선을 두 벌 두지 않는다).

---

## 3. main 프로세스

### `desktop/src/main/aidev-store.ts` (수정)
1. **`parseDecision`**: `approved` 분기가 `reason: rest.trim()`을 보존하도록 고친다
   (`read_decision`과 동일). 기존 테스트(`{verdict:'approved', reason:''}`)는 그대로 통과한다.
2. **`writeApproval`**: 코멘트가 있으면 `approved: <한 줄>`을 쓴다.
   `oneLine()` 접기, 원자적 쓰기, 이미 결정된 게이트 거절 — 전부 지금 그대로 유지.
3. **`readSlice`**가 SliceState에 채우는 필드 추가 (전부 tolerant, 없으면 null):
   - `merge`: `state.merge` → `{status, at, branch, base, commit, conflicts[], push:{status,remote,error}|null}`
   - `quota`: `state.quota` → `{waiting, resumeAt, retries, source}`
   - `stopped`: `state.recovery.stopped` → `{at, stage, pin, detail}`
4. **`listRequirements(repoRoot): RequirementFile[]`** (신규):
   `<root>/tasks/*.md`를 이름순으로, `{path:'tasks/x.md', name, title, bytes, modifiedAt}`.
   `title`은 첫 `# ` 헤더 한 줄(없으면 파일명). 못 읽으면 `[]`.
5. **`readSliceFailure(repoRoot, sliceId): SliceFailure | null`** (신규) — A4:
   - `state.json` → `reason`, 실패한 stage(`stages[*].status === 'failed'` 중 stage_order 마지막), `quota`, `recovery.stopped`
   - `runs.json` → 그 stage의 마지막 run 엔트리(`quota`, `run_dir`, `status`, `exit_code`, `run_id`, `summary`)
   - `<run_dir>/telemetry.json` → `exact.result_subtype` (경로가 이 기계에 없으면 그냥 null — `liveStatusFor`와 같은 관용)
   - `classifyCause(subtype, summary + status, quotaFlag || state.quota?.waiting)` → `cause`
   - `progress.md` → `{path, text, exists}`
   - `quotaWait`: `state.quota.waiting`일 때 `{resumeAt, secondsLeft}` (음수는 0으로)
   - `causeEvidence`: 어떤 근거로 그렇게 판정했는지 한 줄 (추측을 추측이라고 말한다)

### `desktop/src/main/aidev-cli.ts` (신규) — 프로세스 슬롯
Electron을 import 하지 않는다(테스트 대상이 되기 위해). 알림은 주입된 콜백으로 나간다.

```ts
export function resolveAidevBin(env: NodeJS.ProcessEnv, exists: (p:string)=>boolean): string
// AIDEV_BIN → PATH의 'aidev' → /opt/homebrew/bin, /usr/local/bin, ~/.local/bin 순 (GUI 실행은
// 로그인 셸 PATH를 물려받지 못한다). 못 찾으면 'aidev'를 그대로 반환하고 ENOENT는 로그로 보고.

export class CommandRunner {
  constructor(onChange: (state: CommandState, appended: LogLine[]) => void)
  state(): CommandState | null
  start(repoRoot: string, req: CommandRequest): CommandStart   // 슬롯이 차 있으면 {ok:false,busy:true}
  stop(): void                                                  // SIGTERM
  dispose(): void                                               // app quit 시
}
```
- `spawn(bin, argv, { cwd: repoRoot, shell: false, env: {...process.env, PYTHONUNBUFFERED:'1'} })`
- stdout/stderr를 줄 단위로 잘라 링버퍼(최근 2000줄)에 `LogLine`으로 넣는다.
  stdout→`info`, stderr→`warn`, `[pipeline] ...` 중 `FAILED`/`error:`/`MERGE CONFLICT`→`error`,
  첫 줄은 실행한 argv 자체(`tool`), 마지막 줄은 `exit <code>`(0이면 `ok`, 아니면 `error`).
- `seq`를 증가시키며 최대 10Hz로 `onChange(state, appended)`를 부른다.
- 자식은 앱 수명에 묶인다(터미널을 닫는 것과 같다). 앱 종료 시 `dispose()`가 SIGTERM.

### `desktop/src/main/graph-store.ts` (신규) — readonly Function DB
```ts
export function graphPaths(repoRoot: string): { dir:string; db:string; dirtyMarker:string }
export function readGraphIndex(repoRoot: string): GraphIndexResult
export function readGraphNode(repoRoot: string, id: number): GraphNodeDetail | null
```
`openReadonly()`는 `new DatabaseSync(path, { readOnly: true })`를 try 안에서 만들고
**요청마다 열고 닫는다**(파이프라인이 같은 파일을 갈아끼우는 중일 수 있다). 어떤 실패도
던지지 않고 `{ok:false, problem:'no-graph'|'unreadable'|'schema'|'no-sqlite', detail}`로 돌아온다.
`meta.schema !== '1'`이면 `schema` 문제로 처리하고 화면은 [Rebuild]를 권한다(`commands.py:153`과 같은 태도).

SQL은 `db.py`의 읽기 메서드를 그대로 옮긴 것이다:

```sql
-- index
SELECT key, value FROM meta;
SELECT path, lang, funcs FROM files WHERE parsed = 1 ORDER BY path;
SELECT id,qualname,name,path,lang,lineno,end_lineno,signature,kind,summary,has_spec,is_test
  FROM functions ORDER BY path, lineno;
-- node (tags_of / calls_of / callers 포팅, callers에는 f.id를 추가해 이동 가능하게)
SELECT tag,value,core,lineno FROM tags WHERE func_id=? ORDER BY lineno, rowid;
SELECT c.lineno AS call_line, c.callee_name AS callee, c.callee_raw AS raw,
       c.resolved_id AS target_id, t.path AS target_path, t.lineno AS target_line,
       t.qualname AS target_qualname
  FROM calls c LEFT JOIN functions t ON t.id=c.resolved_id
 WHERE c.caller_id=? ORDER BY c.lineno, c.id;
SELECT f.id AS caller_id, f.path AS path, f.qualname AS caller, f.lineno AS caller_line,
       c.lineno AS call_line, c.resolved_id AS target_id
  FROM calls c JOIN functions f ON f.id=c.caller_id
 WHERE c.callee_name=? ORDER BY f.path, c.lineno;
```
callers 행은 `target_id === id`(나로 해결됨)와 `target_id === null`(모호/외부 — `db.callers`가
일부러 남기는 것)만 남기고 후자는 화면에서 `unresolved`로 표시. 200개에서 자르고 `+N more`.

인덱스 응답에 `meta`를 함께 담는다: `baseCommit, branch, builtAt, updatedAt, dirty('1'),
markedStale(= dirty 마커 파일 존재), functions, files, specCovered(non-test && has_spec)`.

### `desktop/src/main/index.ts` (수정)
- `CommandRunner` 인스턴스 하나 생성, `onChange`에서 `BrowserWindow.getAllWindows()`에
  `IPC.commandEvent`로 `{id, seq, from, lines, status, exitCode}` push.
- 새 핸들러: `requirements / runCommand / stopCommand / commandState / graphIndex / graphNode / sliceFailure`.
- `runCommand`: `repoRoot` 없으면 거절 → `commandArgv`로 argv 생성(형식 검증) →
  requirement는 `listRequirements()` 결과에 실제로 있는지 확인, sliceId는 슬라이스 디렉터리
  존재 확인 → `runner.start()`. 어떤 예외도 `{ok:false, error}`로 돌려준다.
- `app.on('will-quit')` 에서 `runner.dispose()`.

### `desktop/src/preload/index.ts` (수정)
`AidevBridge`에 위 capability들을 추가. `onCommandEvent(cb): () => void`는
`ipcRenderer.on` 등록 + 해제 함수 반환(sandbox 안에서 `ipcRenderer`만 사용).
여전히 `exec` 없음, 임의 경로 읽기 없음.

---

## 4. 렌더러

### `desktop/src/shared/ide.ts` (수정)
새 타입: `RequirementFile, CommandKind, CommandRequest, CommandStart, CommandState,
CommandResult, DeathCause, SliceFailure, SliceMerge, SliceQuota,
GraphMeta, GraphFunction, GraphFileGroup, GraphIndexResult, GraphNodeDetail, GraphCallRow, GraphCallerRow`.
`SliceState`에 `merge/quota/stopped` 추가. `AidevBridge`와 `IPC`에 위 채널 추가.
기존 v0.0.1 mock 타입은 손대지 않는다.

### 새 컴포넌트
| 파일 | 역할 |
|---|---|
| `components/DemoBadge.tsx` | `[DEMO]` 배지 하나 (warn 톤, title에 "authored mock data") |
| `features/pipeline/LaunchPanel.tsx` | A1: `tasks/*.md` `<select>` + [Launch] — Pipeline 사이드바 상단 |
| `features/pipeline/SliceActions.tsx` | A3: Resume(+턴 입력) / Merge(+push 체크) / Discard(+`discardGuard`) |
| `features/pipeline/FailurePanel.tsx` | A4: 사인 배지 + 근거 한 줄 + state.reason + 리셋 카운트다운 + `recovery.stopped` + progress.md |
| `features/graph/FunctionGraphSurface.tsx` | B: 검색창 + 신선도 헤더 + [Rebuild] + SVG 캔버스 + 우측 상세 패널 |
| `features/graph/FunctionDetail.tsx` | B2 사이드 패널: 요약/시그니처/@param/@flow/callers/calls(클릭 이동) |
| `features/graph/FunctionGraphSidebar.tsx` | 파일 목록 + 파일별 `n fn · m/n spec`, 클릭 시 해당 상자로 스크롤 |
| `lib/useCommandState.ts` | 스냅샷 + 이벤트 구독(`seq` 불일치 시 전체 재조회) |
| `lib/useFunctionGraph.ts` | 인덱스 1회 로드 + `reload()` (graph-build 종료 후 자동 호출) |
| `lib/useSliceFailure.ts` | 선택 slice가 failed/quota_wait일 때만 조회 |

### 수정되는 화면
- **`App.tsx`**: `functions` activity/surface 추가, `command`/`graph`/`failure` 상태를 소유,
  명령 시작 시 하단 패널을 `run` 탭으로 열고, 명령 종료 시 `repo.refresh()`(+graph-build면 graph reload).
  `lastMerge`(마지막 merge 결과)를 `SliceActions`에 내려 가드에 먹인다.
- **`ActivityBar.tsx`**: `functions` 항목 추가(아이콘 `graph`, 라벨 "Function graph").
  기존 `graph`(mock) 라벨은 "Code graph (demo)".
- **`Sidebar.tsx`**: `functions` 분기 추가. mock 뷰(workspaces/graph/changes/tests/telemetry)
  헤더에 `<DemoBadge/>`.
- **`SurfacePane.tsx`**: `functions` 탭 추가(실데이터, 배지 없음). `graph/code/test/diff/browser`
  탭에는 `badge: <DemoBadge/>`.
- **`PipelineSidebar.tsx`**: `RepoHeader` 아래 `<LaunchPanel/>`. repo가 없거나 `.aidev`가
  없으면 감춘다. 실행 중이면 비활성 + "aidev is running".
- **`PlanSurface.tsx`**: 게이트 줄을 A2 형태로 — 코멘트 입력칸 하나를 **승인/반려가 공유**한다
  (`approved: <문구>` / `rejected: <문구>`). Approve는 코멘트 없이도 가능(=`approved`),
  Reject는 지금처럼 사유 필수. 아래에 `<SliceActions/>`, failed면 `<FailurePanel/>`.
  approved에 조건이 붙었으면 결과 줄에 그 조건을 되비춘다(사람이 반영됐음을 눈으로 확인).
- **`BottomPanel.tsx`**: `run` 탭 추가(실데이터: `CommandState.lines`, 자동 하단 스크롤,
  헤더에 argv와 exit code). 기존 agent/terminal/test/telemetry 탭에 `<DemoBadge/>`.
- **`TopBar.tsx` / `StatusBar.tsx` / `Inspector.tsx` / `GraphSurface.tsx` / `CodeSurface.tsx` /
  `TestSurface.tsx` / `DiffSurface.tsx` / `BrowserSurface.tsx`**: `<DemoBadge/>` 한 개씩
  (헤더 또는 좌상단 코너). 그 외 변경 없음.

### Function Graph 화면의 구체
- 캔버스: `layoutGraph()`가 준 좌표를 그대로 그린다. 파일 상자 헤더 = 파일명(전체 경로는 `<title>`)
  + `n fn` + `m/n spec`. 행 = 함수 이름(mono).
- **명세 없음 = 회색**: `has_spec === 0` → `text-fg-mute`, 있으면 `text-fg-dim` + 점 마커.
  `is_test` 행은 접두 마커 `t`. 커버리지가 화면에서 바로 읽힌다.
- **엣지는 선택된 노드의 이웃만**: `calls`(해결된 target) + `callers`(해결된 caller).
  선택이 없으면 엣지 0개. 8669개를 동시에 그리는 경로 자체가 존재하지 않는다.
- **성능**: `FileBox`는 `React.memo`. 선택 상태는 `selectedLocalId: number | null`로
  내려서 **선택을 담고 있는 상자 2개만** 리렌더된다. 초기 렌더는 78상자 / 1335행.
- **검색**: 헤더 입력 → `matchFunctions()`(부분 일치, 대소문자 무시, name→qualname 순, 40개)
  → 목록에서 Enter/클릭 시 선택 + 스크롤 센터링(`anchors.get(id)` × zoom).
- **상세 패널**(우측 320px): 요약 한 줄 / `path:line` / 시그니처 / `@param`·`@flow`(태그 순서 그대로,
  비코어 태그는 있는 그대로) / calls / callers. 항목 클릭 → 그 노드로 이동 + 스크롤.
- **헤더 신선도**(B3): `base <commit7> <branch> · built <built_at> · updated <updated_at>`,
  `dirty=1`이면 "(worktree dirty)", dirty 마커가 있으면 "stale — 커밋 이후 코드가 움직였다",
  그리고 [Rebuild]. 그래프가 없으면 화면 전체가 [Build Graph] 안내다.

---

## 5. 검증

### 5.1 파이프라인이 실제로 돌릴 수 있는 것
이 slice의 요구사항에는 `setup:`이 없어 worktree에 `desktop/node_modules`가 없다(실측).
따라서 **stage 안에서는 `npm test`/`tsc`가 돌 수 없다.** 정직하게 말하면:

- **implement/test stage의 채점 대상은 `python -m pytest`** (repo 루트에서).
  이것이 Done Criteria의 "기존 pytest 전량 통과 (엔진 무변경 증명)"이며, 이 slice는
  `aidev/**`를 한 줄도 건드리지 않으므로 전량 통과가 곧 무변경의 증거다.
- **test stage에 대한 지시**: 이 저장소의 스위트는 `python -m pytest`다(`pyproject.toml`의
  `[tool.pytest.ini_options] testpaths=["tests"]`). `desktop`의 `npm test`는
  `desktop/node_modules`를 요구하고 이 worktree에는 그것이 없다 —
  `npm ci`는 이 stage에 허용되지 않았으므로 **"실행 불가"로 보고**하고,
  PASS/FAIL은 pytest 결과로 판정하라. desktop 스위트를 억지로 통과시키려 하지 말 것.

### 5.2 이 slice가 추가하는 단위 테스트 (사람이 `desktop`에서 실행)
`desktop/src/main/*.test.ts`로 두고 `tsconfig.test.json`의 `include`에
`src/main/aidev-cli.ts`, `src/main/graph-store.ts`를 추가한다
(`src/main/index.ts`는 electron을 import 하므로 **넣지 않는다**).

| 파일 | 무엇을 못 박는가 |
|---|---|
| `aidev-store.test.ts` (확장) | `approved: <조건>`이 왕복에서 살아남는다(`parseDecision`/`writeApproval`이 `read_decision`과 일치) · `merge`/`quota`/`stopped`가 SliceState에 실린다 · `listRequirements`가 tasks/*.md만, 이름순으로 · `readSliceFailure`: quota=true→`usage_limit`, `result_subtype=error_max_turns`→`turns`, 둘 다 아니면 `other`, run_dir이 없는 기계에서도 죽지 않는다 · progress.md 본문과 리셋 잔여 시간 |
| `commands.test.ts` (신규) | 다섯 argv가 정확히 위 표대로 만들어진다 · `../../etc/passwd`, `a/b`, `..`, 빈 문자열, `tasks/x.md; rm -rf /` 전부 거절 · 턴 수는 정수 1..1000 |
| `guards.test.ts` (신규) | ★ merge 실패(exit≠0) → discard **비활성** · conflict 기록 → 비활성 · merged → 허용 · merged+push failed → 허용하되 경고 · merge 기록 없음 → 2단계 확인 |
| `graph-layout.test.ts` (신규) | 78파일/1335함수 입력에서 상자 78개, 상자끼리 겹치지 않음, 모든 함수에 anchor 존재 · 검색이 부분/대소문자 무시로 맞고 정렬이 안정적 |
| `graph-store.test.ts` (신규) | `node:sqlite`로 스키마 1짜리 가짜 graph.db를 만들어: 인덱스가 파일/함수/커버리지를 세고, 노드 상세가 calls/callers/tags를 되돌리고, 파일 없음/깨진 파일/스키마 2가 **던지지 않고** `{ok:false, problem}`이 된다. `node:sqlite`가 없는 런타임에서는 `{skip:true}` |

실행: `cd desktop && npm ci && npm test` (typecheck:node + typecheck:web + node --test).

### 5.3 Done Criteria 실물 검증 (사람이 Pluto repo로, 앱 안에서만)
1. `cd desktop && npm run dev` → repo로 이 저장소를 연다.
2. Pipeline 탭 → `tasks/*.md` 중 하나 선택 → **[Launch]** → 하단 `Run` 탭에 argv와
   `[pipeline] ...` 진행 줄이 실시간으로 흐른다.
3. plan 게이트가 열리면 Plan 화면에서 코멘트를 넣고 **[Approve]** →
   `approvals/plan.md`가 `approved: <문구>`가 되고, 이후 stage 프롬프트에 조건이 실린다.
4. 실패시킨 slice(또는 기존 failed slice)를 선택 → 사인/progress/리셋 잔여가 보이는지 →
   턴을 올려 **[Resume]**.
5. done slice에서 **[Merge + push]**. 일부러 base가 아닌 브랜치에 체크아웃해두고 눌러
   **merge 실패 → Discard 버튼이 잠기고 경고가 뜨는지** 확인(★). base로 옮겨 다시 merge →
   성공 후 **[Discard]** 동작.
6. Function graph 탭: 1300+ 함수와 78 파일이 뜨고, 검색 `run_pipeline` → 노드 포커스,
   클릭 시 요약/시그니처/@param/@flow/callers/calls가 뜨고 callers 클릭으로 이동,
   명세 없는 함수가 회색, 헤더에 base commit과 built 시각, [Rebuild] 동작.
7. 터미널 사용 0회.

---

## 6. 위험과 대비

**R1. Electron 39에 `node:sqlite`가 없을 수 있다.** (Node 22.16+이므로 Node 쪽 조건은 만족하지만
Electron 빌드 플래그에 달려 있다.) → 드라이버는 `graph-store.ts`의 `openReadonly()` 한 함수 뒤에만
있다. 없으면 그 함수가 `{ok:false, problem:'no-sqlite', detail}`을 돌려주고 화면이 이유를 그대로
말한다. 대체안은 `better-sqlite3` 추가(`postinstall`에 이미 `electron-builder install-app-deps`가
있어 리빌드는 자동) — 쿼리 코드는 그대로 두고 `openReadonly`만 갈아끼운다.

**R2. stage 안에서 TS를 컴파일할 수 없다.** 타입 오류가 사람의 `npm test`까지 잡히지 않는다.
→ 완화: 새 모듈을 작게/평범하게 쓰고, 제네릭·조건부 타입을 쓰지 않으며, 이 계획이 타입 시그니처와
SQL을 문자 그대로 적어 둔다. 남은 위험은 사람이 §5.2를 돌릴 때 드러난다 — 이 slice의 정직한 한계다.

**R3. GUI가 `aidev`를 PATH에서 못 찾는다**(Finder 실행 시 로그인 셸 PATH 미상속).
→ `resolveAidevBin`이 `AIDEV_BIN` → PATH → 흔한 설치 경로를 훑고, 실패하면 로그에
"aidev not found — set AIDEV_BIN"을 남긴다(조용한 무반응 금지).

**R4. 앱을 닫으면 자식 프로세스가 죽는다** (터미널을 닫는 것과 같다). 승인 대기 중이면 몇 시간짜리
런이 함께 사라질 수 있다. → 헤더에 "이 실행은 Pluto가 열려 있는 동안만 계속된다"를 표시하고,
[Stop]으로 명시적으로 끝낼 수 있게 한다. state.json은 어차피 durable하므로 [Resume]으로 복귀 가능.

**R5. 1335행 SVG의 렌더 비용.** → 상자 단위 `React.memo` + 선택 상태를 담은 상자만 리렌더.
그래도 무거우면 다음 slice에서 뷰포트 컬링(스크롤 교차 상자만 렌더)을 얹는다 — 자료구조
(`GraphBox`에 x/y/w/h가 이미 있다)는 그 확장을 이미 지원한다.

**R6. graph.db를 파이프라인이 갈아끼우는 중일 수 있다.** → 요청마다 열고 닫는 readonly 접근,
모든 실패는 빈 화면이 아니라 이유가 적힌 화면. 앱은 절대 쓰지 않는다(3조).

**R7. 단일 슬롯이 답답할 수 있다**(파이프라인이 승인 대기로 슬롯을 점유). → 일상 동선은
순차적이라 실제로 막히지 않는다. 막히면 [Stop] 또는 실행 종료 후 재시도. 두 슬롯은
merge/discard 경합을 다시 열기 때문에 일부러 하지 않는다.

---

## 7. 하지 않는 것 (요구사항 그대로)
Graph Notes / 도메인 구획 / `graph move` 드래그 / 계기판 탭 / `aidev/*.py` 수정 /
mock 데이터 삭제(배지만 단다) / 새 렌더 라이브러리 / 새 CLI 동사.
