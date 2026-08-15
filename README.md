# ai-dev-orchestrator

Claude Code 실행을 감싸서 **어디서 컨텍스트와 돈이 새는지** 측정하는 러너.
특정 프로젝트(Joker 등)에 종속되지 않는 독립 도구다. 다른 게임이든 MiniGPT든
`--repo`만 바꾸면 그대로 쓴다.

**v0.1 = Telemetry Runner**(`aidev run`) **+ v0.2 = Slice Pipeline**(`aidev pipeline`)
**+ v0.3 = Workspace 격리 + v0.4 = Epic → Slice Planner**. 현재 v0.4.0.
요구사항 .md 하나를 주면 plan → 승인 → implement → test를 사람 없이 진행하고,
정해둔 게이트에서만 승인을 기다린다. **에픽 .md 하나를 주면** 그것을 slice 목록으로
분해하고, 목록을 사람이 승인한 뒤 slice들을 순서대로 완주시킨다. 그 전부는
pipeline이 만든 **worktree + 전용 브랜치** 안에서 일어나고, 당신의 체크아웃에는
당신이 `--merge`를 칠 때만 반영된다.

```
aidev run
    ↓
claude -p --output-format stream-json --verbose --max-turns 80
    ↓
stream-json 한 줄씩 실시간 파싱
    ↓
raw event 원본 보존 (events.jsonl)
    ↓
tool / file access 집계
    ↓
session / cost / turn / time 기록 (telemetry.json + SQLite)
    ↓
terminal report
    ↓
aidev report <run-id>

(실행 중에는 다른 터미널에서 aidev watch)
```

## 설치

```bash
pip install -e ".[dev]"
```

의존성 없음(표준 라이브러리만). `claude` CLI가 PATH에 있어야 한다.

## 사용

```bash
aidev run \
  --phase implement \
  --repo ~/jokertest \
  --prompt tasks/doctor.md
```

`--prompt`는 현재 디렉터리 기준으로 먼저 찾고, 없으면 `--repo` 기준으로 찾는다.

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--phase` | `plan` / `explore` / `implement` / `review` / `test` / `repair` / `decompose` (기본 `implement`) |
| `--project`, `--task` | 라벨. 기본값은 repo 디렉터리명, 프롬프트 파일명 |
| `--max-turns` | 기본 80 |
| `--model` | claude에 그대로 전달 |
| `--permission-mode` | headless 실행 시 필요할 수 있음 (`acceptEdits` 등) |
| `--allowed-tools` | `--allowedTools`로 전달 |
| `--disallowed-tools` | `--disallowedTools`로 전달 (쉼표 구분) |
| `--safety` | `default` / `readonly`. readonly는 변경 계열 툴 전부 차단 |
| `--resume` | 기존 session id 이어받기 (pipeline이 쿼터 재시도에 쓰는 것과 같은 배선) |
| `--claude-bin`, `--claude-arg` | 실행 파일 교체 / 원시 인자 추가 |
| `--dry-run` | 실행할 커맨드만 출력 |
| `--no-live` | 라이브 패널 끄기 |
| `--data-dir` | 저장 위치 (기본 `$AIDEV_DATA_DIR` 또는 프로젝트의 `data/`) |

리포트:

```bash
aidev report last          # 마지막 실행
aidev report 20260813-19   # prefix / 부분 일치 가능
aidev report <run-id> --json
aidev list                 # 최근 실행 목록
aidev stats -n 20          # 최근 20회 phase 분포
```

## Slice Pipeline (v0.2)

요구사항 .md 하나를 단계 루프로 완주시킨다. 각 단계는 `execute()` 한 번이라
v0.1이 재던 것(events.jsonl / telemetry.json / live.json / SQLite)이 **단계마다
그대로** 남는다.

```
Requirement (.md)
    ↓
worktree 생성   <repo>-slices/<slice-id> + 브랜치 slice/<slice-id>  (base = repo의 현재 HEAD)
    ↓            커밋: slice(<id>): requirement      ← 요구사항이 첫 커밋
[setup]      front matter에 선언했을 때만, 1회 (미선언 = 아무것도 안 함)
    ↓
[plan]       safety readonly — 파일 변경 금지
    ↓        모델의 최종 메시지를 plan.md로 저장 / 커밋: slice(<id>): plan
게이트        approvals/plan.md에 "approved" / "rejected: 사유"를 쓸 때까지 대기
    ↓
[implement]  permission-mode acceptEdits, 프롬프트 = 요구사항 + 승인 시점의 plan.md
    ↓            커밋: slice(<id>): implement
[test]       프로젝트 테스트 실행 후 결과 보고 (고치지는 않는다)
    ↓          마지막 줄에 TEST_RESULT: PASS / FAIL 을 요구하고 그걸로 판정한다
    ↓            커밋: slice(<id>): test
단계별 토큰 / 비용 / 변경 파일 요약
    ↓
--merge <id>   base 브랜치에 merge      (사람이 친다)
--discard <id> worktree + 브랜치 제거    (사람이 친다)
```

```bash
aidev pipeline --repo ~/jokertest --requirement tasks/doctor.md
aidev pipeline --repo ~/jokertest --resume-slice last    # 중단된 slice 이어가기
aidev pipeline --repo ~/jokertest --list                 # slice 목록 / 상태
aidev pipeline --repo ~/jokertest --merge 20260816-doctor    # 승인 = base에 반영
aidev pipeline --repo ~/jokertest --discard 20260816-doctor  # 반려 = 폐기
```

`--repo` 기본값은 현재 디렉터리다. run 단위 세션 재개인 `aidev run --resume`과
이름이 겹치지 않게 `--resume-slice`로 분리했다. 인자는 정확한 id → 유일한 prefix →
유일한 부분일치 순으로 찾고, **여러 개에 걸리면 고르지 않고 에러를 낸다** (남의
repo에서 엉뚱한 slice를 재개하는 게 더 나쁘다). `last`는 이름 사전순이 아니라
`updated_at` 기준 **가장 최근에 움직인** slice다.

### Workspace 격리 (v0.3)

> AI는 사용자의 체크아웃과 base 브랜치에 절대 직접 닿지 않는다.
> 모든 AI 작업은 pipeline이 만든 worktree + 전용 브랜치에서 일어나고,
> 사람의 merge만이 결과를 진짜 이력으로 만든다.

`--requirement`로 slice를 시작하면 pipeline이 먼저 격리 공간을 만든다.

```
<repo>                     당신의 체크아웃 — claude가 여기서 도는 일은 없다
<repo>-slices/<slice-id>/   worktree, 브랜치 slice/<slice-id>
```

- **base는 main을 가정하지 않는다.** 기본값은 **repo의 현재 HEAD**다. 개발선이
  `windows-handoff-20260808`이고 main이 사용 중지 상태여도 그대로 동작한다.
  다른 데서 갈라지고 싶으면 `--base <branch>`.
- worktree 위치는 `--worktree-root <path>`로 바꾼다 (Windows 경로 길이 대책).
- 브랜치 이름은 `slice/<slice-id>`, 커밋 메시지는 `slice(<slice-id>): <stage>`로
  고정이다. `requirement` / `plan` / `implement` / `test` 네 개가 순서대로 쌓인다.
  이 커밋들이 이후 그래프 스냅샷의 기준점이다.
- **커밋은 pipeline이 한다.** 에이전트에게는 여전히 "커밋하지 마라"고 말한다.
  단계가 성공했을 때만 커밋하고, 실패한 단계가 남긴 변경은 다음에 성공한 단계의
  커밋에 함께 실린다(사람이 merge 때 한 diff로 본다). 이 커밋은 `--no-verify`로
  만든다 — 대상 repo의 pre-commit 훅 하나가 무인 루프를 통째로 세우면 안 되기
  때문이다. 사람이 검토하는 지점은 `--merge`이고, **거기서는 훅을 우회하지 않는다.**
  git identity가 없는 머신에서만 `aidev <aidev@localhost>`를 주입하고 한 번 알린다.

**잔해는 감지·거부만 한다. 청소는 사람이 한다.**

```text
error: cannot create the workspace for this slice:
  - path already exists: C:\...\joker-slices\20260816-doctor
    contains: node_modules, src
    registered to branch slice/20260816-doctor, prunable: gitdir file points to ...
    aidev never reuses or cleans a worktree. Remove it yourself, or pass --worktree-root <path>.
