# v0.3 Workspace 격리 — 구현 계획

## 0. 현재 코드에서 확인한 사실 (추측 아님)

| 사실 | 위치 |
| --- | --- |
| 파이프라인이 claude를 띄우는 cwd는 `RunConfig.repo` 하나뿐. `execute_stage`가 `cfg.repo`를 그대로 넘기고, `runner.execute`가 `cwd=str(cfg.repo)`로 Popen한다 | `aidev/pipeline.py:930-947`, `aidev/runner.py:157-167` |
| slice 상태 디렉터리 위치는 `slices_root(repo) = <repo>/.aidev/slices` 하나로 고정 | `aidev/pipeline.py:234-236` |
| git 호출은 `git_porcelain()` 딱 하나. `git status --porcelain -uall`만 쓴다. 커밋·브랜치·worktree 관련 코드는 **존재하지 않는다** | `aidev/pipeline.py:456-477` |
| dirty 거부는 `ensure_clean_repo()`(시작 시)와 `run_pipeline` 루프의 `state["mutated"]` 가드(매 단계 직전) 두 곳 | `aidev/pipeline.py:543-546`, `1259-1263` |
| `dirty_reason()`의 메시지에 이미 "temporary restriction until v0.3 workspace isolation"이 박혀 있다 | `aidev/pipeline.py:526-539` |
| plan 사후 검증은 `repo_changes(cfg.repo, include_slice_files=True)`의 before/after 문자열 비교 | `aidev/pipeline.py:1267`, `1197-1201` |
| `LIVE_STATE_FILES` 예외는 **파이프라인이 stage 도중 직접 쓰는 파일**만 면제한다는 규약 | `aidev/pipeline.py:480-483` |
| `allowed_tools_for(stage, cfg)`가 `--allowedTools`와 프롬프트 문구를 **같은 튜플**에서 만든다 → 여기 붙이면 둘 다 자동 반영 | `aidev/pipeline.py:825-839`, `766-769`, `788-801` |
| `PipelineConfig`는 dataclass, `_config()`가 argparse에서 조립 | `aidev/pipeline.py:807-822`, `1511-1527` |
| `_dispatch()`는 `--list` → `--resume-slice` → `--requirement` 순의 단순 분기 | `aidev/pipeline.py:1493-1508` |
| `state.json` 스키마 상수 `STATE_SCHEMA = 1`, reader는 모르는 키에 관용적이어야 한다는 규약이 README에 명시 | `aidev/pipeline.py:87`, `README.md:158-162` |
| 이 repo의 `.gitignore`에 `.aidev/`는 **없다**. `.claude/`는 통째로 무시된다 | `.gitignore:26` |
| 본진의 `.aidev/slices/<id>/requirement.md`는 v0.2가 **untracked로** 만든다 (`start_slice` → `write_text_atomic`) | `aidev/pipeline.py:1574-1575` |
| 테스트의 `repo` fixture는 git repo가 아니다. `git_repo()` 헬퍼는 일부 안전핀 테스트만 쓴다 | `tests/test_pipeline.py:32-37`, `550-563` |
| 스텁은 호출마다 `argv` / `cwd` / `prompt`를 JSONL로 남긴다 → **cwd로 격리를 증명할 수 있다** | `tests/fake_pipeline_claude.py:109-125` |
| 현재 실행 중인 이 slice(`20260815-v0-3-workspace-isolation`) 자체는 `state.json`에 workspace 키가 없는 v0.2 형태다 | `.aidev/slices/20260815-v0-3-workspace-isolation/state.json` |

---

## 1. 결정 사항 (요구사항이 "plan에서 결정하라"고 한 것들)

### 1.1 상태 파일 위치 = **본진**. 브랜치에는 별도 경로로 스냅샷을 커밋한다.

판단 기준 두 개가 서로 반대 방향을 가리킨다: (a) worktree 폐기 후에도 이력이 남아야 하고, (b) `state.json`의 단일 writer / authoritative source 규약을 깨면 안 된다.

| 후보 | worktree 폐기 후 | 단일 writer | merge 시 |
| --- | --- | --- | --- |
| A. worktree 안에만 둔다 | **전부 소실** | 유지 | 상태파일이 base에 섞임 |
| B. 본진에만 둔다 | 유지 | 유지 | 무관 |
| C. 양쪽에 산다 | 유지 | **깨짐** (writer 2개) | 충돌 |

**결정: B + 커밋용 스냅샷.** 살아있는 상태(`state.json`, `runs.json`, `plan.md`, `approvals/`, `requirement.md`, `.lock`)는 v0.2와 **완전히 같은 자리** — 본진 `<repo>/.aidev/slices/<id>/` — 에 남는다. writer는 여전히 본진에서 도는 파이프라인 프로세스 하나뿐이고, `SliceLock`도 그대로 유효하다.

"커밋에 연결 가능한 slice 메타데이터"는 **읽기 전용 투영**으로 만족시킨다. 각 stage 커밋 직전에 파이프라인이 worktree 안 `.aidev/history/<slice-id>/`에 세 파일을 써서 같이 커밋한다.

```
.aidev/history/<slice-id>/
├── requirement.md   요구사항 원문 (첫 커밋)
├── plan.md          승인 시점 기준 최신 plan (plan stage부터)
└── slice.json       state.json + runs.json의 투영 (stage/status/approval/commit/turns/tokens/cost)
```

- 경로가 `.aidev/slices/`(본진, untracked)와 **다르다.** 같은 경로를 쓰면 `--merge` 때 git이 *"untracked working tree file .aidev/slices/<id>/requirement.md would be overwritten by merge"* 로 죽는다. 본진에 이미 그 파일이 untracked로 있기 때문이다. 이건 반드시 피해야 하는 실제 충돌이다.
- `slice.json`은 파이프라인만 쓰고 아무도 읽지 않는다(도구는 언제나 본진 `state.json`을 읽는다). 즉 authoritative source는 한 개로 유지된다.

