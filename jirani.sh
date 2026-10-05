#!/usr/bin/env bash
# Jirani prototype: one script for everything. No sudo needed.
#   bash jirani.sh help
set -u
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

VENV=.venv
PY="$VENV/bin/python"
METHOD=""
UV=""

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

load_env() { if [ -f .env ]; then set -a; . ./.env; set +a; fi; }
has_key() { [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${GOOGLE_API_KEY:-}" ]; }
venv_ok() { [ -x "$PY" ] && "$PY" -c "import streamlit, pandas, google.genai, pytest" >/dev/null 2>&1; }
need_ready() { venv_ok || die "Not set up yet. Run:  bash jirani.sh setup"; }
only_method() { [ -z "${JIRANI_SETUP_METHOD:-}" ] || [ "$JIRANI_SETUP_METHOD" = "$1" ]; }

# ------------------------------------------------------------------ environment creation (three fallbacks)
try_venv() {
  only_method venv || return 1
  say "Trying: python3 -m venv"
  rm -rf "$VENV"
  if python3 -m venv "$VENV" >/dev/null 2>&1 && [ -x "$VENV/bin/pip" ]; then METHOD=venv; return 0; fi
  note "not available here (python3-venv is not installed). Trying the next method."
  rm -rf "$VENV"; return 1
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv"
  return 0
}

try_uv() {
  only_method uv || return 1
  say "Trying: uv (installs into your home folder, no admin rights)"
  UV=$(find_uv)
  if [ -z "$UV" ]; then
    if command -v curl >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    elif command -v wget >/dev/null 2>&1; then wget -qO- https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    fi
    UV=$(find_uv)
  fi
  if [ -z "$UV" ]; then note "could not get uv (network blocked?). Trying the next method."; return 1; fi
  rm -rf "$VENV"
  if "$UV" venv "$VENV" >/dev/null 2>&1; then METHOD=uv; return 0; fi
  rm -rf "$VENV"; return 1
}

try_virtualenv() {
  only_method virtualenv || return 1
  say "Trying: virtualenv (user install)"
  if ! python3 -m virtualenv --version >/dev/null 2>&1; then
    if python3 -m pip --version >/dev/null 2>&1; then
      python3 -m pip install --user --break-system-packages -q virtualenv >/dev/null 2>&1 \
        || python3 -m pip install --user -q virtualenv >/dev/null 2>&1
    elif command -v curl >/dev/null 2>&1; then
      curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
        && python3 /tmp/get-pip.py --user --break-system-packages -q >/dev/null 2>&1 \
        && python3 -m pip install --user --break-system-packages -q virtualenv >/dev/null 2>&1
    fi
  fi
  rm -rf "$VENV"
  if python3 -m virtualenv "$VENV" >/dev/null 2>&1 && [ -x "$VENV/bin/pip" ]; then METHOD=virtualenv; return 0; fi
  rm -rf "$VENV"; return 1
}

cmd_setup() {
  command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
  if venv_ok; then
    say "Environment already set up. Nothing to install."
    return 0
  fi
  try_venv || try_uv || try_virtualenv || die "Could not create a virtual environment with any method.
   Copy the messages above and send them to me and I will fix it."
  say "Installing packages with $METHOD (1-3 minutes)"
  case "$METHOD" in
    uv) "$UV" pip install -q --python "$PY" -r requirements.txt ;;
    *)  "$VENV/bin/pip" install -q --disable-pip-version-check -r requirements.txt ;;
  esac || die "Package install failed. Check your internet connection and run setup again."
  venv_ok || die "Packages installed but the check failed. Delete the .venv folder and run setup again."
  say "Setup complete."
  note "Next:  bash jirani.sh key     (paste your Gemini key, optional)"
  note "       bash jirani.sh all     (test + evaluate + open the app)"
}

# ------------------------------------------------------------------ commands
cmd_key() {
  say "Paste your Gemini API key (input is hidden), then press Enter."
  printf 'Key: '; read -rs KEY; echo
  [ -n "$KEY" ] || die "No key entered."
  touch .env; chmod 600 .env
  { grep -v '^GEMINI_API_KEY=' .env 2>/dev/null || true; } > .env.tmp
  printf "GEMINI_API_KEY='%s'\n" "$KEY" >> .env.tmp
  mv .env.tmp .env; chmod 600 .env
  say "Saved to .env (private to you, and git ignores it). Do not share that file."
}