```

목표 경로가 이미 있거나, 브랜치가 선점됐거나, 등록만 남고 디렉터리가 없으면
**거부하고 사유를 출력한다** (exit 2). 조용히 재사용하지 않는다. 다른 경로의
잔해(실측: 조커에 Orca worktree 12개)는 **경고만 찍고 진행한다** — `git worktree
prune`은 어떤 경우에도 자동으로 돌리지 않고, 필요하면 사람이 칠 명령으로 안내만 한다.

**종료: 승인 = merge, 반려 = discard.**

```bash
aidev pipeline --repo ~/jokertest --merge 20260816-doctor
aidev pipeline --repo ~/jokertest --discard 20260816-doctor
```

`--merge`는 base 브랜치에 `--no-ff`로 merge한다. 전제조건을 하나라도 어기면
거부한다(exit 2): slice가 `done`이 아니거나, worktree에 커밋 안 된 작업이 남았거나,
본진이 base를 체크아웃하고 있지 않거나, 본진의 tracked 파일이 더럽거나.
**우리가 당신의 브랜치를 바꾸지 않는다** — base가 아니면 `git checkout <base>`를
알려주고 멈춘다. 충돌이 나면 **자동 해결하지 않는다**: 충돌 파일 목록을 먼저
읽어두고 `merge --abort`로 본진을 원래대로 되돌린 뒤 exit 4로 끝낸다.

`--discard`는 worktree를 지우고 브랜치를 삭제한다. 삭제 직전 sha를 출력하므로
reflog가 살아있는 동안은 되살릴 수 있다. worktree 제거가 실패하면(Windows에서
에디터·watcher·node가 파일을 잡고 있으면 흔하다) **브랜치는 남긴 채 중단한다** —
순서를 뒤집으면 돌아갈 곳 없는 worktree라는 더 나쁜 잔해가 생긴다.
"본진 무흔적"은 git 이력 기준이다. slice 자체의 기록(`.aidev/slices/<id>/`)은
**남긴다** — worktree를 버려도 무엇을 왜 했는지는 남아야 하기 때문이다.

`--no-worktree`를 주면 v0.2처럼 repo 안에서 직접 돈다. 경고를 찍고, v0.2의 dirty
검사가 그대로 살아난다. git이 없거나 git repo가 아닌 디렉터리에서의 유일한 길이다.

### 의존성 설치 (setup)

worktree에는 `node_modules` 같은 untracked 파생물이 없다 (실측: `npm ci` 수동 필요).
그렇다고 pipeline이 임의 설치 명령을 실행하지는 않는다. 요구사항 front matter에
**선언한 것만** 실행한다.

```markdown
---
approval: plan
setup: npm ci --prefix backend
---
```

- **미선언 = 완전 무동작.** 프로세스 0개, `--allowedTools` 변화 0개.
- 선언하면 implement 직전에 **1회** 실행한다. 성공은 `state.json`의
  `setup.status = done`에 남으므로 resume해도 다시 돌지 않는다. 실패하면 slice가
  실패한다 — 반쯤 깨진 환경 위에 implement를 태우면 모델 탓이 아닌 실패가 나온다.
  전체 출력은 `.aidev/slices/<id>/setup.log`에 남는다.
- 같은 명령이 implement/test의 `--allowedTools`에도 정확형(`Bash(npm ci --prefix
  backend)`)과 접두형(`Bash(npm ci:*)`) 두 가지로 얹힌다.
- **셸 없이 실행한다.** `&&`, `||`, `|`, `;`, `>`, `<`, 백틱, `$(`가 들어오면
  거부한다(exit 2). 셸을 열면 front matter 한 줄이 임의 스크립트가 된다.
  `setup:`을 값 없이 쓰는 것도 `approval:`과 같은 이유로 에러다.

### 승인 게이트

승인은 특정 단계의 기능이 아니라 **단계 사이마다 있을 수 있는 게이트**다. 루프는
매 단계가 끝날 때마다 "이 게이트가 켜져 있나"만 묻는다. 어느 게이트를 켤지는
요구사항 front matter가 정한다.

```markdown
---
approval: plan, implement    # 게이트 추가
# approval: none             # 완전 무인
---
# 밤 페이즈 의사 보호 로직
...
```

미지정이면 **plan 뒤 한 곳**이다. 오타(`approval: pan`)나 값이 빈 `approval:`은
무시하지 않고 에러를 낸다 — 게이트가 조용히 꺼진 채 무인 실행되는 쪽이 훨씬
위험하기 때문이다. 무인으로 돌리려면 `none`이라고 명시해야 한다. 값 뒤의
`# 주석`은 허용한다.

게이트에 걸리면 `state.json`이 `waiting_approval:<단계>`가 되고, 승인 파일이
없으면 설명이 들어간 템플릿을 만들어 둔다. 사람은 그 파일 아무 줄에나 쓴다:

```text
approved              → 다음 단계 진행
rejected: 사유         → slice 중단, 사유를 state.json에 기록
```

`#`으로 시작하는 줄은 무시하므로 템플릿 자체가 결정으로 읽히지 않는다.
승인 전에 `plan.md`를 직접 고쳐도 된다 — implement는 **승인 시점의** plan.md를 읽는다.
대기 중 프로세스를 죽여도 승인 파일이 곧 기록이라, `--resume-slice`로 이어가면
그 답을 그대로 읽고 계속한다.

### Epic → Slice Planner (v0.4)

> slice 하나의 무인 실행은 v0.3에서 끝났다. 남은 병목은 **사람이 slice마다
> 요구사항을 쓰고 발사 명령을 치는 것**이었다. 에픽 .md 하나를 주면 목록으로
> 분해하고, 사람이 그 목록을 승인하면 나머지는 순서대로 완주한다.

```bash
aidev pipeline --repo ~/jokertest --epic epics/v0-5-memory.md
aidev pipeline --repo ~/jokertest --resume-epic v0-5-memory     # 실패 지점부터
```

```
에픽.md
  → decompose (readonly, 에픽 전용 worktree)  → slices.md
  → 목록 승인 게이트  ← 사람이 고치고 승인. 끌 수 없다
  → slice 1 → slice 2 → ...   각각 v0.3 전체 흐름 그대로
  → 사람이 --merge
```

**decompose는 새 단계다.** plan과 같은 `readonly` 프로파일로 돌고, 끝난 뒤
`git status`로 사후 검증한다 — 분해가 코드를 건드렸으면 에픽을 실패시킨다.
`aidev stats`에도 독립 phase로 집계된다. 본진이 아니라 **에픽 전용 worktree**
(`epic/<epic-id>`)에서 도는데, 그래야 plan과 같은 강도의 사후 검증이 가능한
깨끗한 기준선이 생기고, 분해가 읽는 코드도 사람의 WIP가 아닌 base 커밋이 된다.
이 worktree에는 커밋을 만들지 않고, **목록이 승인되는 순간 통째로 사라진다.**

**분해의 품질 기준은 턴 예산이다.** 프롬프트가 "각 slice의 각 단계가
`--max-turns`(기본 80) 안에 끝나야 한다"고 요구하고, 실측 근거(2026-08-15에 너무
크게 자른 slice 2건이 81턴에서 작업 절반을 남기고 사망)를 그대로 준다.
판단이 서지 않으면 더 작게 자르라고 명시한다.

