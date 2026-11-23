import { company } from '../data/company.js';
import {
  arrowIcon,
  burgerIcon,
  closeIcon,
  infoIcon,
  logoMark,
  mapIcon,
  phoneIcon,
  quoteIcon,
  starIcon,
} from './icons.js';

const esc = (value) =>
  String(value).replace(/[&<>"']/g, (char) => {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return map[char];
  });

const marks = `
  <span class="marks" aria-hidden="true">
    <span class="mark mark-tl"></span>
    <span class="mark mark-tr"></span>
    <span class="mark mark-bl"></span>
    <span class="mark mark-br"></span>
  </span>`;

export const header = () => `
<header class="site-header" id="site-header">
  <div class="container">
    <div class="flex h-16 items-center justify-between gap-3">
      <a href="#top" class="flex min-h-[44px] items-center gap-2.5" aria-label="${esc(company.brand)}: к началу страницы">
        ${logoMark(26)}
        <span class="flex flex-col leading-none">
          <span class="font-display text-[16.5px] font-extrabold tracking-[-0.025em] sm:text-[17.5px]" translate="no">${esc(company.brand)}</span>
          <span class="mt-1 hidden text-[12px] text-ink-muted min-[350px]:block">${esc(company.tagline)}</span>
        </span>
      </a>

      <nav class="hidden items-center gap-6 lg:flex" aria-label="Основная навигация">
        ${company.nav
          .map((item) => `<a class="nav-link" href="${item.href}">${esc(item.label)}</a>`)
          .join('')}
      </nav>

      <div class="flex items-center gap-2.5">
        <a class="header-phone hidden xl:inline-flex" href="tel:${company.primaryPhoneTel}">${esc(company.primaryPhone)}</a>
        <a class="btn btn-primary hidden !min-h-[44px] !px-4 sm:inline-flex" href="#quiz">${esc(company.cta.calculate)}</a>
        <button class="burger lg:hidden" id="menu-toggle" type="button" aria-expanded="false" aria-controls="mobile-menu" aria-label="Открыть меню">
          <span class="burger-icon-open">${burgerIcon(20)}</span>
          <span class="burger-icon-close">${closeIcon(20)}</span>
        </button>
      </div>
    </div>
  </div>

  <div class="mobile-menu lg:hidden" id="mobile-menu">
    <nav aria-label="Мобильная навигация">
      ${company.nav.map((item) => `<a href="${item.href}">${esc(item.label)}</a>`).join('')}
    </nav>
    <div class="mt-3 flex flex-col gap-2">
      <a class="btn btn-primary w-full" href="#quiz">${esc(company.cta.primary)}</a>
      <a class="btn btn-ghost w-full" href="tel:${company.primaryPhoneTel}">${phoneIcon(17)} ${esc(company.primaryPhone)}</a>
    </div>
  </div>
</header>`;

