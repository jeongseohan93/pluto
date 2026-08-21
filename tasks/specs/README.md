<!-- 우산 명세 — 직접 발사 금지, 개별 requirement로 분해 발사 -->

# tasks/specs — 우산 명세 보관소

여기 있는 문서는 **직접 발사 금지**다. `--requirement tasks/specs/...` 는
`aidev`가 exit 2로 거부한다 — 규약 문구가 아니라 기계로 막는다.

우산 명세는 한 영역 전체를 서술하는 문서이고, 한 slice가 감당할 크기가 아니다.
2-1 ~ 2-4 처럼 분해해서 `tasks/` 아래 개별 requirement로 두고 쏜다. 분해 자체를
엔진에 맡기려면 `--epic tasks/specs/<파일>` 로 쏘면 된다 (epic 경로는 막지 않는다:
우산을 쪼개는 것이 epic의 일이다).

## 아직 옮기지 못한 것

`functiondb-implement-context-ab.md` — 이 브랜치에 없다. 본진 working tree에
untracked로 있다면 사람이 그쪽에서 옮겨야 한다:

    git mv tasks/functiondb-implement-context-ab.md tasks/specs/

옮긴 뒤 파일 첫 줄에 이 주석을 붙일 것:

    <!-- 우산 명세 — 직접 발사 금지, 2-1~2-4로 분해 발사 -->