`slices.md`의 각 항목은 **그 자체로 실행 가능한 requirement**다. 형식은 기존
front matter 규약과 그대로 호환이고, 마커만 추가된다.

```markdown
=== SLICE 1: state.json에 memory 키 추가 ===
---
approval: plan
---
# state.json에 memory 키 추가

## 요구 동작
## Scope
## 테스트
## 하지 않는 것

=== SLICE 2: ... ===
```

파서는 번호·콜론·대소문자·문서 전체를 감싼 코드펜스에 관용적이다(모델이 실제로
흔들리는 지점이고, 어느 것도 목록의 의미를 바꾸지 않는다). 첫 마커 앞의 텍스트는
메모로 보존되고 실행되지 않는다. 마커를 하나도 못 찾으면 slices.md 경로와
`--resume-epic`을 찍고 실패한다 — **게이트가 바로 이 실패를 사람 손에 넘기는
장치다.** 목록의 순서가 곧 의존 순서다: 뒤가 앞에 의존할 수는 있어도 반대는 안 된다.

**목록 게이트는 `approval: none`으로도 끌 수 없다.** 분해는 모델이 "무슨 일이
존재하는가"를 정하는 유일한 단계라서, 무엇이든 돌기 전에 반드시 사람을 거친다.
사람은 승인 전에 slices.md를 직접 고쳐도 된다 (plan 수정-승인과 같은 규약).

#### slice N+1은 어디서 갈라지나

요구사항이 명시적으로 요구한 결정이다. 세 안을 실제 코드 기준으로 비교했다.

| | (a) 모두 같은 base | (b) N의 브랜치 위에 N+1 | (c) N을 auto-merge 후 N+1 |
| --- | --- | --- | --- |
| 의존 slice가 선행 결과를 본다 | **못 본다.** slice 2의 worktree에 slice 1의 코드가 없다 | 본다 | 본다 |
| merge 충돌 | 같은 파일을 건드리면 merge마다 충돌 | 사슬이라 base 대비 선형 | 없음 |
| 사람 merge 원칙 | 지킴 | 지킴 | **깨짐.** 무인 루프가 base를 움직인다 |
| 실패 slice 격리 | 좋음 | tip부터 거꾸로 discard | auto-merge된 건 되돌리기 어렵다 |

**(b)를 채택했다.** 단 그대로 쓰면 slice N+1의 base가 `slice/<N>`이 되어 `--merge`가
사람에게 남의 브랜치를 체크아웃하라고 요구한다. 그래서 **"어디서 갈라지나"와
"어디로 merge되나"를 분리한다** — `workspace`의 `base`는 에픽의 base 브랜치로
모든 slice가 동일하고(merge 대상), `start`가 분기 지점이다(slice 1은 에픽 시작
시점에 고정한 `base_commit`, slice N+1은 `slice/<N>`). 사슬이므로 앞 slice들은
조상이고, 사람이 순서대로 merge하든 tip 하나만 merge하든 결과가 같다.
(c)는 채택하지 않았으므로 "사람의 merge만이 진짜 이력" 원칙과의 조화 방안도 필요없다.

사슬 중간 slice를 `--discard`해서 뒤 slice가 갈라질 곳이 사라지면 **조용히 base로
되돌아가지 않고 실패시킨다.** 의존이 사라진 채 도는 쪽이 더 나쁘다.

#### 큐가 멈추는 것과 실패하는 것

- **쿼터 대기는 큐를 중단시키지 않는다.** 해당 slice가 `quota_wait`로 대기 후
  이어가고, 큐는 그 slice의 완료를 기다릴 뿐이다.
- **slice 안의 승인 게이트도 그대로 작동한다.** 큐가 승인 대기에서 멈춰 있는 것은
  정상 상태다. 둘 다 특별 처리가 없다 — 큐는 v0.3 루프를 호출하고 그 안에서
  블로킹되므로 저절로 성립한다.
- slice 하나가 `failed`면 큐는 거기서 **중단**한다.

```text
[pipeline] EPIC FAILED - slice 2 (v0-5-memory-02-queue) failed: stage 'test' reported failing tests
[pipeline]   fix it, then:  aidev pipeline --repo ~/jokertest --resume-epic v0-5-memory
[pipeline]   the remaining list can be rewritten first: ~/jokertest/.aidev/epics/v0-5-memory/slices.md
```

