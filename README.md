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

Python **3.10 이상**. 의존성 없음(표준 라이브러리만). `claude` CLI가 PATH에 있어야 한다.

3.9는 **거부한다**(`requires-python = ">=3.10"`). 실측 2026-08-18 macOS: `aidev/verify.py`가
`Path.write_text(..., newline="\n")`를 부르는데 그 인자는 3.10에 생겼다. 3.9에서는 검증이
로그를 쓰는 순간 `TypeError`로 죽는다. 개행 제어를 빼는 쪽으로 물러서지 않았다 — 윈도우에서
CRLF가 섞이면 로그 대조가 깨지고, 그건 지울 수 없는 요구다.

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
--amend <id>   같은 브랜치 위에서 implement → test 한 바퀴 더 (사람이 친다)
--merge <id>   base 브랜치에 merge      (사람이 친다)
--discard <id> worktree + 브랜치 제거    (사람이 친다)
--stop <id>    돌고 있는 그 프로세스를 종료 — 신원이 맞을 때만 (사람이 친다)
--rollback <id> --to <stage>  브랜치와 상태를 그 단계 커밋으로 되감기 (사람이 친다)
--revert-merge <id>           base에 merge revert 커밋 (사람이 친다)
```

```bash
aidev pipeline --repo ~/jokertest --requirement tasks/doctor.md
aidev pipeline --repo ~/jokertest --resume-slice last    # 중단된 slice 이어가기
aidev pipeline --repo ~/jokertest --list                 # slice 목록 / 상태
aidev pipeline --repo ~/jokertest --amend 20260816-doctor "인원수 표시를 고쳐라"
aidev pipeline --repo ~/jokertest --merge 20260816-doctor    # 승인 = base에 반영
aidev pipeline --repo ~/jokertest --merge 20260816-doctor --push  # + 원격 백업
aidev pipeline --repo ~/jokertest --discard 20260816-doctor  # 반려 = 폐기
aidev pipeline --repo ~/jokertest --rollback 20260816-doctor --to plan  # 되감기
aidev pipeline --repo ~/jokertest --revert-merge 20260816-doctor        # 철회
```

요구사항은 `tasks/*.md`에 둔다. **`tasks/specs/` 아래는 우산 명세 보관소**다 — 한
영역 전체를 서술하는, 한 slice가 감당할 크기가 아닌 문서를 두는 곳이고
`--requirement tasks/specs/...`는 exit 2로 **거부된다**(규약 문구가 아니라 기계로
막는다). 개별 requirement로 분해해 `tasks/`에 두고 쏘거나, 분해 자체를 맡기려면
`--epic`으로 쏜다 — epic 경로는 막지 않는다. 우산을 쪼개는 것이 epic의 일이다.

`--repo` 기본값은 현재 디렉터리다. cwd에 `.aidev/`가 없어서 결과가 비면 그 이유를
찍고, **전에 `--repo`로 지정했던 repo들을 후보로 제시한다**(실측: 빈 목록만 나와서
원인을 못 찾았다). 후보 목록은 `<data-dir>/repos.json`에 남는다. 자동 적용은
하지 않는다 — repo를 고르는 건 끝까지 사람 몫이고, 잘못 고르면 남의 체크아웃에서
일이 벌어진다. run 단위 세션 재개인 `aidev run --resume`과
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

`--merge <id> --push`는 merge가 끝난 뒤 **base 브랜치만** 원격에 push한다(실측:
merge 후 push를 매번 손으로 쳤고, 잊으면 로컬 유일본이 된다). opt-in이고 기본값은
push 안 함이다.

- refspec을 `refs/heads/<base>:refs/heads/<base>`로 **명시**해서 부른다. `push.default`가
  뭐로 설정돼 있든 **slice 브랜치는 argv에 아예 등장하지 않는다.**
- `--force`, `--all`, `--tags`, `--set-upstream` 어느 것도 쓰지 않는다. 백업이지
  설정 변경이 아니다.
- 원격은 base가 추적하는 remote, 없으면 `origin`이다.
- push가 실패해도 **merge는 그대로 유효하다**(커밋은 진짜고 status도 `merged`).
  다만 exit는 `1`이다 — 이 옵션의 존재 이유가 "잊으면 로컬 유일본"인데 실패한
  백업이 성공으로 읽히면 안 된다. 재시도할 `git push` 명령을 그대로 찍어준다.
  결과는 `state.json`의 `merge.push`에 남는다. (원격이 크면 첫 push가 git
  타임아웃 300초를 넘길 수 있다. 그때도 merge는 유효하고 push만 실패로 보고된다.)
- `--merge` 없이 `--push`만 주면 사용법 에러다(exit 2).

`--discard`는 worktree를 지우고 브랜치를 삭제한다. 삭제 직전 sha를 출력하므로
reflog가 살아있는 동안은 되살릴 수 있다. worktree 제거가 실패하면(Windows에서
에디터·watcher·node가 파일을 잡고 있으면 흔하다) **브랜치는 남긴 채 중단한다** —
순서를 뒤집으면 돌아갈 곳 없는 worktree라는 더 나쁜 잔해가 생긴다.
"본진 무흔적"은 git 이력 기준이다. slice 자체의 기록(`.aidev/slices/<id>/`)은
**남긴다** — worktree를 버려도 무엇을 왜 했는지는 남아야 하기 때문이다.

**제거는 원자적이지 않다**(v0.5). `git worktree remove`는 등록 해제와 디렉터리 삭제
**두 가지**이고, 실측 2026-08-18에 앞의 것만 하고 뒤의 것에서 실패했다. 그러면 git이
모르는 고아 폴더가 남는데, `--resume-slice`는 "더 이상 등록돼 있지 않다"며 거부하고
`--discard`를 다시 쳐도 등록 없는 경로라 git이 또 거절한다 — **출구가 없었다.** 지금은
네 단계다.

- **선검사.** 잠긴 worktree, 등록됐는데 디스크에 없는 경로처럼 애초에 통할 수 없는
  상태면 **아무것도 건드리기 전에** 거부하고 풀 명령(`worktree unlock` / `worktree prune`)을
  준다. 실패한 게 아니라 시작하지 않은 것이므로 slice는 그대로다.
- **실패 후 재측정.** git이 무엇을 남겼는지 **가정하지 않고 다시 잰다.** 등록도 폴더도
  그대로면 아무것도 안 바뀐 것이고, 폴더가 이미 없으면 목표에 도달한 것이라 브랜치
  삭제로 넘어간다.
- **등록 복원.** 등록만 사라졌으면 `git worktree repair`로 되살려 본다. 되살아났는지는
  `find_worktree`로 **측정**한다(최선 노력이다 — admin 디렉터리가 통째로 없거나 구버전
  git이면 못 되살린다). 되살아나면 원상 복구이므로 다시 치면 된다.
- **고아 폴더는 케이스지 에러가 아니다.** 되살릴 수 없으면 `state.json`에
  `discard_failed`로 적고 복구 3종을 찍는다. 그 중 셋째가 `--discard` 재실행인데,
  **등록 없는 디렉터리는 이제 우리가 지운다.** 지우는 조건은 셋 다 만족할 때뿐이다 —
  state.json이 기록한 그 slice의 workspace이고, 어떤 worktree로도 등록돼 있지 않고,
  사람이 `--discard`를 직접 쳤을 때.

어느 분기에서도 **브랜치를 먼저 지우지 않는다.** 성공하면 `discard_failed`는 지워진다.

`--no-worktree`는 **v0.8부터 새 slice에서 쓸 수 없다.** 작업 지시서 사후 대조의
보증 범위가 "worktree 안에서 Git이 관찰하는 변경"이라 격리가 없으면 검사의 기준
자체가 없기 때문이다. 새 slice에서 주면 즉시 거부하고, `work_order_required`가 있는
slice는 `workspace` 기록 없이는 resume/amend도 거부한다. 남아 있는 용도는
**v0.3 이전에 시작돼 이미 돌던 legacy slice의 `--resume-slice`** 하나뿐이고, 거기서만
v0.2의 dirty 규칙과 사후 대조 skip이 그대로 산다. git이 없거나 git repo가 아니면
이제는 `git init`이 답이다.

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
- `test_commands:` 미선언이면 같은 명령이 implement/test의 `--allowedTools`에도
  정확형(`Bash(npm ci --prefix backend)`)과 접두형(`Bash(npm ci:*)`)으로 얹힌다.
- **셸 없이 실행한다.** `&&`, `||`, `|`, `;`, `>`, `<`, 백틱, `$(`가 들어오면
  거부한다(exit 2). 셸을 열면 front matter 한 줄이 임의 스크립트가 된다.
  `setup:`을 값 없이 쓰는 것도 `approval:`과 같은 이유로 에러다.

### 검증 명령 (test_commands, v0.4.1)

실측 2026-08-15: `setup: npm ci --prefix backend`가 `Bash(npm ci:*)`만 열어서
frontend 테스트 명령이 전부 거부됐다. 모델은 "이 프로젝트는 검증할 수 없다"고
결론내고 계획에서 이탈했다. **검증 명령은 setup에서 파생하지 않고 따로 선언한다.**

```markdown
---
approval: plan
setup: npm ci --prefix backend
test_commands: npm run test:guards, npm run lint
test_commands: npx vitest run          # 줄을 반복하면 누적된다
---
```

- 구분자는 **쉼표**다. 명령 안의 공백은 그대로 살아있다(`npm run test:guards`는
  한 명령). 쉼표가 들어간 명령은 표현할 수 없다 — 그런 명령은 `--allow-tool`로 준다.
- 선언하면 **선언한 것만** 허용된다. 내장 목록(`Bash(pytest:*)` 등)도, setup에서
  파생된 규칙도 얹지 않는다. 그게 이 결함의 원인이었기 때문이다.
- `--allow-tool`은 선언 여부와 무관하게 언제나 뒤에 더해진다 — 사람이 명시한 override다.
- 같은 목록이 프롬프트에도 "이 프로젝트의 검증 명령은 이것"이라고 들어간다.
  결함의 나머지 절반이 "검증이 불가능하다고 믿은 것"이었기 때문이다.
- 미선언이면 v0.3 동작 그대로다. 셸 메타문자 거부와 빈 값 거부는 `setup:`과 같다.

### 단계별 턴 예산 (max_turns, v0.4.1)

실측 2026-08-15/16: implement가 **81턴에서 3번 죽었다**(자기수리1차 / v0.3 / v0.4).
사인은 전부 "일 다 못 끝내고 강제 종료"이고, 매번 resume 비용이 추가됐다. 모든
단계에 같은 예산을 주는 게 틀린 모양이었다 — plan과 test는 80 안에 편하게 끝난다.

```markdown
---
max_turns: 140                  # 모든 단계
max_turns: implement=140        # 한 단계만
max_turns: 100, implement=140   # 기본값 + 덮어쓰기
---
```

우선순위는 **CLI 단계 > front matter 단계 > CLI 기본 > front matter 기본 > 80**이다.
CLI 쪽은 `--max-turns 100 --max-turns-stage implement=140`. 오타(`planz=`),
정수가 아닌 값, `0` 이하, 한 단계에 서로 다른 값 두 번은 전부 거부한다(exit 2) —
`approval:`과 같은 이유로, 뭔가 하려던 줄이 조용히 아무것도 안 하는 게 더 나쁘다.

plan 산출물이 파일 12개 이상 또는 테스트 파일 5개 이상을 지목하는데 implement
예산이 아직 160 미만이면, 승인 전에 경고 한 줄을 찍는다(로그 + `approvals/plan.md`
안의 `#` 주석). 죽은 세 slice가 공유한 규모가 그 임계값이다. **경고일 뿐 막지
않고, 예산을 자기 마음대로 올리지도 않는다.** 규모는 `state.json`의 `plan_scale`에
남는다.

v0.7부터 이 예산은 **바닥이 아니라 시작점**이다. 여기 적은 숫자에서 죽되 정직하게
죽은 단계는 엔진이 견적을 내서 스스로 올린다 → [실패와 자동 복구](#실패와-자동-복구-v07).

**발사 이후에 올린 예산도 반영된다.** 실측 2건: `tasks/pipeline-gen2b.md`가
`implement=160`인데 stage는 120으로 돌았고, `tasks/graph-trace.md`가 140인데 80으로
돌았다. 원인은 캐시도 worktree 오독도 아니었다 — 발사 시점에 요구사항이 slice
디렉터리로 **복사(동결)**되고, 그 뒤 모든 resume이 그 사본만 읽었다. 원본을 고쳐도
아무 일도 일어나지 않았던 것이다.

이제 resume / `--amend` / `--replan`은 원본 `tasks/*.md`의 **front matter만** 다시
읽어 동결본에 반영하고, 바뀐 키를 로그에 한 줄로 찍는다.

- **본문은 따라가지 않는다.** front matter는 slice가 도는 도중에 사람이 돌려도 되는
  손잡이(예산·모델·검증 명령)지만, 본문이 바뀌는 것은 **다른 slice가 되는 일**이라
  `--amend`의 몫이다.
- 원본이 지워졌거나 이름이 바뀌었거나 utf-8이 아니면 note 한 줄을 찍고 동결본을
  그대로 쓴다. **재동기화가 resume을 죽이지 않는다.**
- 원본의 새 front matter에 오타가 있으면 exit 2로 거부하되 **동결본은 건드리지
  않는다** — 검증을 통과한 뒤에만 다시 쓰므로 기록이 깨지지 않는다.
- epic이 만든 slice는 원본 파일이 없으므로(`requirement_source` 키 없음) 재동기화
  자체를 건너뛴다.

### 검증의 결정론화 (v0.5)

실측 2026-08-16/17: slice 평균 $15에서 **검증 루프가 최대 지출원**이었다.
근거 셋이 전부 같은 곳을 가리켰다.

| 실측 | 원인 |
| --- | --- |
| test 단계 8회 중 8회 PASS, 회당 $0.4 | 에이전트를 불러 재실행+한 줄 기록만 시켰다. 정보 이득 0 |
| implement 회전당 3~5턴 × 세션당 5~10회 | 매 수정 후 pytest 전량(233개)의 **통짜 출력**이 컨텍스트로 들어왔다 |
| 실패·반려하면 재작업이 백지 재탐색 | 맥락이 세션과 함께 증발했다 |

그래서 검증에서 **에이전트를 걷어내고**(엔진 직접 실행), 낭비의 기계적 원인은
프롬프트 지시가 아니라 **기계**로 막는다. 실패했을 때만 규격화된 문서를 남긴다.

**엔진 검증 — PASS 경로의 세션은 0개다.**

`test_commands:`를 선언했으면 test 단계는 세션을 사지 않는다. 엔진이 그 명령을
직접 돌리고, 통과하면 `state.json`에 기록하고 커밋하고 끝난다(비용 0). 명령이
여럿이면 **첫 실패에서 멈춘다** — lint가 깨졌는데 테스트 전량을 또 도는 건 낭비다.
미선언이면 v0.4의 에이전트 test 단계 그대로다(`--no-verify-engine`으로 강제할 수도
있다). 판정은 `state.json`의 `test_verdict`에 그대로 남으므로 기존 reader는 바뀐 걸
모른다.

**실패하면 문서가 남고, 규모에 따라 진단을 산다.**

```text
FAIL → failure.md → 실패 N개 이하?  → 진단 없이 failure.md만 물려 implement 재시도
                  → N개 초과/파싱실패 → diagnose 세션 1개 → diagnosis.md → 재시도
재시도 한도 초과 → failure.md + diagnosis.md가 사람이 볼 보고서 (slice는 failed)
```

임계값은 `--diagnose-threshold`(기본 3), 재시도 한도는 `--max-repairs`(기본 1).
`failure.md`에는 실행 명령/exit code/실패 테스트 목록(`파일:라인 — assert 메시지`)/
카운트(성공은 **개수만**)/traceback 요약/직전 커밋이 들어가고, 전문은
`verify/*.log`로만 남긴다. 출력을 파싱하지 못하면 원문 tail을 그대로 싣고 등급은
**대형**으로 간다 — 요약할 수 없을 때가 진단이 가장 필요한 때다.
진단 세션은 `readonly`라 코드를 고치지도 테스트를 다시 돌리지도 못한다. 기본 20턴.
재시도 커밋은 `slice(<id>): repair1/implement` 형식이라 원래 커밋도 그대로 남는다.

**출력 다이어트 — implement가 보는 것은 요약본뿐이다.**

implement 단계에는 원본 테스트 명령 대신 `aidev verify` 하나만 허용한다. 이 래퍼는
같은 명령을 돌리되 **실패분만** `파일:라인 — 메시지`로 찍고 성공은 개수만 찍는다.
전문은 로그 파일로 간다. PostToolUse 훅은 이미 반환된 Bash 출력을 *줄일 수 없어서*
(더할 수만 있다) 래퍼로 했다. `aidev`가 PATH에 없으면 다이어트를 끄고 원본 명령을
주면서 한 줄 알린다. `--no-output-diet`로도 끈다.

**Write 차단 — 기계 강제.**

대형 파일을 통째로 Write로 재출력하는 것은 가장 비싼 토큰(출력)의 낭비다.
PreToolUse 훅이 **이미 존재하는 파일에 대한 Write를 거부**하고(exit 2) 모델에게
"Edit을 써라"고 알려준다. 신규 파일 생성은 그대로 허용한다.

- 훅은 `<slice dir>/hooks/settings.json`에 쓰고 `--settings`로 넘긴다.
  대상 repo의 `.claude/settings.json`은 **건드리지 않는다**(v0.2 규약 그대로).
- 훅 스크립트는 무조건 **fail open**이다 — 입력을 못 읽거나 스키마가 바뀌면
  exit 0으로 통과시킨다. 훅이 무시돼도 파이프라인은 v0.4처럼 돌 뿐이다.
- `--no-write-guard`로 끄고, `AIDEV_WRITE_GUARD=off`로도 꺼진다.

**함수 명세 규약 — 기계 검사.**

이 slice부터 **신규·수정 함수는 명세 주석을 단다.** verify가 diff를 읽어 기계로
검사하고, 위반이 있으면 FAIL이다(`failure.md`의 `Spec violations` 절).

```python
def run_verify(cfg, rec, state, requirement, amend=None):
    """검증을 엔진이 직접 돌리고, 실패하면 등급에 따라 한 바퀴 고쳐 온다.   ← 기능 한 줄 (필수)

    @param cfg          이 slice의 PipelineConfig                        ← 파라미터마다 (필수)
    @param rec          SliceRecord - failure.md가 쓰이는 곳
    ...
    @flow  run_commands -> spec check -> failure.md -> 등급 -> diagnose?   ← 분기가 있을 때만
    주요 내부 변수: attempt(1부터), result(VerifyResult)                   ← 임의
    """
```

- 대상은 **이 slice의 diff에 걸린 함수만**이다. **기존 코드에 소급하지 않는다**
  (자연 축적 원칙). 건드리지 않은 함수는 그대로 둔다.
- 파이썬 파일만. 테스트 파일, `.aidev/` 하위, 중첩 함수, dunder(`__x__`)는 제외.
  파싱되지 않는 파일은 통째로 건너뛴다 — 검사기가 FAIL의 원인이 되면 안 된다.
- 잡는 것 셋: 명세 **없음**, `@param` **누락**, 함수를 고쳤는데 명세가 **무변경**.
  마지막 것은 한 줄만 고친 큰 함수에도 문서 갱신을 요구한다. 의도된 것이고,
  탈출구는 front matter `spec_check: off`(또는 `--no-spec-check`)다.
- 커밋 범위를 알 수 없으면(`--no-worktree`, v0.2 레거시 state) **건너뛰고 알린다.**
- implement는 `python -m aidev.specs --base <rev>`로 스스로 확인할 수 있다.

**임시파일 가드 — 기계 검사.**

실측 3건: `.tsscratch`, `reindent_tmp.py`, `reindent.py`가 각각 다른 slice의 브랜치에
남았다. "임시파일을 남기지 마라"는 규약 문구로는 한 번도 안 막혔다. 그래서 이름을
기계가 본다.

- 검사 대상은 **이 slice가 신규 추가한 파일**이다: `requirement` 커밋부터 HEAD까지의
  추가분(`git diff --diff-filter=A`) + 아직 커밋 안 된 추가분(`??` / `A`). test 단계
  커밋은 검사 **뒤에** 일어나므로 후자를 안 보면 implement의 잔재가 그냥 통과한다.
  **소급하지 않는다** — 이미 있던 파일은 건드리지 않는다.
- 판정은 **basename만** 본다. 이름을 `[^A-Za-z0-9]+`로 쪼갠 토큰이
  `tmp` `temp` `scratch` `scratchpad` `reindent` `debug` `bak` `backup` `wip`
  `untitled` 중 하나면 걸린다. 확장자가 `.tmp .temp .bak .orig .rej .swp .swo`이거나
  이름이 `~`로 끝나도 걸리고, `scratch`는 어디에 박혀 있든(`.tsscratch`) 걸린다.
  토큰 정확 일치라서 `oldest.py`는 `old`가 아니고 `useDebug.ts`는 `debug`가 아니며,
  `src/debug/panel.ts`는 폴더 이름이라 통과한다(`src/debug.ts`는 걸린다).
- `.aidev/` 하위와 `node_modules/` `data/`는 제외한다. 특히 `.aidev/`는 필수다 —
  엔진이 slice 기록을 worktree에 비추면서 `*.tmp`를 스치므로, 빠뜨리면 **모든
  slice가 자기 기록 때문에 FAIL한다.**
- 걸리면 test FAIL이고 `failure.md`의 `Temporary files` 절에 **파일 목록**이 남는다.
  명령이 전부 초록이어도 마찬가지다. 지우면 끝나는 실패라서 진단 세션은 사지 않고
  바로 수리 회전으로 간다.
- git이 diff를 못 읽으면 note 한 줄 찍고 건너뛴다 — 검사기가 FAIL의 원인이 되면 안
  된다. 오탐 탈출구는 `--no-temp-guard`다(front matter 키는 늘리지 않았다).
- 인코딩도 같은 자리에서 챙긴다: requirement가 utf-8이 아니면 traceback 대신 경로와
  "utf-8 인코딩 확인" 한 줄로 거부한다(exit 2). 실측: cp949로 저장된 `tasks/*.md`
  하나가 `UnicodeDecodeError` 스택 트레이스를 그대로 노출했다.

**progress.md — 턴 소진 대비.**

요구사항은 "턴 예산 90%에서 엔진이 세션에 신호 → 세션이 목록을 산출"이었지만,
headless `claude -p`에는 **실행 중인 세션에 말을 거는 채널이 없다.** 그래서 엔진이
텔레메트리에서 직접 쓴다 — 어떤 파일을 편집했고, 어떤 명령을 돌렸고, 마지막으로 뭐라
했는지. 세션의 협조가 필요 없고, 그래서 **문장 도중에 죽은 세션에도 통한다.**

- 쓰는 시점 둘: 턴이 예산의 90%에 닿았을 때, 그리고 단계가 **미완으로 끝날 때마다**.
- `## Done`(편집된 파일 + 실행한 명령) / `## Remaining`(plan이 지목했는데 아직 손대지
  않은 경로) / `## Last words` / `## Resume`(그대로 붙여넣을 명령).
- resume 시 **그 단계가 아직 미완일 때만** 프롬프트에 주입한다. 끝난 단계의 메모를
  주입하면 이미 커밋된 일을 설명하는 셈이라서다.
- 주입 뒤에도 **지우지 않는다.** 사람이 볼 보고서이기도 하다.

**반려 문서 규격 + 차분 재계획(`--replan`).**

`rejected: <사유>` 한 줄은 **여전히 전체 계약이다**(하위 호환). 선택적으로
`approvals/rejected-detail.md`(또는 `<stage>-rejected-detail.md`)에 **대상 좌표 /
문제 / 요구 / 범위**를 쓰면 재계획에 그대로 주입된다. 범위의 기본값은 **부분 반려**이고,
`전면`/`full rejection` 같은 말이 잡히면 전면 반려로 읽는다.

```bash
aidev pipeline --repo ~/jokertest --replan 20260817-doctor
```

- **반려된 slice를 resume하면 여전히 같은 승인 파일을 다시 읽고 멈춘다.** 그게 보호
  장치라 그대로 둔다. 재계획은 사람이 명시적으로 시키는 별개 명령이다.
- plan 단계에 **직전 plan 전문 + 반려 사유 + 반려 문서**를 주고, 부분 반려면
  *"반려가 지목한 것만 고치고 나머지는 그대로 둬라 — 이건 rewrite가 아니라 diff다"*,
  전면 반려면 *"처음부터 다시 계획해라"*를 붙인다.
- **아무것도 지우지 않는다**: `plan.md` → `plans/001.md`, `approvals/plan.md` →
  `approvals/plan-001.md`로 보존한다. 브랜치의 커밋도 되감지 않는다(그건
  `--rollback --to plan`의 일이다). 대신 그 아래 단계는 전부 pending으로 되돌린다 —
  plan이 바뀌면 그 아래는 전부 다시 결정될 일이다.
- `state.json`에 `replans` / `rejections`가 append-only로 쌓인다.

**승인 조건 승계.**

실측 2026-08-17/18: `approved: scope=A만`이라고 써서 plan을 승인했는데, 그 문구가
**어디에도 남지 않았다.** 파서가 `approved:` 뒤를 버리고 있었다. attempt 1이 죽고
attempt 2가 새 세션으로 뜨자 — 새 세션은 기억이 없다 — 조건 없는 plan만 보고 범위를
넘겨 구현했다. 조건은 **누가 한 번 말한 문장이 아니라 state**다.

- `approved: <조건>`의 문구를 `state.json`의 `stages.<단계>.approval_reason`에 기록한다.
  새 키를 만들지 않은 이유가 있다: `--replan`이 재계획 때 그 키를 **지운다.** 반려된
  plan에 붙었던 조건이 다음 사이클로 새는 것이 공짜로 막힌다. `--amend`는 지우지 않는데
  그것도 맞다 — amend는 승인된 plan 위에서 도는 사이클이라 조건이 계속 유효하다.
- 프롬프트의 **plan 블록 바로 아래**에 붙는다. 계획에 붙은 단서이므로 계획 옆이 제자리다.
  *"이건 승인된 것의 일부다. 이번 회차를 포함해 모든 시도에 유효하고, 조건과 plan이
  어긋나면 조건이 이긴다."*
- 닿는 곳은 implement / test / diagnose, 그리고 **재시도·resume·repair·amend 전부**다.
  프롬프트가 루프 회차마다 새로 만들어지므로 세션이 갈려도 따라간다.
- 조건이 없는 그냥 `approved`는 **바이트 하나 달라지지 않는다**(하위 호환). 조건을
  받아 적은 순간 `approval condition: ...` 한 줄을 찍는다 — 사람이 "기록됐다"를
  믿는 게 아니라 본다.
- desktop 셸(`parseDecision`)은 아직 승인 문구를 읽지 않는다. UI는 이번 범위 밖이고,
  desktop이 쓰는 승인문은 항상 조건 없는 `approved`라 실제로 어긋나지는 않는다.

**빈 requirement 가드.**

실측 2026-08-17: 빈 requirement가 worktree와 브랜치와 plan 단계와 게이트까지 사고
나서야 들켰다. 네 진입점 중 둘만 검사하고 있었다. 이제 검사는 함수 하나이고
**모든 진입점**(`--requirement` / `--epic` / `--resume-slice` / `--amend` /
`--replan`)이 발사 시점에 부른다. 파일이 비었거나, front matter만 있고 본문이 없거나,
본문이 사실상 비었으면 exit 2 + 그 셋을 구분하는 메시지다. 길이 하한은 **5자**로
거의 0인데, 의도적이다 — 다섯 글자가 한 문장인 언어가 있고, 길이를 재는 가드는
언어를 재는 가드가 된다.

**모델 믹스.**

```markdown
---
model: claude-sonnet-5                       # 모든 단계
model: diagnose=claude-haiku-4-5             # 한 단계만
model: claude-opus-5, diagnose=claude-sonnet-5
---
```

`max_turns:`와 **같은 문법**이다. 두 줄이 같은 모양의 질문에 답하기 때문이다. 지정
가능한 단계는 `plan` / `implement` / `test` / `diagnose` / `decompose`. CLI 쪽은
`--model` / `--model-stage diagnose=...`. 기본값은 **현행 유지**(아무것도 안 주면
CLI 기본이 그대로 간다). 오타난 단계, 빈 값, 한 단계를 서로 다르게 두 번은 거부한다.

### 작업 지시서와 사후 대조 (v0.8)

구현 왕복의 최대 원인은 모호함이다. plan의 산문 지시는 해석 여지를 남기고, 여지는
세션이 아무도 부탁하지 않은 파일을 만드는 자리가 된다. 그래서 plan은 산문 **위에**
절 하나를 더 싣고, 그 절만은 기계가 읽는다.

```markdown
## 작업 지시서

| 동사 | 대상 경로 | symbol | 책임 |
| --- | --- | --- | --- |
| CREATE | aidev/workorder.py |  | 지시서 파싱과 사후 대조 |
| MODIFY | aidev/pipeline.py | run_pipeline | scope check 호출 지점 |
| REFERENCE | aidev/verify.py |  | 검증 결과 구조 |
```

**세 동사뿐이다.** `CREATE`는 아직 없는 파일(있으면 plan FAIL), `MODIFY`는 이미 있는
파일(없으면 FAIL, 지우려는 파일도 여기), `REFERENCE`는 읽기만 하는 파일이다.
REFERENCE는 컨텍스트 트레이의 입력 목록이지 쓰기 권한이 아니고, **읽기
allowlist도 아니다** — 지정 밖을 읽는 것은 실패가 아니다.

**symbol은 선택이다.** 채우면 Function DB에서 *그 파일 안에서* 정확히 하나로
해석되어야 한다. 0개도 FAIL, 2개 이상도 FAIL(좌표를 나열한다). 비우면 파일 단위
지시이고, **엔진은 어느 함수를 말한 것인지 추정하지 않는다.** 기존 파일에 새 함수를
추가하는 경우에는 symbol을 비운다 — 검증 시점에 없는 이름은 해석이 0개다.

**형식은 고정이다.** 헤더 한 줄, 구분자 한 줄, 그 뒤가 데이터다. 절 제목은
`## 작업 지시서`(또는 `WORK ORDER`), 인식 가능한 절이 2개 이상이면 FAIL,
fenced code block 안의 제목은 절로 세지 않는다. 파싱은 전부 정규식과 문자열
처리다 — **LLM이 이 표를 읽는 일은 없다.**

**경로는 정규화된 repo-relative다.** 역슬래시·절대경로·드라이브 문자·콜론(ADS)·
제어문자·`..`·빈 세그먼트·후행 `/`·Windows 예약 장치명(`CON`, `COM1`…)·점이나
공백으로 끝나는 세그먼트는 전부 거부한다. symlink/junction으로 저장소를 벗어나는
경로는 `realpath` 비교로 잡는다. 중복 기준은: 같은 경로에 서로 다른 동사면 FAIL,
같은 `(경로, symbol)` 조합이 두 번이면 FAIL, 같은 경로·같은 동사에 **서로 다른**
non-empty symbol은 허용(함수 단위 지시서의 정상 형태), symbol 없는 행과 symbol
행을 한 경로에 섞으면 FAIL.

**언제 검사하나.** plan 커밋 직전(세션 0)에 한 번, 그리고 파일을 바꾼 **모든 실행의
stage commit 직전**에 최종 diff로 한 번 더다.

| 최종 diff | 지시서에 | 결과 |
| --- | --- | --- |
| 새 파일 (A) | CREATE에 없음 | **즉시 FAIL** |
| 삭제 (D) | MODIFY에 없음 | **즉시 FAIL** — 지우려면 그 경로를 미리 MODIFY로 선언한다 |
| 수정 (M/T) | CREATE·MODIFY에 없음 | FAIL 아님. `unplanned_modified`로 분류 |

미선언 수정을 FAIL로 만들지 않는 건 의도다. 한 줄 고쳐야 끝나는 일 때문에 slice
전체를 죽이면 아무도 이 장치를 켜두지 않는다. 대신 **사유를 요구한다**: 그 실행의
최종 응답 맨 끝에 `## 범위 밖 수정 사유` 절을 두고 `| 경로 | 사유 |` 표로 항목당 한
줄씩 선언한다. 엔진은 실제 `unplanned_modified` 경로 집합과 사유 표 경로 집합의
**완전 일치**를 요구한다 — 누락·중복·미존재 경로·여분 선언 전부 FAIL이다. 없으면
절을 생략한다. **사유 선언은 지시서를 확장하지 않는다.** 그 경로는 끝까지
unplanned로 남아 승인 화면과 리포트에 보인다.

**최종 diff는 임시 index로 만든다.** `base..HEAD`에 `status`를 덧칠하지 않고,
`GIT_INDEX_FILE`을 임시 파일로 잡아 `read-tree` → `add -A` →
`diff --cached --name-status -z --no-renames <base>` 를 돌린다. 그래야 원복·삭제 후
복원·추가 후 삭제가 전부 제대로 사라진다. 저장소의 실제 index와 HEAD는 건드리지
않는다. `add -A`는 `.gitignore`를 존중하므로 `.aidev/graph/`와 `__pycache__`는 애초에
보이지 않는다. git이 실패하거나 기준 커밋이 없거나 모르는 상태 문자가 나오면
**예외를 올려 검사를 FAIL시킨다** — 차단 장치에 fail-open은 없다.

**제외 목록은 디렉터리가 아니라 파일이다.** `.aidev/` 통째 제외는 하지 않는다.
`mirror_history`가 실제로 덮어쓸 정확한 파일(`requirement.md`, `plan.md`,
`slice.json`, 기록된 amend 번호, 본진 source가 실존하는 failure/diagnosis/progress)만
빠지고, `.aidev/history/<slice>/evil.py` 같은 것은 **신규 파일 FAIL로 잡힌다.**
setup 산출물은 이름이 아니라 **setup 직후의 mode + blob 지문**으로 제외한다. 내용이
바뀌거나 삭제되면 정상 scope 대상으로 복귀하고, 이미 CREATE/MODIFY로 선언된
경로에는 setup 제외를 적용하지 않는다.

**엔진 verify는 무관용이다.** 엔진 실행에는 사유를 댈 주체가 없기 때문이다. 검증
명령 실행 **직전**에 작업트리가 dirty하면 명령을 아예 실행하지 않고 FAIL,
**직후**에 Git이 관찰하는 변경이 하나라도 있으면 FAIL이다 (지시서 선언 여부와
무관하다). 두 검사는 실행 전후의 **각각 독립적인** 검사지 상태 비교가 아니다.

**digest는 둘이다.** `document_digest = sha256(plan.md 전체 바이트)`는 재승인 판단용,
`scope_digest = 정렬된 verb/path/symbol의 sha256`는 범위 비교·계측용이다(책임 문구는
제외한다 — 문장을 다듬었다고 범위가 움직이면 아무도 그 digest를 믿지 않는다).
plan은 **승인 직후** 재파싱·재검증되어 두 digest와 함께 봉인된다. 게이트는 사람이
plan.md를 고치라고 열어 둔 자리이므로, implement가 받는 건 지금 파일 그대로다.
implement/resume/repair/amend 직전에 `document_digest`가 어긋나면 재승인까지
중단한다. digest가 빈 값이면 "미봉인"으로 취급한다 — 빈 값이 우회로가 되지 않는다.
`approval: none`이면 사람 게이트를 만들지 않고 재검증·자동 재봉인만 한다.

**amend와 데드락.** amend는 plan을 다시 보지 않으므로, 열린 amend 밑에서 plan.md가
바뀌면 재승인할 기회가 영영 없어진다. 그래서 plan 게이트 처리를 **amend 필터보다
먼저** 두고, 재승인이 끝나면 같은 amend 사이클이 그대로 이어진다. amend 지시문
자체가 지시서 절을 실으면 그 행들이 **누적**된다 — plan의 원본 지시서는 불변이고,
대조용 effective order는 `plan 지시서 + 모든 amends[].work_order`의 합이다.

**보증 범위.** 이 장치가 보증하는 것은 **Git이 worktree 안에서 관찰하는 변경**이다.
"모든 신규 파일 즉시 FAIL"이라는 문장도 그 범위 안에서 읽어야 한다. worktree 밖
쓰기는 범위 밖이고, 안전핀 목록에 별도 항목으로 등록만 해 뒀다.

**하위 호환.** `state.json`에 `work_order_required`가 있는 slice에만 적용된다. 그
키가 없는 것은 v0.2/v0.3 시절 slice이고, resume에서 봉인도 사후 대조도 하지 않는다.
새 plan과 replan은 지시서가 없으면 FAIL한다. **끄는 스위치는 없다** — 차단 장치에
우회로를 두지 않는다.

### 사후 수정 (--amend, v0.4.1)

완주한 slice에 사후 지시를 던지는 방법이 "새 requirement 작성"뿐이었다. 사후 감독
모델에서는 이게 **최빈 동작**이 되므로 채널을 따로 냈다.

```bash
aidev pipeline --repo ~/jokertest --amend 20260816-doctor "인원수 표시를 고쳐라"
```

- amend는 **새 단계도 새 slice도 아니고, 같은 기록 위의 한 사이클**이다.
  `implement → test`를 다시 연다. `plan`은 다시 돌지 않는다 — **지시문이 이 사이클의
  plan이다.** 고칠 때마다 readonly 단계 하나와 게이트 하나를 더 무는 건 최빈 동작에
  붙일 비용이 아니다.
- 그래서 **plan 게이트도 다시 묻지 않는다.** `rejected`로 끝난 slice를 amend할 수
  있는 것도 이 때문이다(`approvals/plan.md`의 낡은 답을 다시 읽지 않는다).
- 프롬프트에는 **지시문 + 원 requirement + 직전 RESULT** 세 가지가 함께 들어간다.
  requirement와 plan은 "이미 만든 것을 이해하라는 이력"으로 명시된다.
- worktree와 브랜치는 그 slice가 이미 가진 것을 쓴다. 세션은 **새로 시작한다** —
  "다 끝냈다"고 결론낸 세션이 새 지시를 낡은 기억으로 판단하면 안 된다.
- 커밋은 `slice(<id>): amend1/implement` 형식이다. `slice(<id>): ` 접두는 그대로라
  기존 reader가 전부 그대로 동작하고, 원래의 `implement` 커밋도 `commits`에 남는다.
- `state.json`에는 `amends`(append-only 목록)와 `amend_open`이 붙는다. schema는
  **2 그대로다** — 추가된 키가 전부 optional이기 때문이다. 실패하면 `amend_open`이
  열린 채 남고, `--resume-slice`는 **slice 전체가 아니라 그 사이클을** 이어간다.
- `discarded` slice는 거부한다(브랜치가 없다). `merged` slice는 실행하되 "다시
  merge해야 한다"고 안내한다. 에픽 소속 slice도 실행하되 "뒤 slice들은 이 amend를
  갖고 있지 않다"고 경고한다(뒤 slice는 amend 이전 tip에서 갈라져 나왔다).

### 되돌리기 (--rollback / --revert-merge, v0.4.2)

> 철학 0조: "일단 만든다, 언제든 롤백된다, 그래서 거침없다."

되돌리기가 사람의 git 지식(reset? revert? 어느 해시?)에 의존하고 있었다. 커밋에는
이미 slice와 stage가 박혀 있으므로 **되돌리기도 의미 단위로 한다.** 상황이 둘이라
명령도 둘이다: slice 브랜치는 로컬 전용이라 **되감고**, base 브랜치는 push됐을 수
있으므로 **revert만 한다.**

```bash
aidev pipeline --repo ~/jokertest --rollback 20260816-doctor --to plan
aidev pipeline --repo ~/jokertest --revert-merge 20260816-doctor --reason "설계가 틀렸다"
```

**`--rollback <id> --to <stage>` — 진행 중/실패 slice 되감기.**

- `--to`가 가리키는 커밋은 **남고, 그 뒤가 되감긴다.** `--to plan`이면 plan 커밋까지
  남고 implement/test 커밋이 브랜치에서 빠진다. `requirement`도 target이다.
- slice 브랜치는 **로컬 전용이 원칙**이라 여기서는 `reset --hard`가 허용된다. 다만
  worktree에 **tracked 변경이 남아 있으면 거부한다**(exit 2) — reset이 그걸 삼킨다.
  untracked 파일은 reset이 건드리지 않으므로 그대로 통과시키고, 살아남는다.
- state.json도 같이 되감긴다. **지우지 않는다**: `attempts`/`failures`는 이미 쓴
  비용이라 그대로 두고, 각 단계에 `rewound`(되감기 전 status/run_id/commit/verdict)를
  남긴 뒤 `pending`으로 바꾼다. 되감긴 단계의 `session_id`는 버린다 — 없어진 작업에
  대해 결론을 낸 세션이 그 결론으로 새 작업을 판단하면 안 된다.
- 이어서 `--resume-slice <id>`를 치면 그 지점부터 다시 진행한다.
- 어떤 커밋이 빠지는지는 라벨 순서가 아니라 `git merge-base --is-ancestor`로 판정한다.
  amend는 `amend1/implement`를 `test` **뒤에** 찍으므로 순서 가정은 틀린다.
- `merged` slice는 거부하고 `--revert-merge`를 안내한다. `reverted` / `discarded`도
  각각의 사유로 거부한다.

**`--revert-merge <id>` — merge된 slice 철회.**

- base에 **revert 커밋을 하나 더 쌓는다.** reset은 하지 않는다 — 이미 push됐을 수
  있고, 남이 가진 브랜치를 다시 쓰는 건 되돌리기가 아니라 두 번째 문제다.
- 본진 전제조건은 **`--merge`와 똑같다**(base 체크아웃 + tracked clean). 본진에
  쓰는 순간이 그 둘뿐이라 같은 함수가 같은 문장으로 거부한다.
- merge 커밋이 지금 base에 없으면(이력 재작성 등) 거부한다.
- 충돌은 **자동 해결하지 않는다**: 충돌 파일을 먼저 읽어두고 `revert --abort`로
  본진을 원상복구한 뒤 exit 4. slice status는 `merged` 그대로고 `revert.status`만
  `conflict`로 남는다.
- 성공하면 status는 `reverted`다. worktree와 브랜치는 **그대로 둔다**(우리가 만들지
  않은 걸 지우지 않는 `--discard`의 원칙과 같다). 이후 경로는 `--amend` 후 다시
  `--merge`, 또는 `--discard`. **push는 하지 않는다** — 필요하면 사람이 친다.
- `reverted` slice는 `--resume-slice`가 거부한다. 모든 단계가 `done`인 채로 루프를
  통과해 status가 조용히 `done`으로 돌아가면 번복 기록이 세탁되기 때문이다.

**공통.**

- **실행 전에 현재 지점과 복구 명령을 먼저 찍는다**(`--discard`와 같은 방식).

  ```text
  [pipeline] rollback slice 20260816-doctor -> 'plan' (9f8e7d6)
  [pipeline]   branch  slice/20260816-doctor
  [pipeline]   was at  a1b2c3d   (2 commit(s) dropped: implement, test)
  [pipeline]   undo    git -C C:\...\wt\20260816-doctor reset --hard a1b2c3d4e5
  ```

- 번복은 `.aidev/slices/<id>/rollbacks.json`에 **append-only로 쌓인다**(시각, 대상,
  빠진 커밋 sha, 되감긴 단계, `--reason`, 복구 명령). **미래 Ledger의 번복률은 이
  파일 하나만 읽으면 된다.** state.json에는 가장 최근 한 건의 요약(`rollback`)만 둔다.
- 되돌리는 범위에 마이그레이션 파일이 있으면 **경고만 한다.**

  ```text
  [pipeline] warning: this rollback range contains 2 migration file(s) - the database is NOT
  [pipeline]          rolled back by this command
  [pipeline]            backend/migrations/003_add_seat.sql
  ```

  판정은 경로 휴리스틱이다: 경로에 `migrations`/`migration`/`migrate` 디렉터리가
  있거나, `alembic/versions/`이거나, `.sql` 파일명이 `V1__` / `003_` 형태일 때.
  **막지 않고, DB를 되돌리지도 않는다** — 사람이 판단할 재료를 주는 것뿐이다.
- `--dry-run`이면 위 출력만 하고 **아무것도 바꾸지 않는다.**
- 하지 않는 것: **epic 단위 되감기**(설계만 하고 이번 범위 밖), **DB 마이그레이션
  자동 롤백**(경고만), **push된 slice 브랜치 처리**(slice 브랜치는 로컬 전용이 원칙).

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
approved: 조건         → 진행하되, 조건이 이후 모든 단계·재시도·repair 프롬프트로 따라간다
rejected: 사유         → slice 중단, 사유를 state.json에 기록
```

`approved: scope=A만`처럼 조건을 붙이면 `state.json`에 기록되고 이후 프롬프트마다
plan 바로 아래에 실린다(위 "승인 조건 승계"). 조건 없는 `approved`는 예전 그대로다.

반려는 한 줄이면 충분하지만, 같은 디렉터리의 `rejected-detail.md`에 **대상 좌표 /
문제 / 요구 / 범위**를 쓰면 `--replan`이 그걸 그대로 plan 단계에 물려준다(v0.5,
위 "검증의 결정론화" 절). 범위의 기본은 **부분 반려**다.

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
판단이 서지 않으면 더 작게 자르라고 명시한다. 정말 더 못 자르는 slice는
`max_turns: implement=140`으로 **자기 예산을 스스로 올릴 수 있다**(v0.4.1).
프롬프트가 인용하는 숫자는 decompose가 실제로 받는 예산이다.

`slices.md`의 각 항목은 **그 자체로 실행 가능한 requirement**다. 형식은 기존
front matter 규약과 그대로 호환이고, 마커만 추가된다. 항목의 front matter는
`approval:` / `setup:` / `test_commands:` / `max_turns:` / `model:` /
`spec_check:` / `briefing:` / `auto_resume:` / `auto_extend:` 아홉 키를 받고,
**목록 전체를 먼저 검증한다** — 4번 항목의 오타가 1~3번이 브랜치를 만들기 전에
걸린다. (아홉 키 전부를 실제로 검증한다. `model:`과 `spec_check:`는 여기 적혀
있으면서 정작 검사에서 빠져 있었다 — 선언한 줄이 조용히 아무 일도 안 하는 것은
아래 미지 키와 같은 결함이라 함께 닫았고, `briefing:`과 v0.7의 복구 스위치 둘은
처음부터 같은 자리에 넣었다.)

**모르는 키는 무시하되 경고한다**(v0.5). 실측 2026-08-17: `model:`이 아직 미지 키이던
시절 조용히 버려졌고, 그 바람에 **`max_turns` 적용까지 깨졌다.** 조용한 무시가 결함이지
무시 자체가 결함은 아니다.

```
warning: front matter key(s) ignored: modle - known: approval, setup, test_commands, max_turns, model, spec_check, briefing, auto_resume, auto_extend
```

- **에러가 아니다.** 오타일 수도, 사람이 남긴 메모일 수도, 다음 버전이 추가할 키일 수도
  있다. 그 중 어느 것도 slice를 죽일 값어치는 없다.
- 발사 경로마다 **정확히 한 번** 찍힌다(`--requirement` / `--resume-slice` / `--amend` /
  `--replan` / `--dry-run` 전부 front matter를 해석하는 한 곳을 지난다). epic 항목은
  검증 단계에서 어느 항목인지까지 붙여 찍는다.

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
│   ├── plan.md               사람이 쓰는 승인 파일
│   ├── rejected-detail.md    선택: 구조화된 반려 (대상/문제/요구/범위, v0.5)
│   └── plan-001.md           --replan이 보존한 직전 승인 파일 (v0.5)
├── plans/001.md     --replan이 보존한 직전 plan (v0.5)
├── failure.md       검증이 실패했을 때만 (v0.5)
├── diagnosis.md     대형 실패로 진단 세션을 샀을 때만 (v0.5)
├── progress.md      단계가 미완으로 끝났거나 턴 90%에 닿았을 때만 (v0.5)
├── observation.md   병리 소진으로 Observer를 소환했을 때만 (v0.7)
├── verify/          검증 명령의 출력 전문 (attempt별 로그, v0.5)
├── hooks/settings.json   Write 차단 훅 (--settings로 넘어간다, v0.5)
├── setup.log        setup을 선언했을 때만
├── rollbacks.json   번복 이력 (되돌린 적이 있을 때만, append-only)
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
`failed` / `merged` / `discarded` / `rolled_back` / `reverted`, stage status는
`pending` / `running` / `done` / `failed`다. **stages의 키 집합은 고정이 아니다** — 나중에 단계가 늘어도 reader는
모르는 키를 그대로 표시해야 한다. v0.3에서 `schema`가 2가 되고
`workspace` / `commits` / `setup` 키가 늘었다. reader는 **1도 계속 받는다** —
`workspace`가 없는 slice는 격리 없이 이어진다. v0.4.1이 더한
`amends` / `amend_open` / `plan_scale` / `merge.push`는 전부 optional이라
`schema`는 **2 그대로다** — 모르는 reader는 틀린 게 아니라 그냥 더 오래된 것뿐이다.
v0.4.2가 더한 `rollback` / `revert` / `stages.*.rewound`도 같은 이유로 전부 optional이고,
`schema`는 여전히 **2**다. status 값은 열린 집합이라 reader는 `rolled_back` /
`reverted`를 모르더라도 **문자열 그대로 표시하면 된다.**
v0.5가 더한 `repairs` / `replans` / `rejections` / `progress` /
`stages.test.verify`도 전부 optional이라 `schema`는 **2 그대로다.**
v0.7이 더한 `recovery`(자동 대기·연장·관찰의 원장)도 마찬가지다 — **처음 필요해진
순간에 만들어지므로** 자동 복구가 한 번도 걸리지 않은 slice에는 키 자체가 없고,
옛 state.json은 그대로 읽힌다. `schema`는 여전히 **2**다. 반대로 v0.5는
옛 state를 그대로 받는다 — `commits`도 `workspace`도 없는 v0.2 기록은 명세 검사가
읽을 커밋 범위가 없으므로 **검사를 건너뛰고 그렇다고 알린 뒤** 계속 돈다.

Windows에서는 reader가 파일을 열고 있는 것만으로 `os.replace`가 WinError 5로 죽는다
(CPython의 `open()`이 delete 공유를 주지 않는다). 지연이 아니라 순간 충돌이므로
`write_json_atomic`이 최대 1초(20회 × 0.05초) 다시 시도하고, 그래도 안 되면 예외를
그대로 올린다 — 삼키지 않는다. `state.json`, `runs.json`, `live.json` 전부에 적용된다.

### 테스트 판정

**`test_commands:`를 선언했으면 엔진이 직접 돌리고 exit code로 판정한다**(v0.5,
위 "검증의 결정론화" 절). 모델의 자기신고가 아니므로 판정은 결정론적이고, 세션은
0개다.

아래는 **`test_commands:`를 선언하지 않은 폴백 경로**(또는 `--no-verify-engine`)에만
남는 v0.4 규약이다. `claude` 프로세스는 테스트가 빨갛게 떠도 "정상 종료"하기 때문에
exit code로는 성공을 판정할 수 없다. 그래서 test 단계에 마지막 줄로
`TEST_RESULT: PASS` 또는 `TEST_RESULT: FAIL`을 쓰라고 요구한다.

두 경로 모두 결과는 `state.json`의 `test_verdict`(+ `stages.test.verdict`)에 같은
모양으로 남으므로, 기존 reader는 어느 쪽이 돌았는지 몰라도 된다. 엔진이 돌았을 때만
`stages.test.verify`에 명령별 exit code와 로그 경로가 함께 남는다.

- `FAIL` → 엔진 경로는 `failure.md`를 쓰고 등급에 따라 재시도한다. 폴백 경로는
  v0.4 그대로 slice가 **failed**로 끝난다 (exit 1) — **재시도는 여전히 없다.**
  다만 이제 폴백 경로도 `failure.md`를 **엔진과 같은 규격**(사유·좌표·카운트·
  `last commit:`)으로 남긴다. 세션의 최종 보고문을 엔진이 쓰는 것과 같은 파서에
  태우므로, 모델이 `FAILED tests/x.py::test_y - AssertionError: ...`를 인용했으면
  좌표까지 나오고, 아무것도 파싱되지 않으면 보고문 tail을 그대로 싣는다. **사유는
  언제나 남는다** — 예전에는 세션이 사라지면 읽을 것이 아무것도 없었다.
  규약 위반(명세·임시파일)도 이 경로에서 똑같이 검사한다.
- 폴백 경로에서 줄이 아예 없으면 `unknown`으로 기록하고 요약에 표시한다. 아무 주장도
  없는 걸 실패로 단정하지는 않지만, **절대 통과로도 읽히지 않는다.**

### 실패와 자동 복구 (v0.7)

> 실측 2026-08-18/19: 24시간 안에 한도 사망 2회(8/18 밤, 8/19 새벽). 매번 사람이
> 리셋 시각을 계산해 수동 resume했고, 턴 소진 사망도 매번 사람이 로그를 읽고
> 상향을 결정해 재발사했다. **멈춤이 사람을 기다리는 것이 비용이다.**

죽은 단계는 세 가지로 분류되고, 각각 다른 답을 받는다. 분류는 엔진이 `events.jsonl`
꼬리(마지막 result 이벤트 + stderr)와 telemetry의 `result_subtype`를 **둘 다** 읽어서
한다 — telemetry는 그 스트림의 파생물이라 서로를 검증한다.

| 사인 | 판독 | 엔진의 답 |
| --- | --- | --- |
| **한도(usage limit)** | `QUOTA_MARKERS` + reset 시각 파싱 | 리셋+60초까지 기다렸다 같은 session으로 재개 |
| **턴 소진** | `error_max_turns` / "maximum number of turns" | 건강하면 견적 내서 연장, 병리면 Observer |
| **그 외 에러** | 위 둘 다 아님 | v0.6 그대로 — `failed`, 사람에게 |

**자동인 것은 재개뿐이다. 새 발사는 언제나 사람이 한다** — 데몬도 cron도 watcher도
만들지 않았다.

#### 한도 → 자동 대기·재개

대기 시간은 2순위다. 에러에서 reset 시각을 파싱할 수 있으면 그때까지 기다리고,
못 읽으면 고정 간격(기본 15분)으로 재시도한다. 상한은 기본 20회다.
여유는 **60초**다(v0.6은 30초였다 — 한도가 채 안 풀린 채 재개해 밤 하나를 더 썼다).

```text
[pipeline] 한도 도달 — 03:40 자동 재개 예정
[pipeline]   retry 1/20 in 4h 12m (reset time from the error), resuming session abc123
```

`auto_resume: off`(또는 `--no-auto-resume`)면 **기다리지 않고** 시각과 재개 명령을
말한 뒤 끝낸다. 이때도 대기 자체는 `state.json`에 열린 채 남으므로, 나중에
`--resume-slice`가 **남은 대기를 마저 하고** 이어간다.

프로세스가 죽어 있던 경우의 보완책은 데몬이 아니라 **다음 명령이 말해 주는 것**이다.

```text
$ aidev pipeline --repo ~/jokertest --list
SLICE                   STATUS            STAGES
20260819-doctor         quota_wait 03:40  plan=done implement=running

$ aidev pipeline --repo ~/jokertest --resume-slice last --dry-run
wait    03:40 이후 재개 예정 — aidev pipeline --repo ~/jokertest --resume-slice 20260819-doctor
```

#### 턴 소진 → 건강 판정 → 견적 → 연장

한 세션 안에서의 수치로만 판정한다. 아래 중 **하나라도** 걸리면 병리, 아니면 건강이다.

| 병리 조건 | 임계값 |
| --- | --- |
| 아무 파일도 만지지 않았다 | 편집 0 + 읽기 0 |
| 같은 파일 재편집 | 한 파일 5회 이상 **그리고** 손댄 파일 2개 이하 |
| 재독 폭주 | 한 파일 4회 이상, 또는 재독 총량 12회 이상 |
| 같은 명령 반복 | 같은 명령 3회 이상 |
| 도구 에러 누적 | 10회 이상 |

**애매한 세션은 건강으로 읽는다.** 애매를 병리로 밀면 기본 모드에서는 그냥 죽고,
aggressive에서는 돈만 쓴다. 판정은 요구사항이 정의한 대로 2치다.

건강하면 견적을 낸다. 실측 소화 속도는 **이 세션이 실제로 쓴 턴 ÷ 이 세션이 실제로
움직인 파일 수**다(readonly 단계는 '읽은 파일'이 단위다).

```text
연장량 = ceil(Remaining × (턴 ÷ 파일) × 1.2)      # 여유 20%
       , 최소 20턴, 총 턴 상한(기본 300)까지만
Remaining을 셀 수 없으면(plan.md가 아직 없다) 정액 40턴
```

`Remaining`은 `progress.md`의 `## Remaining`과 **같은 함수**에서 나온다 — 로그의
숫자와 파일의 목록이 어긋날 수 없다.

```text
[pipeline] 정직한 소진 — Remaining 1건, +32턴 연장 재개
```

연장은 같은 session을 `--resume`으로 이어받고 예산만 넓힌다. 연장량은 원장에 남으므로
**프로세스가 죽었다 살아나도 그 예산으로 돌아온다**(`--max-turns-stage`로 사람이 더
높게 주면 사람 쪽이 이긴다).

#### 병리 → Observer (외부 눈)

병리로 판정되면 `auto_extend: aggressive`에서만, slice당 **1회**, 별도 세션을 산다.

- **입력은 로그뿐이다.** telemetry가 요약한 행동(편집·재독·반복 명령·마지막 도구
  호출), 판정 근거, `progress.md`, `failure.md`, requirement. **plan.md도 코드도
  주지 않는다.** requirement를 주는 이유는 하나 — 그게 없으면 "scope 재검토"라는
  선택지가 판단 불가능한 문장이 된다.
- **기계로 막는다.** `readonly` 프로파일이 Bash/Edit/Write/Task를 막고, 그 위에
  `Read` / `Glob` / `Grep` / `LS` / `WebFetch` / `WebSearch`까지 `--disallowedTools`로
  막는다. "로그만 보라"가 지시가 아니라 기계가 된다.
- 산출은 `observation.md` 하나다: 반복 중인 행동 / 막힌 지점 추정 원인 /
  `suggestion: switch-approach | rescope | call-human`.
- **`call-human`이면 재시도하지 않는다.** 외부 눈이 사람을 불렀는데 한 번 더 태우면
  그 눈을 산 이유가 지워진다.
- 그 외에는 `observation.md`를 프롬프트에 주입하고 **새 session으로** 1회 재개한다.
  병리 세션을 그대로 물려주면 관찰이 그 세션의 머리 위에 얹히기 때문이다.
- `model: observe=claude-sonnet-5`로 **다른 모델에게 시킬 수 있다** — 모델 믹스 문법
  그대로이고 새 키는 없다. `max_turns: observe=30`도 같다(기본 20턴).

#### 스위치와 상한

| 모드 | 자동 연장 | Observer |
| --- | --- | --- |
| `auto_extend: off` | 0회 (턴 소진 = 즉시 failed, v0.6 동작) | 0회 |
| `auto_extend: conservative` **(기본)** | 1회 | 0회 |
| `auto_extend: aggressive` | 2회 | 1회 |

상한에 걸리면 **새 상태를 만들지 않는다** — 기존 `failed`로 멈추고, 어느 핀이
걸었는지와 사람이 칠 명령을 말한다.

```text
[pipeline] 자동 복구 중지 — 자동 연장 상한(1회) 도달 — 더 늘리지 않는다
[pipeline]   사람이 판단할 차례다:  aidev pipeline --repo ~/jokertest --resume-slice 20260819-doctor
```

모든 자동 조치는 `state.json`의 `recovery`에 남는다(Ledger의 원천).

```json
"recovery": {
  "waits":        [{"stage": "plan", "at": "...", "kind": "usage_limit", "resume_at": "...", "wait_s": 15180.0}],
  "extensions":   [{"stage": "implement", "at": "...", "from": 80, "to": 112, "remaining": 1, "units": 3, "turns": 80, "pace": 26.67}],
  "observations": [{"stage": "implement", "at": "...", "run_id": "...", "suggestion": "switch-approach"}],
  "stopped":      {"at": "...", "stage": "implement", "pin": "extensions", "detail": "..."}
}
```

요약표는 넓히지 않는다. 원장이 비어 있지 않을 때만 아래 한 줄이 붙는다.

```text
Auto      연장 1 (implement 80->112), Observer 1 (switch-approach), 한도 대기 1
```

#### 그 외 에러 — 바뀐 것이 없다

```text
일반 실패 → state = failed, slice 중단 (검증 실패의 repair 루프는 위 "검증의 결정론화" 절)
```

**쿼터 감지 조건은 잠정값이다** — 실제 한도에 걸렸을 때 events.jsonl / stderr.log에
오는 문구를 실측한 뒤 `QUOTA_MARKERS`를 갱신한다. 오탐을 줄이려고 스트림 전체가
아니라 **마지막 result 이벤트와 stderr만** 본다 (모델이 rate limit을 *말하는* 것과
실제로 걸리는 것은 다르다). 턴 소진 오탐도 같은 값을 치른다 — 어느 출처도 턴 소진을
말하지 않으면 '그 외 에러'로 읽고, 오탐의 비용은 세션 한 번이며 위 상한이 그걸
1~2회로 묶는다.

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
  | — | `--rollback`은 worktree에 tracked 변경이 없을 것(reset이 삼킨다. untracked는 무관). `--revert-merge`는 본진에 쓰므로 `--merge`와 **같은 조건**이다 |

  v0.3 이전에 시작된 slice(`state.json`에 `workspace` 키가 없는 것)는 본진에서
  이어지고 v0.2의 dirty 규칙이 그대로 살아난다 — 지금 worktree를 새로 만들면 이미
  찍힌 단계 커밋들과 다른 곳에서 나머지가 진행되기 때문이다. v0.8부터 그것이
  `--no-worktree`의 **유일한** 용도다: 새 slice는 격리 없이 발사할 수 없다.
- **worktree 밖 쓰기 차단 — 범위 밖, 미구현.** 사후 대조(v0.8)의 보증은 "worktree
  안에서 Git이 관찰하는 변경"이다. 세션이 홈 디렉터리나 다른 저장소에 쓴 것은 이
  검사가 보지 못한다 (요약의 `(+N outside the workspace)` 줄이 세는 게 전부다).
  안전 프로파일과 write guard가 부분적으로 막지만 기계적 보증은 아니다. 여기에
  항목으로 등록만 해 두고, 구현은 이 slice의 범위가 아니다.
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
  규약이 규약이 아니라 강제가 된다.
- **lock은 pid가 아니라 신원을 적는다.** pid는 재사용되는 번호라 "pid 4812가 살아
  있다"와 "lock을 잡은 그 프로세스가 살아 있다"는 다른 질문이다. 그래서 lock에는 네
  가지가 들어간다: pid, **그 프로세스의 생성 시각**, 어느 레코드의 것인지, 어느
  repo의 것인지 (POSIX는 독립 process group으로 발사됐는지도). 넷이 **전부** 맞아야
  "그 프로세스"다.
  - 주인이 죽었거나, pid는 살아 있는데 생성 시각이 다르면(= 번호가 남에게 넘어갔다)
    **stale**이다. 다음 실행이 한 줄 알리고 자동으로 인수한다 — 사람이 지울 필요가 없다.
  - 생성 시각을 못 읽거나(다른 계정의 pid 등), 다른 repo·다른 레코드의 lock이면
    **아무것도 하지 않고** 사유와 경로만 알려준다. 못 죽여서 드는 비용은 Ctrl-C
    한 번이고, 잘못 죽여서 드는 비용은 남의 작업 전부다.
- **`--stop <slice>`: 신원이 전부 맞을 때만 종료한다.**
  ```bash
  aidev pipeline --repo <repo> --stop 20260822-doctor
  ```
  - 신원 4요소 일치 → 종료. POSIX는 **독립 process group으로 발사된 경우에만**
    그 그룹에 SIGTERM을 보낸다 (부모 pid 단독 시그널은 자식을 고아로 남기므로
    아예 제공하지 않는다). 그룹을 공유하고 있으면 거부하고, 그 터미널에서 직접
    끊으라고 안내한다. Windows는 정중한 그룹 시그널이 없어 `taskkill /T /F`다.
  - lock이 없거나 stale이면 종료할 게 없다고만 말하고 exit 0 — 아무것도 죽이지 않는다.
  - **구형 lock**(신원 정보 없는 `pid`/`since` 두 줄)은 자동 인수도 자동 종료도
    거부하고 exit 2로 수동 정리 경로를 알려준다. 파일은 손대지 않는다.
  - `--stop`은 lock을 **지우지 않는다.** 죽는 프로세스가 나가면서 지우고, 못 지웠으면
    다음 실행이 stale로 인수한다. 밖에서 지우면 그 인수와 경합할 뿐이다.
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
  명령이 어긋날 수 없다. 프로젝트의 검증 명령을 front matter `test_commands:`로
  선언하면 **그것만** 허용된다(위 "검증 명령" 절).
- 단계별 `--max-turns` 기본 80. front matter `max_turns:`나 `--max-turns-stage`로
  단계별로 올릴 수 있다(위 "단계별 턴 예산" 절).
- **이미 있는 파일에 대한 Write는 거부한다**(v0.5). PreToolUse 훅이 exit 2로 막고
  "Edit을 써라"고 알려준다. 신규 파일 생성은 그대로 허용한다. 훅은 슬라이스
  디렉터리의 `hooks/settings.json`에 쓰고 `--settings`로 넘기므로 대상 repo의
  `.claude/settings.json`은 여전히 건드리지 않는다. 훅은 무조건 **fail open**이라
  (입력을 못 읽으면 통과) 스키마가 바뀌어도 파이프라인이 벽돌이 되지 않는다.
  `--no-write-guard` 또는 `AIDEV_WRITE_GUARD=off`로 끈다.
- **이 slice가 고친 함수에 명세 주석이 없으면 verify가 FAIL이다**(v0.5).
  대상은 diff에 걸린 파이썬 함수만이고, 테스트 파일·`.aidev/`·중첩 함수·dunder는
  제외, **기존 코드에 소급하지 않는다.** 커밋 범위를 모르면 건너뛰고 알린다.
  `spec_check: off` 또는 `--no-spec-check`로 끈다. (위 "검증의 결정론화" 절)
- **이 slice가 임시 이름의 파일을 새로 추가하면 verify가 FAIL이다.** 신규 추가분의
  basename만 보고, `.aidev/`·`node_modules/`·`data/`는 제외하며 **소급하지 않는다.**
  `--no-temp-guard`로 끈다. (위 "검증의 결정론화" 절)
- **빈 requirement는 발사 시점에 거부한다**(v0.5, exit 2). 모든 진입점이 같은
  검사를 부른다. **utf-8이 아닌 requirement도 같은 자리에서 거부한다** — traceback
  대신 경로와 "utf-8 인코딩 확인" 한 줄이다.
- **`tasks/specs/` 아래 문서는 발사할 수 없다**(exit 2). 우산 명세는 분해해서
  `tasks/`의 개별 requirement로 쏘거나 `--epic`으로 쏜다.
- **plan / implement는 발사 직전에 그래프 브리핑을 받는다**(v0.6). `briefing: off`
  / `--no-briefing` / `--no-graph`로 끄고, 끄면 프롬프트는 바이트 단위로 예전과
  같다. (아래 "브리핑 생성기 — 상차림" 절)
- **자동 복구에는 상한이 셋 있다**(v0.7, 무한 과금 방지). ①자동 연장은 slice당
  최대 2회(`aggressive`), 기본은 1회 ②어떤 연장도 **총 턴 상한**(`--turn-cap`,
  기본 300)을 넘겨 예산을 올리지 못한다 ③Observer는 slice당 최대 1회이고
  `aggressive`에서만 소환된다. 상한에 걸리면 새 상태를 만들지 않고 **기존
  `failed`로 멈추고 사람을 부른다.** Observer 자신과 diagnose는 자동 복구의
  대상이 아니다 — 관찰자를 관찰하면 루프가 된다.
- **자동으로 한 일은 전부 `state.json`의 `recovery`에 남는다**(v0.7): 시각·사유·
  연장량·어느 핀이 멈췄는지. 상한은 루프 변수가 아니라 **원장의 길이**로 걸리므로,
  프로세스가 죽었다 살아나도 횟수가 이어진다. (위 "실패와 자동 복구" 절)

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--requirement` | 요구사항 .md (cwd → repo 순으로 찾는다) |
| `--epic` | 에픽 .md — 분해 → 목록 승인 → slice 순차 실행 (v0.4) |
| `--resume-slice` | 중단된 slice 이어가기 (id / prefix / `last`) |
| `--resume-epic` | 중단된 에픽 큐를 실패 지점부터 이어가기 (id / prefix / `last`) |
| `--list` | slice 목록과 상태 (에픽이 있으면 에픽 진행 상황도 위에 함께) |
| `--amend` | 끝난 slice에 지시문 하나로 implement → test 한 바퀴 더 (v0.4.1) |
| `--replan` | 반려된 slice의 plan 단계를 반려 문서와 함께 다시 열기 — 차분 재계획 (v0.5) |
| `--merge` | 끝난 slice를 base 브랜치에 merge (충돌 시 exit 4) |
| `--push` | `--merge`와 함께: base 브랜치만 원격에 push (slice 브랜치는 절대 안 함) |
| `--discard` | slice의 worktree 제거 + 브랜치 삭제 |
| `--stop` | slice의 lock을 쥔 프로세스를 종료 — 신원 4요소가 전부 맞을 때만 |
| `--rollback` | slice 브랜치와 상태를 그 slice의 stage 커밋으로 되감기 (`--to` 필수, v0.4.2) |
| `--to` | `--rollback`이 되감을 지점: `requirement` / `plan` / `implement` / `test` |
| `--revert-merge` | merge된 slice를 base에서 철회 — revert 커밋만, reset 없음 (충돌 시 exit 4) |
| `--reason` | 되돌린 이유. `rollbacks.json`에 남는다 (`--rollback` / `--revert-merge` 전용) |
| `--base` | slice가 갈라져 나올 브랜치 (기본: repo의 현재 HEAD, **main 아님**) |
| `--worktree-root` | worktree 부모 디렉터리 (기본 `<repo>-slices`) |
| `--no-worktree` | legacy slice의 `--resume-slice` 전용. 새 slice에서는 거부한다 (v0.8) |
| `--setup-timeout` | front matter setup 명령의 제한 시간 (기본 1800초) |
| `--commit-file-limit` | 단계 커밋이 건드릴 수 있는 최대 파일 수 (기본 2000) |
| `--max-turns` | 모든 단계의 기본 상한 (기본 80) |
| `--max-turns-stage` | 한 단계만 지정, `implement=140` (반복 가능, front matter보다 우선) |
| `--model` | 모든 단계의 기본 모델 |
| `--model-stage` | 한 단계만 지정, `diagnose=claude-haiku-4-5` (반복 가능, front matter보다 우선, v0.5) |
| `--max-repairs` | 검증 실패가 implement를 다시 부를 수 있는 횟수 (기본 1, v0.5) |
| `--diagnose-threshold` | 이 개수를 넘는 실패면 진단 세션을 산다 (기본 3, v0.5) |
| `--verify-timeout` | 검증 명령 하나의 제한 시간 (기본 1800초, v0.5) |
| `--no-verify-engine` | test 단계를 v0.4처럼 에이전트 세션으로 (v0.5) |
| `--no-write-guard` | 기존 파일에 대한 Write 차단을 끈다 (v0.5) |
| `--no-output-diet` | implement에 `aidev verify` 대신 원본 테스트 명령을 준다 (v0.5) |
| `--no-spec-check` | 함수 명세 기계 검사를 끈다 (v0.5) |
| `--no-temp-guard` | 임시파일 이름 기계 검사를 끈다 (오탐 탈출구) |
| `--no-graph` | 단계 커밋 뒤 Function Graph를 stale로 표시하지 않는다 (브리핑도 함께 꺼진다, v0.6) |
| `--no-briefing` | 세션 발사 전 브리핑을 차리지 않는다 (v0.6) |
| `--permission-mode` | implement/test용 (plan은 항상 readonly) |
| `--allow-tool` | implement/test에 추가할 권한 규칙 (반복 가능) |
| `--session-reset-after` | 같은 단계가 비쿼터 실패 N회면 새 session (기본 1) |
| `--poll-interval` | 승인 파일 polling 간격 (기본 3초) |
| `--approval-timeout` | 승인 대기 포기 시간 (기본 0 = 무한 대기) |
| `--quota-wait` | reset 시각을 못 읽을 때의 재시도 간격 (기본 900초) |
| `--quota-max-retries` | 쿼터 재시도 상한 (기본 20) |
| `--no-auto-resume` | 한도 대기에 앉아 있지 않는다 — 시각과 재개 명령만 말하고 끝낸다 (v0.7) |
| `--auto-extend` | 턴 소진에 엔진이 할 수 있는 것: `off` / `conservative`(기본) / `aggressive` (v0.7) |
| `--turn-cap` | 어떤 자동 연장도 단계 예산을 이 값 위로 못 올린다 (기본 300, v0.7) |
| `--dry-run` | slice id / base / 브랜치 / worktree / setup / 검증 명령 / 턴 예산 / 게이트 / 경로만 출력하고 종료 (`--amend`와 함께면 돌 사이클, `--rollback` / `--revert-merge`와 함께면 되돌릴 범위와 복구 명령) |

종료 코드: `0` 완주, `1` 실패, `2` 사용법·전제조건 위반, `3` 거부,
`4` merge 충돌 **및 revert 충돌**, `127` claude 없음.

**Windows 주의.** worktree 경로 + 깊은 `node_modules`는 `MAX_PATH`(260자)에 쉽게
닿는다. `--worktree-root D:\wt`처럼 짧은 경로를 주거나
`git config --global core.longpaths true`를 켠다. `--discard`가 실패하면 대개
에디터·watcher·node가 worktree 안 파일을 잡고 있는 것이다 — 닫고 다시 실행한다.

## Function Graph (v0.6, 1단계)

> 그래프 엔진은 **사람에겐 시선(view), AI에겐 DB(query)** 다.
> 이 단계는 그중 데이터층이다 — 에이전트의 '탐색'을 '조회'로 바꾼다.
> **찾지 않는다, 좌표로 요청한다.**

코드와 명세 주석을 파싱해 함수 단위 DB를 만든다. 진실은 언제나 코드고
DB는 파생물이다. 언제 지워도 잃는 것이 없다.

```bash
aidev graph build --repo .            # 전체 빌드
aidev graph update --repo .           # 변경된 파일만 재파싱
aidev graph status --repo .           # 기준 커밋 / 파일·함수 수 / 커버리지 / 실패 목록

aidev graph show run_pipeline         # 명세 + 시그니처 + 좌표 + 호출/피호출
aidev graph callers merge_slice       # 부르는 곳들 (파일:줄)
aidev graph calls commit_stage        # 부르는 것들
aidev graph summaries --dir aidev     # 범위 안 함수 이름 + 기능 한 줄
```

모든 동사가 `--repo`(기본 현재 디렉터리)와 `--graph-dir`(기본
`<repo>/.aidev/graph`)를 받는다. 출력은 토큰 효율 우선 — 항목당 1~2줄,
좌표는 항상 `경로:줄`. 같은 이름의 함수가 여럿이면 **전부** 나열하고 경로로
구분한다. 엔진이 대신 고르지 않는다.

```
run_pipeline(cfg: PipelineConfig, rec: SliceRecord, ...)  aidev/pipeline.py:3310-3447  py
  The single loop: run the stage if it is not done, then ask about its gate.
  @param cfg  the slice's configuration
  @flow  per stage: dirty check -> run -> commit -> gate
  calls 29: run_stage aidev/pipeline.py:2284 | commit_stage aidev/pipeline.py:2771 | +17 more
  callers 1: _finish aidev/pipeline.py:5355
```

종료 코드: `0` 답했다, `1` 그런 이름이 없다, `2` 물어볼 수가 없다
(그래프 없음 / 다른 스키마 / 파일 잠김).

### 태그 규약 — 닫힌 코어, 열린 주변

**코어 태그 4종**만 도구가 의미를 해석한다: 기능 한 줄 / `@param` / `@flow` /
`@why` — 위 **함수 명세 규약 — 기계 검사**와 같은 형식이다.
그 밖의 `@태그`는 **해석하지 않고 그대로 수집·보존**해서 DB에 넣고 `show`에
원문 그대로 찍고 `status`에 개수를 센다. front matter의 미지 키를 다루는 원칙과
같다 — **태그는 데이터고, 검사는 블록의 일이다.** 커스텀 태그에 검사 규칙을
붙이는 것은 이 단계가 하지 않는다.

Python은 docstring이 없으면 `def` 위의 `#` 블록을 명세로 읽고(명세 검사기와
같은 함수를 쓴다), JS/TS는 선언 위의 `/** ... */` 또는 `//` 연속 블록을 읽는다.
JSDoc의 `@param`이 그대로 코어 태그가 된다.

