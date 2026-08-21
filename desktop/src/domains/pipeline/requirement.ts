/**
 * What a requirement file is called, what a new one starts as, and what
 * committing it says. Pure — no fs, no Electron, no monaco.
 *
 * Same reason `commands.ts` is pure: the editor's buttons and the tests have to
 * read the *same* answer to "is this a requirement path?". So the rule is not
 * restated here — `isRequirementPath` is imported. The launcher and the editor
 * cannot drift apart about `tasks/<name>.md`, because there is only one of it.
 *
 * That single regex is also the whole of the "requirement 전용" guard the
 * requirement asks for: no path with a separator in it, no `..`, no drive
 * letter and no `tasks/specs/…` can be spelled as one, so this editor cannot be
 * turned into a code editor by typing a different name into it.
 */
import { isRequirementPath } from './commands'

/** Where a repository keeps the requirements a human may launch. */
export const TASKS_DIR = 'tasks'

/** `aidev/pipeline.py` MIN_REQUIREMENT_CHARS — a body denser than this launches. */
export const MIN_REQUIREMENT_CHARS = 5

/**
 * What a new `tasks/*.md` opens as: the front matter the pipeline reads, and
 * the four sections every requirement in this repository has.
 *
 * `# test_commands:` is a comment on purpose. `parse_front_matter`
 * (`aidev/pipeline.py`) skips any line starting with `#`, so the example is
 * there to be uncommented and turns nothing on until it is — and it does not
 * count as an unknown key either.
 *
 * LF throughout. `saveRequirement` normalises CRLF back to this on the way out,
 * so a file written on Windows is not a diff of the whole document.
 */
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

/**
 * The name a human typed, as the file will be called. The extension goes on
 * once and is never doubled.
 *
 * The spelling is otherwise left exactly as typed — including a `.MD` this
 * editor will go on to refuse. Quietly rewriting somebody's filename to one
 * that passes is worse than saying which spelling is wanted.
 *
 * @param input  what is in the filename box
 * @flow  trim -> empty stays empty -> already ends in .md (any case) -> as is ;
 *        otherwise append `.md`
 */
export function normalizeRequirementName(input: string): string {
  const name = typeof input === 'string' ? input.trim() : ''
  if (name === '') return ''
  return /\.md$/i.test(name) ? name : `${name}.md`
}

/**
 * `tasks/<name>.md` for a name this editor may write, or null for one it may not.
 *
 * The judgement is `commands.ts`'s, not a second copy of it: whatever the
 * launcher would refuse to launch, this refuses to create. That is what keeps
 * `../x`, `a/b`, `specs/x`, `tasks/x`, `/abs`, `C:\x`, `.hidden` and `-lead`
 * out without a rule of their own.
 *
 * @param input  what is in the filename box
 */
export function requirementPathFor(input: string): string | null {
  const name = normalizeRequirementName(input)
  if (name === '') return null
  const path = `${TASKS_DIR}/${name}`
  return isRequirementPath(path) ? path : null
}

/**
 * Why this name was refused, in one sentence, or '' when it was not refused.
 *
 * Shown under the filename box. Every branch names the actual rule that was
 * broken — a refusal a human cannot act on is the same as a broken editor.
 *
 * @param input  what is in the filename box
 * @flow  accepted -> '' ; empty -> ask for one ; a separator -> this is one
 *        file in tasks/, not a tree ; a `.MD` -> the extension is lowercase ;
 *        anything else -> the character rule
 */
export function requirementNameProblem(input: string): string {
  if (requirementPathFor(input) !== null) return ''
  const name = typeof input === 'string' ? input.trim() : ''
  if (name === '') return '파일 이름을 입력하세요'
  if (name.includes('/') || name.includes('\\')) {
    return 'tasks/ 안의 파일 하나만 — 폴더는 만들 수 없습니다'
  }
  if (/\.md$/i.test(name) && !name.endsWith('.md')) {
    return '확장자는 소문자 .md 여야 합니다'
  }
  return '영문/숫자로 시작하고 . _ - 만 쓸 수 있습니다'
}

/**
 * The commit message a save writes: `task: <파일명>`, without the extension.
 *
 * The stem rather than the whole filename, deliberately. `tasks/` commits in
 * this repository read `task: 소탕 2호 — …`, `task: req 에디터 — …`; a
 * `task: req-editor.md` in that log would be the one line with a file
 * extension in it. It is still the filename, and it still reads.
 *
 * @param path  the repo-relative `tasks/<name>.md` being saved
 */
export function requirementCommitMessage(path: string): string {
  const text = typeof path === 'string' ? path : ''
  const name = text.slice(text.lastIndexOf('/') + 1)
  return `task: ${name.replace(/\.md$/i, '')}`
}
