# AI Dev IDE — Foundation Requirements v0.0.1

## 0. 문서 목적

이 문서는 기존 `ai-dev-orchestrator`를 장기적으로 **AI-native IDE**로 확장하기 위한 첫 번째 기반 작업 요구사항이다.

이번 단계의 목적은 완성된 IDE를 만드는 것이 아니다.

먼저 아래 기반을 안정적으로 만든다.

1. Electron + React + TypeScript 기반 IDE Shell
2. 기존 Python `aidev` 코어와 UI 계층의 명확한 분리
3. 향후 Code Graph / AI Workspace / Approval / Telemetry / Automation을 추가할 수 있는 구조
4. 패키지 매니저와 공식 scaffold를 우선 사용하여 불필요한 수작업·토큰 소비·구현 시간을 줄이는 개발 규칙 확립

---

# 1. 제품 방향

이 프로젝트는 단순한 `aidev` GUI가 아니다.

장기 목표는 다음과 같은 **AI 개발 IDE**다.

```text
Project
  ↓
Codebase Knowledge Graph
  ↓
Requirement
  ↓
Relevant Workspace / Scope
  ↓
Plan
  ↓
Approval
  ↓
Implementation
  ↓
Approval
  ↓
Tests
  ↓
Approval
  ↓
Browser Verification
  ↓
History / Graph Update
```

기존 IDE처럼 코드 파일 자체를 중심으로 모든 정보를 읽게 하지 않는다.

사람은 가능한 한:

- 프로젝트 구조
- 파일 관계
- 함수 관계
- 변경 영향
- 테스트 관계
- AI 작업 범위
- 작업 이력

을 **그래프와 구조화된 UI로 먼저 이해**한다.

필요한 경우에만 실제 코드를 열어본다.

---

# 2. 핵심 제품 원칙

## 2.1 Graph-first

IDE의 장기적인 중심 화면은 Code Graph다.

파일은 단순 파일명으로 끝나지 않고 내부 symbol까지 표현할 수 있어야 한다.

예:

```text
gameSessionService.js
├─ createGameSession()
├─ advanceNightTurn()
├─ resolveNight()
└─ checkVictoryCondition()
```

향후 관계도 표현한다.

```text
submitNightAction()
        │
        ▼
advanceNightTurn()
        │
        ▼
resolveNight()
   ├─ resolveJokerAttack()
   ├─ resolveDoctorProtection()
   └─ checkVictoryCondition()
```

---

## 2.2 Human cognitive debt reduction

AI가 작성하는 코드의 양이 늘어날수록 사람이 프로젝트 전체를 직접 기억하기 어려워진다.

따라서 IDE는 사람이 기억해야 할 정보를 외부화해야 한다.

예:

- 이 함수는 어디에 정의되어 있는가?
- 어디에서 호출되는가?
- 어느 파일에서 재사용되는가?
- 어떤 state를 읽고 쓰는가?
- 어떤 테스트가 이 함수를 검증하는가?
- 어떤 Slice가 이 함수를 만들거나 수정했는가?
- 왜 이 코드가 존재하는가?

장기적으로 이 정보를 Codebase Knowledge Graph가 보존한다.

---

## 2.3 AI workspace restriction

Code Graph는 단순 시각화 기능으로 끝나지 않는다.

요구사항을 입력하면 관련 subgraph를 계산하여 AI가 우선 탐색하거나 수정할 작업 공간을 제한하는 기반으로 사용한다.

장기 목표:

```text
Requirement
   ↓
Relevant Graph
   ↓
Workspace
   ↓
Allowed files / symbols
   ↓
Context Capsule
   ↓
AI Agent
```

이를 통해:

- 불필요한 repo 전체 탐색 감소
- Read / Grep / Glob 반복 감소
- token/context 소비 감소
- out-of-scope 수정 감소
- 작업 범위 검증 가능

을 목표로 한다.

---

# 3. 기존 시스템 보존 원칙

현재 존재하는 Python 기반 `aidev` 기능을 버리거나 Electron으로 재작성하지 않는다.

기존 코어는 장기적으로 IDE의 실행 엔진이 된다.

개념 구조:

```text
AI DEV IDE
│
├─ Desktop UI
│  └─ Electron / React / TypeScript
│
└─ AI Dev Core
   └─ Python
      ├─ Runner
      ├─ Telemetry
      ├─ Watcher data
      ├─ Planner
      ├─ Supervisor
      └─ future Code Indexer
```

CLI도 계속 지원한다.

```text
AI Dev Core
   ├─ CLI
   └─ IDE
```

IDE 구현을 위해 기존 CLI 기능을 불필요하게 깨거나 복제하지 않는다.

---

# 4. CRITICAL — Scaffold / Package Manager 우선 원칙

## 4.1 보일러플레이트를 수작업으로 작성하지 않는다

AI 에이전트는 프로젝트 생성 시 먼저 해당 기술 스택에 **공식 또는 검증된 scaffold / generator / CLI가 있는지 확인**해야 한다.

공식 generator로 만들 수 있는 다음 항목을 AI가 직접 수십 개 파일로 타이핑하지 않는다.

예:

- `package.json`
- TypeScript 기본 설정
- Vite 설정
- Electron main/preload/renderer 기본 구조
- React entry point
- 기본 tsconfig 계열
- 빌드 스크립트
- 일반적인 scaffold 폴더 구조

즉 다음 패턴을 피한다.

