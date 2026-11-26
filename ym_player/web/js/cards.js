// 포스터/채널 카드와 가로 줄(허브). 홈, 라이브러리, 검색, 상세 화면이 같이 쓴다.
import { h, icon, fmtDuration, toast } from './util.js';
import { player } from './player.js';
import { registry } from './providers/index.js';
import { channelHealth, isFavorite, toggleFavorite } from './providers/iptv.js';
import { isFavItem, toggleFavItem } from './favitems.js';
// 사진 보기는 열 때 받는다
const openViewer = (...a) => import('./viewer.js').then((m) => m.openViewer(...a));

const PLAYABLE = new Set(['movie', 'episode', 'track', 'clip']);

function img(src, cls, fallbackIcon, fit = '') {
    const box = h('div', { class: cls + (src && fit === 'contain' ? ' art-contain' : '') });
    // contain: 16:9 칸에 정사각형·세로 그림 - 자르지 않고 가운데에, 뒤는 같은 그림을 흐리게 (넷플릭스처럼)
    if (src && fit === 'contain') box.style.setProperty('--art-bg', `url("${String(src).replace(/"/g, '%22')}")`);
    if (src) {
        // 다른 사이트 그림(브런치·만개의레시피 등)은 우리 주소를 알리지 않고 받는다 (퍼 가기 막는 곳이 있어서)
        const im = h('img', { src, alt: '', loading: 'lazy', decoding: 'async', referrerpolicy: /^https?:/i.test(src) ? 'no-referrer' : null });
        im.addEventListener('error', () => { im.remove(); box.appendChild(icon(fallbackIcon, 'card-fallback')); });
        box.appendChild(im);
    } else {
        box.appendChild(icon(fallbackIcon, 'card-fallback'));
    }
    return box;
}

export function progressBar(ratio) {
    if (!ratio || ratio <= 0) return null;
    return h('div', { class: 'card-progress' }, h('div', { style: { width: `${Math.min(100, ratio * 100)}%` } }));
}

/** 포스터(2:3) / 정사각(음악) / 와이드(16:9) 카드 */
export function posterCard(item, { wide = false } = {}) {
    const provider = registry.byId(item.pid);
    const shape = wide ? 'wide' : item.shape;
    // 와이드 칸(이어보기)인데 그림이 넓은 그림이 아니면(팟캐스트·음악 정사각형, 책·포스터 세로) 자르지 않는다
    const fit = wide && item.shape !== 'wide' && (!item.wideThumb || item.wideThumb === item.thumb) ? 'contain' : '';
    const fallback = { movie: 'film', show: 'tv', season: 'tv', episode: 'tv', artist: 'music', album: 'compact-disc', track: 'music', photo: 'image', book: 'book-open', article: 'newspaper' }[item.type] || 'photo-film';
    const art = img(wide ? (item.wideThumb || item.thumb) : item.thumb, `card-art shape-${shape}`, fallback, fit);

    if (item.isNew) art.appendChild(h('span', { class: 'card-new', title: '지난번에 본 뒤 새로 올라옴', text: 'NEW' }));
    if (item.watched) art.appendChild(h('span', { class: 'card-watched', title: '시청함' }, icon('check')));
    else if (item.unwatched) art.appendChild(h('span', { class: 'card-count', text: String(item.unwatched) }));
    const bar = progressBar(item.progress);
    if (bar) art.appendChild(bar);

    // item.playable: 시리즈 카드처럼 종류는 재생 대상이 아니어도 [재생]이 되는 것 (다음 볼 회차를 틂)
    if ((PLAYABLE.has(item.type) || item.playable) && provider) {
        art.appendChild(h('button', {
            class: 'card-play', title: '재생',
            on: {
                click: (e) => { e.preventDefault(); e.stopPropagation(); player.playItem(provider, item.id); },
                // 누르기 직전에 서버가 미리 준비할 수 있는 소스면 (텔레그램: 형식 확인)
                pointerenter: () => { if (provider.warm) provider.warm(item.id); },
            },
        }, icon('play')));
    }
    // 즐겨찾기 별 (레시피처럼 item.fav = {title, image, sub}를 주는 소스)
    if (item.fav) {
        const on0 = isFavItem(item.pid, item.id);
        const star = h('span', { class: `card-star${on0 ? ' on' : ''}`, role: 'button', tabindex: '0', title: '즐겨찾기' },
            h('i', { class: on0 ? 'fa-solid fa-star' : 'fa-regular fa-star' }));
        const flip = (e) => {
            e.preventDefault(); e.stopPropagation();
            const on = toggleFavItem({ pid: item.pid, id: item.id, ...item.fav });
            star.classList.toggle('on', on);
            star.firstChild.className = on ? 'fa-solid fa-star' : 'fa-regular fa-star';
            toast(on ? `${item.title}: 즐겨찾기에 추가했습니다.` : `${item.title}: 즐겨찾기에서 뺐습니다.`);
            if (item.onFavChange) item.onFavChange(on);
        };
        star.addEventListener('click', flip);
        star.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') flip(e); });
        art.appendChild(star);
    }
    if (item.type === 'photo') {
        art.appendChild(h('span', { class: 'card-kind', title: '사진' }, icon('image')));
        // 사진은 뷰어로. 같은 격자/줄에 있는 사진끼리 좌우로 넘겨 본다
        const card = h('a', {
            class: `card card-${shape} card-photo`, href: '#',
            on: {
                click: (e) => {
                    e.preventDefault();
                    const box = card.closest('.grid, .row-track, .item-list, .item-table') || card.parentNode;
                    const cards = [...box.querySelectorAll('.card-photo')];
                    openViewer(cards.map((c) => c._item), cards.indexOf(card));
                },
            },
        },
        art,
        h('div', { class: 'card-title', text: item.title, title: item.title }),
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null);
        card._item = item;
        return card;
    }
    // item.onOpen: 상세 화면 대신 바로 여는 것 (텔레그램 웹툰 한 화 -> 웹툰 보기)
    return h('a', { class: `card card-${shape}${item.cardClass ? ` ${item.cardClass}` : ''}`, href: item.route || '#',
        on: item.onOpen ? { click: (e) => { e.preventDefault(); item.onOpen(); } } : {} },
        art,
        h('div', { class: 'card-title', text: item.title, title: item.title }),
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null,
        // 셋째 줄 - 격자 보기에서만 보인다 (app.css .grid .card-file): 소스가 준 extraLine(텔레그램 영상: 등록일), 없으면 파일 이름
        item.extraLine || item.fileName ? h('div', { class: 'card-file', text: item.extraLine || item.fileName, title: item.fileName || item.extraLine }) : null);
}