### 캐시 규약

`.aidev/graph/graph.db` — SQLite 한 파일. 빌드할 때마다 같은 디렉터리에
`.gitignore`(내용 `*`)를 써 두므로 **어떤 repo에서도 커밋되지 않는다.**
`git add -A`로 커밋하는 단계 커밋도, readonly 단계 직전의 청결 검사도 이걸
보지 못한다.

증분 갱신은 `mtime`+크기가 그대로면 읽지도 않고, 달라졌으면 sha256을 재서
해시가 같으면 재파싱하지 않는다. 스키마가 다르면 마이그레이션하지 않고
**버리고 다시 만든다** — 소모품 캐시에 마이그레이션 코드를 쓰는 것은 그것을
소모품이 아니라고 말하는 것이다.

json이 아니라 SQLite인 이유는 두 접근 패턴 모두에서 json이 지기 때문이다.
조회는 이름 인덱스 한 번이면 되는데 json은 전체 문서를 메모리에 올려야 하고,
증분 갱신은 `DELETE ... WHERE path=?` + 수십 행 삽입이면 되는데 json은 매번
전체를 다시 직렬화해야 한다.

### 파서의 한계 (알고 쓰는 것)

의존성을 **추가하지 않았다.** Python은 stdlib `ast`(좌표가 정확하다),
JS/TS는 자체 문자 스캐너다. tree-sitter를 쓰지 않은 이유: 이 도구는 무인 루프에서
임의의 worktree를 돌아다니고, `dependencies = []`라는 것은 설치 실패라는 고장
모드 자체가 없다는 뜻이다. 네이티브 휠 3종은 OS·파이썬 버전마다 새 실패 지점이 된다.

