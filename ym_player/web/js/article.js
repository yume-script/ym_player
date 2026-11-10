// 글 읽기 화면 (브런치 연재 글 / 만개의레시피 레시피). 전체 화면 위에 열리고 Esc·뒤로 가기·✕로 닫는다.
//   source.load(id) -> { title, meta, cover, html (서버가 허용한 태그만 남긴 HTML) 또는 nodes (직접 만든 요소들),
//                        url (원문), notice, prev, next ({id, title}), episodes: [{id, title, num, current}] }
//   앞·뒤 글은 같은 화면에서 이어서 연다 (← → 키도).
// [듣기]: 브라우저 음성(speechSynthesis)으로 문단을 차례로 읽고 읽는 문단을 강조한다. 속도는 이 브라우저에 기억.
import { h, icon, clear, storeGet, storeSet } from './util.js';

const RATE_KEY = 'yp_article_rate';
const RATES = [0.8, 1, 1.2, 1.4, 1.6, 2];
let current = null;

export function openArticle(source, id) {
    if (current) current.close(true);
    current = new ArticleView(source);
    current.show(id);
    return current;
}

class ArticleView {
    constructor(source) {
        this.source = source;
        this.alive = true;
        this.titleEl = h('b', { class: 'viewer-title', text: source.title || '' });
        this.subEl = h('span', { class: 'viewer-sub', text: source.sub || '' });
        this.listenBtn = h('button', { class: 'btn btn-sm article-listen', title: '소리 내어 읽기 (브라우저 음성)', on: { click: () => this.toggleListen() } },
            icon('volume-high'), ' 듣기');
        this.rateSel = h('select', { class: 'select select-sm article-rate', title: '읽는 속도' },
            RATES.map((r) => h('option', { value: String(r), text: `${r}x`, selected: r === Number(storeGet(RATE_KEY, 1)) })));
        this.rateSel.addEventListener('change', () => { storeSet(RATE_KEY, Number(this.rateSel.value)); if (this.speaking) this.speakFrom(this.spoken); });
        const canSpeak = 'speechSynthesis' in window;
        this.origin = h('a', { class: 'btn btn-sm', target: '_blank', rel: 'noopener noreferrer', title: '원문을 새 탭에서' }, icon('arrow-up-right-from-square'), ' 원문');
        this.bar = h('div', { class: 'reader-bar' },
            h('button', { class: 'icon-btn', title: '닫기 (Esc)', on: { click: () => this.close() } }, icon('xmark')),
            h('div', { class: 'viewer-titles' }, this.titleEl, this.subEl),
            h('div', { class: 'doc-tools' }, canSpeak ? this.listenBtn : null, canSpeak ? this.rateSel : null, this.origin));
        this.page = h('article', { class: 'article-page' });
        this.scroller = h('div', { class: 'article-scroll' }, this.page);
        this.root = h('div', { class: 'reader article-reader', role: 'dialog', 'aria-label': '글 읽기' }, this.scroller, this.bar);
        document.body.appendChild(this.root);
        document.body.classList.add('viewer-open');
        this._onKey = (e) => {
            if (e.key === 'Escape') { e.preventDefault(); this.close(); }
            else if (e.key === 'ArrowLeft' && this.prev && !e.target.closest('input, select')) this.show(this.prev.id);
            else if (e.key === 'ArrowRight' && this.next && !e.target.closest('input, select')) this.show(this.next.id);
        };
        document.addEventListener('keydown', this._onKey, true);
        history.pushState({ ypReader: true }, '');
        this._onPop = () => this.close(true);
        window.addEventListener('popstate', this._onPop);
    }