/** 세부/테이블 보기에 쓸 정보 (Plex 메타데이터 / 텔레그램 색인) */
export function itemFacts(item) {
    if (item.facts) return item.facts;   // 소스가 직접 채운 것 (팟캐스트)
    const m = item.raw || {};
    const p = registry.byId(item.pid);
    if (p && p.kind === 'plex') {
        return {
            year: m.year ? String(m.year) : '',
            date: m.addedAt ? new Date(m.addedAt * 1000).toISOString().slice(0, 10) : '',
            length: m.duration ? fmtDuration(m.duration) : (m.leafCount ? `${m.leafCount}화` : (m.childCount ? `시즌 ${m.childCount}` : '')),
            rating: m.audienceRating || m.rating || '',
            extra: m.contentRating || '',
            summary: m.summary || '',
        };
    }
    const caption = String(item.caption != null ? item.caption : (m.caption || ''));
    const lines = caption.split('\n');
    const summary = (lines[0] || '').trim() === item.title ? lines.slice(1).join('\n').trim() : caption.trim();
    return {
        year: '',
        date: (m.date || '').slice(0, 10),
        length: m.series ? `${m.series.count}화`
            : m.kind === 'book' || (m.kind === 'doc' && m.mime === 'application/pdf') ? (m.duration ? `${Math.round(m.duration)}쪽` : '')
                : m.kind === 'doc' ? ''
                : (m.duration ? fmtDuration(m.duration * 1000) : ''),
        rating: '',
        extra: '',
        summary,
    };
}