- **JS/TS는 이름 있는 선언만** 잡는다 — `function f`, `const f = () =>`,
  `const f = function`, `class` 본문의 메서드/화살표 프로퍼티. 객체 리터럴 메서드와
  익명 콜백은 잡지 않는다(이름으로 물어볼 수 없는 것들이다). 익명 콜백 안의 호출은
  그것이 쓰인 함수의 것으로 친다.
- 문자열·템플릿·주석·정규식 리터럴을 먼저 공백으로 지운 사본 위에서 중괄호를
  세므로 JSX도 그대로 성립한다. 정규식과 나눗셈의 구분은 휴리스틱이고, 틀리면
  **그 파일 하나만** 실패 목록으로 가고 빌드는 완주한다.
- **호출 관계는 이름 기반 best effort.** 같은 파일에 그 이름이 하나면 그것으로,
  아니면 repo 전체에서 하나일 때만 잇는다. 언어가 다르면 잇지 않는다
  (Python의 `str()`은 TypeScript의 `str`이 아니다). 나머지는 `(unresolved)` /
  `(ambiguous: N)`으로 **모른다고 말한다.**
- 파싱에 실패한 파일은 건너뛰고 `status`의 실패 목록에 사유와 함께 남는다.
- 테스트 파일도 색인한다(테스트도 호출자다). 다만 명세 커버리지 %에서는 빼고
  `status`가 두 숫자를 다 보고한다.

