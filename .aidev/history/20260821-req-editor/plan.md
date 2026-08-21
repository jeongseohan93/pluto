# plan — req 에디터: requirement 작성을 IDE에서

## 0. 읽은 것 / 확정된 사실

| 사실 | 근거 |
|---|---|
| `tasks/*.md` 목록은 이미 pipeline 도메인 소유 | `desktop/src/domains/pipeline/main/store.ts:37` `listRequirements` |
| 목록은 이미 최상위 **파일만** 훑는다 (`entry.isFile()`) → `tasks/specs/` 는 이미 제외됨 | `store.ts:41-43`, 기존 테스트 `store.test.ts:60` 이 `tasks/sub/` 를 만들어 두고 결과에 없음을 확인 |
| 발사 경로의 정규식이 이미 존재 | `domains/pipeline/commands.ts:22` `REQUIREMENT = /^tasks\/[A-Za-z0-9][A-Za-z0-9._-]*\.md$/`, `isRequirementPath` |
| 발사 배선 = `runCommand({kind:'pipeline', requirement})` → `commandArgv` → `CommandRunner.start` | `commands.ts:76`, `main/cli-runner.ts:150`, `App.tsx:279` |
| main 은 요청을 **두 번** 검사한다 (모양 + 이 repo 에 실재하는가) | `app/main/register-handlers.ts:191` `refuse()` |
| Monaco 은 worker 없이 `edcore.main` + Monarch 만 로드, 실패하면 null → 평문 폴백 | `domains/code-view/ui/monaco.ts:105`, `CodeViewer.tsx:98` |
| `EDITOR_OPTIONS` 는 `readOnly: true` 고정 | `monaco.ts:53` |
| markdown Monarch 문법은 **로드되지 않음** (python/ts/js 셋뿐) | `monaco.ts:111-113` |
| code-view 도메인은 "쓰기 없음"이 명시된 계약 | `domains/code-view/types.ts:53-56` |
| repo 로 쓰는 유일한 write 는 `writeApproval` 이라고 preload/ide.ts 가 문서로 선언 | `preload/index.ts:13`, `shared/ide.ts:305-310` |
| 원자적 쓰기 유틸이 이미 있음 (Windows rename 재시도 포함) | `main/aidev-store.ts:381` `writeTextAtomic` |
| git 신원 폴백 관례 = `-c user.name=aidev -c user.email=aidev@localhost`, 서명은 `commit.gpgsign=false` | `aidev/workspace.py:77-93` |
| front matter: `#` 로 시작하는 줄은 파서가 건너뜀 / 알려진 키 9개 | `aidev/pipeline.py:250`, `:642` |
| 커밋되지 않은 requirement 로도 발사는 된다 (`.aidev/slices/<id>/requirement.md` 로 복사됨) | `pipeline.py:5307-5311`, `:5439` |
| `node --test` 대상은 `src/domains/**/*.ts` 이되 `ui/` 는 제외, web 대상은 `main/` 제외 | `desktop/tsconfig.test.json:19-28`, `tsconfig.web.json:14` |

**설계 결론:** 편집 기능은 `code-view` 가 아니라 **`domains/pipeline`** 에 넣는다. `tasks/` 어휘(경로 정규식·목록·발사 argv)가 이미 전부 거기 있고, code-view 의 "읽기 전용" 계약을 깨면 코드 편집 개방으로 가는 문이 열린다. 요구사항의 "코드 편집 개방 아님 — requirement 전용"이 그대로 폴더 구조가 된다.

---

## 1. 새 파일

### 1-1. `desktop/src/domains/pipeline/requirement.ts` (순수 · fs/electron 없음)

`commands.ts` 와 같은 성격의 모듈. 테스트와 버튼이 같은 함수를 읽는다.

