#!/usr/bin/env bash
# AlarmFW Smoke Test
# Tüm servislerin ayakta ve temel işlevlerin çalışır durumda olduğunu doğrular.
# Kullanım: ./smoke-test.sh
# .env yoksa API_KEY ve UI_ADMIN_PASSWORD'ü ortam değişkeni olarak geçirebilirsiniz.

set -euo pipefail

API="http://localhost:8000"
OBSERVE="http://localhost:8001"
UI="http://localhost:3000"
MAILPIT="http://localhost:8025"

ENV_FILE="$(dirname "$0")/.env"
if [[ -f "$ENV_FILE" ]]; then
  # Yalnızca export satırları olmadan key=value oku
  set -a
  # shellcheck disable=SC1090
  source <(grep -v '^\s*#' "$ENV_FILE" | grep '=')
  set +a
fi

API_KEY="${ALARMFW_API_KEY:-}"
UI_ADMIN_PASS="${UI_ADMIN_PASSWORD:-}"

# ── Renkler ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

PASS=0
FAIL=0
WARN=0

pass()  { echo -e "  ${GREEN}[PASS]${RESET} $1"; ((PASS++)); }
fail()  { echo -e "  ${RED}[FAIL]${RESET} $1"; ((FAIL++)); }
warn()  { echo -e "  ${YELLOW}[WARN]${RESET} $1"; ((WARN++)); }
section(){ echo -e "\n${CYAN}▶ $1${RESET}"; }

# HTTP GET wrapper — curl -s -o /dev/null -w "%{http_code}"
http_code() {
  curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$@"
}

http_get() {
  curl -s --max-time 8 "$@"
}

# ─────────────────────────────────────────────────────────────────────────────
section "1. Servis liveness"
# ─────────────────────────────────────────────────────────────────────────────

# API health
code=$(http_code "$API/api/health")
[[ "$code" == "200" ]] && pass "API /api/health → 200" || fail "API /api/health → $code (beklenen 200)"

# Observe health
code=$(http_code "$OBSERVE/api/observe/auth")
[[ "$code" == "200" ]] && pass "Observe /api/observe/auth → 200" || fail "Observe /api/observe/auth → $code"

# UI login sayfası
code=$(http_code "$UI/login")
[[ "$code" == "200" ]] && pass "UI /login → 200" || fail "UI /login → $code"

# Mailpit web UI
code=$(http_code "$MAILPIT/api/v1/info")
[[ "$code" == "200" ]] && pass "Mailpit /api/v1/info → 200" || warn "Mailpit /api/v1/info → $code (Mailpit kapalı olabilir)"

# Swagger docs
code=$(http_code "$API/docs")
[[ "$code" == "200" ]] && pass "API /docs (Swagger) → 200" || fail "API /docs → $code"

# ─────────────────────────────────────────────────────────────────────────────
section "2. API Key auth"
# ─────────────────────────────────────────────────────────────────────────────

if [[ -z "$API_KEY" ]]; then
  warn "ALARMFW_API_KEY boş — auth testleri atlandı (API key zorunsuz modda çalışıyor)"
else
  # Geçerli key → 200
  code=$(http_code -H "X-API-Key: $API_KEY" "$API/api/checks")
  [[ "$code" == "200" ]] && pass "Geçerli API key → 200" || fail "Geçerli API key → $code (beklenen 200)"

  # Yanlış key → 403
  code=$(http_code -H "X-API-Key: wrong-key-xyz" "$API/api/checks")
  [[ "$code" == "403" ]] && pass "Yanlış API key → 403" || fail "Yanlış API key → $code (beklenen 403)"

  # Key yok → 403
  code=$(http_code "$API/api/checks")
  [[ "$code" == "403" ]] && pass "API key eksik → 403" || fail "API key eksik → $code (beklenen 403)"
fi

# ─────────────────────────────────────────────────────────────────────────────
section "3. API veri endpoint'leri"
# ─────────────────────────────────────────────────────────────────────────────

AUTH_HEADER=()
[[ -n "$API_KEY" ]] && AUTH_HEADER=(-H "X-API-Key: $API_KEY")

# /api/alarms — liste (boş da olsa array dönmeli)
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/alarms")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  pass "/api/alarms → JSON array ($count kayıt)"
else
  fail "/api/alarms → geçerli JSON array değil: ${body:0:120}"
fi

