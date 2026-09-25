#!/usr/bin/env bash
#
# tests/smoke.sh — Linux-side smoke test of Music_Agent (25 checks).
#
# Run from the openhands orchestration container:  bash /host/opt/Music_Agent/tests/smoke.sh
# (needs /host = the host's / and the docker CLI talking to the host daemon).
#
# What it covers: the pytest suite per module (T01-T08), the CLI on a fresh config (T09-T14), the two
# log lines semgrep flagged plus the secrets that must never reach the log (T15), a ruff fingerprint
# (T16), and the release workflow's tag step rendered the way GitHub renders it and run in real pwsh
# (T17-T25).
#
# Side effects: none that last. Every check runs in a throwaway `--network none` container that mounts
# only src/, tests/, pyproject.toml, .env.example and .github, never the repo root, so an owner's .env
# in the checkout can never reach a test. Scratch is a mktemp dir under $U, removed on exit. The only
# network use: a one-time pip install of the pinned test tools into $U/pydeps, and pulling the pinned
# pwsh image when absent (removed again on exit). Sentinel secrets are generated at runtime, never printed.

set -uo pipefail

R=${R:-/opt/Music_Agent}; RC=/host$R
U=${U:-/opt/secfix/Music_Agent}; UC=/host$U; D=$U/pydeps
PY=${PY:-python:3.12-slim}
PWSH=mcr.microsoft.com/powershell@sha256:a6beeddb2fcf45547c9099fba091ce231e51aa374fe62ecc182f7c28b69a6cbf
BASE=e2ceb762d6ec34b024674d284353898008f82405     # the commit before the 2026-09 secfix run
WF=.github/workflows/release.yml
[[ -d "$RC" ]] && command -v docker >/dev/null || { echo "run from the openhands container (needs /host + docker)" >&2; exit 2; }

mkdir -p "$UC"
W="$(mktemp -d "$UC/run.XXXXXX")"; WH="${W#/host}"
PULLED=0
cleanup() { rm -rf "$W"; [[ "$PULLED" -eq 1 ]] && docker rmi "$PWSH" >/dev/null 2>&1; true; }
trap cleanup EXIT

if [[ ! -f "$UC/pydeps/.ok" ]]; then
  mkdir -p "$UC/pydeps"      # before the bind mount: dockerd would create a missing source dir as root
  docker run --rm -u 1001:1001 -e HOME=/tmp -v "$D":/deps "$PY" pip install -q --no-cache-dir \
      --disable-pip-version-check --only-binary=:all: --target /deps pytest==9.1.1 ruff==0.16.9 PyYAML==6.0.3 \
      >/dev/null 2>&1 && touch "$UC/pydeps/.ok" || { echo "could not install the test tools into $D" >&2; exit 2; }
fi
if ! docker image inspect "$PWSH" >/dev/null 2>&1; then
  free=$(df -BG --output=avail /host/mnt/dockerdata | tail -1 | tr -dc 0-9)
  [[ "${free:-0}" -ge 4 ]] || { echo "only ${free}G free on dockerdata; refusing to pull $PWSH" >&2; exit 2; }
  docker pull -q "$PWSH" >/dev/null 2>&1 && PULLED=1
fi

MNT=(-v "$R/src:/app/src:ro" -v "$R/tests:/app/tests:ro" -v "$R/pyproject.toml:/app/pyproject.toml:ro"
     -v "$R/.env.example:/app/.env.example:ro" -v "$R/.github:/app/.github:ro")
RUN=(docker run --rm -i --network none -u 1001:1001 -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 "${MNT[@]}" -v "$WH:/w" -w /app)
PYRUN=("${RUN[@]}" -e PYTHONPATH=/deps:/app/src -v "$D:/deps:ro" "$PY")    # with pytest/ruff/PyYAML
CLIRUN=("${RUN[@]}" -e PYTHONPATH=/app/src "$PY")                           # stdlib only, like the CLI

PASS=0; TOTAL=0
check() {  # check <name> <fn>: fn prints the reason on failure and returns nonzero
  local name="$1" out; TOTAL=$((TOTAL + 1))
  if out="$("$2" 2>&1 </dev/null)"; then PASS=$((PASS + 1)); echo "PASS $name"
  else echo "FAIL $name — $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"; fi
}