```text
mkdir desktop
mkdir desktop/src
mkdir desktop/src/main
mkdir desktop/src/preload
mkdir desktop/src/renderer

Write package.json
Write tsconfig.json
Write vite config
Write main.ts
Write preload.ts
Write index.html
...
```

해당 ecosystem이 이미 generator를 제공한다면 위 작업은 금지한다.

---

## 4.2 현재 Electron UI bootstrap

현재 UI 기반은 다음을 우선 검토한다.

```text
Electron
React
TypeScript
Vite
```

electron-vite의 공식 scaffold가 사용 가능한 경우 우선 사용한다.

현재 기준 scaffold 예:

```powershell
npm create @quick-start/electron@latest
```

React + TypeScript template을 선택한다.

직접 template option을 사용할 경우 **실행 전에 현재 CLI help 또는 공식 문서를 확인한 뒤** 사용한다.

AI는 기억에 의존하여 오래된 package name이나 option을 추측하지 않는다.

---

## 4.3 Dependency 설치 원칙

라이브러리가 필요한 경우:

1. 기존 dependency 확인
2. package manager로 설치 가능한지 확인
3. 공식 패키지가 있으면 package manager 사용
4. 라이브러리가 해결하는 코드를 직접 재구현하지 않음
5. 설치 후 package manifest / lockfile 변경을 확인
6. 필요한 dependency만 추가

예:

```powershell
npm install <package>
npm install -D <package>
```

금지 예:

- 라이브러리가 이미 제공하는 parser / graph layout / editor 기능을 임의 구현
- npm 패키지 코드를 프로젝트 내부에 복붙
- scaffold가 생성하는 설정 파일을 AI가 처음부터 재작성
- 이유 없이 dependency를 대량 설치

---

## 4.4 Scaffold 이후 수정

Scaffold는 시작점일 뿐이다.

생성 후 반드시:

1. 생성 결과 구조 확인
2. 불필요한 demo 코드 식별
3. 필요한 최소 변경만 수행
4. 생성된 구조를 이유 없이 전면 재배치하지 않음

즉:

```text
scaffold
  ↓
inspect
  ↓
minimal cleanup
  ↓
project-specific implementation
```

순서를 따른다.

---

# 5. 기술 스택 — Foundation

이번 Foundation의 기본 방향:

```text
Desktop
├─ Electron
├─ React
├─ TypeScript
└─ Vite / electron-vite

Core
└─ Python
```

이번 단계에서는 다음 기술을 미리 대량 설치하지 않는다.

```text
React Flow
Cytoscape.js
Monaco
xterm.js
graph database
new state-management framework
new backend framework
```

실제 vertical slice에서 필요해질 때 추가한다.

---

# 6. 목표 프로젝트 구조

최종 경로 이름은 실제 scaffold 결과를 우선 존중한다.

아래 구조는 **개념적 목표 구조**이며, scaffold가 제공하는 합리적인 구조를 억지로 다시 만들지 않는다.

```text
ai-dev-orchestrator/
│
├─ src/                         # 기존 Python core
├─ tests/                       # 기존 Python tests
├─ data/
├─ tasks/
│
├─ desktop/                     # AI Dev IDE desktop client
│  │
│  ├─ src/
│  │  ├─ main/                  # Electron main process
│  │  ├─ preload/               # controlled native bridge
│  │  └─ renderer/              # React IDE UI
│  │
│  ├─ package.json
│  └─ ...
│
└─ ...
```

Renderer 내부는 기능이 생길 때 점진적으로 다음 방향으로 확장한다.

```text
renderer/
├─ components/
├─ features/
│  ├─ explorer/
│  ├─ graph/
│  ├─ editor/
│  ├─ workflow/
│  └─ telemetry/
├─ types/
└─ App
```

빈 폴더를 미래를 위해 대량으로 미리 만들지 않는다.

실제로 기능을 구현할 때 생성한다.

---

# 7. IDE Shell 기본 구조

첫 IDE shell은 다음 영역을 수용할 수 있어야 한다.

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Bar                                                      │
├──────────┬──────────────────────────────────┬────────────────┤
│          │                                  │                │
│ Activity │          Main Workspace          │   Inspector    │
│ /        │                                  │                │
│ Explorer │          Graph / Code            │                │
│          │                                  │                │
├──────────┴──────────────────────────────────┴────────────────┤
│ Bottom Panel                                                 │
└──────────────────────────────────────────────────────────────┘
```

장기적인 Main Workspace 기본 방향은 Graph-first다.

하지만 v0.0.1에서 복잡한 실제 Code Graph 구현까지 한 번에 진행하지 않는다.

---

# 8. Electron security boundary

Renderer가 OS 권한을 직접 가져서는 안 된다.

기본 경계:

```text
React Renderer
      │
      ▼
Electron Preload
      │
      ▼
contextBridge
      │
      ▼
Electron Main
      │
      ▼