    async show(id) {
        this.stopListen();
        this.id = id;
        this.prev = this.next = null;
        clear(this.page).appendChild(h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text: '불러오는 중...' })));
        let d;
        try {
            d = await this.source.load(id);
        } catch (e) {
            if (this.alive && this.id === id) clear(this.page).appendChild(h('p', { class: 'muted error article-msg', text: `불러오지 못했습니다: ${e.message}` }));
            return;
        }
        if (!this.alive || this.id !== id) return;
        this.prev = d.prev || null;
        this.next = d.next || null;
        this.titleEl.textContent = d.title || this.source.title || '';
        this.subEl.textContent = d.sub || this.source.sub || '';
        this.origin.href = d.url || '#';
        this.origin.classList.toggle('hidden', !d.url);
        const body = h('div', { class: `article-body${d.align ? ` align-${d.align}` : ''}` });
        if (d.html) body.innerHTML = d.html;   // (서버가 허용한 태그·속성만 남긴 것)
        if (d.nodes) body.append(...d.nodes);
        const nav = (x, label, ic) => (x ? h('button', { class: 'btn article-nav-btn', title: x.title, on: { click: () => this.show(x.id) } },
            ic === 'left' ? icon('chevron-left') : null, h('span', { text: `${label}: ${x.title}` }), ic === 'right' ? icon('chevron-right') : null) : h('span'));
        clear(this.page).append(...[   // (Element.append는 null을 "null" 글자로 넣어서 빈 것은 뺀다)
            d.cover ? h('img', { class: 'article-cover', src: d.cover, alt: '', referrerpolicy: 'no-referrer', loading: 'lazy' }) : null,
            d.kicker ? h('div', { class: 'article-kicker', text: d.kicker }) : null,
            h('h1', { class: 'article-title', text: d.title || '' }),
            d.subtitle ? h('p', { class: 'article-subtitle', text: d.subtitle }) : null,
            d.meta ? h('p', { class: 'article-meta muted', text: d.meta }) : null,
            d.notice ? h('p', { class: 'article-notice' }, icon('lock'), ` ${d.notice} `,
                d.url ? h('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer', text: '원문에서 계속 읽기' }) : null) : null,
            body,
            d.source ? h('p', { class: 'article-source muted', text: d.source }) : null,
            (this.prev || this.next) ? h('div', { class: 'article-nav' }, nav(this.prev, '이전', 'left'), nav(this.next, '다음', 'right')) : null,
            d.episodes && d.episodes.length > 1 ? h('details', { class: 'article-episodes' },
                h('summary', { text: `회차 목록 (${d.episodes.length})` }),
                h('ol', {}, d.episodes.map((e) => h('li', { class: e.current ? 'current' : '' },
                    h('button', { class: 'linklike', on: { click: () => this.show(e.id) } }, `${e.num ? `${e.num} ` : ''}${e.title}`))))) : null,
        ].filter(Boolean));
        this.scroller.scrollTop = 0;
        // 읽을 문단 (강조·이어 읽기)
        this.paras = [...this.page.querySelectorAll('.article-title, .article-subtitle, .article-body p, .article-body li, .article-body h2, .article-body h3, .article-body blockquote, .article-body .speak')]
            .filter((el) => el.textContent.trim() && !el.querySelector('p, li'));
        this.paras.forEach((el, i) => el.addEventListener('click', (e) => { if (this.speaking && !e.target.closest('a')) this.speakFrom(i); }));
    }

    // ----- 듣기 (브라우저 음성) -----
    toggleListen() {
        if (this.speaking) this.stopListen();
        else this.speakFrom(0);
    }

    speakFrom(i) {
        const synth = window.speechSynthesis;
        synth.cancel();
        this.speaking = true;
        this.listenBtn.classList.add('on');
        this.listenBtn.lastChild.textContent = ' 멈춤';
        const token = (this._speakToken = (this._speakToken || 0) + 1);
        const voice = synth.getVoices().find((v) => /^ko/i.test(v.lang));
        const step = (n) => {
            if (token !== this._speakToken || !this.alive) return;
            this.paras.forEach((el) => el.classList.remove('speaking'));
            if (n >= this.paras.length) {
                this.stopListen();
                if (this.next && this.source.listenNext !== false) this.show(this.next.id).then(() => { if (this.paras && this.paras.length) this.speakFrom(0); });
                return;
            }
            this.spoken = n;
            const el = this.paras[n];
            el.classList.add('speaking');
            el.scrollIntoView({ block: 'center', behavior: 'smooth' });
            // 크롬은 긴 문장을 15초쯤에서 끊어서 문장 단위로 나눠 읽는다
            const parts = el.textContent.replace(/\s+/g, ' ').trim().match(/[^.!?。…]{1,180}[.!?。…]*/g) || [];
            const sayPart = (k) => {
                if (token !== this._speakToken) return;
                if (k >= parts.length) return step(n + 1);
                const u = new SpeechSynthesisUtterance(parts[k]);
                u.lang = 'ko-KR';
                if (voice) u.voice = voice;
                u.rate = Number(this.rateSel.value) || 1;
                u.onend = () => sayPart(k + 1);
                u.onerror = () => { if (token === this._speakToken) sayPart(k + 1); };
                synth.speak(u);
            };
            sayPart(0);
        };
        step(i);
    }

    stopListen() {
        this._speakToken = (this._speakToken || 0) + 1;
        this.speaking = false;
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
        if (this.listenBtn) {
            this.listenBtn.classList.remove('on');
            this.listenBtn.lastChild.textContent = ' 듣기';
        }
        (this.paras || []).forEach((el) => el.classList.remove('speaking'));
    }

    close(fromPop = false) {
        if (!this.alive) return;
        this.alive = false;
        this.stopListen();
        document.removeEventListener('keydown', this._onKey, true);
        window.removeEventListener('popstate', this._onPop);
        this.root.remove();
        document.body.classList.remove('viewer-open');
        if (!fromPop && history.state && history.state.ypReader) history.back();
        if (current === this) current = null;
        if (this.source.onClose) this.source.onClose();
    }
}
