# 코드 뷰어 소환 (Monaco, readonly) — 구현 계획

## 0. 먼저 확인한 사실 (실측)

| 확인 대상 | 결과 |
|---|---|
| `desktop/node_modules` | **없다.** `.gitignore:12`이 `node_modules/`를 제외하므로 이 slice 워크트리에는 존재하지 않는다 |
| `tasks/graph-codeview.md` front matter | `setup:` 없음, `test_commands: npx --prefix desktop tsc --noEmit -p desktop` |
| implement 단계 Bash 권한 | `pipeline.py:111` `TEST_COMMAND_TOOLS` + `command_tool_rules()` (`pipeline.py:719`) → `Bash(npm test)`, `Bash(pytest:*)`, `Bash(python -m pytest:*)`, `Bash(npx --prefix desktop tsc --noEmit -p desktop)`, `Bash(npx --prefix:*)`. **`npm ci` / `npm install` 규칙은 없다** |
| `desktop/tsconfig.json` | `{"files": [], "references": [...]}` — 솔루션 파일. `--build` 없이 `-p desktop`은 0개 파일을 검사하고 통과한다 |
| `run_pipeline` 실제 좌표 | `aidev/pipeline.py:4277` (요구사항에 적힌 3310행은 옛 리비전 기준) |
| `index.html` CSP | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` — `blob:` 없음 |
| 창 로드 방식 | dev는 `loadURL(http)`, 프로덕션은 `loadFile` → `file://` origin |
| 렌더러 보안 | `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false` (`main/index.ts:31`) |
| 기존 IPC 도그마 | `register-handlers.ts:10-16` — "아무것도 throw하지 않는다", "렌더러의 문자열은 두 번 검사한다" |

**이 계획이 서 있는 전제(정직하게):** implement 단계는 `node_modules`를 만들 수 없다. 따라서 monaco 유무와 무관하게 이 워크트리에서 의미 있는 `tsc`/`node --test` 실행은 불가능하다. 선언된 test_command는 위 표대로 **아무것도 검사하지 않고 통과**한다. 이 계획은 그 사실을 우회하지 않고, 대신 (a) 실행 불가한 검증을 통과했다고 주장하지 말 것, (b) 런타임 실패가 화면을 날리지 않도록 폴백을 반드시 넣을 것을 요구한다. §6/§7 참조.

---

## 1. 설계 요지

**경계는 하나 더 늘어난다: `readSource`.** 지금까지 렌더러가 도달할 수 있는 것은 `.aidev/` 읽기·승인 쓰기·명명된 CLI 실행·Function DB 읽기뿐이었다. 여기에 "저장소 안의 텍스트 파일 하나 읽기"가 추가된다. `preload/index.ts:8-11`이 명시적으로 금지한 `readFile(anyPath)`가 되지 않도록, 이 능력은 **세 겹으로** 좁힌다.

1. `source-store.ts`: 상대 경로만, `..`·절대경로·root 밖 해석 거부, 확장자 화이트리스트, 크기 상한, NUL 바이트 → binary 거부
2. `register-handlers.ts`: **그래프가 아는 파일인가** — 기존 `refuse()`가 "이 저장소에 실제로 존재하는 requirement/slice인가"를 묻는 것과 같은 층의 검사
3. `main/index.ts`가 소유한 `repoRoot` — 렌더러는 루트를 지명할 수 없다 (변경 없음)

**Monaco는 웹 워커 없이 쓴다.** 프로덕션 창은 `file://`로 로드되고, Chromium은 `file://` origin에서 `new Worker(...)`를 차단한다. CSP에 `blob:`도 없으므로 monaco 기본 `getWorkerUrl`(blob importScripts)도 막힌다. readonly 뷰어에 필요한 것은 **Monarch 신택스 하이라이트 + 내장 find 위젯**뿐이고 둘 다 메인 스레드에서 돈다. 그래서 언어 서비스(ts/json/css/html worker)를 아예 번들에 넣지 않고, 워커를 건드리는 에디터 옵션을 전부 끈다.

**소환이므로 지연 로드한다.** Monaco는 뷰어가 처음 열릴 때 `await import(...)`로 들어온다. Function 그래프 탭을 열기만 한 사용자는 monaco 청크를 받지 않는다.

**패널 위치는 Function 그래프 표면 안이다.** 요구사항의 "패널이 열리고"는 탭 전환이 아니다. 캔버스와 Node 상세 패널 사이에 세 번째 컬럼으로 들어간다 (`FunctionGraphSurface.tsx:861` `<div className="flex min-h-0 flex-1">` 안). 기존 DEMO `code` 탭(`features/code/CodeSurface.tsx`)은 건드리지 않는다 — 요구사항 범위 밖이다.

---

## 2. 변경 파일 목록

### 신규 도메인 `desktop/src/domains/code-view/`

