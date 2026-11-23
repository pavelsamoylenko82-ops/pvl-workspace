import { animate, inView } from 'motion';

const EASE = [0.22, 1, 0.36, 1];

export function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Плавное появление секций и карточек: 400-600 мс, ease-out, один раз на элемент. */
function initReveal() {
  inView(
    '[data-reveal]',
    (element) => {
      const delay = Number(element.dataset.delay || 0) / 1000;
      animate(
        element,
        { opacity: [0, 1], y: [24, 0] },
        { duration: 0.55, delay, ease: EASE },
      );
    },
    { amount: 0.12, margin: '0px 0px -6% 0px' },
  );
}

/**
 * Фирменный приём: главный кадр оформлен как оконная створка.
 * При скролле вниз она приоткрывается вокруг левой рамы.
 * Только transform, без пересчёта раскладки, поэтому без лагов на телефоне.
 */
function initWindowSash() {
  const windowEl = document.querySelector('[data-window]');
  const sash = document.querySelector('[data-sash]');
  if (!windowEl || !sash) return;

  const MAX_ANGLE = -88;
  let rafId = 0;

  const update = () => {
    rafId = 0;
    const distance = Math.max(380, Math.min(760, window.innerHeight * 0.72));
    const progress = Math.min(1, Math.max(0, window.scrollY / distance));
    const eased = 1 - Math.pow(1 - progress, 3);
    sash.style.transform = `rotateY(${(MAX_ANGLE * eased).toFixed(2)}deg)`;
  };

  const request = () => {
    if (!rafId) rafId = requestAnimationFrame(update);
  };

  update();
  window.addEventListener('scroll', request, { passive: true });
  window.addEventListener('resize', request);
}

export function initMotion() {
  if (prefersReducedMotion()) {
    document.documentElement.classList.remove('js-motion');
    document.querySelectorAll('[data-reveal]').forEach((element) => {
      element.style.opacity = '1';
      element.style.transform = 'none';
    });
    document.querySelectorAll('[data-sash]').forEach((element) => {
      element.style.transform = 'rotateY(-88deg)';
    });
    return;
  }

  initReveal();
  initWindowSash();
}
