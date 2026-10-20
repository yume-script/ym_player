// 웹툰 보기 (텔레그램 "웹툰" 채널의 zip/cbz 압축 파일). 압축 안의 그림을 위아래로 이어 붙여 스크롤로 본다.
//   스크롤 / 휠 / ↑↓ / Space / PageUp·PageDown: 넘겨 보기     ← →: 이전·다음 화     Esc / 뒤로 가기: 닫기
//   화면 가운데를 누르면 위아래 막대를 숨기거나 보인다. 끝까지 내리면 본 것으로 표시하고 [다음 화]
// 보던 위치(쪽 + 그 쪽에서 내려온 비율)는 서버에 저장해서 다음에 이어서 본다.
// 압축 파일은 처음 열 때 서버가 텔레그램에서 통째로 받으므로 그동안 받은 비율을 보여 준다.
// source: { load(id, onProgress) -> {id, title, sub, pages: [주소], position, prev, next}, save(id, position, total, done) }
import { h, icon, toast, storeGet, storeSet } from './util.js';

const WIDE_KEY = 'yp_reader_wide';
const SAVE_EVERY_MS = 4000;

let current = null;

export function openReader(source, id) {
    if (current) current.close(true);
    current = new Reader(source);
    current.open(id);
    return current;
}

class Reader {
    constructor(source) {
        this.source = source;
        this.book = null;
        this.seq = 0;
        this.saved = { at: 0, pos: -1 };
        this.done = false;

        this.titleEl = h('b', { class: 'viewer-title' });
        this.subEl = h('span', { class: 'viewer-sub' });
        this.counter = h('span', { class: 'viewer-count' });
        this.prevBtn = h('button', { class: 'icon-btn', title: '이전 화 (←)', on: { click: () => this.go(-1) } }, icon('backward-step'));
        this.nextBtn = h('button', { class: 'icon-btn', title: '다음 화 (→)', on: { click: () => this.go(1) } }, icon('forward-step'));
        this.wideBtn = h('button', { class: 'icon-btn', title: '화면 폭에 맞추기 / 좁게', on: { click: () => this.toggleWide() } }, icon('left-right'));
        this.pagesEl = h('div', { class: 'reader-pages' });
        this.endEl = h('div', { class: 'reader-end' });
        this.statusEl = h('div', { class: 'reader-status hidden' });
        this.scroller = h('div', { class: 'reader-scroll', tabindex: '-1' }, this.pagesEl, this.endEl);
        this.bar = h('div', { class: 'reader-bar' },
            h('button', { class: 'icon-btn', title: '닫기 (Esc)', on: { click: () => this.close() } }, icon('xmark')),
            h('div', { class: 'viewer-titles' }, this.titleEl, this.subEl),
            this.counter, this.prevBtn, this.nextBtn, this.wideBtn);
        this.progressEl = h('div', { class: 'reader-progress' }, h('div'));
        this.root = h('div', { class: `reader${storeGet(WIDE_KEY, false) ? ' wide' : ''}`, role: 'dialog', 'aria-label': '웹툰 보기' },
            this.scroller, this.bar, this.progressEl, this.statusEl);
        document.body.appendChild(this.root);
        document.body.classList.add('viewer-open');

        this._onKey = (e) => this.onKey(e);
        document.addEventListener('keydown', this._onKey, true);
        history.pushState({ ypReader: true }, '');
        this._onPop = () => this.close(true);
        window.addEventListener('popstate', this._onPop);

        this._lastTop = 0;
        this._raf = 0;
        this.scroller.addEventListener('scroll', () => {
            if (this._raf) return;
            this._raf = requestAnimationFrame(() => { this._raf = 0; this.onScroll(); });
        }, { passive: true });
        // 가운데를 누르면 막대 보이기/숨기기 (버튼·링크는 빼고)
        this.scroller.addEventListener('click', (e) => {
            if (e.target.closest('button, a')) return;
            this.root.classList.toggle('bars-hidden');
        });
        this._io = new IntersectionObserver((entries) => {
            for (const en of entries) if (en.isIntersecting) this.loadPage(en.target);
        }, { root: this.scroller, rootMargin: '150% 0px 400% 0px' });
        this._onHide = () => { if (document.visibilityState === 'hidden') this.save(true); };
        document.addEventListener('visibilitychange', this._onHide);
        this.scroller.focus({ preventScroll: true });
    }

