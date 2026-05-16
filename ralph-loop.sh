#!/usr/bin/env bash
# Ralph loop for this repo: reads tasks.json, sends one pending task at a time
# to pi in print mode, and updates task status after each iteration.
#
# Usage:
#   bash ralph-loop.sh                 # run until all runnable tasks finish
#   bash ralph-loop.sh --once          # run one task
#   bash ralph-loop.sh -n 5            # run at most five tasks
#   bash ralph-loop.sh --tasks file.json -- --model sonnet:high
#
# Environment:
#   PI_BIN=pi                          # pi executable
#   RALPH_PI_ARGS="--model sonnet"      # extra pi args, shell-split
#   RALPH_MAX_ATTEMPTS=1               # retries per task before blocked
#   RALPH_STOP_ON_FAILURE=1            # exit immediately on failed task

set -uo pipefail

TASKS_FILE="${TASKS_FILE:-tasks.json}"
PI_BIN="${PI_BIN:-pi}"
MAX_ITERATIONS="${RALPH_MAX_ITERATIONS:-0}"
MAX_ATTEMPTS="${RALPH_MAX_ATTEMPTS:-1}"
STOP_ON_FAILURE="${RALPH_STOP_ON_FAILURE:-1}"
declare -a PI_EXTRA_ARGS_ARRAY=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --once)
      MAX_ITERATIONS=1
      shift
      ;;
    --tasks)
      if [[ $# -lt 2 ]]; then
        echo "error: --tasks requires a path" >&2
        exit 2
      fi
      TASKS_FILE="$2"
      shift 2
      ;;
    --max-iterations|-n)
      if [[ $# -lt 2 ]]; then
        echo "error: $1 requires a number" >&2
        exit 2
      fi
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --)
      shift
      PI_EXTRA_ARGS_ARRAY+=("$@")
      break
      ;;
    [0-9]*)
      MAX_ITERATIONS="$1"
      shift
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "${RALPH_PI_ARGS:-}" ]]; then
  # Intentionally shell-split so callers can provide normal CLI flags.
  # shellcheck disable=SC2206
  PI_EXTRA_ARGS_ARRAY=(${RALPH_PI_ARGS} "${PI_EXTRA_ARGS_ARRAY[@]}")
fi