`graph-view`와 같은 모양: `types.ts`(양쪽이 공유) / `main/`(node만) / `ui/`(React만). 세 tsconfig의 include·exclude 패턴이 이미 `src/domains/**`를 덮으므로 **tsconfig 수정은 필요 없다** (`tsconfig.test.json:19-28`, `tsconfig.web.json:3-14`, `tsconfig.node.json:3-14` 확인함).

---

#### 2.1 `desktop/src/domains/code-view/types.ts` (신규)

```ts
/** 뷰어가 겨냥한 좌표. path만 파일을 결정하고, 나머지는 그 안에서의 위치. */
export interface CodeTarget {
  /** repo-relative, 그래프 DB가 쓰는 그 철자 그대로 (POSIX 구분자). */
  path: string
  /** 스크롤이 멈출 줄. 1-based. */
  line: number
  /** 범위 하이라이트의 끝. line과 같으면 한 줄만 칠한다. */
  endLine: number
  /** 툴팁/헤더용 라벨 — 어떤 함수/호출부를 보러 왔는가. */
  label: string
}

export type SourceProblem =
  | 'no-repo' | 'not-in-graph' | 'unsupported' | 'missing'
  | 'too-large' | 'binary' | 'unreadable'

export interface SourceFile {
  ok: boolean
  problem?: SourceProblem
  detail?: string
  /** 요청받은 그대로의 상대 경로. 실패해도 채운다 — "어디를 못 읽었나"가 답이다. */
  path: string
  text: string
  lines: number
  bytes: number
  /** monaco language id. 실패 시 빈 문자열. */
  lang: string
}

/** 렌더러가 소스에 대해 할 수 있는 전부. 읽기 하나. */
export interface CodeViewBridge {
  getSourceFile(path: string): Promise<SourceFile>
}
```

#### 2.2 `desktop/src/domains/code-view/language.ts` (신규)

순수 함수 하나. Electron·fs 의존 없음 → main·renderer·`node --test` 셋 다에서 쓰인다.

```ts
export function monacoLanguage(path: string): string   // 모르는 확장자면 ''
```

매핑: `py→python`, `ts→typescript`, `tsx→typescript`, `js|mjs|cjs|jsx→javascript`, `json→json`, `md→markdown`, `css→css`, `html→html`, `sh→shell`, `yml|yaml→yaml`, `toml→ini`, `txt→plaintext`.

**이 함수가 확장자 화이트리스트를 겸한다** — `''`를 돌려주면 `source-store`가 `unsupported`로 거부한다. 판단 지점을 하나로 유지하기 위해서다. 그래프가 인덱싱하는 것은 py/ts/tsx/js뿐이므로 나머지는 여유분이다.

#### 2.3 `desktop/src/domains/code-view/language.test.ts` (신규)

`node:test`. 각 확장자 → 기대 id, 대문자 확장자(`.PY`), 확장자 없음, 점 없는 이름, `.tar.gz` 같은 이중 확장자, 빈 문자열.

#### 2.4 `desktop/src/domains/code-view/main/source-store.ts` (신규)

`graph-store.ts`와 같은 계약: **아무것도 throw하지 않고, 모든 실패가 화면 위의 한 문장이 된다.**

```ts
/** pipeline.py는 ~200KB. 4MiB면 사람이 읽는 소스 파일의 상한으로 넉넉하다. */
const MAX_BYTES = 4 * 1024 * 1024

export function readSource(repoRoot: string, relPath: string): SourceFile
```

`@flow`:
1. `relPath`가 문자열이 아니거나 비었으면 → `unreadable`
2. 구분자를 `/`로 정규화, `..` 세그먼트·절대경로(`/`, 드라이브 문자, UNC) 포함 시 → `unreadable`
3. `resolve(repoRoot, rel)`가 `resolve(repoRoot) + sep`로 시작하지 않으면 → `unreadable` (`aidev-store.ts:415-422` `sliceDir`와 같은 관용구)
4. `monacoLanguage(rel) === ''` → `unsupported`
5. `statSync` 실패 → `missing`; 디렉터리 → `missing`; `size > MAX_BYTES` → `too-large` (실제 바이트 수를 `detail`에)
6. `readFileSync(abs)` → 버퍼. 앞 8KB에 `0x00`이 있으면 → `binary`
7. `buf.toString('utf8')`, 선두 BOM 제거, `lines = text.split('\n').length`
8. `{ok: true, path: rel, text, lines, bytes, lang}`

전체를 `try/catch`로 감싸 마지막 방어선은 `unreadable` + `String(err)`.

#### 2.5 `desktop/src/domains/code-view/main/source-store.test.ts` (신규)

`graph-store.test.ts`의 `mkdtempSync` 관용구를 그대로 따른다. 케이스: 정상 py 읽기(줄 수·lang), `../../etc/passwd`, `..\\..\\x`, 절대경로, `C:\\Windows\\...`, 심볼릭 링크 없이 `a/../../b`, `.png` → unsupported, 없는 파일 → missing, 디렉터리 → missing, `MAX_BYTES` 초과 → too-large, NUL 포함 → binary, BOM 제거, CRLF 보존.