# --------------------------------------------------------------------------- shared runs (once each)
# The suite, minus the two tests that can only pass on Windows: test_hotkeys (ctypes.WinDLL) and the
# DPAPI proxy-sealing test (no platform guard; its portable half is T15).
"${PYRUN[@]}" python -m pytest -p no:cacheprovider \
    --deselect tests/test_hotkeys.py::test_hotkeys \
    --deselect tests/test_config.py::test_a_proxy_address_with_credentials_in_it_is_sealed_and_redacted \
    --junitxml=/w/junit.xml </dev/null >"$W/pytest.log" 2>&1
PYTEST_RC=$?

# The release workflow's step 'which', rendered the way GitHub renders it: every ${{ expr }} in env and
# run is replaced by its value, env becomes $env:K='v', and $ErrorActionPreference='stop' goes first.
# Cases: N REQUESTED(inputs.tag) REF(github.ref_name).
git -C "$RC" show "$BASE:$WF" >"$W/base.yml" 2>/dev/null
"${PYRUN[@]}" python - >"$W/render.log" 2>&1 <<'EOF'
import re, yaml
step = next(s for s in yaml.safe_load(open("/app/.github/workflows/release.yml"))["jobs"]["build"]["steps"]
            if s.get("id") == "which")
cases = {"18": ("", "v4.6.1"), "19": ("v4.6.0", "main"), "20": ("", "main"),
         "21": ("", "v1$(1+1)"), "25": ("v1$(1+1)", "main")}
for n, (req, ref) in cases.items():
    ctx = {"github.ref_name": ref, "inputs.tag": req}
    def value(m):
        k = m.group(1).strip()
        if k not in ctx:
            raise SystemExit("unknown expression in step 'which': " + k)
        return ctx[k]
    sub = lambda t: re.sub(r"\$\{\{(.*?)\}\}", value, str(t))
    q = lambda v: "'" + v.replace("'", "''") + "'"
    lines = ["$ErrorActionPreference='stop'", f"$env:GITHUB_OUTPUT='/w/c{n}.out'"]
    lines += [f"$env:{k}={q(sub(v))}" for k, v in (step.get("env") or {}).items()]
    lines.append(sub(step["run"]))
    open(f"/w/c{n}.ps1", "w").write("\n".join(lines) + "\n")
EOF
for n in 18 19 20 21 25; do
  [[ -f "$W/c$n.ps1" ]] || continue
  docker run --rm --network none -u 1001:1001 -e HOME=/tmp -e POWERSHELL_TELEMETRY_OPTOUT=1 -v "$WH:/w" "$PWSH" \
      pwsh -NoProfile -NonInteractive -File "/w/c$n.ps1" </dev/null >"$W/c$n.log" 2>&1
  echo $? >"$W/c$n.rc"
done

# --------------------------------------------------------------------------- helpers
want_junit() {  # want_junit "<passed failed errors skipped>" classname...
  local want="$1" got; shift
  [[ -s "$W/junit.xml" ]] || { echo "no junit.xml (pytest rc=$PYTEST_RC)"; return 1; }
  got=$(python3 - "$W/junit.xml" "$@" <<'EOF'
import sys, xml.etree.ElementTree as ET
n = dict(p=0, f=0, e=0, s=0)
for tc in ET.parse(sys.argv[1]).iter("testcase"):
    if tc.get("classname") in sys.argv[2:]:
        tags = {c.tag for c in tc}
        n["f" if "failure" in tags else "e" if "error" in tags else "s" if "skipped" in tags else "p"] += 1
print(n["p"], n["f"], n["e"], n["s"])
EOF
) || { echo "junit.xml unreadable"; return 1; }
  [[ "$got" == "$want" ]] || { echo "passed/failed/errors/skipped = $got, want $want"; return 1; }
}