cmd_run() {
  need_ready; load_env
  if has_key; then say "Gemini key found: the app will use the model."; else say "No key set: the app will run in TEMPLATE MODE (not AI output)."; fi
  note "Open http://localhost:8501 in your browser.  Stop with Ctrl+C."
  (sleep 4; xdg-open http://localhost:8501 >/dev/null 2>&1 || true) &
  "$VENV/bin/streamlit" run app.py
}

cmd_test() {
  need_ready; load_env
  say "Running automated tests"
  "$PY" -m pytest -q
}

cmd_eval() {
  need_ready; load_env
  local mode="${1:-auto}"
  if [ "$mode" != template ] && has_key; then say "Evaluation using Gemini"; else say "Evaluation using offline templates (no key found)"; fi
  "$PY" run_eval.py --mode "$mode" --out evaluation-record-dev.csv --drafts dev-drafts.txt
  note "Results: evaluation-record-dev.csv    Full draft texts to read: dev-drafts.txt"
}

cmd_mock() {
  need_ready
  say "Starting MOCK n8n (this is not real n8n). Stop with Ctrl+C."
  "$PY" tools/mock_n8n.py "$@"
}

cmd_n8n() {
  if command -v npx >/dev/null 2>&1; then
    say "Starting n8n at http://localhost:5678  (first start downloads it, and takes a few minutes)"
    note "Then: Workflows > Import from file > n8n/jirani-review-outcome-workflow.json, then click Execute workflow."
    npx --yes n8n
  else
    say "n8n needs Node.js, which is not installed."
    note "Install Node without admin rights using nvm: https://github.com/nvm-sh/nvm"
    note "Or use the mock instead:  bash jirani.sh mock"
  fi
}

port_busy() { "$PY" - <<'EOF'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("localhost", 5678)) == 0 else 1)
EOF
}

cmd_all() {
  cmd_setup
  cmd_test || die "Tests failed. Send me the output above."
  cmd_eval auto
  need_ready; load_env
  if port_busy; then
    say "Port 5678 is already in use, so I will assume that is real n8n and NOT start the mock."
  else
    say "Starting MOCK n8n in the background so Approve works (stopped when you quit)."
    "$PY" tools/mock_n8n.py >/dev/null 2>&1 &
    MOCK_PID=$!
    trap 'kill "$MOCK_PID" 2>/dev/null' EXIT INT TERM
    sleep 1
  fi
  cmd_run
}

cmd_reset() {
  printf 'This deletes jirani.db (all drafts, reviews and workflow records). Type yes to continue: '
  read -r ans
  if [ "$ans" = yes ]; then rm -f jirani.db; say "Records cleared."; else say "Cancelled."; fi
}

cmd_help() {
  cat <<'EOF'

Usage: bash jirani.sh <command>

  all            setup + tests + evaluation + start the app (with a mock n8n)  <- start here
  setup          create the environment and install packages (no sudo)
  key            save your Gemini API key privately into .env
  run            start the app at http://localhost:8501
  test           run the 55 automated tests
  eval [mode]    evaluate the 8 dev tickets; mode = auto (default) | llm | template
  mock [--fail]  start a MOCK n8n webhook (--fail makes it always error)
  n8n            start real n8n (needs Node.js)
  reset          delete all saved records (asks first)

Typical first time:   bash jirani.sh setup  &&  bash jirani.sh key  &&  bash jirani.sh all
EOF
}

case "${1:-help}" in
  setup) cmd_setup ;;
  key) cmd_key ;;
  run) cmd_run ;;
  test) cmd_test ;;
  eval) shift; cmd_eval "$@" ;;
  mock) shift; cmd_mock "$@" ;;
  n8n) cmd_n8n ;;
  all) cmd_all ;;
  reset) cmd_reset ;;
  help|-h|--help) cmd_help ;;
  *) cmd_help; exit 1 ;;
esac