### 파이프라인 훅 — lazy

단계 커밋이 성공하면 `.aidev/graph/dirty` 마커 한 줄을 쓰고 끝난다. **파싱은 하지
않는다.** 아직 어떤 단계도 그래프를 읽지 않으므로 커밋마다 재파싱하는 것은 아무도
안 보는 캐시에 시간을 쓰는 일이다. 대신 커밋 뒤 **첫 조회**가 값을 치르고, 그때도
움직인 파일만 다시 읽는다. 마커 쓰기가 실패해도 경고 한 줄이고 slice는 계속 간다 —
파생 캐시가 파이프라인을 죽일 수는 없다. `--no-graph`면 아무것도 하지 않는다.

## 브리핑 생성기 — 상차림 (v0.6, 2단계)

> 에이전트는 찾지 않는다. 좌표로 요청한다. 좌표는 그래프가 안다.

**왜.** 실측 2026-08-15~18: plan이 계획할 코드를 **찾는 데만** 20~59턴을 썼고,
implement는 같은 파일을 다시 찾느라 slice당 $2~4를 썼으며, 한 세션 안에서 같은
파일을 5~11번 읽었다. 그건 사고가 아니라 상차림이고, 방 안에서 가장 비싼 사람이
매번 다시 차리고 있었다. 그래서 **엔진이 세션 발사 직전에 한 번 차린다** — 이미
갖고 있는 DB에서.

