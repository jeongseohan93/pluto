# process identity — lock 신원 강화와 안전 종료 (2-1.5)

## 0. 실측: 요구가 전제한 것과 실제 코드의 차이

읽고 확인한 사실(추측 아님):

- `SliceLock`(`aidev/pipeline.py:1337-1375`)은 `.lock`에 `"pid {n}\nsince {iso}\n"` 두 줄만 쓴다. 생존 검사도, 인수도 없다. `FileExistsError`면 무조건 거부(`PipelineError` → exit 2)이고 "지우려면 이 경로" 안내만 낸다.
- **`--stop`은 존재하지 않는다.** `add_parser`(`pipeline.py:6032-6282`)에도 `_dispatch`의 `modes`(`pipeline.py:6321-6333`)에도 없다. `detach` 발사도 없다 — `tasks/background-run.md`는 아직 **미착수 과제 문서**다(repo 전체 grep: `--stop`/`--detach`는 `tasks/*.md` 안에서만 나온다).
- 따라서 요구 3의 "`--stop` 강화"와 요구 5의 "기존 detach·`--stop` 테스트 통과"는 **전제가 아직 없다**. 이 slice가 `--stop`을 신원 게이트가 달린 형태로 **새로 만든다**. 통과 유지해야 할 "기존 테스트"는 실재하는 두 개다: `tests/test_pipeline.py:1253` (`test_a_second_process_cannot_run_the_same_slice`, exit 2 + lock 해제 확인), `tests/test_epic.py:397` (에픽 lock 해제 확인).
- `.lock`의 **내용**을 읽는 코드는 `SliceLock._holder` 하나뿐이다. desktop(TS)은 slices 디렉터리를 훑으며 파일을 무시하고, `LIVE_STATE_FILES`(`pipeline.py:1584`)는 이름만 쓴다. 그러므로 lock **형식** 변경의 파급은 `pipeline.py` 안에 갇힌다 (파일 위치·이름은 그대로 — 요구의 "하지 않는 것" 준수).
- lock을 잡는 레코드는 둘이다: `SliceRecord`(`pipeline.py:1043`)와 `EpicRecord`(`aidev/epic.py:63`, `pipeline.SliceLock(erec)` at `epic.py:844`). 둘 다 `dir = <repo>/.aidev/{slices|epics}/<id>`, `label` 프로퍼티를 가진다 → repo는 `rec.dir.resolve().parents[2]`로 유도 가능.
- 의존성은 **없다**(`pyproject.toml:15` `dependencies = []`), Python ≥3.10. 즉 `psutil` 금지 — 생성 시각은 표준 라이브러리로 직접 얻어야 한다.
- 새 함수에는 spec 주석이 필수다(`run_spec_check`, `pipeline.py:3822`). 새 모듈의 모든 함수는 이 저장소 문체의 docstring(`@param`/`@flow`)을 단다.

## 1. 설계

### 1.1 새 모듈 `aidev/procid.py` (표준 라이브러리만)

`pipeline`을 import하지 않는 잎 모듈이다(순환 방지). `pipeline`은 `from . import (... procid ...)`(`pipeline.py:53-66`의 알파벳 순서 자리)로 가져온다.

**신원 4요소 + POSIX 그룹**

```python
@dataclass(frozen=True)
class Identity:
    pid: int
    start: Optional[str]   # 플랫폼별 생성 시각 토큰 (불투명 문자열)
    label: str             # slice_id / epic_id
    repo: str              # normcase(resolve(repo))
    pgid: Optional[int]    # POSIX만. 자기 그룹 리더면 pid와 같다
    since: str             # 사람이 읽는 시각 (판정에 쓰지 않는다)
```

**생성 시각 조회 `process_start(pid) -> Optional[str]`**

| 플랫폼 | 방법 |
| --- | --- |
| Windows | `ctypes`: `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION=0x1000, False, pid)` → `GetProcessTimes` → creation `FILETIME`을 64비트 정수 문자열로. 실패코드 `ERROR_INVALID_PARAMETER(87)`은 "없는 pid", `ERROR_ACCESS_DENIED(5)`는 "살아있으나 확인 불가" |
| Linux | `/proc/<pid>/stat`을 마지막 `)` 뒤로 자르고 토큰 19번(=field 22, starttime) |
| 그 외 POSIX(macOS 포함) | `ps -p <pid> -o lstart=` 를 `subprocess.run(timeout=5)`으로. 초 단위 문자열을 그대로 토큰으로 |