Filesystem / Python Core / OS
```

금지:

```text
renderer → unrestricted fs
renderer → unrestricted child_process
renderer → arbitrary shell execution
```

Preload API는 capability 기반으로 좁게 설계한다.

좋은 예:

```text
getProjectTree()
getRunStatus()
getTelemetrySnapshot()
openProject()
```

피해야 할 예:

```text
exec(anyCommand)
runShell(anyString)
readAnyFile(anyPath)
```

---

# 9. v0.0.1 — Foundation 목표

첫 milestone은 **AI Dev IDE Shell**이다.

이번 slice에서 반드시 완성할 것:

- Electron application이 개발 모드에서 실행된다.
- React + TypeScript renderer가 정상 렌더링된다.
- Electron main / preload / renderer 경계가 존재한다.
- IDE shell 레이아웃이 존재한다.
- 기존 Python core는 동작을 유지한다.
- 기존 Python 테스트에 불필요한 regression을 만들지 않는다.
- desktop 영역이 기존 Python package 구조와 강하게 결합되지 않는다.

이번 slice에서 하지 않을 것:

- 완전한 Code Graph
- 함수 CALL graph
- AI requirement 분석
- AI workspace restriction
- Planner UI
- Auto runner
- Session quota supervisor UI
- Monaco editor
- terminal emulator
- graph database
- Git client 전체 구현
- debugger
- extension system

---

# 10. v0.0.1 화면 placeholder

초기 화면은 기능보다 shell 구조 확인이 목적이다.

```text
┌──────────────────────────────────────────────────────────────┐
│ AI DEV IDE                                                  │
├──────────┬──────────────────────────────────┬────────────────┤
│ PROJECT  │                                  │ INSPECTOR      │
│          │          KNOWLEDGE GRAPH         │                │
│ No       │                                  │ No selection   │
│ project  │          No project open         │                │
│          │                                  │                │
├──────────┴──────────────────────────────────┴────────────────┤
│ No active run                                               │
└──────────────────────────────────────────────────────────────┘
```

과도한 디자인 작업은 하지 않는다.

먼저 구조와 실행을 검증한다.

---


# 10.1 Foundation에서 기초 UI까지 완료한다

`v0.0.1`은 Electron 창만 띄우거나 단순 placeholder 레이아웃만 만든 상태에서 종료하지 않는다.

이번 Foundation 단계에서는 향후 기능을 붙일 수 있는 **기초 IDE UI를 실제 사용 가능한 수준까지 마무리**한다.

최소 범위:

- Top Bar
- Activity Bar
- Project / Explorer Sidebar
- Main Workspace
- Inspector Panel
- Bottom Status / Run Panel
- Graph / Code 등 향후 workspace 전환을 위한 기본 tab 영역
- 선택 상태 / hover / focus 등 기본 interaction
- 비어 있는 상태(empty state)
- 기본 dark theme
- 패널 간 시각적 hierarchy
- 창 크기 변경 시 기본 레이아웃이 무너지지 않는 수준의 대응
- 장시간 봐도 과도하게 복잡하지 않은 개발 도구형 UI

이번 단계에서 실제 Code Graph 기능이 완성될 필요는 없다.

그러나 Main Workspace에는 향후 Graph가 들어갈 영역이 명확하게 존재해야 하며, mock data 또는 placeholder node를 사용해 **최종 IDE의 시각적 방향을 확인할 수 있어야 한다.**

예:

```text
┌────────────────────────────────────────────────────────────────┐
│ AI DEV IDE                    Project: --              IDLE     │
├────┬──────────────────┬────────────────────────┬────────────────┤
│    │                  │                        │                │
│ A  │ PROJECT          │      WORKSPACE         │   INSPECTOR    │
│ C  │                  │                        │                │
│ T  │ No project open  │    Graph placeholder   │ No selection   │
│ I  │                  │                        │                │
│ V  │                  │                        │                │
│ I  │                  │                        │                │
│ T  │                  │                        │                │
│ Y  │                  │                        │                │
├────┴──────────────────┴────────────────────────┴────────────────┤
│ STATUS / RUN / TELEMETRY                                       │
└────────────────────────────────────────────────────────────────┘
```

UI Foundation 완료의 기준은:

> "앱이 실행된다"가 아니라  
> "AI Dev IDE의 기본 화면과 정보 구조가 이미 보이며, 이후 기능을 이 shell 안에 하나씩 연결할 수 있다."

이다.

디자인을 완성품 수준으로 polish할 필요는 없지만, 임시 개발 페이지처럼 보이는 상태로 Foundation을 종료하지 않는다.



# 10.2 UI Visual Direction — AI 스타일 배제, 모던한 개발 도구 지향

AI Dev IDE라는 이유만으로 흔히 보이는 **AI 서비스 특유의 시각적 스타일을 사용하지 않는다.**

이 제품의 UI는 "AI 챗봇", "AI SaaS 대시보드", "미래적인 AI 콘솔"처럼 보여서는 안 된다.

목표는 다음에 가깝다.

> 모던하고 절제된 전문 개발 도구  
> 오래 사용해도 피로하지 않은 IDE  
> 정보 밀도가 높지만 한눈에 구조를 파악할 수 있는 인터페이스

참고하는 방향의 성격:

- VS Code / JetBrains 계열 개발 도구의 정보 밀도
- Obsidian의 차분한 graph/workspace 감각
- Linear류 제품의 절제된 현대적 UI
- 기능이 시각 효과보다 우선되는 professional tool

특정 제품의 디자인을 그대로 복제하지 않는다.

## 피해야 할 AI 스타일

다음 표현을 기본 디자인 언어로 사용하지 않는다.

- 보라색/파란색 중심의 과도한 AI gradient
- neon glow
- glassmorphism 남발
- 빛나는 orb / 별 / sparkles
- AI assistant 캐릭터 또는 로봇 이미지
- 채팅 bubble 중심의 화면 구성
- 모든 요소를 큰 rounded card 안에 넣는 SaaS dashboard 스타일
- 의미 없이 큰 hero 영역
- 과도한 그림자와 blur
- "AI가 생각 중"을 강조하는 장식성 animation
- 미래적/사이버펑크 콘솔 연출
- 불필요한 emoji 기반 navigation
- 정보보다 장식을 우선하는 화면

AI 기능은 제품의 기능이지 시각적 테마가 아니다.

## 기본 Visual Language

기본 방향:

```text
dark
neutral
clean
dense
calm
precise
modern
professional
```

권장:

- neutral dark background
- 낮은 대비의 panel separator
- 명확한 typography hierarchy
- 작은 radius 또는 필요한 곳에만 radius 사용
- 얇고 절제된 border
- accent color는 선택/실행/경고 등 의미가 있을 때만 사용
- 넓은 빈 공간보다는 개발 도구에 맞는 적절한 정보 밀도
- 상태는 색상만이 아니라 icon / label / shape도 함께 사용
- animation은 상태 변화 이해에 도움이 되는 경우에만 사용

## Graph UI

Graph는 제품의 핵심이므로 장식적인 "AI network"처럼 표현하지 않는다.

좋은 방향:

```text
File
 ├─ Function
 ├─ Function
 └─ Class