```ts
export const TASKS_DIR = 'tasks'

/** 사람이 친 이름 → 저장될 파일 이름. 확장자는 한 번만 붙는다. */
export function normalizeRequirementName(input: string): string
//  ' foo '   -> 'foo.md'      'foo.md' -> 'foo.md'      'foo.MD' -> 'foo.MD'
//  ''        -> ''            'a/b'    -> 'a/b.md' (여기서 거르지 않음 — 판정은 아래)

/** `tasks/<name>.md`, 또는 이 편집기가 열 수 없는 이름이면 null. */
export function requirementPathFor(input: string): string | null
//  normalize 후 `isRequirementPath` 로 판정. commands.ts 의 정규식을 그대로 재사용한다 —
//  발사기와 편집기가 "requirement 경로란 무엇인가"에 대해 어긋날 수 없게.
//  자동으로 걸러지는 것: '../x', 'a/b', 'specs/x', 'tasks/x'(중복), '.hidden', '-lead', 'C:\x'

/** 편집기가 이 이름을 왜 거부했는지, 한 문장. 입력칸 아래에 그대로 뜬다. */
export function requirementNameProblem(input: string): string
//  '' -> '파일 이름을 입력하세요'
//  경로 구분자 포함 -> 'tasks/ 안의 파일 하나만 — 폴더는 만들 수 없습니다'
//  그 외 -> '영문/숫자로 시작하고 . _ - 만 쓸 수 있습니다'

/** 커밋 메시지. */
export function requirementCommitMessage(path: string): string
//  'tasks/req-editor.md' -> 'task: req-editor'
```

> **판단 하나를 명시한다.** 요구사항은 `"task: <파일명>"` 이라고 썼다. 확장자를 붙이면 `task: req-editor.md` 가 되어 기존 로그(`task: req 에디터 — …`, `task: 소탕 2호 — …`)와 결이 어긋난다. **stem(확장자 없는 파일명)** 을 쓴다 — 여전히 "파일명"이고, 로그에서 읽힌다. 함수 docstring 에 이 선택을 적는다.

템플릿(같은 파일에서 export):

```ts
export const REQUIREMENT_TEMPLATE = `---
approval: plan
max_turns: implement=120
# test_commands: npm --prefix desktop run typecheck
---
# 제목

## 배경

## 요구사항
- 

## 하지 않는 것
- 

## Done Criteria
- 
`
```

- `# test_commands:` 는 주석 예시 — `parse_front_matter`(pipeline.py:250)가 `#` 시작 줄을 건너뛰므로 실제로 아무 것도 켜지 않고, `unknown_front_matter_keys` 경고도 나지 않는다. 요구사항이 말한 "주석 예시"가 정확히 이 뜻.
- `approval: plan` / `max_turns: implement=120` 은 요구사항이 지정한 값 그대로.
- 줄바꿈은 LF. 저장 시에도 CRLF→LF 로 정규화한다(`writeApproval` 이 이미 같은 규칙: `aidev-store.ts:367`).

### 1-2. `desktop/src/domains/pipeline/main/requirement-store.ts` (node:fs + node:child_process)

```ts
/** 편집기가 열 수 있는 최대 크기. requirement 는 수천 자다. */
export const MAX_REQUIREMENT_BYTES = 512 * 1024

export function readRequirement(repoRoot: string, relPath: string): RequirementDoc
export function saveRequirement(repoRoot: string, input: RequirementSaveInput): RequirementSave
```

`readRequirement` 흐름:
1. `requirementPathFor(basename)` 이 아니라 **경로 자체**를 `isRequirementPath` 로 판정 → 아니면 `{ok:false, problem:'refused'}`. `tasks/specs/a.md` 는 여기서 죽는다(정규식에 `/` 자리가 없다).
2. `resolve(repoRoot)` 기준 `abs.startsWith(base + sep)` 재확인 — `source-store.ts:57` 과 같은 벨트+멜빵. 모양으로 한 번, 해석 후 한 번.
3. 없으면 `missing`, `MAX_REQUIREMENT_BYTES` 초과면 `too-large`, 읽히면 BOM 제거 후 텍스트.
4. **절대 throw 하지 않는다.** 거부는 값의 모양으로 온다 (도메인 관례: `source-store.ts:111` `fail()`).