#### 2.6 `desktop/src/domains/graph-view/main/graph-store.ts` (수정)

두 번째 검사를 위한 작은 조회 하나를 추가한다. 기존 함수·쿼리는 손대지 않는다.

```ts
/**
 * 이 그래프가 아는 파일인가 — 렌더러가 지명한 경로의 두 번째 검사.
 *
 * `register-handlers.ts`의 `refuse()`가 requirement/slice에 대해 묻는 것과
 * 같은 질문이다: 모양이 맞는다고 이 저장소의 것은 아니다.
 *
 * @param repoRoot  앱이 열어둔 저장소
 * @param path      렌더러가 보낸 상대 경로
 * @flow  db 없음/열기 실패 -> false ; files 또는 functions에 이 경로가 있으면 true
 */
export function pathInGraph(repoRoot: string, path: string): boolean
```

`SELECT 1 FROM files WHERE path = ? LIMIT 1` 실패 시 `SELECT 1 FROM functions WHERE path = ? LIMIT 1`. `openReadonly` 재사용, `finally`에서 `closeQuietly`. throw 없음(catch → false).

> 반쯤 쓰인 갱신으로 `files` 행이 사라진 함수도 존재한다는 것을 `readGraphIndex:154-157`가 이미 인정하고 있으므로 `functions` 쪽도 본다.

#### 2.7 `desktop/src/domains/graph-view/main/graph-store.test.ts` (수정)

기존 `repoWithGraph()` 픽스처에 대해: 아는 파일 → true, 파싱 실패한 파일도 `files`에 있으니 → true, 모르는 경로 → false, graph.db 없는 루트 → false, `''` → false.

#### 2.8 `desktop/src/domains/code-view/ui/monaco.ts` (신규) — **의존성 이음매**

Monaco를 아는 유일한 파일. `graph-store.ts:314-358`이 SQLite 드라이버를 한 곳에 가둔 것과 같은 이유다: 실패할 수 있는 질문은 한 함수 뒤에 있어야 한다.

```ts
import type * as MonacoNs from 'monaco-editor'
export type MonacoApi = typeof MonacoNs

/** 팔레트 이름 — theme.css의 --color-* 를 monaco 토큰으로 옮긴 것. */
export const THEME = 'pluto-dark'

/**
 * Monaco를, 한 번만. 실패는 예외가 아니라 null이다 — 뷰어는 폴백으로 그린다.
 */
export function ensureMonaco(): Promise<MonacoApi | null>
```

내부:

```ts
let pending: Promise<MonacoApi | null> | null = null

async function load(): Promise<MonacoApi | null> {
  try {
    // edcore.main = editor.api + editor.all(find/folding/bracket/hover 등).
    // editor.main이 아니다: 그쪽은 ts/json/css/html 언어 서비스를 끌고 들어오고,
    // 그 서비스들은 웹 워커를 요구한다. 프로덕션 창은 file:// 이라 워커를 만들 수 없다.
    await import('monaco-editor/esm/vs/editor/edcore.main')
    // Monarch 하이라이트만. 워커 없음.
    await import('monaco-editor/esm/vs/basic-languages/python/python.contribution')
    await import('monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution')
    await import('monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution')
    const api = (await import('monaco-editor/esm/vs/editor/editor.api')) as unknown as MonacoApi
    api.editor.defineTheme(THEME, { /* 아래 표 */ })
    return api
  } catch {
    return null
  }
}
```

테마 (`base: 'vs-dark'`, `inherit: true`) — `theme.css:9-32`의 값 그대로:

| monaco 키 | 값 | 출처 |
|---|---|---|
| `editor.background` | `#0d0e10` | `--color-app` |
| `editor.foreground` | `#9ea3ab` | `--color-fg-dim` |
| `editorLineNumber.foreground` | `#676c74` | `--color-fg-mute` |
| `editorLineNumber.activeForeground` | `#e3e5e8` | `--color-fg` |
| `editor.selectionBackground` | `#1c2836` | `--color-accent-soft` |
| `editor.lineHighlightBackground` | `#131417` | `--color-panel` |
| `editorGutter.background` | `#0d0e10` | `--color-app` |
| `editorWidget.background` | `#131417` | `--color-panel` |
| `editorWidget.border` | `#232529` | `--color-line` |
| `input.background` | `#0d0e10` | `--color-app` |
| `focusBorder` | `#4a7fc7` | `--color-accent` |
| `scrollbarSlider.background` | `#2b2e35` | 스크롤바 규칙과 동일 |

`export const EDITOR_OPTIONS` — **워커를 건드리지 않는 readonly 구성**:

```ts
readOnly: true, domReadOnly: true,
automaticLayout: true,
minimap: { enabled: false },
fontFamily: "'Cascadia Mono', Consolas, ui-monospace, monospace",
fontSize: 12, lineHeight: 18,
scrollBeyondLastLine: false,
renderLineHighlight: 'none',
// --- 아래는 전부 editor worker를 부르는 기능들이다. readonly 뷰어엔 불필요.
links: false,
unicodeHighlight: { ambiguousCharacters: false, invisibleCharacters: false, nonBasicASCII: false },
wordBasedSuggestions: 'off', quickSuggestions: false, suggestOnTriggerCharacters: false,
occurrencesHighlight: 'off', codeLens: false, hover: { enabled: false },
renderValidationDecorations: 'off',
// find 위젯은 model.findMatches — 메인 스레드다. 요구사항이 이걸 쓰라고 했으므로 켠 채 둔다.
find: { addExtraSpaceOnTop: false, seedSearchStringFromSelection: 'never' },
```

> **implement에게:** 위 서브패스가 설치된 버전에 없으면(`edcore.main` 부재 등) 폴백은 `import * as monaco from 'monaco-editor'` 하나로 바꾸고 `EDITOR_OPTIONS`는 그대로 두는 것이다. 그 경우 언어 서비스가 워커를 요구할 수 있으므로, **`ensureMonaco`의 try/catch와 §2.9의 폴백 본문이 반드시 살아 있어야 한다.** 서브패스를 검증할 수 없는 환경이므로 이 이중 안전장치는 선택이 아니다.

#### 2.9 `desktop/src/domains/code-view/ui/CodeViewer.tsx` (신규)

```tsx
export function CodeViewer({ target, onClose }: {
  target: CodeTarget | null
  onClose: () => void
}): JSX.Element
```

상태: `source: SourceFile | null`, `loading: boolean`, `api: MonacoApi | null`, `failed: boolean`.

동작:

- **fetch는 `target.path`에만 걸린다** (`useEffect(..., [target?.path])`). 같은 파일 안의 다른 함수로 옮겨갈 때 5000행짜리 파일을 다시 읽지 않는다. 언마운트/경로 교체 후 도착한 응답은 `alive` 플래그로 버린다 — `useFunctionGraph.ts:29-36`, `FunctionGraphSurface.tsx:292-307`과 같은 관용구.
- **Monaco 생성은 한 번.** `ensureMonaco()` → `api.editor.create(hostRef.current, {...EDITOR_OPTIONS, theme: THEME})`. `null`이면 `failed = true`.
- **모델은 한 번에 하나.** 경로가 바뀌면 이전 모델 `dispose()` 후 `createModel(text, lang)` → `setModel`. 멀티 탭은 요구사항이 명시적으로 배제했다.
- **좌표 이동:**
  ```ts
  const end = Math.max(target.line, Math.min(target.endLine, model.getLineCount()))
  decorations.set([{
    range: new api.Range(target.line, 1, end, 1),
    options: { isWholeLine: true,
               className: 'code-range',
               linesDecorationsClassName: 'code-range-gutter' }
  }])
  editor.revealLineNearTop(target.line, ScrollType.Immediate)
  editor.setPosition({ lineNumber: target.line, column: 1 })
  ```
  `createDecorationsCollection`을 한 번 만들어 ref에 두고 `.set()`으로 갈아끼운다 (`deltaDecorations`는 deprecated).
- **정리:** 언마운트 시 decorations → model → editor 순으로 `dispose()`.
- **레이아웃:** `automaticLayout: true`가 `ResizeObserver`로 처리하므로 `react-resizable-panels`의 크기 변화를 따라간다.

**본문 3-way 분기:**

| 상태 | 그리는 것 |
|---|---|
| `loading` 또는 `source === null` | "Reading …" (기존 `FunctionDetail`의 대기 문구와 같은 톤) |
| `source.ok === false` | 이유 한 문장 + 경로 (`EmptyState` 재사용, `problem`별 제목) |
| `source.ok && api && !failed` | Monaco 호스트 `<div ref={hostRef} className="h-full w-full" />` |
| `source.ok && (failed \|\| !api)` | **`PlainBody`** — 아래 |

`PlainBody`: `CodeSurface.tsx:39-52`의 `<table>` 줄번호 관용구를 그대로 쓰되 전체 파일을 그리고, `target.line ~ endLine` 행에 `bg-accent-soft`, 좌측에 `border-l-2 border-accent`를 준다. 마운트 시 `scrollIntoView({block: 'center'})`로 시작 줄로 스크롤. 헤더에 `monaco unavailable` 배지.

> 이 폴백은 장식이 아니라 이 slice의 **검증 불가 리스크에 대한 유일한 실질적 완화**다. monaco 로드가 실패해도 요구사항의 "파일 로드 + 시작 라인 스크롤 + 범위 하이라이트"는 성립한다.

**헤더** (`h-7`, `border-b border-line`, 기존 Node 패널 헤더와 같은 치수):
접기 셰브런(`Icon name="chevron"`, `FunctionGraphSurface.tsx:1053-1062`과 동일한 패턴) → `onClose`, `panel-label` "Code", 그 뒤에 `path:line-endLine` (truncate, `title`에 전체 경로), 우측에 `{lines} lines · {kb} KB`.

#### 2.10 `desktop/src/shared/ide.ts` (수정)