재개 전에 **남은 목록을 다시 쓸 수 있다.** 이미 시작된 항목은 위치 기준으로
그대로 유지하고(그 requirement는 이미 브랜치에 커밋돼 있어서 지금 바꾸면
거짓말이 된다), 시작되지 않은 첫 위치부터 끝까지를 현재 slices.md로 통째
교체한다. 목록이 이미 돈 개수보다 짧아지면 무엇이 이미 돌았는지 알려주고
거부한다. 버려진(`discarded`) 항목은 "시작되지 않음"으로 되돌아가 다시 만들어진다 —
사람이 slice를 버리고 다시 쓰는 탈출구다. 분해의 **자동** 재조정은 하지 않는다.

`.aidev/epics/<epic-id>/`에 들어가는 것:

```
<대상repo>/.aidev/epics/v0-5-memory/
├── epic.md          원본 사본
├── slices.md        decompose 산출물 (승인 전 사람이 고쳐도 된다)
├── slices/01-x.md   목록에서 잘라낸, 항목당 하나의 실행 가능한 requirement
├── state.json       에픽 진행의 유일한 원천 (단일 writer = 이 프로세스)
├── approvals/
│   └── decompose.md
└── runs.json        decompose의 run들
```

epic `state.json`은 slice의 규약을 그대로 상속한다(단일 writer, atomic 쓰기,
관용적 reader). status는 `running:decompose` / `waiting_approval:decompose` /
`running:slice` / `quota_wait` / `done` / `failed` / `rejected`이고, `slices[]`에
각 항목의 `index` / `title` / `requirement` / `slice_id` / `branch` / `start`가
들어간다. **거기 적힌 `status`는 투영이다** — authoritative source는 언제나 해당
slice의 `state.json`이고, 표시할 때마다 그쪽을 읽는다 (`.aidev/history/`의
`slice.json`과 같은 철학). slice 쪽 state에도 `epic` 키로 어느 에픽의 몇 번째인지
역참조가 남는다.

`--list`는 에픽이 하나라도 있으면 slice 목록 위에 진행 상황을 함께 찍는다.

```text
EPIC                              STATUS                    SLICES                        UPDATED
v0-5-memory                       running:slice 2/3         1=done 2=running:test 3=pending  2026-08-15T21:04
```

에픽은 중첩되지 않고(에픽 안의 에픽), slice는 **직렬로만** 돈다.

### 저장 위치

run 상세는 도구의 `data/`에, slice 상태는 **대상 repo를 따라다녀야 하므로**
프로젝트 안에 둔다.

```
<대상repo>/.aidev/slices/20260815-doctor/       ← 본진. 살아있는 상태
├── requirement.md   원본 사본
├── plan.md          plan 산출물 (승인 전 사람이 고쳐도 된다)
├── state.json       진행 상태의 유일한 원천 (단일 writer = pipeline 프로세스)
├── approvals/
│   └── plan.md      사람이 쓰는 승인 파일
├── setup.log        setup을 선언했을 때만
└── runs.json        단계 → run_id (상세는 data/runs/로 연결)
```

**살아있는 상태는 worktree가 아니라 본진에 둔다.** worktree를 폐기해도 slice
이력은 남아야 하고, 동시에 `state.json`의 단일 writer 규약을 깨면 안 되기
때문이다. 양쪽에 두면 writer가 둘이 된다.

커밋에 연결 가능한 메타데이터는 **읽기 전용 투영**으로 따로 만든다. 매 단계 커밋
직전에 pipeline이 worktree 안에 아래 세 파일을 쓰고 같이 커밋한다.

```
<worktree>/.aidev/history/20260815-doctor/     ← 브랜치. 커밋되는 스냅샷
├── requirement.md   요구사항 원문 (첫 커밋부터)
├── plan.md          승인 시점 기준 최신 plan
└── slice.json       state.json + runs.json의 투영 (stage/승인/commit/토큰/비용)
```

경로가 `.aidev/slices/`와 **다른 것이 핵심이다.** 같은 경로였다면 `--merge` 때
git이 *"untracked working tree file .aidev/slices/<id>/requirement.md would be
overwritten by merge"* 로 죽는다 — 본진에 그 파일이 이미 untracked로 있기
때문이다. `slice.json`은 pipeline만 쓰고 아무도 읽지 않는다. 도구는 언제나 본진의
`state.json`을 읽으므로 authoritative source는 계속 하나다.
(게이트 중에 사람이 `plan.md`를 고치면 그 단계 커밋에는 안 들어가지만, 매 커밋마다
투영을 다시 쓰므로 **다음 단계 커밋에 수정본이 실린다.**)

`state.json`은 live.json에서 검증된 규칙을 그대로 쓴다: 단일 writer,
`.tmp` → `os.replace` atomic 쓰기, reader는 깨진 파일에 관용적. slice status는
`running:<단계>` / `waiting_approval:<단계>` / `quota_wait` / `done` / `rejected` /
`failed` / `merged` / `discarded`, stage status는 `pending` / `running` / `done` /
`failed`다. **stages의 키 집합은 고정이 아니다** — 나중에 단계가 늘어도 reader는
모르는 키를 그대로 표시해야 한다. v0.3에서 `schema`가 2가 되고
`workspace` / `commits` / `setup` 키가 늘었다. reader는 **1도 계속 받는다** —
`workspace`가 없는 slice는 격리 없이 이어진다.

Windows에서는 reader가 파일을 열고 있는 것만으로 `os.replace`가 WinError 5로 죽는다
(CPython의 `open()`이 delete 공유를 주지 않는다). 지연이 아니라 순간 충돌이므로
`write_json_atomic`이 최대 1초(20회 × 0.05초) 다시 시도하고, 그래도 안 되면 예외를
그대로 올린다 — 삼키지 않는다. `state.json`, `runs.json`, `live.json` 전부에 적용된다.

### 테스트 판정

`claude` 프로세스는 테스트가 빨갛게 떠도 "정상 종료"한다. 그래서 exit code로는
성공을 판정할 수 없다. test 단계에는 마지막 줄에 `TEST_RESULT: PASS` 또는
`TEST_RESULT: FAIL`을 쓰라고 요구하고, 그 값을 `state.json`의
`test_verdict`(+ `stages.test.verdict`)에 남긴다.

- `FAIL` → slice는 **failed**로 끝난다 (exit 1).
- 줄이 아예 없으면 `unknown`으로 기록하고 요약에 표시한다. 아무 주장도 없는 걸
  실패로 단정하지는 않지만, **절대 통과로도 읽히지 않는다.**

