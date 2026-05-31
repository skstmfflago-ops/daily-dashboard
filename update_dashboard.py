"""
update_dashboard.py
───────────────────
매일 오전 6시 자동 실행 → 오늘 뉴스 웹 검색 → dashboard.html 생성 → GitHub Pages 배포
Windows 작업 스케줄러로 등록해 사용 (update_dashboard_run.bat 참조)
"""

import base64
import io
import json
import logging
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 한국어 출력 ───────────────────────────────────────────────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

THIS_DIR = Path(__file__).parent

# ── 로그 파일 (같은 폴더에 update_dashboard.log) ─────────────────
logging.basicConfig(
    filename=str(THIS_DIR / "update_dashboard.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger(__name__)


# ── 환경변수 로드 ─────────────────────────────────────────────────
def _load_env():
    for name in (".env", "config.txt"):
        p = THIS_DIR / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and not os.environ.get(k):
                os.environ[k] = v
        break


_load_env()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = os.environ.get("GITHUB_OWNER", "skstmfflago-ops")
GITHUB_REPO   = os.environ.get("GITHUB_REPO",  "daily-dashboard")

# ── 날짜 ─────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
TODAY_KR    = f"{TODAY.year}년 {TODAY.month}월 {TODAY.day}일 {WEEKDAYS[TODAY.weekday()]}"
TODAY_SHORT = TODAY.strftime("%m/%d")
TODAY_ISO   = TODAY.strftime("%Y-%m-%d")

# ── 보유 종목 ────────────────────────────────────────────────────
PORTFOLIO = {
    "국내주식": ["SK하이닉스(000660)", "삼성전자(005930)", "삼성전기(009150)", "현대자동차(005380)", "에이피알(278470)", "삼양식품(003230)"],
    "미국주식": ["테슬라(TSLA)", "알파벳(GOOGL)"],
}


# ══════════════════════════════════════════════════════════════════
# 실시간 시장 데이터 (Yahoo Finance — AI 아님, 실제 API)
# ══════════════════════════════════════════════════════════════════
def _yf_price(symbol: str) -> float | None:
    """Yahoo Finance에서 현재가 1개 조회"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=2d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        # None 제거 후 마지막 값
        vals = [v for v in closes if v is not None]
        return round(vals[-1], 2) if vals else None
    except Exception as e:
        log.warning("Yahoo Finance 조회 실패 %s: %s", symbol, e)
        return None

def fetch_market_data() -> dict:
    """코스피·환율·보유종목 현재가를 실제 API에서 가져옴"""
    print("시장 데이터 조회 중 (Yahoo Finance)...")
    return {
        "kospi":   _yf_price("^KS11"),        # 코스피 지수
        "usdkrw":  _yf_price("KRW=X"),        # 원달러 환율
        "hynix":   _yf_price("000660.KS"),    # SK하이닉스
        "samsung": _yf_price("005930.KS"),    # 삼성전자
        "semec":   _yf_price("009150.KS"),    # 삼성전기
        "hyundai": _yf_price("005380.KS"),    # 현대자동차
        "apr":     _yf_price("278470.KS"),    # 에이피알
        "samyang": _yf_price("003230.KS"),    # 삼양식품
        "tsla":    _yf_price("TSLA"),         # 테슬라
        "googl":   _yf_price("GOOGL"),        # 알파벳
    }


# ── 차트 데이터 (chart_data.json 에서 로드, 매월 1일 갱신) ────────
CHART_DATA_FILE = THIS_DIR / "chart_data.json"

DEFAULT_CHARTS = {
    "rate": {
        "labels": ["25.01","02","03","04","05","06","07","08","09","10","11","12","26.01","02","03","04","05"],
        "korea":  [3.00,2.75,2.75,2.75,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50,2.50],
        "us":     [4.38,4.38,4.38,4.38,4.38,4.25,4.00,3.88,3.75,3.75,3.75,3.63,3.63,3.63,3.63,3.63,3.63],
    },
    "m2": {
        "labels": ["25.01","02","03","04","05","06","07","08","09","10","11","12","26.01","02","03"],
        "data":   [4280,4305,4325,4348,4368,4390,4408,4422,4430,4480,4510,4541,4565,4582,4600],
    },
    "apt": {
        "labels": ["25.01","02","03","04","05","06","07","08","09","10","11","12","26.01","02","03"],
        "data":   [174,820,4742,3980,5210,4850,6100,7200,8400,6100,4300,3800,8200,9500,7800],
    },
    "kospi": {
        "labels": ["03/09","03/16","03/23","03/30","04/06","04/13","04/21","04/27","05/03","05/11","05/15","05/27"],
        "data":   [5640,5750,5840,5930,5980,6020,6050,6200,6700,7800,8000,8228],
    },
    "updated": "2026-05",
}

def load_chart_data() -> dict:
    if CHART_DATA_FILE.exists():
        try:
            return json.loads(CHART_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_CHARTS

CHART_UPDATE_PROMPT = f"""오늘: {TODAY_ISO}
최신 차트 데이터를 검색해서 JSON으로만 응답하세요.

{{
  "rate":  {{"labels":["25.01",...,"현재월"], "korea":[...], "us":[...]}},
  "m2":    {{"labels":["25.01",...,"최근발표월"], "data":[...]}},
  "apt":   {{"labels":["25.01",...,"최근발표월"], "data":[...]}},
  "kospi": {{"labels":["최근3개월 주봉MM/DD 형식"], "data":[...]}},
  "updated": "{TODAY.strftime('%Y-%m')}"
}}

- rate: 한국·미국 기준금리(%), 회의 없는 달은 직전값 유지, 2025.01부터 현재까지
- m2: 한국 M2 총통화량(조원), 약 2개월 발표 시차, 2025.01부터
- apt: 서울 아파트 매매거래량(건), 전월세 제외, 2025.01부터
- kospi: 최근 3개월 주봉 종가
JSON만 출력."""

def fetch_chart_data(client) -> dict:
    print("월 1회 차트 데이터 업데이트 중...")
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
        messages=[{"role": "user", "content": CHART_UPDATE_PROMPT}],
    )
    raw = ""
    for block in resp.content:
        if block.type == "text":
            raw = block.text
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    charts = json.loads(raw.strip())
    CHART_DATA_FILE.write_text(json.dumps(charts, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("차트 데이터 업데이트 완료: %s", charts.get("updated"))
    print(f"차트 데이터 업데이트 완료 ({charts.get('updated')})")
    return charts


def safe_fetch_chart_data(client) -> dict:
    """차트 업데이트 실패 시 기존 데이터로 안전하게 폴백"""
    try:
        return fetch_chart_data(client)
    except Exception as e:
        log.warning("차트 업데이트 실패, 기존 데이터 사용: %s", e)
        print(f"[경고] 차트 업데이트 실패 → 기존 데이터 사용 ({e})")
        return load_chart_data()


# ══════════════════════════════════════════════════════════════════
# 1. Claude API로 데이터 수집
# ══════════════════════════════════════════════════════════════════
PROMPT = f"""오늘: {TODAY_ISO} ({TODAY_KR})

━━ 작업 순서 (반드시 이 순서로) ━━
1. 아래 지정된 키워드로 웹 검색을 실행한다.
2. 검색 결과(제목·스니펫·URL)에서 확인된 내용만 JSON에 기록한다.
3. 검색 결과에 없는 내용은 절대 작성하지 않는다. 지어내거나 추측하면 안 된다.
4. source_url = 검색 결과에 나타난 URL을 그대로 복사한다. URL을 직접 만들거나 수정하면 안 된다.
5. 찾지 못한 항목은 배열에서 제외한다. 7개를 채우려고 내용을 만들지 말 것.
6. 1주일 이내({(datetime.now(KST)-timedelta(days=7)).strftime('%Y-%m-%d')} 이후) 기사만 포함.

━━ 검색 키워드 목록 ━━

[주요뉴스 — 아래 쿼리로 검색]
· "한국 경제 뉴스 {TODAY_ISO}"
· "미국 연준 금리 최신 뉴스"
· "글로벌 증시 뉴스 오늘"
· "한국 수출 무역 뉴스 최신"

[마케팅·MZ 트렌드 — 아래 쿼리로 검색]
· "MZ세대 소비 트렌드 2026"
· "마케팅 트렌드 인플루언서 최신"
· "Z세대 SNS 유행 최신"

[AI 트렌드 — 아래 쿼리로 검색]
· "인공지능 AI 뉴스 최신 2026"
· "ChatGPT Claude AI 업데이트 최신"
· "AI 반도체 기술 뉴스"

[보유종목 이슈 — 각 종목별 검색]
· "SK하이닉스 뉴스 최신"        (반도체·HBM·DRAM 회사)
· "삼성전자 뉴스 최신"          (반도체·스마트폰)
· "삼성전기 뉴스 최신"          (MLCC·기판, 뷰티·배터리 무관)
· "현대자동차 뉴스 최신"        (완성차·전기차)
· "에이피알 뷰티 뉴스 최신"     (화장품·메디큐브, 배터리 절대 무관)
· "삼양식품 불닭 뉴스 최신"     (라면·식품)
· "테슬라 Tesla 뉴스 최신"
· "알파벳 구글 뉴스 최신"

━━ 응답 JSON ━━
{{
  "news":      [검색 결과에서 찾은 주요뉴스, 최대 7개],
  "mz_trends": [검색 결과에서 찾은 MZ트렌드, 최대 7개],
  "ai_trends": [검색 결과에서 찾은 AI트렌드, 최대 7개],
  "stocks":    [검색 결과에서 이슈 확인된 종목만, 없으면 빈배열]
}}

항목 구조 (news·mz_trends·ai_trends):
{{"title":"검색결과기사제목(40자이내)","body":"검색결과스니펫기반요약(50자이내)","tags":["태그1","태그2"],"date_type":"today|week|old","date_display":"오늘|MM/DD|YYYY.MM","source_name":"매체명","source_url":"검색결과에나온URL그대로"}}

stocks 항목:
{{"ticker":"코드","company":"회사명","icon":"이모지","change_label":"▲이유","change_type":"up|down|flat","is_important":true,"title":"제목(40자이내)","body":"요약(50자이내)","tags":["태그"],"date_type":"today|week|old","date_display":"날짜","source_name":"매체명","source_url":"검색결과URL그대로"}}

date_type 기준: 오늘 발행→"today"/오늘표시, 이번주→"week"/MM/DD, 그 이전→"old"/YYYY.MM

JSON만 출력. 코드블록·설명 없이."""


def fetch_data(client) -> dict:
    log.info("Claude API 호출 시작")
    print("뉴스 검색 중... (1~2분 소요)")

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": PROMPT}],
    )

    raw = ""
    for block in resp.content:
        if block.type == "text":
            raw = block.text

    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[JSON 파싱 오류] {e}")
        print(f"[응답 길이] {len(raw)} chars")
        print(f"[응답 마지막 200자] ...{raw[-200:]}")
        raise

    # ── 출처 URL 검증: 도메인 루트 URL 걸러내기 ──────────────────
    def _is_article_url(url: str) -> bool:
        if not url or not url.startswith("http"):
            return False
        from urllib.parse import urlparse
        p = urlparse(url)
        # path가 없거나 "/" 하나뿐이면 홈페이지
        return len(p.path.strip("/")) > 5

    def _clean_items(items: list) -> list:
        cleaned = []
        for it in items:
            url = it.get("source_url", "")
            if not _is_article_url(url):
                log.warning("홈페이지 URL 제거: %s → %s", it.get("title",""), url)
                it["source_url"] = "#"
                it["source_name"] = "출처미확인"
            cleaned.append(it)
        return cleaned

    for key in ("news", "mz_trends", "ai_trends", "stocks"):
        if key in data:
            data[key] = _clean_items(data[key])

    log.info(
        "수집 완료: 뉴스 %d / MZ %d / AI %d / 주식 %d",
        len(data.get("news", [])),
        len(data.get("mz_trends", [])),
        len(data.get("ai_trends", [])),
        len(data.get("stocks", [])),
    )
    return data


# ══════════════════════════════════════════════════════════════════
# 2. HTML 빌드
# ══════════════════════════════════════════════════════════════════
def _date_chip(item: dict) -> str:
    cls = {"today": "date-today", "week": "date-week"}.get(item.get("date_type", "old"), "date-old")
    return f'<span class="date-chip {cls}">{item.get("date_display", "날짜미확인")}</span>'


def _tags(items):
    return "".join(f'<span class="tag">{t}</span>' for t in items)


def news_cards(items: list) -> str:
    html = ""
    for i, it in enumerate(items, 1):
        html += f"""
    <div class="card">
      <div class="card-title"><span class="card-num">{i:02d}</span>{it['title']}</div>
      <div class="card-body">{it['body']}</div>
      <div class="card-footer">
        <div class="card-tags">{_tags(it.get('tags',[]))}{_date_chip(it)}</div>
        <a class="source-link" href="{it.get('source_url','#')}" target="_blank">{it.get('source_name','출처')}</a>
      </div>
    </div>"""
    return html


def stock_section(stocks: list) -> str:
    if not stocks:
        return '<div style="padding:20px;color:var(--sub);font-size:12px;text-align:center;background:var(--surface);border:1px solid var(--border);border-radius:14px;">오늘 보유 종목 주요 이슈 없음</div>'
    html = ""
    for s in stocks:
        cc = {"up": "stock-up", "down": "stock-down"}.get(s.get("change_type", ""), "stock-flat")
        alert = '<span class="stock-alert">🔥 중요</span>&nbsp;' if s.get("is_important") else ""
        html += f"""
  <div class="section stock">
    <div class="section-header">
      <div class="section-icon">{s.get('icon','📌')}</div>
      <div class="section-title">{s['company']}</div>
      <span class="stock-ticker">{s.get('ticker','')}</span>
      <span class="stock-change {cc}">{s.get('change_label','')}</span>
    </div>
    <div class="card">
      <div class="card-title">{alert}{s['title']}</div>
      <div class="card-body">{s['body']}</div>
      <div class="card-footer">
        <div class="card-tags">{_tags(s.get('tags',[]))}{_date_chip(s)}</div>
        <a class="source-link" href="{s.get('source_url','#')}" target="_blank">{s.get('source_name','출처')}</a>
      </div>
    </div>
  </div>"""
    return html


def build_archive_links() -> str:
    """로컬 archive/ 폴더 + GitHub API 두 곳에서 아카이브 목록 수집"""
    archive_dir = THIS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)

    # 로컬 파일 목록
    stems = set()
    for f in archive_dir.glob("*.html"):
        stems.add(f.stem)

    # GitHub API에서도 목록 조회 (로컬에 없을 수 있음)
    if GITHUB_TOKEN:
        try:
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/archive"
            hdrs = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req) as r:
                for item in json.load(r):
                    name = item.get("name", "")
                    if name.endswith(".html"):
                        stems.add(name[:-5])
        except Exception:
            pass

    # 날짜 정렬 (최신순), 오늘 날짜 제외
    sorted_stems = sorted(
        [s for s in stems if s != TODAY_ISO],
        reverse=True
    )[:14]  # 최대 14개

    links = f'<a class="archive-link active" href="index.html">{TODAY_SHORT}</a>\n'
    for stem in sorted_stems:
        try:
            dt = datetime.strptime(stem, "%Y-%m-%d")
            links += f'  <a class="archive-link" href="archive/{stem}.html">{dt.strftime("%m/%d")}</a>\n'
        except Exception:
            pass
    return links


def _fmt(v, unit="", decimals=0):
    if v is None: return "—"
    fmt = f"{v:,.{decimals}f}"
    return f"{fmt}{unit}"

def build_html(data: dict, charts: dict, market: dict | None = None) -> str:
    market = market or {}
    r   = charts["rate"]
    m2  = charts["m2"]
    apt = charts["apt"]
    ksp = charts["kospi"]

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Dashboard – {TODAY.strftime('%Y.%m.%d')}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3250;--news:#4f8ef7;--mz:#f76f8e;--ai:#6fe0b0;--econ:#f0b429;--stock:#b07ef7;--text:#e8eaf6;--sub:#8b90b0;--tag:#2a2f4a;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--text);font-family:'Pretendard','Apple SD Gothic Neo','Noto Sans KR',sans-serif;font-size:14px;line-height:1.6;}}
  header{{background:linear-gradient(135deg,#1a1d27,#12152a);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}}
  .logo{{font-size:19px;font-weight:800;letter-spacing:-.5px;}}.logo span{{color:#4f8ef7;}}
  .header-center{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}}
  .date-badge{{background:var(--tag);border:1px solid var(--border);border-radius:20px;padding:3px 13px;font-size:12px;color:var(--sub);}}
  .live-dot{{width:6px;height:6px;border-radius:50%;background:#6fe0b0;display:inline-block;animation:pulse 2s infinite;}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
  .header-right{{font-size:11px;color:var(--sub);}}
  .archive-bar{{background:#12152a;border-bottom:1px solid var(--border);padding:8px 28px;display:flex;align-items:center;gap:10px;overflow-x:auto;scrollbar-width:none;}}
  .archive-bar::-webkit-scrollbar{{display:none;}}
  .archive-label{{font-size:11px;color:var(--sub);white-space:nowrap;flex-shrink:0;}}
  .archive-link{{font-size:11px;padding:3px 10px;border-radius:12px;text-decoration:none;white-space:nowrap;flex-shrink:0;border:1px solid var(--border);color:var(--sub);transition:all .15s;}}
  .archive-link:hover{{border-color:#4f8ef7;color:#4f8ef7;}}.archive-link.active{{background:#4f8ef7;color:#fff;border-color:#4f8ef7;}}
  .container{{max-width:1440px;margin:0 auto;padding:22px 28px;}}
  .grid-3{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:18px;align-items:start;}}
  .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px;}}
  .section{{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;}}
  .section-header{{padding:13px 18px 11px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:9px;}}
  .section-icon{{width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0;}}
  .news .section-icon{{background:rgba(79,142,247,.15);}}.mz .section-icon{{background:rgba(247,111,142,.15);}}.ai .section-icon{{background:rgba(111,224,176,.15);}}.econ .section-icon{{background:rgba(240,180,41,.15);}}.stock .section-icon{{background:rgba(176,126,247,.15);}}
  .section-title{{font-size:13.5px;font-weight:700;}}
  .news .section-title{{color:var(--news);}}.mz .section-title{{color:var(--mz);}}.ai .section-title{{color:var(--ai);}}.econ .section-title{{color:var(--econ);}}.stock .section-title{{color:var(--stock);}}
  .section-count{{margin-left:auto;background:var(--tag);border-radius:10px;padding:2px 9px;font-size:11px;color:var(--sub);}}
  .card{{padding:13px 18px;border-bottom:1px solid var(--border);transition:background .15s;}}.card:last-child{{border-bottom:none;}}.card:hover{{background:var(--surface2);}}
  .card-title{{font-size:13px;font-weight:700;line-height:1.4;margin-bottom:5px;display:flex;align-items:flex-start;gap:7px;}}
  .card-num{{font-size:10px;font-weight:800;padding:1px 5px;border-radius:4px;flex-shrink:0;margin-top:2px;}}
  .news .card-num{{background:rgba(79,142,247,.2);color:var(--news);}}.mz .card-num{{background:rgba(247,111,142,.2);color:var(--mz);}}.ai .card-num{{background:rgba(111,224,176,.2);color:var(--ai);}}
  .card-body{{font-size:12px;color:var(--sub);line-height:1.6;margin-bottom:7px;}}
  .card-footer{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:5px;}}
  .card-tags{{display:flex;gap:5px;flex-wrap:wrap;align-items:center;}}
  .tag{{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600;background:var(--tag);color:var(--sub);}}
  .news .tag{{border-left:2px solid var(--news);}}.mz .tag{{border-left:2px solid var(--mz);}}.ai .tag{{border-left:2px solid var(--ai);}}.stock .tag{{border-left:2px solid var(--stock);}}
  .date-chip{{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:10px;flex-shrink:0;}}
  .date-today{{background:rgba(111,224,176,.2);color:#6fe0b0;border:1px solid rgba(111,224,176,.4);}}.date-week{{background:rgba(240,180,41,.15);color:#f0b429;border:1px solid rgba(240,180,41,.3);}}.date-old{{background:var(--tag);color:var(--sub);border:1px solid var(--border);}}
  .source-link{{font-size:10px;color:var(--sub);text-decoration:none;display:flex;align-items:center;gap:2px;opacity:.7;transition:opacity .15s;white-space:nowrap;}}.source-link:hover{{opacity:1;color:var(--text);}}.source-link::before{{content:'↗';font-size:9px;}}
  .chart-wrap{{padding:14px 18px 18px;}}.chart-meta{{font-size:10.5px;color:var(--sub);margin-bottom:4px;display:flex;justify-content:space-between;}}.chart-note{{font-size:9.5px;color:#555a7a;margin-top:6px;}}
  canvas{{max-height:170px;}}
  .divider{{text-align:center;padding:14px 0 2px;font-size:11px;color:var(--sub);letter-spacing:.06em;font-weight:600;}}.divider span{{background:var(--tag);padding:3px 14px;border-radius:10px;}}
  .stock-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:18px;}}
  .stock-ticker{{font-size:10px;font-weight:800;padding:1px 6px;border-radius:4px;background:rgba(176,126,247,.18);color:var(--stock);flex-shrink:0;white-space:nowrap;}}
  .stock-change{{font-size:11px;font-weight:700;margin-left:auto;}}.stock-up{{color:#6fe0b0;}}.stock-down{{color:#f76f8e;}}.stock-flat{{color:var(--sub);}}
  .stock-alert{{display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:700;padding:1px 7px;border-radius:4px;background:rgba(247,111,78,.18);color:#f7784e;border:1px solid rgba(247,111,78,.3);flex-shrink:0;}}
  footer{{text-align:center;padding:18px;color:var(--sub);font-size:11px;border-top:1px solid var(--border);margin-top:8px;}}
  @media(max-width:900px){{.grid-3,.grid-2{{grid-template-columns:1fr;}}.container{{padding:12px 14px;}}header{{flex-wrap:wrap;gap:8px;}}.stock-grid{{grid-template-columns:1fr;}}}}
</style>
</head>
<body>

<header>
  <div class="logo">Daily<span>Brief</span></div>
  <div class="header-center">
    <div class="date-badge">{TODAY_KR}</div>
    <span class="live-dot"></span>
    <span style="font-size:11px;color:var(--sub);">매일 자정 자동 업데이트</span>
  </div>
  <div class="header-right">skstmfflago-ops</div>
</header>
<div style="background:#12152a;border-bottom:1px solid var(--border);padding:6px 28px;display:flex;gap:20px;overflow-x:auto;scrollbar-width:none;font-size:11px;align-items:center;">
  <span style="color:var(--sub);flex-shrink:0;">실시간(전일종가)</span>
  <span style="flex-shrink:0;">🇰🇷 코스피 <b style="color:#4f8ef7">{_fmt(market.get('kospi'))}</b></span>
  <span style="flex-shrink:0;">💱 원달러 <b style="color:#f0b429">{_fmt(market.get('usdkrw'))}원</b></span>
  <span style="flex-shrink:0;">SK하이닉스 <b style="color:#6fe0b0">{_fmt(market.get('hynix'))}원</b></span>
  <span style="flex-shrink:0;">삼성전자 <b style="color:#6fe0b0">{_fmt(market.get('samsung'))}원</b></span>
  <span style="flex-shrink:0;">삼성전기 <b style="color:#6fe0b0">{_fmt(market.get('semec'))}원</b></span>
  <span style="flex-shrink:0;">현대차 <b style="color:#6fe0b0">{_fmt(market.get('hyundai'))}원</b></span>
  <span style="flex-shrink:0;">에이피알 <b style="color:#6fe0b0">{_fmt(market.get('apr'))}원</b></span>
  <span style="flex-shrink:0;">삼양식품 <b style="color:#6fe0b0">{_fmt(market.get('samyang'))}원</b></span>
  <span style="flex-shrink:0;">TSLA <b style="color:#b07ef7">${_fmt(market.get('tsla'),decimals=2)}</b></span>
  <span style="flex-shrink:0;">GOOGL <b style="color:#b07ef7">${_fmt(market.get('googl'),decimals=2)}</b></span>
</div>

<div class="archive-bar">
  <span class="archive-label">이전 날짜:</span>
  {build_archive_links()}
</div>

<div class="container">

<div class="grid-3">
  <div class="section news">
    <div class="section-header">
      <div class="section-icon">📰</div>
      <div class="section-title">주요 뉴스</div>
      <div class="section-count">{len(data['news'])}</div>
    </div>
    {news_cards(data['news'])}
  </div>
  <div class="section mz">
    <div class="section-header">
      <div class="section-icon">💡</div>
      <div class="section-title">마케팅 · MZ 트렌드</div>
      <div class="section-count">{len(data['mz_trends'])}</div>
    </div>
    {news_cards(data['mz_trends'])}
  </div>
  <div class="section ai">
    <div class="section-header">
      <div class="section-icon">🤖</div>
      <div class="section-title">AI 트렌드</div>
      <div class="section-count">{len(data['ai_trends'])}</div>
    </div>
    {news_cards(data['ai_trends'])}
  </div>
</div>

<div class="divider"><span>💼 보유 주식 주요 이슈</span></div>
<div class="stock-grid">
{stock_section(data.get('stocks', []))}
</div>

<div class="divider"><span>📊 경제 지표 (월별)</span></div>
<div class="grid-2">
  <div class="section econ">
    <div class="section-header"><div class="section-icon">🏦</div><div class="section-title">한국 · 미국 기준금리 추이</div></div>
    <div class="chart-wrap">
      <div class="chart-meta"><span>단위: %</span><span>{r['labels'][0]} – {r['labels'][-1]}</span></div>
      <canvas id="rateChart"></canvas>
      <div class="chart-note">※ 한국은행·연준 공시 기준. 회의 미개최 월은 직전 결정값 유지.</div>
    </div>
  </div>
  <div class="section econ">
    <div class="section-header"><div class="section-icon">💰</div><div class="section-title">한국 총통화량 M2</div></div>
    <div class="chart-wrap">
      <div class="chart-meta"><span>단위: 조원</span><span>{m2['labels'][0]} – {m2['labels'][-1]}</span></div>
      <canvas id="m2Chart"></canvas>
      <div class="chart-note">※ 한국은행 발표 기준. 최신 월 수치는 약 2개월 발표 시차.</div>
    </div>
  </div>
</div>
<div class="grid-2">
  <div class="section econ">
    <div class="section-header"><div class="section-icon">🏠</div><div class="section-title">서울 아파트 매매거래량</div></div>
    <div class="chart-wrap">
      <div class="chart-meta"><span>단위: 건</span><span>{apt['labels'][0]} – {apt['labels'][-1]}</span></div>
      <canvas id="aptChart"></canvas>
      <div class="chart-note">※ 국토부 실거래가 공개시스템 기준. 매매거래량만 집계 (전월세 제외).</div>
    </div>
  </div>
  <div class="section econ">
    <div class="section-header"><div class="section-icon">📈</div><div class="section-title">코스피 주간 추이</div></div>
    <div class="chart-wrap">
      <div class="chart-meta"><span>단위: 포인트</span><span>{ksp['labels'][0]} – {ksp['labels'][-1]}</span></div>
      <canvas id="kospiChart"></canvas>
      <div class="chart-note">※ 한국거래소 종가 기준 (주봉).</div>
    </div>
  </div>
</div>

</div>
<footer>DailyBrief · {TODAY.strftime('%Y.%m.%d')} · 매일 자정 자동 업데이트 · <a href="https://skstmfflago-ops.github.io/daily-dashboard/" style="color:var(--sub);text-decoration:none;">skstmfflago-ops.github.io/daily-dashboard</a></footer>

<script>
const G={{color:'#8b90b0',grid:'rgba(46,50,80,.45)',font:{{size:10}}}};
const ax={{x:{{ticks:{{color:G.color,font:G.font}},grid:{{color:G.grid}}}},y:{{ticks:{{color:G.color,font:G.font}},grid:{{color:G.grid}}}}}};
new Chart(document.getElementById('rateChart'),{{type:'line',data:{{labels:{json.dumps(r['labels'],ensure_ascii=False)},datasets:[
  {{label:'한국 기준금리',data:{json.dumps(r['korea'])},borderColor:'#6fe0b0',backgroundColor:'rgba(111,224,176,.08)',borderWidth:2,pointRadius:3,tension:0.1,fill:true}},
  {{label:'미국 기준금리',data:{json.dumps(r['us'])},borderColor:'#4f8ef7',backgroundColor:'rgba(79,142,247,.06)',borderWidth:2,pointRadius:3,tension:0.1,fill:true}}
]}},options:{{plugins:{{legend:{{labels:{{color:G.color,font:G.font,boxWidth:10,padding:12}}}}}},scales:ax}}}});
new Chart(document.getElementById('m2Chart'),{{type:'line',data:{{labels:{json.dumps(m2['labels'],ensure_ascii=False)},datasets:[{{label:'M2(조원)',data:{json.dumps(m2['data'])},borderColor:'#f0b429',backgroundColor:'rgba(240,180,41,.1)',borderWidth:2,pointRadius:3,tension:0.4,fill:true}}]}},options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:G.color,font:G.font}},grid:{{color:G.grid}}}},y:{{ticks:{{color:G.color,font:G.font,callback:v=>v.toLocaleString()}},grid:{{color:G.grid}}}}}}}}}});
new Chart(document.getElementById('aptChart'),{{type:'bar',data:{{labels:{json.dumps(apt['labels'],ensure_ascii=False)},datasets:[{{label:'매매거래량(건)',data:{json.dumps(apt['data'])},backgroundColor:'rgba(240,180,41,.55)',borderColor:'#f0b429',borderWidth:1,borderRadius:3}}]}},options:{{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:G.color,font:G.font}},grid:{{color:G.grid}}}},y:{{ticks:{{color:G.color,font:G.font,callback:v=>v.toLocaleString()}},grid:{{color:G.grid}}}}}}}}}});
new Chart(document.getElementById('kospiChart'),{{type:'line',data:{{labels:{json.dumps(ksp['labels'],ensure_ascii=False)},datasets:[{{label:'코스피',data:{json.dumps(ksp['data'])},borderColor:'#4f8ef7',backgroundColor:'rgba(79,142,247,.1)',borderWidth:2,pointRadius:3,tension:0.4,fill:true}}]}},options:{{plugins:{{legend:{{display:false}}}},scales:ax}}}});
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
# 3. GitHub Pages 배포
# ══════════════════════════════════════════════════════════════════
def put_github_file(path: str, file_path: Path):
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN 없음, 배포 건너뜀")
        return

    content = base64.b64encode(file_path.read_bytes()).decode()
    sha = None
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            sha = json.load(r).get("sha")
    except Exception:
        pass

    body: dict = {"message": f"Auto-update {TODAY_ISO}", "content": content}
    if sha:
        body["sha"] = sha

    req2 = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="PUT",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req2) as r:
        json.load(r)
    print(f"  배포 완료: {path}")
    log.info("배포 완료: %s", path)


# ══════════════════════════════════════════════════════════════════
# 4. 메인
# ══════════════════════════════════════════════════════════════════
def main():
    print(f"=== DailyBrief 업데이트 시작 ({TODAY_KR}) ===")
    log.info("=== 업데이트 시작 %s ===", TODAY_KR)

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("anthropic 패키지 필요: pip install anthropic")
    if not ANTHROPIC_KEY:
        sys.exit("ANTHROPIC_API_KEY 없음")
    client = Anthropic(api_key=ANTHROPIC_KEY)

    # (a) 차트 데이터 로드 (매월 1일에만 API 갱신)
    if TODAY.day == 1:
        charts = safe_fetch_chart_data(client)
        if CHART_DATA_FILE.exists():
            put_github_file("chart_data.json", CHART_DATA_FILE)
    else:
        charts = load_chart_data()
        print(f"차트 데이터 로드 완료 (최근 갱신: {charts.get('updated','?')})")

    # (b) 현재 index.html → archive/ 에 백업 (GitHub Actions: index.html, 로컬: dashboard.html)
    archive_dir = THIS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    src = THIS_DIR / "index.html"
    if not src.exists():
        src = THIS_DIR / "dashboard.html"
    if src.exists():
        dst = archive_dir / f"{TODAY_ISO}.html"
        dst.write_bytes(src.read_bytes())
        print(f"아카이브 저장: {dst.name}")
    else:
        print("[경고] 아카이브할 현재 파일 없음")

    # (c) 실시간 시장 데이터 (Yahoo Finance — 100% 실제값)
    market = fetch_market_data()
    print(f"시장 데이터: 코스피={market.get('kospi')} 환율={market.get('usdkrw')}")

    # (d) Claude로 뉴스·종목 데이터 수집
    data = fetch_data(client)
    print(
        f"수집 완료 — 뉴스 {len(data.get('news',[]))} / "
        f"MZ {len(data.get('mz_trends',[]))} / "
        f"AI {len(data.get('ai_trends',[]))} / "
        f"주식 {len(data.get('stocks',[]))}"
    )

    # (f) HTML 생성 & 저장
    html = build_html(data, charts, market)
    (THIS_DIR / "dashboard.html").write_text(html, encoding="utf-8")
    print("dashboard.html 저장 완료")
    log.info("dashboard.html 저장 완료")

    # (g) GitHub Pages 배포
    print("GitHub Pages 배포 중...")
    put_github_file("index.html", THIS_DIR / "dashboard.html")
    archive_file = archive_dir / f"{TODAY_ISO}.html"
    if archive_file.exists():
        put_github_file(f"archive/{TODAY_ISO}.html", archive_file)

    print(f"\n완료! → https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/")
    log.info("=== 업데이트 완료 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("오류 발생: %s", e)
        print(f"\n오류: {e}")
        sys.exit(1)