`saveRequirement` 흐름:
1. 경로 판정(위와 동일). 실패 → `{ok:false, error}`.
2. `input.create === true` 인데 파일이 이미 있으면 → `{ok:false, alreadyExists:true, error:'tasks/x.md 는 이미 있습니다'}`. 새 요구사항 버튼이 남의 파일을 덮는 일이 없게.
3. 본문 정규화: CRLF→LF, 끝에 개행 하나 보장.
4. `mkdirSync(join(repoRoot,'tasks'), {recursive:true})` → `writeTextAtomic`(`aidev-store.ts` 에서 import, 재구현 금지).
5. 커밋: `gitCommitPath(repoRoot, rel, message)`.
6. 반환: `{ok:true, path, committed, commit, unchanged?, error?}`. **파일 쓰기가 성공했으면 `ok:true`** — 커밋 실패는 `committed:false` + 사람이 읽을 이유로 따로 보고한다. 커밋이 안 됐다고 사람이 방금 쓴 글을 되돌리는 편집기는 없다.

`gitCommitPath` (같은 파일, 비공개):
```
git [-c commit.gpgsign=false] [-c user.name=aidev -c user.email=aidev@localhost]? add -- <rel>
git [같은 preamble]                                                                 commit -m <msg> -- <rel>
git rev-parse --short HEAD
```
- `spawnSync('git', argv, { cwd: repoRoot, shell: false, windowsHide: true, encoding: 'utf8' })`. `shell:false` 는 이 앱 전체의 규칙(`cli-runner.ts:186`).
- 신원 `-c` 는 `git config --get user.name` / `user.email` 이 **빈 값일 때만** 주입 — `workspace.py:77` `identity_args` 를 그대로 옮긴 것. repo 마다 한 번만 물어보고 모듈 안에 캐시.
- **`commit -- <경로>` (pathspec 한정)가 핵심이다.** 사용자의 체크아웃은 더러울 수 있고 index 에 남의 변경이 stage 돼 있을 수 있다. pathspec 을 주면 git 은 index 상태와 무관하게 그 파일 하나만 커밋한다. `add` 는 새 파일을 추적 대상으로 만들기 위해 남긴다(요구사항이 "git add + commit"이라고 명시).
- 실패 처리:
  - `error.code === 'ENOENT'` → `committed:false, error:'git 을 찾을 수 없습니다 (PATH)'`
  - `git commit` 이 exit 1 이고 stdout 에 `nothing to commit` → `committed:false, unchanged:true`, **에러 아님**. 내용이 안 바뀐 저장은 흔하다.
  - 그 외 non-zero → `committed:false, error: stderr 첫 줄들`.
  - repo 가 git 이 아님 → `add` 가 `not a git repository` 로 실패 → 위 경로로 흡수.

---

## 2. 고치는 파일

### 2-1. `desktop/src/domains/pipeline/types.ts`
- `RequirementFile` 에 `editable: boolean` 추가.
  - 목록은 **거르지 않는다.** 지금 26개 `tasks/*.md` 는 전부 정규식을 통과하지만, 한글 파일명 같은 것을 목록에서 조용히 지워버리면 발사기가 회귀한다. 대신 "이 편집기로 열 수 있는가"를 행에 표시한다.
- 새 타입:
```ts
export type RequirementProblem = 'refused' | 'missing' | 'too-large' | 'unreadable'
export interface RequirementDoc {
  ok: boolean; problem?: RequirementProblem; detail?: string
  path: string; name: string; text: string; bytes: number
}
export interface RequirementSaveInput { path: string; text: string; create: boolean }
export interface RequirementSave {
  ok: boolean; path: string; error?: string; alreadyExists?: boolean
  committed: boolean; commit: string | null; unchanged?: boolean; commitError?: string
}
```
- `PipelineBridge` 에 두 줄 추가:
```ts
  /** tasks/<name>.md 하나. 그 모양이 아닌 경로는 거부된다 — 코드 읽기가 아니다. */
  getRequirement(path: string): Promise<RequirementDoc>
  /** 이 앱이 repo 로 쓰는 두 번째이자 마지막 쓰기. 저장 = 파일 + git add/commit. */
  saveRequirement(input: RequirementSaveInput): Promise<RequirementSave>
```

### 2-2. `desktop/src/shared/ide.ts`
- `IPC` 에 `requirementRead: 'aidev:get-requirement'`, `requirementSave: 'aidev:save-requirement'` 추가.
- `AidevBridge` 의 doc 주석(`:305`) 수정: **"단 하나의 write" 문장이 거짓이 된다.** 두 개가 되었고 두 번째가 무엇이며 왜 좁은지(오직 `tasks/<name>.md`) 적는다. 문서가 코드에 대해 거짓말하게 두지 않는 것이 이 저장소의 관례.

