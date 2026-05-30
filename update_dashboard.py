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
# 1. Claude API로 데이터 수집
# ══════════════════════════════════════════════════════════════════
PROMPT = f"""오늘 날짜: {TODAY_KR} ({TODAY_ISO})

당신은 국내 Top-tier 증권사 리서치센터 수석 애널리스트입니다.
웹 검색으로 오늘 기준 최신 정보를 꼼꼼히 수집하고, 아래 JSON 형식으로만 응답하세요.

━━ 검색 품질 기준 (반드시 준수) ━━
1. 날짜 검증: 모든 기사/보고서의 실제 발행일을 URL 직접 접속으로 확인. 추정 금지.
2. 증권가 시각: 단순 뉴스 요약 아님. 주가·목표가·실적·밸류에이션 관점에서 해석.
3. 최신성: 오늘~이번 주 발행 자료 우선. 오래된 내용이면 날짜미확인 처리.
4. 구체성: "상승" → "XX% 상승, 종가 YY원", "호조" → "영업이익 N조원, 전년비 +N%".
5. 출처: 증권사 리포트·한국경제·매경·파이낸셜뉴스·연합인포맥스·Reuters 우선.
6. 종목별 검색: 각 보유 종목마다 ① 최신 애널리스트 목표가 변경 ② 실적 서프라이즈/쇼크
   ③ 수급 특이사항 ④ 섹터 이슈를 개별적으로 검색할 것.
7. 주요 뉴스: 글로벌 매크로(연준·환율·유가)·한국 수출입·지정학 이슈 포함.

━━ 날짜 표기 규칙 ━━
- 오늘 발행  → date_type:"today",  date_display:"오늘"
- 이번주 발행 → date_type:"week",   date_display:"MM/DD" (예: "05/03")
- 그보다 오래 → date_type:"old",    date_display:"YYYY.MM" (예: "2026.03")
- 날짜 확인 불가 → date_type:"old", date_display:"날짜미확인"
반드시 링크 접속 후 실제 발행일을 확인하고 표기할 것.

━━ 응답 JSON 구조 ━━
{{
  "news":       [주요뉴스 7개],
  "mz_trends":  [마케팅·MZ·소비 트렌드 7개],
  "ai_trends":  [AI 기술·산업 트렌드 7개],
  "stocks":     [중요 이슈 있는 보유 종목만, 자잘한 건 제외. 이슈 없으면 빈 배열],
  "charts": {{
    "rate":  {{"labels":[...], "korea":[...], "us":[...]}},
    "m2":    {{"labels":[...], "data":[...]}},
    "apt":   {{"labels":[...], "data":[...]}},
    "kospi": {{"labels":[...], "data":[...]}}
  }}
}}

뉴스/트렌드 항목 구조:
{{
  "title":        "제목 (40자 이내)",
  "body":         "2~3문장 요약",
  "tags":         ["태그1","태그2"],
  "date_type":    "today|week|old",
  "date_display": "오늘|MM/DD|YYYY.MM|날짜미확인",
  "source_name":  "출처명",
  "source_url":   "https://실제접속가능한URL"
}}

stocks 항목 구조:
{{
  "ticker":       "종목코드 또는 티커",
  "company":      "회사명",
  "icon":         "이모지",
  "change_label": "▲ 상승이유 또는 ▼ 하락이유 또는 — 보합",
  "change_type":  "up|down|flat",
  "is_important": true,
  "title":        "제목",
  "body":         "요약",
  "tags":         [...],
  "date_type":    "...",
  "date_display": "...",
  "source_name":  "...",
  "source_url":   "https://..."
}}

보유 종목: {json.dumps(PORTFOLIO, ensure_ascii=False)}

차트 데이터 수집 기준:
- rate:  한국·미국 기준금리 (2025.01~현재, 단위: %, 회의 미개최 월은 직전값 유지)
- m2:    한국 M2 총통화량 (2025.01~최근 발표 월, 단위: 조원, 약 2개월 시차)
- apt:   서울 아파트 매매거래량 (2025.01~최근, 단위: 건, 전월세 제외)
- kospi: 코스피 주봉 종가 (최근 3개월)

JSON만 출력. 코드블록·설명 텍스트 없이 순수 JSON만."""


def fetch_data() -> dict:
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("anthropic 패키지 필요: pip install anthropic")

    if not ANTHROPIC_KEY:
        sys.exit("ANTHROPIC_API_KEY 없음")

    client = Anthropic(api_key=ANTHROPIC_KEY)
    log.info("Claude API 호출 시작")
    print("뉴스 검색 중... (1~2분 소요)")

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 25}],
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

    data = json.loads(raw)
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
    archive_dir = THIS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    files = sorted(archive_dir.glob("*.html"), reverse=True)
    links = f'<a class="archive-link active" href="index.html">{TODAY_SHORT}</a>\n'
    for f in files[:10]:
        try:
            dt = datetime.strptime(f.stem, "%Y-%m-%d")
            links += f'  <a class="archive-link" href="archive/{f.stem}.html">{dt.strftime("%m/%d")}</a>\n'
        except Exception:
            pass
    return links


def build_html(data: dict) -> str:
    c   = data["charts"]
    r   = c["rate"]
    m2  = c["m2"]
    apt = c["apt"]
    ksp = c["kospi"]

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
    <span style="font-size:11px;color:var(--sub);">매일 오전 6시 자동 업데이트</span>
  </div>
  <div class="header-right">skstmfflago-ops</div>
</header>

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
<footer>DailyBrief · {TODAY.strftime('%Y.%m.%d')} · 매일 오전 6시 자동 업데이트 · <a href="https://skstmfflago-ops.github.io/daily-dashboard/" style="color:var(--sub);text-decoration:none;">skstmfflago-ops.github.io/daily-dashboard</a></footer>

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

    # (a) 오늘 dashboard.html → archive/ 에 백업
    archive_dir = THIS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    src = THIS_DIR / "dashboard.html"
    if src.exists():
        dst = archive_dir / f"{TODAY_ISO}.html"
        dst.write_bytes(src.read_bytes())
        print(f"아카이브 저장: {dst.name}")

    # (b) Claude로 최신 데이터 수집
    data = fetch_data()
    print(
        f"수집 완료 — 뉴스 {len(data.get('news',[]))} / "
        f"MZ {len(data.get('mz_trends',[]))} / "
        f"AI {len(data.get('ai_trends',[]))} / "
        f"주식 {len(data.get('stocks',[]))}"
    )

    # (c) HTML 생성 & 저장
    html = build_html(data)
    (THIS_DIR / "dashboard.html").write_text(html, encoding="utf-8")
    print("dashboard.html 저장 완료")
    log.info("dashboard.html 저장 완료")

    # (d) GitHub Pages 배포
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
