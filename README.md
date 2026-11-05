# esd2026-starter — 임베디드시스템설계 기말 프로젝트 시작 템플릿

AI 코딩 에이전트와 함께 Raspberry Pi 프로젝트를 진행할 때 쓰는 **규칙 파일 · 인수인계 · 기록 틀**입니다.
에이전트는 세션이 끝나면 대화를 잊습니다. 그래서 **기억을 파일에 둡니다** — 규칙은 `AGENTS.md`, 다음 할 일은 `handoff.md`,
무슨 일이 있었는지는 회의록과 git 이력에 둡니다. 이 과목의 기말 프로젝트는 **개인 과제**이므로, 저장소 하나를 학생 한 명이 씁니다.

- 튜토리얼: https://yongsoojoo.github.io/esd2026-agent-workflow/ — git · 에이전트가 처음이면 **빠른 시작** (https://yongsoojoo.github.io/esd2026-agent-workflow/start.html) 부터 읽습니다
- 사례 저장소 (이 규칙들이 왜 생겼는지 보여 주는 재구성 사례): https://github.com/yongsoojoo/esd2026-example-beam

## 왜 프롬프트를 전부 기록하나요

세션을 닫을 때 에이전트가 회의록을 쓰고, 그 회의록의 「프롬프트 기록」 에 **학생이 입력한 프롬프트를 전부 원문 그대로** 옮깁니다.
이렇게 남긴 프롬프트는 나중에 「학생이 프롬프트로 논의를 어떻게 끌어갔는가」 를 분석하는 자료가 됩니다 — 계획을 먼저 요구했는지,
근거를 물었는지, 질문으로 방향을 바꿨는지, 에이전트의 가정을 잡아냈는지. 결과물만으로는 알 수 없는 과정을 사후에 분석하고
다음 프로젝트의 개선안을 도출하는 것이 이 형상관리의 목적입니다.

## 빠른 시작

1. GitHub 에서 **Use this template → Create a new repository** 를 누릅니다. 공개 범위와 지도교수 · 조교 초대는 강의 공지를 따릅니다
2. 노트북에 clone 하고, Raspberry Pi 에도 같은 저장소를 clone 합니다
3. `AGENTS.md` §0 을 채우고, `topics/` 에서 자기 주제 파일 하나를 골라 그 내용을 §8 에 붙여 넣습니다
4. 쓰는 에이전트 도구가 규칙을 읽는지 확인합니다 (아래 「규칙 적재 확인」)
5. 첫 세션을 아래 「세션 시작 프롬프트」 로 엽니다. 세션을 닫을 때는 `/session-close` 를 씁니다

## 폴더 구조

```
AGENTS.md                    규칙 원본 — 모든 에이전트가 읽습니다
handoff.md                   다음 세션의 단일 진입점 — 세션 끝마다 덮어씁니다
CLAUDE.md                    Claude Code 용: "@AGENTS.md" 한 줄
.gemini/settings.json        Gemini CLI (API 키 사용자) 용: AGENTS.md 를 읽게 하는 설정
.github/copilot-instructions.md   JetBrains 등의 Copilot Chat 용 안내
.kiro/steering/hw-edit.md    Kiro 용 보조 규칙 (구동 코드를 다룰 때만 적재)
.kiro/steering/session-close.md   Kiro 용 세션 종료 명령 (/session-close)
.claude/commands/session-close.md Claude Code 용 세션 종료 명령 (/session-close)
topics/                      주제별 규칙 3 종 — 하나를 골라 AGENTS.md §8 에 붙여 넣습니다
docs/meeting-notes/          회의록 (TEMPLATE.md) — 세션 종료 때 에이전트가 작성합니다
docs/ai-usage.md             AI 사용 기록 (필수)
docs/writing-guide.md        보고서 작성 가이드
logs/experiments/            실험 기록 (TEMPLATE.md) — 로그 CSV 는 logs/ 아래에 둡니다
hw/limits.py                 구동기 한계 상수 — 이 값들은 이 파일에만 둡니다
tools/check_log.py           로그 무결성 검사 (샘플 수 · 간격 · tick)
tools/prompt_stats.py        회의록 「프롬프트 기록」 통계 — 회고 때 씁니다
```

코드는 `src/` (제어 · 센싱), `hw/` (구동기 드라이버 · 한계), `sim/` (시뮬레이터), `tests/` 에 두기를 권합니다.

## 도구별 설정