wf_why() { [[ -f "$W/c$1.ps1" ]] || echo "not rendered: $(tail -1 "$W/render.log" 2>/dev/null)"; }
wf_accepts() {  # wf_accepts N tag: rc 0 and GITHUB_OUTPUT is exactly tag=<tag>
  local rc out; rc=$(cat "$W/c$1.rc" 2>/dev/null); out=$(cat "$W/c$1.out" 2>/dev/null)
  [[ "$rc" == 0 && "$out" == "tag=$2" ]] || { echo "rc=$rc out=[$out], want rc=0 out=[tag=$2] $(wf_why "$1")"; return 1; }
}
wf_rejects() {  # wf_rejects N value: nonzero rc, the step's own error naming <value>, no tag= written
  local rc out; rc=$(cat "$W/c$1.rc" 2>/dev/null); out=$(cat "$W/c$1.out" 2>/dev/null)
  [[ -n "$rc" && "$rc" != 0 ]] || { echo "rc=${rc:-none}: accepted, GITHUB_OUTPUT=[$out] $(wf_why "$1")"; return 1; }
  grep -qF "not a version tag: '$2'" "$W/c$1.log" || { echo "rc=$rc but no \"not a version tag: '$2'\""; return 1; }
  [[ "$out" != *tag=* ]] || { echo "rc=$rc but GITHUB_OUTPUT=[$out]"; return 1; }
}

# --------------------------------------------------------------------------- T01-T08 pytest suite
t01() { want_junit "1 0 0 0" tests.test_cadence; }
t02() { want_junit "1 0 0 0" tests.test_spotify; }
t03() { want_junit "2 0 0 0" tests.test_config; }
t04() { want_junit "1 0 0 0" tests.test_httpmin; }
t05() { want_junit "3 0 0 0" tests.test_cli; }
t06() { want_junit "1 0 0 0" tests.test_log && want_junit "1 0 0 0" tests.test_winhttp; }
t07() { want_junit "10 0 0 0" tests.test_integration; }
t08() {
  local got
  got=$(python3 - "$W/junit.xml" <<'EOF' 2>/dev/null
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot(); s = r if r.tag == "testsuite" else r.find("testsuite")
skipped = sorted(tc.get("name") for tc in r.iter("testcase") if any(c.tag == "skipped" for c in tc))
print(*(s.get(k) for k in ("tests", "failures", "errors", "skipped")), *skipped)
EOF
)
  local want="24 0 0 4 tests.test_login tests.test_settings tests.test_tray tests.test_widgets"
  [[ "$PYTEST_RC" == 0 && "$got" == "$want" ]] || { echo "pytest rc=$PYTEST_RC; tests/failures/errors/skipped=[$got], want [$want]"; return 1; }
}

# --------------------------------------------------------------------------- T09-T14 CLI
t09() {  # the Linux-runnable half of test_hotkeys: parsing the defaults, rejecting nonsense
  local out; out=$("${CLIRUN[@]}" python - 2>&1 <<'EOF'
from music_agent.win32.hotkeys import parse, MOD_NOREPEAT as N
assert parse("ctrl+alt+up") == (3 | N, 0x26); assert parse("ctrl+alt+right") == (3 | N, 0x27)
assert parse("ctrl+alt+l") == (3 | N, ord("L")); assert parse("CTRL+ALT+L") == parse("ctrl + alt + l")
assert parse("ctrl+shift+alt+f9") == (7 | N, 0x78); assert parse("win+7") == (8 | N, ord("7"))
for bad in ("ctrl+alt", "", "ctrl+nonsense", "ctrl+a+b"):
    try:
        parse(bad)
    except ValueError:
        continue
    raise SystemExit("accepted " + repr(bad))
print("ok")
EOF
)
  [[ "$out" == ok ]] || { echo "$(tail -1 <<<"$out")"; return 1; }
}

t10() {  # the CLI imports nothing outside the standard library
  local out; out=$("${CLIRUN[@]}" python -c "import music_agent.cli, music_agent.win32.hotkeys, sys; print([m.__name__ for m in list(sys.modules.values()) if 'site-packages' in (getattr(m, '__file__', '') or '')])" 2>&1)
  [[ "$out" == "[]" ]] || { echo "non-stdlib imports: $(tail -1 <<<"$out")"; return 1; }
}

t11() {
  local out rc n miss=""
  out=$("${CLIRUN[@]}" python -m music_agent --help 2>&1); rc=$?
  for n in status login logout devices run setup get set hotkeys play resume pause toggle next prev like now; do
    grep -qE "^ +$n( |$)" <<<"$out" || miss+=" $n"
  done
  grep -qF -- '-v, --verbose' <<<"$out" || miss+=" -v/--verbose"
  grep -qF -- '-d, --debug' <<<"$out" || miss+=" -d/--debug"
  [[ "$rc" == 0 && -z "$miss" ]] || { echo "rc=$rc missing:${miss:- none}"; return 1; }
}

