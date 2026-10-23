// 책 보기 (텔레그램의 PDF / EPUB 파일).
//   PDF: PDF.js로 쪽을 위아래로 이어 그린다. 파일을 통째로 받지 않고 보는 쪽에 필요한 부분만 Range로 받는다.
//        스크롤 / ↑↓ / Space / PageUp·PageDown: 넘겨 보기   + -: 크게·작게   Esc / 뒤로 가기: 닫기
//   EPUB: epub.js로 한 쪽씩 (파일은 처음에 한 번 받는다). ← → / 화면 양쪽 누르기: 앞·뒤 쪽   목차, 글자 크기, 밝은·어두운 바탕
// 보던 위치는 서버에 저장한다: PDF는 쪽(+그 쪽에서 내려온 비율)과 쪽 수, EPUB은 천분율(0~1000).
// EPUB은 정확한 자리(CFI)를 이 브라우저에도 적어 두어 같은 기기에서는 글자 하나 어긋나지 않게 이어 본다.
// source: { id, url, format: 'pdf'|'epub', title, sub, position, save(position, total, done), onClose }
import { h, icon, toast, storeGet, storeSet } from './util.js';

// 책을 열 때만 불러온다 (PDF.js 약 1MB, epub.js + JSZip 약 300KB)
const PDFJS_URL = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.4.168/build/pdf.min.mjs';
const PDFJS_WORKER_URL = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.4.168/build/pdf.worker.min.mjs';
const JSZIP_URL = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
const EPUBJS_URL = 'https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js';

const SAVE_EVERY_MS = 4000;
const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3];
const ZOOM_KEY = 'yp_doc_zoom';
const FONT_KEY = 'yp_epub_font';
const THEME_KEY = 'yp_epub_theme';
const CFI_KEY = 'yp_epub_cfi';
const THEMES = {
    dark: { body: { background: '#0b0b0c !important', color: '#dcdcdc !important' }, a: { color: '#e5a00d !important' } },
    sepia: { body: { background: '#f4ecd8 !important', color: '#3b2f22 !important' }, a: { color: '#8a5a00 !important' } },
    light: { body: { background: '#ffffff !important', color: '#1c1c1c !important' }, a: { color: '#0b57d0 !important' } },
};
const THEME_LABEL = { dark: '어두운 바탕', sepia: '누런 종이', light: '밝은 바탕' };

let pdfjsLoading = null;
function loadPdfjs() {
    if (!pdfjsLoading) {
        pdfjsLoading = import(PDFJS_URL).then((m) => {
            m.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
            return m;
        }).catch((e) => {
            pdfjsLoading = null;
            throw new Error(`PDF 보기 도구를 불러오지 못했습니다 (인터넷 연결 확인): ${e.message}`);
        });
    }
    return pdfjsLoading;
}

const scripts = new Map();
function loadScript(url, ready) {
    if (ready()) return Promise.resolve();
    if (!scripts.has(url)) {
        scripts.set(url, new Promise((resolve, reject) => {
            const el = document.createElement('script');
            el.src = url;
            el.onload = () => (ready() ? resolve() : reject(new Error(url)));
            el.onerror = () => { scripts.delete(url); el.remove(); reject(new Error('책 보기 도구를 불러오지 못했습니다 (인터넷 연결 확인)')); };
            document.head.appendChild(el);
        }));
    }
    return scripts.get(url);
}

async function loadEpubjs() {
    await loadScript(JSZIP_URL, () => !!window.JSZip);
    await loadScript(EPUBJS_URL, () => !!window.ePub);
    return window.ePub;
}

let current = null;

export function openDocReader(source) {
    if (current) current.close(true);
    current = source.format === 'epub' ? new EpubReader(source) : new PdfReader(source);
    current.open();
    return current;
}