```ts
import type { CodeViewBridge } from '../domains/code-view/types'
export interface AidevBridge extends PipelineBridge, GraphBridge, CodeViewBridge { ... }
```
`IPC` 객체에 한 줄 추가:
```ts
  /** v0.2.7 — 저장소 안의 텍스트 파일 하나. 그래프가 아는 경로만. */
  sourceFile: 'aidev:get-source-file'
```

#### 2.11 `desktop/src/preload/index.ts` (수정)

`getGraphNode` 아래에 한 줄. 파일 상단 주석(8-11행)에 "no `readFile(anyPath)`"라고 쓰여 있으므로, **왜 이것이 그 예외가 아닌지**를 짧게 덧붙인다.

```ts
  // -- v0.2.7 소스 한 파일, 읽기 전용.
  // 8행이 금지한 `readFile(anyPath)`가 아니다: main이 열어둔 저장소 안이어야 하고,
  // 그래프가 아는 경로여야 하며, 확장자와 크기 상한을 통과해야 한다.
  getSourceFile: (path) => ipcRenderer.invoke(IPC.sourceFile, path)
```

#### 2.12 `desktop/src/app/main/register-handlers.ts` (수정)

`IPC.graphNode` 핸들러 뒤에 추가. 파일 상단의 두 규칙(never throws / checked twice)을 그대로 지킨다.

```ts
ipcMain.handle(IPC.sourceFile, (_event, path: string): SourceFile => {
  const rel = typeof path === 'string' ? path : ''
  const root = ctx.repoRoot()
  if (!root) return fail(rel, 'no-repo', 'no repository selected')
  // 두 번째 검사: 모양이 맞는다고 이 저장소의 파일은 아니다.
  try {
    if (!pathInGraph(root, rel)) {
      return fail(rel, 'not-in-graph', `this graph does not know ${rel}`)
    }
    return readSource(root, rel)
  } catch (err) {
    return fail(rel, 'unreadable', String(err))
  }
})
```
`fail()`은 이 파일 하단의 작은 헬퍼(`{ok:false, problem, detail, path, text:'', lines:0, bytes:0, lang:''}`).

#### 2.13 `desktop/src/domains/graph-view/ui/FunctionDetail.tsx` (수정)

새 prop 두 개:
```ts
  /** 좌표로 뷰어를 옮긴다 (열지는 않는다). */
  onReveal: (target: CodeTarget) => void
  /** 뷰어를 연다 — [코드 보기] 버튼. */
  onOpenCode: (target: CodeTarget) => void
```

1. **헤더에 `[코드 보기]` 버튼.** 43-52행의 헤더 블록, `fn.path:fn.lineno` 줄 우측. 클릭 → `onOpenCode({path: fn.path, line: fn.lineno, endLine: fn.endLineno || fn.lineno, label: fn.qualname})`. `FreshnessBar`의 `Rebuild` 버튼과 같은 스타일(`rounded-sm border border-line px-1.5 py-0.5 text-micro`).

2. **`Row`에 좌표를 실어 보낸다.** `Row`의 `onClick`은 지금 `onSelect(id)` 하나다. 여기에 `onReveal`을 겹쳐 부른다 — **선택 이동과 좌표 이동은 같은 클릭이다.**
   - calls 행: `call.targetId !== null && call.targetPath !== null`일 때
     `onReveal({path: call.targetPath, line: call.targetLine ?? 1, endLine: call.targetLine ?? 1, label: call.targetQualname ?? call.callee})`
     — 여기서 목적지는 **함수 정의 줄**(`targetLine`)이다.
   - callers 행: 언제나
     `onReveal({path: caller.path, line: caller.callLine, endLine: caller.callLine, label: caller.caller})`
     — **호출부 줄(`callLine`)**이지 호출자 정의 줄(`callerLine`)이 아니다. 화면이 이미 `${caller.path}:${caller.callLine}`을 보여주고 있으므로, 클릭이 데려가는 곳도 그곳이어야 한다. `unresolved` 행도 마찬가지로 이동한다 — 엔진이 대상을 못 고른 것이지 호출부가 없는 게 아니다 (`graph-store.ts:282-284`의 논거 그대로).

#### 2.14 `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` (수정)

상태 두 개 추가: `const [codeTarget, setCodeTarget] = useState<CodeTarget | null>(null)`, `const [codeOpen, setCodeOpen] = useState(false)`.

**여는 규칙 (요구사항의 두 문장을 그대로):**

| 동작 | target | open |
|---|---|---|
| 노드 행 **더블클릭** | 설정 | `true` |
| Node 패널 **[코드 보기]** | 설정 | `true` |
| callers/calls 행 **클릭** | 설정 | 그대로 (열려 있으면 이동, 닫혀 있으면 조용히 겨냥만) |
| 뷰어 헤더 셰브런 | 그대로 | `false` |