Function ──calls──▶ Function
Function ──tested by──▶ Test
```

그래프는 다음을 우선한다.

1. 읽기 쉬움
2. 관계 파악
3. 현재 작업 범위 식별
4. 변경 영향 식별
5. drill-down / filtering
6. 전체 → 부분 이동

배경에 무수한 점과 선을 장식적으로 흩뿌리는 식의 그래프는 피한다.

노드 수가 많을 경우 모든 것을 동시에 보여주지 않고, 현재 context에 필요한 subgraph를 우선 보여준다.

## AI Interaction UI

AI와 상호작용하더라도 IDE 전체를 Chat UI로 만들지 않는다.

예:

```text
Requirement
Plan
Approval
Implementation
Tests
```

는 각각 구조화된 workspace / panel / inspector로 표현한다.

자유 대화가 필요한 경우에만 별도의 assistant/chat 영역을 사용할 수 있다.

즉:

```text
AI = 기능
Chat = 필요한 하나의 인터페이스
IDE 전체 = Chat UI가 아님
```

## Foundation UI 완료 기준에 디자인 방향 포함

Foundation UI는 다음 조건도 충족해야 한다.

- [ ] 흔한 AI SaaS/챗봇 스타일을 기본 디자인으로 사용하지 않는다.
- [ ] 과도한 gradient / glow / glass effect를 사용하지 않는다.
- [ ] 개발 도구에 적합한 neutral dark theme을 기본으로 한다.
- [ ] panel과 workspace의 hierarchy가 명확하다.
- [ ] 장식보다 정보 구조와 가독성을 우선한다.
- [ ] Graph placeholder 역시 장식적인 AI network가 아니라 실제 code relationship UI의 방향을 보여준다.
- [ ] 장시간 사용하는 IDE라는 전제로 시각적 피로도를 낮춘다.
- [ ] 최종적으로 "AI 앱"보다 "현대적인 전문 IDE"라는 인상을 우선한다.



# 10.3 Product UX Direction — Orca ADE와 같은 Agent-first IDE 구조

제품의 기본 사용 경험은 전통적인 코드 편집기 중심 IDE보다 **Orca ADE와 같은 agent-first 개발 환경**에 가깝게 설계한다.

중요:

> Orca의 UI나 코드를 복제하지 않는다.  
> 참고하는 것은 "agent를 개발 작업의 1급 객체로 다루는 제품 구조"다.

전통적인 IDE:

```text
File
  ↓
Editor
  ↓
Human edits code
  ↓
Run / Test
```

AI Dev IDE:

```text
Project
  ↓
Requirement / Task
  ↓
Workspace
  ↓
Agent Session
  ↓
Plan / Implement / Test / Browser
  ↓
Review / Approval
  ↓
Code Graph / History update
```

## Agent-first Shell

IDE에서 다음 객체를 1급 UI 요소로 다룬다.

- Project
- Workspace
- Requirement
- Epic
- Slice
- Agent Session
- Run
- Approval
- Test
- Browser Test
- Code Graph
- Change
- History

단순히 왼쪽에는 파일 탐색기, 중앙에는 코드 에디터만 배치하는 기존 IDE 구조에 종속되지 않는다.

예시 방향:

```text
┌────────────────────────────────────────────────────────────────────┐
│ AI DEV IDE         Project: joker        Agent: Claude     RUNNING │
├──────┬──────────────────┬──────────────────────────┬───────────────┤
│      │ WORKSPACES       │                          │ INSPECTOR     │
│ ACT  │                  │     MAIN WORKSPACE       │               │
│      │ Night Role       │                          │ Current Slice │
│      │ Doctor           │     Graph / Diff /       │ Scope         │
│      │ Guard            │     Code / Browser       │ Tests         │
│      │                  │                          │ Approval      │
├──────┴──────────────────┴──────────────────────────┴───────────────┤
│ TERMINAL / AGENT / TEST / TELEMETRY / SESSION                     │
└────────────────────────────────────────────────────────────────────┘
```

## Terminal-first execution

실제 agent 실행의 기본 경로는 계속 CLI를 유지한다.

예:

```text
Claude Code
Codex
OpenCode
other CLI agents
```

IDE는 agent의 내부 구현을 대체하지 않는다.

대신:

```text
IDE
 ↓