# /api/alarms/state
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/alarms/state")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  pass "/api/alarms/state → JSON array"
else
  fail "/api/alarms/state → geçerli JSON array değil"
fi

# /api/alarms/metrics
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/alarms/metrics")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'rules_evaluated_total' in d" 2>/dev/null; then
  updated=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('updated_at_utc','?'))")
  pass "/api/alarms/metrics → OK (last update: $updated)"
else
  fail "/api/alarms/metrics → beklenen alan yok"
fi

# /api/checks
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/checks")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  if [[ "$count" -gt 0 ]]; then
    pass "/api/checks → $count check tanımlı"
  else
    warn "/api/checks → liste boş (config/checks/ dizini boş olabilir)"
  fi
else
  fail "/api/checks → geçerli JSON array değil"
fi

# /api/notifiers
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/notifiers")
if echo "$body" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  if [[ "$count" -gt 0 ]]; then
    pass "/api/notifiers → $count notifier tanımlı"
  else
    warn "/api/notifiers → notifier yok (config/notifiers/ boş olabilir)"
  fi
else
  fail "/api/notifiers → geçerli JSON değil"
fi

# /api/monitor/clusters
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/monitor/clusters")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  pass "/api/monitor/clusters → $count cluster"
else
  fail "/api/monitor/clusters → geçerli JSON array değil"
fi

# /api/monitor/namespaces
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/monitor/namespaces")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  pass "/api/monitor/namespaces → $count namespace"
else
  fail "/api/monitor/namespaces → geçerli JSON array değil"
fi

# /api/config/clusters
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/config/clusters")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  pass "/api/config/clusters → $count cluster config"
else
  fail "/api/config/clusters → geçerli JSON array değil"
fi

# /api/policies/maintenance
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/policies/maintenance")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'silences' in d" 2>/dev/null; then
  pass "/api/policies/maintenance → OK"
else
  fail "/api/policies/maintenance → beklenen 'silences' alanı yok"
fi

# /api/run/last — runner cycle durumu
body=$(http_get "${AUTH_HEADER[@]}" "$API/api/run/last")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'status' in d" 2>/dev/null; then
  status=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  if [[ "$status" == "never_run" ]]; then
    warn "/api/run/last → henüz hiç cycle çalışmadı"
  elif [[ "$status" == "done" ]]; then
    exit_code=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exit_code','?'))")
    duration=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('duration_sec','?'))")
    pass "/api/run/last → done (exit_code=$exit_code, ${duration}s)"
  elif [[ "$status" == "running" ]]; then
    warn "/api/run/last → şu an çalışıyor"
  else
    fail "/api/run/last → status=$status"
  fi
else
  fail "/api/run/last → geçerli JSON değil"
fi

# ─────────────────────────────────────────────────────────────────────────────
section "4. Observe servis"
# ─────────────────────────────────────────────────────────────────────────────

# /api/observe/clusters
body=$(http_get "$OBSERVE/api/observe/clusters")
if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  count=$(echo "$body" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  pass "Observe /api/observe/clusters → $count cluster"
  if [[ "$count" -gt 0 ]]; then
    # İlk cluster'ı al ve health/overview dene
    first_cluster=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['name'])")
    prom_avail=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d[0].get('prometheus_available',False)).lower())")
    if [[ "$prom_avail" == "true" ]]; then
      ov_body=$(http_get "$OBSERVE/api/observe/health/overview?cluster=$first_cluster")
      if echo "$ov_body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
        firing=$(echo "$ov_body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('firing_alerts','?'))")
        pass "Observe health/overview ($first_cluster) → firing_alerts=$firing"
      else
        err=$(echo "$ov_body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','?'))" 2>/dev/null || echo "$ov_body")
        warn "Observe health/overview ($first_cluster) → Prometheus erişim sorunu: $err"
      fi
    else
      warn "Observe: '$first_cluster' için prometheus_available=false (token eksik olabilir)"
    fi
  fi
else
  fail "Observe /api/observe/clusters → geçerli JSON array değil"
fi

# OCP auth durumu
body=$(http_get "$OBSERVE/api/observe/auth")
if echo "$body" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  pass "Observe /api/observe/auth → geçerli JSON"
else
  fail "Observe /api/observe/auth → geçerli JSON değil"
fi

# ─────────────────────────────────────────────────────────────────────────────
section "5. UI NextAuth login"
# ─────────────────────────────────────────────────────────────────────────────