어떤 경로든 예외·타임아웃·빈 출력은 `None`이다. **crash 금지**가 이 함수의 유일한 불변식이다.

**생존 `process_alive(pid) -> Optional[bool]`** — `True`/`False`/`None`(모름). POSIX는 `os.kill(pid, 0)`(`ProcessLookupError`→False, `PermissionError`→True), Windows는 `OpenProcess` 결과(87→False, 5→True, 성공→True).

**직렬화** — `encode(identity) -> str`은 `{"schema":1,"pid":…,"start":…,"label":…,"repo":…,"pgid":…,"since":…}` 한 줄 JSON. `decode(text) -> Optional[Identity]`는 JSON이 아니거나 `pid`가 정수가 아니면 `None`(= 구형/미상). 구버전 `aidev`가 이 파일을 읽어도 `_holder()`는 줄을 이어붙일 뿐이라 깨지지 않는다.

**판정 `inspect(lock_path, expected) -> Holder`** — 상태는 여섯 가지, 순서대로:

| 상태 | 조건 | 자동 인수 | 자동 종료 |
| --- | --- | --- | --- |
| `absent` | 파일 없음 | — | — |
| `unknown` | 파일이 비었음(생성 중일 수 있다) | 거부 | 거부 |
| `legacy` | JSON 아님 / pid 없음 (구형 형식) | 거부 | 거부 |
| `foreign` | label 또는 repo가 이 레코드와 다름 | 거부 | 거부 |
| `stale` | pid가 죽었음 **또는** 살아있는데 start 토큰이 기록과 다름(pid 재사용) | **허용** | 거부 |
| `unknown` | pid는 살아있으나 기록 start가 `None`이거나 현재 start를 못 읽음 | 거부 | 거부 |
| `live` | pid 생존 + start 일치 + label 일치 + repo 일치 | 거부 | **허용** |

`Holder`는 `status`, `identity`, `text`(사람이 읽는 한 줄), `detail`(안내문에 붙일 사유)을 담는다.

**그룹 판정** `owns_group(identity)` = `os.name != "nt" and identity.pgid is not None and identity.pgid == identity.pid`.

**종료 `terminate(identity) -> str`** — 무엇을 했는지 한 줄로 돌려준다.
- POSIX: `owns_group`일 때만 `_killpg(pgid, signal.SIGTERM)`. 아니면 `TerminateRefused` 예외 → 호출자가 안내로 바꾼다. 부모 pid 단독 시그널은 **구현하지 않는다**(요구 4).
- Windows: `_taskkill = subprocess.run(["taskkill","/PID",str(pid),"/T","/F"], timeout=30, capture)`. Windows에는 정중한 그룹 시그널이 없으므로 트리 강제 종료가 유일하게 고아를 안 남기는 수단이다. 비영점 종료코드는 stderr를 담아 예외.
- `_killpg`/`_taskkill`은 모듈 최상위 이름으로 두어(`_sleep = time.sleep`, `pipeline.py:229`과 같은 관용구) 테스트가 monkeypatch로 가로챈다.

### 1.2 `SliceLock` (pipeline.py:1337)

- `__init__(self, rec, repo=None)` — `repo=None`이면 `Path(rec.dir).resolve().parents[2]`로 유도하고, `IndexError`면 `""`(빈 repo끼리는 서로 일치). 기존 6개 호출부(`7439`, `7680`, `7790`, `7951`, `8008`, `epic.py:844`)는 **고치지 않는다**.
- `_take()` — `O_EXCL` 생성 후 같은 fd에 `procid.encode(self.identity)`를 쓴다. `FileExistsError`면 `False`. 원자적 생성은 그대로 유지된다(`write_json_atomic`의 replace는 O_EXCL을 무너뜨리므로 쓰지 않는다).
- `__enter__` — `_take()` 실패 → `procid.inspect()`.
  - `stale`이면 `say("taking over a lock whose owner is gone: …")` **한 줄**을 남기고 unlink 후 `_take()` 한 번 더. 그 재시도가 또 지면(레이스) 거부한다.
  - 그 외 상태는 전부 거부: 첫 줄은 지금과 같은 `"{label} is already running ({holder})."`를 유지하고, 그 아래에 상태별 안내를 붙인다.
    - `live`: `stop it: aidev pipeline --repo <repo> --stop <label>`
    - `legacy`: `이 lock에는 프로세스 신원이 없다(구형 형식) — 자동 인수도 자동 종료도 하지 않는다. 직접 확인한 뒤 지워라: <path>`
    - `unknown`/`foreign`: 사유를 밝히고 같은 수동 안내.