export const hero = () => `
<section id="top" class="pt-5 pb-10 sm:pt-8 lg:pt-14 lg:pb-16">
  <div class="container">
    <div class="grid items-center gap-7 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,0.98fr)] lg:gap-12">
      <div class="lg:row-start-1 lg:col-start-1">
        <p class="eyebrow"><span class="whitespace-nowrap">${esc(company.hero.eyebrow)} ·</span> <span class="whitespace-nowrap">${esc(company.hero.eyebrowTail)}</span></p>
        <h1 class="mt-3.5 max-w-[21ch] text-[clamp(1.9rem,5vw,3.45rem)] font-extrabold leading-[1.03] tracking-[-0.025em]">${esc(company.hero.title)}</h1>
        <div class="mt-4 max-w-[56ch] space-y-2.5 text-[16.5px] leading-[1.6] text-ink-muted sm:text-[17px]">
          ${company.hero.lines.map((line) => `<p>${esc(line)}</p>`).join('')}
        </div>
        <div class="mt-6 flex flex-col gap-2.5 sm:flex-row sm:gap-3">
          <a class="btn btn-primary" href="#quiz">${esc(company.cta.primary)}</a>
          <a class="btn btn-ghost" href="tel:${company.primaryPhoneTel}">${phoneIcon(17)} ${esc(company.cta.call)}</a>
        </div>
      </div>

      <figure class="lg:col-start-2 lg:row-span-2 lg:row-start-1">
        <div class="frame">
          ${marks}
          <div class="frame-img aspect-[4/3]">
            <img
              src="${company.hero.image.src}"
              width="${company.hero.image.width}"
              height="${company.hero.image.height}"
              alt="${esc(company.hero.image.alt)}"
              loading="eager"
              fetchpriority="high"
              decoding="async"
              class="object-cover object-center"
            />
          </div>
        </div>
        <figcaption class="figure-caption">
          <span>${esc(company.hero.image.caption)}</span>
        </figcaption>
      </figure>

      <div class="grid grid-cols-2 border-y border-line sm:grid-cols-4 sm:divide-x sm:divide-line lg:col-start-1 lg:row-start-2">
        ${company.hero.facts
          .map(
            (fact) => `
          <div class="hero-fact">
            <div class="hero-fact-value">${esc(fact.value)}</div>
            <div class="hero-fact-label">${esc(fact.label)}</div>
          </div>`,
          )
          .join('')}
      </div>
    </div>
  </div>
</section>`;

export const production = () => `
<section id="production" class="on-dark bg-graphite text-white">
  <div class="container py-14 lg:py-20">
    <div class="grid gap-10 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] lg:gap-14">
      <div>
        <p class="eyebrow">Производство</p>
        <h2 class="section-title mt-3">${esc(company.production.title)}</h2>
        <figure class="mt-6">
          <div class="frame !border-white/10">
            <div class="frame-img">
              <img
                src="${company.production.image.src}"
                width="${company.production.image.width}"
                height="${company.production.image.height}"
                alt="${esc(company.production.image.alt)}"
                loading="lazy"
                decoding="async"
                class="!h-auto"
              />
            </div>
          </div>
          <figcaption class="figure-caption !border-white/10 !text-white/50">
            <span>${esc(company.production.image.caption)}</span>
            <span class="hidden shrink-0 sm:inline">Фото производства</span>
          </figcaption>
        </figure>
        <p class="mt-6 max-w-[54ch] text-[16.5px] leading-[1.62] text-white/70">${esc(company.production.lead)}</p>
        <p class="mt-5 text-[14px] leading-[1.55] text-white/50">На рынке Беларуси с ${company.since} года. ${esc(company.production.footnote)}</p>
      </div>

      <div class="lg:pt-1">
        <div class="flex items-baseline justify-between gap-4 border-b border-white/10 pb-3">
          <h3 class="text-[12.5px] uppercase tracking-[0.13em] text-white/60">${esc(company.production.specsTitle)}</h3>
          <span class="text-[12.5px] uppercase tracking-[0.13em] text-white/40">Своя линия</span>
        </div>
        <dl class="spec-list mt-4">
          ${company.production.specs
            .map(
              (spec) => `
            <div class="spec-row">
              <dt>${esc(spec.label)}</dt>
              <dd>${esc(spec.value)}</dd>
            </div>`,
            )
            .join('')}
        </dl>
        <p class="mt-5 max-w-[46ch] text-[14px] leading-[1.55] text-white/50">
          Замер и точный расчёт делает мастер производства, поэтому в смете нет наценки посредника и лишних позиций.
        </p>
      </div>
    </div>
  </div>
</section>`;