    status(text, isError = false) {
        this.statusEl.classList.toggle('hidden', !text);
        this.statusEl.classList.toggle('error', isError);
        this.statusEl.textContent = '';
        if (text) {
            if (!isError) this.statusEl.appendChild(icon('circle-notch', 'fa-spin'));
            this.statusEl.append(` ${text}`);
        }
    }

    async open(id) {
        const seq = ++this.seq;
        this.save(true);
        this.book = null;
        this.done = false;
        this._io.disconnect();
        this.pagesEl.replaceChildren();
        this.endEl.replaceChildren();
        this.counter.textContent = '';
        this.status('불러오는 중…');
        let book;
        try {
            book = await this.source.load(id, (p) => {
                if (seq === this.seq) this.status(`텔레그램에서 받는 중… ${Math.round(p * 100)}%`);
            }, () => seq !== this.seq);
        } catch (e) {
            if (seq === this.seq) this.status(`열지 못했습니다: ${e.message}`, true);
            return;
        }
        if (seq !== this.seq || !book) return;
        this.book = book;
        this.saved = { at: Date.now(), pos: book.position || 0 };
        this.status('');
        this.titleEl.textContent = book.title;
        this.subEl.textContent = book.sub || '';
        this.prevBtn.disabled = !book.prev;
        this.nextBtn.disabled = !book.next;
        this.pageEls = book.pages.map((src, i) => {
            const el = h('div', { class: 'reader-page', dataset: { n: String(i) } });
            el._src = src;
            this.pagesEl.appendChild(el);
            return el;
        });
        this.renderEnd();
        // 보던 쪽으로 (앞쪽 그림이 아직 안 와서 자리만 잡혀 있어도, 그 그림이 오면 loadPage가 위치를 맞춘다)
        const start = Math.min(Math.max(0, book.position || 0), Math.max(0, book.pages.length - 0.001));
        const first = this.pageEls[Math.floor(start)];
        this.scroller.scrollTop = 0;
        if (first && start > 0) {
            this._resume = { el: first, frac: start - Math.floor(start) };
            this.loadPage(first);
            this.scroller.scrollTop = first.offsetTop;
        }
        for (const el of this.pageEls) this._io.observe(el);
        this.onScroll();
    }

    renderEnd() {
        const b = this.book;
        this.endEl.replaceChildren(
            h('p', { class: 'reader-end-title', text: `${b.title} 끝` }),
            b.next
                ? h('button', { class: 'btn btn-accent btn-lg', on: { click: () => this.go(1) } }, icon('forward-step'), ` 다음 화 · ${b.next.title}`)
                : h('p', { class: 'muted', text: b.series ? '마지막 회차입니다.' : '' }),
            h('div', { class: 'reader-end-actions' },
                b.prev ? h('button', { class: 'btn', on: { click: () => this.go(-1) } }, icon('backward-step'), ' 이전 화') : null,
                h('button', { class: 'btn', on: { click: () => this.close() } }, icon('list'), ' 목록으로')));
    }

    loadPage(el) {
        if (el._loading) return;
        el._loading = true;
        this._io.unobserve(el);
        const img = h('img', { alt: `${Number(el.dataset.n) + 1}쪽`, decoding: 'async', draggable: false });
        img.addEventListener('load', () => {
            // 지금 보는 곳보다 위에 있는 쪽이 늦게 오면 그만큼 아래로 밀리지 않게 스크롤 위치를 맞춘다
            const before = el.offsetHeight;
            const above = el.offsetTop + before <= this.scroller.scrollTop + 1;
            el.classList.add('ready');
            const r = this._resume;
            if (r && r.el === el) {
                this._resume = null;
                this.scroller.scrollTop = el.offsetTop + el.offsetHeight * r.frac;
            } else if (above) {
                this.scroller.scrollTop += el.offsetHeight - before;
            }
        });
        img.addEventListener('error', () => {
            el.classList.add('ready', 'failed');
            el.replaceChildren(h('button', { class: 'btn', on: { click: (e) => {
                e.stopPropagation();
                el._loading = false;
                el.classList.remove('ready', 'failed');
                el.replaceChildren();
                this.loadPage(el);
            } } }, icon('rotate-right'), ` ${Number(el.dataset.n) + 1}쪽 다시 불러오기`));
        });
        img.src = el._src;
        el.appendChild(img);
    }