- `__exit__` 불변.

### 1.3 `--stop` (신설)

- `add_parser`: `cmd.add_argument("--stop", default=None, metavar="SLICE", help="stop the process that holds this slice's lock, only if its recorded identity still matches")` (`--discard` 옆).
- `_dispatch`: `modes` 튜플에 `("stop", "--stop")` 추가 → 다른 명령과 조합 시 기존 거부 로직이 그대로 먹는다. 분기는 `--discard` 다음에, 마지막 "one of …" 문구에도 `--stop`을 넣는다.
- 새 함수 `stop_slice(args, repo, repo_given=True, data_dir=None) -> int`:
  1. `find_slice(...)`로 레코드 해석(에픽은 이번 범위 밖 — 없으면 slice 기준 안내).
  2. `procid.inspect(rec.dir/".lock", procid.current_identity(rec.label, repo))`.
  3. `absent` → `"not running"` + exit 0. `stale` → `"not running — lock은 pid N을 가리키지만 그 프로세스는 없다(또는 재사용됐다). 다음 실행이 자동으로 인수한다."` + exit 0, **kill 없음**.
  4. `legacy`/`unknown`/`foreign` → `PipelineError`(exit 2) + 수동 정리 안내(경로 명시).
  5. `live` → POSIX인데 `owns_group`이 아니면 exit 2로 거부(`이 프로세스는 독립 process group으로 발사되지 않았다 — 그 터미널에서 직접 ^C 하라`). 아니면 `procid.terminate()` 실행, 무엇을 보냈는지 출력, exit 0. lock은 **지우지 않는다** — 죽는 프로세스의 `__exit__`이 지우고, 못 지웠으면 다음 실행이 `stale`로 인수한다. 그 사실을 출력에 한 줄 적는다.

### 1.4 README

`README.md:1241-1244`의 "slice 하나당 프로세스 하나" 문단을 실제 동작으로 갱신한다: lock이 신원 4요소를 기록한다는 것, 죽은/재사용된 pid는 자동 인수된다는 것, `--stop`은 신원이 전부 맞을 때만 종료한다는 것, 구형 lock은 손대지 않고 안내만 한다는 것.

## 2. 검증

`python -m pytest -q` (요구 front matter). 새 파일 `tests/test_procid.py`는 `test_epic.py`가 하듯 `test_pipeline`에서 fixture(`repo`, `claude_bin`, `log`, `argv`, `slice_dir`, `git_repo`)를 import한다.

Done Criteria 대응:

| 테스트 | 확인 |
| --- | --- |
| `test_a_lock_records_the_identity_of_the_process_that_took_it` | `.lock` JSON의 pid/start/label/repo가 실제 값과 일치, POSIX에서만 pgid |
| `test_this_process_can_read_its_own_start_time` | `process_start(os.getpid())`가 `None`이 아니고 두 번 호출해도 같다 |
| `test_a_reaped_child_is_not_alive` | `subprocess.run`으로 끝내고 회수한 pid → `process_alive` False |
| `test_the_same_lock_is_refused_while_its_owner_lives` | 같은 프로세스가 두 번 → exit 2, lock 파일 유지 |
| `test_a_reused_pid_is_taken_over` | pid=자기 자신, start만 조작한 lock → `inspect`=stale, `SliceLock` 진입 성공 + 인수 로그 1줄 + lock이 내 신원으로 다시 쓰였다 |
| `test_a_dead_owner_is_taken_over` | 죽은 pid의 lock → 인수 성공 |
| `test_a_legacy_lock_is_never_taken_over` | `"pid 1\nsince …"` → 거부(exit 2), 안내에 경로 포함, **파일 그대로** |
| `test_stop_terminates_on_a_full_identity_match` | 살아있는 자식의 진짜 신원으로 lock 작성 → `--stop`이 `_killpg`/`_taskkill`을 그 pid(POSIX는 pgid)로 정확히 1회 호출, exit 0 |
| `test_stop_refuses_a_reused_pid` | start만 다른 lock → kill 호출 0회, exit 0, "인수 가능" 안내 → 이어서 `SliceLock` 인수 성공 |
| `test_stop_refuses_a_legacy_lock` | exit 2, kill 0회, 수동 정리 안내 |
| `test_stop_says_nothing_is_running_without_a_lock` | exit 0 |
| `test_stop_cannot_be_combined_with_another_command` | `--stop x --merge y` → exit 2 "cannot be combined" |
| `test_stop_refuses_a_process_that_does_not_lead_its_group` (POSIX 전용) | pgid≠pid인 lock → exit 2, kill 0회 |
| `test_stop_really_ends_a_detached_child` | 자기 세션으로 띄운 `python -c "time.sleep(30)"`에 진짜 `--stop` → 10초 내 사망. `finally`에서 무조건 정리 |