### 2-3. `desktop/src/preload/index.ts`
- 두 멤버 배선(다른 것과 같은 한 줄짜리 `ipcRenderer.invoke`).
- 파일 상단 주석 `:13` "Exactly one member writes into the target repository: writeApproval" → 두 개로 갱신, 두 번째의 경계(모양이 `tasks/<name>.md` 인 것 하나, main 이 다시 검사)를 명시.

### 2-4. `desktop/src/app/main/register-handlers.ts`
```ts
ipcMain.handle(IPC.requirementRead, (_e, path: string): RequirementDoc => {
  const root = ctx.repoRoot()
  if (!root) return noRequirement(String(path ?? ''), 'refused', 'no repository selected')
  try { return readRequirement(root, String(path ?? '')) }
  catch (err) { return noRequirement(String(path ?? ''), 'unreadable', String(err)) }
})

ipcMain.handle(IPC.requirementSave, (_e, input: unknown): RequirementSave => {
  const root = ctx.repoRoot()
  const ask = (input ?? {}) as Partial<RequirementSaveInput>
  if (!root) return { ok:false, path:'', committed:false, commit:null, error:'no repository selected' }
  // 렌더러의 문자열은 두 번 검사된다: 여기서 모양, requirement-store 에서 해석 후 다시.
  if (!isRequirementPath(String(ask.path ?? ''))) {
    return { ok:false, path:String(ask.path ?? ''), committed:false, commit:null,
             error:`not a requirement: ${ask.path}` }
  }
  try { return saveRequirement(root, { path:String(ask.path), text:String(ask.text ?? ''), create: ask.create === true }) }
  catch (err) { return { ok:false, path:String(ask.path), committed:false, commit:null, error:String(err) } }
})
```
- 이 계층의 두 규칙(아무 것도 throw 하지 않는다 / 렌더러 문자열은 두 번 검사된다)을 그대로 지킨다.
- `getSourceFile` 처럼 그래프에 물어보지 **않는다** — requirement 는 그래프에 없다. 대신 경계가 `tasks/<name>.md` 정규식 하나로 더 좁다.

### 2-5. `desktop/src/domains/pipeline/main/store.ts`
- 로컬 `const TASKS_DIR = 'tasks'` 삭제 → `import { TASKS_DIR } from '../requirement'`. 정의 하나.
- `listRequirements` 각 항목에 `editable: isRequirementPath(\`${TASKS_DIR}/${name}\`)`.
- 주석 한 줄 추가: 왜 `entry.isFile()` 이 곧 `tasks/specs/` 제외인지 — 지금은 우연처럼 읽히는데 이제는 계약이다.
- 그 외 동작 변경 없음.

### 2-6. `desktop/src/domains/code-view/ui/monaco.ts`
- markdown Monarch 문법 추가. **단, `load()` 의 공용 try 안에 넣지 않는다.** 지금 `load()` 는 하나의 try 로 감싸여 있어서(`:106`) 경로가 틀리면 monaco 전체가 조용히 죽는다. 별도 함수로:
```ts
/** 색칠 하나가 에디터 전체를 죽이지 않도록, 문법은 따로 감싼다. */
async function tryLanguage(load: () => Promise<unknown>): Promise<void> {
  try { await load() } catch { /* 이 언어는 색이 없다. 그뿐이다. */ }
}
```
  기존 세 개도 여기로 옮기고 `markdown/markdown.contribution` 을 넷째로 추가. (monaco-editor 0.52.2 는 이 경로를 제공하지만 이 워크트리에 `node_modules` 가 없어 **직접 확인하지 못했다** — 그래서 격리한다.)
