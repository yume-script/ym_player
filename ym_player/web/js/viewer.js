// 사진 뷰어 (텔레그램 채널의 사진/이미지 파일). 같은 격자에 있는 사진끼리 좌우로 넘겨 본다.
//   ← → / 화면 양옆 버튼 / 옆으로 밀기: 이전·다음     Esc / 아래로 밀기 / 뒤로 가기: 닫기
//   두 번 누르기 / 휠 / 두 손가락 / + − 0: 확대·축소    확대한 채로 끌기: 이동
// 원본이 오는 동안에는 썸네일을 흐리게 먼저 보여 준다 (텔레그램에서 처음 받을 때 몇 초 걸릴 수 있음).
import { h, icon } from './util.js';

const MAX_SCALE = 8;

let current = null; // 열려 있는 뷰어 (한 번에 하나)

export function openViewer(items, index = 0) {
    if (!items || !items.length) return;
    if (current) current.close(true);
    current = new Viewer(items, Math.max(0, index));
}

class Viewer {
    constructor(items, index) {
        this.items = items;
        this.index = index;
        this.scale = 1; this.x = 0; this.y = 0;
        this.pointers = new Map();

        this.thumb = h('img', { class: 'viewer-thumb', alt: '' });
        this.img = h('img', { class: 'viewer-img', alt: '', draggable: false });
        this.spin = h('div', { class: 'viewer-spin' }, icon('circle-notch', 'fa-spin'));
        this.err = h('div', { class: 'viewer-err hidden' });
        this.stage = h('div', { class: 'viewer-stage' }, this.thumb, this.img, this.spin, this.err);
        this.counter = h('span', { class: 'viewer-count' });
        this.titleEl = h('b', { class: 'viewer-title' });
        this.subEl = h('span', { class: 'viewer-sub' });
        this.caption = h('div', { class: 'viewer-caption hidden' });
        this.openLink = h('a', { class: 'icon-btn', title: '원본 새 탭에서 열기', target: '_blank', rel: 'noopener' }, icon('arrow-up-right-from-square'));
        this.zoomBtn = h('button', { class: 'icon-btn', title: '확대/맞춤 (두 번 누르기)', on: { click: () => this.toggleZoom() } }, icon('magnifying-glass-plus'));
        this.prevBtn = h('button', { class: 'viewer-nav prev', title: '이전 (←)', on: { click: (e) => { e.stopPropagation(); this.go(-1); } } }, icon('chevron-left'));
        this.nextBtn = h('button', { class: 'viewer-nav next', title: '다음 (→)', on: { click: (e) => { e.stopPropagation(); this.go(1); } } }, icon('chevron-right'));
        this.root = h('div', { class: 'viewer', role: 'dialog', 'aria-label': '사진 보기' },
            h('div', { class: 'viewer-top' },
                this.counter,
                h('div', { class: 'viewer-titles' }, this.titleEl, this.subEl),
                this.zoomBtn, this.openLink,
                h('button', { class: 'icon-btn', title: '닫기 (Esc)', on: { click: () => this.close() } }, icon('xmark'))),
            this.stage, this.prevBtn, this.nextBtn, this.caption);
        document.body.appendChild(this.root);
        document.body.classList.add('viewer-open');

        this.img.addEventListener('load', () => this._loaded());
        this.img.addEventListener('error', () => this._failed());
        this._onKey = (e) => this.key(e);
        document.addEventListener('keydown', this._onKey, true);
        this.stage.addEventListener('wheel', (e) => this.wheel(e), { passive: false });
        this.stage.addEventListener('dblclick', (e) => this.toggleZoom(e));
        this.stage.addEventListener('pointerdown', (e) => this.down(e));
        this.stage.addEventListener('pointermove', (e) => this.move(e));
        for (const ev of ['pointerup', 'pointercancel']) this.stage.addEventListener(ev, (e) => this.up(e));
        // 휴대폰 뒤로 가기로 닫히게
        history.pushState({ ypViewer: true }, '');
        this._onPop = () => this.close(true);
        window.addEventListener('popstate', this._onPop);
        this.show();
    }

    get item() { return this.items[this.index]; }

    show() {
        const it = this.item;
        this.resetZoom();
        this.counter.textContent = this.items.length > 1 ? `${this.index + 1} / ${this.items.length}` : '';
        this.titleEl.textContent = it.title || '';
        this.subEl.textContent = it.subtitle || '';
        this.caption.textContent = it.caption || '';
        this.caption.classList.toggle('hidden', !it.caption);
        this.openLink.href = it.image;
        this.prevBtn.classList.toggle('hidden', this.index === 0);
        this.nextBtn.classList.toggle('hidden', this.index >= this.items.length - 1);
        this.err.classList.add('hidden');
        this.thumb.classList.toggle('hidden', !it.thumb);
        if (it.thumb) this.thumb.src = it.thumb;
        this.img.classList.remove('ready');
        this.spin.classList.remove('hidden');
        this.img.src = it.image;
        // 다음/이전 사진을 미리 받아 둔다
        for (const d of [1, -1]) {
            const n = this.items[this.index + d];
            if (n) new Image().src = n.image;
        }
    }