### 1.2 본진 dirty 검사 — **완전히 제거하고, 조건을 worktree로 옮긴다**

`git worktree add`는 dirty 본진에서 동작한다. 본진에 대해 실제로 필요한 전제는 다음 네 가지뿐이며, 작업 트리의 청결은 그중 하나도 아니다.

```
1. git repo이고 git이 PATH에 있다               (rev-parse --git-dir)
2. base ref가 커밋으로 해석된다 (unborn HEAD 거부)  (rev-parse --verify <base>^{commit})
3. worktree 목표 경로가 비어 있다                (디스크 + worktree list 양쪽)
4. 브랜치 slice/<slice-id>가 아직 없다
```

**대체 조건 정의 (README에 그대로 쓴다):**

| v0.2 | v0.3 |
| --- | --- |
| 시작 시 본진 dirty면 거부 | 본진 dirty는 **무관**. AI가 본진을 건드리지 않으므로 검사 자체가 성립하지 않는다 |
| `mutated`가 false인 동안 매 단계 직전 재검사 | **readonly stage(plan) 직전에만 worktree가 clean한지 검사.** 더러우면 거부 |
| — | mutating stage 직전엔 검사 없음. worktree는 AI의 샌드박스다 |
| — | `--merge` 시에만 본진 clean을 요구한다 (tracked 변경 없음). merge가 본진을 실제로 쓰는 유일한 순간이기 때문 |

readonly 직전 검사를 남기는 이유는 두 가지다. plan의 before/after 비교가 깨끗한 기준선을 요구하고, `test_resume_after_a_readonly_violation_does_not_launder_it`이 지키던 성질(위반이 다음 실행에서 세탁되지 않는다)을 격리 후에도 유지해야 하기 때문이다.

### 1.3 명령 형태 — `pipeline --merge <id>` / `--discard <id>` (요구사항 문구 그대로)

별도 서브커맨드(`aidev merge`)도 검토했으나 기각한다: 세 동작이 `--repo` 해석, `find_slice()` 매칭 규칙, `SliceLock`, `state.json` 갱신을 전부 공유한다. `_dispatch()`(`pipeline.py:1493`)에 두 분기를 추가하는 쪽이 중복이 없고, `--dry-run` / `--data-dir` 처리도 그대로 물려받는다.

### 1.4 setup 선언 방식

front matter에 한 줄. **파이프라인이 직접 1회 실행**하고, 같은 명령을 allowedTools에도 얹는다.

```markdown
---
approval: plan
setup: npm ci --prefix backend
---
```

- 미선언 → `setup` 키 없음 → 프로세스 0개, allowedTools 변화 0개. (**무동작의 정의**)
- 실행 시점: implement stage 직전 1회. 성공 기록은 `state["setup"]["status"] = "done"`이라 resume해도 재실행하지 않는다.
- 실패 → slice failed. 환경이 깨진 채 implement를 태우지 않는다.
- 셸 없이 실행한다(`shell=False`). `&&`, `|`, `;`, `>`, `<`, backtick이 들어오면 **거부**한다 — test 프롬프트가 이미 "한 개의 평문 명령"을 요구하는 것과 같은 규율이고, 셸을 열면 front matter 한 줄이 임의 스크립트가 된다.

---

## 2. 신규 파일 — `aidev/workspace.py`

git plumbing 전부를 여기 모은다. `pipeline.py`는 이미 1700줄이고, worktree/커밋/merge를 인라인하면 관리가 안 된다. 여기 있는 함수는 pipeline 지식이 없다(SliceRecord/state를 모른다) — 순수 git 래퍼 + 전제조건 검사다.

```python
"""Git worktree isolation: the pipeline's own workspace, never the user's checkout."""

class GitError(RuntimeError):        # argv / returncode / stderr를 들고 있다
    ...

@dataclass
class WorktreeEntry:                 # `git worktree list --porcelain` 한 항목
    path: Path
    branch: Optional[str]            # refs/heads/x -> "x"
    head: Optional[str]
    prunable: Optional[str]          # 잔해 사유 (예: "gitdir file points to non-existent location")
    locked: bool

@dataclass
class Workspace:                     # state.json에 그대로 직렬화된다
    path: Path
    branch: str
    base: Optional[str]              # base 브랜치명. detached HEAD면 None
    base_commit: str
    created_at: str
    def to_dict(self) -> Dict[str, Any]
    @classmethod
    def from_dict(cls, data) -> Optional["Workspace"]   # 깨진/없는 값이면 None
```

함수 (전부 `subprocess.run`, `shell=False`, `universal_newlines=True`, timeout):

| 함수 | 하는 일 |
| --- | --- |
| `git(repo, *args, check=True)` | stdout 반환. 실패 시 `GitError`. `-c commit.gpgsign=false` 등 공통 `-c`는 `_base_args()`에서 붙인다 |
| `git_available()` | `shutil.which("git")` |
| `is_git_repo(path)` | `rev-parse --git-dir` |
| `current_branch(repo)` | `symbolic-ref --quiet --short HEAD`. detached면 None |
| `resolve_commit(repo, ref)` | `rev-parse --verify <ref>^{commit}`. 없으면 None |
| `branch_exists(repo, name)` | `show-ref --verify --quiet refs/heads/<name>` |
| `list_worktrees(repo)` | `worktree list --porcelain` 파싱 → `List[WorktreeEntry]` |
| `porcelain(repo)` | `status --porcelain -uall` (기존 `pipeline.git_porcelain`을 여기로 옮기고 pipeline은 위임) |
| `is_clean(repo, tracked_only=False)` | 위 결과 판정 |
| `worktree_add(repo, path, branch, start_commit)` | `worktree add -b <branch> <path> <commit>` |
| `worktree_remove(repo, path)` | `worktree remove --force <path>` |
| `commit_all(worktree, message, allow_empty=True)` | `add -A` → 스테이징 파일 수 확인 → `commit --no-verify [--allow-empty] -m <msg>` → 새 HEAD sha 반환 |
| `staged_count(worktree)` | `diff --cached --name-only` 줄 수 (커밋 폭발 가드용) |
| `head_commit(repo)` / `log_subjects(repo, rev_range)` | 검증·요약·테스트용 |
| `merge_branch(repo, branch, message)` | `merge --no-ff --no-edit -m <msg> <branch>`. 실패 시 `diff --name-only --diff-filter=U`로 충돌 목록을 **먼저 캡처한 뒤** `merge --abort` → `MergeResult(ok=False, conflicts=[...])` |
| `delete_branch(repo, name)` | 삭제 전 sha를 읽어두고 `branch -D` → sha 반환 (reflog 복구 안내용) |
| `identity_args(repo)` | `user.name`/`user.email`이 없을 때만 `-c user.name=aidev -c user.email=aidev@localhost`를 반환하고 호출부가 한 번 알린다 |