**LOD 3단.** 축척이 큰 것부터, 그 순서 그대로 프롬프트에 들어간다. 앞쪽(저해상)은
같은 커밋이면 단계가 바뀌어도 그대로라 **프롬프트 캐시가 맞고**, 뒤쪽(고해상)은
plan마다 바뀐다.

| 절 | 무엇 | 언제 |
|---|---|---|
| `1. REPO MAP` | 디렉터리별 파일·함수 수 + 피호출 상위 진입점 | 항상 |
| `2. RELATED` | requirement(+plan)의 식별자와 겹치는 함수 + 좌표 | 항상 |
| `3. SCOPE` | plan이 지목한 파일/함수의 명세 전문 + 호출/피호출 + 본문 발췌 | plan 이후 |
| `4. ALREADY EXISTS` | scope와 이름이 겹치는 **기존** 함수 (재발명 방지) | implement |

```
# BRIEFING  implement

graph: 1252 functions, 76 files, spec 70%   (base ce7ccfd, 2026-08-19T13:10:26+09:00)

## 1. REPO MAP   (the whole repository, at a distance)
aidev/                        23 files    610 functions
tests/                        18 files    528 functions
entry points (most called):
  say                     aidev/pipeline.py:3712            142 callers

## 2. RELATED   (what the requirement's words name)
matched on: briefing, graph, prompt, stage, cache
  build_prompt            aidev/pipeline.py:1889            The prompt one stage is given, ...
  +18 more (aidev graph summaries --dir aidev)

## 3. SCOPE   (what the plan names, in full)
aidev/pipeline.py  610 functions indexed   (aidev graph summaries --dir aidev/pipeline.py)

prepare_briefing(cfg, rec, state, stage, requirement, fixed=None)  aidev/pipeline.py:2432-2489  py
  Set the table before the session is launched, and write down what it cost.
  @param cfg  the slice's configuration - the switch and the worktree
  @flow  not ours -> {} ; build (cached or fresh) -> record it in state -> say what it cost
  calls 6: briefing.build aidev/briefing.py:249 | say aidev/pipeline.py:3712
  callers 1: run_stage aidev/pipeline.py:2586
   2432 | def prepare_briefing(

## 4. ALREADY EXISTS   (before you write a new one)
  Briefing.to_meta        aidev/briefing.py:124             The sidecar written beside the md
기존 함수 재사용 우선. 유사 기능 신설 시 사유 명시. 통합 리팩토링은 금지.

---
이외는 aidev graph show/callers/calls/summaries로 요청하라.
파일 통읽기 전에 조회 우선.
```