- 편집용 옵션을 파생 export (기존 상수는 건드리지 않는다 — 읽기 전용 뷰어가 같은 객체를 쓴다):
```ts
/** 같은 창, 같은 제약(워커 없음). 다른 것은 딱 하나 — 사람이 칠 수 있다. */
export const EDIT_OPTIONS: MonacoNs.editor.IStandaloneEditorConstructionOptions = {
  ...EDITOR_OPTIONS,
  readOnly: false,
  domReadOnly: false,
  wordWrap: 'on',            // requirement 는 산문이다
  renderLineHighlight: 'line',
  contextmenu: true          // 붙여넣기 메뉴. 워커를 부르지 않는다.
}
```
  `wordBasedSuggestions:'off'` / `quickSuggestions:false` / `hover:{enabled:false}` 등 워커를 부르는 것은 **끈 채로 상속** — `file://` + CSP 에서 워커를 못 만든다는 이 파일의 이유는 편집기에서도 그대로다.

### 2-7. `desktop/src/domains/pipeline/ui/RequirementEditor.tsx` (신규, 렌더러)

```tsx
export function RequirementEditor({
  target,       // { path: null } = 새 요구사항 · { path } = 기존 파일 수정
  busy,         // aidev 가 도는 중 — 저장/발사 잠금
  onClose,
  onSaved,      // (path) => void  — 사이드바 목록 갱신
  onLaunch      // (path) => void  — 기존 발사 배선 그대로
}): JSX.Element | null
```

- **웹 프로젝트는 `domains/**/main/**` 을 볼 수 없다**(`tsconfig.web.json:14`). 이 파일은 `../requirement`(순수) 와 `window.aidev` 만 import 한다. `requirement-store` import 금지.
- monaco 수명 관리는 `CodeViewer.tsx` 의 규율을 그대로 따른다: `live` 인 동안 host div 를 절대 조건부 unmount 하지 않고, 대기/거부 상태는 그 **위에** 덮는다(`CodeViewer.tsx:104-121`, 195-198). 안 지키면 detached node 로 그려서 패널이 조용히 빈다.
- 모델은 `createModel(text, 'markdown')`, `onDidChangeModelContent` 으로 `dirty` 와 현재 텍스트 추적.
- **monaco 폴백:** `failed` 면 `<textarea>` 로 같은 텍스트를 편집한다. 뷰어의 `PlainBody` 와 같은 정신 — "IDE 안에서 완결"이 monaco 로딩 여부에 걸리면 안 된다.
- 헤더 줄:
  - 파일 이름 입력칸(새 파일일 때만 활성). 아래에 `tasks/<name>.md` 미리보기, 거부되면 빨간 글씨 + `requirementNameProblem()` 한 문장. **이름 바꾸기는 범위 밖** — 기존 파일은 입력칸이 잠긴다.
  - `[저장]` — `busy || saving || !requirementPathFor(name) || (!dirty && saved)` 이면 비활성. tooltip 에 커밋 메시지(`task: <stem>`)를 미리 보여준다(승인 버튼이 쓸 문구를 미리 보여주는 `PlanSurface.tsx:126` 과 같은 관례).
  - `[발사]` — `savedPath !== null && !dirty && !busy` 일 때만. 누르면 `onLaunch(savedPath)`. **발사 로직은 한 줄도 손대지 않는다.**
  - `[닫기]` — `dirty` 면 한 번 더 묻는다.
- 결과 줄(`ResultLine` 과 같은 어조, 지어내지 않는다):
  - `saved · task: req-editor (a1b2c3d)`
  - `saved — 커밋되지 않음: git 을 찾을 수 없습니다 (PATH)` (경고색; 파일은 있고 발사도 된다)
  - `saved — 내용이 그대로라 커밋할 것이 없었습니다`
  - `tasks/x.md 는 이미 있습니다` (저장 안 됨)
- 새 파일 열기: 텍스트 = `REQUIREMENT_TEMPLATE`, 커서는 첫 `# 제목` 줄, 파일명 칸에 포커스.
- 기존 파일 열기: `getRequirement(path)` → `ok:false` 면 이유를 화면에 한 문장으로.