t12() {  # a fresh config (HOME=/tmp; /app is not writable, so AppData) prints exactly the defaults
  local out rc want
  out=$("${CLIRUN[@]}" python -m music_agent status 2>/dev/null); rc=$?
  want=$(cat <<'EOF'
Config file : /tmp/.local/share/MusicAgent/Music Agent ERFFFFF/cadence_config.txt
Secrets file: (none — using the config file only)
Proxy       : none — connecting directly
Mode        : cadence
Server      : (not set)
Account     : (not set)
Session     : not signed in
CF Access   : no token
Hotkeys     :
  ctrl+alt+up            play_pause
  ctrl+alt+right         next_track
  ctrl+alt+left          previous_track
  ctrl+alt+l             like_unlike
  ctrl+alt+c             show_current
EOF
)
  [[ "$rc" == 0 && "$out" == "$want" ]] && return 0
  echo "rc=$rc; first differing line: $(diff <(printf '%s\n' "$out") <(printf '%s\n' "$want") | grep -m1 -oE '^[0-9]+')"
  return 1
}

t13() {  # set / get / rebind / status round trip, and the config file is private
  local out rc l bad=""
  out=$("${CLIRUN[@]}" sh -c 'python -m music_agent set mode spotify && python -m music_agent get mode &&
        python -m music_agent hotkeys next_track ctrl+alt+n && python -m music_agent hotkeys &&
        python -m music_agent status && stat -c %a "$HOME/.local/share/MusicAgent/Music Agent ERFFFFF/cadence_config.txt"' 2>&1); rc=$?
  [[ "$rc" == 0 ]] || bad+=" rc=$rc"
  [[ $(grep -cxF 'mode = spotify' <<<"$out") == 2 ]] || bad+=" 'mode = spotify' x$(grep -cxF 'mode = spotify' <<<"$out")"
  for l in '  ctrl+alt+n             next_track' 'Mode        : spotify' \
           'Redirect URI: http://127.0.0.1:8888/callback' 'Authorised  : no — happens on first use'; do
    grep -qxF -- "$l" <<<"$out" || bad+=" missing[$l]"
  done
  grep -qF 'next_track -> ctrl+alt+n' <<<"$out" || bad+=" missing[next_track -> ctrl+alt+n]"
  grep -qF 'ctrl+alt+right' <<<"$out" && bad+=" ctrl+alt+right-still-bound"
  [[ $(tail -1 <<<"$out") == 600 ]] || bad+=" config-mode-not-600"
  [[ -z "$bad" ]] || { echo "$bad"; return 1; }
}

t14() {  # an unconfigured command fails with one sentence, no traceback, no network
  local rc o e want='Not signed in to Cadence, and no USERNAME / PASSWORD in a .env — run: python -m music_agent login'
  "${CLIRUN[@]}" python -m music_agent next >"$W/t14.o" 2>"$W/t14.e"; rc=$?
  o=$(cat "$W/t14.o"); e=$(cat "$W/t14.e")
  [[ "$rc" == 1 && -z "$o" && "$e" == "$want" ]] || {
    echo "rc=$rc stdout-bytes=${#o} stderr-exact=$([[ "$e" == "$want" ]] && echo yes || echo no) traceback=$(grep -c Traceback <<<"$e")"; return 1; }
}

# --------------------------------------------------------------------------- T15-T16 code
t15() {  # the two lines semgrep flagged: exact text, their side effects, and no secret in the log
  local out; out=$("${PYRUN[@]}" python - 2>&1 <<'EOF'
import io, logging, secrets, tempfile
from music_agent import config
from music_agent.backends import spotify
RT, SEC, PW = "RT-" + secrets.token_hex(8), secrets.token_hex(8), secrets.token_hex(8)   # never printed
buf = io.StringIO()
logging.getLogger().addHandler(logging.StreamHandler(buf)); logging.getLogger().setLevel(logging.INFO)
assert config._encrypt(PW) == PW, "no DPAPI: the value must be stored as-is"

class Resp:
    status_code, content = 400, b"x"
    def json(self):
        return {"error": "invalid_grant", "error_description": "Refresh token revoked"}
spotify.httpmin.post = lambda *a, **k: Resp()
saved = []
a = spotify._Auth("id", SEC, "http://127.0.0.1:8888/callback", refresh_token=RT, on_token=saved.append)
a._authorize = lambda: "NEW"
assert a.token() == "NEW", "a revoked refresh token did not fall through to consent"
assert a.refresh_token == "" and saved == [""], "the dead refresh token was not dropped and persisted as ''"

url = "http://corp-user:" + PW + "@proxy.company.com:8080"
with tempfile.TemporaryDirectory() as d:
    config.app_dir = lambda: d
    config.use_env(False)
    cfg = config.load_config(); cfg["proxy_url"] = url; config.save_config(cfg)
    assert config.load_config()["proxy_url"] == url, "the proxy URL did not round-trip"
    assert config.redact_url(url) == "http://corp-user:***@proxy.company.com:8080", "redact_url changed"

log = buf.getvalue()
assert "DPAPI unavailable — credentials in cadence_config.txt are NOT encrypted.\n" in log, "DPAPI line changed"
assert "Spotify refresh token is no longer valid (Refresh token revoked) — re-authorising.\n" in log, \
    "refresh-token line changed"
assert not any(s in log for s in (RT, SEC, PW)), "a sentinel secret reached the log"
print("ok")
EOF
)
  [[ "$out" == ok ]] || { echo "$(tail -1 <<<"$out")"; return 1; }
}

t16() {  # ruff: the fatal set is clean, and the full default set is exactly the 159 findings of e2ceb76
  local out got want
  out=$("${PYRUN[@]}" sh -c '/deps/bin/ruff check --no-cache --select E9,F63,F7,F82 src tests 2>&1; echo "rc=$?"
        /deps/bin/ruff check --no-cache --output-format json src tests >/w/ruff.json 2>/dev/null; true')
  [[ "$out" == *"All checks passed!"*"rc=0" ]] || { echo "fatal rule set not clean: $(tail -1 <<<"$out")"; return 1; }
  got=$(python3 - "$W/ruff.json" <<'EOF' 2>&1
import collections, hashlib, json, sys
d = json.load(open(sys.argv[1]))
h = collections.Counter(x["code"] for x in d)
key = sorted((x["filename"].replace("/app/", ""), x["code"]) for x in d)
print(len(d), " ".join(f"{c}:{n}" for c, n in sorted(h.items())), hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16])
EOF
)
  want="159 B023:2 BLE001:6 F401:7 F811:1 G201:3 I001:22 ISC004:1 LOG015:59 PLW1510:1 RUF023:1 RUF034:1 RUF100:26 S110:9 SIM102:1 SIM115:18 SIM117:1 fc9f1af3ffd8c0f6"
  [[ "$got" == "$want" ]] || { echo "ruff set changed: [$got]"; return 1; }
}

# --------------------------------------------------------------------------- T17-T25 release workflow
t17() {  # nothing but step 'which' (run + REF_NAME) and the @refs differs from e2ceb76
  [[ -s "$W/base.yml" ]] || { echo "git show $BASE:$WF failed"; return 1; }
  local out; out=$("${PYRUN[@]}" python - 2>&1 <<'EOF'
import yaml
base = yaml.safe_load(open("/w/base.yml")); cur = yaml.safe_load(open("/app/.github/workflows/release.yml"))
w = next(s for s in cur["jobs"]["build"]["steps"] if s.get("id") == "which")
env, run = dict(w.get("env") or {}), w.get("run", "").splitlines()
assert env.get("REQUESTED") == "${{ inputs.tag }}", "env REQUESTED changed"
assert set(env) <= {"REQUESTED", "REF_NAME"}, "unexpected env keys: " + ",".join(sorted(env))
assert r"""if ($tag -notmatch '^v[0-9][0-9A-Za-z.\-+]*$') { throw "not a version tag: '$tag'" }""" in run, "tag check changed"
assert '"tag=$tag" >> $env:GITHUB_OUTPUT' in run, "output line changed"
def norm(doc):
    for job in doc["jobs"].values():
        for s in job["steps"]:
            if "uses" in s:
                s["uses"] = s["uses"].split("@")[0]
            if s.get("id") == "which":
                s.pop("run", None); (s.get("env") or {}).pop("REF_NAME", None)
    return doc
assert norm(base) == norm(cur), "the workflow differs from e2ceb76 outside step 'which' run/REF_NAME and the @refs"
print("ok")
EOF
)
  [[ "$out" == ok ]] || { echo "$(tail -1 <<<"$out")"; return 1; }
}

t18() { wf_accepts 18 v4.6.1; }          # tag push
t19() { wf_accepts 19 v4.6.0; }          # dispatch input wins over the ref
t20() { wf_rejects 20 main; }            # a branch is not a version tag
t21() { wf_rejects 21 'v1$(1+1)'; }      # a legal git tag name must not be evaluated by pwsh
t25() { wf_rejects 25 'v1$(1+1)'; }      # ...nor the dispatch input (the env route)

t22() {  # every action pinned to the commit its major tag pointed at on 2026-09-25
  local lines a bad=""
  declare -A PINS=([checkout]=11d5960a326750d5838078e36cf38b85af677262
                   [setup-python]=a26af69be951a213d495a4c3e4e4022e16d87065
                   [upload-artifact]=ea165f8d65b6e75b540449e92b4886f43607fa02)
  declare -A VERS=([checkout]=v4.4.0 [setup-python]=v5.6.0 [upload-artifact]=v4.6.2)
  lines=$(grep -E '^\s*-?\s*uses:' "$RC/$WF")
  [[ $(grep -c . <<<"$lines") == 3 ]] || bad+=" uses-lines=$(grep -c . <<<"$lines")"
  for a in checkout setup-python upload-artifact; do
    grep -qE "^\s*-?\s*uses: actions/$a@${PINS[$a]}  # ${VERS[$a]//./\\.}$" <<<"$lines" || bad+=" $a@$(grep -oE "actions/$a@\S+" <<<"$lines" | sed 's/.*@//')"
  done
  [[ -z "$bad" ]] || { echo "not pinned:$bad"; return 1; }
}

t23() {  # nothing outside the four files changed, and the Python change is comment lines only
  local files extra src
  files=$({ git -C "$RC" diff --name-only "$BASE"; git -C "$RC" ls-files --others --exclude-standard; } | sort -u)
  extra=$(grep -vxF -e "$WF" -e src/music_agent/backends/spotify.py -e src/music_agent/config.py -e tests/smoke.sh <<<"$files" | grep .)
  src=$(git -C "$RC" diff "$BASE" -- src/ | grep -E '^[+-]' | grep -vE '^(\+\+\+|---) ' | grep -vE '^\+\s*(#.*)?$')
  [[ -z "$extra" && -z "$src" ]] || { echo "outside scope: [$(echo $extra)]; non-comment src lines: $(grep -c . <<<"$src")"; return 1; }
}

t24() {  # the static form of the semgrep rule: no ${{ }} inside any run script
  local out; out=$("${PYRUN[@]}" python - 2>&1 <<'EOF'
import yaml
doc = yaml.safe_load(open("/app/.github/workflows/release.yml"))
bad = [s.get("name") or s.get("id") or "?" for j in doc["jobs"].values() for s in j["steps"] if "${{" in (s.get("run") or "")]
print("ok" if not bad else "expression inside run: " + "; ".join(bad))
EOF
)
  [[ "$out" == ok ]] || { echo "$(tail -1 <<<"$out")"; return 1; }
}

check "T01 pytest_cadence" t01
check "T02 pytest_spotify" t02
check "T03 pytest_config" t03
check "T04 pytest_httpmin" t04
check "T05 pytest_cli" t05
check "T06 pytest_log_and_winhttp" t06
check "T07 pytest_integration" t07
check "T08 pytest_suite_totals" t08
check "T09 hotkeys_parse" t09
check "T10 cli_stdlib_only" t10
check "T11 cli_help_surface" t11
check "T12 cli_status_fresh_defaults" t12
check "T13 cli_config_roundtrip" t13
check "T14 cli_unconfigured_command_fails_cleanly" t14
check "T15 log_lines_and_secrets_unchanged" t15
check "T16 ruff_fingerprint_unchanged" t16
check "T17 wf_structure_unchanged" t17
check "T18 wf_tag_push_resolves" t18
check "T19 wf_dispatch_input_wins" t19
check "T20 wf_rejects_non_version_ref" t20
check "T21 wf_ref_name_not_evaluated" t21
check "T22 wf_actions_pinned_to_recorded_shas" t22
check "T23 change_scope_guard" t23
check "T24 wf_no_expression_in_run" t24
check "T25 wf_dispatch_input_not_evaluated" t25

echo "RESULT $PASS/$TOTAL"
[[ "$PASS" -eq "$TOTAL" ]]