**마법은 없다.** 매칭은 식별자 부분 문자열이 전부다 — requirement에서 `[A-Za-z_]\w{2,}`
를 뽑아 snake/camel로 쪼개고, 불용어를 빼고, `LIKE %토큰%`으로 이름·qualname·기능
한 줄을 훑는다. **임베딩도 시맨틱 검색도 하지 않는다.** 새벽 세 시에 "왜 이게
브리핑에 들어왔나"를 단어만 보고 설명할 수 없으면 그건 디버깅할 수 없는 브리핑이다.

**상한이 곧 설계다.** 절마다 토큰 상한(900 / 1200 / 2500 / 500)이 있고, 넘으면
**줄 단위로 뒤에서 버린다** — 줄 중간을 자르지 않는다. 좌표가 깨지면 브리핑의 존재
이유가 없어지기 때문이다. 잘린 자리에는 `+N more (aidev graph summaries --dir ...)`
처럼 **나머지를 여는 동사**를 남긴다. 이 저장소 자신(1252 함수) 기준 실측 3619 토큰.

**언제·어디에.** plan / implement 발사 직전(=repair·amend·쿼터 재시도 포함)에 만들고,
`.aidev/slices/<id>/briefings/<stage>.md`에 사이드카(`.json`)와 함께 남긴다.
브랜치에는 싣지 않는다 — 그래프에서 언제든 다시 만들 수 있는 파생물에 단계 커밋마다
수천 토큰짜리 md를 태우는 것은 값을 두 번 치르는 일이다. test 단계는 브리핑하지
않는다(검증 엔진이 켜져 있으면 세션 자체가 없고, 있어도 그 단계의 일은 선언된 명령을
돌리는 것이지 코드를 찾는 것이 아니다).