### 2-8. `desktop/src/domains/pipeline/ui/LaunchPanel.tsx`
- 시그니처: `{ busy, onLaunch, onNew, onEdit, reloadKey }`.
- **동작 변경 하나(의도적):** 지금은 `files.length === 0` 이면 패널 전체가 `null` 을 반환한다(`:36`). 그러면 빈 repo 에서 [새 요구사항] 에 닿을 길이 없다. 패널은 **항상** 그리고, 목록이 비었을 때는 select/Launch 줄만 감춘 채 `[+ 새 요구사항]` 만 남긴다.
- `[+ 새 요구사항]` 버튼(제목줄), 선택된 파일 옆 `[수정]` 버튼 → `onEdit(chosen)`. 선택된 파일이 `editable === false` 면 `[수정]` 비활성 + tooltip 으로 이유.
- `useEffect` 의존성에 `reloadKey` 추가 → 저장 후 목록이 즉시 갱신된다.

### 2-9. `desktop/src/renderer/src/features/pipeline/PipelineSidebar.tsx`
- `PipelineSidebarProps` 에 `onNewRequirement: () => void`, `onEditRequirement: (path:string) => void`, `requirementsVersion: number` 추가 → `LaunchPanel` 로 통과. 다른 변경 없음.

### 2-10. `desktop/src/renderer/src/App.tsx`
```ts
const [editing, setEditing] = useState<{ path: string | null } | null>(null)
const [requirementsVersion, setRequirementsVersion] = useState(0)
```
- 사이드바 props 에 `onNewRequirement: () => setEditing({ path: null })`, `onEditRequirement: (path) => setEditing({ path })`, `requirementsVersion` 배선.
- 메인 페인 위에 오버레이로 `<RequirementEditor …/>` 렌더:
  - `onSaved: () => setRequirementsVersion(n => n + 1)`
  - `onLaunch: (path) => { setEditing(null); runCommand({ kind:'pipeline', requirement: path }) }` — `runCommand`(`App.tsx:176`)가 이미 Run 패널을 열어준다.
- **새 `SurfaceId` 를 만들지 않는다.** `tab-cleanup` 슬라이스가 탭바를 Plan/Graph 둘로 줄여 놓았고, 대부분의 시간 비어 있을 탭을 하나 더 붙이는 것은 그 결정을 되돌리는 일이다. 오버레이가 더 싸고 스플릿/탭 기계를 건드리지 않는다.

---

## 3. 테스트

`ui/` 는 `tsconfig.test.json` 에서 제외되므로 테스트는 순수 모듈과 main 쪽에만 붙는다. 그것이 가드가 사는 곳이라 문제되지 않는다.

### 3-1. `desktop/src/domains/pipeline/requirement.test.ts` (신규)
- `normalizeRequirementName`: `'foo'→'foo.md'`, `'foo.md'→'foo.md'`(중복 안 붙음), `' foo '→'foo.md'`, `''→''`.
- `requirementPathFor` 승낙: `'foo'`, `'v0.2.5-x'`, `'a_b.md'` → `tasks/…`.
- `requirementPathFor` 거부(각각 별도 assert): `'../x'`, `'a/b'`, `'specs/x'`, `'tasks/x'`, `'/abs'`, `'C:\\x'`, `'.hidden'`, `'-lead'`, `''`.
- **템플릿을 실제 계약에 대고 검사:** front matter 블록이 `---` 로 열리고 닫힌다 / `approval: plan` 과 `max_turns: implement=120` 이 있다 / `test_commands` 를 담은 줄은 `#` 로 시작한다(= 파서가 무시 = 아무 것도 켜지 않는다) / 본문에 `## 배경`, `## 요구사항`, `## 하지 않는 것`, `## Done Criteria` 넷이 있다 / front matter 를 뗀 본문의 공백 제거 길이가 `MIN_REQUIREMENT_CHARS`(5) 이상이다.
- `requirementCommitMessage('tasks/req-editor.md') === 'task: req-editor'`.