class BaseReader {
    constructor(source, cls) {
        this.source = source;
        this.alive = true;
        this.saved = { at: 0, pos: -1 };
        this.done = false;

        this.titleEl = h('b', { class: 'viewer-title', text: source.title || '' });
        this.subEl = h('span', { class: 'viewer-sub', text: source.sub || '' });
        this.counter = h('span', { class: 'viewer-count' });
        this.tools = h('div', { class: 'doc-tools' });
        this.bar = h('div', { class: 'reader-bar' },
            h('button', { class: 'icon-btn', title: '닫기 (Esc)', on: { click: () => this.close() } }, icon('xmark')),
            h('div', { class: 'viewer-titles' }, this.titleEl, this.subEl),
            this.counter, this.tools);
        this.progressEl = h('div', { class: 'reader-progress' }, h('div'));
        this.statusEl = h('div', { class: 'reader-status hidden' });
        this.body = h('div', { class: 'doc-body' });
        this.root = h('div', { class: `reader doc-reader ${cls}`, role: 'dialog', 'aria-label': '책 보기' },
            this.body, this.bar, this.progressEl, this.statusEl);
        document.body.appendChild(this.root);
        document.body.classList.add('viewer-open');

        this._onKey = (e) => this.onKey(e);
        document.addEventListener('keydown', this._onKey, true);
        history.pushState({ ypReader: true }, '');
        this._onPop = () => this.close(true);
        window.addEventListener('popstate', this._onPop);
        this._onHide = () => { if (document.visibilityState === 'hidden') this.save(true); };
        document.addEventListener('visibilitychange', this._onHide);
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

    tool(name, title, fn) {
        const b = h('button', { class: 'icon-btn', title, on: { click: (e) => { e.stopPropagation(); fn(); } } }, icon(name));
        this.tools.appendChild(b);
        return b;
    }

    setProgress(frac) {
        this.progressEl.firstChild.style.width = `${Math.max(0, Math.min(1, frac)) * 100}%`;
    }

    /** {pos, total} 지금 위치 (없으면 null) */
    where() { return null; }

    save(force) {
        const w = this.where();
        if (!w || !w.total) return;
        const pos = this.done ? w.total : w.pos;
        if (pos === this.saved.pos) return;
        if (!force && (Date.now() - this.saved.at < SAVE_EVERY_MS || Math.abs(pos - this.saved.pos) < w.total * 0.002)) return;
        this.saved = { at: Date.now(), pos };
        Promise.resolve(this.source.save(pos, w.total, this.done)).catch(() => {});
    }

    markDone() {
        if (this.done) return;
        this.done = true;
        this.save(true);
    }

    onKey(e) {
        if (e.target.closest && e.target.closest('input, textarea, select')) return;
        const fn = this.keys(e)[e.key];
        if (!fn) return;
        e.preventDefault();
        e.stopPropagation();
        fn();
    }

    close(fromHistory = false) {
        if (!this.root.isConnected) return;
        this.save(true);
        this.alive = false;
        this.destroy();
        document.removeEventListener('keydown', this._onKey, true);
        document.removeEventListener('visibilitychange', this._onHide);
        window.removeEventListener('popstate', this._onPop);
        this.root.remove();
        document.body.classList.remove('viewer-open');
        if (!fromHistory && history.state && history.state.ypReader) history.back();
        if (current === this) current = null;
        if (this.source.onClose) this.source.onClose();
    }

    destroy() {}
}

// ---------------------------------------------------------------------------
// PDF
// ---------------------------------------------------------------------------
class PdfReader extends BaseReader {
    constructor(source) {
        super(source, 'doc-pdf');
        this.zoom = ZOOMS.includes(Number(storeGet(ZOOM_KEY, 1))) ? Number(storeGet(ZOOM_KEY, 1)) : 1;
        this.pagesEl = h('div', { class: 'doc-pages' });
        this.scroller = h('div', { class: 'reader-scroll', tabindex: '-1' }, this.pagesEl);
        this.body.appendChild(this.scroller);
        this.tool('magnifying-glass-minus', '작게 (-)', () => this.setZoom(-1));
        this.zoomEl = h('button', { class: 'doc-zoom', title: '폭에 맞추기', on: { click: (e) => { e.stopPropagation(); this.setZoom(0); } } });
        this.tools.appendChild(this.zoomEl);
        this.tool('magnifying-glass-plus', '크게 (+)', () => this.setZoom(1));
        this.counter.title = '눌러서 쪽 번호로 가기';
        this.counter.addEventListener('click', () => this.askPage());
        this._lastTop = 0;
        this._raf = 0;
        this.scroller.addEventListener('scroll', () => {
            if (this._raf) return;
            this._raf = requestAnimationFrame(() => { this._raf = 0; this.onScroll(); });
        }, { passive: true });
        this.scroller.addEventListener('click', (e) => {
            if (e.target.closest('button, a, input')) return;
            this.root.classList.toggle('bars-hidden');
        });
        this._near = new Set();
        this._io = new IntersectionObserver((entries) => {
            for (const en of entries) {
                const el = en.target;
                if (en.isIntersecting) { this._near.add(el); this.renderPage(el); } else { this._near.delete(el); this.releasePage(el); }
            }
        }, { root: this.scroller, rootMargin: '150% 0px 250% 0px' });
        this._onResize = () => { clearTimeout(this._rt); this._rt = setTimeout(() => this.relayout(), 200); };
        window.addEventListener('resize', this._onResize);
        this._syncZoom();
    }