### 실패와 쿼터

```text
쿼터류 실패 → state = quota_wait → 대기 후 같은 session으로 --resume 재시도
일반 실패   → state = failed, slice 중단 (자동 repair 루프는 이후 버전)
```

대기 시간은 2순위다. 에러에서 reset 시각을 파싱할 수 있으면 그때까지 기다리고,
못 읽으면 고정 간격(기본 15분)으로 재시도한다. 상한은 기본 20회다.
**쿼터 감지 조건은 잠정값이다** — 실제 한도에 걸렸을 때 events.jsonl / stderr.log에
오는 문구를 실측한 뒤 `QUOTA_MARKERS`를 갱신한다. 오탐을 줄이려고 스트림 전체가
아니라 **마지막 result 이벤트와 stderr만** 본다 (모델이 rate limit을 *말하는* 것과
실제로 걸리는 것은 다르다).

세션 정책은 **왜 멈췄는지**로 갈린다. 쿼터 대기는 세션이 *중단*된 것뿐이라 같은
session을 resume한다. 반면 일반 실패는 세션이 이미 "막혔다 / FAIL"이라는 **결론**을
갖고 있어서, 그 세션을 물려주면 환경이 바뀌어도 낡은 기억으로 몇 턴 만에 같은
결론을 재확정한다(실측). 그래서 같은 단계가 비쿼터 실패를 `--session-reset-after`회
(기본 1회) 연속하면 재시도는 **새 session**으로 시작하고, 프롬프트에 "이전 시도의
기억이 없으니 지금의 repo를 보고 판단하라"고 명시한다. 다음 단계는 언제나 새 session이다.

첫 시도 도중 프로세스가 죽은 경우(`failures`가 0인데 session_id는 있음)는 *진행 중인
작업*이므로 그대로 resume한다. 옛 동작이 필요하면 `--session-reset-after`를 크게 준다.

### 안전핀

- **본진 dirty 검사는 없앴다.** v0.2의 "임시 제한"을 v0.3이 대체한 결과다.
  AI가 본진에 닿지 않으므로 본진이 깨끗한지는 더 이상 전제조건이 아니다.
  대신 아래가 전제조건이다.

  | v0.2 (임시 제한) | v0.3 (대체 조건) |
  | --- | --- |
  | 시작 시 본진이 dirty면 거부 | 본진 dirty는 **무관**. 대신 시작 시 ①git repo일 것 ②base ref가 커밋으로 해석될 것(빈 repo 거부) ③worktree 목표 경로가 비어 있을 것 ④브랜치 `slice/<id>`가 없을 것 |
  | `mutated`가 false인 동안 매 단계 직전 재검사 | **readonly 단계(plan) 직전에만 worktree가 clean한지** 검사. plan의 사전/사후 비교에 깨끗한 기준선이 필요하고, 이전 실행의 위반이 다음 실행에서 세탁되면 안 되기 때문 |
  | — | 변경 단계 직전엔 검사 없음. worktree는 AI의 샌드박스이고 더러운 게 정상이다 |
  | — | `--merge` 할 때만 본진에 tracked 변경이 없을 것 + base가 체크아웃돼 있을 것. merge가 본진을 실제로 쓰는 유일한 순간이다 |

  `--no-worktree`로 돌리면 격리가 없으므로 v0.2의 dirty 규칙이 그대로 살아난다.
  v0.3 이전에 시작된 slice(`state.json`에 `workspace` 키가 없는 것)도 마찬가지로
  본진에서 이어지고, 그렇다고 알려준다 — 지금 worktree를 새로 만들면 이미 찍힌
  단계 커밋들과 다른 곳에서 나머지가 진행되기 때문이다.
- **stage 커밋이 파일을 너무 많이 쓸어담으면 커밋하지 않고 slice를 실패시킨다**
  (`--commit-file-limit`, 기본 2000). `.gitignore`가 부실한 프로젝트에서 setup이
  만든 `node_modules`가 통째로 브랜치에 실려 merge로 본진을 오염시키는 것을 막는다.
  단계의 작업물은 worktree에 그대로 있고, 사유에 상위 디렉터리별 개수를 찍는다.
- plan 단계는 `readonly` 프로파일로 돌고, 끝난 뒤 `git status`로 **실제로** 변경이
  없었는지 사후 검증한다. 이때 `.aidev/` 전체를 면제하지 않는다 — 면제하면 plan이
  **자기 승인 파일(`approvals/plan.md`)을 위조**해도 못 잡는다. 파이프라인이
  단계 중에 직접 쓰는 `state.json` / `runs.json` / `.lock`만 제외한다.
  (untracked 디렉터리가 한 줄로 접히지 않도록 `git status -uall`을 쓴다)
  격리 덕분에 위조 시도는 애초에 본진의 승인 파일에 닿지 못하지만, **worktree
  안에서 `.aidev/`에 쓰는 행위 자체도 여전히 readonly 위반으로 잡힌다.** 둘 다다.
- **slice 하나당 프로세스 하나.** `.aidev/slices/<id>/.lock`을 O_EXCL로 잡는다.
  같은 slice를 두 번 돌리면 두 번째는 거부된다 (exit 2). state.json의 단일 writer
  규약이 규약이 아니라 강제가 된다. 프로세스가 강제 종료돼 lock이 남으면 지우라고
  경로를 알려준다.
- `--permission-mode`는 implement/test에만 먹는다. plan은 무슨 값을 줘도 readonly다.
- **에픽의 목록 게이트는 `approval: none`으로도 못 끈다.** 다른 게이트는 사람이
  끄겠다고 하면 꺼지지만, 분해는 무슨 일이 존재하는지를 정하는 단계라 예외다.
- **decompose도 readonly + 사후 git 검증이다.** plan과 같은 프로파일, 같은 검사다.
  게다가 에픽 전용 worktree에서 돌기 때문에 위조 시도가 본진의 승인 파일에
  애초에 닿지 못한다.
- **큐는 조용히 base로 되돌아가지 않는다.** slice N+1이 갈라질 `slice/<N>`이
  없어졌으면 base에서 분기하는 대신 실패시킨다 — 의존이 사라진 채 도는 것이
  더 나쁘다.
