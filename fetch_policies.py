"""
경기도 청년정책 데이터 자동 갱신
매일 오전 9시 GitHub Actions에서 실행됩니다.

수집 경로:
1. 온통청년 API
2. 잡아바 (청년기본소득 등)
3. 경기복지포털 (고립은둔청년 등)
4. 경기청년포털
5. 31개 시군 공고 게시판
6. 네이버 뉴스 RSS
"""

# ── 전체 import (최상단에 모아서) ──────────────────────────
import sys
import os
import json
import re
import math
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import Counter

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Windows 콘솔의 cp949 기본 인코딩에서도 이모지/한글 출력이 깨지지 않도록 강제 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False
    print("⚠️ beautifulsoup4 없음 - 크롤링 일부 스킵")

# ── 상수 ────────────────────────────────────────────────────
API_KEY      = os.environ.get("API_KEY", "c937731f-99f2-489c-a334-07bbfff0da0d")
JOBABA_KEY   = os.environ.get("JOBABA_KEY", "231944106408426fa30737e055d48493")
YOUTH_API_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
# 구 API URL이 다운된 경우를 대비해 복수 엔드포인트 시도
BASE_URLS = [
    "https://www.youthcenter.go.kr/opi/youthPlcyList.do",
    "https://youth.go.kr/opi/youthPlcyList.do",
]
BASE_URL = BASE_URLS[0]
TODAY    = datetime.now()
CUR_M    = TODAY.month
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

FIELD_MAP = {
    "023010": "일자리",
    "023020": "주거",
    "023030": "교육·직업훈련",
    "023040": "금융·복지·문화",
    "023050": "참여·기반",
}

# 온통청년 API 대분류 → 분야 매핑
LCLSF_MAP = {
    "일자리": "일자리",
    "주거":   "주거",
    "교육":   "교육·직업훈련",
    "복지문화": "금융·복지·문화",
    "참여권리": "참여·기반",
    "창업":   "창업",
}

GYEONGGI_CITIES = [
    "수원","성남","의정부","안양","부천","광명","평택","동두천","안산",
    "고양","과천","구리","남양주","오산","시흥","군포","의왕","하남",
    "용인","파주","이천","안성","김포","화성","광주","양주","포천",
    "여주","연천","가평","양평","경기"
]

# ── 모집상태 판별 ────────────────────────────────────────────
def get_status(text):
    if not text or not text.strip():
        return "미정"
    t = re.sub(r'\s+', ' ', text).strip()

    if re.search(r'모집.?중|접수.?중|진행.?중', t): return "모집중"
    if re.search(r'마감|종료|완료', t):              return "마감"
    if re.search(r'연중|상시|수시|예산.?소진|자금.?소진|분기별|소진시까지', t): return "모집중"
    if re.search(r'신규.?모집.?없음|모집.?계획.?없음|미시행|해당없음', t): return "미정"

    m = re.search(r'~\s*(\d{4})[.\s]*(\d{1,2})[.\s]*(\d{1,2})', t)
    if m:
        try:
            end = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if end < TODAY: return "마감"
        except: pass

    # 종료일에 연도가 생략된 경우 (예: "2026.2. 23. ~ 3. 13." → 종료일 연도는
    # 시작일과 동일한 것으로 간주). 이걸 놓치면 종료일이 지난 사업도 시작일
    # 기준으로만 판단해 계속 "모집중"으로 남는 버그가 생긴다.
    m = re.search(r'(\d{4})[.\s]+(\d{1,2})[.\s]+(\d{1,2})\.?\s*~\s*(\d{1,2})[.\s]+(\d{1,2})\.?', t)
    if m:
        try:
            year = int(m.group(1))
            end = datetime(year, int(m.group(4)), int(m.group(5)))
            if end < TODAY: return "마감"
        except: pass

    m = re.search(r'(\d{4})[.\s]*(\d{1,2})[.\s]*(\d{1,2})', t)
    if m:
        try:
            start = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return "모집예정" if start > TODAY else "모집중"
        except: pass

    m = re.search(r'(\d{1,2})월.*?~.*?(\d{1,2})월', t)
    if m:
        sm, em = int(m.group(1)), int(m.group(2))
        if CUR_M > em:  return "마감"
        if CUR_M < sm:  return "모집예정"
        return "확인필요"

    # 종료일 없이 "YYYY년 M월 ~" 형태로 끝나는 경우 (상시/현재진행 시작일)
    m = re.search(r'(\d{4})년\s*(\d{1,2})월\s*~\s*$', t)
    if m:
        try:
            start = datetime(int(m.group(1)), int(m.group(2)), 1)
            return "모집예정" if start > TODAY else "모집중"
        except: pass

    if '상반기' in t: return "확인필요"
    if '하반기' in t: return "모집예정"

    m = re.search(r'(\d{1,2})월', t)
    if m:
        month = int(m.group(1))
        if month < CUR_M: return "마감"
        if month == CUR_M: return "확인필요"
        return "모집예정"

    return "미정"

# ── 온통청년 API (신규 엔드포인트) ──────────────────────────
# 경기도 우편번호 앞자리 (10000~18999)
def _is_gyeonggi(zip_str):
    if not zip_str:
        return False
    for z in zip_str.split(","):
        z = z.strip()
        if z.isdigit() and 10000 <= int(z) <= 18999:
            return True
    return False