    async open() {
        this.status('PDF 보기 도구를 불러오는 중…');
        try {
            const pdfjs = await loadPdfjs();
            if (!this.alive) return;
            this.status('텔레그램에서 받는 중…');
            // 보는 쪽에 필요한 부분만 받는다 (뒤에서 몰래 통째로 받지 않음) - 큰 책도 바로 열린다
            this.task = pdfjs.getDocument({ url: this.source.url, rangeChunkSize: 512 * 1024, disableAutoFetch: true, disableStream: true });
            this.task.onProgress = ({ loaded, total }) => {
                if (this.alive && !this.pdf && total) this.status(`텔레그램에서 받는 중… ${Math.round((loaded / total) * 100)}%`);
            };
            this.pdf = await this.task.promise;
        } catch (e) {
            if (this.alive) this.status(`열지 못했습니다: ${(e && e.message) || e}`, true);
            return;
        }
        if (!this.alive) return;
        const first = await this.pdf.getPage(1);
        const vp = first.getViewport({ scale: 1 });
        this.ratio = vp.height / vp.width;
        this.status('');
        this.subEl.textContent = [this.source.sub, `${this.pdf.numPages}쪽`].filter(Boolean).join(' · ');
        this.pageEls = [];
        for (let i = 0; i < this.pdf.numPages; i++) {
            const el = h('div', { class: 'doc-page', dataset: { n: String(i) } },
                h('div', { class: 'reader-wait' }, h('span', { text: `${i + 1}쪽` })));
            el._ratio = this.ratio;
            this.pagesEl.appendChild(el);
            this.pageEls.push(el);
        }
        this.relayout();
        const start = Math.min(Math.max(0, Number(this.source.position) || 0), this.pdf.numPages - 0.001);
        if (start > 0) this.goTo(start);
        this._lastTop = this.scroller.scrollTop;   // 이어 보는 자리로 옮긴 것은 "아래로 넘김"이 아니다 (막대를 숨기지 않게)
        this.saved = { at: Date.now(), pos: start };
        for (const el of this.pageEls) this._io.observe(el);
        this.onScroll();
        this.scroller.focus({ preventScroll: true });
    }

    /** 쪽 폭: 화면 폭(최대 900px) x 확대 비율 */
    pageWidth() {
        return Math.round(Math.min(this.scroller.clientWidth, 900) * this.zoom);
    }

    relayout() {
        if (!this.pageEls) return;
        const pos = this.position();
        const w = this.pageWidth();
        this.pagesEl.style.width = `${w}px`;
        for (const el of this.pageEls) {
            el.style.height = `${Math.round(w * el._ratio)}px`;
            if (el._rendered && el._rendered !== w) { el._rendered = 0; if (this._near.has(el)) this.renderPage(el); }
        }
        this.goTo(pos);
    }

    async renderPage(el) {
        const w = this.pageWidth();
        if (el._rendered === w || el._busy) return;
        el._busy = true;
        const n = Number(el.dataset.n);
        try {
            const page = await this.pdf.getPage(n + 1);
            if (!this.alive) return;
            const base = page.getViewport({ scale: 1 });
            const ratio = base.height / base.width;
            if (Math.abs(ratio - el._ratio) > 0.001) {   // 쪽마다 크기가 다를 수 있다 (펼친 면, 가로 쪽)
                const before = el.offsetHeight, above = el.offsetTop + before <= this.scroller.scrollTop + 1;
                el._ratio = ratio;
                el.style.height = `${Math.round(w * ratio)}px`;
                if (above) this.scroller.scrollTop += el.offsetHeight - before;
            }
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            const vp = page.getViewport({ scale: (w / base.width) * dpr });
            const canvas = h('canvas', { class: 'doc-canvas' });
            canvas.width = Math.floor(vp.width);
            canvas.height = Math.floor(vp.height);
            if (el._task) el._task.cancel();
            el._task = page.render({ canvasContext: canvas.getContext('2d'), viewport: vp });
            await el._task.promise;
            el._task = null;
            if (!this.alive || !this._near.has(el)) { canvas.width = 0; return; }
            el.replaceChildren(canvas);
            el.classList.add('ready');
            el._rendered = w;
        } catch (e) {
            if (e && e.name === 'RenderingCancelledException') return;
            el.classList.add('failed');
            el.replaceChildren(h('button', { class: 'btn', on: { click: (ev) => {
                ev.stopPropagation();
                el.classList.remove('failed');
                el._rendered = 0;
                this.renderPage(el);
            } } }, icon('rotate-right'), ` ${n + 1}쪽 다시 불러오기`));
        } finally {
            el._busy = false;
        }
    }

