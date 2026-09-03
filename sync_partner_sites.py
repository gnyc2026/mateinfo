"""
partners.json(유관기관 탭)에는 있지만 partner_sites.json(청년정책 탭 크롤링 대상)에는
아직 없는 기관을 찾아, 실제로 청년 관련 게시글을 뽑아낼 수 있는 곳만 자동 등록한다.

과거엔 사람이 URL을 하나씩 열어보고 크롤러가 실제로 동작하는지 확인해서 골라 넣었다
(K-startup·알리오 등은 게시판처럼 보여도 검증 결과 실패해 제외됐던 이력 있음). 단순히
"글 목록이 뽑히는지"만 보면 개인정보처리방침/로그인 같은 메뉴 링크만 있는 사이트도
통과해버려서(실제로 그런 사례 확인함), scrape_partner_orgs()가 매일 실제로 쓰는 것과
똑같은 필터(PARTNER_YOUTH_KW 포함 + PARTNER_NAV_SUFFIX 제외)를 통과하는 제목이
하나라도 있어야 등록한다 — 즉 "오늘 당장 크롤링해도 뭔가 나오는 사이트"만 통과.
오늘 통과 못 해도 다음날 다시 후보로 잡히니 별도의 영구 제외 목록은 안 둔다.
"""
import json
from fetch_policies import _fetch_org_titles, PARTNER_YOUTH_KW, PARTNER_NAV_SUFFIX

PARTNERS_PATH = "partners.json"
SITES_PATH = "partner_sites.json"

# 온통청년/경기청년포털/경기도일자리재단/잡아바/경기민원24/마이홈은 이미 전용
# API·크롤러 경로로 수집 중이라(fetch_policies.py 상단 주석 참고), 여기서 generic
# 크롤러로 또 등록하면 같은 공고가 두 경로로 중복 수집될 위험이 있어 제외한다.
EXCLUDE_URL_SUBSTRINGS = [
    "youthcenter.go.kr",
    "youth.gg.go.kr",
    "gjf.or.kr/main/main.do",
    "apply.jobaba.net/bsns/bsnsListView.do",
    "gg24.gg.go.kr",
    "myhome.go.kr",
]


def load(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def has_real_youth_post(titles):
    for title, _link in titles:
        if len(title) < 6:
            continue
        if any(sfx in title for sfx in PARTNER_NAV_SUFFIX):
            continue
        if any(kw in title for kw in PARTNER_YOUTH_KW):
            return True
    return False


def main():
    partners = load(PARTNERS_PATH)
    sites = load(SITES_PATH)

    existing_urls = {s["url"] for s in sites}
    existing_names = {s.get("기관명") for s in sites}

    candidates = [
        p for p in partners
        if p.get("웹주소", "").startswith("http")
        and p["웹주소"] not in existing_urls
        and p["기관명"] not in existing_names
        and not any(sub in p["웹주소"] for sub in EXCLUDE_URL_SUBSTRINGS)
    ]
    print(f"검증 대상 후보 {len(candidates)}곳")

    added = 0
    for p in candidates:
        기관명, 시군, url = p["기관명"], p["시군"], p["웹주소"]
        try:
            titles = _fetch_org_titles(url)
        except Exception as e:
            print(f"  ⚠️ [{기관명}] {type(e).__name__}")
            continue

        if has_real_youth_post(titles):
            sites.append({"기관명": 기관명, "시군": 시군, "url": url})
            existing_urls.add(url)
            existing_names.add(기관명)
            added += 1
            print(f"  ✅ [{기관명}] 등록")
        else:
            print(f"  ⏭️  [{기관명}] 청년 관련 게시글 없음, 보류")

    if added:
        with open(SITES_PATH, "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=2)
        print(f"\n총 {added}곳 신규 등록 (partner_sites.json {len(sites)-added} → {len(sites)})")
    else:
        print("\n신규 등록 없음")


if __name__ == "__main__":
    main()