# 다른 시도 소관 정책이 경기도 우편번호 대역과 우연히 겹쳐 "경기도"로
# 오분류되는 경우 방지 (예: 전남광주통합특별시 zipCd가 12xxx로 잡히는 사례)
_NON_GG_REGION_KW = ['서울','부산','대구','인천','대전','울산','세종',
                     '강원','충북','충청북도','충남','충청남도',
                     '전북','전라북도','전남','전라남도',
                     '경북','경상북도','경남','경상남도','제주']

def _mentions_other_region(name):
    if not name or '경기' in name:
        return False
    return any(kw in name for kw in _NON_GG_REGION_KW)

# "중앙정부" 배지는 전국 어디서나 신청 가능한 진짜 중앙부처 정책에만 붙인다.
# 온통청년 API는 경기도가 아니면 전부 "중앙정부"로 뭉뚱그려 놨는데, 실제로는
# 그 안에 다른 시/도의 지역 한정 정책이 대부분 섞여 있어 경기 청년에게는
# 의미가 없다. 운영기관명이나 사업명에 다른 지역명이 있으면 특정 지역 한정
# 정책으로 보고 아예 목록에서 제외한다.
def _is_other_region_local_policy(기관, 사업명):
    return _mentions_other_region(기관) or _mentions_other_region(사업명)

def fetch_api_page(page=1, per_page=100, keyword=""):
    params = {
        "apiKeyNm":  API_KEY,
        "pageIndex": page,
        "display":   per_page,
    }
    if keyword:
        params["plcyNm"] = keyword
    try:
        r = requests.get(YOUTH_API_URL, params=params, timeout=30)
        r.encoding = "utf-8"
        data = r.json()
        if data.get("resultCode") == 200:
            return data.get("result", {})
    except Exception as e:
        print(f"  온통청년 API 오류: {e}")
    return None

def _format_date(ymd):
    ymd = (ymd or "").strip()
    if len(ymd) == 8:
        return f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:]}"
    return ymd

def parse_api_item(item):
    lclsf = item.get("lclsfNm", "")
    분야 = LCLSF_MAP.get(lclsf, "청년정책")

    bgng = item.get("bizPrdBgngYmd", "").strip()
    end  = item.get("bizPrdEndYmd", "").strip()
    if bgng and end:
        시기 = f"{_format_date(bgng)} ~ {_format_date(end)}"
    elif bgng:
        시기 = f"{_format_date(bgng)} ~"
    else:
        시기 = item.get("bizPrdEtcCn", "")

    기관 = item.get("sprvsnInstCdNm", "") or item.get("operInstCdNm", "")
    사업명 = item.get("plcyNm", "")
    zip_str = item.get("zipCd", "")

    if _is_gyeonggi(zip_str) and not _is_other_region_local_policy(기관, 사업명):
        시군 = "경기도"
    elif _is_other_region_local_policy(기관, 사업명):
        return None  # 경기도 청년과 무관한 타 지역 한정 정책 제외
    else:
        시군 = "중앙정부"

    return {
        "시군":     시군,
        "분야":     분야,
        "사업명":   item.get("plcyNm", ""),
        "주요내용": (item.get("plcyExplnCn", "") or item.get("plcySprtCn", ""))[:500],
        "모집시기": 시기,
        "모집상태": get_status(시기),
        "신청방법": item.get("plcyAplyMthdCn", ""),
        "운영기관": 기관,
        "문의처":   item.get("inqCn", ""),
        "링크":     item.get("aplyUrlAddr", "") or item.get("refUrlAddr1", ""),
        "링크_모집":   item.get("aplyUrlAddr", ""),
        "링크_전년도": "",
        "출처":     "온통청년API",
        "갱신일":   TODAY.strftime("%Y-%m-%d"),
    }

# ── 확인필요 항목 키워드 검색 ────────────────────────────────
def search_active(policy_name):
    keyword = re.sub(r'[^\w]', ' ', policy_name.replace("경기", "").replace("청년", "")).strip()
    if len(keyword) < 2:
        keyword = policy_name
    result = fetch_api_page(keyword=keyword, per_page=10)
    if result is None:
        return None
    for item in result.get("youthPolicyList", []):
        name = item.get("plcyNm", "")
        core = [w for w in keyword.split() if len(w) >= 2]
        if any(w in name for w in core):
            bgng = item.get("bizPrdBgngYmd", "").strip()
            end  = item.get("bizPrdEndYmd", "").strip()
            시기 = f"{_format_date(bgng)} ~ {_format_date(end)}" if bgng and end else ""
            if get_status(시기) == "모집중":
                link = item.get("aplyUrlAddr", "") or item.get("refUrlAddr1", "")
                return {"link": link, "period": 시기}
    return None

# ── 경기도 일자리재단 OpenAPI (JobFndtnSportPolocy) ──────────
DIV_TO_FIELD = {
    "구직활동 지원": "일자리",
    "재직 지원":     "일자리",
    "기업 지원":     "일자리",
    "생활 지원":     "금융·복지·문화",
    "주거 지원":     "주거",
}

_GYEONGGI_GUN = {"가평", "연천", "양평"}
def _with_city_suffix(city):
    return city + ("군" if city in _GYEONGGI_GUN else "시")

# 이 API의 REGION_NM 필드는 거의 항상 "그외 지역"으로 채워져 있어 쓸모가
# 없다. 대신 공고 제목의 "[oo시]" 접두어나 담당기관명("oo시청" 등)에서
# 실제 시군을 뽑아낸다.
def _extract_gg_city(title, inst_nm, region_nm):
    m = re.match(r'\s*\[([^\]]+)\]', title or '')
    if m:
        bracket = m.group(1)
        for city in GYEONGGI_CITIES:
            if city != "경기" and city in bracket:
                return _with_city_suffix(city)
    for city in GYEONGGI_CITIES:
        if city != "경기" and city in (inst_nm or ''):
            return _with_city_suffix(city)
    if region_nm and region_nm not in ("그외 지역", "경기"):
        return region_nm
    return "경기도"