if [[ -z "$UI_ADMIN_PASS" ]]; then
  warn "UI_ADMIN_PASSWORD boş — UI login testi atlandı"
else
  # CSRF token al
  csrf_body=$(http_get -c /tmp/smoke_cookies.txt "$UI/api/auth/csrf")
  csrf_token=$(echo "$csrf_body" | python3 -c "import sys,json; print(json.load(sys.stdin)['csrfToken'])" 2>/dev/null || echo "")

  if [[ -z "$csrf_token" ]]; then
    fail "UI CSRF token alınamadı"
  else
    pass "UI CSRF token alındı"

    # Login dene
    login_resp=$(curl -s --max-time 10 \
      -b /tmp/smoke_cookies.txt \
      -c /tmp/smoke_cookies.txt \
      -X POST "$UI/api/auth/callback/credentials" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -d "csrfToken=${csrf_token}&username=admin&password=${UI_ADMIN_PASS}&redirect=false&callbackUrl=%2F&json=true" \
      -w "\n%{http_code}" 2>/dev/null)

    login_code=$(echo "$login_resp" | tail -1)
    login_body=$(echo "$login_resp" | head -n -1)

    if [[ "$login_code" == "200" ]]; then
      url=$(echo "$login_body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || echo "")
      if echo "$url" | grep -q "error"; then
        fail "UI admin login → hata URL: $url"
      else
        pass "UI admin login → 200 (redirect: $url)"
      fi
    else
      fail "UI admin login → HTTP $login_code"
    fi
    rm -f /tmp/smoke_cookies.txt
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
section "6. Zabbix webhook erişimi"
# ─────────────────────────────────────────────────────────────────────────────

ZABBIX_URL="${ZABBIX_URL:-http://zabbix.example.com:9000/webhook}"
zabbix_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$ZABBIX_URL" 2>/dev/null || echo "000")
if [[ "$zabbix_code" == "000" ]]; then
  warn "Zabbix webhook ($ZABBIX_URL) → bağlantı kurulamadı (network erişimi yok olabilir)"
elif [[ "$zabbix_code" =~ ^(200|405|404) ]]; then
  pass "Zabbix webhook → HTTP $zabbix_code (erişilebilir)"
else
  warn "Zabbix webhook → HTTP $zabbix_code (beklenmedik yanıt)"
fi

# ─────────────────────────────────────────────────────────────────────────────
section "7. Mailpit e-posta kontrolü"
# ─────────────────────────────────────────────────────────────────────────────

mailpit_info=$(http_get "$MAILPIT/api/v1/info" 2>/dev/null || echo "")
if echo "$mailpit_info" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  version=$(echo "$mailpit_info" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Version','?'))" 2>/dev/null || echo "?")
  pass "Mailpit çalışıyor (v$version)"

  messages=$(http_get "$MAILPIT/api/v1/messages?limit=5" 2>/dev/null || echo "")
  if echo "$messages" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'messages' in d or 'items' in d or isinstance(d,list)" 2>/dev/null; then
    pass "Mailpit /api/v1/messages → erişilebilir"
  else
    warn "Mailpit messages endpoint beklenmedik yanıt"
  fi
else
  warn "Mailpit çalışmıyor veya erişilemiyor"
fi

# ─────────────────────────────────────────────────────────────────────────────
section "8. Docker container durumu"
# ─────────────────────────────────────────────────────────────────────────────

for svc in alarmfw-api alarmfw-observe alarmfw-ui mailpit; do
  state=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "not_found")
  if [[ "$state" == "running" ]]; then
    pass "Container $svc → running"
  elif [[ "$state" == "not_found" ]]; then
    warn "Container $svc → bulunamadı (farklı isimle çalışıyor olabilir)"
  else
    fail "Container $svc → $state"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# ÖZET
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────"
echo -e " ${GREEN}PASS${RESET}: $PASS   ${YELLOW}WARN${RESET}: $WARN   ${RED}FAIL${RESET}: $FAIL"
echo "────────────────────────────────────────"

if [[ $FAIL -gt 0 ]]; then
  echo -e " ${RED}Smoke test BAŞARISIZ — $FAIL kritik sorun var.${RESET}"
  exit 1
elif [[ $WARN -gt 0 ]]; then
  echo -e " ${YELLOW}Smoke test TAMAM — $WARN uyarı mevcut.${RESET}"
  exit 0
else
  echo -e " ${GREEN}Smoke test BAŞARILI.${RESET}"
  exit 0
fi