    /** 멀리 지나간 쪽의 그림은 버린다 (수백 쪽짜리 책에서 메모리가 넘치지 않게) */
    releasePage(el) {
        if (el._task) { el._task.cancel(); el._task = null; }
        if (!el._rendered) return;
        const c = el.querySelector('canvas');
        if (c) c.width = 0;
        el._rendered = 0;
        el.classList.remove('ready');
        el.replaceChildren(h('div', { class: 'reader-wait' }, h('span', { text: `${Number(el.dataset.n) + 1}쪽` })));
    }

    position() {
        const els = this.pageEls || [];
        if (!els.length) return 0;
        const top = this.scroller.scrollTop + els[0].offsetTop;   // 맨 위 여백(막대 자리)만큼은 첫 쪽 앞
        let lo = 0, hi = els.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (els[mid].offsetTop <= top) lo = mid; else hi = mid - 1;
        }
        const el = els[lo];
        return lo + Math.min(0.999, Math.max(0, (top - el.offsetTop) / Math.max(1, el.offsetHeight)));
    }

    goTo(pos) {
        const els = this.pageEls || [];
        if (!els.length) return;
        const i = Math.min(els.length - 1, Math.max(0, Math.floor(pos)));
        this.scroller.scrollTop = els[i].offsetTop - els[0].offsetTop + els[i].offsetHeight * (pos - i);
    }

    where() {
        return this.pdf && this.pageEls ? { pos: this.position(), total: this.pdf.numPages } : null;
    }

    onScroll() {
        if (!this.pageEls) return;
        const top = this.scroller.scrollTop;
        if (top > this._lastTop + 30) this.root.classList.add('bars-hidden');
        else if (top < this._lastTop - 30 || top < 10) this.root.classList.remove('bars-hidden');
        if (Math.abs(top - this._lastTop) > 30 || top < 10) this._lastTop = top;
        const n = this.pdf.numPages;
        this.counter.textContent = `${Math.min(n, Math.floor(this.position()) + 1)} / ${n}`;
        const max = this.scroller.scrollHeight - this.scroller.clientHeight;
        this.setProgress(max > 0 ? top / max : 1);
        if (max - top < 4) this.markDone();   // 마지막 쪽 끝까지 내리면 다 본 것
        this.save(false);
    }

    _syncZoom() {
        this.zoomEl.textContent = `${Math.round(this.zoom * 100)}%`;
    }

    setZoom(dir) {
        const i = ZOOMS.indexOf(this.zoom);
        const z = dir === 0 ? 1 : ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, i + dir))];
        if (z === this.zoom) return;
        this.zoom = z;
        storeSet(ZOOM_KEY, z);
        this._syncZoom();
        this.relayout();
    }

    askPage() {
        if (!this.pdf) return;
        const n = this.pdf.numPages;
        const v = Number(window.prompt(`몇 쪽으로 갈까요? (1~${n})`, String(Math.floor(this.position()) + 1)));
        if (v >= 1 && v <= n) this.goTo(v - 1);
    }

    keys(e) {
        const page = () => this.scroller.clientHeight * 0.9;
        return {
            Escape: () => this.close(),
            ArrowDown: () => this.scroller.scrollBy({ top: 120 }),
            ArrowUp: () => this.scroller.scrollBy({ top: -120 }),
            ArrowRight: () => this.goTo(Math.floor(this.position()) + 1),
            ArrowLeft: () => this.goTo(Math.max(0, Math.ceil(this.position()) - 1)),
            ' ': () => this.scroller.scrollBy({ top: e.shiftKey ? -page() : page(), behavior: 'smooth' }),
            PageDown: () => this.scroller.scrollBy({ top: page(), behavior: 'smooth' }),
            PageUp: () => this.scroller.scrollBy({ top: -page(), behavior: 'smooth' }),
            Home: () => { this.scroller.scrollTop = 0; },
            End: () => { this.scroller.scrollTop = this.scroller.scrollHeight; },
            '+': () => this.setZoom(1),
            '=': () => this.setZoom(1),
            '-': () => this.setZoom(-1),
            0: () => this.setZoom(0),
        };
    }

    destroy() {
        this._io.disconnect();
        window.removeEventListener('resize', this._onResize);
        for (const el of this.pageEls || []) if (el._task) el._task.cancel();
        if (this.task) this.task.destroy().catch(() => {});
    }
}