    _loaded() {
        this.spin.classList.add('hidden');
        this.thumb.classList.add('hidden');
        this.img.classList.add('ready');
    }

    async _failed() {
        this.spin.classList.add('hidden');
        let msg = '사진을 불러오지 못했습니다.';
        try {
            const j = await (await fetch(this.item.image)).json();
            if (j && j.error) msg = j.error;
        } catch (e) { /* 이미지가 아닌 오류 응답이 아니면 기본 문구 */ }
        if (/\.(heic|heif)$/i.test(this.item.raw && this.item.raw.file_name || '')) msg += ' (HEIC 사진은 Safari에서만 열립니다)';
        this.err.textContent = msg;
        this.err.classList.remove('hidden');
    }

    go(d) {
        const n = this.index + d;
        if (n < 0 || n >= this.items.length) return;
        this.index = n;
        this.show();
    }

    close(fromHistory = false) {
        if (current !== this) return;
        current = null;
        document.removeEventListener('keydown', this._onKey, true);
        window.removeEventListener('popstate', this._onPop);
        this.root.remove();
        document.body.classList.remove('viewer-open');
        if (!fromHistory && history.state && history.state.ypViewer) history.back();
    }

    key(e) {
        const k = e.key;
        const handled = { ArrowLeft: () => this.go(-1), ArrowRight: () => this.go(1), Escape: () => this.close(),
            '+': () => this.zoomAt(this.scale * 1.5), '=': () => this.zoomAt(this.scale * 1.5),
            '-': () => this.zoomAt(this.scale / 1.5), 0: () => this.resetZoom() }[k];
        if (!handled) return;
        e.preventDefault();
        e.stopPropagation(); // 재생기/검색 단축키로 가지 않게
        handled();
    }

    // ----- 확대/이동 -----
    apply() {
        this.img.style.transform = `translate(${this.x}px, ${this.y}px) scale(${this.scale})`;
        this.thumb.style.transform = this.img.style.transform;
        this.root.classList.toggle('zoomed', this.scale > 1.01);
        this.zoomBtn.firstChild.className = `fa-solid fa-magnifying-glass-${this.scale > 1.01 ? 'minus' : 'plus'}`;
    }

    resetZoom() { this.scale = 1; this.x = 0; this.y = 0; this.apply(); }

    /** cx, cy(화면 좌표)를 중심으로 scale까지 */
    zoomAt(scale, cx, cy) {
        const r = this.stage.getBoundingClientRect();
        const px = (cx ?? r.left + r.width / 2) - (r.left + r.width / 2);
        const py = (cy ?? r.top + r.height / 2) - (r.top + r.height / 2);
        const s = Math.min(MAX_SCALE, Math.max(1, scale));
        // 손가락/커서 아래 지점이 그대로 있게
        this.x = px - (px - this.x) * (s / this.scale);
        this.y = py - (py - this.y) * (s / this.scale);
        this.scale = s;
        if (s === 1) { this.x = 0; this.y = 0; }
        this.apply();
    }

    toggleZoom(e) {
        if (this.scale > 1.01) this.resetZoom();
        else this.zoomAt(2.5, e && e.clientX, e && e.clientY);
    }

    wheel(e) {
        e.preventDefault();
        this.zoomAt(this.scale * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX, e.clientY);
    }

    down(e) {
        if (e.target.closest('button, a')) return;
        this.stage.setPointerCapture(e.pointerId);
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.pointers.size === 1) this.start = { x: e.clientX, y: e.clientY, ox: this.x, oy: this.y, t: Date.now() };
        if (this.pointers.size === 2) {
            const [a, b] = [...this.pointers.values()];
            this.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), s: this.scale };
        }
    }

    move(e) {
        if (!this.pointers.has(e.pointerId)) return;
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.pointers.size === 2 && this.pinch) {
            const [a, b] = [...this.pointers.values()];
            this.zoomAt(this.pinch.s * Math.hypot(a.x - b.x, a.y - b.y) / (this.pinch.d || 1), (a.x + b.x) / 2, (a.y + b.y) / 2);
            return;
        }
        if (this.pointers.size !== 1 || !this.start) return;
        const dx = e.clientX - this.start.x, dy = e.clientY - this.start.y;
        if (this.scale > 1.01) {
            this.x = this.start.ox + dx; this.y = this.start.oy + dy;
            this.apply();
        } else {
            // 맞춤 상태에서는 미는 방향을 살짝 따라가게 (놓으면 넘기거나 닫음)
            this.img.style.transform = `translate(${dx}px, ${Math.max(0, dy)}px)`;
        }
    }

    up(e) {
        if (!this.pointers.has(e.pointerId)) return;
        this.pointers.delete(e.pointerId);
        if (this.pointers.size < 2) this.pinch = null;
        if (this.pointers.size || !this.start) return;
        const dx = e.clientX - this.start.x, dy = e.clientY - this.start.y;
        const quick = Date.now() - this.start.t < 600;
        this.start = null;
        if (this.scale > 1.01) return;
        this.apply();
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) this.go(dx < 0 ? 1 : -1);
        else if (dy > 110 && quick) this.close();
    }
}
