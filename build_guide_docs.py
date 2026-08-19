"""
'기본안내' 탭 데이터 수집 — 구글 문서(분야별 주요 정책 정리)에서
basic_guide.json / policy_cards.json / region_resources.json 생성.

문서가 바뀌면 이 스크립트를 다시 돌리면 됨. 문서 링크(공유 설정이
"링크가 있는 모든 사용자"여야 함):
https://docs.google.com/document/d/17tcTlogpaVrT_17fD9XX8Lpk1vOOblYvx_yVtLZb3aU
"""
import requests, json, re
from bs4 import BeautifulSoup

DOC_ID = '17tcTlogpaVrT_17fD9XX8Lpk1vOOblYvx_yVtLZb3aU'
CATEGORY_MAP = {0: '일자리', 1: '주거', 2: '교육·직업훈련', 3: '금융·복지·문화', 4: '참여·기반'}
LABELS = {'대표정책·서비스', '확인 질문'}


def fetch_html():
    url = f'https://docs.google.com/document/d/{DOC_ID}/export?format=html'
    r = requests.get(url, timeout=30)
    r.encoding = 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')


def li_texts(tag):
    return [li.get_text(strip=True) for li in tag.find_all('li') if li.get_text(strip=True)]


def parse_org(name_org):
    m = re.match(r'^(.*?)\s*\[([^\]]+)\]\s*$', name_org.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (name_org.strip(), '')


def build_basic_guide(soup):
    body = soup.find('body')
    children = [c for c in body.children if getattr(c, 'name', None)]

    guides = []
    cur_category = None
    cur = None

    for c in children:
        if c.name == 'table':
            break
        txt = c.get_text(strip=True)

        if c.name == 'h1':
            m = re.match(r'^([ⅠⅡⅢⅣⅤ])\.\s*(.+)$', txt)
            if m: cur_category = m.group(2).strip()
            continue

        if c.name == 'h2':
            m = re.match(r'^\d+\.\s*(.+)$', txt)
            if m and cur_category:
                cur = {'분야': cur_category, '상황': m.group(1).strip(),
                       '대표정책': [], '확인질문': [], '분기안내': '', '주의사항': ''}
                guides.append(cur)
            continue

        if cur is None:
            continue

        if c.name == 'ol':
            items = li_texts(c)
            if items and not cur['대표정책']:
                cur['대표정책'] = items
            continue

        if c.name == 'ul':
            items = li_texts(c)
            if set(items) & LABELS:
                continue  # "대표정책·서비스"/"확인 질문" 라벨 자체도 목록으로 렌더되어 있어 건너뜀
            if items and not cur['확인질문']:
                cur['확인질문'] = items
            continue

        if c.name == 'p':
            if txt.startswith('→'):
                cur['분기안내'] = txt.lstrip('→').strip()
            elif txt.startswith('확인'):
                rest = re.sub(r'^확인\s*(사항|질문)?', '', txt).strip()
                if rest and not cur['확인질문']:
                    cur['확인질문'] = [q.strip() for q in re.split(r'\s*/\s*', rest) if q.strip()]
            elif txt.startswith('주의'):
                cur['주의사항'] = re.sub(r'^주의\s*', '', txt).strip()

    return guides


def build_policy_cards(soup):
    tables = soup.find_all('table')
    cards = []
    for idx in range(5):
        t = tables[idx]
        rows = t.find_all('tr')
        header = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
        for r in rows[1:]:
            cells = [c.get_text(strip=True) for c in r.find_all(['td', 'th'])]
            if len(cells) < 3:
                continue
            row = dict(zip(header, cells))
            name_raw = row.get('정책명[주관]', '')
            if not name_raw:
                continue
            정책명, 주관 = parse_org(name_raw)
            cards.append({
                '분야': CATEGORY_MAP[idx],
                '연번': row.get('연번', ''),
                '정책명': 정책명,
                '주관': 주관,
                '대상': row.get('대상', ''),
                '요건': row.get('대상·핵심요건', '') or row.get('핵심내용', ''),
                '지원내용': row.get('주요 지원', ''),
                '확인처': row.get('확인처', ''),
                '기타': row.get('기타', ''),
            })
    return cards


REGION_FIELD_MAP = {
    '지역': '시군',
    '청년센터·연락처': '청년센터_연락처',
    '일자리·취업': '일자리_취업',
    '일자리·창업': '일자리_창업',
    '주거': '주거',
    '교육·복지·문화': '교육_복지_문화',
    '참여': '참여',
    '전문기관 연결': '전문기관_연결',
    '공식 확인경로': '공식_확인경로',
}


def build_region_resources(soup):
    tables = soup.find_all('table')
    t = tables[5]
    rows = t.find_all('tr')
    header = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
    result = []
    for r in rows[1:]:
        cells = [c.get_text(strip=True) for c in r.find_all(['td', 'th'])]
        if len(cells) < 3:
            continue
        row = dict(zip(header, cells))
        result.append({REGION_FIELD_MAP.get(k, k): v for k, v in row.items()})

    # "지역별 특성" 서술형 설명을 시군명으로 매칭해 붙임
    blocks = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4'])
    texts = [b.get_text(strip=True) for b in blocks if b.get_text(strip=True)]
    if '지역별 특성' in texts:
        idx = texts.index('지역별 특성')
        tail = texts[idx + 1:]
        narratives = {tail[i]: tail[i + 1] for i in range(0, len(tail) - 1, 2)}
        for x in result:
            x['특성설명'] = narratives.get(x['시군'], '')

    return result


if __name__ == '__main__':
    print('구글 문서 수집...')
    soup = fetch_html()

    guides = build_basic_guide(soup)
    with open('basic_guide.json', 'w', encoding='utf-8') as f:
        json.dump(guides, f, ensure_ascii=False, indent=2)
    print(f'basic_guide.json: {len(guides)}개 상황')

    cards = build_policy_cards(soup)
    with open('policy_cards.json', 'w', encoding='utf-8') as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
    print(f'policy_cards.json: {len(cards)}개 정책')

    regions = build_region_resources(soup)
    with open('region_resources.json', 'w', encoding='utf-8') as f:
        json.dump(regions, f, ensure_ascii=False, indent=2)
    print(f'region_resources.json: {len(regions)}개 시군')