/** 세부 보기(포스터 + 제목·정보·줄거리) / 테이블 보기(작은 그림 + 한 줄) 한 항목. 누르면 카드를 누른 것과 같다 */
export function listItem(item, mode, playlist = null) {
    // 라이브 채널(IPTV·라디오 방송국)은 채널 카드를 그림으로, 날짜·길이 대신 분류와 LIVE
    const live = item.type === 'channel';
    const card = live ? channelCard(item, playlist) : posterCard(item);
    // (세부 보기에선 부제가 이미 분류이거나 지금 나오는 곡이라 분류는 부제와 다를 때만, LIVE는 그림 위에 이미 있음)
    const group = live && item.group !== item.subtitle ? item.group || '' : '';
    const f = live ? { year: '', date: mode === 'table' ? item.group || '' : '', length: '', extra: group, rating: '', summary: '' } : itemFacts(item);
    const status = live ? h('span', { class: 'list-status' }, mode === 'table' ? h('span', { class: 'live-badge', text: 'LIVE' }) : null)
        : item.watched ? h('span', { class: 'list-status done', title: '봤음' }, icon('check'))
        : item.progress > 0 ? h('span', { class: 'list-status', text: `${Math.round(item.progress * 100)}%` })
            : item.unwatched ? h('span', { class: 'list-status', text: `안 본 ${item.unwatched}` }) : h('span', { class: 'list-status' });
    const open = (e) => { if (!e.target.closest('a, button')) card.click(); };
    if (mode === 'table') {
        return h('div', { class: 'table-row', role: 'row', on: { click: open } },
            card,
            // (좁은 화면은 날짜 칸이 없어 extraLine(등록일)을 부제 뒤에 붙인다)
            h('div', { class: 'table-title' }, h('b', { text: item.title }),
                item.subtitle || item.extraLine ? h('small', { text: [item.subtitle, item.extraLine].filter(Boolean).join(' · ') }) : null),
            h('span', { class: 'table-cell', text: f.year || f.date }),
            h('span', { class: 'table-cell', text: f.length }),
            status);
    }
    const meta = [f.year, f.length, f.extra, f.rating ? `★ ${Number(f.rating).toFixed(1)}` : '', !f.year ? f.date : ''].filter(Boolean);
    return h('div', { class: 'list-row', on: { click: open } },
        card,
        h('div', { class: 'list-info' },
            h('h3', { class: 'list-title', text: item.title }),
            item.subtitle ? h('div', { class: 'list-sub muted', text: item.subtitle }) : null,
            meta.length ? h('div', { class: 'list-meta' }, meta.map((x) => h('span', { text: x }))) : null,
            f.summary ? h('p', { class: 'list-summary', text: f.summary }) : null,
            status));
}

/** 라이브 채널 카드 - 누르면 바로 재생. playlist: 위/아래 채널 전환용 */
export function channelCard(item, playlist) {
    const provider = registry.byId(item.pid);
    const ch = item.channel;
    const art = img(item.logo, 'card-art shape-channel', item.fallbackIcon || 'tv');
    art.appendChild(h('span', { class: 'live-badge corner', text: 'LIVE' }));
    const bar = progressBar(item.progress);
    if (bar) art.appendChild(bar);
    art.appendChild(h('span', { class: 'card-play static' }, icon('play')));
    // 즐겨찾기 별 (공중파 라디오처럼 카드에서 바로 고르는 소스)
    if (item.favoritable) {
        const star = h('span', { class: `card-star${isFavorite(ch) ? ' on' : ''}`, role: 'button', tabindex: '0', title: '즐겨찾기' }, icon('star'));
        const flip = (e) => {
            e.preventDefault(); e.stopPropagation();
            const on = toggleFavorite(ch);
            star.classList.toggle('on', on);
            star.firstChild.className = on ? 'fa-solid fa-star' : 'fa-regular fa-star';
            toast(on ? `${item.title}: 즐겨찾기에 추가했습니다.` : `${item.title}: 즐겨찾기에서 뺐습니다.`);
        };
        star.firstChild.className = isFavorite(ch) ? 'fa-solid fa-star' : 'fa-regular fa-star';
        star.addEventListener('click', flip);
        star.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') flip(e); });
        art.appendChild(star);
    }
    // [관리자] 이름 바꾸기 연필 (공중파 라디오)
    if (item.onRename) {
        const pen = h('span', { class: 'card-star card-rename', role: 'button', tabindex: '0', title: '이름 바꾸기 (관리자)' }, icon('pen'));
        const go = (e) => { e.preventDefault(); e.stopPropagation(); item.onRename(); };
        pen.addEventListener('click', go);
        pen.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') go(e); });
        art.appendChild(pen);
    }
    const offline = channelHealth.get(ch.id) === 'offline';
    return h('button', {
        class: `card card-channel${offline ? ' offline' : ''}`,
        title: item.subtitle ? `${item.title} — ${item.subtitle}` : item.title,
        on: { click: () => player.playChannel(provider, ch, playlist) },
    },
    art,
    h('div', { class: 'card-title', text: item.title }),
    h('div', { class: 'card-sub', text: item.subtitle || ' ' }),
    item.timeLine ? h('div', { class: 'card-time', text: item.timeLine }) : null);
}

