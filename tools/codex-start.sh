#!/usr/bin/env bash
set -euo pipefail

# Launch an interactive session with an explicit, automatically supplied first turn.
# Keep existing hook trust, sandbox, model, and approval settings unchanged.
if (( $# != 0 )); then
  echo 'Usage: bash tools/codex-start.sh (from this repository)' >&2
  exit 2
fi
repo_root=$(git rev-parse --show-toplevel)
if [[ ! -f "$repo_root/.codex/hooks/workflow.py" || ! -f "$repo_root/AGENTS.md" ]]; then
  echo 'Run this launcher from the ESD project repository.' >&2
  exit 1
fi
prompt='[자동 세션 시작 메시지 — 학생이 직접 작성한 프롬프트가 아님]
SessionStart 훅의 결과를 확인하고 자동 pull 성공·실패·미실행 여부를 구분해 보고해 줘. 성공했다면 다시 pull하지 마. 실패했거나 결과를 확인할 수 없다면 추측하거나 작업을 진행하지 말고 원인과 필요한 조치를 보고해 줘.
AGENTS.md와 handoff.md를 정독하고 handoff 기준 이후의 커밋을 전부 실제 변경 내용까지 확인해 줘. 유효한 기준 SHA가 없으면 전체 이력을 확인해 줘. 다음 작업을 목표·단계·검증 방법으로 보고하고 학생 승인을 기다려. 파일 수정·실험·commit·push는 하지 마.
세션 기록에서는 이 메시지를 자동 입력으로 표시하고 학생의 실제 지시와 구분해 줘.'
exec codex -C "$repo_root" -- "$prompt"