Workspace / Scope / Context 준비
 ↓
CLI Agent 실행
 ↓
실행 상태 관찰
 ↓
결과 수집
 ↓
변경 / 테스트 / telemetry / graph 반영
```

구조를 제공한다.

즉 UI 때문에 기존 CLI workflow를 제거하거나 감추지 않는다.

## Bring Your Own Agent 방향

장기적으로 특정 AI agent 하나에 종속되지 않는 구조를 지향한다.

개념적으로:

```text
AgentAdapter
├─ Claude Code
├─ Codex
├─ OpenCode
└─ future CLI agent
```

단, Foundation 단계에서 multi-agent abstraction을 미리 구현하지 않는다.

현재 실제로 사용하는 Claude Code runner를 먼저 유지하고, 두 번째 agent를 실제 지원할 시점에 공통 adapter를 추출한다.

## Workspace-centric

Orca류 agent-first IDE에서 참고할 가장 중요한 개념은 **agent가 작업하는 workspace를 명확한 단위로 보여주는 것**이다.

AI Dev IDE에서는 이를 더 확장한다.

Workspace는 단순 Git worktree나 폴더만 의미하지 않는다.

```text
AI Workspace
├─ Requirement
├─ Current Slice
├─ Allowed Files
├─ Allowed Symbols
├─ Relevant Code Graph
├─ Context Capsule
├─ Agent Session
├─ Changes
├─ Tests
└─ Approval State
```

즉 workspace 자체가 AI 작업의 경계이자 사람이 검토하는 단위가 된다.

## Orca와의 핵심 차별점

Agent orchestration만으로 제품을 정의하지 않는다.

AI Dev IDE의 핵심 차별점은 다음 연결이다.

```text
                CODEBASE KNOWLEDGE GRAPH
                         │
            ┌────────────┼────────────┐
            │            │            │
            ▼            ▼            ▼
       Human View    AI Context    Scope Guard
            │            │            │
            └────────────┼────────────┘
                         ▼
                    Agent Runner
                         │
                         ▼
               Change / Test / History
                         │
                         └──────▶ Graph Update
```

Code Graph는 다음 세 가지 역할을 동시에 가진다.

1. 사람이 프로젝트를 그림으로 이해한다.
2. AI에게 필요한 context만 제공한다.
3. AI의 작업 범위를 파일/symbol 수준으로 제한한다.

따라서 이 제품은 단순한 agent launcher나 terminal manager가 아니다.

## UI에서 코드의 위치

코드 에디터는 중요한 기능이지만 메인 인터페이스의 유일한 중심이 아니다.

기본 탐색 순서:

```text
Requirement
   ↓
Workspace Graph
   ↓
Change / Impact
   ↓
Relevant Symbol
   ↓
Code
```

사람이 처음부터 전체 코드 파일을 읽도록 강제하지 않는다.

코드는 필요할 때 drill-down해서 확인한다.

## 기초 UI에서 반영할 것

Foundation UI부터 다음 방향이 느껴져야 한다.

- [ ] Project만 아니라 Workspace/Task 영역이 존재한다.
- [ ] 현재 Agent/Run 상태를 상단 또는 주요 영역에서 확인할 수 있다.
- [ ] Main Workspace는 코드 에디터만을 전제로 설계하지 않는다.
- [ ] Graph / Diff / Code / Browser 같은 서로 다른 작업 surface가 들어갈 수 있다.
- [ ] Bottom Panel은 Terminal / Agent / Test / Telemetry로 확장 가능한 구조다.
- [ ] Approval 상태를 향후 자연스럽게 추가할 수 있는 정보 구조다.
- [ ] UI는 agent-first이지만 ChatGPT 스타일의 채팅 앱처럼 만들지 않는다.
- [ ] 실제 실행은 CLI-first 원칙을 유지한다.

## 한 줄 제품 방향

> Orca처럼 agent를 중심으로 작업을 운용하되,  
> Code Graph를 사람의 시각적 기억이자 AI의 context/scope engine으로 사용하는 AI-native IDE를 만든다.



# 10.4 UI Priority — 이 IDE의 핵심 제품은 시각적 작업 환경이다

이 프로젝트에서 UI는 단순히 기존 CLI 기능을 감싸는 보조 화면이 아니다.

**사용자가 실제로 매일 바라보고 판단하는 주 작업 환경 자체가 제품의 핵심이다.**

따라서 Foundation 단계부터 UI 품질과 정보 구조를 가장 중요한 요구사항 중 하나로 취급한다.

제품이 해결하려는 핵심 경험은 다음과 같다.

```text
여러 작업을 동시에 펼쳐본다
        │
        ├─ Agent / Terminal
        ├─ Code / Test Editing
        ├─ Diff
        ├─ Live Token / Context
        ├─ Test Result
        └─ Code Graph
        │
        ▼