**신선도와 캐시.** 그래프가 dirty로 표시돼 있으면 **먼저 증분 갱신**하고 차린다.
worktree에 DB가 아예 없으면(gitignore되므로 따라오지 않는다) 전체 빌드를 한 번
치른다. `sha1(stage + HEAD + requirement + plan)`이 같으면 **다시 만들지 않고**
파일을 그대로 재사용한다 — plan digest가 scope의 상위집합이라 "같은 커밋 + 같은
scope"를 정확히 덮고, 게이트에서 사람이 plan.md를 고친 경우도 놓치지 않는다.
커밋을 읽을 수 없으면(비-git) 같음을 증명할 수 없으므로 **재사용하지 않는다.**

**끄는 법 셋.** front matter `briefing: off`, 플래그 `--no-briefing`, 그리고
`--no-graph`(그래프를 아예 안 쓴다는 약속이므로 브리핑도 함께 꺼진다). 껐을 때의
프롬프트는 **바이트 단위로** 이 기능이 없던 때와 같다 — A/B가 두 가지를 한꺼번에
재는 일이 없도록.

**계측.** RESULT에 한 줄:

```
Briefing  plan 1.2k, implement 3.1k tok   reused 1   graph queries 0
```

`runs.json`의 각 세션에 `briefing_tokens`와 `graph_queries`가 함께 남는다.
`graph_queries`는 **지금은 항상 0으로 읽힌다** — 어떤 단계도 `aidev graph`를 실행할
권한이 없기 때문이다(plan은 readonly 프로파일이 Bash 자체를 막고, implement/test가
받는 규칙에 조회 동사가 없다). 이 slice는 권한을 새로 열지 않는다. 열고 싶으면
사람이 직접 준다:

