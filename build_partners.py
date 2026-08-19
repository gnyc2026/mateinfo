import urllib.parse, requests, csv, io, json, re

SHEET_ID = '1ZjmkXyo0Sk5LOtGDPrE4ZA4uVkDveW8Ihir-xDr7AdY'
PARTNER_TABS = ['중앙/경기도','고양시','파주시','동두천시','양주시','연천군','의정부시','포천시','가평군','구리시','남양주시']
CENTER_GID = '192136370'

def fetch_sheet_csv(name):
    enc = urllib.parse.quote(name)
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={enc}'
    r = requests.get(url, timeout=20)
    r.encoding = 'utf-8'
    return list(csv.reader(io.StringIO(r.text)))

def fetch_gid_csv(gid):
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}'
    r = requests.get(url, timeout=20)
    r.encoding = 'utf-8'
    return list(csv.reader(io.StringIO(r.text)))

def find_header_row(rows):
    for i, row in enumerate(rows):
        if '기관명' in row:
            return i, row
    return None, None

def clean(v):
    return (v or '').strip()

def build_partners():
    partners = []
    for tab in PARTNER_TABS:
        rows = fetch_sheet_csv(tab)
        hdr_idx, header = find_header_row(rows)
        if header is None:
            print(f'  ⚠️ {tab}: 헤더를 찾지 못함')
            continue
        col = {name: i for i, name in enumerate(header)}
        시군 = '중앙/경기도' if tab == '중앙/경기도' else tab

        last = None
        for row in rows[hdr_idx+1:]:
            def get(key):
                i = col.get(key)
                return clean(row[i]) if i is not None and i < len(row) else ''

            name = get('기관명')
            url = get('웹 주소')
            구분 = get('구분')
            카테고리 = get('카테고리')
            담당자 = get('담당자')
            메일 = get('메일주소')
            대표전화 = get('대표번호')
            내선 = get('내선번호')
            휴대폰 = get('휴대전화번호')
            비고 = get('비고')

            if not name:
                # 기관명 없는 행은 이전 항목에 대한 보충 정보(추가 링크 등)로 간주
                extra = ' '.join(x for x in [url, 비고] if x)
                if extra and last is not None:
                    last['비고'] = (last['비고'] + ' / ' + extra).strip(' /') if last['비고'] else extra
                continue

            전화 = ' / '.join(x for x in [대표전화, 내선, 휴대폰] if x)
            item = {
                '시군': 시군,
                '구분': 구분,
                '분류': 카테고리,
                '기관명': name,
                '웹주소': url,
                '담당자': 담당자,
                '메일주소': 메일,
                '전화번호': 전화,
                '비고': 비고,
            }
            partners.append(item)
            last = item
        print(f'  {tab}: {sum(1 for p in partners if p["시군"]==시군)}건')
    partners.extend(MANUAL_ADDITIONS)

    seen = set()
    deduped = []
    for p in partners:
        key = (p['기관명'], p['웹주소'], p['시군'])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped

# 원본 시트에는 없지만 직접 추가 요청받은 기관들.
# (build_partners.py를 다시 돌려도 유지되도록 시트 데이터가 아니라 코드에 둠)
MANUAL_ADDITIONS = [
    {
        '시군': '중앙/경기도', '구분': '주거', '분류': '',
        '기관명': '마이홈',
        '웹주소': 'https://www.myhome.go.kr/hws/portal/sch/selectRsdtRcritNtcView.do',
        '담당자': '', '메일주소': '', '전화번호': '', '비고': '',
    },
]

# 청년센터현황 시트에는 링크 컬럼이 없어서 직접 조사한 값을 수동으로 관리.
# (build_partners.py를 다시 돌려도 유지되도록 시트 데이터가 아니라 코드에 둠)
CENTER_LINKS = {
    '고양시':   'https://goyangjobcafe.kr/',
    '파주시':   'https://www.paju.go.kr/youth/index.do',
    '동두천시': 'https://www.ddcstartup.co.kr/',
    '양주시':   'https://www.yangju.go.kr/youth/index.do',
    '연천군':   'https://www.yeoncheon.go.kr/',
    '의정부시': 'https://www.uiyouth.or.kr/',
    '포천시':   'https://www.pocheon.go.kr/youth/index.do',
    '가평군':   'https://www.gp.go.kr/',
    '구리시':   'https://guristartup.or.kr/',
    '남양주시': 'https://www.nyj.go.kr/',
}

def build_centers():
    rows = fetch_gid_csv(CENTER_GID)
    hdr_idx = None
    for i, row in enumerate(rows):
        if '시군목록' in row:
            hdr_idx = i
            break
    centers = []
    if hdr_idx is None:
        print('  ⚠️ 청년센터현황 헤더를 찾지 못함')
        return centers
    for row in rows[hdr_idx+1:]:
        row = [clean(c) for c in row]
        if not row or not row[0] or row[0] in ('8시2군',):
            continue
        시군 = row[0]
        get = lambda i: row[i] if i < len(row) else ''
        centers.append({
            '시군': 시군,
            '권역': get(1),
            '청년센터': get(2),
            '운영방식': get(3),
            '전체인구수': get(4),
            '청년인구수': get(5),
            '청년인구비율': get(6),
            '청년연령기준': get(7),
            '청년공간': get(8),
            '링크': CENTER_LINKS.get(시군, ''),
        })
    return centers

if __name__ == '__main__':
    print('유관기관 데이터 수집...')
    partners = build_partners()
    print(f'총 {len(partners)}건')
    with open('partners.json', 'w', encoding='utf-8') as f:
        json.dump(partners, f, ensure_ascii=False, indent=2)

    print('\n청년센터현황 데이터 수집...')
    centers = build_centers()
    print(f'총 {len(centers)}건')
    with open('centers.json', 'w', encoding='utf-8') as f:
        json.dump(centers, f, ensure_ascii=False, indent=2)