// ---------------------------------------------------------------------------
// EPUB
// ---------------------------------------------------------------------------
class EpubReader extends BaseReader {
    constructor(source) {
        super(source, 'doc-epub');
        this.theme = THEMES[storeGet(THEME_KEY, 'dark')] ? storeGet(THEME_KEY, 'dark') : 'dark';
        this.font = Math.min(200, Math.max(70, Number(storeGet(FONT_KEY, 100)) || 100));
        this.view = h('div', { class: 'epub-view' });
        // 화면 양쪽을 누르면 앞·뒤 쪽 (글 속 iframe은 누른 자리를 알기 어려워 그 위에 얇은 판을 깐다)
        const zone = (side, fn) => h('button', { class: `epub-zone epub-zone-${side}`, 'aria-label': side === 'l' ? '앞 쪽' : '다음 쪽',
            on: { click: (e) => { e.stopPropagation(); fn(); } } });
        this.body.append(this.view, zone('l', () => this.turn(-1)), zone('r', () => this.turn(1)));
        this.root.classList.add(`epub-theme-${this.theme}`);
        this.tocBtn = this.tool('list', '목차', () => this.toggleToc());
        this.tool('font', '글자 작게', () => this.setFont(-10));
        this.fontEl = h('span', { class: 'doc-zoom' });
        this.tools.appendChild(this.fontEl);
        this.tool('font', '글자 크게', () => this.setFont(10)).classList.add('big-font');
        this.tool('circle-half-stroke', '바탕색 바꾸기', () => this.cycleTheme());
        this.tocEl = h('div', { class: 'sub-menu epub-toc hidden', on: { click: (e) => e.stopPropagation() } });
        this.root.appendChild(this.tocEl);
        this.fontEl.textContent = `${this.font}%`;
        this.cfiKey = `${CFI_KEY}:${source.id}`;
    }

    async open() {
        this.status('책 보기 도구를 불러오는 중…');
        try {
            const [ePub, buf] = await Promise.all([loadEpubjs(), this.download()]);
            if (!this.alive) return;
            this.status('책을 여는 중…');
            this.book = ePub(buf);
            await this.book.ready;
        } catch (e) {
            if (this.alive) this.status(`열지 못했습니다: ${(e && e.message) || e}`, true);
            return;
        }
        if (!this.alive) return;
        // 책 속 스크립트는 돌리지 않는다 (epub.js 기본값이지만 분명히)
        this.rendition = this.book.renderTo(this.view, { width: '100%', height: '100%', flow: 'paginated', spread: 'auto', allowScriptedContent: false });
        for (const [name, css] of Object.entries(THEMES)) this.rendition.themes.register(name, css);
        this.rendition.themes.select(this.theme);
        this.rendition.themes.fontSize(`${this.font}%`);
        this.rendition.on('relocated', (loc) => this.onRelocated(loc));
        this.rendition.on('keydown', (e) => this.onKey(e));
        this.rendition.on('click', () => { this.tocEl.classList.add('hidden'); this.root.classList.toggle('bars-hidden'); });
        this.book.loaded.navigation.then((nav) => { this.toc = nav.toc || []; }).catch(() => {});
        const cfi = storeGet(this.cfiKey, '');
        const permille = Number(this.source.position) || 0;
        try {
            await this.rendition.display(cfi || undefined);
        } catch (e) {
            await this.rendition.display();
        }
        this.status('');
        this.saved = { at: Date.now(), pos: permille };
        // 쪽 위치(%)를 알려면 책 전체를 한 번 훑어야 한다 - 보여 준 다음 뒤에서
        this.book.locations.generate(1200).then(async () => {
            if (!this.alive) return;
            this.located = true;
            if (!cfi && permille > 0) await this.rendition.display(this.book.locations.cfiFromPercentage(permille / 1000));
            else if (this.rendition.location) this.onRelocated(this.rendition.location);
        }).catch(() => {});
    }