| 도구 | 읽는 파일 | 이 템플릿에서 할 일 |
|---|---|---|
| **Kiro** (IDE · CLI) | `AGENTS.md` 를 기본으로 항상 읽음 + `.kiro/steering/*.md` | 저장소를 처음 열 때 **Trust** 를 누릅니다 (누르지 않으면 steering 이 적재되지 않습니다) |
| **Claude Code** | `CLAUDE.md` (→ `@AGENTS.md` 로 원본을 불러옴) | 없음 |
| **Codex CLI** | `AGENTS.md` | 없음 |
| **Gemini CLI** | 기본은 `GEMINI.md` 만 | 이 템플릿의 `.gemini/settings.json` 이 `AGENTS.md` 를 읽게 합니다. 단 2026-06-18 부터 개인 계정은 쓸 수 없고 API 키 인증만 동작합니다 |
| **GitHub Copilot** (VS Code · CLI) | `AGENTS.md` | VS Code 설정 `chat.useAgentsMdFile` 이 켜져 있는지 확인합니다 |
| **Cursor** | `AGENTS.md` | 없음 |
| 웹 채팅만 쓰는 경우 | 없음 | 세션 시작 때 `AGENTS.md` 와 `handoff.md` 를 직접 붙여 넣습니다 |

**Kiro 사용자 유의**
- 학생 프로그램 (Kiro for Students) 계정은 약관상 Free Tier 로 간주되어, 기본 설정에서는 대화 내용이 서비스 개선에 쓰일 수 있습니다.
  원하지 않으면 설정 → User → Application → **Telemetry and Content** 에서 끕니다
- 구동 코드를 다루는 동안에는 **Supervised** 모드를 씁니다 (`AGENTS.md` §3)
- `product.md` · `tech.md` · `structure.md` 를 자동 생성하면 `AGENTS.md` §0 과 내용이 겹칩니다. 생성했다면 둘 중 하나만 남깁니다

## 규칙 적재 확인 (양성 대조)

「규칙 파일을 만들었다」 와 「에이전트가 그 파일을 읽었다」 는 다릅니다. 새 세션을 열고 이렇게 묻습니다.

> 파일을 새로 열지 말고, 이미 받은 지시만으로 답해 줘. AGENTS.md §3 의 1 번 조항 제목은 무엇이야?

도구 호출 (파일 읽기) 없이 「구동 명령은 사람 입회 하에」 라고 답하면 적재된 것입니다. 파일을 읽으러 가거나 모른다고 하면
적재되지 않은 것이므로 위 표의 설정을 다시 확인합니다. 적재 목록을 보여 주는 명령도 있습니다
(Kiro CLI `/context show`, Claude Code `/memory`, Gemini CLI `/memory show`).

## 세션 시작 프롬프트

> handoff.md 와 지난 세션 이후의 git log 를 읽고, 다음 작업을 계획 (목표 / 단계 / 검증 방법) 으로 보고해 줘. 승인 전에는 착수하지 마.

세션을 닫을 때는 Kiro · Claude Code 에서 `/session-close` 를 입력합니다. 다른 도구에서는 이렇게 요청합니다.

> AGENTS.md §1-3 의 종료 절차대로 세션을 닫아 줘. 회의록의 프롬프트 기록에는 이번 세션의 내 프롬프트를 전부 원문 그대로 옮기고, 시각은 date 로 읽어.

에이전트가 쓴 회의록은 반드시 직접 검토합니다 — 프롬프트가 빠지지 않았는지, 시각이 실측값인지, 결정자가 적혀 있는지 봅니다.

## 회의록 보기 — Obsidian

저장소 폴더를 [Obsidian](https://obsidian.md) 의 vault 로 열면 회의록 · 실험 기록 · handoff 를 링크와 백링크로 오가며 볼 수 있습니다.
설정 → Files and links 에서 **Use [[Wikilinks]] 를 끄면** 표준 Markdown 링크로 저장되어 GitHub 에서도 링크가 깨지지 않습니다.
Obsidian 이 만드는 `.obsidian/` 폴더는 `.gitignore` 에 들어 있습니다. 자세한 설정은 튜토리얼의 「시작하기」 에 있습니다.

## 이 템플릿을 만든 과정의 AI 사용

이 템플릿은 지도교수가 AI 코딩 에이전트 (Claude Code) 와 함께 작성하고 검토했습니다. 규칙의 원형은 지도교수 연구실의 실제 연구 프로젝트에서
에이전트와 일하며 생긴 규칙이며, 학부 개인 프로젝트 규모에 맞게 줄였습니다.

## 라이선스

- 문서 (Markdown · HTML 의 본문) 는 [CC BY 4.0](LICENSE-docs), 코드 (Python · CSS · JavaScript · 설정 파일) 는 [MIT](LICENSE) 입니다. Copyright (c) 2026 Yongsoo Joo
- 이 템플릿으로 만든 저장소에서는 `AGENTS.md` 머리말의 「유래」 줄과 README 의 템플릿 링크를 남기는 것으로 출처를 표시할 수 있습니다
