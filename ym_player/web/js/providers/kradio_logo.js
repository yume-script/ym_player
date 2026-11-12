// 공중파 라디오 로고: 관리자가 정한 그림이 없으면 방송사 글자 로고를 그린다 (어두운 둥근 판 + 굵은 방송사 이름 + 채널 + 주파수).
// 예) MBC FM4U 91.9 / KBS 1FM 93.1 / 부산 MBC 표준FM
// 주파수는 수도권 대표 채널만 적어 두었다 (지역마다 달라서) - 모르면 빼고 그린다.

// 방송사 영문 이름 (이름 안에서 처음 나오는 것을 크게)
const BRANDS = ['FEBC', 'CPBC', 'JIBS', 'KBS', 'MBC', 'SBS', 'EBS', 'CBS', 'TBN', 'TBS', 'YTN', 'OBS', 'BBS', 'WBS', 'AFN', 'KNN', 'UBC', 'TBC',
    'KBC', 'JTV', 'TJB', 'CJB', 'G1', 'iFM', 'Arirang'];

// 수도권 주파수 (MHz) - 채널 키(방송사.채널) 기준
const FREQ_SEOUL = {
    'kbs.1radio': '97.3', 'kbs.2radio': '106.1', 'kbs.3radio': '104.9', 'kbs.1fm': '93.1', 'kbs.2fm': '89.1',
    'mbc.sfm': '95.9', 'mbc.fm4u': '91.9', 'sbs.lovefm': '103.5', 'sbs.powerfm': '107.7', 'ebs.fm': '104.5',
    'cbs.sfm': '98.1', 'cbs.mfm': '93.9', 'tbs.fm': '95.1', 'tbs.efm': '101.3', 'ytn': '94.5', 'kugak': '99.1',
    'bbs': '101.9', 'cpbc': '105.3', 'febc': '106.9', 'wbs': '89.7', 'kookbang': '100.5', 'ifm': '90.7', 'tbn': '99.9',
};

/** 채널 id("지역.방송사.채널.도시") -> 수도권 주파수 (모르면 "") */
export function frequencyOf(stationId) {
    const [region, stn, ch, city] = String(stationId).split('.');
    if (region !== 'seoul' || (city && city !== '-')) return '';
    return FREQ_SEOUL[ch && ch !== '-' ? `${stn}.${ch}` : stn] || FREQ_SEOUL[stn] || '';
}

/** 이름 -> [크게 쓸 방송사, 나머지] */
export function splitName(name) {
    const text = String(name || '').trim();
    for (const b of BRANDS) {
        const at = text.indexOf(b);
        if (at < 0) continue;
        const rest = `${text.slice(0, at)} ${text.slice(at + b.length)}`.replace(/\s+/g, ' ').trim();
        return [b, rest];
    }
    return [text, ''];
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** 글자 로고 SVG (data: 주소). 카드(16:9)에 맞춰 가로로 긴 판 */
export function textLogo(name, freq = '') {
    const [brand, rest] = splitName(name);
    const tail = [rest, freq].filter(Boolean).join(' ');
    // 글자 수로 크기를 맞춘다 (한글은 영문보다 넓게 센다)
    const width = (s, size) => [...s].reduce((w, c) => w + (/[가-힣]/.test(c) ? 1.0 : /[A-Z0-9]/.test(c) ? 0.68 : 0.55), 0) * size;
    let big = 64, small = 40;
    const room = 400;
    const total = () => width(brand, big) + (tail ? 16 + width(tail, small) : 0);
    while (total() > room && big > 20) { big -= 2; small = Math.max(16, small - 1.4); }
    const w = total();
    const x0 = 240 - w / 2;
    const x1 = x0 + width(brand, big) + 16;
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 160">'
        + '<rect x="4" y="4" width="472" height="152" rx="34" fill="#2a2b2f"/>'
        + '<rect x="4" y="4" width="472" height="152" rx="34" fill="none" stroke="#3a3b40" stroke-width="2"/>'
        + `<text x="${x0.toFixed(1)}" y="${(80 + big * 0.36).toFixed(1)}" font-family="Arial Black,Arial,Helvetica,sans-serif" font-weight="900" `
        + `font-size="${big}" fill="#f2f2f2" letter-spacing="-1">${esc(brand)}</text>`
        + (tail ? `<text x="${x1.toFixed(1)}" y="${(80 + big * 0.36).toFixed(1)}" font-family="Arial,Helvetica,'Apple SD Gothic Neo','Malgun Gothic',sans-serif" `
            + `font-weight="700" font-size="${small.toFixed(1)}" fill="#e6e6e6">${esc(tail)}</text>` : '')
        + '</svg>';
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
