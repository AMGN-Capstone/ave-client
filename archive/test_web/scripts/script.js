document.addEventListener('DOMContentLoaded', () => {
    const profileImg = document.querySelector('header img[alt="사용자"]');
    if (profileImg) {
        const profileLink = profileImg.closest('a');
        const nav = document.querySelector('nav');

        if (profileLink && nav) {
            profileLink.style.cursor = 'pointer';
            profileLink.addEventListener('click', (e) => {
                if (!profileLink.getAttribute('href') || profileLink.getAttribute('href') === '#') {
                    e.preventDefault();
                }
                nav.classList.toggle('show');
            });
        }
    }

    const asideToggleBtn = document.getElementById('aside-toggle-btn');
    const aside = document.querySelector('aside');

    if (asideToggleBtn && aside) {
        asideToggleBtn.addEventListener('click', () => {
            aside.classList.toggle('show');
        });
    }
});