/** 홈 맨 위 큰 배너 (넷플릭스처럼): 그림 + 제목·부제·줄거리 + [재생]/[자세히]. actions: 덧붙일 단추들 */
export function heroBanner(item, actions = []) {
    const provider = registry.byId(item.pid);
    const src = item.wideThumb || item.thumb || '';
    const wideArt = item.shape === 'wide' || (item.wideThumb && item.wideThumb !== item.thumb);
    const f = itemFacts(item);
    const canPlay = (PLAYABLE.has(item.type) || item.playable) && provider;
    const play = canPlay ? h('button', { class: 'btn hero-play', on: { click: () => player.playItem(provider, item.id) } },
        icon('play'), item.progress > 0 ? ' 이어보기' : ' 재생') : null;
    const more = item.route ? h('a', { class: 'btn hero-more', href: item.route }, icon('circle-info'), ' 자세히') : null;
    const bar = progressBar(item.progress);
    const box = h('section', { class: `hero${wideArt ? '' : ' hero-soft'}` },
        h('div', { class: 'hero-art' }, src ? h('img', { src, alt: '', decoding: 'async', referrerpolicy: /^https?:/i.test(src) ? 'no-referrer' : null }) : null),
        // 넓은 그림이 아니면(포스터·정사각형) 오른쪽에 원래 그림을 그대로 한 장 더
        !wideArt && item.thumb ? h('img', { class: 'hero-poster', src: item.thumb, alt: '' }) : null,
        h('div', { class: 'hero-shade' }),
        h('div', { class: 'hero-body' },
            item.heroLabel ? h('div', { class: 'hero-label', text: item.heroLabel }) : null,
            h('h2', { class: 'hero-title', text: item.title }),
            item.subtitle ? h('div', { class: 'hero-sub', text: item.subtitle }) : null,
            f.summary ? h('p', { class: 'hero-summary', text: f.summary }) : null,
            bar ? h('div', { class: 'hero-progress' }, bar) : null,
            h('div', { class: 'hero-actions' }, play, more, ...actions)));
    return box;
}

export function cardFor(item, playlist, wide = false) {
    return item.type === 'channel' ? channelCard(item, playlist) : posterCard(item, { wide });
}

export function playlistOf(items) {
    return items.filter((i) => i.type === 'channel').map((i) => ({ provider: registry.byId(i.pid), channel: i.channel }));
}

/** 제목 + 좌우로 넘기는 카드 줄. hub.wide면 모든 카드를 16:9로 (여러 소스를 섞은 이어보기 줄) */
export function hubRow(hub) {   // hub.action: 줄 제목 오른쪽에 둘 단추 (이어보기의 [랜덤 재생])
    const playlist = hub.live ? playlistOf(hub.items) : null;
    const track = h('div', { class: 'row-track' }, hub.items.map((it) => cardFor(it, playlist, !!hub.wide)));
    const scroll = (dir) => track.scrollBy({ left: dir * track.clientWidth * 0.85, behavior: 'smooth' });
    return h('section', { class: 'row' },
        h('div', { class: 'row-head' },
            h('h3', {}, hub.title, hub.source ? h('span', { class: 'row-source', text: hub.source }) : null),
            hub.action || null,
            hub.more ? h('a', { class: 'row-more', href: hub.more, text: '모두 보기' }) : null),
        h('div', { class: 'row-wrap' },
            h('button', { class: 'row-arrow left', title: '이전', on: { click: () => scroll(-1) } }, icon('chevron-left')),
            track,
            h('button', { class: 'row-arrow right', title: '다음', on: { click: () => scroll(1) } }, icon('chevron-right'))));
}

export function emptyState(iconName, title, text, action) {
    return h('div', { class: 'empty' },
        icon(iconName, 'empty-icon'),
        h('h2', { text: title }),
        text ? h('p', { text }) : null,
        action || null);
}

/** 불러오는 동안 자리를 잡아 두는 빈 카드 줄 (글자 대신 - 다 불러와도 아래 내용이 밀리지 않게) */
export function skeletonRow(label = '', { count = 6, shape = 'wide' } = {}) {
    return h('section', { class: 'row skeleton-row', 'aria-busy': 'true', title: label },
        h('div', { class: 'row-head' }, h('div', { class: 'sk-title' })),
        h('div', { class: 'row-wrap' }, h('div', { class: 'row-track' },
            Array.from({ length: count }, () => h('div', { class: `card card-${shape} sk-card` },
                h('div', { class: `card-art shape-${shape} sk` }), h('div', { class: 'sk-line' }), h('div', { class: 'sk-line short' }))))));
}

export function loading(text = '불러오는 중...') {
    return h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text }));
}
