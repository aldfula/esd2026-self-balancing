# 노트북 Codex CLI 형상관리 자동화

공식 문서와 교수님 가이드에 맞춰 구현함. 규칙의 원본은 `AGENTS.md`임.
이 추가 기능은 모터·센서 코드를 실행하지 않음. Linux/macOS/WSL의
Python 3, Git, `date`, OpenSSH 환경을 대상으로 함. Windows 네이티브에서는
`fcntl`과 POSIX 셸을 사용할 수 없으므로 WSL에서 실행함.

## 확인한 근거

- [교수님 개요](https://yongsoojoo.github.io/esd2026-agent-workflow/index.html),
  [설정](https://yongsoojoo.github.io/esd2026-agent-workflow/setup.html),
  [규칙](https://yongsoojoo.github.io/esd2026-agent-workflow/rules.html),
  [세션 운영](https://yongsoojoo.github.io/esd2026-agent-workflow/session.html)을 확인함.
- [공식 Hooks 문서](https://learn.chatgpt.com/docs/hooks)에서
  `.codex/hooks.json`, `hooks → 이벤트 → matcher 그룹 → command` 형식,
  표준입력 JSON, `hookSpecificOutput.additionalContext`를 확인함.
  `SessionStart`의 source는 startup/resume/clear/compact/fork임.
  `UserPromptSubmit`에 prompt와 turn_id가 있으며 matcher는 적용되지 않음.
  `SessionEnd`도 지원되지만 알림용이고 종료 차단·후속 에이전트 작업을
  보장하지 않으므로 여기에 종료 절차를 연결하지 않음.
- [공식 Skills 문서](https://learn.chatgpt.com/docs/build-skills)에서
  `.agents/skills/<이름>/SKILL.md`, `/skills`와 `$이름` 호출,
  `agents/openai.yaml`의 `allow_implicit_invocation: false`를 확인함.
- 구현 환경에서 `codex-cli 0.165.0`을 확인함. 이 버전을 최소 지원 버전으로
  주장하지 않음. 공식 문서 확인과 Python/Git 테스트를 수행했으며,
  로그인된 CLI의 실제 `/hooks` 신뢰·이벤트 전달까지 종단 검증한 것은 아님.

## 최초 설정 — 사용자가 노트북에서 실행

저장소에서 `codex --version`을 확인하고 `codex`를 실행함. 프로젝트를 신뢰한
뒤 `/hooks`에서 두 훅과 실행 파일을 검토하고 신뢰함. 훅 신뢰 우회 옵션은
사용하지 않음. 새 세션에서 `/skills`에 `session-close`가 보이는지 확인함.
`/hooks`가 없거나 이벤트가 실행되지 않으면 자동화가 지원된다고 가정하지
말고 아래 수동 절차를 사용함. 개인 설정 파일을 자동 수정하지 않음.

자동 pull과 프롬프트 저장은 기본 OFF임. 원하는 기능을 사용자가 각각 켬:

```bash
git config --local codexWorkflow.autoPull true
git config --local codexWorkflow.recordPrompts true
```

첫 설정은 이후 startup/resume에서 안전 조건을 만족할 때 pull하는 데 대한
로컬 선택임. 작업 계획·문서화·commit·push 승인과는 별개임. 두 번째 설정은
비밀값 탐지의 한계를 이해하고 로컬 프롬프트 기록을 켜는 선택임.
설정은 `.git/config`에만 남아 다른 기기로 전달되지 않음. 끄려면 true를
false로 바꿈. 공개 저장소에도 키·토큰을 입력하지 않음.

새 세션에서 무해한 여러 줄 프롬프트를 보내고 다음을 확인함:

1. 시작 훅이 Git 상태와 handoff의 이력 기준 유효성을 보고하는지 확인함.
2. `git rev-parse --absolute-git-dir`로 찾은 디렉터리의
   `codex-workflow/<session_id>.jsonl`에 해당 입력과 `+0900` 시각이 있는지 확인함.
3. `AGENTS.md`와 `handoff.md`를 읽은 계획 보고가 나온 뒤 학생이 승인함.
4. 종료할 때 **Codex 채팅 입력창**에 `$session-close`를 입력함.
   셸의 환경변수 명령이나 `/session-close`가 아님.

## 동작과 중단 조건

시작 훅은 untracked 파일을 포함한 dirty tree, 진행 중인 merge/rebase 등,
분리된 HEAD, upstream 누락을 검사함. 이 경우 pull하지 않고 학생에게 해결을
요청함. 자동 stash, reset, clean을 하지 않음. 깨끗한 상태에서 자동 pull을
켰을 때만 해당 저장소 GitHub URL을 확인하고 `pull --ff-only`를 실행함.
Git 설정의 rebase/autostash를 명시적으로 끄고 submodule 재귀와 Git 실행 훅을
비활성화함. 인증은 기존 Git/SSH 환경에 맡기며 비밀번호를 묻거나 저장하지
않음. URL·Git 오류 원문에는 토큰이 들어갈 수 있어 훅 출력에 내보내지 않음.
동시에 시작한 훅은 잠금으로 충돌을 감지함. 다른 프로그램의 동시 Git 수정까지
막는 트랜잭션은 아니므로 동일 checkout에서 동시 작업하지 않음.

compact/clear/fork에서는 pull하지 않고 규칙을 다시 확인하도록 안내함.
이력은 handoff의 유효한 조상 SHA 이후 전부를 확인하도록 지시함. 현재
handoff처럼 SHA가 자리표시이면 전체 이력을 검토하게 함. 훅이 이력을
읽었다는 체크포인트를 임의로 전진시키지 않음. agent가 실제 `git log`와
handoff를 읽어야 함. 실패·동기화 미실행 상태에서 새 작업을 강행하지 않음.

훅은 편의 기능이며 승인 강제 장치가 아님. 꺼진 훅, 신뢰되지 않은 훅,
시간 초과·런타임 오류는 CLI에 따라 작업 자체를 막지 못할 수 있음.
`AGENTS.md`의 승인 절차와 CLI 권한 설정을 함께 유지함. 이 구현에는
stage·commit·push를 실행하는 훅이 없음. 종료 스킬은 결과 승인 후 기록을
준비하고, 정확한 commit 범위 및 push 대상에 대한 실제 학생 승인을 요구함.
승인 대기·인증 실패·push 거절이면 **종료 보류**로 보고함.

## 프롬프트 기록과 보안 경계

로그는 Git 관리 디렉터리에 저장해 일반적인 `git add` 대상에서 제외함.
폴더 권한은 0700, 파일은 0600이며 심볼릭 링크 로그를 거부함. 저장 시점의
`TZ=Asia/Seoul date` 결과를 기록함. 이는 사용자가 키를 누른 시각이 아니라
훅 수신 시각임. session_id별 파일에 추가하고 flock으로 쓰기를 직렬화함.

일반 입력은 공백·줄바꿈을 그대로 보존함. 알려진 GitHub/OpenAI/AWS 키,
개인키 블록, Bearer 값, 비밀번호·token 대입문, 인증 URL, IPv4 패턴은
`[비밀값]`으로 가리고 가림 횟수를 남김. 환경변수·키 파일·전체 transcript를
수집하지 않음. 탐지는 완전하지 않으며 사용자 정의 토큰·개인정보·IPv6 등을
놓칠 수 있음. 반대로 일반 문장을 가릴 수도 있음. **원문 완전 보존과 모든
비밀값 제거를 동시에 보장하지 않음.** 기록을 켜기 전 이 한계를 검토하고,
회의록으로 옮기기 전 사람이 다시 검토함. 로그 자체를 공유하거나 커밋하지
않음. 로컬 보존 기간은 사용자가 정하며 Git 복제·push로 동기화되지 않음.

훅 설치 전 입력, 꺼진 구간, 취소·재시도, 이미지/첨부, 다른 UI의 입력,
합성 continuation은 완전하게 구별·수집하지 못할 수 있음. turn_id는 있으면
보존하되 자동 중복 제거로 실제 입력을 잃지 않도록 함. 회의록에서 채팅과
대조하고 누락·가림·중복 구간을 명시함. 알 수 없는 과거 시각을 만들지 않음.

## 수동 대체 절차와 테스트

훅이 지원되지 않거나 신뢰되지 않았으면 Git 상태 확인 후 학생이 동기화를
승인하고 안전 조건을 확인해 `git pull --ff-only --no-rebase --no-autostash`를
실행함. 이후 전체 관련 이력·AGENTS·handoff를 읽고 계획 승인부터 진행함.
프롬프트는 채팅에서 수동 대조하며 실제 시각이 없는 부분을 명시함.
종료 스킬도 지원되지 않으면 SKILL.md의 절차를 읽도록 명시적으로 요청함.

```bash
python3 -m unittest discover -s tests -p 'test_codex_workflow.py' -v
```

임시 저장소에서 fast-forward, dirty/untracked, 분기, detached HEAD,
upstream 누락, 진행 중 작업, compact, opt-in, URL 제한, 이력 기준,
원문·시각·권한, 가림, 경로/링크 거부, 하위 디렉터리 실행과 오류 출력의
비밀값 비노출을 검증함. 테스트의 push는 임시 로컬 bare 저장소에만 수행함.

## 첫 메시지까지 자동으로 시작하기 (Bash)

`SessionStart`는 내부 지시를 전달함. 첫 응답까지 시작하려면 저장소에서
`bash tools/codex-start.sh`를 실행함. 이 명령이 Codex를 열면서 시작 메시지를
자동 전달하므로 채팅창에 따로 입력할 필요가 없음. 자동 입력은 회의록에서
학생이 직접 작성한 프롬프트와 구분하도록 표시함.

평소처럼 `codex`만 입력하려면 아래 설정을 **한 번만** 수행함:

```bash
# 저장소 루트에서 실행. 같은 줄을 중복 추가하지 않음.
printf '\nsource %q\n' "$(pwd -P)/tools/codex-shell.bash" >> ~/.bashrc
source ~/.bashrc
```

이후 이 checkout과 하위 폴더에서 인자 없이 `codex`를 실행하면 시작 절차를
자동 요청함. 다른 폴더나 `codex resume`, `codex --version`처럼 인자가 있는
명령은 원래 CLI에 전달함. 기존 모델·권한·훅 신뢰 설정을 변경하지 않음.
자동 pull이 실패하거나 결과가 없으면 진행하지 않고 상태를 보고하게 함.
훅이 미실행된 경우에는 `/hooks`의 신뢰·활성 상태부터 확인함.

이미 개인 `.bashrc`에 `ESD CODEX AUTO START` 블록을 설치했다면 그것으로
동일한 동작을 사용할 수 있음. 저장소의 최신 스크립트를 사용하도록 전환할
때는 해당 블록을 제거하고 위 source 줄 하나로 교체함. 두 방식을 중복
설정하지 않음. 해제하려면 해당 source 줄 또는 기존 블록을 제거하고 새
터미널을 엶. 저장소를 이동하면 source 경로도 갱신함. 개인 `.bashrc` 자체는
Git에 포함하지 않음.