기존 회귀: `tests/test_pipeline.py:1253`(재진입 거부 exit 2 + lock 해제)과 `tests/test_epic.py:397`은 손대지 않는다. 전자는 **같은 pid·같은 start**로 재진입하므로 `live`로 판정되어야만 통과한다 — 이 테스트가 "자기 자신을 stale로 오판하지 않는다"의 감시자 역할을 겸한다.

## 3. 위험과 대응

- **pid 재사용을 실제로 만들 수 없다.** 기록된 start 토큰을 바꿔 모사한다(요구도 "재사용 모사"라고 쓴다). 판정 코드가 보는 것과 같은 필드를 건드리므로 검증력은 동일하다.
- **Windows 접근 거부**: 다른 계정의 pid면 start를 못 읽는다 → `unknown` → 인수·종료 모두 거부, 안내만. 보수적 실패가 기본값이다.
- **생성-기록 사이의 창**: `O_EXCL` 생성 직후 JSON을 쓰기 전에 다른 프로세스가 읽으면 빈 파일이 보인다 → `unknown`(인수 거부). 기존 코드에도 있던 창이며, 이 변경으로 "빈 파일이면 인수한다" 같은 위험이 새로 생기지 않는 쪽을 택했다.
- **POSIX 좀비**: 회수되지 않은 자식은 `os.kill(pid,0)`에 살아있다고 나온다 → `live`로 남는다. 파이프라인은 lock 소유자를 자기 자식으로 띄우지 않으므로 실사용 영향은 없고, 테스트에서는 반드시 `wait()`로 회수한다.
- **repo 경로 비교**: `resolve()` + `os.path.normcase()`로 정규화한다. Windows 8.3 단축 경로나 심볼릭 링크로 서로 다른 문자열이 나오면 `foreign`으로 떨어져 **거부**된다 — 오작동이 아니라 안전한 실패이고, 안내문에 양쪽 경로를 찍어 사람이 판단할 수 있게 한다.
- **레코드 밖에서 만든 `SliceRecord`**(테스트가 `SliceRecord(tmp_path, "x")`처럼 쓰는 경우) → `parents[2]`가 없다 → repo `""`. 기대값도 `""`라 일치하므로 판정은 정상 동작한다.
- **Windows는 `/F` 강제, POSIX는 SIGTERM 요청**이라는 비대칭이 남는다. Windows에 정중한 그룹 시그널이 없어서이며, SIGKILL 승격은 넣지 않는다(요구 4의 취지).
- **범위 이탈 금지**: `--apply-review`, detach 발사, lock 경로·이름 변경, UI는 건드리지 않는다. `--stop`은 신원 게이트를 증명하는 데 필요한 최소 표면으로만 만든다.

## 작업 지시서

| 동사 | 대상 경로 | symbol | 책임 |
| --- | --- | --- | --- |
| CREATE | aidev/procid.py |  | 프로세스 신원 기록·생존/동일성 판정·안전 종료 |
| CREATE | tests/test_procid.py |  | 신원 기록, stale 인수, 구형 lock 거부, --stop 종료 |
| MODIFY | aidev/pipeline.py |  | SliceLock 신원화와 stale 인수, --stop 플래그·분기·stop_slice |
| MODIFY | README.md |  | lock 규약 문단을 신원·인수·--stop으로 갱신 |
| REFERENCE | aidev/epic.py |  | 같은 lock을 쓰는 두 번째 레코드 모양 |
| REFERENCE | tests/test_pipeline.py |  | 재사용할 fixture와 통과 유지할 기존 lock 테스트 |
