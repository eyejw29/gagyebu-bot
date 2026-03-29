"""
가계부 CMS v2.1 — API 서버 (①②③④ 부서)
Collector + Parser + Storage + Analyzer

[역할] 24시간 대기하면서:
  - Macrodroid에서 SMS 수신 (HTTP GET)
  - 텔레그램 명령어 처리 (Webhook)
  - Dashboard에 데이터 제공 (REST API)

[Dashboard는 별도 관리]
  - GitHub Pages에서 index.html로 배포
  - 이 서버의 API를 호출해서 데이터를 가져감
"""

import os
import json
import re
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request

# ============================================================
# 설정
# ============================================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))
DATA_FILE = "transactions.json"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("가계부봇")


# ============================================================
# ② Parser 설정값
# ============================================================

CARD_NAMES = {
    "하나": "하나", "신한": "신한", "삼성": "삼성", "현대": "현대",
    "롯데": "롯데", "우리": "우리", "국민": "국민", "KB": "KB국민",
    "NH": "NH농협", "BC": "BC", "카카오뱅크": "카카오뱅크",
    "토스": "토스", "씨티": "씨티",
}

CATEGORY_MAP = {
    "배달의민족": "식비", "요기요": "식비", "쿠팡이츠": "식비", "맥도날드": "식비",
    "버거킹": "식비", "롯데리아": "식비", "파리바게뜨": "식비", "뚜레쥬르": "식비",
    "이삭토스트": "식비", "김밥천국": "식비", "편의점": "식비", "CU": "식비",
    "GS25": "식비", "세븐일레븐": "식비", "이마트24": "식비", "bbq": "식비",
    "치킨": "식비", "피자": "식비", "족발": "식비", "분식": "식비",
    "스타벅스": "카페", "투썸": "카페", "이디야": "카페", "메가커피": "카페",
    "컴포즈": "카페", "빽다방": "카페", "할리스": "카페", "카페": "카페", "커피": "카페",
    "택시": "교통", "카카오택시": "교통", "티머니": "교통", "주유": "교통",
    "GS칼텍스": "교통", "SK에너지": "교통", "현대오일": "교통", "주차": "교통",
    "하이패스": "교통", "석유": "교통", "주유소": "교통", "셀프": "교통",
    "쿠팡": "쇼핑", "네이버페이": "쇼핑", "무신사": "쇼핑", "올리브영": "쇼핑",
    "다이소": "쇼핑", "이마트": "쇼핑", "홈플러스": "쇼핑", "코스트코": "쇼핑", "롯데마트": "쇼핑",
    "넷플릭스": "구독", "유튜브": "구독", "멜론": "구독", "스포티파이": "구독",
    "KT": "통신", "SKT": "통신", "LGU": "통신",
    "병원": "의료", "약국": "의료", "의원": "의료", "치과": "의료", "안과": "의료",
    "관리비": "생활", "전기": "생활", "가스": "생활", "수도": "생활",
    "호프": "유흥", "주점": "유흥", "노래방": "유흥", "당구": "유흥",
}

TX_TYPE_KEYWORDS = [
    (r"입금|이체입금|급여|월급|상여", "입금"),
    (r"이체|송금", "이체"),
]

MULTILINE_DETECT = ["금액", "사용처", "거래시간", "거래종류", "거래구분",
                     "이용시간", "가맹점", "이용처", "거래일시"]

# 삼성카드 전용 형식 (예: "삼성5851승인 오*재 1,000원 일시불 03/29 09:17 애플컴퍼니 누적...")
# 공백 유무 모두 대응: "삼성5851승인" / "삼성5851 승인"
SAMSUNG_PATTERN = r"(삼성|삼성카드)\d{2,4}\s*승인\s+[^\d\s]+\s*\n?\s*([\d,]+)원\s*일시불\s*\n?\s*(\d{2}/\d{2})\s+(\d{2}:\d{2})\s+(.+?)(?:\s*\n?\s*누적|$)"

ONELINE_PATTERNS = [
    r"(?:\[?)(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)(?:\]?)\s*(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    r"(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)\s*([\d,]+)원\s*(?:승인|결제|출금)\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    r"(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
]


# ============================================================
# ② Parser 로직
# ============================================================
def _detect_tx_type(text):
    for pattern, tx_type in TX_TYPE_KEYWORDS:
        if re.search(pattern, text):
            return tx_type
    return "지출"