- **테스트 명령은 규칙 단위로만 허용한다.** `acceptEdits`는 편집만 자동 승인하고
  Bash는 여전히 승인 대상이라, 규칙이 없으면 test 단계가 테스트를 **한 번도 못 돌린
  채** 끝난다(v0.2 실측). 그래서 implement/test에는 `--allowedTools`로 아래 규칙만
  얹는다. `--dangerously-skip-permissions` 같은 무제한 Bash는 쓰지 않는다.

  ```text
  Bash(npm test)   Bash(npm test:*)   Bash(npm run test:*)
  Bash(node --test)   Bash(node --test:*)
  Bash(pytest)   Bash(pytest:*)   Bash(python -m pytest:*)
  ```

  대상 repo의 `.claude/settings.json`은 **건드리지 않는다** — 사람 파일을 도구가
  옮기지 않는다는 v0.2 규약 그대로다. 사람이 영속 규칙을 원하면
  `.claude/settings.local.json`을 직접 두면 되고, 위 규칙은 거기에 *더해진다*.
  다만 **gitignore된 파일은 worktree에 따라오지 않으므로** 격리 실행에서는 본진의
  `.claude/settings.local.json`이 보이지 않는다. 파이프라인은 `--allowedTools`를
  매번 명시 전달하므로 파이프라인 동작에는 영향이 없지만, 그 파일에 의존하고
  있었다면 달라지는 지점이다. (복사는 하지 않는다.)
  다른 명령이 필요하면 `--allow-tool 'Bash(npx vitest:*)'`처럼 추가한다.
  같은 목록이 프롬프트에도 그대로 들어가므로, 모델이 아는 명령과 실제로 허용된
  명령이 어긋날 수 없다.
- 단계별 `--max-turns` 기본 80.

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--requirement` | 요구사항 .md (cwd → repo 순으로 찾는다) |
| `--epic` | 에픽 .md — 분해 → 목록 승인 → slice 순차 실행 (v0.4) |
| `--resume-slice` | 중단된 slice 이어가기 (id / prefix / `last`) |
| `--resume-epic` | 중단된 에픽 큐를 실패 지점부터 이어가기 (id / prefix / `last`) |
| `--list` | slice 목록과 상태 (에픽이 있으면 에픽 진행 상황도 위에 함께) |
| `--merge` | 끝난 slice를 base 브랜치에 merge (충돌 시 exit 4) |
| `--discard` | slice의 worktree 제거 + 브랜치 삭제 |
| `--base` | slice가 갈라져 나올 브랜치 (기본: repo의 현재 HEAD, **main 아님**) |
| `--worktree-root` | worktree 부모 디렉터리 (기본 `<repo>-slices`) |
| `--no-worktree` | 격리 없이 repo 안에서 직접 실행 (v0.2 동작) |
| `--setup-timeout` | front matter setup 명령의 제한 시간 (기본 1800초) |
| `--commit-file-limit` | 단계 커밋이 건드릴 수 있는 최대 파일 수 (기본 2000) |
| `--max-turns` | 단계별 상한 (기본 80) |
| `--permission-mode` | implement/test용 (plan은 항상 readonly) |
| `--allow-tool` | implement/test에 추가할 권한 규칙 (반복 가능) |
| `--session-reset-after` | 같은 단계가 비쿼터 실패 N회면 새 session (기본 1) |
| `--poll-interval` | 승인 파일 polling 간격 (기본 3초) |
| `--approval-timeout` | 승인 대기 포기 시간 (기본 0 = 무한 대기) |
| `--quota-wait` | reset 시각을 못 읽을 때의 재시도 간격 (기본 900초) |
| `--quota-max-retries` | 쿼터 재시도 상한 (기본 20) |
| `--dry-run` | slice id / base / 브랜치 / worktree / setup / 게이트 / 경로만 출력하고 종료 |

종료 코드: `0` 완주, `1` 실패, `2` 사용법·전제조건 위반, `3` 거부, `4` merge 충돌,
`127` claude 없음.

**Windows 주의.** worktree 경로 + 깊은 `node_modules`는 `MAX_PATH`(260자)에 쉽게
닿는다. `--worktree-root D:\wt`처럼 짧은 경로를 주거나
`git config --global core.longpaths true`를 켠다. `--discard`가 실패하면 대개
에디터·watcher·node가 worktree 안 파일을 잡고 있는 것이다 — 닫고 다시 실행한다.

## 다른 터미널에서 관찰 (v0.1.2)

```bash
aidev watch              # 현재 RUNNING인 최신 run에 자동으로 붙는다
aidev watch last         # 위와 동일
aidev watch 20260813-22  # 특정 run (prefix 가능)
aidev watch <run-id> --once      # 한 프레임만 찍고 종료
aidev watch --interval 0.5       # 0.3~0.5로 clamp
```

```
AI DEV WATCH   20260813-221956-doctor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Project   jokertest
Task      doctor
Phase     IMPLEMENT
Status    RUNNING

Session   slow-session
Turn      2
Elapsed   00:02
Cost      -

Tokens  EXACT, PROVISIONAL
 in                  40
 cache w            200
 cache r          2,500
 out                 80
 peak ctx         1,620

Tools
 Read           2

Largest reads
 src/gameSession.js                  19 KB

Repeated reads
 src/gameSession.js                     ×2

Attribution (estimated)
 Code exploration     100%    ~4.8k
 Other                  0%      ~18
 attributable                ~4.8k

> Read src/gameSession.js

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
updated  0.2s ago
Ctrl+C stops watching only - the run keeps going
```

**단일 writer 원칙.** telemetry 계산은 runner만 한다. watcher는 `live.json`을 읽어
그리기만 하고, run 디렉터리에 아무것도 쓰지 않는다.

```
runner  ──write──>  live.json  <──read──  watcher (N개 가능)
```

- **PROVISIONAL** — result event 도착 전 토큰 값은 지금까지 온 assistant 메시지
  누적치일 뿐이다. 헤더에 명시한다. result가 도착하면 reconcile 후
  **FINAL EXACT**로 바뀌고, run 종료 시 마지막 프레임에 이어 최종 리포트를 찍고
  watcher가 스스로 종료한다.
- **깨진 파일에 죽지 않는다** — watcher가 읽는 순간 runner가 파일을 교체 중일 수
  있다. 파일 없음/잠김/잘림/JSON 아님 전부 `None`으로 처리하고 직전 프레임을
  유지한다. 반대편도 방어한다: 모든 JSON은 `.tmp`에 쓰고 `os.replace`로 바꿔서
  reader는 이전 완전본 아니면 다음 완전본만 본다.
- **Ctrl+C는 watcher만 멈춘다** — 별도 프로세스이므로 runner는 영향이 없다.
  watcher를 `kill -9` 해도 runner는 끝까지 돈다.
- **STALE 감지** — runner가 죽어서 `live.json`이 RUNNING인 채로 멈추면 10초 후
  `STALE: no update for Ns`로 표시한다.
- run 종료 시 `live.json`은 **telemetry.json을 쓴 다음에** 마지막으로 갱신한다.
  watcher가 terminal status를 본 시점엔 최종 리포트가 반드시 디스크에 있다.

## 실행 중 화면

```
AI DEV RUNNER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Project   joker
Task      doctor
Phase     IMPLEMENT
Status    RUNNING