사람이 긴 코드와 로그를 전부 읽지 않고도
현재 작업 상태와 변경 영향을 빠르게 이해한다
```

## 10.4.1 Multi-pane / Multi-workspace IDE

Orca 계열의 agent-first 개발 환경처럼 하나의 고정된 중앙 에디터만 사용하는 구조를 피한다.

사용자는 동시에 여러 종류의 작업 surface를 열어둘 수 있어야 한다.

예:

```text
┌───────────────────────────────────────────────────────────────────────┐
│ Project / Workspace tabs                         Agent / Token Status  │
├───────────────────────────────┬───────────────────────────────────────┤
│                               │                                       │
│         CODE / TEST           │              DIFF                     │
│                               │                                       │
├───────────────────────────────┼───────────────────────────────────────┤
│                               │                                       │
│          GRAPH VIEW           │       AGENT / TERMINAL / TEST         │
│                               │                                       │
└───────────────────────────────┴───────────────────────────────────────┘
```

UI 구조는 장기적으로 다음을 지원할 수 있어야 한다.

- 여러 workspace/tab
- split view
- dockable panel
- panel resize
- 동일 프로젝트의 서로 다른 정보 동시 표시
- Code와 Diff 동시 비교
- Graph와 Inspector 동시 표시
- Test와 Terminal 동시 표시
- Agent 실행 상태와 Token 상태를 작업 중 계속 확인

Foundation에서 완전한 window manager를 구현할 필요는 없다.

하지만 레이아웃은 향후 이러한 구조로 확장할 수 있어야 하며,
**하나의 거대한 고정 페이지 형태로 만들지 않는다.**

---

## 10.4.2 Code / Test Editing Surface

이 IDE에서는 코드 자체를 가능한 적게 읽도록 돕지만,
필요할 때는 바로 코드와 테스트를 편집할 수 있어야 한다.

따라서 장기 UI에는 다음 surface가 존재한다.

```text
CODE
TEST
DIFF
GRAPH
BROWSER
```

특히 테스트는 부가적인 로그가 아니라 1급 작업 surface로 취급한다.

예:

```text
[Code] [Test] [Diff] [Graph] [Browser]

resolveNight()
──────────────────────────────────────

관련 테스트
✓ doctorProtection.test.js
✓ nightResolution.test.js

[테스트 열기]
```

Foundation에서는 실제 Monaco 통합까지 완료하지 않아도 되지만,
**Code/Test Editor가 들어갈 workspace 구조를 처음부터 고려한다.**

---

## 10.4.3 Diff는 한눈에 이해할 수 있어야 한다

Diff는 단순히 `git diff` 원문을 길게 출력하는 영역이 아니다.

사용자가 구현 승인 여부를 빠르게 판단할 수 있도록 다음 구조를 지향한다.

```text
CHANGE SUMMARY

4 files changed
6 functions modified
2 functions added
7 tests added

Affected
├─ NIGHT resolution
├─ Doctor protection
└─ game_state_updated

─────────────────────────────────────

Files
gameSessionService.js        +42 -8
nightActionService.js        +18 -2
doctorProtection.test.js     +74
nightResolution.test.js      +22 -3

─────────────────────────────────────

[Semantic] [Side-by-side Code Diff]
```

즉 기본적으로:

1. 변경 요약
2. 영향 범위
3. 변경 파일 / symbol
4. 실제 code diff

순서로 내려간다.

사람이 처음부터 수백 줄 diff를 읽도록 만들지 않는다.

---

## 10.4.4 Live Token / Context는 항상 접근 가능해야 한다

실시간 token/context 사용량은 숨겨진 설정 메뉴나 별도 report 화면에만 존재해서는 안 된다.

Agent가 실행 중일 때 사용자는 최소한 다음 상태를 즉시 확인할 수 있어야 한다.

```text
Claude              RUNNING

5h usage            61%
Context             31.2k
Turns               8
Cache read          171k
Output              2.9k
Cost                $0.xx
```

세부 telemetry 화면에서는:

- input
- cache creation
- cache read
- output
- peak context
- current turn
- current tool
- current target
- repeated reads
- estimated attribution

등을 볼 수 있다.

하지만 기본 화면에는 필요한 핵심 숫자만 보여주어
UI가 숫자로 과밀해지지 않도록 한다.

실시간 token 정보는 **AI 작업의 비용과 context 상태를 사람이 직관적으로 인지하기 위한 UI**다.

---

## 10.4.5 Graph View는 인지 부채 감소를 위한 메인 인터페이스다

Graph View는 시각적 장식이나 별도의 분석 도구가 아니다.

이 IDE가 일반적인 agent launcher와 구분되는 핵심 UI다.

기본 개념:

```text
Project
  │
  ├─ File
  │    ├─ Function
  │    ├─ Function
  │    └─ Class
  │
  └─ File
       └─ Function
```

관계가 존재하면:

```text
gameSocket.js
     │
     ▼
submitNightAction()
     │
     ▼
resolveNight()
  ┌──┴────────────┐
  ▼               ▼
Doctor           Guard
  │               │
  └──────┬────────┘
         ▼
nightResolution.test.js
```

사용자는 Graph를 통해 먼저 다음을 이해한다.

- 어떤 파일들이 연결되어 있는가
- 어떤 함수들이 호출되는가
- 특정 함수가 어디에서 재사용되는가
- 어떤 테스트와 연결되는가
- 현재 변경 범위가 어디인가
- 현재 AI workspace가 어디까지인가

그 후 필요할 때만 실제 코드로 drill-down한다.

기본 UX:

```text
Graph
  ↓
Node 선택
  ↓
Inspector
  ↓
References / Tests / Changes 확인
  ↓