def _extract_card_name(raw):
    cleaned = re.sub(r"[\d\*\-]+", "", raw).strip()
    for key, display in CARD_NAMES.items():
        if key in cleaned:
            return display
    return cleaned if cleaned else "기타"


def _classify_category(text, store):
    for keyword, category in CATEGORY_MAP.items():
        if keyword in store or keyword in text:
            return category
    return "기타"


def _make_result(text):
    return {
        "raw": text,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "amount": 0, "store": "", "card": "기타",
        "category": "기타", "type": _detect_tx_type(text),
        "member": "은재", "parsed": False,
    }


def _parse_multiline(text):
    r = _make_result(text)
    m = re.search(r"금액\s*([\d,]+)원", text)
    if m:
        r["amount"] = int(m.group(1).replace(",", ""))
    m = re.search(r"카드\s+(.+?)(?:\n|$)", text)
    if m:
        r["card"] = _extract_card_name(m.group(1))
    m = re.search(r"(?:사용처|가맹점|이용처|적요)\s+(.+?)(?:거래시간|이용시간|일시|누적|잔액|\n|$)", text)
    if m:
        r["store"] = m.group(1).strip()
    m = re.search(r"(?:거래시간|이용시간|일시|거래일시)\s*(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", text)
    if m:
        month, day, hour, minute = m.groups()
        r["date"] = f"{datetime.now().year}-{month}-{day}"
        r["time"] = f"{hour}:{minute}"
    else:
        m = re.search(r"(\d{2})/(\d{2})", text)
        if m:
            r["date"] = f"{datetime.now().year}-{m.group(1)}-{m.group(2)}"
    if r["amount"] > 0:
        r["parsed"] = True
        if not r["store"]:
            r["store"] = "알수없음"
    return r


def _parse_oneline(text):
    r = _make_result(text)
    m = re.search(r"(\d{2})[/.](\d{2})", text)
    if m:
        r["date"] = f"{datetime.now().year}-{m.group(1)}-{m.group(2)}"
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
    if not r["parsed"]:
        m = re.search(r"([\d,]+)원", text)
        if m:
            r["amount"] = int(m.group(1).replace(",", ""))
            r["parsed"] = True
    return r


def _parse_samsung(text):
    """삼성카드 전용 파서 (예: 삼성5851승인 오*재 / 1,000원 일시불 / 03/29 09:17 애플컴퍼니 / 누적...)"""
    m = re.search(SAMSUNG_PATTERN, text, re.DOTALL)
    if not m:
        return None
    r = _make_result(text)
    r["card"] = "삼성"
    r["amount"] = int(m.group(2).replace(",", ""))
    date_str = m.group(3)  # "03/29"
    month, day = date_str.split("/")
    r["date"] = f"{datetime.now().year}-{month}-{day}"
    r["time"] = m.group(4)  # "09:17"
    r["store"] = m.group(5).strip()
    r["parsed"] = True
    r["category"] = _classify_category(text, r["store"])
    return r


def parse_sms(text):
    # 삼성카드 전용 형식 먼저 체크
    samsung = _parse_samsung(text)
    if samsung:
        return samsung
    # 기존 멀티라인 파서
    hit = sum(1 for kw in MULTILINE_DETECT if kw in text)
    if hit >= 2:
        result = _parse_multiline(text)
        if result["parsed"]:
            result["category"] = _classify_category(text, result["store"])
            return result
    result = _parse_oneline(text)
    result["category"] = _classify_category(text, result["store"])
    return result


# ============================================================
# ③ Storage (JSON 로컬 + Firebase 영구 저장)
# ============================================================
FIREBASE_URL = os.environ.get("FIREBASE_URL", "")  # 예: https://xxx.firebaseio.com


def _firebase_request(path, method="GET", data=None):
    """Firebase Realtime Database REST API 호출"""
    if not FIREBASE_URL:
        return None
    url = f"{FIREBASE_URL}/{path}.json"
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"} if payload else {},
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error(f"Firebase 오류 ({method} {path}): {e}")
        return None


def _sync_from_firebase():
    """Firebase에서 전체 거래 데이터를 로컬로 동기화"""
    result = _firebase_request("transactions")
    if result and isinstance(result, dict):
        txs = list(result.values())
        txs.sort(key=lambda x: x.get("id", 0))
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(txs, f, ensure_ascii=False, indent=2)
        log.info(f"Firebase → 로컬 동기화 완료: {len(txs)}건")
        return txs
    return []


