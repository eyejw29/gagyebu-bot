"""
가계부 CMS v2.1 — 텔레그램 봇 서버
문자 메시지를 받아서 파싱 → 저장 → 대시보드 연동

[파이프라인]
카드결제 → SMS → SMS Forwarder → 텔레그램 그룹 → 이 서버 → JSON 저장

[수정 가이드]
- 카드사 추가: CARD_NAMES에 추가
- 카테고리 추가: CATEGORY_MAP에 추가
- 새 문자 형식: MULTILINE_FIELDS에 키워드 추가
- 거래유형 추가: TX_TYPE_KEYWORDS에 추가
"""

import os
import json
import re
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request
import threading

# ============================================================
# 설정
# ============================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))
DATA_FILE = "transactions.json"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # 텔레그램 알림 보낼 채팅/그룹 ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("가계부봇")

# ============================================================
# 대시보드 HTML (웹 UI)
# ============================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>가계부 CMS</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; }
.header { background:linear-gradient(135deg,#1e293b,#334155); padding:20px; text-align:center; border-bottom:2px solid #3b82f6; }
.header h1 { font-size:1.5rem; color:#60a5fa; }
.header p { font-size:0.85rem; color:#94a3b8; margin-top:4px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; padding:16px; }
.card { background:#1e293b; border-radius:12px; padding:16px; text-align:center; border:1px solid #334155; }
.card .label { font-size:0.75rem; color:#94a3b8; margin-bottom:6px; }
.card .value { font-size:1.4rem; font-weight:700; color:#f1f5f9; }
.card .value.income { color:#34d399; }
.card .value.expense { color:#f87171; }
.section { padding:0 16px 16px; }
.section h2 { font-size:1rem; color:#94a3b8; margin-bottom:10px; padding-bottom:6px; border-bottom:1px solid #334155; }
.cat-bar { display:flex; align-items:center; margin-bottom:8px; }
.cat-bar .name { width:60px; font-size:0.8rem; color:#cbd5e1; }
.cat-bar .bar-bg { flex:1; height:22px; background:#1e293b; border-radius:6px; overflow:hidden; margin:0 8px; }
.cat-bar .bar-fill { height:100%; border-radius:6px; background:linear-gradient(90deg,#3b82f6,#60a5fa); transition:width 0.5s; }
.cat-bar .amt { font-size:0.8rem; color:#94a3b8; width:80px; text-align:right; }
.tx-list { list-style:none; }
.tx-item { background:#1e293b; border-radius:10px; padding:12px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; border:1px solid #334155; }
.tx-left .store { font-size:0.9rem; font-weight:600; color:#f1f5f9; }
.tx-left .meta { font-size:0.75rem; color:#64748b; margin-top:2px; }
.tx-right .amount { font-size:1rem; font-weight:700; }
.tx-right .amount.expense { color:#f87171; }
.tx-right .amount.income { color:#34d399; }
.tx-right .cat { font-size:0.7rem; color:#64748b; text-align:right; margin-top:2px; }
.refresh-btn { display:block; margin:16px auto; padding:10px 24px; background:#3b82f6; color:white; border:none; border-radius:8px; font-size:0.9rem; cursor:pointer; }
.refresh-btn:active { background:#2563eb; }
.empty { text-align:center; color:#64748b; padding:40px 16px; font-size:0.9rem; }
.loading { text-align:center; color:#64748b; padding:40px; }
</style>
</head>
<body>
<div class="header">
  <h1>가계부 CMS v2.1</h1>
  <p id="statusText">로딩 중...</p>
</div>
<div class="cards" id="summaryCards"></div>
<div class="section" id="catSection" style="display:none">
  <h2>카테고리별 지출</h2>
  <div id="catBars"></div>
</div>
<div class="section">
  <h2>최근 거래 내역</h2>
  <ul class="tx-list" id="txList"><li class="loading">데이터 불러오는 중...</li></ul>
</div>
<button class="refresh-btn" onclick="loadData()">새로고침</button>

<script>
const API = window.location.origin;

function formatMoney(n) {
  return n.toLocaleString('ko-KR') + '원';
}

function getToday() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function getMonth() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0');
}

async function loadData() {
  try {
    const res = await fetch(API + '/api/transactions');
    const txs = await res.json();

    const today = getToday();
    const month = getMonth();
    const todayTxs = txs.filter(t => t.date === today && t.type !== '입금');
    const monthTxs = txs.filter(t => t.date && t.date.startsWith(month) && t.type !== '입금');
    const todayTotal = todayTxs.reduce((s,t) => s + t.amount, 0);
    const monthTotal = monthTxs.reduce((s,t) => s + t.amount, 0);

    // 요약 카드
    document.getElementById('summaryCards').innerHTML =
      '<div class="card"><div class="label">오늘 지출</div><div class="value expense">' + formatMoney(todayTotal) + '</div></div>' +
      '<div class="card"><div class="label">이번달 지출</div><div class="value expense">' + formatMoney(monthTotal) + '</div></div>' +
      '<div class="card"><div class="label">이번달 건수</div><div class="value">' + monthTxs.length + '건</div></div>' +
      '<div class="card"><div class="label">전체 기록</div><div class="value">' + txs.length + '건</div></div>';

    // 카테고리별 지출
    const cats = {};
    monthTxs.forEach(t => { cats[t.category] = (cats[t.category]||0) + t.amount; });
    const catEntries = Object.entries(cats).sort((a,b) => b[1]-a[1]);
    const maxCat = catEntries.length > 0 ? catEntries[0][1] : 1;

    if (catEntries.length > 0) {
      document.getElementById('catSection').style.display = 'block';
      document.getElementById('catBars').innerHTML = catEntries.map(([name, amt]) =>
        '<div class="cat-bar">' +
        '<span class="name">' + name + '</span>' +
        '<div class="bar-bg"><div class="bar-fill" style="width:' + (amt/maxCat*100) + '%"></div></div>' +
        '<span class="amt">' + formatMoney(amt) + '</span></div>'
      ).join('');
    }

    // 최근 거래 (최신 20건)
    const recent = txs.slice(-20).reverse();
    const listEl = document.getElementById('txList');
    if (recent.length === 0) {
      listEl.innerHTML = '<li class="empty">아직 기록된 거래가 없습니다</li>';
    } else {
      listEl.innerHTML = recent.map(t => {
        const isIncome = t.type === '입금';
        return '<li class="tx-item">' +
          '<div class="tx-left">' +
          '<div class="store">' + (t.store||'알수없음') + '</div>' +
          '<div class="meta">' + t.date + ' | ' + (t.card||'기타') + '카드</div></div>' +
          '<div class="tx-right">' +
          '<div class="amount ' + (isIncome?'income':'expense') + '">' + (isIncome?'+':'-') + formatMoney(t.amount) + '</div>' +
          '<div class="cat">' + (t.category||'기타') + '</div></div></li>';
      }).join('');
    }

    document.getElementById('statusText').textContent = '마지막 업데이트: ' + new Date().toLocaleTimeString('ko-KR');
  } catch(e) {
    document.getElementById('txList').innerHTML = '<li class="empty">데이터를 불러올 수 없습니다</li>';
    document.getElementById('statusText').textContent = '연결 실패';
  }
}

loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>"""


# ============================================================
# ② Parser 설정값 (여기만 수정하면 됨)
# ============================================================

# --- 카드사 이름 매핑 (문자에서 추출된 값 → 표시 이름) ---
# 추가 방법: "문자에나오는이름": "표시할이름"
CARD_NAMES = {
    "하나": "하나", "신한": "신한", "삼성": "삼성", "현대": "현대",
    "롯데": "롯데", "우리": "우리", "국민": "국민", "KB": "KB국민",
    "NH": "NH농협", "BC": "BC", "카카오뱅크": "카카오뱅크",
    "토스": "토스", "씨티": "씨티",
}

# --- 카테고리 자동 분류 (키워드 → 카테고리) ---
# 추가 방법: "가맹점키워드": "카테고리명"
CATEGORY_MAP = {
    # 식비
    "배달의민족": "식비", "요기요": "식비", "쿠팡이츠": "식비", "맥도날드": "식비",
    "버거킹": "식비", "롯데리아": "식비", "파리바게뜨": "식비", "뚜레쥬르": "식비",
    "이삭토스트": "식비", "김밥천국": "식비", "편의점": "식비", "CU": "식비",
    "GS25": "식비", "세븐일레븐": "식비", "이마트24": "식비", "bbq": "식비",
    "치킨": "식비", "피자": "식비", "족발": "식비", "분식": "식비",
    # 카페
    "스타벅스": "카페", "투썸": "카페", "이디야": "카페", "메가커피": "카페",
    "컴포즈": "카페", "빽다방": "카페", "할리스": "카페", "카페": "카페",
    "커피": "카페",
    # 교통
    "택시": "교통", "카카오택시": "교통", "티머니": "교통", "주유": "교통",
    "GS칼텍스": "교통", "SK에너지": "교통", "현대오일": "교통", "주차": "교통",
    "하이패스": "교통", "석유": "교통", "주유소": "교통", "셀프": "교통",
    # 쇼핑
    "쿠팡": "쇼핑", "네이버페이": "쇼핑", "무신사": "쇼핑", "올리브영": "쇼핑",
    "다이소": "쇼핑", "이마트": "쇼핑", "홈플러스": "쇼핑", "코스트코": "쇼핑",
    "롯데마트": "쇼핑",
    # 구독/통신
    "넷플릭스": "구독", "유튜브": "구독", "멜론": "구독", "스포티파이": "구독",
    "KT": "통신", "SKT": "통신", "LGU": "통신",
    # 의료
    "병원": "의료", "약국": "의료", "의원": "의료", "치과": "의료", "안과": "의료",
    # 생활
    "관리비": "생활", "전기": "생활", "가스": "생활", "수도": "생활",
    # 술/유흥
    "호프": "유흥", "주점": "유흥", "노래방": "유흥", "당구": "유흥",
}

# --- 거래유형 감지 키워드 ---
# 추가 방법: ("키워드1|키워드2", "유형명")
TX_TYPE_KEYWORDS = [
    (r"입금|이체입금|급여|월급|상여", "입금"),
    (r"이체|송금", "이체"),
    # 기본값은 "지출"
]

# --- 멀티라인 문자 필드 매핑 ---
# 카드사마다 라벨이 다를 수 있으므로 여러 키워드 대응
# 추가 방법: 리스트에 새 키워드 추가
MULTILINE_FIELDS = {
    "amount": [r"금액\s*([\d,]+)원"],
    "card": [r"카드\s+(.+?)(?:\n|$)"],
    "store": [r"(?:사용처|가맹점|이용처|적요)\s+(.+?)(?:\n|$)"],
    "datetime": [
        r"(?:거래시간|이용시간|일시|거래일시)\s*(\d{2})/(\d{2})\s+(\d{2}):(\d{2})",
    ],
    "date_only": [r"(\d{2})/(\d{2})"],
}

# --- 멀티라인 감지 키워드 (2개 이상 매치시 멀티라인으로 판단) ---
MULTILINE_DETECT = ["금액", "사용처", "거래시간", "거래종류", "거래구분",
                     "이용시간", "가맹점", "이용처", "거래일시"]

# --- 한 줄 형식 정규식 패턴 ---
ONELINE_PATTERNS = [
    # [카드사] 승인 금액 가맹점
    r"(?:\[?)(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)(?:\]?)\s*(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    # 카드사 금액원 승인 가맹점
    r"(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)\s*([\d,]+)원\s*(?:승인|결제|출금)\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    # 일반 패턴: 금액 + 가맹점
    r"(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
]


# ============================================================
# ② Parser 로직
# ============================================================
def _detect_tx_type(text):
    """거래 유형 감지 (지출/입금/이체)"""
    for pattern, tx_type in TX_TYPE_KEYWORDS:
        if re.search(pattern, text):
            return tx_type
    return "지출"


def _extract_card_name(raw):
    """카드 원문에서 카드사 이름 추출 (하나2*6* → 하나)"""
    cleaned = re.sub(r"[\d\*\-]+", "", raw).strip()
    # CARD_NAMES에서 매칭
    for key, display in CARD_NAMES.items():
        if key in cleaned:
            return display
    return cleaned if cleaned else "기타"


def _classify_category(text, store):
    """가맹점/문자 내용으로 카테고리 자동 분류"""
    for keyword, category in CATEGORY_MAP.items():
        if keyword in store or keyword in text:
            return category
    return "기타"


def _make_result(text):
    """빈 결과 템플릿 생성"""
    return {
        "raw": text,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "amount": 0,
        "store": "",
        "card": "기타",
        "category": "기타",
        "type": _detect_tx_type(text),
        "member": "은재",
        "parsed": False,
    }


def _parse_multiline(text):
    """멀티라인 카드 문자 파싱

    대응 형식 예시:
    금액 60,000원 / 카드 하나2*6* / 사용처 동광석유(주)대야 / 거래시간 03/28 18:40
    """
    r = _make_result(text)

    # 금액
    for pattern in MULTILINE_FIELDS["amount"]:
        m = re.search(pattern, text)
        if m:
            r["amount"] = int(m.group(1).replace(",", ""))
            break

    # 카드
    for pattern in MULTILINE_FIELDS["card"]:
        m = re.search(pattern, text)
        if m:
            r["card"] = _extract_card_name(m.group(1))
            break

    # 사용처
    for pattern in MULTILINE_FIELDS["store"]:
        m = re.search(pattern, text)
        if m:
            r["store"] = m.group(1).strip()
            break

    # 거래시간 (날짜+시간)
    for pattern in MULTILINE_FIELDS["datetime"]:
        m = re.search(pattern, text)
        if m:
            month, day, hour, minute = m.groups()
            r["date"] = f"{datetime.now().year}-{month}-{day}"
            r["time"] = f"{hour}:{minute}"
            break
    else:
        # 날짜만이라도
        for pattern in MULTILINE_FIELDS["date_only"]:
            m = re.search(pattern, text)
            if m:
                month, day = m.groups()
                r["date"] = f"{datetime.now().year}-{month}-{day}"
                break

    # 파싱 성공 판단
    if r["amount"] > 0:
        r["parsed"] = True
        if not r["store"]:
            r["store"] = "알수없음"

    return r


def _parse_oneline(text):
    """한 줄 형식 카드 문자 파싱

    대응 형식 예시:
    [신한] 승인 15,000원 스타벅스 03/28
    """
    r = _make_result(text)

    # 날짜
    date_match = re.search(r"(\d{2})[/.](\d{2})", text)
    if date_match:
        m, d = date_match.groups()
        r["date"] = f"{datetime.now().year}-{m}-{d}"

    # 패턴 매칭
    for pattern in ONELINE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                r["card"] = _extract_card_name(groups[0])
                r["amount"] = int(groups[1].replace(",", ""))
                r["store"] = groups[2].strip()
            elif len(groups) == 2:
                r["amount"] = int(groups[0].replace(",", ""))
                r["store"] = groups[1].strip()
            r["parsed"] = True
            break

    # 최후 안전망: 금액만이라도 추출
    if not r["parsed"]:
        amount_match = re.search(r"([\d,]+)원", text)
        if amount_match:
            r["amount"] = int(amount_match.group(1).replace(",", ""))
            r["parsed"] = True

    return r


def parse_sms(text):
    """카드 문자 파싱 메인 함수 (모든 형식 자동 감지)

    1순위: 멀티라인 형식 (금액/사용처/거래시간 등 라벨 기반)
    2순위: 한 줄 형식 ([카드사] 승인 금액 가맹점)
    3순위: 금액만 추출 (안전망)
    """
    # 멀티라인 감지
    hit_count = sum(1 for kw in MULTILINE_DETECT if kw in text)
    if hit_count >= 2:
        result = _parse_multiline(text)
        if result["parsed"]:
            result["category"] = _classify_category(text, result["store"])
            return result

    # 한 줄 형식
    result = _parse_oneline(text)
    result["category"] = _classify_category(text, result["store"])
    return result


# ============================================================
# ③ Storage (저장)
# ============================================================
def load_transactions():
    """저장된 거래 내역 불러오기"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_transaction(tx):
    """거래 내역 저장"""
    data = load_transactions()
    tx["id"] = len(data) + 1
    tx["created_at"] = datetime.now().isoformat()
    data.append(tx)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"저장 완료: {tx['store']} {tx['amount']}원 [{tx['category']}] ({tx['type']})")
    return tx


# ============================================================
# ① Collector (수집) — 텔레그램 봇
# ============================================================
def telegram_api(method, data=None):
    """텔레그램 API 호출"""
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if data:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error(f"Telegram API 오류: {e}")
        return None


def send_message(chat_id, text):
    """텔레그램 메시지 전송"""
    telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    })


def handle_telegram_message(message):
    """텔레그램 메시지 처리 (전체 파이프라인)"""
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if not text:
        send_message(chat_id, "텍스트 메시지를 보내주세요.")
        return

    # ---- 명령어 처리 ----
    if text == "/start":
        send_message(chat_id, (
            "🏠 <b>가계부 봇</b>에 오신 걸 환영합니다!\n\n"
            "카드 문자를 이 채팅방에 전달하면\n"
            "자동으로 파싱해서 가계부에 기록합니다.\n\n"
            "📌 <b>명령어</b>\n"
            "/today - 오늘 지출 요약\n"
            "/month - 이번 달 요약\n"
            "/recent - 최근 5건"
        ))
        return

    if text == "/today":
        today = datetime.now().strftime("%Y-%m-%d")
        txs = [t for t in load_transactions() if t["date"] == today]
        if not txs:
            send_message(chat_id, "📊 오늘 기록된 지출이 없습니다.")
            return
        total = sum(t["amount"] for t in txs)
        lines = [f"📊 <b>오늘 지출 요약</b> ({today})\n"]
        for t in txs:
            lines.append(f"• {t['store']} — {t['amount']:,}원 [{t['category']}]")
        lines.append(f"\n💰 합계: <b>{total:,}원</b> ({len(txs)}건)")
        send_message(chat_id, "\n".join(lines))
        return

    if text == "/month":
        month_prefix = datetime.now().strftime("%Y-%m")
        txs = [t for t in load_transactions() if t["date"].startswith(month_prefix)]
        if not txs:
            send_message(chat_id, "📊 이번 달 기록된 지출이 없습니다.")
            return
        total = sum(t["amount"] for t in txs)
        cats = {}
        for t in txs:
            cats[t["category"]] = cats.get(t["category"], 0) + t["amount"]
        lines = [f"📊 <b>이번 달 요약</b> ({month_prefix})\n"]
        for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
            pct = amt / total * 100
            lines.append(f"• {cat}: {amt:,}원 ({pct:.0f}%)")
        lines.append(f"\n💰 합계: <b>{total:,}원</b> ({len(txs)}건)")
        send_message(chat_id, "\n".join(lines))
        return

    if text == "/recent":
        txs = load_transactions()[-5:]
        if not txs:
            send_message(chat_id, "📊 기록된 지출이 없습니다.")
            return
        lines = ["📋 <b>최근 5건</b>\n"]
        for t in reversed(txs):
            lines.append(f"• [{t['date']}] {t['store']} — {t['amount']:,}원 [{t['category']}]")
        send_message(chat_id, "\n".join(lines))
        return

    # ---- 카드 문자 처리 (핵심 파이프라인) ----
    # Collector → Parser → Storage
    tx = parse_sms(text)

    if tx["parsed"] and tx["amount"] > 0:
        saved = save_transaction(tx)

        # 거래 유형별 응답
        type_config = {
            "입금": ("💵", "입금 기록 완료!"),
            "이체": ("🔄", "이체 기록 완료!"),
            "지출": ("✅", "기록 완료!"),
        }
        emoji, label = type_config.get(saved["type"], ("✅", "기록 완료!"))

        send_message(chat_id, (
            f"{emoji} <b>{label}</b>\n\n"
            f"🏪 {saved['store']}\n"
            f"💳 {saved['card']}카드\n"
            f"💰 {saved['amount']:,}원\n"
            f"📂 {saved['category']}\n"
            f"📅 {saved['date']}"
        ))
    else:
        send_message(chat_id, (
            "❓ 파싱에 실패했습니다.\n"
            "카드 문자를 그대로 전달해주세요.\n\n"
            f"받은 메시지: {text[:100]}"
        ))


# ============================================================
# 웹 서버 (Dashboard API + Telegram Webhook)
# ============================================================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        # API: 거래 내역 조회 (Dashboard용)
        if parsed.path == "/api/transactions":
            data = load_transactions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # API: 서버 상태 확인
        if parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            status = {
                "status": "running",
                "transactions": len(load_transactions()),
                "timestamp": datetime.now().isoformat()
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        # GET 방식 SMS 수신 (Macrodroid 웹사이트 열기용)
        # URL: /api/sms?text=금액 60,000원...
        if parsed.path == "/api/sms":
            params = parse_qs(parsed.query)
            sms_text = params.get("text", [""])[0]
            log.info(f"SMS GET 수신: {sms_text[:100]}")

            if sms_text:
                tx = parse_sms(sms_text)
                if tx["parsed"] and tx["amount"] > 0:
                    saved = save_transaction(tx)
                    if CHAT_ID:
                        type_config = {
                            "입금": ("💵", "입금 기록 완료!"),
                            "이체": ("🔄", "이체 기록 완료!"),
                            "지출": ("✅", "기록 완료!"),
                        }
                        emoji, label = type_config.get(saved["type"], ("✅", "기록 완료!"))
                        send_message(CHAT_ID, (
                            f"{emoji} <b>{label}</b>\n\n"
                            f"🏪 {saved['store']}\n"
                            f"💳 {saved['card']}카드\n"
                            f"💰 {saved['amount']:,}원\n"
                            f"📂 {saved['category']}\n"
                            f"📅 {saved['date']}"
                        ))

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("OK".encode("utf-8"))
            return

        # 대시보드 페이지
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

    def do_POST(self):
        # Telegram Webhook
        if self.path == f"/webhook/{TOKEN}":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if "message" in body:
                handle_telegram_message(body["message"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # Macrodroid SMS 수신 엔드포인트
        # Macrodroid에서 HTTP POST → /api/sms 로 문자 내용 전송
        if self.path == "/api/sms":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                log.info(f"SMS 수신: {raw[:100]}")

                # JSON 또는 plain text 모두 지원
                content_type = self.headers.get("Content-Type", "")
                if "json" in content_type:
                    body = json.loads(raw)
                    sms_text = body.get("sms", body.get("text", body.get("message", raw)))
                else:
                    sms_text = raw

                # 파싱 → 저장
                tx = parse_sms(sms_text)

                if tx["parsed"] and tx["amount"] > 0:
                    saved = save_transaction(tx)

                    # 텔레그램으로 알림 전송
                    notify_chat_id = CHAT_ID
                    if notify_chat_id:
                        type_config = {
                            "입금": ("💵", "입금 기록 완료!"),
                            "이체": ("🔄", "이체 기록 완료!"),
                            "지출": ("✅", "기록 완료!"),
                        }
                        emoji, label = type_config.get(saved["type"], ("✅", "기록 완료!"))
                        send_message(notify_chat_id, (
                            f"{emoji} <b>{label}</b>\n\n"
                            f"🏪 {saved['store']}\n"
                            f"💳 {saved['card']}카드\n"
                            f"💰 {saved['amount']:,}원\n"
                            f"📂 {saved['category']}\n"
                            f"📅 {saved['date']}"
                        ))

                    # 성공 응답
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "ok": True,
                        "store": saved["store"],
                        "amount": saved["amount"],
                        "category": saved["category"]
                    }, ensure_ascii=False).encode("utf-8"))
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "ok": False,
                        "error": "파싱 실패",
                        "raw": sms_text[:100]
                    }, ensure_ascii=False).encode("utf-8"))

            except Exception as e:
                log.error(f"SMS 처리 오류: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        log.info(f"HTTP: {args[0]}")


# ============================================================
# 메인 실행
# ============================================================
def setup_webhook(base_url):
    """텔레그램 웹훅 설정"""
    webhook_url = f"{base_url}/webhook/{TOKEN}"
    result = telegram_api("setWebhook", {"url": webhook_url})
    if result and result.get("ok"):
        log.info(f"✅ 웹훅 설정 완료: {webhook_url}")
    else:
        log.error(f"❌ 웹훅 설정 실패: {result}")


if __name__ == "__main__":
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN 환경변수를 설정해주세요!")
        print("예: export TELEGRAM_BOT_TOKEN='your-token-here'")
        exit(1)

    # 서버 URL이 있으면 웹훅 설정 (배포 시)
    base_url = os.environ.get("BASE_URL", "")
    if base_url:
        setup_webhook(base_url)
    else:
        log.info("⚠️ BASE_URL 미설정 — 웹훅 없이 로컬 모드로 실행")

    log.info(f"🚀 가계부 봇 서버 시작 (포트: {PORT})")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