전제조건 / 잔해:

```python
DEFAULT_ROOT_SUFFIX = "-slices"
def default_worktree_root(repo: Path) -> Path:        # <repo>.parent / "<repo.name>-slices"

@dataclass
class WorkspacePlan:
    path: Path; branch: str; base: Optional[str]; base_commit: str

def plan_workspace(repo, slice_id, base=None, root=None) -> WorkspacePlan
    # base 미지정 -> 본진의 현재 HEAD. main을 절대 가정하지 않는다.
    #   branch = current_branch(repo)  (detached면 None)
    #   base_commit = resolve_commit(repo, base or "HEAD")
def blocking_reasons(repo, plan) -> List[str]         # 비어 있으면 생성 가능
def stale_worktrees(repo) -> List[WorktreeEntry]      # prunable/locked. 경고만 한다
def create(repo, plan) -> Workspace
def verify(repo, ws) -> Optional[str]                 # resume 시: 경로 존재 + 등록됨 + 브랜치 일치
```

`blocking_reasons`가 만드는 거부 사유 (요구사항: "이미 존재하면 실행 거부 + 사유 출력"):

```
worktree path already exists: C:\...\joker-slices\20260816-doctor
    (registered to branch slice/20260816-doctor, prunable: gitdir file points to non-existent location)
    aidev never reuses or cleans a worktree. Remove it yourself, or pass --worktree-root.
branch already exists: slice/20260816-doctor  (at 1a2b3c4)
```

`stale_worktrees`는 **다른 경로의** 잔해(조커의 Orca 12개)를 목록으로 찍기만 하고 진행을 막지 않는다. 청소는 절대 하지 않는다 — `git worktree prune`은 어떤 경로에서도 자동 실행하지 않고, 필요한 경우 사람이 칠 명령으로 출력만 한다.

---

## 3. `aidev/pipeline.py` 변경

### 3.1 상수 / 스키마

```python
STATE_SCHEMA = 2                       # 87행. reader는 1도 계속 받는다
SLICE_BRANCH_PREFIX = "slice/"
HISTORY_DIR = ".aidev/history"         # 브랜치에 커밋되는 스냅샷 (본진 .aidev/slices/와 다른 경로)
REQUIREMENT_STAGE = "requirement"      # 첫 커밋의 <stage> 이름
COMMIT_MESSAGE = "slice({0}): {1}"     # 형식 고정
MAX_COMMIT_FILES = 2000                # 커밋 폭발 가드 (아래 3.6)
SETUP_TIMEOUT_S = 1800.0
EXIT_CONFLICT = 4                      # merge 충돌 전용 종료 코드
```

### 3.2 `PipelineConfig` (807-822행)

```python
    workspace: Optional["workspace.Workspace"] = None   # None = in-place(구 slice / --no-worktree)
    setup_command: Optional[str] = None
    setup_timeout: float = SETUP_TIMEOUT_S
    commit_file_limit: int = MAX_COMMIT_FILES

    @property
    def cwd(self) -> Path:
        """Where Claude actually runs. The worktree when isolated, the repo otherwise."""
        return self.workspace.path if self.workspace else self.repo
```

`cfg.repo`는 **본진 고정**이다. 다음을 전부 `cfg.cwd`로 바꾼다:

- `execute_stage`: `runner.RunConfig(repo=cfg.cwd, ...)` (930행), `Telemetry(repo=str(cfg.cwd), ...)` (948-957행)
- `run_pipeline`: readonly 검증의 `repo_changes(cfg.cwd, include_slice_files=True)` (1267행, 1197행)

`cfg.repo`로 **남는** 것: `project=cfg.repo.name`(930/948행 — 여기까지 worktree 이름으로 바꾸면 `aidev stats`가 slice마다 쪼개진다), `slices_root(cfg.repo)`, 모든 git 명령의 대상.

### 3.3 front matter — setup