즉 `reveal(t)`는 `setCodeTarget(t)`만, `openCode(t)`는 `setCodeTarget(t); setCodeOpen(true)`. 로직이 두 줄이고, "callers 항목 클릭 → 해당 파일:라인 이동"과 "더블클릭 → 뷰어가 열린다"를 둘 다 만족한다. caller 하나 눌렀다고 패널이 튀어나오지 않는다.

**더블클릭 배선 — 여기가 이 slice에서 가장 틀리기 쉬운 지점이다.**

`FileBox`의 행 `<g>`(1380-1386행)에는 이미 `onClick={() => onSelect(fn.id)}`가 있고, `pickRow`(579-585행)는 **이미 선택된 행을 다시 누르면 선택을 해제한다.** 더블클릭은 `click, click, dblclick` 순으로 발생하므로, 그대로 두면 첫 클릭이 선택하고 둘째 클릭이 곧바로 해제한 뒤 dblclick이 도착한다 — 선택이 풀린 채 뷰어만 열린다.

따라서:
```tsx
onClick={(event) => { if (event.detail > 1) return; onSelect(fn.id) }}
onDoubleClick={() => onOpenRow(fn)}
```
`event.detail > 1`인 클릭(연타의 두 번째 이후)은 무시한다. `onOpenRow`는 `FileBox`의 새 prop이며 `FunctionGraphSurface`에서
```ts
const openRow = useCallback((fn: GraphFunction) => {
  onSelect(fn.id)                                  // 더블클릭도 선택이다
  setCodeTarget({ path: fn.path, line: fn.lineno,
                  endLine: fn.endLineno || fn.lineno, label: fn.qualname })
  setCodeOpen(true)
}, [onSelect])
```
로 만든다. `pick`/`pickRow`가 그렇듯 `useCallback`으로 참조 동일성을 지켜야 `FileBox`의 `memo`(1281행)가 계속 유효하다 — 1435개 행이 매 렌더 재조정되면 그래프가 느려진다는 것이 그 memo의 존재 이유다.

**드래그 뒤의 더블클릭도 삼킨다.** 995-1015행 박스 래퍼의 `onClickCapture={swallowClick}` 옆에 `onDoubleClickCapture={swallowClick}`을 추가한다. 박스를 끌어다 놓은 뒤 발생하는 dblclick이 뷰어를 열어서는 안 된다.

**파일 레벨(lod === 'file')에서는 행이 없다** — 파일 박스의 더블클릭은 배선하지 않는다. 현재의 클릭=줌인(`pickFile`) 동작을 그대로 둔다.

**레이아웃.** 861행 `<div className="flex min-h-0 flex-1">` 안, 캔버스 컬럼(864행)과 Node 패널(1050행) 사이:
```tsx
{codeOpen ? (
  <div className="flex w-[34rem] min-w-0 shrink-0 flex-col border-l border-line bg-panel">
    <CodeViewer target={codeTarget} onClose={() => setCodeOpen(false)} />
  </div>
) : null}
```
접혔을 때 별도의 스트립은 두지 않는다 — Node 패널의 `[코드 보기]`가 다시 여는 길이고, 늘 보이는 세로 막대가 하나 더 생기면 캔버스만 좁아진다.

`FunctionDetail` 호출(1066행)에 `onReveal={reveal}` / `onOpenCode={openCode}` 추가.

#### 2.15 `desktop/src/renderer/src/styles/theme.css` (수정)

`@layer components` 끝에:
```css
  /* 코드 뷰어의 함수 범위. Monaco 데코레이션과 폴백이 같은 색을 쓴다. */
  .code-range { background: var(--color-accent-soft); }
  .code-range-gutter { border-left: 2px solid var(--color-accent); }

  /* body가 user-select: none 이고 Tailwind preflight가 * 에 box-sizing을 준다.
     Monaco는 자기 레이아웃을 직접 계산하므로 둘 다 그 안에서는 되돌린다. */
  .monaco-editor, .monaco-editor * { box-sizing: content-box; }
  .monaco-editor .view-lines { user-select: text; }
```

#### 2.16 `desktop/package.json` (수정)

`dependencies`에:
```json
    "monaco-editor": "0.52.2",
```
**정확한 버전으로 고정한다** (캐럿 아님). 이 환경에서는 설치도 검증도 불가능하므로, 나중에 누군가 설치할 때 서브패스가 §2.8과 다른 버전이 조용히 들어오는 것보다 고정이 낫다.

`devDependencies`가 아니라 `dependencies`인 이유: 렌더러 번들에 들어가고 electron-builder가 보는 목록이 이쪽이다 (`react-resizable-panels`와 같은 자리).

⚠ **`package-lock.json`은 손대지 않는다.** integrity 해시를 손으로 지어낼 수 없고, 틀린 항목은 `npm ci`를 지금보다 더 나쁘게 깨뜨린다. §7의 후속 조치 참조.

---

## 3. 데이터 흐름 (전체)