def fetch_jobfndtn_api():
    results = []
    url = "https://openapi.gg.go.kr/JobFndtnSportPolocy"
    page = 1
    total = None
    while True:
        try:
            r = requests.get(url, params={
                "KEY": JOBABA_KEY, "Type": "json",
                "pIndex": page, "pSize": 1000,
            }, timeout=20)
            data = r.json()
            body = data.get("JobFndtnSportPolocy", [{}])
            if len(body) < 2:
                break
            if total is None:
                total = int(body[0].get("list_total_count", 0))
            rows = body[1].get("row", [])
            for row in rows:
                begin = row.get("RECRUT_BEGIN_DE", "")
                end   = row.get("RECRUT_END_DE", "")
                if end:
                    try:
                        end_dt = datetime.strptime(end, "%Y%m%d")
                        status = "마감" if end_dt < TODAY else "모집중"
                    except:
                        status = "확인필요"
                elif begin:
                    status = "모집중"
                else:
                    status = "확인필요"

                시기 = ""
                if begin and end:
                    시기 = f"{begin[:4]}.{begin[4:6]}.{begin[6:]} ~ {end[:4]}.{end[4:6]}.{end[6:]}"
                elif begin:
                    시기 = f"{begin[:4]}.{begin[4:6]}.{begin[6:]} ~"

                div_nm = row.get("DIV_NM") or ""
                분야 = DIV_TO_FIELD.get(div_nm, "일자리")
                title = row.get("PBLANC_TITLE", "")
                region = _extract_gg_city(title, row.get("INST_NM",""), row.get("REGION_NM",""))

                results.append({
                    "시군":     region,
                    "분야":     분야,
                    "사업명":   title,
                    "주요내용": "",
                    "모집시기": 시기,
                    "모집상태": status,
                    "신청방법": "잡아바 온라인 신청",
                    "운영기관": row.get("INST_NM", ""),
                    "문의처":   "",
                    "링크":     row.get("DETAIL_PAGE_URL", ""),
                    "링크_모집":row.get("DETAIL_PAGE_URL", ""),
                    "링크_전년도": "",
                    "출처":     "경기일자리재단API",
                    "갱신일":   TODAY.strftime("%Y-%m-%d"),
                })
            if len(rows) < 1000:
                break
            page += 1
        except Exception as e:
            print(f"  경기일자리재단 API 오류: {e}")
            break
    print(f"  경기일자리재단 API: {len(results)}건 (전체 {total}건)")
    return results

