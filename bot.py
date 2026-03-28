"""
가계부 CMS v2.0 — 텔레그램 봇 서버
문자 메시지를 받아서 파싱 → 저장 → 대시보드 연동
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("가계부봇")

# ============================================================
# ②번 부서: Parser (정형화)
# 카드 문자 → 구조화된 거래 데이터
# ============================================================
CATEGORY_MAP = {
    # 식비
    "배달의민족": "식비", "요기요": "식비", "쿠팡이츠": "식비", "맥도날드": "식비",
    "버거킹": "식비", "롯데리아": "식비", "파리바게뜨": "식비", "뚜레쥬르": "식비",
    "이삭토스트": "식비", "김밥천국": "식비", "편의점": "식비", "CU": "식비",
    "GS25": "식비", "세븐일레븐": "식비", "이마트24": "식비",
    # 카페
    "스타벅스": "카페", "투썸": "카페", "이디야": "카페", "메가커피": "카페",
    "컴포즈": "카페", "빽다방": "카페", "할리스": "카페", "카페": "카페",
    # 교통
    "택시": "교통", "카카오택시": "교통", "티머니": "교통", "주유": "교통",
    "GS칼텍스": "교통", "SK에너지": "교통", "현대오일": "교통", "주차": "교통",
    "하이패스": "교통",
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
}

# 카드사 패턴
CARD_PATTERNS = [
    # [카드사] 승인 금액 가맹점
    r"(?:\[?)(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)(?:\]?)\s*(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    # 카드사 금액원 승인 가맹점
    r"(\w+카드|KB|신한|삼성|현대|롯데|하나|우리|NH|BC|카카오뱅크|토스)\s*([\d,]+)원\s*(?:승인|결제|출금)\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
    # 일반 패턴: 금액 + 가맹점
    r"(?:승인|결제|출금)\s*([\d,]+)원?\s+(.+?)(?:\s+\d{2}[:/]\d{2}|\s*$)",
]


def parse_sms(text):
    """카드 문자를 파싱해서 거래 데이터로 변환"""
    result = {
        "raw": text,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "amount": 0,
        "store": "",
        "card": "기타",
        "category": "기타",
        "member": "은재",  # 기본값
        "parsed": False
    }

    # 날짜 추출 시도
    date_match = re.search(r"(\d{2})[/.](\d{2})", text)
    if date_match:
        month, day = date_match.groups()
        year = datetime.now().year
        result["date"] = f"{year}-{month}-{day}"

    # 카드사 + 금액 + 가맹점 추출
    for pattern in CARD_PATTERNS:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                result["card"] = groups[0].replace("카드", "").strip()
                result["amount"] = int(groups[1].replace(",", ""))
                result["store"] = groups[2].strip()
            elif len(groups) == 2:
                result["amount"] = int(groups[0].replace(",", ""))
                result["store"] = groups[1].strip()
            result["parsed"] = True
            break

    # 금액만이라도 추출
    if not result["parsed"]:
        amount_match = re.search(r"([\d,]+)원", text)
        if amount_match:
            result["amount"] = int(amount_match.group(1).replace(",", ""))
            result["parsed"] = True

    # 카테고리 자동 분류
    for keyword, category in CATEGORY_MAP.items():
        if keyword in text or keyword in result["store"]:
            result["category"] = category
            break

    return result


# ============================================================
# ③번 부서: Storage (저장)
# JSON 파일 기반 거래 내역 저장
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
    log.info(f"저장 완료: {tx['store']} {tx['amount']}원 [{tx['category']}]")
    return tx


# ============================================================
# ①번 부서: Collector (수집) — 텔레그램 봇
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

    # /start 명령
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

    # /today 명령
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

    # /month 명령
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

    # /recent 명령
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

    # ========== 카드 문자 처리 (핵심 파이프라인) ==========
    # Collector → Parser → Storage
    tx = parse_sms(text)

    if tx["parsed"] and tx["amount"] > 0:
        saved = save_transaction(tx)
        send_message(chat_id, (
            f"✅ <b>기록 완료!</b>\n\n"
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

        # 기본 페이지
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("🏠 가계부 봇 서버 가동 중!".encode("utf-8"))

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