```python
_SHELL_METACHARS = ("&&", "||", "|", ";", ">", "<", "`", "$(")

def resolve_setup(fields: Dict[str, str]) -> Optional[str]:
    """The one command this slice may run before implement. Absent means: run nothing."""
    # 빈 값('setup:')은 approval과 같은 이유로 에러: 조용히 꺼지는 것보다 낫다
    # 메타문자 포함 시 PipelineError

def split_command(command: str) -> List[str]:
    """Windows keeps its backslashes; POSIX gets POSIX quoting."""
    # shlex.split(command, posix=(os.name != "nt")) + nt에서 토큰 양끝 따옴표 제거

def setup_tool_rules(command: str) -> Tuple[str, ...]:
    """('Bash(npm ci --prefix backend)', 'Bash(npm ci:*)') - 정확형 + 접두형."""
    # 접두형은 앞 2토큰(없으면 1토큰). TEST_COMMAND_TOOLS와 같은 '둘 다 넣는' 규율
```

`allowed_tools_for`(825-839행)는 `TEST_COMMAND_TOOLS + setup_tool_rules(cfg.setup_command) + cfg.allow_tools` 순으로 합친다. 중복 제거 로직은 그대로. plan은 여전히 `()` — setup을 선언해도 plan은 열리지 않는다.

### 3.4 setup 실행

```python
def run_setup(cfg, rec, state) -> Optional[str]:
    """Run the declared setup command once, in the workspace. Returns a failure reason."""
    # state["setup"]["status"] == "done"이면 즉시 반환 (resume 시 재실행 금지)
    # argv[0]을 shutil.which로 해석 (Windows에서 npm -> npm.cmd)
    # cwd=cfg.cwd, shell=False, timeout=cfg.setup_timeout
    # stdout+stderr를 rec.dir/"setup.log" (본진)에 기록, 마지막 20줄을 실패 사유에 싣는다
    # state["setup"] = {command, status, exit_code, ran_at, log}
```

호출 위치: `run_pipeline` 루프에서 `stage == "implement"`이고 그 stage가 아직 done이 아닐 때, `run_stage` 직전. 실패하면 `fail_slice`.

### 3.5 브랜치 스냅샷 + stage 커밋

```python
def mirror_history(cfg, rec, state) -> None:
    """Project the slice's own record into the worktree, so the commit carries it."""
    # <cwd>/.aidev/history/<slice-id>/requirement.md | plan.md | slice.json
    # slice.json = {slice_id, status, base, branch, stages:{status,approval,commit,run_id},
    #               runs:[{stage,attempt,run_id,turns,tokens,cost_usd}], updated_at}
    # write_text_atomic / write_json_atomic 재사용

def commit_stage(cfg, rec, state, stage) -> Optional[str]:
    """One commit per stage, made by the pipeline - never by the agent."""
    # workspace가 None이면 아무것도 하지 않고 None (구 slice / --no-worktree)
    # mirror_history() -> staged_count 가드 -> commit_all(message=COMMIT_MESSAGE.format(id, stage))
    # 반환 sha를 stage_entry(state, stage)["commit"], state["commits"][stage]에 기록
```

`--no-verify`를 쓰는 이유: 이 커밋들은 사람의 작업물이 아니라 기계적 스냅샷이고, 대상 repo의 pre-commit 훅 하나가 무인 루프를 통째로 세우는 걸 막는다. 사람이 검토하는 지점은 `--merge`이고 거기서는 훅을 우회하지 않는다. README에 명시한다.

### 3.6 `run_pipeline` (1245-1317행) 재배선

단계 순서만 바꾸고 구조는 유지한다.

```
for stage in stage_order(state):
    if entry.status != done:
        readonly = STAGE_POLICY[stage].safety_profile == "readonly"
        if cfg.workspace:
            if readonly:
                dirty = repo_changes(cfg.cwd, include_slice_files=True)   # 여기가 유일한 dirty 게이트
                if dirty: return fail_slice(... "workspace has uncommitted changes before "
                                                "readonly stage '<stage>'" ...)
        else:
            <v0.2 경로 그대로: state["mutated"] 가드 + dirty_reason(cfg.repo)>
        before = repo_changes(cfg.cwd, include_slice_files=True) if readonly else None
        if stage == "implement":
            reason = run_setup(cfg, rec, state)          # 미선언이면 즉시 None
            if reason: return fail_slice(rec, state, reason)
        run = run_stage(...)
        ... (실패 처리 전부 기존 그대로) ...
        reason = finish_stage(cfg, rec, state, run, before)
        if reason: ... 기존 그대로 ...
        commit_stage(cfg, rec, state, stage)             # 성공한 stage에 대해서만
        rec.write_state(state)
    if stage in gates: <기존 승인 로직 그대로>
```

- 실패한 stage는 커밋하지 않는다. 남은 변경은 worktree에 그대로 있고, 다음 성공 stage의 커밋에 함께 실린다(사람이 merge 때 diff로 본다).
- `commit_stage`가 스테이징하는 파일이 `commit_file_limit`을 넘으면 **커밋하지 않고 slice를 실패시킨다.** 사유에 상위 디렉터리별 개수를 찍는다. `.gitignore`가 없는 프로젝트에서 setup이 만든 `node_modules`가 통째로 브랜치에 실려 merge 때 본진을 오염시키는 사고를 막는 실측 기반 가드다.
- `state["mutated"]`는 격리 모드에서 더 이상 게이트가 아니지만, 값은 계속 기록한다(구 slice 호환).

### 3.7 `finish_stage`, 프롬프트

`finish_stage`(1182-1215행)는 로직 변경 없음. plan의 before/after 비교는 대상만 worktree로 바뀐다 — **`.aidev/` 면제 규약은 그대로 유지한다.** 격리 덕분에 forge 공격(승인 파일 위조)은 애초에 본진에 닿지 못하지만, worktree 안에서 `.aidev/`에 쓰는 행위 자체는 여전히 readonly 위반으로 잡혀야 한다.

`_IMPLEMENT_PROMPT` / `_TEST_PROMPT`(693-746행)에 두 줄만 추가:

```
- You are in an isolated git worktree on branch {branch}, created from {base}. It is
  yours: the user's checkout is somewhere else and you must not look for it.
- Do not commit, do not branch, do not touch git state. The pipeline commits each
  stage for you when it finishes.
```

(둘째 줄은 기존 문장 확장. `build_prompt`에 `branch`/`base` 인자를 추가하고 `run_stage`가 `cfg.workspace`에서 채운다. workspace가 없으면 문장 자체를 생략한다 — `_ALLOWED_NOTE`와 같은 방식.)

### 3.8 `start_slice` (1541-1582행)

```
1. requirement 해석 / front matter (approval + setup) 파싱          [기존]
2. slice_id 결정, SliceRecord 준비                                   [기존]
3. --dry-run: 아래 항목까지 출력하고 종료 (부작용 0)
       slice / repo / base / branch / worktree / setup / stages / gates / slice dir
4. 격리 전제조건:
       git 없음 or repo 아님 -> PipelineError (exit 2), "--no-worktree로 v0.2
       in-place 동작을 강제할 수 있다"고 안내
       plan_workspace(repo, slice_id, args.base, args.worktree_root)
       blocking_reasons() 비어있지 않으면 PipelineError (사유 전부 출력)
       stale_worktrees() 있으면 경고 출력 (진행은 계속)
5. rec.ensure(); 본진에 requirement.md / state.json 기록             [기존]
       state["workspace"] = ws.to_dict(); state["schema"] = 2
6. workspace.create() -> worktree + 브랜치 slice/<id>
7. mirror_history() + commit_all("slice(<id>): requirement")  -> state["commits"]["requirement"]
8. _finish(...)                                                      [기존]
```

`ensure_clean_repo(repo)` 호출(1569행)은 **삭제**한다. 4번의 전제조건 검사가 그 자리를 차지한다.

실패 시 정리: 6번 성공 후 7번이 실패하면 worktree/브랜치가 반쯤 남는다. 이 경우 만들어진 것을 **되돌린다**(우리가 방금 만든 것이므로 잔해 청소 금지 조항에 걸리지 않는다) — `worktree_remove` + `delete_branch` 후 원래 오류를 올린다. 되돌리기까지 실패하면 두 경로를 모두 출력한다.

### 3.9 `resume_slice` (1585-1612행)

```
state["workspace"] -> Workspace.from_dict()
  None (v0.2 slice)  -> in-place로 계속. "note: legacy slice, running in <repo> (no worktree)" 출력
  존재               -> workspace.verify(repo, ws)
        경로 없음 / 등록 안 됨 / 브랜치 불일치 -> PipelineError (exit 2)
        "worktree for <id> is gone (<path>). Re-create it yourself, or --discard the slice."
        (조용히 재생성하지 않는다 — 재생성하면 그동안의 커밋 위치가 달라진다)
```

이 분기가 **현재 실행 중인 이 slice**를 살린다. `20260815-v0-3-workspace-isolation`의 `state.json`에는 workspace 키가 없으므로, 새 코드로 resume해도 v0.2 in-place 경로로 계속 돈다.

### 3.10 `--merge` / `--discard`

```python
def merge_slice(args, repo, data_dir) -> int:
    rec = find_slice(repo, args.merge)
    state = rec.read_state()  # 없으면 에러
    ws = Workspace.from_dict(state.get("workspace"))
    # 거부 조건 (전부 사유를 출력하고 exit 2):
    #   ws is None                       -> "slice <id> has no workspace (v0.2 slice)"
    #   state["status"] != STATUS_DONE   -> 현재 status를 그대로 보여준다
    #   ws.base is None                  -> "base was a detached HEAD; merge it yourself"
    #   worktree가 dirty                 -> 커밋 안 된 작업을 조용히 버리지 않는다
    #   본진 current_branch != ws.base   -> "checkout <base> first" (본진 브랜치를 우리가 바꾸지 않는다)
    #   본진에 tracked 변경 있음          -> "commit or stash first" (merge가 유일하게 본진을 쓰는 순간)
    with SliceLock(rec):
        result = workspace.merge_branch(repo, ws.branch, COMMIT_MESSAGE.format(id, "merge"))
    # 성공: state["merge"] = {at, base, commit, branch}; status -> "merged"
    #       다음 할 일로 --discard 명령줄을 출력한다 (worktree는 우리가 지우지 않는다)
    # 충돌: 이미 abort되어 본진은 merge 이전 상태 그대로.
    #       충돌 파일 목록 + 사람이 칠 수동 명령을 출력하고 EXIT_CONFLICT(4)
```

```python
def discard_slice(args, repo, data_dir) -> int:
    rec = find_slice(repo, args.discard); ws = Workspace.from_dict(...)
    with SliceLock(rec):
        worktree_remove(repo, ws.path)        # --force: node_modules 등 untracked 파생물
        sha = delete_branch(repo, ws.branch)  # -B로 사라진 sha를 출력 -> reflog로 복구 가능
    # state["discard"] = {at, branch, commit: sha}; status -> "discarded"
    # 출력: 지운 브랜치와 sha, "본진에 남은 흔적: 없음 (slice 기록은 <rec.dir>에 남는다)"
```

- `worktree remove`가 실패하면(파일 잠금 — Windows에서 node_modules/에디터가 잡고 있으면 흔하다) **브랜치는 지우지 않고** 중단한다. 순서를 뒤집으면 브랜치 없는 worktree라는 더 나쁜 잔해가 남는다.
- 등록만 남고 디렉터리가 이미 없으면 `git worktree prune`을 **자동으로 돌리지 않고** 사람이 칠 명령으로 안내한다("청소는 사람").
- 본진 `.aidev/slices/<id>/`는 남긴다. 요구사항의 "본진 무흔적"은 git 이력(worktree·브랜치·커밋) 기준이고, 같은 요구사항이 "worktree 폐기 후에도 slice 이력은 남아야 한다"고 못박았기 때문이다. 출력에서 이 구분을 한 줄로 설명한다.

### 3.11 인자 (`add_parser`, 1428-1482행) / `_dispatch`

| 새 인자 | 의미 |
| --- | --- |
| `--base <branch>` | base 브랜치 명시. 기본 = 본진 현재 HEAD (**main을 가정하지 않는다**) |
| `--worktree-root <path>` | worktree 부모 디렉터리. 기본 `<repo>.parent/<repo.name>-slices` |
| `--no-worktree` | v0.2 in-place 동작. 경고 배너를 찍는다("the AI will edit your checkout directly") |
| `--merge <slice-id>` | base에 merge |
| `--discard <slice-id>` | worktree 제거 + 브랜치 삭제 |
| `--setup-timeout <s>` | 기본 1800 |
| `--commit-file-limit <n>` | 기본 2000 |

`_dispatch` 분기 순서: `--list` → `--merge` → `--discard` → `--resume-slice` → `--requirement`. 두 개 이상 동시 지정은 사용법 오류(exit 2).

### 3.12 `render_summary` (1337-1403행)

표 아래에 블록을 추가한다(기존 열 폭/포맷은 건드리지 않는다 — `test_summary_counts_every_attempt_not_just_the_last`가 `$0.0400`과 `Try`를 문자열로 본다).

```
Workspace C:\...\joker-slices\20260816-doctor
Branch    slice/20260816-doctor  (base: windows-handoff-20260808 @ 1a2b3c4)
Commits   requirement 9f1c2ab  plan 3d4e5f6  implement 77aa88b  test c0ffee1
Setup     npm ci --prefix backend  (done, exit 0)

Next      aidev pipeline --repo <repo> --merge 20260816-doctor
          aidev pipeline --repo <repo> --discard 20260816-doctor
```

`list_slices`(1691-1713행)는 **손대지 않는다.** 열 폭 검증 테스트가 있고, worktree 정보는 목록의 관심사가 아니다.

---

## 4. 그 외 파일

| 파일 | 변경 |
| --- | --- |
| `aidev/__init__.py:9` | `__version__ = "0.3.0"` + 모듈 docstring에 v0.3 한 줄 |
| `pyproject.toml:7` | `version = "0.3.0"`, description에 workspace isolation 추가 |
| `aidev/cli.py` | **변경 없음.** `pipeline.add_parser(sub)`(91행)가 새 인자를 그대로 가져간다 |

---

## 5. README 갱신 (`README.md`)

1. 7-9행 머리말: v0.3 = Workspace 격리. 현재 0.3.0.
2. `## Slice Pipeline` 다이어그램(84-96행)에 worktree 생성 / requirement 첫 커밋 / stage 커밋 / merge·discard를 넣는다.
3. **새 절 `### Workspace 격리 (v0.3)`** — 핵심 원칙 인용, 명령 3종, base가 main이 아니라 현재 HEAD라는 것, 브랜치 명명, 커밋 메시지 형식, 잔해 거부 정책(자동 청소 없음).
4. **206-210행의 "v0.3 worktree 격리 전까지의 임시 제한"을 대체한다.** 표로 v0.2 조건 → v0.3 조건을 나란히 쓰고(§1.2), 본진 dirty가 왜 더 이상 조건이 아닌지(AI가 본진에 닿지 않는다), 대신 무엇이 조건인지(worktree 경로/브랜치 여유, readonly 직전 worktree clean, merge 시 본진 clean + base 체크아웃)를 명시한다. — **Done Criteria 항목**
5. 143-167행 저장 위치: 본진 `.aidev/slices/`(살아있는 상태) vs 브랜치 `.aidev/history/`(커밋되는 스냅샷) 구분과 그 이유(merge 시 untracked 충돌 회피, 단일 writer 유지).
6. setup front matter 예시와 "미선언 시 무동작", 셸 메타문자 거부, allowedTools에 얹힌다는 것.
7. 옵션 표(242-255행)에 신규 인자 7개, 종료 코드(257행)에 `4` 충돌 추가.
8. 로드맵(549-556행): v0.3 완료, `← 지금 여기`를 v0.4로.
9. Windows 주의: 긴 경로(`--worktree-root`로 짧은 경로 지정, `core.longpaths`), `worktree remove` 실패 시 파일 잠금.
10. 본진의 `.claude/settings.local.json`은 gitignore되므로 **worktree에 따라오지 않는다** — 파이프라인이 `--allowedTools`를 명시적으로 넘기므로 파이프라인 동작에는 영향이 없지만, 사람이 본진 local settings에 의존하고 있었다면 달라지는 지점이라 적어둔다.

---

## 6. 검증

### 6.1 기존 테스트 처리 (전량 통과가 Done Criteria)

`repo` fixture(`tests/test_pipeline.py:32-37`)를 **git repo로 바꾼다.** 초기 브랜치 이름을 `windows-handoff-20260808`으로 만들어(`git init -b`가 없는 구버전 대비 `git symbolic-ref HEAD` 또는 `git checkout -b`) **"base가 main이 아닌 브랜치에서 동작"을 전체 스위트가 상시 증명**하게 한다. git이 없으면 skip(기존 `git_repo()`와 같은 규약). `argv()` 헬퍼에 `--worktree-root <tmp_path>/wt`를 넣어 Windows 경로 길이를 짧게 유지한다.

이렇게 하면 아래를 제외한 나머지 테스트는 **수정 없이** 격리 경로를 그대로 통과한다(상태 파일이 본진에 남기 때문에 `state_of` / `slice_dir` / `runs.json` 단언이 전부 유효하다).

| 테스트 | 처리 | 이유 |
| --- | --- | --- |
| `test_dirty_repo_is_refused` (566행) | **역전** → `test_dirty_repo_is_no_longer_refused`: 본진에 미커밋 파일을 두고도 완주하고, 그 파일이 그대로 남아있음을 확인 | v0.3이 명시적으로 없애는 동작 |
| `test_repo_going_dirty_during_a_gate_stops_implement` (788행) | **역전** → 게이트 도중 사람이 본진을 편집해도 implement가 정상 진행되고 본진 편집물은 무사함 | 같은 이유. 격리의 요점 |
| `test_resume_after_a_readonly_violation_does_not_launder_it` (808행) | 유지, 대상만 worktree로 | §1.2의 readonly 직전 검사가 같은 성질을 보장 |
| `test_plan_cannot_forge_its_own_approval` (769행) | 강화: (a) 여전히 readonly 위반으로 실패, (b) 본진 `approvals/plan.md`가 손대지지 않음 | 스텁 forge 모드는 worktree에 approvals 경로가 없으므로 `os.makedirs` 후 쓰도록 고친다(`fake_pipeline_claude.py:177-181`) |
| `test_clean_git_repo_runs_and_its_own_state_is_not_dirt` (578행) | 유지 + 본진 HEAD sha 불변 단언 추가 | |
| `test_allowed_tools_for_grants_only_the_command_stages` (166행) | 유지 (`PipelineConfig` 신규 필드는 전부 기본값) | |

`tests/test_cli.py` / `test_runner.py` / `test_telemetry.py` / `test_storage.py` / `test_reporter.py` / `test_events.py` / `test_watch.py`는 무변경.

### 6.2 신규 — `tests/test_workspace.py` (git plumbing 단위)

- `list_worktrees` 파싱: 정상/`prunable`/`locked`/detached 항목.
- `plan_workspace`: base 미지정 → 현재 HEAD 브랜치(`windows-handoff-...`), `--base` 지정 → 그 브랜치, 없는 브랜치 → 에러, unborn HEAD → 에러.
- `blocking_reasons`: 경로 선점 / 등록된 잔해 / 브랜치 선점 각각에서 사유 문자열.
- `commit_all`: 메시지 그대로, 빈 변경에서도 `--allow-empty`, 반환 sha == `head_commit`.
- `merge_branch`: fast-forward 아닌 merge 커밋 생성 / 충돌 시 `conflicts` 목록 + 본진 HEAD·작업트리 원상복구 확인.
- `delete_branch`가 sha를 돌려주고 실제로 사라짐.
- `split_command` / `setup_tool_rules`: POSIX·Windows 각각, 메타문자 거부.

### 6.3 신규 — `tests/test_pipeline.py` 시나리오 (fake_pipeline_claude 기반)

| 테스트 | 단언 |
| --- | --- |
| `test_one_command_creates_the_workspace_and_finishes` | worktree 디렉터리 존재, 브랜치 `slice/<id>`, `git log --format=%s`가 정확히 `slice(<id>): requirement/plan/implement/test` 4개(첫 커밋에 `.aidev/history/<id>/requirement.md` 포함), 스텁 호출 3건의 `cwd`가 전부 worktree, 본진 HEAD sha 불변, 본진 status에 `.aidev/slices/` 외 신규 항목 없음 |
| `test_base_defaults_to_head_not_main` | 본진에 `main`과 `windows-handoff-20260808`를 두고 후자에 체크아웃 → slice 브랜치의 merge-base가 후자. `main`은 전혀 참조되지 않는다 |
| `test_base_flag_selects_another_branch` | `--base main`이 실제로 main에서 갈라짐 |
| `test_existing_worktree_path_is_refused` | 목표 경로에 파일을 미리 만들고 실행 → exit 2, 메시지에 경로 + "already exists", 스텁 호출 0, slice 디렉터리 미생성 |
| `test_registered_stale_worktree_is_refused_not_reused` | 같은 경로에 진짜 worktree를 미리 `git worktree add` → 거부, 그 worktree의 파일이 그대로 남아있음(청소 안 함) |
| `test_existing_slice_branch_is_refused` | 브랜치만 선점 → 거부 |
| `test_stale_worktrees_elsewhere_are_reported_not_cleaned` | 다른 경로 잔해 2개 → 경고 출력에 두 경로가 나오고, 실행은 완주하며, 잔해는 그대로 |
| `test_requirement_is_the_first_commit_not_a_dirty_file` | 첫 커밋 트리에 requirement가 있고, 커밋 직후 worktree가 clean |
| `test_stage_commits_carry_the_slice_metadata` | 각 stage 커밋의 `.aidev/history/<id>/slice.json`에 그 시점 stage status/승인/run_id/토큰이 들어있고, `state["stages"][x]["commit"]`이 실제 sha와 일치 |
| `test_setup_runs_once_before_implement` | `setup:` 선언 → 명령이 worktree cwd에서 1회 실행(파일 생성으로 확인), implement/test의 `--allowedTools`에 두 규칙 포함, resume해도 재실행 없음 |
| `test_no_setup_declared_runs_nothing` | 선언 없음 → 프로세스 0, allowedTools가 v0.2와 동일 |
| `test_setup_failure_stops_before_implement` | 실패 종료 코드 → slice failed, implement 스텁 호출 0, `setup.log` 존재 |
| `test_setup_rejects_a_shell_chain` | `setup: npm ci && rm -rf /` → exit 2 |
| `test_merge_lands_on_a_non_main_base` | 완주 후 `--merge` → base 브랜치가 slice 커밋들을 포함(`git branch --contains`), merge 커밋 메시지 확인, state `merged` |
| `test_merge_conflict_aborts_and_reports` | base와 slice가 같은 파일을 다르게 고침 → exit 4, 충돌 파일명 출력, 본진 HEAD·작업트리 원상, 브랜치 살아있음 |
| `test_merge_refuses_when_not_on_base_or_slice_unfinished` | 각 전제조건별 exit 2 |
| `test_discard_leaves_no_git_trace` | `--discard` → worktree 디렉터리 없음, `git worktree list`에 없음, `git branch --list slice/*` 비어있음, 본진 status에 신규 tracked 변경 없음, `state["status"] == "discarded"`, slice 디렉터리는 남아있음 |
| `test_legacy_slice_without_workspace_resumes_in_place` | workspace 키 없는 state.json을 만들어 resume → 본진에서 실행되고 v0.2 dirty 규칙이 살아있음 |
| `test_non_git_repo_is_refused_with_a_way_out` | git 아닌 디렉터리 → exit 2 + `--no-worktree` 안내. `--no-worktree`로는 완주 |
| `test_commit_file_limit_refuses_a_node_modules_explosion` | `--commit-file-limit 2`로 축소 후 stage가 파일 3개 생성 → 커밋 없이 failed, 사유에 개수 |
| `test_dry_run_prints_the_workspace_plan_and_touches_nothing` | worktree/브랜치/base/setup 출력, worktree 미생성, slice 디렉터리 미생성 |

### 6.4 스텁 (`tests/fake_pipeline_claude.py`)

- `forge` 모드: `.aidev/slices/*/approvals` 글롭이 worktree에서는 비므로 `.aidev/slices/<any>/approvals`를 **만들어서** 쓰도록 변경(179-181행). 그래야 "위조를 시도했지만 본진에 닿지 못했고 readonly 검사에도 걸린다"를 둘 다 증명한다.
- `touch` 모드: 그대로. cwd가 worktree라 자동으로 worktree를 더럽힌다.
- 신규 `AIDEV_FAKE_FILES=<n>`: implement 단계에서 파일 n개 생성(커밋 파일 수 가드 테스트용).
- setup 명령은 스텁이 아니라 `sys.executable -c ...`로 직접 준다(경로 백슬래시가 `split_command`의 Windows 분기를 실제로 태운다).

### 6.5 실행

```powershell
pytest -q                      # 전량
pytest -q tests/test_workspace.py tests/test_pipeline.py
```

Windows(현 환경)에서 전량 통과가 기준이다. `git`이 없는 환경에서는 workspace 계열이 skip된다.

### 6.6 실전 (사람 손, 코드 외)

조커 본진에서 요구사항 하나를 `--base windows-handoff-20260808`로 완주시키고, `--merge`까지 확인한다. 조커에는 Orca 잔해 worktree 12개가 있으므로 §6.3의 `test_stale_worktrees_elsewhere_are_reported_not_cleaned`가 실전에서 그대로 재현되는지(경고만 나오고 진행되는지) 본다.

---

## 7. 위험과 대응

| 위험 | 왜 생기나 | 대응 |
| --- | --- | --- |
| **merge가 본진의 untracked 파일과 충돌** | 본진 `.aidev/slices/<id>/requirement.md`가 untracked인데 브랜치가 같은 경로를 tracked로 만들면 git이 merge를 거부한다 | 브랜치 쪽 경로를 `.aidev/history/<id>/`로 분리(§1.1). 이게 이 설계에서 가장 중요한 한 가지다 |
| **node_modules가 브랜치에 실려 merge로 본진 오염** | setup이 만든 파생물 + `git add -A`, `.gitignore`가 부실한 repo | `commit_file_limit` 가드(기본 2000)로 커밋 전에 실패 |
| **Windows 경로 길이(MAX_PATH)** | `<repo>-slices/<긴 slice-id>/` + 깊은 node_modules | `--worktree-root`로 짧은 경로 지정 가능. README에 `core.longpaths` 안내. 테스트는 `tmp_path/wt` 사용 |
| **`worktree remove` 실패** | 에디터·watcher·node 프로세스가 파일을 잡고 있음(Windows에서 흔함) | 실패를 삼키지 않고 브랜치를 남긴 채 중단, 사유 출력. 사람이 정리 후 재실행 |
| **git identity 없음 / gpg 서명 / pre-commit 훅이 무인 커밋을 막음** | CI·새 머신 | `identity_args()`로 없을 때만 `aidev <aidev@localhost>` 주입(1회 고지), `-c commit.gpgsign=false`, 파이프라인 커밋에 `--no-verify`. merge는 우회하지 않는다 |
| **setup이 임의 명령 실행 통로가 된다** | front matter는 사람이 쓰는 파일이지만 결국 문자열 | 셸 없음, 메타문자 거부, 1회 제한, 타임아웃, 로그 보존, 미선언 시 완전 무동작 |
| **본진 브랜치를 우리가 바꿔버림** | merge가 base 체크아웃을 요구 | 체크아웃을 **우리가 하지 않는다.** base가 체크아웃돼 있지 않으면 거부하고 사람에게 명령을 알려준다 |
| **구 slice 깨짐** | schema 1 state.json에 workspace 키 없음 | `Workspace.from_dict` → None → in-place 경로. 현재 돌고 있는 이 slice가 첫 번째 검증 대상 |
| **자기 자신을 대상으로 도는 중** | 이 slice는 v0.2 코드로 본진에서 실행 중이다 | implement는 이미 임포트된 모듈을 바꿀 뿐 실행 중 루프에 영향이 없다. 다만 **본진이 dirty해진 상태에서 test 단계가 돈다** — 격리 경로의 실제 증명은 6.3의 신규 테스트와 6.6의 새 slice가 담당한다 |
| **plan.md를 게이트 중에 사람이 고쳐도 plan 커밋엔 안 들어감** | 커밋이 stage 종료 직후라서 | 매 커밋마다 `mirror_history()`가 최신 plan.md를 다시 투영하므로 다음 stage 커밋(implement)에 수정본이 실린다. README에 한 줄로 명시 |
| **`.claude/settings.local.json`이 worktree에 없다** | gitignore된 파일은 worktree에 따라오지 않는다 | 파이프라인은 `--allowedTools`를 명시 전달하므로 영향 없음. README에 동작 차이로 기록(복사는 하지 않는다 — 사람 파일을 우리가 옮기지 않는다는 v0.2 규약 유지) |

---

## 8. 완료 보고 (v0.2와 동일한 RESULT 형식)

implement/test 완료 후 최종 메시지에: 변경 파일 목록, 신규 테스트 수와 `pytest` 결과, Done Criteria 8항목 체크(코드 밖 항목인 "조커 실전 완주"는 사람 확인 대기로 표시), 남은 위험.