def load_transactions():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data:
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # 로컬 데이터 없으면 Firebase에서 복원 시도
    if FIREBASE_URL:
        log.info("로컬 데이터 없음 — Firebase에서 복원 시도")
        return _sync_from_firebase()
    return []


def _save_to_firebase(tx):
    """Firebase에 거래 데이터 영구 저장"""
    if not FIREBASE_URL:
        log.warning("FIREBASE_URL 미설정 — Firebase 저장 건너뜀")
        return False
    try:
        result = _firebase_request(f"transactions/tx_{tx['id']}", method="PUT", data=tx)
        if result:
            log.info(f"Firebase 저장 완료: {tx['store']} {tx['amount']}원")
            return True
        return False
    except Exception as e:
        log.error(f"Firebase 저장 실패 (로컬 저장은 정상): {e}")
        return False


def save_transaction(tx):
    data = load_transactions()
    tx["id"] = len(data) + 1
    tx["created_at"] = datetime.now().isoformat()
    data.append(tx)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"저장 완료: {tx['store']} {tx['amount']}원 [{tx['category']}] ({tx['type']})")
    # Firebase 영구 저장 (실패해도 로컬 저장은 유지)
    _save_to_firebase(tx)
    return tx


# ============================================================
# ① Collector — 텔레그램 봇
# ============================================================
def telegram_api(method, data=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if data:
        req = urllib.request.Request(
            url, data=json.dumps(data).encode("utf-8"),
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
    telegram_api("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})


def _tx_response(saved, chat_id):
    """저장된 거래를 텔레그램으로 알림"""
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


def handle_telegram_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    if not text:
        return

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
            lines.append(f"• {cat}: {amt:,}원 ({amt/total*100:.0f}%)")
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

    # 카드 문자 처리
    tx = parse_sms(text)
    if tx["parsed"] and tx["amount"] > 0:
        saved = save_transaction(tx)
        _tx_response(saved, chat_id)
    else:
        send_message(chat_id, f"❓ 파싱 실패\n받은 메시지: {text[:100]}")


# ============================================================
# 웹 서버 (API + Webhook)
# ============================================================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        # ④ Analyzer — 거래 내역 API (Dashboard용)
        if parsed.path == "/api/transactions":
            data = load_transactions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # ④ Analyzer — 서버 상태 API
        if parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "running",
                "transactions": len(load_transactions()),
                "timestamp": datetime.now().isoformat()
            }).encode("utf-8"))
            return

        # ① Collector — Macrodroid SMS 수신 (GET)
        if parsed.path == "/api/sms":
            params = parse_qs(parsed.query)
            sms_text = params.get("text", [""])[0]
            log.info(f"SMS GET 수신: {sms_text[:100]}")
            if sms_text:
                tx = parse_sms(sms_text)
                if tx["parsed"] and tx["amount"] > 0:
                    saved = save_transaction(tx)
                    if CHAT_ID:
                        _tx_response(saved, CHAT_ID)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("OK".encode("utf-8"))
            return

        # 기본 페이지
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("가계부 CMS API 서버 가동 중".encode("utf-8"))

    def do_POST(self):
        # ① Collector — Telegram Webhook
        if self.path == f"/webhook/{TOKEN}":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if "message" in body:
                handle_telegram_message(body["message"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # ① Collector — Macrodroid SMS 수신 (POST)
        if self.path == "/api/sms":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                log.info(f"SMS POST 수신: {raw[:100]}")
                content_type = self.headers.get("Content-Type", "")
                sms_text = json.loads(raw).get("text", raw) if "json" in content_type else raw
                tx = parse_sms(sms_text)
                if tx["parsed"] and tx["amount"] > 0:
                    saved = save_transaction(tx)
                    if CHAT_ID:
                        _tx_response(saved, CHAT_ID)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except Exception as e:
                log.error(f"SMS 처리 오류: {e}")
                self.send_response(500)
                self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        """UptimeRobot 등 모니터링 서비스의 HEAD 요청 처리"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_OPTIONS(self):
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
    webhook_url = f"{base_url}/webhook/{TOKEN}"
    result = telegram_api("setWebhook", {"url": webhook_url})
    if result and result.get("ok"):
        log.info(f"웹훅 설정 완료: {webhook_url}")
    else:
        log.error(f"웹훅 설정 실패: {result}")


if __name__ == "__main__":
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN 환경변수를 설정해주세요!")
        exit(1)
    base_url = os.environ.get("BASE_URL", "")
    if base_url:
        setup_webhook(base_url)
    log.info(f"가계부 API 서버 시작 (포트: {PORT})")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