    /** 화면 맨 위에 걸친 쪽과 그 쪽에서 내려온 비율 -> 0..쪽 수 */
    position() {
        const els = this.pageEls || [];
        if (!els.length) return 0;
        const top = this.scroller.scrollTop;
        let lo = 0, hi = els.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (els[mid].offsetTop <= top) lo = mid; else hi = mid - 1;
        }
        const el = els[lo];
        return lo + Math.min(0.999, Math.max(0, (top - el.offsetTop) / Math.max(1, el.offsetHeight)));
    }

    onScroll() {
        if (!this.book) return;
        const top = this.scroller.scrollTop;
        // 아래로 넘기면 막대를 숨기고, 위로 올리면 다시
        if (top > this._lastTop + 30) this.root.classList.add('bars-hidden');
        else if (top < this._lastTop - 30 || top < 10) this.root.classList.remove('bars-hidden');
        if (Math.abs(top - this._lastTop) > 30 || top < 10) this._lastTop = top;
        const n = this.book.pages.length;
        const pos = this.position();
        this.counter.textContent = `${Math.min(n, Math.floor(pos) + 1)} / ${n}`;
        const max = this.scroller.scrollHeight - this.scroller.clientHeight;
        this.progressEl.firstChild.style.width = `${max > 0 ? Math.min(100, (top / max) * 100) : 0}%`;
        // 끝(다음 화 안내)이 보이면 본 것으로
        if (!this.done && !this._resume && this.endEl.getBoundingClientRect().top < this.scroller.clientHeight) {
            this.done = true;
            this.save(true);
        }
        this.save(false);
    }

    save(force) {
        const b = this.book;
        if (!b || !b.pages.length) return;
        const pos = this.done ? b.pages.length : this.position();
        if (!force && (Date.now() - this.saved.at < SAVE_EVERY_MS || Math.abs(pos - this.saved.pos) < 0.05)) return;
        if (Math.abs(pos - this.saved.pos) < 0.01 && !this.done) return;
        this.saved = { at: Date.now(), pos };
        Promise.resolve(this.source.save(b.id, pos, b.pages.length, this.done)).catch(() => {});
    }

    go(dir) {
        const b = this.book;
        const target = b && (dir < 0 ? b.prev : b.next);
        if (!target) {
            if (b) toast(dir < 0 ? '첫 회차입니다.' : '마지막 회차입니다.');
            return;
        }
        this.open(target.id);
    }

    toggleWide() {
        const wide = this.root.classList.toggle('wide');
        storeSet(WIDE_KEY, wide);
    }

    onKey(e) {
        if (e.target.closest && e.target.closest('input, textarea, select')) return;
        const page = () => this.scroller.clientHeight * 0.85;
        const handled = {
            Escape: () => this.close(),
            ArrowLeft: () => this.go(-1),
            ArrowRight: () => this.go(1),
            ArrowDown: () => this.scroller.scrollBy({ top: 120 }),
            ArrowUp: () => this.scroller.scrollBy({ top: -120 }),
            ' ': () => this.scroller.scrollBy({ top: e.shiftKey ? -page() : page(), behavior: 'smooth' }),
            PageDown: () => this.scroller.scrollBy({ top: page(), behavior: 'smooth' }),
            PageUp: () => this.scroller.scrollBy({ top: -page(), behavior: 'smooth' }),
            Home: () => { this.scroller.scrollTop = 0; },
            End: () => { this.scroller.scrollTop = this.scroller.scrollHeight; },
        }[e.key];
        if (!handled) return;
        e.preventDefault();
        e.stopPropagation();
        handled();
    }

    close(fromHistory = false) {
        if (!this.root.isConnected) return;
        this.save(true);
        this.seq++;
        this._io.disconnect();
        document.removeEventListener('keydown', this._onKey, true);
        document.removeEventListener('visibilitychange', this._onHide);
        window.removeEventListener('popstate', this._onPop);
        this.root.remove();
        document.body.classList.remove('viewer-open');
        if (!fromHistory && history.state && history.state.ypReader) history.back();
        if (current === this) current = null;
        if (this.source.onClose) this.source.onClose();
    }
}