    /** 파일 전체를 받는다 (EPUB은 압축 파일이라 끝의 목차부터 읽어야 해서 쪽만 골라 받기 어렵다) */
    async download() {
        const r = await fetch(this.source.url, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const total = Number(r.headers.get('Content-Length')) || 0;
        if (!r.body || !total) return r.arrayBuffer();
        const reader = r.body.getReader();
        const out = new Uint8Array(total);
        let got = 0;
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (!this.alive) { reader.cancel().catch(() => {}); throw new Error('닫힘'); }
            out.set(value.subarray(0, Math.max(0, total - got)), got);
            got += value.length;
            this.status(`텔레그램에서 받는 중… ${Math.min(100, Math.round((got / total) * 100))}%`);
        }
        return out.buffer;
    }

    onRelocated(loc) {
        if (!loc || !loc.start) return;
        this.loc = loc;
        storeSet(this.cfiKey, loc.start.cfi);
        const pct = this.located ? this.book.locations.percentageFromCfi(loc.start.cfi) : null;
        if (pct != null) {
            this.counter.textContent = `${Math.round(pct * 100)}%`;
            this.setProgress(pct);
        } else {
            const d = loc.start.displayed || {};
            this.counter.textContent = d.total ? `${d.page} / ${d.total}` : '';
        }
        if (loc.atEnd) this.markDone();
        this.save(false);
    }

    where() {
        if (!this.loc || !this.located) return null;
        return { pos: Math.round(this.book.locations.percentageFromCfi(this.loc.start.cfi) * 1000), total: 1000 };
    }

    turn(dir) {
        if (!this.rendition) return;
        this.tocEl.classList.add('hidden');
        if (dir < 0) this.rendition.prev(); else this.rendition.next();
    }

    setFont(d) {
        this.font = Math.min(200, Math.max(70, this.font + d));
        storeSet(FONT_KEY, this.font);
        this.fontEl.textContent = `${this.font}%`;
        if (this.rendition) this.rendition.themes.fontSize(`${this.font}%`);
    }

    cycleTheme() {
        const names = Object.keys(THEMES);
        this.root.classList.remove(`epub-theme-${this.theme}`);
        this.theme = names[(names.indexOf(this.theme) + 1) % names.length];
        this.root.classList.add(`epub-theme-${this.theme}`);
        storeSet(THEME_KEY, this.theme);
        if (this.rendition) this.rendition.themes.select(this.theme);
        toast(THEME_LABEL[this.theme]);
    }

    toggleToc() {
        const open = this.tocEl.classList.toggle('hidden') === false;
        if (!open) return;
        this.tocEl.replaceChildren(h('div', { class: 'sub-menu-head', text: '목차' }));
        const list = h('div', { class: 'sub-list' });
        const add = (items, depth) => {
            for (const it of items || []) {
                list.appendChild(h('button', { class: 'sub-item', style: { paddingLeft: `${0.6 + depth}rem` },
                    on: { click: () => { this.tocEl.classList.add('hidden'); this.rendition.display(it.href).catch(() => toast('그 자리로 가지 못했습니다.', 'error')); } } },
                h('span', { class: 'sub-label', text: (it.label || '').trim() || '(제목 없음)' })));
                add(it.subitems, depth + 1);
            }
        };
        add(this.toc, 0);
        if (!list.childElementCount) list.appendChild(h('p', { class: 'muted', text: '목차가 없는 책입니다.' }));
        this.tocEl.appendChild(list);
    }

    keys() {
        return {
            Escape: () => (this.tocEl.classList.contains('hidden') ? this.close() : this.tocEl.classList.add('hidden')),
            ArrowRight: () => this.turn(1),
            ArrowLeft: () => this.turn(-1),
            ArrowDown: () => this.turn(1),
            ArrowUp: () => this.turn(-1),
            PageDown: () => this.turn(1),
            PageUp: () => this.turn(-1),
            ' ': () => this.turn(1),
            '+': () => this.setFont(10),
            '=': () => this.setFont(10),
            '-': () => this.setFont(-10),
        };
    }

    destroy() {
        try { if (this.book) this.book.destroy(); } catch (e) { /* 이미 정리됨 */ }
    }
}