export const quizShell = () => `
<section id="quiz" class="py-14 lg:py-20">
  <div class="container">
    <div class="max-w-[720px]">
      <p class="eyebrow">Расчёт</p>
      <h2 class="section-title mt-3">${esc(company.quiz.title)}</h2>
      <p class="lead mt-4">${esc(company.quiz.lead)}</p>
    </div>

    <div class="quiz-shell mt-8 lg:mt-10">
      <div class="p-5 sm:p-7 lg:p-9">
        <div class="quiz-topline">
          <span class="step-counter" id="quiz-counter">Шаг <b>1</b> из ${company.quiz.steps.length}</span>
          <span class="hidden text-[12.5px] uppercase tracking-[0.12em] text-ink-muted sm:block">Расчёт по факту замера</span>
        </div>
        <div class="progress" id="quiz-progress" aria-hidden="true">
          ${company.quiz.steps.map((_, index) => `<span class="${index === 0 ? 'is-done' : ''}"></span>`).join('')}
        </div>
        <div id="quiz-panel" class="quiz-panel"></div>
      </div>
    </div>
  </div>
</section>`;

export const works = () => `
<section id="works" class="py-14 lg:py-20">
  <div class="container">
    <div class="flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
      <div>
        <p class="eyebrow">Работы</p>
        <h2 class="section-title mt-3">Выполненные работы</h2>
      </div>
      <p class="max-w-[40ch] text-[14.5px] leading-[1.5] text-ink-muted">
        Объекты из раздела «Наши работы»: окна, балконы, двери и монтаж. Нажмите на кадр, чтобы открыть его целиком.
      </p>
    </div>

    <div class="works-grid mt-7" id="works-grid">
      ${company.works
        .map(
          (work, index) => `
        <button class="work" type="button" data-area="${work.area}" data-index="${index}"
                aria-label="Открыть фото: ${esc(work.caption)}">
          <img src="${work.src}" width="${work.width}" height="${work.height}" alt="${esc(work.alt)}"
               loading="lazy" decoding="async" />
          <span class="work-zoom" aria-hidden="true">${arrowIcon(15)}</span>
          <span class="work-cap">
            <span>${esc(work.caption)}</span>
            <span class="work-tag">${esc(work.tag)}</span>
          </span>
        </button>`,
        )
        .join('')}
    </div>
  </div>
</section>`;

export const extras = () => `
<section id="extra" class="pb-14 lg:pb-20">
  <div class="container border-t border-line pt-10 lg:pt-12">
    <div class="grid gap-8 lg:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)] lg:gap-14">
      <div>
        <p class="eyebrow">Дополнительно</p>
        <h2 class="mt-3 text-[22px] font-bold leading-[1.15] tracking-[-0.02em] sm:text-[26px]">${esc(company.extras.title)}</h2>
        <p class="mt-3 max-w-[36ch] text-[15px] leading-[1.55] text-ink-muted">${esc(company.extras.lead)}</p>
        <a class="btn btn-ghost mt-5" href="tel:${company.primaryPhoneTel}">${phoneIcon(17)} ${esc(company.extras.cta)}</a>
      </div>

      <dl class="grid gap-x-10 sm:grid-cols-2">
        ${company.extras.items
          .map(
            (item) => `
          <div class="extra-row">
            <dt>${esc(item.title)}</dt>
            <dd>${esc(item.note)}</dd>
          </div>`,
          )
          .join('')}
      </dl>
    </div>
  </div>
</section>`;

export const process = () => `
<section id="process" class="border-y border-line bg-card">
  <div class="container py-14 lg:py-20">
    <div class="flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
      <div>
        <p class="eyebrow">Порядок работы</p>
        <h2 class="section-title mt-3">${esc(company.process.title)}</h2>
      </div>
      <p class="lead max-w-[44ch] text-[16px]">${esc(company.process.lead)}</p>
    </div>

    <div class="process-grid mt-9">
      ${company.process.steps
        .map(
          (step) => `
        <div class="process-step">
          <h3 class="process-name">${esc(step.title)}</h3>
          <p class="process-text">${esc(step.text)}</p>
        </div>`,
        )
        .join('')}
    </div>
  </div>
</section>`;

