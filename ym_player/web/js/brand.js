// 화면 이름/아이콘. 처음 그릴 때는 서버가 HTML에 넣어 주고, 관리자가 설정에서 바꾸면 이걸로 바로 고친다.
export const DEFAULT_FAVICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23e5a00d'/%3E%3Cpath d='M12 9l11 7-11 7z' fill='%23141517'/%3E%3C/svg%3E";

export function applyBranding({ title, icon_url: icon }) {
    document.title = title;
    document.querySelectorAll('.brand-name').forEach((el) => { el.textContent = title; });
    document.querySelectorAll('.brand-mark').forEach((el) => {
        el.classList.toggle('has-img', !!icon);
        el.replaceChildren();
        if (icon) {
            const img = document.createElement('img');
            img.src = icon;
            img.alt = '';
            el.appendChild(img);
        } else {
            const i = document.createElement('i');
            i.className = 'fa-solid fa-play';
            el.appendChild(i);
        }
    });
    const link = document.querySelector('link[rel="icon"]');
    if (link) link.href = icon || DEFAULT_FAVICON;
}