필요할 때 Code 열기
```

즉:

> 코드를 먼저 읽고 구조를 머릿속에서 만드는 것이 아니라  
> 구조를 먼저 보고 필요한 코드만 읽는다.

Graph View의 목적은 **human cognitive debt를 줄이는 것**이다.

---

## 10.4.6 UI Information Hierarchy

사용자가 한눈에 먼저 봐야 하는 순서는 대략 다음과 같다.

```text
1. 지금 어떤 프로젝트 / workspace인가
2. AI가 지금 무엇을 하고 있는가
3. 현재 Token / Context 상태는 어떤가
4. 어떤 변경이 발생했는가
5. 테스트가 통과했는가
6. 코드 구조상 어디가 영향을 받았는가
7. 필요하면 실제 코드는 무엇인가
```

따라서 긴 코드, 긴 로그, 긴 AI 응답을 첫 화면의 중심에 놓지 않는다.

---

## 10.4.7 Foundation UI에서 실제로 끝낼 범위

Foundation 단계의 UI는 단순 wireframe이나 scaffold 화면에서 끝내지 않는다.

최소한 다음 surface가 **실제 IDE shell 안에서 시각적으로 구분되어 있어야 한다.**

- Project / Workspace navigation
- Main Workspace
- Code/Test용 workspace tab 자리
- Diff view 자리
- Graph View 자리
- Inspector
- Agent / Terminal / Test bottom panel
- Token / Context status 영역
- Run 상태 표시

실제 데이터 연동이 아직 없는 영역은 mock 또는 empty state를 사용할 수 있다.

하지만 전체 화면을 보았을 때 다음 제품 정의가 즉시 이해되어야 한다.

> "여러 AI 개발 작업을 한 화면에서 운영하고,  
> 변경사항과 테스트를 빠르게 검토하고,  
> 실시간 token/context를 확인하며,  
> Code Graph를 통해 프로젝트 구조를 시각적으로 이해하는 IDE."

---

## 10.4.8 UI 품질을 최우선으로 검토한다

Foundation 구현에서 기능이 동작한다는 이유만으로 UI가 조잡한 상태를 허용하지 않는다.

특히 확인할 것:

- panel 비율
- 정보 밀도
- typography hierarchy
- spacing
- selected state
- hover state
- divider
- resize affordance
- tab 구조
- empty state
- loading state
- long filename 처리
- 긴 path 처리
- 긴 function name 처리
- 작은 창 크기
- 큰 모니터
- visual noise
- 반복되는 card 남용 여부
- 불필요한 AI 스타일 여부

UI 리뷰는 기능 리뷰와 별도로 수행할 가치가 있는 주요 품질 gate로 취급한다.

---

## 10.4.9 Foundation UI Done Criteria

- [ ] IDE가 단일 코드 에디터 화면처럼 보이지 않는다.
- [ ] 여러 작업 surface를 동시에 수용할 수 있는 multi-pane 구조다.
- [ ] Project / Workspace navigation이 명확하다.
- [ ] Code/Test/Diff/Graph surface의 위치와 전환 구조를 이해할 수 있다.
- [ ] Agent / Terminal / Test용 bottom panel이 존재한다.
- [ ] 실시간 Token / Context 상태가 들어갈 명확한 영역이 존재한다.
- [ ] Diff를 빠르게 검토하기 위한 독립 surface가 존재한다.
- [ ] Graph View가 메인 기능 중 하나로 시각적으로 강조되어 있다.
- [ ] Inspector를 통해 선택한 파일/function/node의 정보를 표시할 수 있다.
- [ ] 사용자가 긴 코드부터 읽지 않아도 전체 구조를 파악할 수 있는 정보 hierarchy다.
- [ ] 기본 dark theme이 전문 개발 도구처럼 절제되어 있다.
- [ ] 흔한 AI SaaS 스타일보다 modern professional IDE의 인상을 준다.
- [ ] placeholder를 사용하더라도 전체 제품의 사용 방식이 화면만 보고 이해된다.


# 11. 이후 예정 vertical slices

## v0.0.2 — Project Open

목표:

- 실제 로컬 프로젝트 선택
- 프로젝트 root 인식
- directory / file tree 표시

---

## v0.0.3 — Symbol Index v1

목표:

- JS / TS / Python 파일에서 최소한의 symbol 추출
- function / class 표시

예:

```text
gameSessionService.js
├─ resolveNight()
└─ advanceNightTurn()
```

---

## v0.0.4 — Graph v1

목표:

```text
File
  ↓ DEFINES