```bash
aidev pipeline --requirement tasks/x.md --allow-tool 'Bash(aidev graph:*)'
```

그 순간부터 이 카운터가 0이 아닌 값을 읽고, 그 값이 **예외 경로 발생률** — 상차림이
빠뜨린 것의 양 — 의 원천이 된다.

**실패해도 slice는 죽지 않는다.** 그래프를 못 읽든 파일을 못 쓰든 경고 한 줄
(`warning: no briefing this stage (...)`)을 찍고 그 단계는 브리핑 없이 발사된다.
`mark_graph_stale`과 같은 원칙이다 — 파생 캐시는 파이프라인을 죽일 자격이 없다.

그래프 화면(사람이 보는 시선)은 3단계다.

## 다른 터미널에서 관찰 (v0.1.2)

```bash
aidev watch              # 현재 RUNNING인 최신 run에 자동으로 붙는다
aidev watch last         # 위와 동일
aidev watch 20260813-22  # 특정 run (prefix 가능)
aidev watch <run-id> --once      # 한 프레임만 찍고 종료
aidev watch --interval 0.5       # 0.3~0.5로 clamp
aidev watch --repo ~/jokertest   # 이 저장소(+그 slice worktree)의 run만 후보 (v0.5)
```

**`last`가 죽은 run을 고르지 않는다**(v0.5). 실측 2026-08-18: **이틀 전에 죽은 run**에
붙었다. 원인 둘 — ① 죽은 러너는 `live.json`을 `running`인 채로 남기는데 선택이 그
`status`만 봤고 ② watch에 저장소 스코프가 아예 없어서 남의 저장소 run도 후보였다.

- 선택 기준이 `status == running` **그리고 최근에 갱신됨**으로 바뀌었다. 무갱신 기준은
  **30분**(`RUN_STALE_AFTER_S`)이다. 표시용 10초(위 STALE)와 **다른 숫자이고 달라야
  한다** — 긴 Bash 호출 하나에 갇힌 세션은 이벤트를 전혀 못 내고, 검증 타임아웃이
  정확히 그만큼(1800초)을 허용한다. 10초로 고르면 살아있는 run을 죽었다고 부른다.
- 순서는 ① 스코프 안의 살아있는 running ② 없으면 stale-running이 아닌 최신 ③ 그래도
  없으면 스코프 안의 최신이다. run이 있는데 "없다"고 하지는 않는다.
- **잴 수 없으면 죽었다고 보지 않는다.** `updated_at`이 없으면 `live.json`의 mtime으로
  대신 재고, 그것도 안 되면 stale 판정을 하지 않는다. 모르는 것과 아는 것은 다르고,
  살아있는 run을 숨기는 쪽이 더 나쁜 실수다.
- **명시한 run id는 언제나 이긴다.** `--repo`와 무갱신 판정은 `last`에만 적용된다.
- `--repo`는 그 저장소와 **그 저장소의 slice worktree**(`<repo>-slices/`)를 함께 본다.
  pipeline 단계의 run은 본진이 아니라 worktree 경로로 기록되기 때문이다. 소속을
  기록하지 않은 run은 스코프에서 **뺀다** — 증명 못 하는 run을 넣는 것이 이 결함의
  재발이다. `--worktree-root`로 경로 규칙 밖에 worktree를 만들었다면 run id를 직접 대야 한다.
- **죽은 run에 붙어도 무한 폴링하지 않는다.** 예전 루프는 `status != running`일 때만
  빠져서 영원히 돌았다. 이제 무갱신을 감지하면 한 줄 보고하고 끝낸다(exit 0 —
  관찰이 실패한 게 아니라 관찰할 것이 끝난 것이다).

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
  `STALE: no update for Ns`로 표시한다. 이건 **표시**용 숫자다. 어느 run에 붙을지
  **고르는** 쪽은 30분을 쓴다(위 `last` 설명) — 다른 질문에 답하는 다른 숫자다.
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

Function Graph는 그 어느 쪽도 아니다. `.aidev/graph/graph.db`는 **코드에서 언제든
다시 만들 수 있는 파생 캐시**라서 진행 상태처럼 따라다닐 필요가 없다. 그래서 같은
디렉터리에 `.gitignore`(`*`)를 함께 써 두고 git에서 통째로 감춘다 — 지워도 잃는
것이 없다는 것이 이 설계의 요점이다 (위
[Function Graph](#function-graph-v06-1단계) 참고).

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

`tests/test_graph.py`는 tmp repo와 **이 repo 자신**을 둘 다 대상으로 돈다.
자기 자신을 빌드해서 `show run_pipeline`이 내놓는 좌표를 실제 소스에서 찾은
`def` 줄과 비교하므로(줄 번호를 하드코딩하지 않는다) 구현이 줄을 밀어도
회귀가 아니고, `desktop/src/**`의 실제 `.ts/.tsx`가 파싱되는지도 같이 본다.
소스 체크아웃이 아니면 skip된다.

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
v0.5  파이프라인 2세대              ← 완료 (엔진 검증 / 실패 문서 규격 / Write 차단 / 명세 검사)
v0.6  Codebase Memory             ← 지금 여기 (1단계: 파서 + Function DB 완료)
v0.7  Pluto IDE 바인딩 (state.json / live.json → window.aidev)
      (v0.2.5에서 상태/plan/승인 3종 선행)
```

v0.5는 새 기능이 아니라 **비용 구조를 고친 것**이다. 실측된 slice당 $15에서 검증
루프가 최대 지출원이었고, 원인 셋(정보 이득 0인 test 세션 / 통짜 테스트 출력 /
증발하는 실패 맥락)을 각각 엔진 실행·요약 래퍼·산출물 규격으로 막았다.
프롬프트 지시는 하나만 늘렸다 — **기계 > 양식 > 지시** 순서다.

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