# ── 잡아바 크롤링 ────────────────────────────────────────────
def scrape_jobaba():
    results = []
    FIXED = [
        {"사업명": "경기도 청년기본소득", "분야": "금융ㆍ복지ㆍ문화",
         "url": "https://apply.jobaba.net/special/gibon/main.do",
         "모집시기": "분기별 신청 (1분기:3월, 2분기:6월, 3분기:9월, 4분기:12월)",
         "문의처": "1877-0566"},
    ]
    if not BS4_OK:
        for p in FIXED:
            results.append(_make_gyeonggi_item(p))
        return results

    try:
        r = requests.get("https://apply.jobaba.net/bsns/bsnsListView.do",
                         headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select(".bsns-item, .list-bsns li, .program-item")
        for item in items:
            title_el = item.select_one(".bsns-nm, .tit, h3, strong")
            link_el  = item.select_one("a")
            if not title_el: continue
            name = title_el.get_text(strip=True)
            if not name or "청년" not in name: continue
            link = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://apply.jobaba.net{href}"
            results.append({
                "시군":"경기도","분야":"일자리","사업명":name,
                "주요내용":"","모집시기":"","모집상태":"확인필요",
                "신청방법":"잡아바 온라인 신청","운영기관":"경기도일자리재단",
                "문의처":"","링크":link,"링크_모집":link,"링크_전년도":"",
                "출처":"잡아바","갱신일":TODAY.strftime("%Y-%m-%d"),
            })
    except Exception as e:
        print(f"  잡아바 크롤링 오류: {e}")

    for p in FIXED:
        if not any(r["사업명"] == p["사업명"] for r in results):
            results.append(_make_gyeonggi_item(p))

    return results

def _make_gyeonggi_item(p):
    return {
        "시군":"경기도","분야":p.get("분야","금융ㆍ복지ㆍ문화"),
        "사업명":p["사업명"],"주요내용":"",
        "모집시기":p.get("모집시기",""),"모집상태":"모집중",
        "신청방법":"잡아바 온라인 신청","운영기관":"경기도일자리재단",
        "문의처":p.get("문의처",""),"링크":p["url"],
        "링크_모집":p["url"],"링크_전년도":"",
        "출처":"잡아바","갱신일":TODAY.strftime("%Y-%m-%d"),
    }

# ── 경기복지포털 (경기민원24) ──────────────────────────────────
# gg24.gg.go.kr은 최초 접속 시 "가상 대기실" 안내 페이지를 내려주는데,
# 브라우저에서는 JS가 wcCookie를 세팅해 통과시킨다. 서버는 이 쿠키의
# 값 자체를 검증하지 않고 존재 여부만 확인하므로 임의 값으로 충분하다.
GG24_STATUS_MAP = {"접수중": "모집중", "접수예정": "모집예정", "접수마감": "마감"}

def scrape_gg24():
    results = []
    FIXED = [
        {"사업명":"경기 고립은둔청년 지원사업","분야":"금융ㆍ복지ㆍ문화",
         "url":"https://gg24.gg.go.kr/svcreqst/selectSvcReqst.do?svc_seq=945",
         "모집상태":"모집중","문의처":"031-267-9100"},
    ]
    if not BS4_OK:
        for p in FIXED:
            results.append({**_make_gyeonggi_item(p), "운영기관":"경기복지재단","출처":"경기복지포털"})
        return results

    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.cookies.set("wcCookie", "bypass", domain="gg24.gg.go.kr")
        url = "https://gg24.gg.go.kr/svcreqst/selectPageListSvcReqst.do"
        r = s.post(url, data={"contentLimit": "300", "type": "", "currentPage": "1"}, timeout=20)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")

        seen_links = set()
        for li in soup.select("li.board-boxlist__item"):
            a = li.select_one("a.board-boxlist__link")
            if not a or not a.get("href"): continue
            href = a["href"]
            link = href if href.startswith("http") else f"https://gg24.gg.go.kr{href}"
            if link in seen_links: continue

            title_el  = li.select_one("h4.board-boxlist--bottom__tit")
            badge_el  = li.select_one("div.board-boxlist--top__badge")
            period_el = li.select_one("p.board-boxlist--bottom__sub")
            name = title_el.get_text(strip=True) if title_el else ""
            if not name or "청년" not in name: continue
            seen_links.add(link)

            badge  = badge_el.get_text(strip=True) if badge_el else ""
            period = period_el.get_text(strip=True) if period_el else ""
            status = GG24_STATUS_MAP.get(badge) or get_status(period)

            results.append({
                "시군":"경기도","분야":"금융ㆍ복지ㆍ문화","사업명":name,
                "주요내용":"","모집시기":period,"모집상태":status,
                "신청방법":"경기민원24 온라인 신청","운영기관":"경기도",
                "문의처":"","링크":link,"링크_모집":link,"링크_전년도":"",
                "출처":"경기복지포털","갱신일":TODAY.strftime("%Y-%m-%d"),
            })
    except Exception as e:
        print(f"  경기복지포털 오류: {e}")

    for p in FIXED:
        core = p["사업명"].replace("지원사업","").replace("지원","").strip()
        if not any(core in r["사업명"] for r in results):
            results.append({
                "시군":"경기도","분야":p["분야"],"사업명":p["사업명"],
                "주요내용":"","모집시기":"","모집상태":p["모집상태"],
                "신청방법":"경기민원24 온라인 신청","운영기관":"경기복지재단",
                "문의처":p["문의처"],"링크":p["url"],"링크_모집":p["url"],"링크_전년도":"",
                "출처":"경기복지포털","갱신일":TODAY.strftime("%Y-%m-%d"),
            })
    return results

# ── 경기청년포털 ─────────────────────────────────────────────
# 2025년 개편된 게시판 구조: 5개 분야별 게시판을 offset=10 단위로 페이징.
# 목록에는 제목만 있고, 신청기간/링크/문의처는 상세(mode=view) 페이지에만
# 있어서 신규 글만 골라 상세 페이지를 추가로 요청한다.
GG_YOUTH_BOARDS = [
    ("https://youth.gg.go.kr/gg/intro/youth-policy-job-test.do", "일자리"),
    ("https://youth.gg.go.kr/gg/intro/youth-policy-educational-testing.do", "주거"),
    ("https://youth.gg.go.kr/gg/intro/youth-policy-housing-test.do", "금융·복지·문화"),
    ("https://youth.gg.go.kr/gg/intro/youth-policy-culture-test.do", "교육·직업훈련"),
    ("https://youth.gg.go.kr/gg/intro/youth-policy-law-test.do", "참여·기반"),
]
GG_YOUTH_MAX_OFFSET = 100  # 분야당 최대 10페이지(약 100건)까지만 순회

def _parse_gg_youth_detail(base_url, article_no):
    detail_url = f"{base_url}?mode=view&articleNo={article_no}&article.offset=0&articleLimit=10"
    r = requests.get(detail_url, headers=HEADERS, timeout=15)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.select_one(".fr-view")
    if not body:
        return None

    intro = " ".join(p.get_text(strip=True) for p in body.select(".youth_polish_txt"))

    fields = {}
    for li in body.select(".content_sums li"):
        span = li.select_one("span")
        if not span: continue
        key = span.get_text(strip=True).rstrip(":：").strip()
        val = li.get_text(strip=True)
        val = val[len(span.get_text(strip=True)):].strip()
        fields[key] = val

    시기 = ""
    for k, v in fields.items():
        if "기간" in k or "일정" in k:
            시기 = v
            break

    문의 = ""
    call_el = None
    for h in body.select("h2.youth_polish_contents_call"):
        if "문의" in h.get_text():
            call_el = h.find_next("ul")
            break
    if call_el:
        문의 = call_el.get_text(strip=True).split(":",1)[-1].strip()

    link_el = body.select_one("a.youth_polish_check_btn")
    link = link_el.get("href","") if link_el else ""

    return {"주요내용": intro[:500], "모집시기": 시기, "문의처": 문의, "링크": link}

def scrape_gyeonggi_youth(existing_data):
    results = []
    if not BS4_OK:
        return results

    existing_links = {d.get("링크","") for d in existing_data if d.get("링크")}

    for base_url, 분야 in GG_YOUTH_BOARDS:
        offset = 0
        while offset <= GG_YOUTH_MAX_OFFSET:
            try:
                r = requests.get(base_url, params={
                    "mode": "list", "article.offset": offset, "articleLimit": 10,
                }, headers=HEADERS, timeout=15)
                r.encoding = "utf-8"
                soup = BeautifulSoup(r.text, "html.parser")
                links = soup.select("td.t-tit a[href*='mode=view']")
                if not links:
                    break

                for a in links:
                    name = a.get_text(strip=True)
                    m = re.search(r'articleNo=(\d+)', a.get("href",""))
                    if not name or not m: continue
                    article_no = m.group(1)
                    list_link = f"{base_url}?mode=view&articleNo={article_no}"
                    if list_link in existing_links: continue

                    try:
                        detail = _parse_gg_youth_detail(base_url, article_no)
                    except Exception as e:
                        print(f"  경기청년포털 상세 오류({article_no}): {e}")
                        detail = None
                    if not detail: continue

                    link = detail["링크"] or list_link
                    results.append({
                        "시군":"경기도","분야":분야,"사업명":name,
                        "주요내용":detail["주요내용"],"모집시기":detail["모집시기"],
                        "모집상태":get_status(detail["모집시기"]),
                        "신청방법":"","운영기관":"경기도",
                        "문의처":detail["문의처"],"링크":link,"링크_모집":link,"링크_전년도":"",
                        "출처":"경기청년포털","갱신일":TODAY.strftime("%Y-%m-%d"),
                    })
                    existing_links.add(list_link)
                    time.sleep(0.2)

                offset += 10
            except Exception as e:
                print(f"  경기청년포털 오류({분야}): {e}")
                break

    return results

# ── 31개 시군 공고게시판 ──────────────────────────────────────
def scrape_sigungu(existing_data):
    if not BS4_OK:
        print("  beautifulsoup4 없어 시군 크롤링 스킵")
        return []

    try:
        with open("sites.json", "r", encoding="utf-8-sig") as f:
            sites = json.load(f)
    except Exception as e:
        print(f"  sites.json 읽기 오류: {e}")
        return []

    existing_links = {d.get("링크","") for d in existing_data if d.get("링크")}
    existing_names = {d.get("사업명","") for d in existing_data}
    YOUTH_KW = ["청년","청소년지원","청년지원","청년정책","청년취업","청년주거","청년창업"]
    results = []

    for site in sites:
        시군 = site.get("city") or site.get("시군", "")
        url  = site["url"]
        try:
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
            except requests.exceptions.SSLError:
                # 일부 시군 사이트가 중간 인증서 체인을 누락해 SSL 검증이 실패함
                r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
            r.encoding = r.apparent_encoding or "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            rows = []
            for sel in [".bdList li","table tr",".board-list tr",".list-item","li"]:
                rows = soup.select(sel)
                if len(rows) > 2: break

            for row in rows:
                link_el = row.select_one("a")
                if not link_el: continue
                title = link_el.get_text(strip=True)
                if not title or len(title) < 4: continue
                if not any(kw in title for kw in YOUTH_KW): continue
                href = link_el.get("href","")
                full_link = (href if href.startswith("http")
                             else f"https://{url.split('/')[2]}{href}" if href.startswith("/")
                             else url)
                if full_link in existing_links: continue
                # 제목이 기존 항목과 겹치면 중복 추가만 막는다. 게시판 제목이
                # 우연히 겹친다고 모집상태를 함부로 "모집중"으로 덮어쓰지 않는다
                # (실제 모집시기/본문과 무관하게 상태가 틀어지는 오류가 반복 발생함).
                if any(title[:8] in name for name in existing_names if len(name) > 4):
                    continue
                results.append({
                    "시군":시군,"분야":"기타","사업명":title,
                    "주요내용":"","모집시기":"","모집상태":"모집중",
                    "신청방법":"","운영기관":"","문의처":"",
                    "링크":full_link,"링크_모집":full_link,"링크_전년도":"",
                    "출처":f"{시군}공고게시판","갱신일":TODAY.strftime("%Y-%m-%d"),
                })
                existing_links.add(full_link)
                print(f"  🆕 [{시군}] {title[:30]}")
        except Exception as e:
            print(f"  ⚠️ [{시군}] {type(e).__name__}")
        time.sleep(0.3)

    return results

# ── 마이홈 (LH 공공주택 청약/모집공고) ──────────────────────
# 검색 페이지 자체는 JS로 렌더링되지만, 실제 목록은 JSON API
# (selectRsdtRcritNtcList.do)를 POST로 호출해서 받아온다.
# srchbrtcCode=41 이 경기도. resultCnt는 필터와 무관하게 항상 큰 값이
# 찍히는 버그가 있어 신뢰하지 않고, 실제 resultList 길이만 사용한다.
MYHOME_STATUS_MAP = {"모집중": "모집중", "모집예정": "모집예정", "마감": "마감", "접수중": "모집중"}

def scrape_myhome():
    results = []
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        view_url = "https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do"
        list_url = "https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcList.do"
        s.get(view_url, timeout=15)
        payload = {
            "pageIndex": 1, "pageUnit": 200, "srchbrtcCode": "41", "srchsignguCode": "",
            "searchTyId": "", "srchSuplyTy": "", "srchHouseTy": "",
            "srchSuplyPrvuseAr": "", "srchBassMtRntchrg": "", "srchPrgrStts": "",
            "srchPblancNm": "", "srchRcritPblancDeYearMtBegin": "", "srchRcritPblancDeYearMtEnd": "",
        }
        r = s.post(list_url, data=payload, timeout=20, headers={
            "X-Requested-With": "XMLHttpRequest", "Referer": view_url,
        })
        items = r.json().get("resultList", [])
        for it in items:
            title = it.get("pblancNm", "")
            if not title:
                continue
            city = _extract_gg_city(title, it.get("suplyInsttNm", ""), "")
            공고일 = (it.get("rcritPblancDe") or "").strip()
            발표일 = (it.get("przwnerPresnatnDe") or "").strip()
            시기 = ""
            if 공고일:
                시기 = f"{공고일[:4]}.{공고일[4:6]}.{공고일[6:]} 공고"
                if 발표일:
                    시기 += f" (예비입주자 발표 {발표일[:4]}.{발표일[4:6]}.{발표일[6:]})"
            상태 = MYHOME_STATUS_MAP.get(it.get("prgrStts", "")) or get_status(시기)
            주요내용 = " ".join(x for x in [it.get("guidanceCn"), it.get("etcCn")] if x)[:500]
            link = it.get("url", "")

            results.append({
                "시군": city, "분야": "주거",
                "사업명": title, "주요내용": 주요내용,
                "모집시기": 시기, "모집상태": 상태,
                "신청방법": "", "운영기관": it.get("suplyInsttNm", "") or "마이홈",
                "문의처": it.get("refrnc", ""),
                "링크": link, "링크_모집": link, "링크_전년도": "",
                "출처": "마이홈", "갱신일": TODAY.strftime("%Y-%m-%d"),
            })
    except Exception as e:
        print(f"  마이홈 오류: {e}")
    print(f"  마이홈: {len(results)}건")
    return results

# ── 유관기관 홈페이지 게시판 (partner_sites.json) ────────────
# 유관기관 탭에 있는 130개 기관 중, 홈페이지가 실제 "공고 목록" 구조라
# 범용 크롤링이 통하는 곳만 골라 partner_sites.json에 정리해뒀다.
# (온통청년/경기청년포털/경기도일자리재단처럼 이미 전용 경로로 수집 중인
# 곳은 중복이라 제외)
def _fetch_org_titles(url):
    """기관 게시판 URL을 열어 (제목, 링크) 목록을 반환. 실패 시 빈 리스트."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
    except requests.exceptions.SSLError:
        r = requests.get(url, headers=HEADERS, timeout=10, verify=False)
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for sel in [".bdList li","table tr",".board-list tr",".list-item","li"]:
        rows = soup.select(sel)
        if len(rows) > 2: break

    titles = []
    for row in rows:
        link_el = row.select_one("a")
        if not link_el: continue
        title = link_el.get_text(strip=True)
        if not title or len(title) < 6: continue
        href = link_el.get("href","")
        full_link = (href if href.startswith("http")
                     else f"https://{url.split('/')[2]}{href}" if href.startswith("/")
                     else url)
        titles.append((title, full_link))
    return titles

def scrape_partner_orgs(existing_data):
    if not BS4_OK:
        print("  beautifulsoup4 없어 유관기관 크롤링 스킵")
        return []

    try:
        with open("partner_sites.json", "r", encoding="utf-8-sig") as f:
            sites = json.load(f)
    except Exception as e:
        print(f"  partner_sites.json 읽기 오류: {e}")
        return []

    existing_links = {d.get("링크","") for d in existing_data if d.get("링크")}
    existing_names = {d.get("사업명","") for d in existing_data}
    YOUTH_KW = ["청년","청소년지원","청년지원","청년정책","청년취업","청년주거","청년창업"]
    # 게시판이 아니라 메뉴/소개 링크인데 우연히 잡히는 것들 (실제 공고가 아님)
    NAV_SUFFIX = ["소개","안내서","안내","연계망","바로가기","페이지","주요 사이트","알림","사이트","(새 창)","새 창)"]
    candidates = []
    seen_org_titles = set()

    for site in sites:
        if site.get("source") == "지역시행계획":
            continue  # 경기 외 16개 시도는 대시보드가 표시할 수단이 없어 크롤링 제외
        시군_raw = site.get("시군", "")
        시군 = "경기도" if 시군_raw == "중앙/경기도" else 시군_raw
        기관명 = site.get("기관명", "")
        url = site["url"]
        try:
            for title, full_link in _fetch_org_titles(url):
                if not any(kw in title for kw in YOUTH_KW): continue
                if any(sfx in title for sfx in NAV_SUFFIX): continue
                if full_link in existing_links: continue
                if any(title[:8] in name for name in existing_names if len(name) > 4):
                    continue
                if (기관명, title) in seen_org_titles: continue
                seen_org_titles.add((기관명, title))
                candidates.append({
                    "시군":시군,"분야":"기타","사업명":title,
                    "주요내용":"","모집시기":"","모집상태":"모집중",
                    "신청방법":"","운영기관":기관명,"문의처":"",
                    "링크":full_link,"링크_모집":full_link,"링크_전년도":"",
                    "출처":"유관기관크롤링","갱신일":TODAY.strftime("%Y-%m-%d"),
                })
                existing_links.add(full_link)
        except Exception as e:
            print(f"  ⚠️ [{기관명}] {type(e).__name__}")
        time.sleep(0.3)

    # 서로 다른 기관에서 완전히 같은 제목이 나오면 전국 공용 위젯/배너일 가능성이
    # 높아 걸러낸다 (기관별 자체 공고가 아님).
    title_orgs = {}
    for c in candidates:
        title_orgs.setdefault(c["사업명"], set()).add(c["운영기관"])
    results = [c for c in candidates if len(title_orgs[c["사업명"]]) == 1]
    for c in results:
        print(f"  🆕 [{c['운영기관']}] {c['사업명'][:30]}")

    return results

# ── 중앙정부/경기 시행계획 출처 재검증 (partner_sites.json URL 매칭) ─
# "2026중앙정부시행계획"/"2026경기시행계획" 레코드는 엑셀에서 그대로 반영된
# 정적 데이터라 모집시기 텍스트만으로는 상태가 절대 바뀌지 않는다. 매핑된
# 기관 URL을 열어 사업명과 비슷한 제목의 게시글이 실제로 있는지 확인해서만
# "모집중"으로 갱신한다 — 못 찾았다고 "마감"으로 단정하면 게시판 구조가
# 안 맞아 오탐(정책 누락)이 날 수 있으므로 그 경우는 기존 상태를 유지한다.
def verify_plan_orgs(existing):
    if not BS4_OK:
        print("  beautifulsoup4 없어 시행계획 재검증 스킵")
        return existing

    try:
        with open("partner_sites.json", "r", encoding="utf-8-sig") as f:
            sites = json.load(f)
    except Exception as e:
        print(f"  partner_sites.json 읽기 오류: {e}")
        return existing

    PLAN_SOURCES = {"중앙정부시행계획", "경기시행계획"}
    org_url = {}
    for site in sites:
        src_set = {s.strip() for s in site.get("source","").split(",")}
        if src_set & PLAN_SOURCES:
            기관명 = site.get("기관명","")
            if 기관명:
                org_url[기관명] = site["url"]

    targets = [d for d in existing
               if d.get("출처") in ("2026중앙정부시행계획", "2026경기시행계획")
               and d.get("운영기관") in org_url]
    print(f"  재검증 대상: {len(targets)}건 ({len({d['운영기관'] for d in targets})}개 기관)")

    titles_cache = {}
    confirmed = 0
    for d in targets:
        기관명 = d.get("운영기관")
        if 기관명 not in titles_cache:
            try:
                titles_cache[기관명] = _fetch_org_titles(org_url[기관명])
            except Exception as e:
                print(f"  ⚠️ [{기관명}] {type(e).__name__}")
                titles_cache[기관명] = []
            time.sleep(0.3)

        사업명 = d.get("사업명","")
        match = next((t for t, link in titles_cache[기관명]
                      if 사업명[:8] in t or t[:8] in 사업명), None)
        if match:
            d["모집상태"] = "모집중"
            confirmed += 1
            print(f"  ✅ [{기관명}] {사업명[:20]} → 모집중 확인")

    print(f"  시행계획 재검증: {confirmed}/{len(targets)}건 모집중 확인")
    return existing

# ── 네이버 뉴스 RSS ──────────────────────────────────────────
def search_naver_news(existing_data):
    existing_links = {d.get("링크","") for d in existing_data if d.get("링크")}
    existing_names = {d.get("사업명","") for d in existing_data}
    QUERIES = [
        "경기도 청년 모집 공고", "경기청년 지원사업 신청",
        "경기 청년정책 신규", "수원시 청년 모집",
        "성남시 청년 지원", "용인시 청년 모집",
    ]
    YOUTH_KW   = ["청년","청년정책","청년지원","청년모집"]
    EXCLUDE_KW = ["부동산","주식","투자","광고","대출금리","분양"]
    results = []
    seen   = set()

    for query in QUERIES:
        try:
            encoded = urllib.parse.quote(query)
            url = (f"https://s.search.naver.com/p/newssearch/search.naver"
                   f"?query={encoded}&where=news&pd=4&sort=1&field=0&start=1&display=10&format=rss")
            r = requests.get(url, headers={**HEADERS,"Referer":"https://search.naver.com"}, timeout=10)
            root = ET.fromstring(r.content)

            for item in root.findall(".//item"):
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description")
                if title_el is None: continue
                title = re.sub(r'<[^>]+>','', title_el.text or "").strip()
                link  = link_el.text.strip() if link_el is not None and link_el.text else ""
                desc  = re.sub(r'<[^>]+>','', desc_el.text or "").strip() if desc_el is not None else ""

                if not any(kw in title for kw in YOUTH_KW): continue
                if any(ex in title for ex in EXCLUDE_KW): continue
                if not any(city in title+desc for city in GYEONGGI_CITIES): continue
                if link in seen or link in existing_links: continue
                if any(title[:8] in name for name in existing_names if len(name) > 4): continue

                seen.add(link)
                시군 = "경기도"
                for city in GYEONGGI_CITIES:
                    if city in title and city != "경기":
                        시군 = city + ("" if city.endswith(("시","군")) else "시")
                        break

                results.append({
                    "시군":시군,"분야":"기타","사업명":title[:60],
                    "주요내용":desc[:200],"모집시기":"","모집상태":"확인필요",
                    "신청방법":"","운영기관":"","문의처":"",
                    "링크":link,"링크_모집":link,"링크_전년도":"",
                    "출처":"네이버뉴스RSS","갱신일":TODAY.strftime("%Y-%m-%d"),
                    "메모":f"뉴스 자동감지 - 담당자 확인 필요",
                })
                print(f"  📰 [{시군}] {title[:35]}")
        except Exception as e:
            print(f"  뉴스RSS 오류: {type(e).__name__}")
        time.sleep(0.5)

    return results

# ── 메인 ────────────────────────────────────────────────────
def main():
    print(f"[{TODAY.strftime('%Y-%m-%d')}] 청년정책 데이터 갱신 시작")

    # 기존 데이터 로드
    try:
        with open("data.json","r",encoding="utf-8") as f:
            existing = json.load(f)
        print(f"기존: {len(existing)}개")
    except:
        existing = []
        print("기존 data.json 없음")

    updated = []
    check_list = []

    # 상태 재계산
    for d in existing:
        if d.get("링크_모집") and d.get("모집상태") == "모집중" and d.get("출처") == "수동추가":
            updated.append(d); continue
        new_status = get_status(d.get("모집시기",""))
        d["모집상태"] = new_status
        d.setdefault("링크_모집","")
        d.setdefault("링크_전년도","")
        if new_status == "확인필요":
            check_list.append(d)
        updated.append(d)

    # 중앙정부/경기 시행계획 → partner_sites.json URL로 재검증
    print("\n시행계획 재검증 (partner_sites.json)...")
    updated = verify_plan_orgs(updated)

    # 확인필요 → API 검색
    print(f"\n확인필요 {len(check_list)}개 API 검색...")
    confirmed = 0
    for d in check_list:
        result = search_active(d.get("사업명",""))
        if result:
            d["모집상태"] = "모집중"
            d["링크_모집"] = result["link"]
            d["모집시기"]  = result["period"]
            confirmed += 1
        else:
            d["모집상태"] = "모집예정"
    print(f"확인 완료: {confirmed}개 모집중")

    existing_names = {d.get("사업명","") for d in updated}

    # 같은 정책이 수동 입력본과 자동수집본에 공백 차이 등으로 이름이 살짝
    # 다르게 들어오는 경우가 많다. "청년정책위원회 운영"처럼 여러 시군이
    # 각자 운영하는 동명의 사업도 있으므로, 이름 정규화만으로는 다른 시군의
    # 별개 사업을 하나로 합쳐버릴 수 있다 → (정규화한 이름, 시군)이 모두
    # 같을 때만 같은 정책으로 보고, 새로 추가하지 않고 기존 항목을 최신
    # 정보로 갱신한다.
    _norm = lambda s: re.sub(r'\s+', '', s or '')
    existing_by_key = {}
    for d in updated:
        existing_by_key.setdefault((_norm(d.get("사업명","")), d.get("시군","")), d)

    def add_new(items, label):
        added = 0
        merged = 0
        for item in items:
            name = item.get("사업명","")
            if not name or len(name) <= 2:
                continue
            if name in existing_names:
                continue
            key = (_norm(name), item.get("시군",""))
            match = existing_by_key.get(key)
            if match is not None:
                for f in ["모집시기","모집상태","링크","링크_모집","신청방법","문의처","운영기관"]:
                    if item.get(f):
                        match[f] = item[f]
                match["모집상태"] = get_status(match.get("모집시기","")) or match.get("모집상태","미정")
                merged += 1
                continue
            item["모집상태"] = get_status(item.get("모집시기","")) or item.get("모집상태","미정")
            updated.append(item)
            existing_names.add(name)
            existing_by_key.setdefault(key, item)
            added += 1
        suffix = f" (+{merged}개 기존 항목 최신화)" if merged else ""
        print(f"{label}: {added}개 추가{suffix}")

    # 각 소스 수집
    print("\n온통청년 API...")
    first = fetch_api_page(1, per_page=100)
    if first is not None:
        total = int(first.get("pagging", {}).get("totCount", 0))
        api_items = [parse_api_item(i) for i in first.get("youthPolicyList", [])]
        for page in range(2, math.ceil(total/100)+1):
            result = fetch_api_page(page, per_page=100)
            if result:
                api_items.extend([parse_api_item(i) for i in result.get("youthPolicyList", [])])
        api_items = [i for i in api_items if i is not None]
        gg_cnt = sum(1 for i in api_items if i["시군"]=="경기도")
        print(f"  온통청년 API: 전체 {total}건 중 경기도 {gg_cnt}건 (경기 외 지역 한정 정책 제외)")
        add_new(api_items, "온통청년 API")

    print("\n경기일자리재단 API...")
    add_new(fetch_jobfndtn_api(), "경기일자리재단API")

    print("\n잡아바...")
    add_new(scrape_jobaba(), "잡아바")

    print("\n경기복지포털...")
    add_new(scrape_gg24(), "경기복지포털")

    print("\n마이홈...")
    add_new(scrape_myhome(), "마이홈")

    print("\n경기청년포털...")
    add_new(scrape_gyeonggi_youth(updated), "경기청년포털")

    print("\n31개 시군 게시판...")
    sigungu_new = scrape_sigungu(updated)
    add_new(sigungu_new, "시군게시판")

    print("\n유관기관 홈페이지...")
    add_new(scrape_partner_orgs(updated), "유관기관크롤링")

    print("\n네이버 뉴스 RSS...")
    add_new(search_naver_news(updated), "네이버뉴스")

    # 안전 장치: 기존 데이터보다 현저히 적으면 저장 중단
    MIN_ITEMS = 10
    if len(existing) >= MIN_ITEMS and len(updated) < len(existing) * 0.5:
        print(f"\n⚠️ 안전 중단: 기존 {len(existing)}개 → 수집 {len(updated)}개 (50% 미만)")
        print("  data.json을 덮어쓰지 않습니다. API/스크래핑 오류를 확인하세요.")
        return

    # 저장
    with open("data.json","w",encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)

    status_count = Counter(d.get("모집상태","미정") for d in updated)
    print(f"\n✅ 완료: 총 {len(updated)}개")
    for k,v in sorted(status_count.items()):
        print(f"  {k}: {v}개")

if __name__ == "__main__":
    main()