```
행 더블클릭 (FunctionGraphSurface)
  → openRow(fn)  ──▶ onSelect(fn.id)          [기존 선택 경로 — 엣지·마크·스크롤]
                 └─▶ setCodeTarget({path, line: fn.lineno, endLine: fn.endLineno})
                     setCodeOpen(true)
                        │
                        ▼
                  <CodeViewer target=...>
                        │  target.path 가 바뀌었나?
                        ├─ 예 ─▶ window.aidev.getSourceFile(path)
                        │          │  preload → IPC.sourceFile
                        │          ▼
                        │        register-handlers:
                        │          repoRoot() 있나 → pathInGraph(root, rel) → readSource(root, rel)
                        │          (셋 중 어디서 막혀도 SourceFile{ok:false, problem}) 
                        │          ▼
                        │        setSource(...) → monaco 모델 교체
                        └─ 아니오 ─▶ 모델 그대로
                        │
                        ▼
                  decorations.set(Range(line,1,endLine,1))
                  revealLineNearTop(line)

Node 패널 callers 행 클릭
  → onSelect(callerId)                         [기존]
  → onReveal({path: caller.path, line: caller.callLine, ...})
  → 뷰어가 열려 있으면 같은/다른 파일의 그 줄로 이동
```

---

## 4. 하지 않는 것 (요구사항이 배제한 것 + 이 계획이 배제하는 것)

- 편집·저장. `readOnly: true` + `domReadOnly: true`, 그리고 소스 쓰기 IPC는 없다. 저장소에 쓰는 능력은 여전히 `writeApproval` 하나뿐이다 (`ide.ts:296-301`).
- 멀티 탭 / 모델 캐시 LRU. 한 번에 모델 하나.
- 자체 검색 UI. Monaco 내장 find(Ctrl+F).
- graph 갱신 연동. 그래프가 재빌드되어도 열린 뷰어는 그대로 둔다.
- DEMO `code` 탭(`features/code/CodeSurface.tsx`) 대체. 범위 밖.
- 파일 레벨(줌 아웃) 박스의 더블클릭.
- `package-lock.json` 수정.

---

## 5. 검증

### 5.1 이 워크트리에서 실제로 돌릴 수 있는 것

```
python -m pytest              # 허용됨 (TEST_COMMAND_TOOLS)
```
Python 파일은 하나도 바뀌지 않으므로 통과해야 한다. **반드시 실행하고 실제 출력을 보고할 것.**

```
npx --prefix desktop tsc --noEmit -p desktop     # 선언된 test_command
```
실행은 하되, **이것이 통과했다고 "tsc 통과"라고 쓰지 말 것.** `desktop/tsconfig.json`은 `"files": []`인 솔루션 파일이고 `--build`가 없으므로 0개 파일을 검사한다. 그리고 `node_modules`가 없어 typescript 자체를 npx가 내려받아야 한다. 이 명령이 무엇을 검사하는지 그대로 보고하라.

### 5.2 돌릴 수 없는 것 (그리고 왜)

```
npm ci --prefix desktop && npm install --prefix desktop monaco-editor@0.52.2
npm test --prefix desktop        # = typecheck:node + typecheck:web + tsc -p tsconfig.test.json + node --test
```
`npm ci`·`npm install`에 대응하는 Bash 권한 규칙이 implement 단계에 없다 (§0 표). 시도해도 좋으나 **거부되면 거부되었다고 쓰라.** 통과했다고 주장하지 말 것.

### 5.3 사람이 해야 하는 확인 (구현 보고서에 이 목록을 그대로 남길 것)

```
npm install --prefix desktop            # 잠금 파일 갱신 포함
npm run dev --prefix desktop
```
1. Function graph 탭 → 검색창에 `run_pipeline` → Enter → 행 더블클릭
   → 코드 패널이 열리고 `aidev/pipeline.py`가 로드되며 **`run_pipeline`의 정의 줄로 스크롤 + 함수 범위 하이라이트**
   ⚠ 요구사항에 적힌 "3310행"은 옛 리비전 값이다. 현재 체크아웃에서 `run_pipeline`은 **`aidev/pipeline.py:4277`**이다. 확인해야 할 것은 특정 숫자가 아니라 **그래프 DB의 `functions.lineno`와 화면이 일치하는가**다.
2. Node 패널 `callers` 항목 클릭 → 같은 파일이면 그 줄로, 다른 파일이면 그 파일:줄로 이동
3. 다른 노드 더블클릭 → 같은 패널이 그 위치로 전환 (새 패널이 생기지 않음)
4. 셰브런으로 닫기 → `[코드 보기]`로 다시 열기
5. `aidev/pipeline.py`(5000행+) 로드 체감 — 첫 로드에 monaco 청크가 함께 오므로 가장 느리다
6. Ctrl+F로 내장 find 위젯 동작
7. **`npm run build --prefix desktop && npm run start --prefix desktop`** — 프로덕션(`file://`)에서 뷰어가 뜨는지. 여기가 워커 위험이 드러나는 유일한 지점이다 (§6-1)
8. 브라우저 콘솔에 CSP 위반·워커 오류가 없는지

### 5.4 새 단위 테스트