### 3-2. `desktop/src/domains/pipeline/main/requirement-store.test.ts` (신규)
`store.test.ts` 의 관례(`mkdtempSync` + `after()` 정리)를 따른다.
- **읽기:** 있는 파일 → 텍스트 / 없는 파일 → `problem:'missing'` / `'tasks/specs/a.md'` → `problem:'refused'` (파일이 실제로 있어도) / `'../x.md'`, `'aidev/pipeline.py'`, `'C:\\x.md'` → `refused`. 어느 것도 throw 하지 않는다.
- **저장(git 없이):** git 이 아닌 임시 폴더에서도 `tasks/x.md` 가 실제로 생기고 `ok:true, committed:false`, `error` 아닌 `commitError` 에 이유가 담긴다.
- **저장(git 있음):** `git init` + 초기 커밋한 임시 repo 에서
  - 파일 생성 + `committed:true`, `git log -1 --format=%s` 가 `task: x`.
  - `git show --name-only HEAD` 에 `tasks/x.md` **하나만** 있다 — 미리 dirty 하게 만들어 둔 다른 파일(예: `noise.txt` 를 `git add`)이 휩쓸려 들어가지 않음을 확인. pathspec 한정의 이유가 곧 테스트.
  - 같은 내용으로 다시 저장 → `ok:true, committed:false, unchanged:true`, throw 없음.
  - `create:true` 인데 이미 있음 → `ok:false, alreadyExists:true`, 그리고 **디스크의 내용이 변하지 않았음**을 확인.
  - `../` / `tasks/specs/…` 저장 시도 → `ok:false`, 그리고 그 경로에 파일이 생기지 않았음을 확인.
- git 이 필요한 케이스는 `spawnSync('git',['--version'])` 성공 여부로 감싸 **skip** 한다 (`workspace.py:54` `git_available` 과 같은 태도 — git 없는 기계에서 조용히 물러난다).

### 3-3. `desktop/src/domains/pipeline/main/store.test.ts` (추가)
- `tasks/specs/umbrella.md` 를 만들어 두고 목록에 없음을 확인 — 지금은 `tasks/sub/`(빈 폴더)로만 간접 확인 중이라, 요구사항이 이름을 댄 케이스를 직접 못 박는다.
- `editable`: `a-first.md` 는 true, `한글.md` 는 false 이면서 **목록에는 남아 있다**(발사기 회귀 없음).

### 3-4. 파이썬
변경 없음. `aidev/` 는 한 줄도 건드리지 않는다.

---

## 4. 검증

### 실제로 돌릴 것
```
pytest                                  # aidev/ 무회귀 — 이 워크트리에서 지금 돌아간다
npm ci   --prefix desktop               # 아래 '위험' 참조
npm test --prefix desktop               # = typecheck(node+web) + tsc -p tsconfig.test.json + node --test
```
`npm test` 하나가 요구사항의 "tsc 통과"와 새 테스트 실행을 동시에 덮는다(`desktop/package.json:14`).

### 손으로 확인할 동선 (Done Criteria 를 그대로 따라감)
1. Pipeline 사이드바 → `[+ 새 요구사항]` → 편집기가 템플릿을 채운 채 열린다.
2. 이름 `smoke-req` 입력 → 미리보기가 `tasks/smoke-req.md`. `../x` 를 치면 빨간 글씨로 거부 이유.
3. 본문을 조금 고치고 `[저장]` → `saved · task: smoke-req (<sha>)`. 터미널에서 `git show --stat HEAD` 로 그 파일 하나뿐임을 확인.
4. `[발사]` → Run 패널에 `aidev pipeline --repo … --requirement tasks/smoke-req.md`.
5. 목록에서 기존 `tasks/ui-split.md` 선택 → `[수정]` → 같은 편집기에 내용이 뜬다. 한 글자 고치고 저장 → 새 커밋 하나.
6. `tasks/specs/` 를 만들고 안에 md 를 넣어도 목록에 안 나오고, DevTools 에서 `window.aidev.getRequirement('tasks/specs/x.md')` 를 직접 불러도 `refused`.
7. 빈 repo(=`tasks/` 없음)를 열어도 `[+ 새 요구사항]` 이 보인다.

---

## 5. 잘못될 수 있는 것