const reviewCard = (review) => `
<article class="review">
  <p class="review-text">${esc(review.text)}</p>
  <div class="review-meta">
    <span class="review-author">${esc(review.author)}</span>
    <span>${esc(review.date)}</span>
    <span class="review-stars" aria-label="Оценка ${review.rating} из 5">
      ${Array.from({ length: review.rating }, () => starIcon(12)).join('')}
    </span>
  </div>
</article>`;

export const reviews = () => {
  const featured = company.reviews.find((review) => review.featured) || company.reviews[0];
  const rest = company.reviews.filter((review) => review !== featured);
  return `
  <section id="reviews" class="border-b border-line bg-card">
    <div class="container py-14 lg:py-20">
      <div class="grid gap-9 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,0.88fr)] lg:gap-14">
        <div>
          <p class="eyebrow">Отзывы</p>
          <h2 class="section-title mt-3">Отзывы клиентов с Яндекс Карт</h2>
          <div class="mt-6 flex items-end gap-4">
            <span class="rating-value">${esc(company.rating.valueLabel)}</span>
            <div class="pb-1">
              <div class="review-stars" aria-hidden="true">
                ${Array.from({ length: 4 }, () => starIcon(13)).join('')}
                <span class="opacity-30">${starIcon(13)}</span>
              </div>
              <p class="mt-1.5 text-[13.5px] text-ink-muted">${company.rating.marks} оценок · ${company.rating.reviews} отзывов</p>
            </div>
          </div>
          <a class="link-arrow mt-4" href="${company.map.reviewsUrl}" target="_blank" rel="noopener">
            Смотреть карточку на Яндекс Картах ${arrowIcon(15)}
          </a>
          <p class="mt-3 max-w-[40ch] text-[13.5px] leading-[1.5] text-ink-muted">
            Тексты приведены дословно, с датой и именем автора. Рейтинг и число оценок - по данным карточки организации.
          </p>

          <div class="review-featured mt-7">
            <span class="block text-accent">${quoteIcon(22)}</span>
            <p class="review-text mt-3">${esc(featured.text)}</p>
            <div class="review-meta">
              <span class="review-author">${esc(featured.author)}</span>
              <span>${esc(featured.date)}</span>
              <span>Яндекс Карты, ${featured.rating} из 5</span>
            </div>
          </div>
        </div>

        <div class="lg:pt-1">
          ${rest.map(reviewCard).join('')}
        </div>
      </div>
    </div>
  </section>`;
};