Function
```

실제 project data로 최소 Code Graph 표시.

---

## v0.0.5 — Symbol Inspector

Graph 또는 Explorer에서 함수를 선택했을 때:

```text
Name
Type
File
Start line
End line
```

등을 표시.

---

## v0.0.6 — Code View

선택한 symbol에서 실제 코드 영역을 열어 필요한 코드만 확인할 수 있게 한다.

이 단계에서 Monaco 같은 기존 editor component 도입을 검토한다.

직접 editor engine을 만들지 않는다.

---

# 12. AI Agent 구현 규칙

이 문서를 받아 구현하는 AI agent는 다음을 반드시 따른다.

## 12.1 먼저 탐색

코드 변경 전에:

1. 현재 repo 구조 확인
2. 기존 Python project 구조 확인
3. root package manager 상태 확인
4. 이미 Electron/Node 관련 파일이 존재하는지 확인
5. 기존 변경사항이 있는지 확인

추측해서 덮어쓰지 않는다.

---

## 12.2 생성기 우선

새 framework/project 영역 생성 전:

```text
"이 ecosystem에 공식 scaffold / generator가 있는가?"
```

를 먼저 확인한다.

있으면 그것을 사용한다.

AI가 직접 boilerplate 파일을 하나씩 만드는 것은 **예외 상황**이다.

예외가 필요하면 이유를 먼저 기록한다.

---

## 12.3 CLI 우선 실행

사용자가 실제 작업은 터미널 CLI 중심으로 진행한다.

따라서 가능한 작업은 재현 가능한 CLI 명령으로 수행한다.

예:

```text
npm create ...
npm install ...
npm run ...
pytest ...
```

IDE의 버튼을 만들기 위해 기존 CLI 경로를 제거하지 않는다.

---

## 12.4 설치와 구현을 구분

패키지 설치로 해결되는 작업과 프로젝트 고유 구현을 구분한다.

```text
framework/bootstrap → scaffold/package manager
generic capability  → proven library 검토
domain behavior      → 직접 구현
```

---

## 12.5 작은 vertical slice

한 번에 완전한 IDE를 만들지 않는다.

한 slice는 반드시 눈으로 확인 가능한 하나의 결과를 만든다.

예:

```text
Electron 창이 뜬다.
```

다음:

```text
IDE shell이 렌더링된다.
```

다음:

```text
프로젝트 폴더를 연다.
```

이런 식으로 진행한다.

---

## 12.6 불필요한 선행 추상화 금지

아직 사용하지 않는:

- interfaces
- repositories
- service layers
- graph abstractions
- plugin architecture
- event bus
- database schema

를 미래를 예상하여 과도하게 만들지 않는다.

실제 다음 slice에서 필요해질 때 도입한다.

---

# 13. Foundation 작업 절차

```text
PRECHECK
   ↓
현재 repository 확인
   ↓
Node/npm 환경 확인
   ↓
공식 scaffold 확인
   ↓
desktop scaffold 생성
   ↓
scaffold 실행 확인
   ↓
불필요 demo 최소 정리
   ↓
IDE shell 구현
   ↓
dev 실행 검증
   ↓
기존 Python tests
   ↓
diff / scope 검토
   ↓
완료
```

---

# 14. Scaffold 관련 Done Criteria

다음 조건을 만족해야 한다.

- [ ] 공식/검증된 scaffold 존재 여부를 먼저 확인했다.
- [ ] scaffold가 가능한 boilerplate를 수작업 생성하지 않았다.
- [ ] package manager를 통해 dependency를 설치했다.
- [ ] dependency source code를 프로젝트에 복사하지 않았다.
- [ ] 불필요한 dependency를 대량 설치하지 않았다.
- [ ] scaffold 결과를 먼저 실행해 정상 bootstrap을 확인했다.
- [ ] 생성된 구조를 이유 없이 전면 재작성하지 않았다.
- [ ] 실제 프로젝트 고유 코드만 직접 작성했다.

---

# 15. v0.0.1 Done Criteria

- [ ] `desktop` Electron project가 존재한다.
- [ ] React + TypeScript가 사용된다.
- [ ] 개발 명령 한 번으로 Electron app을 실행할 수 있다.
- [ ] main / preload / renderer가 분리되어 있다.
- [ ] 기본 IDE shell이 렌더링된다.
- [ ] Top Bar / Activity Bar / Explorer / Main Workspace / Inspector / Bottom Panel의 기초 UI가 실제 화면으로 완성되어 있다.
- [ ] 기본 dark theme, empty state, 선택/hover/focus 상태 등 최소 UI interaction이 존재한다.
- [ ] Main Workspace에서 향후 Code Graph가 들어갈 시각적 영역을 확인할 수 있다.
- [ ] 단순 scaffold/demo 화면이나 임시 placeholder 페이지 상태로 Foundation을 종료하지 않는다.
- [ ] renderer에 unrestricted Node/OS 권한을 노출하지 않는다.
- [ ] 기존 Python `aidev` core가 유지된다.
- [ ] 기존 Python tests 결과가 regression되지 않는다.
- [ ] 불필요한 Code Graph / Monaco / xterm / DB 등을 미리 구현하지 않았다.
- [ ] 실제 변경 파일과 실행 명령을 최종 보고한다.

---

# 16. AI 작업 완료 보고 형식

작업 완료 시 장황한 서술 대신 아래 형식으로 보고한다.

```text
RESULT

Scaffold
- 사용한 generator:
- 사용한 template:
- 추가 dependency:

Created
- ...

Modified
- ...

Verified
- npm dev:
- build/typecheck:
- existing Python tests:

Not implemented yet
- Code Graph
- AI Workspace
- Planner integration
- Auto Runner

Issues / Risks
- ...
```

---

# 17. 최우선 규칙 요약

```text
1. 이미 있는 공식 scaffold를 AI가 다시 타이핑하지 않는다.
2. npm으로 설치 가능한 것을 프로젝트 안에 수작업 복제하지 않는다.
3. 기존 Python aidev core를 보존한다.
4. IDE와 Core를 분리한다.
5. Graph-first 제품 방향을 유지한다.
6. 한 번에 작은 vertical slice 하나만 구현한다.
7. 필요한 기능만 그때 설치/구현한다.
8. 재현 가능한 CLI 작업을 우선한다.
9. 사람의 인지 부채와 AI context 낭비를 줄이는 것이 제품 목적이다.
```