Session   9df1c2ab3f…
Turn      6
Elapsed   02:48
Cost      -

Tokens  EXACT, PROVISIONAL
 in                 1,204
 cache w           18,300
 cache r          412,900
 out                9,120
 peak ctx          63,240

Tools
 Read          19
 Bash           7
 Edit           4

Largest reads
 …/src/game/gameSession.js         42 KB
 …/src/game/gameSession.test.js    37 KB

Repeated reads
 …/src/game/gameSession.js         ×5

Attribution (estimated)
 Code exploration      38%   ~52.1k
 Implementation        27%   ~37.0k
 Test output           17%   ~23.3k
 attributable               ~137.1k
```

TTY가 아니면 자동으로 한 줄짜리 로그 모드로 떨어진다. result event가 도착하면
헤더가 `Tokens  FINAL EXACT`로 바뀐다 — runner 패널과 watcher가 같은 규칙을 쓴다.

## 실행 후 리포트

```
TOKEN / CONTEXT HOTSPOTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Code exploration        38%   ~52.1k  ███████·············
2. Implementation          27%   ~37.0k  █████···············
3. Test output             17%   ~23.3k  ███·················
4. Model output            11%   ~15.1k  ██··················
5. Other                    7%    ~9.6k  █···················

Estimated attributable tool context   ~137.1k

EXACT vs ESTIMATED

 Peak context (exact)            63.2k
 Attributable tools (est.)      ~52.3k
 Unattributed gap                10.9k   (17%)
 Billed input (cumulative)      432.4k

 note: gap = system prompt, tool schemas, CLAUDE.md, conversation scaffolding

Suspected waste

 src/game/gameSession.js
   read × 7   91 KB returned   (~19.5k repeated)
 npm test
   run × 3   184 KB output   (~31.2k repeated)

Estimated avoidable context
  ~31k tokens
```

## 세 가지 신뢰 등급

숫자를 절대 섞지 않는다. `telemetry.json`도 이 구조 그대로 저장된다.

| 등급 | 내용 | 출처 |
| --- | --- | --- |
| **EXACT** | session_id, num_turns, duration_ms, duration_api_ms, total_cost_usd, exit_code, **token usage** | Claude Code가 직접 보고 |
| **OBSERVED** | 툴별 호출 횟수, 읽은 파일, tool result 바이트, 테스트 실행 횟수, 에러 result, 중복 이벤트 | 이벤트 스트림에서 카운트 |
| **ESTIMATED** | 버킷별 귀속 컨텍스트, 반복 read 낭비, 회피 가능 토큰 | 바이트 → 토큰 환산 (≈4 chars/token) |

파일별 토큰은 **추정**이다. Claude Code 이벤트가 파일 단위 토큰 귀속을 주지
않으므로, 반환된 바이트 수로 환산한다. 그래서 리포트에 `~`를 붙인다.

### EXACT token usage

assistant 메시지마다 오는 `usage`를 필드별로 실시간 누적한다.

- `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens`
- **peak context** = 한 요청의 `input + cache creation + cache read` 중 최대값.
  실제로 모델에 들어간 컨텍스트 크기.
- **billed input** = 턴마다 재전송된 것까지 포함한 누적 입력. peak과 다르다.

**request_id 중복 제거** — 같은 assistant 메시지가 두 번 오면(재시도, 세션 재개,
transcript replay) usage도 turn도 tool 호출도 두 번 세면 안 된다. `request_id` →
`message.id` → `uuid` 순으로 키를 잡아 이미 본 메시지는 통째로 버리고
`duplicate_messages`로만 카운트한다. 셋 다 없으면 판단 불가이므로 **버리지 않는다**
(누락보다 중복이 낫다). tool_use id 중복도 따로 막는다.

**reconciliation** — 실행이 끝나면 누적값을 최종 `result.usage`와 대조한다.
result가 있으면 그쪽이 authoritative, 없으면 누적값이 authoritative. 차이가 나면
필드별 delta를 `telemetry.json`에 남기고 리포트 하단에 note를 찍는다.

### EXACT vs ESTIMATED gap

```
Peak context (exact)        63.2k     ← 진짜 컨텍스트 크기
Attributable tools (est.)  ~52.3k     ← 우리가 툴 트래픽으로 설명 가능한 몫
Unattributed gap            10.9k     ← 나머지 (17%)
```

gap은 system prompt, tool schema, CLAUDE.md, 대화 스캐폴딩처럼 이벤트 스트림에서
귀속시킬 수 없는 실제 컨텍스트다. 양쪽 다 **1회 컨텍스트 크기**라 비교가 성립한다
(누적 billed input과 비교하면 안 된다). 추정이 peak을 넘으면 `Estimate overshoot`
으로 뒤집어 표시한다 — 바이트→토큰 휴리스틱이 과대평가했다는 신호다.

hotspot 버킷 정의 (중복 집계 없음):

- **exploration** — Read/Grep/Glob/WebFetch 결과 바이트
- **implementation** — Edit/Write 툴 입력 + 결과 바이트
- **test output** — 테스트로 판정된 Bash 명령의 출력
- **model output** — 모델의 text/thinking 출력 (tool_use 입력은 제외)
- **other** — 나머지 Bash 출력과 기타 툴

## Safety profile

```bash
aidev run --safety readonly --phase review --repo ~/jokertest --prompt tasks/audit.md
```

`readonly`는 `--disallowedTools`로 `Bash, BashOutput, KillBash, KillShell, Edit,
MultiEdit, Write, NotebookEdit, Task, SlashCommand`를 막는다. Task까지 막는 이유는
서브에이전트가 Bash/Edit 권한을 그대로 물려받기 때문이다. `--disallowed-tools`로
준 목록은 프로파일 목록 뒤에 중복 없이 합쳐진다.

차단은 Claude Code가 하는 것이고 우리가 하는 게 아니다. 그래서 실행이 끝나면
**실제로 변경 계열 툴이 돌았는지 사후 검증**해서, 걸리면 리포트 최상단에
`SAFETY VIOLATION`을 띄우고 `observed.safety_violations`에 남긴다.

## 저장 구조

```
data/
├── aidev.db
└── runs/
    └── 20260813-194500-doctor/
        ├── run.json        실행 설정 + 커맨드 + 상태
        ├── events.jsonl    stream-json 원본 (파싱 전에 먼저 기록)
        ├── telemetry.json  EXACT / OBSERVED / ESTIMATED
        ├── live.json       실행 중 실시간 스냅샷 (watcher용, atomic replace)
        ├── prompt.md       사용한 프롬프트 사본
        └── stderr.log