if ! [[ "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "error: max iterations must be a non-negative integer" >&2
  exit 2
fi

if ! [[ "$MAX_ATTEMPTS" =~ ^[0-9]+$ ]] || [[ "$MAX_ATTEMPTS" -lt 1 ]]; then
  echo "error: RALPH_MAX_ATTEMPTS must be a positive integer" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required to read and update $TASKS_FILE" >&2
  exit 127
fi

if ! command -v "$PI_BIN" >/dev/null 2>&1; then
  echo "error: pi executable '$PI_BIN' was not found on PATH" >&2
  exit 127
fi

if [[ ! -f "$TASKS_FILE" ]]; then
  echo "error: tasks file not found: $TASKS_FILE" >&2
  exit 1
fi

shell_join() {
  if [[ "$#" -eq 0 ]]; then
    printf '(none)'
    return
  fi

  local quoted=()
  local arg
  for arg in "$@"; do
    printf -v arg '%q' "$arg"
    quoted+=("$arg")
  done
  printf '%s' "${quoted[*]}"
}

print_pi_configuration() {
  local model="(pi default/current settings)"
  local provider=""
  local scoped_models=""
  local thinking="(pi default/current settings)"
  local thinking_explicit=0
  local i arg next

  for ((i = 0; i < ${#PI_EXTRA_ARGS_ARRAY[@]}; i++)); do
    arg="${PI_EXTRA_ARGS_ARRAY[$i]}"
    case "$arg" in
      --model)
        if ((i + 1 < ${#PI_EXTRA_ARGS_ARRAY[@]})); then
          model="${PI_EXTRA_ARGS_ARRAY[$((i + 1))]}"
        fi
        ;;
      --model=*)
        model="${arg#--model=}"
        ;;
      --provider)
        if ((i + 1 < ${#PI_EXTRA_ARGS_ARRAY[@]})); then
          provider="${PI_EXTRA_ARGS_ARRAY[$((i + 1))]}"
        fi
        ;;
      --provider=*)
        provider="${arg#--provider=}"
        ;;
      --models)
        if ((i + 1 < ${#PI_EXTRA_ARGS_ARRAY[@]})); then
          scoped_models="${PI_EXTRA_ARGS_ARRAY[$((i + 1))]}"
        fi
        ;;
      --models=*)
        scoped_models="${arg#--models=}"
        ;;
      --thinking)
        if ((i + 1 < ${#PI_EXTRA_ARGS_ARRAY[@]})); then
          thinking="${PI_EXTRA_ARGS_ARRAY[$((i + 1))]}"
          thinking_explicit=1
        fi
        ;;
      --thinking=*)
        thinking="${arg#--thinking=}"
        thinking_explicit=1
        ;;
    esac
  done

  if [[ "$thinking_explicit" -eq 0 && "$model" =~ :(off|minimal|low|medium|high|xhigh)$ ]]; then
    thinking="${BASH_REMATCH[1]} (from --model)"
  fi

  echo "Pi configuration:"
  echo "  executable: $PI_BIN"
  echo "  model: $model"
  if [[ -n "$provider" ]]; then
    echo "  provider: $provider"
  fi
  if [[ -n "$scoped_models" ]]; then
    echo "  scoped models: $scoped_models"
  fi
  echo "  thinking: $thinking"
  echo "  extra args: $(shell_join "${PI_EXTRA_ARGS_ARRAY[@]}")"
}

json_get() {
  local json="$1"
  local field="$2"
  FIELD="$field" python3 -c 'import json, os, sys; print(json.load(sys.stdin).get(os.environ["FIELD"], ""))' <<<"$json"
}

json_get_task() {
  local json="$1"
  python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)["task"], ensure_ascii=False))' <<<"$json"
}

next_task() {
  RALPH_MAX_ATTEMPTS="$MAX_ATTEMPTS" python3 - "$TASKS_FILE" <<'PY'
import datetime as dt
import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
max_attempts = int(os.environ.get("RALPH_MAX_ATTEMPTS", "1"))
now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

tasks = data.get("tasks")
if not isinstance(tasks, list):
    raise SystemExit("tasks.json must contain a top-level tasks array")

def task_id(task):
    value = task.get("id")
    if not isinstance(value, str) or not value:
        raise SystemExit("every task must have a non-empty string id")
    return value

def is_done(task):
    return task.get("passes") is True or task.get("status") == "done"

completed = {task_id(task) for task in tasks if is_done(task)}

if len(completed) == len(tasks):
    print(json.dumps({"state": "complete"}))
    raise SystemExit(0)

runnable = []
blocked = []
for task in tasks:
    if is_done(task):
        continue
    tid = task_id(task)
    status = task.get("status", "pending")
    attempts = int(task.get("attempts", 0) or 0)
    deps = task.get("depends_on", [])
    if not isinstance(deps, list):
        raise SystemExit(f"task {tid} depends_on must be an array")
    missing = [dep for dep in deps if dep not in completed]
    if missing:
        blocked.append({"id": tid, "missing": missing})
        continue
    if status == "failed" and attempts >= max_attempts:
        blocked.append({"id": tid, "missing": ["retry-attempts-exhausted"]})
        continue
    if status not in {"pending", "running", "failed"}:
        blocked.append({"id": tid, "missing": [f"unsupported-status:{status}"]})
        continue
    runnable.append(task)

if not runnable:
    print(json.dumps({"state": "blocked", "blocked": blocked}, ensure_ascii=False))
    raise SystemExit(0)

def sort_key(task):
    return (int(task.get("priority", 0) or 0), task_id(task))

task = sorted(runnable, key=sort_key)[0]
task["status"] = "running"
task["passes"] = False
task["attempts"] = int(task.get("attempts", 0) or 0) + 1
task.setdefault("started_at", now)
task["updated_at"] = now

tmp = path.with_name(f"{path.name}.tmp")
with tmp.open("w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
tmp.replace(path)

print(json.dumps({"state": "task", "task": task}, ensure_ascii=False))
PY
}

mark_task() {
  local task_id="$1"
  local status="$2"
  local exit_code="$3"
  local reason="$4"

  python3 - "$TASKS_FILE" "$task_id" "$status" "$exit_code" "$reason" <<'PY'
import datetime as dt
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
task_id = sys.argv[2]
status = sys.argv[3]
exit_code = int(sys.argv[4])
reason = sys.argv[5]
now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

tasks = data.get("tasks", [])
for task in tasks:
    if task.get("id") != task_id:
        continue
    task["status"] = status
    task["passes"] = status == "done"
    task["updated_at"] = now
    task["last_exit_code"] = exit_code
    if status == "done":
        task["completed_at"] = now
        task.pop("last_failure", None)
        task.pop("failed_at", None)
    else:
        task["failed_at"] = now
        task["last_failure"] = reason
    break
else:
    raise SystemExit(f"task not found: {task_id}")

tmp = path.with_name(f"{path.name}.tmp")
with tmp.open("w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
tmp.replace(path)
PY
}

build_prompt() {
  local task_json="$1"
  TASK_JSON="$task_json" python3 - <<'PY'
import json
import os

task = json.loads(os.environ["TASK_JSON"])

def bullets(values):
    if not values:
        return "- None"
    return "\n".join(f"- {value}" for value in values)

print(f"""You are a fresh pi coding agent running inside a Ralph loop.

Implement exactly one task from tasks.json.

Task:
- id: {task.get('id')}
- title: {task.get('title')}
- phase: {task.get('phase')}

Objective:
{task.get('objective', '')}

Likely files:
{bullets(task.get('files_hint', []))}

Steps:
{bullets(task.get('steps', []))}

Acceptance criteria:
{bullets(task.get('acceptance', []))}

Suggested checks:
{bullets(task.get('checks', []))}

Loop rules:
- Read PLAN.md and the relevant code/tests before editing.
- Implement ONLY this task. Do not start another task from tasks.json or PLAN.md.
- Do not edit tasks.json or ralph-loop.sh; this loop updates task state.
- Keep changes focused to files needed for this task.
- Preserve path/symlink security behavior.
- Preserve normal Pi project behavior unless this task explicitly changes it.
- Do not commit changes.
- Always use sub-agent to review your changes against acceptance criteria before declaring completion.
- If you cannot complete the task safely, stop and explain why.

Final response marker:
End your final response with exactly one marker line:
TASK_STATUS: complete
or
TASK_STATUS: failed

Use TASK_STATUS: complete only if the acceptance criteria are met and relevant checks were run, or if a check could not be run and you clearly explain why.
""")
PY
}

run_pi() {
  local prompt="$1"
  local output_file="$2"

  echo "--- pi task prompt start ---"
  echo "$prompt"
  echo "--- pi task prompt end ---"

  printf '%s\n' "$prompt" | "$PI_BIN" "${PI_EXTRA_ARGS_ARRAY[@]}" --no-session -p 2>&1 | tee "$output_file"
  return "${PIPESTATUS[1]}"
}

print_pi_configuration

iteration=0
while true; do
  if [[ "$MAX_ITERATIONS" -gt 0 && "$iteration" -ge "$MAX_ITERATIONS" ]]; then
    echo "Reached max iterations: $MAX_ITERATIONS"
    exit 0
  fi

  next_json="$(next_task)" || exit $?
  state="$(json_get "$next_json" state)"

  case "$state" in
    complete)
      echo "COMPLETE: all tasks pass."
      exit 0
      ;;
    blocked)
      echo "BLOCKED: no runnable pending tasks. Details:" >&2
      echo "$next_json" >&2
      exit 1
      ;;
    task)
      ;;
    *)
      echo "error: unexpected task selector state: $state" >&2
      echo "$next_json" >&2
      exit 1
      ;;
  esac

  task_json="$(json_get_task "$next_json")"
  task_id="$(TASK_JSON="$task_json" python3 - <<'PY'
import json, os
print(json.loads(os.environ["TASK_JSON"])["id"])
PY
)"
  task_title="$(TASK_JSON="$task_json" python3 - <<'PY'
import json, os
print(json.loads(os.environ["TASK_JSON"]).get("title", ""))
PY
)"
  prompt="$(build_prompt "$task_json")"
  output_file="$(mktemp "${TMPDIR:-/tmp}/ralph-loop.${task_id}.XXXXXX")"

  echo "==> Ralph iteration $((iteration + 1)): $task_id - $task_title"

  if run_pi "$prompt" "$output_file"; then
    pi_status=0
  else
    pi_status=$?
  fi

  if [[ "$pi_status" -ne 0 ]]; then
    mark_task "$task_id" "failed" "$pi_status" "pi exited non-zero"
    rm -f "$output_file"
    echo "FAILED: $task_id (pi exit $pi_status)" >&2
    exit "$pi_status"
  fi

  if grep -Eq '^TASK_STATUS:[[:space:]]*complete[[:space:]]*$' "$output_file"; then
    mark_task "$task_id" "done" 0 ""
    echo "DONE: $task_id"
  elif grep -Eq '^TASK_STATUS:[[:space:]]*failed[[:space:]]*$' "$output_file"; then
    mark_task "$task_id" "failed" 0 "agent reported TASK_STATUS: failed"
    rm -f "$output_file"
    echo "FAILED: $task_id (agent marker)" >&2
    if [[ "$STOP_ON_FAILURE" == "1" ]]; then
      exit 1
    fi
  else
    mark_task "$task_id" "failed" 0 "missing TASK_STATUS marker"
    rm -f "$output_file"
    echo "FAILED: $task_id (missing TASK_STATUS marker)" >&2
    if [[ "$STOP_ON_FAILURE" == "1" ]]; then
      exit 1
    fi
  fi

  rm -f "$output_file"
  iteration=$((iteration + 1))
done