export const contacts = () => `
<footer id="contacts" class="on-dark bg-graphite text-white">
  <div class="container py-14 lg:py-20">
    <div class="grid gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-14">
      <div>
        <p class="eyebrow">Контакты</p>
        <h2 class="section-title mt-3">Молодечно, производство и офис</h2>
        <p class="lead mt-4 max-w-[46ch]">${esc(company.intro)}</p>

        <div class="mt-7 grid gap-5 sm:grid-cols-2">
          ${company.addresses
            .map(
              (address) => `
            <div>
              <p class="text-[12.5px] uppercase tracking-[0.12em] text-white/50">${esc(address.title)}</p>
              <p class="mt-1.5 text-[17px] leading-[1.4] text-white">${esc(address.value)}</p>
              <p class="mt-1 text-[13px] text-white/40">${esc(address.note)}</p>
            </div>`,
            )
            .join('')}
        </div>

        <div class="mt-7 grid gap-6 sm:grid-cols-2">
          <div>
            <p class="text-[12.5px] uppercase tracking-[0.12em] text-white/50">Телефоны</p>
            <div class="mt-1 flex flex-col">
              ${company.phones
                .map(
                  (phone) =>
                    `<a class="contact-link" href="tel:${phone.tel}">${esc(phone.label)} <span class="ml-2 text-[12px] font-normal uppercase tracking-[0.1em] text-white/40">${esc(phone.note)}</span></a>`,
                )
                .join('')}
            </div>
          </div>
          <div>
            <p class="text-[12.5px] uppercase tracking-[0.12em] text-white/50">График</p>
            <p class="mt-2 font-display text-[19px] font-bold tracking-[-0.02em]">${esc(company.hours.days)} ${esc(company.hours.time)}</p>
            <p class="mt-1 text-[14px] text-white/50">${esc(company.hours.weekend)}</p>
            <p class="mt-2 max-w-[30ch] text-[13px] leading-[1.45] text-white/40">${esc(company.hours.note)}</p>
            <a class="footer-link mt-1" href="mailto:${esc(company.email)}">${esc(company.email)}</a>
          </div>
        </div>

        <div class="mt-7 flex flex-wrap gap-3">
          <a class="btn btn-primary" href="${company.map.url}" target="_blank" rel="noopener">${mapIcon(17)} ${esc(company.map.label)}</a>
          <a class="btn btn-outline-dark" href="tel:${company.primaryPhoneTel}">${phoneIcon(17)} ${esc(company.primaryPhone)}</a>
        </div>
      </div>

      <div>
        <div class="flex items-baseline justify-between gap-4 border-b border-white/10 pb-3">
          <h3 class="text-[12.5px] uppercase tracking-[0.13em] text-white/60">Реквизиты</h3>
          <span class="text-[12.5px] uppercase tracking-[0.13em] text-white/40">ЧТУП</span>
        </div>
        <dl class="mt-2">
          ${company.requisites
            .map(
              (item) => `
            <div class="footer-spec">
              <dt>${esc(item.label)}</dt>
              <dd>${esc(item.value)}</dd>
            </div>`,
            )
            .join('')}
        </dl>

        <div class="mt-6">
          <p class="text-[12.5px] uppercase tracking-[0.12em] text-white/50">Ссылки</p>
          <div class="mt-1 flex flex-wrap gap-x-6">
            ${company.socials
              .map(
                (social) =>
                  `<a class="footer-link" href="${social.url}" target="_blank" rel="noopener">${esc(social.label)}</a>`,
              )
              .join('')}
          </div>
        </div>

        <p class="mt-6 max-w-[40ch] text-[13px] leading-[1.5] text-white/40">
          Условия, комплектация и сроки согласуются после замера. Информация на странице не является публичной офертой.
        </p>
      </div>
    </div>

    <div class="mt-12 flex flex-col gap-3 border-t border-white/10 pt-6 text-[13px] text-white/40 sm:flex-row sm:items-center sm:justify-between">
      <p>
        <span translate="no">${esc(company.brand)}</span> · ${esc(company.legalName)} · УНП ${esc(company.requisites[1].value)}
      </p>
      <p>© 2026 ${esc(company.brand)}</p>
    </div>
  </div>
</footer>

<nav class="mobile-bar md:hidden" aria-label="Быстрые действия">
  <a class="is-primary" href="tel:${company.primaryPhoneTel}">Позвонить</a>
  <a href="${company.map.url}" target="_blank" rel="noopener">На карте</a>
  <a href="#quiz">Расчёт</a>
</nav>`;

export const lightbox = () => `
<div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="Просмотр фото" aria-hidden="true">
  <div class="lightbox-inner">
    <button class="lightbox-close" type="button" id="lightbox-close" aria-label="Закрыть просмотр">
      ${closeIcon(20)}
    </button>
    <img id="lightbox-img" alt="" width="1" height="1"
         src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" />
    <div class="lightbox-cap">
      <span id="lightbox-caption"></span>
      <span class="shrink-0 text-white/40" translate="no">СтеклоПак Торг</span>
    </div>
  </div>
</div>`;

export const skipLink = () => `<a class="skip-link" href="#quiz">Перейти к расчёту стоимости</a>`;