1. **★ 이 워크트리에 `desktop/node_modules` 가 없다 (확인함).** 그리고 `tasks/req-editor.md` 에는 `setup:` 줄이 없다 — 다른 requirement 들(`ui-split.md:4`, `graph-codeview.md:4`)은 전부 `setup: npm ci --prefix desktop` 을 달고 있는데 이것만 없다. `setup:` 이 없으면 `npm ci` 에 해당하는 Bash 권한이 열리지 않고(`pipeline.py:111` `TEST_COMMAND_TOOLS` 에 npm install 계열 없음), 선언된 `test_commands: npx --prefix desktop tsc --noEmit -p desktop` 도 의존성 없이는 `react`/`electron`/`monaco-editor` 를 못 찾아 실패한다.
   **implement 단계에 대한 지시:** `npm ci --prefix desktop` 을 시도하라. 권한으로 막히면 **거짓으로 통과를 주장하지 말고** progress/summary 에 "TypeScript 검증 미실행 — 워크트리에 node_modules 없음, setup: 줄 부재"라고 그대로 적어라. 그 경우 실제로 돌릴 수 있는 검증은 `pytest`(기본 허용) 뿐이고, TS 쪽은 (a) 위 테스트 파일들을 정확히 작성해 두고 (b) 타입을 손으로 맞춰 두는 것까지가 이번 슬라이스의 몫이다. 병합 전에 사람이 `npm ci && npm test` 를 한 번 돌려야 한다.
2. **markdown Monarch 경로 미확인.** `node_modules` 가 없어 `esm/vs/basic-languages/markdown/markdown.contribution` 의 존재를 직접 못 봤다. 지금 `load()` 는 통짜 try 라 경로가 틀리면 **monaco 전체가 null 이 되어 앱이 조용히 평문 폴백으로 떨어진다**. 그래서 2-6 에서 언어 로드를 개별 try 로 쪼갠다. 이게 이 위험에 대한 유일한 방어다.
3. **`EDITOR_OPTIONS` 를 제자리에서 고치면 읽기 전용 뷰어가 편집 가능해진다.** 같은 객체를 공유한다. 반드시 스프레드로 새 상수를 만들 것.
4. **워커 옵션을 켜고 싶은 유혹.** `file://` + CSP 에서 워커는 못 만든다(`monaco.ts:15-20`). 자동완성/검증을 켜면 콘솔이 조용히 터진다. 상속된 off 를 그대로 둔다.
5. **커밋이 사용자 체크아웃의 HEAD 를 움직인다.** 이후에 만들어질 슬라이스 워크트리는 그 HEAD 에서 분기하므로 오히려 원하는 동작이다(사람이 지금 손으로 하는 일이 정확히 이것). 이미 돌고 있는 슬라이스는 자기 브랜치를 갖고 있어 영향 없다. 다만 pathspec 한정을 빠뜨리면 사용자가 stage 해 둔 남의 변경까지 삼킨다 — 3-2 에 이걸 못 박는 테스트가 있다.
6. **Windows:** rename 경합은 `writeTextAtomic` 의 재시도 예산이 이미 처리한다. `spawnSync('git')` 은 PATH 에 `git.exe` 가 있어야 하고, 없으면 ENOENT 를 문장으로 보고한다(뷰어가 `AIDEV_BIN` 없음을 다루는 방식과 같음, `cli-runner.ts:203`).
7. **줄바꿈.** 저장 시 CRLF→LF 정규화. 안 하면 Windows 편집기가 만든 파일이 diff 전체를 붉게 물들인다.
8. **`LaunchPanel` 이 더 이상 null 을 반환하지 않는 것은 눈에 보이는 동작 변경이다.** 요구사항이 강제하는 변경이고 위에 명시했다.
9. **문서가 거짓말하게 두지 말 것.** `preload/index.ts:13` 과 `shared/ide.ts:305` 의 "쓰기는 writeApproval 하나뿐" 문장은 이번에 거짓이 된다. 같은 커밋에서 고친다.
10. **범위 이탈 유혹:** `getRequirement` 를 "그냥 파일 읽기"로 넓히거나 `saveRequirement` 를 임의 경로로 넓히면 그 순간 코드 편집 개방이 된다. 두 채널의 유일한 관문은 `commands.ts` 의 `REQUIREMENT` 정규식이며, 그 정규식은 **새로 쓰지 않고 import 한다** — 발사기와 편집기가 어긋날 여지를 아예 없앤다.
11. **템플릿만 저장하고 발사하면** `guard_requirement`(`pipeline.py:697`)를 통과한다(섹션 제목만으로도 5자를 넘는다). 편집기가 이걸 막지는 않는다 — 빈 requirement 판정을 두 곳에서 하면 규칙이 갈라진다. CLI 의 판정이 유일한 판정으로 남고, 실패하면 Run 로그가 이유를 말한다.