```

`events.jsonl`은 **파싱하기 전에** 원본 그대로 기록한다. 파서에 버그가 있어도
나중에 raw event로 다시 분석할 수 있다. JSON이 아닌 줄도 그대로 남고
`malformed_lines`로 카운트된다.

pipeline이 만든 slice 상태는 여기가 아니라 **대상 repo의 `.aidev/slices/`** 에
들어간다 (위 [Slice Pipeline](#slice-pipeline-v02) 참고). 도구의 `data/`는 run
상세, 대상 repo의 `.aidev/`는 프로젝트를 따라다녀야 하는 진행 상태다.
slice 브랜치에 커밋되는 읽기 전용 스냅샷은 그 둘과 또 다른
`.aidev/history/<slice-id>/`다. 에픽은 같은 원리로 `.aidev/epics/<epic-id>/`에
들어간다 (위 [Epic → Slice Planner](#epic--slice-planner-v04) 참고).

SQLite 테이블(전체 실행 비교용): `runs`, `tool_calls`, `file_accesses`, `phases`.
`runs`에는 실행별 exact token(input / cache creation / cache read / output /
peak context)과 attributable / gap 토큰도 같이 들어간다. 예전 스키마로 만들어진
db는 열 때 자동으로 컬럼이 추가된다.

## 테스트

```bash
pytest
```

`tests/fake_claude.py`가 실제 `claude` 대신 stream-json을 뱉는 스텁이라, CLI
전 구간을 API 비용 없이 검증한다. 스텁은 일부러 **파싱 불가능한 줄 하나와
request_id가 같은 중복 메시지 하나**를 섞어 보낸다 — 둘 다 회귀 테스트 대상이다.

pipeline은 `tests/fake_pipeline_claude.py`를 쓴다. 환경변수로 단계별 응답과
실패 모드(쿼터 / 일반 실패 / readonly 위반)를 지시할 수 있고, 호출마다 argv와
**cwd**와 프롬프트를 로그로 남겨서 어느 단계에 어떤 플래그와 프롬프트가 갔는지,
그리고 **그 단계가 실제로 worktree에서 돌았는지**까지 검증한다. 승인 대기·거부·
중단 후 재개·쿼터 재시도가 전부 테스트에 있다.

격리 쪽 테스트는 진짜 git repo에서 돈다. `repo` fixture의 초기 브랜치 이름이
`windows-handoff-20260808`이라서 **"base가 main이 아닌 브랜치에서 동작한다"를
스위트 전체가 상시 증명한다.** git이 없으면 skip된다.

## Pluto IDE — 데스크톱 셸 (desktop/, v0.0.1)

Electron + React + TypeScript 기반 첫 셸. Python 코어와 완전히 분리돼 있고,
CLI 경로는 그대로 남는다. 앱 아이콘은 `png/pluto.png`에서 정사각 크롭해
`desktop/resources/icon.png`와 `desktop/build/icon.{ico,icns,png}`로 만든다.

```bash
cd desktop
npm install
npm run dev        # Electron 개발 실행
npm run build      # typecheck + electron-vite build
```

`@quick-start/create-electron`의 `react-ts` 템플릿으로 생성했다. renderer는 OS 권한을
직접 갖지 않는다 — `sandbox: true` / `contextIsolation: true`에서 preload가 노출하는
읽기 전용 capability API(`window.aidev`)만 쓴다.

**이 단계의 데이터는 전부 main 프로세스가 소유한 mock이다.** IPC 경계의 모양만
확정했고 `aidev` 코어 연동은 아직 없다. Code Graph도 좌표가 손으로 적힌 mock이며,
레이아웃 엔진은 v0.0.4에서 만든다.

### v0.2.5 — 최소 바인딩 (mock 제거 1단계)

Activity bar의 **Pipeline** 화면 세 가지만 실데이터다: 대상 repo의 slice/epic
상태 목록, `waiting_approval`인 slice의 산출물(plan.md) 뷰어, 승인/반려 버튼.
대상 repo에 **쓰는 것은 `approvals/<stage>.md` 하나뿐**이고(preload의 신규 쓰기
capability도 `writeApproval` 하나), 판단은 그대로 Python 코어에 있다. Graph /
Code / Diff / Test / Browser 화면은 아직 mock 그대로다.

```bash
npm test --prefix desktop   # typecheck + main 프로세스 읽기·쓰기 단위 테스트
```

승인이 실제로 파이프라인을 진행시키는지는 자동으로 증명할 수 없다 —
`desktop/README.md`의 수동 검증 절차가 그 자리를 채운다.

> Windows에서 VS Code 내장 터미널로 실행하면 `ELECTRON_RUN_AS_NODE=1`이 상속돼
> Electron이 plain Node로 떠서 죽는다. 그 터미널에서는 변수를 지우고 실행한다.

## 로드맵

```
v0.1  Telemetry Runner            ← 완료
v0.2  Slice Pipeline              ← 완료
v0.3  Workspace 격리               ← 완료 (worktree / 브랜치 / 단계별 커밋 / merge·discard)
v0.4  Epic → Slice Planner        ← 완료 (decompose / 목록 게이트 / 순차 큐 / --resume-epic)
v0.5  Codebase Memory             ← 지금 여기
v0.6  Pluto IDE 바인딩 (state.json / live.json → window.aidev)
      (v0.2.5에서 상태/plan/승인 3종 선행)
```

Planner(에픽→slice 자동 분해)를 뒤로 미룬 이유: 그건 지금 사람이 직접 해도
진행이 되지만, Pipeline(무인 실행 루프)이 없으면 사람이 자리를 비우는 순간 모든
작업이 멈춘다. 병목부터 풀었다. 구 로드맵의 "HTML Dashboard"는 Pluto IDE가
같은 화면을 담당하므로 제거했다.

v0.4로 루프가 닫혔다. 사람이 치는 것은 **에픽 하나와 승인들과 merge**뿐이고,
slice마다의 요구사항 작성과 발사는 없어졌다. 남긴 것: 병렬 slice 실행,
분해의 자동 재조정, 에픽 중첩. 셋 다 사람의 판단을 대체하려다 조용히 틀릴 수 있는
자리라서 의도적으로 비웠다.

세션 정책: **쿼터 대기는 기존 session resume, 결론을 낸 실패 뒤의 재시도는 새 session,
다음 단계는 언제나 새 session.**
v0.2의 쿼터 재시도가 이 정책을 그대로 쓴다.
