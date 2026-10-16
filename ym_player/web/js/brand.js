// 화면 이름/아이콘. 처음 그릴 때는 서버가 HTML에 넣어 주고, 관리자가 설정에서 바꾸면 이걸로 바로 고친다.
export const DEFAULT_FAVICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='15' fill='%23e5a00d'/%3E%3Ccircle cx='27' cy='33' r='19' fill='%23141517'/%3E%3Ccircle cx='37' cy='26' r='15.5' fill='%23e5a00d'/%3E%3Cpath d='M33 19.5v15l12.5-7.5z' fill='%23141517'/%3E%3C/svg%3E";
// 기본 아이콘 (server.py LOGO_SVG와 같은 그림). 색은 CSS(.logo-bg/.logo-ink)가 강조색으로 칠한다
const LOGO_SVG = '<svg class="logo" viewBox="0 0 64 64" aria-hidden="true"><rect class="logo-bg" width="64" height="64" rx="15"/><circle class="logo-ink" cx="27" cy="33" r="19"/><circle class="logo-bg" cx="37" cy="26" r="15.5"/><path class="logo-ink" d="M33 19.5v15l12.5-7.5z"/></svg>';

export function applyBranding({ title, icon_url: icon, show_version: showVersion = true, version = '' }) {
    document.title = title;
    document.querySelectorAll('.brand-name').forEach((el) => { el.textContent = title; });
    const short = String(version || '');
    document.querySelectorAll('.brand-mark').forEach((el) => {
        el.classList.toggle('has-img', !!icon);
        el.replaceChildren();
        if (icon) {
            const img = document.createElement('img');
            img.src = icon;
            img.alt = '';
            el.appendChild(img);
        } else {
            el.insertAdjacentHTML('beforeend', LOGO_SVG);
        }
        if (showVersion && short) {
            const badge = document.createElement('span');
            badge.className = 'brand-ver';
            badge.textContent = short;
            el.appendChild(badge);
        }
    });
    const link = document.querySelector('link[rel="icon"]');
    if (link) link.href = icon || DEFAULT_FAVICON;
}
