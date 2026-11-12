// 즐겨찾는 항목 (레시피처럼 목록이 사이트에 있는 것) - 사용자마다 서버(/api/me/prefs의 fav_items)에 둔다.
// 목록을 다시 받지 않고도 카드를 그릴 수 있게 제목·그림·부제를 같이 저장한다: {pid, id, title, image, sub}
import { api, toast } from './util.js';
import { registry } from './providers/index.js';

const MAX = 500;

function list() {
    const p = registry.prefs || (registry.prefs = {});
    if (!Array.isArray(p.fav_items)) p.fav_items = [];
    return p.fav_items;
}

export function favItems(pid) { return list().filter((x) => x.pid === pid); }

export function isFavItem(pid, id) { return list().some((x) => x.pid === pid && x.id === String(id)); }

/** 넣거나 뺀다. 반환: 지금 즐겨찾기인지 */
export function toggleFavItem(entry) {
    const items = list();
    const at = items.findIndex((x) => x.pid === entry.pid && x.id === String(entry.id));
    if (at >= 0) items.splice(at, 1);
    else items.unshift({ pid: entry.pid, id: String(entry.id), title: entry.title || '', image: entry.image || '', sub: entry.sub || '' });
    if (items.length > MAX) items.length = MAX;
    api('/api/me/prefs', { method: 'PUT', json: { fav_items: items } }).catch((e) => toast(`즐겨찾기 저장 실패: ${e.message}`, 'error'));
    return at < 0;
}