`language.test.ts`, `source-store.test.ts`, `graph-store.test.ts` 추가분은 위 §2.3/2.5/2.7대로 작성한다. 이 워크트리에서 실행할 수 없다(`tsc`가 없어 `out-test/`를 만들 수 없다). 작성은 하되, **실행하지 않았다고 명시할 것.**

---

## 6. 무엇이 잘못될 수 있는가

1. **워커 (가장 큰 위험).** 프로덕션 창은 `loadFile` → `file://`이고, Chromium은 그 origin에서 워커 생성을 차단한다. CSP에 `blob:`이 없어 monaco 기본 `getWorkerUrl`도 막힌다. §2.8의 구성(edcore.main + basic-languages + 워커를 부르는 옵션 전부 off)은 이를 피하기 위한 것이다. 그래도 무언가가 워커를 요구하면 → `ensureMonaco`가 `null`을 돌려주거나 에디터 생성이 throw → **§2.9의 `PlainBody` 폴백이 받는다.** 폴백이 이 위험의 유일한 보험이므로 생략하면 안 된다. 근본 해결(커스텀 프로토콜 등록 또는 `webPreferences` 조정)은 이 slice 범위 밖이다.

2. **`monaco-editor` 서브패스가 설치 버전에 없다.** `edcore.main` / `basic-languages/*/*.contribution` 경로는 monaco의 문서화된 진입점이지만 이 환경에서 검증할 수 없다. 완화: §2.8의 명시된 폴백(`import * as monaco from 'monaco-editor'`) + `try/catch` + `PlainBody`.

3. **`node_modules` 부재로 인한 검증 공백.** `tsc`·`node --test`·앱 실행 중 어느 것도 이 워크트리에서 불가능하다. 완화는 없다. 요구되는 것은 §5의 정직한 보고뿐이다.

4. **`package-lock.json` 불일치.** `package.json`에 `monaco-editor`를 넣지만 잠금 파일은 갱신하지 못한다. **`setup: npm ci --prefix desktop`을 쓰는 다음 slice는 `EUSAGE`(lock out of sync)로 실패한다** — 직전 slice `tasks/edge-style-fix.md:4`가 정확히 그 setup을 쓴다. §7의 후속 조치가 필요하다.

5. **더블클릭 vs 토글 선택.** `pickRow`(579-585행)가 같은 행 재클릭을 선택 해제로 쓰고 있어, 대책 없이는 더블클릭이 선택을 지운다. §2.14의 `event.detail > 1` 가드가 이를 막는다. 놓치기 쉬운 회귀다.

6. **`FileBox`의 `memo` 붕괴.** 새 `onOpenRow` prop을 `useCallback` 없이 인라인 화살표로 넘기면 105개 박스·1435개 행이 매 렌더 재조정된다. `FunctionGraphSurface.tsx:1258-1265` 주석이 경고하는 바로 그 성능 특성이다.

7. **Tailwind preflight × Monaco.** `*{box-sizing:border-box}`와 `body{user-select:none}`이 Monaco의 자체 레이아웃 계산과 충돌할 수 있다 (커서 위치 어긋남, 선택 불가). §2.15의 두 규칙이 1차 방어. 증상이 남으면 뷰어 컨테이너에 국한해 추가 조정.

8. **`revealLineNearTop`이 모델보다 먼저 호출된다.** 모델 교체와 데코레이션·스크롤은 반드시 같은 effect 안에서 순서대로 일어나야 한다. `endLine`은 `model.getLineCount()`로 클램프한다 — 그래프가 stale이면 `end_lineno`가 파일 길이를 넘을 수 있다.

9. **경로 철자.** 그래프 DB는 POSIX 구분자(`aidev/pipeline.py`)를 쓰고 Windows `path.resolve`는 `\`를 쓴다. `source-store`는 입력을 `/`로 정규화한 뒤 `join`해야 하며, `pathInGraph`의 SQL 비교는 **DB에 있는 철자 그대로**(정규화하지 않은 채) 해야 맞는다.

10. **`callersTruncated`.** `graph-store.ts:44` `MAX_CALLERS = 200`으로 잘린 호출부는 클릭할 수 없다. 기존 동작이고 화면이 `+N`으로 이미 말하고 있다. 범위 밖.

---

## 7. 후속 조치 (이 slice가 끝난 뒤 사람이 해야 할 일)

`package.json`과 `package-lock.json`이 어긋난 채 커밋되므로, 병합 직후 한 번:

```
npm install --prefix desktop        # lock 갱신 + monaco 설치
npm test --prefix desktop           # 진짜 typecheck + node --test
git add desktop/package-lock.json
```

그리고 이후 slice의 requirement front matter에는 `setup: npm ci --prefix desktop` / `test_commands: npm test --prefix desktop`를 쓰는 편이 낫다 — 이 slice가 `npx ... tsc -p desktop`으로 선언되어 있는 탓에 데스크톱 코드가 실질적으로 무검증으로 통과했다는 사실 자체가, 이 계획이 남기는 가장 중요한 관찰이다.
