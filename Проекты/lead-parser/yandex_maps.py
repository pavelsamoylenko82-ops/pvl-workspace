from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import quote_plus, unquote, urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from models import Lead

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CaptchaDetected(RuntimeError):
    """Яндекс показал капчу; парсинг нужно остановить без потери уже собранного."""


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class YandexMapsParser:
    def __init__(
        self,
        city: str,
        query: str,
        limit: int = 30,
        min_reviews: int = 0,
        headless: bool = True,
        timeout_ms: int = 45000,
    ) -> None:
        self.city = city.strip()
        self.query = query.strip()
        self.limit = max(1, limit)
        self.min_reviews = max(0, min_reviews)
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.leads: list[Lead] = []
        self.captcha_detected = False

    async def parse(self) -> tuple[list[Lead], bool]:
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
                context = await browser.new_context(
                    locale="ru-RU",
                    timezone_id="Europe/Minsk",
                    viewport={"width": 1440, "height": 960},
                    user_agent=USER_AGENT,
                    ignore_https_errors=True,
                )
                await context.add_init_script(STEALTH_JS)
                page = await context.new_page()
                try:
                    await self._load_search(page)
                    links = await self._collect_org_links(page)
                    print(f"Найдено ссылок на карточки: {len(links)}")

                    candidate_limit = min(len(links), max(self.limit * 5, 40))
                    for index, card_url in enumerate(links[:candidate_limit], start=1):
                        if len(self.leads) >= self.limit:
                            break
                        print(f"  Карточка {index}/{candidate_limit}: {card_url}")
                        try:
                            lead = await self._parse_card(page, card_url)
                        except CaptchaDetected:
                            raise
                        except Exception as exc:
                            print(f"    Пропуск карточки: {type(exc).__name__}: {exc}")
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                            continue

                        if lead and lead.reviews >= self.min_reviews:
                            self.leads.append(lead)
                            print(
                                f"    + {lead.name} | отзывы: {lead.reviews} | "
                                f"сайт: {lead.website or 'нет'}"
                            )
                        elif lead:
                            print(f"    - {lead.name} | отзывы: {lead.reviews} < {self.min_reviews}")

                        await asyncio.sleep(random.uniform(1.4, 3.2))
                except CaptchaDetected:
                    self.captcha_detected = True
                    print("ВНИМАНИЕ: Яндекс показал капчу. Парсинг остановлен, собранные лиды сохраняются.")
                finally:
                    await context.close()
                    await browser.close()
        except Exception as exc:
            if self.leads:
                print(
                    f"ВНИМАНИЕ: Playwright остановился с ошибкой {type(exc).__name__}: {exc}. "
                    f"Сохраняю уже собранные лиды ({len(self.leads)})."
                )
                return self.leads, self.captcha_detected
            raise

        return self.leads, self.captcha_detected

    async def _load_search(self, page: Page) -> None:
        search_url = "https://yandex.ru/maps/?text=" + quote_plus(f"{self.query} {self.city}")
        print(f"Открываю Яндекс.Карты: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(1800, 3200))
        await self._dismiss_cookie_banner(page)
        await self._check_captcha(page)

        try:
            await page.wait_for_selector('a[href*="/org/"]', timeout=20000)
        except PlaywrightTimeoutError:
            await self._check_captcha(page)
            body = _clean_text(await page.locator("body").inner_text(timeout=3000))
            raise RuntimeError(
                "Не дождался карточек организаций. Возможно, изменилась разметка Яндекс.Карт "
                f"или выдача пустая. Фрагмент страницы: {body[:300]}"
            )

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        for text in ("Принять всё", "Принять", "Согласен", "Понятно"):
            try:
                button = page.get_by_role(
                    "button", name=re.compile(rf"^{re.escape(text)}$", re.IGNORECASE)
                ).first
                if await button.is_visible(timeout=700):
                    await button.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    async def _check_captcha(self, page: Page) -> None:
        try:
            url = page.url.lower()
            body = (await page.locator("body").inner_text(timeout=2500)).lower()
            html = (await page.content()).lower()
        except Exception:
            return

        captcha_markers = (
            "подтвердите, что вы не робот",
            "я не робот",
            "не робот",
            "captcha",
            "smartcaptcha",
        )
        has_iframe = False
        try:
            has_iframe = (
                await page.locator(
                    "iframe[src*='captcha'], iframe[src*='showcaptcha'], "
                    ".smart-captcha, [class*='captcha']"
                ).count()
                > 0
            )
        except Exception:
            pass

        if "showcaptcha" in url or "captcha" in url or has_iframe:
            raise CaptchaDetected("Капча в URL/разметке Яндекс.Карт")
        if any(marker in body for marker in captcha_markers):
            raise CaptchaDetected("Текст капчи на странице Яндекс.Карт")
        if "smart-captcha" in html or "showcaptcha" in html:
            raise CaptchaDetected("Капча в HTML Яндекс.Карт")

    async def _collect_org_links(self, page: Page) -> list[str]:
        seen: dict[str, str] = {}
        no_growth_rounds = 0
        previous_count = 0

        for _ in range(30):
            await self._check_captcha(page)
            try:
                anchors = await page.locator('a[href*="/org/"]').evaluate_all(
                    """elements => elements.map(element => ({
                        href: element.href || element.getAttribute('href') || '',
                        text: (element.textContent || '').trim()
                    }))"""
                )
            except Exception:
                anchors = []

            for anchor in anchors:
                href = (anchor.get("href") or "").strip()
                if not href:
                    continue
                parsed = urlparse(href)
                if "/org/" not in parsed.path:
                    continue
                normalized_path = self._canonical_org_path(parsed.path)
                if not normalized_path:
                    continue
                clean_url = parsed._replace(
                    path=normalized_path, query="", fragment=""
                ).geturl()
                seen.setdefault(clean_url, _clean_text(anchor.get("text") or ""))

            current_count = len(seen)
            if current_count >= self.limit * 5:
                break
            if current_count <= previous_count:
                no_growth_rounds += 1
            else:
                no_growth_rounds = 0
            previous_count = current_count
            if no_growth_rounds >= 6:
                break

            await self._scroll_results(page)
            await page.wait_for_timeout(random.randint(900, 1800))

        return list(seen.keys())

    @staticmethod
    def _canonical_org_path(path: str) -> str:
        match = re.search(r"(/maps/org/[^/]+/\d+)", path)
        if match:
            return match.group(1) + "/"
        match = re.search(r"(/org/[^/]+/\d+)", path)
        if match:
            return match.group(1) + "/"
        return ""

    async def _scroll_results(self, page: Page) -> None:
        try:
            await page.evaluate(
                r"""() => {
                    const links = Array.from(document.querySelectorAll('a[href*="/org/"]'));
                    if (!links.length) {
                        window.scrollBy(0, window.innerHeight);
                        return;
                    }
                    let element = links[links.length - 1];
                    while (element && element !== document.body) {
                        const style = window.getComputedStyle(element);
                        if (/(auto|scroll)/i.test(style.overflowY) && element.scrollHeight > element.clientHeight + 40) {
                            element.scrollTop = element.scrollHeight;
                            return;
                        }
                        element = element.parentElement;
                    }
                    window.scrollBy(0, window.innerHeight);
                }"""
            )
        except Exception:
            pass

    async def _parse_card(self, page: Page, card_url: str) -> Lead | None:
        await page.goto(card_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(1400, 2600))
        await self._check_captcha(page)

        try:
            await page.wait_for_selector("h1", timeout=12000)
        except PlaywrightTimeoutError:
            await self._check_captcha(page)

        name = await self._first_text(
            page,
            (
                "h1",
                "[itemprop='name']",
                "[class*='orgpage-header-view__header']",
            ),
        )
        if not name:
            try:
                name = _clean_text(await page.title())
            except Exception:
                name = ""
        if not name:
            return None

        phone = await self._extract_phone(page)
        address = await self._extract_address(page)
        website = await self._extract_website(page)
        rating, reviews = await self._extract_rating_reviews(page)

        return Lead(
            name=name,
            address=address,
            phone=phone,
            rating=rating,
            reviews=reviews,
            website=website,
            card_url=self._canonicalize_card_url(page.url or card_url),
        )

    def _canonicalize_card_url(self, url: str) -> str:
        parsed = urlparse(url)
        normalized_path = self._canonical_org_path(parsed.path)
        if not normalized_path:
            return url
        return parsed._replace(path=normalized_path, query="", fragment="").geturl()

    async def _first_text(self, page: Page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = _clean_text(await locator.inner_text(timeout=2500))
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _extract_phone(self, page: Page) -> str:
        try:
            hrefs = await page.locator("a[href^='tel:']").evaluate_all(
                "elements => elements.map(element => element.getAttribute('href') || '')"
            )
            for href in hrefs:
                phone = _clean_text(unquote(href.replace("tel:", "", 1)))
                if phone:
                    return phone
        except Exception:
            pass

        phone = await self._first_text(page, ("[itemprop='telephone']", "[class*='phone']"))
        if phone:
            return phone

        try:
            return _clean_text(
                await page.evaluate(
                    r"""() => {
                        const text = document.body.innerText || '';
                        const match = text.match(/(?:\+375|8\s*0?29|\+7)[\d\s()\-]{7,20}/);
                        return match ? match[0] : '';
                    }"""
                )
            )
        except Exception:
            return ""

    async def _extract_address(self, page: Page) -> str:
        address = await self._first_text(
            page,
            (
                "[itemprop='address']",
                "a[href*='whatshere']",
                "[class*='business-contacts-view__address']",
                "[class*='orgpage-header-view__address']",
            ),
        )
        if address:
            return address

        try:
            return _clean_text(
                await page.evaluate(
                    r"""() => {
                        const text = document.body.innerText || '';
                        const match = text.match(/(?:Адрес\s*\n)([^\n]{5,180})/i);
                        return match ? match[1] : '';
                    }"""
                )
            )
        except Exception:
            return ""

    async def _extract_website(self, page: Page) -> str:
        try:
            website = await page.evaluate(
                r"""() => {
                    const yandexHost = host => (
                        /(^|\.)yandex\.(ru|by|com|kz|ua)$/i.test(host) ||
                        /(^|\.)ya\.ru$/i.test(host) ||
                        /(^|\.)yastatic\.net$/i.test(host) ||
                        /passport\./i.test(host)
                    );
                    const unwrap = raw => {
                        try {
                            const url = new URL(raw, location.href);
                            for (const key of ['url', 'to', 'target', 'redir', 'redirect']) {
                                const value = url.searchParams.get(key);
                                if (value && /^https?:\/\//i.test(value)) return value;
                            }
                            return url.href;
                        } catch {
                            return '';
                        }
                    };
                    const anchors = Array.from(document.querySelectorAll('a[href]'));
                    const candidates = [];
                    for (const anchor of anchors) {
                        const text = (anchor.textContent || '').trim().toLowerCase();
                        const href = unwrap(anchor.getAttribute('href') || '');
                        if (!/^https?:\/\//i.test(href)) continue;
                        let host = '';
                        try { host = new URL(href).hostname.toLowerCase(); } catch { continue; }
                        if (yandexHost(host)) continue;
                        const cls = ((anchor.className || '') + ' ' + (anchor.getAttribute('data-testid') || '')).toLowerCase();
                        let score = 0;
                        if (/website|site|contacts-view__url/.test(cls)) score += 6;
                        if (text === 'сайт' || text === 'перейти на сайт' || text.startsWith('сайт')) score += 10;
                        if (/^[\w.-]+\.[a-zа-я]{2,}/i.test(text)) score += 4;
                        candidates.push({ href, score });
                    }
                    candidates.sort((a, b) => b.score - a.score);
                    return candidates.length ? candidates[0].href : '';
                }"""
            )
            return _clean_text(website)
        except Exception:
            return ""

    async def _extract_rating_reviews(self, page: Page) -> tuple[str, int]:
        try:
            data = await page.evaluate(
                r"""() => {
                    const text = document.body.innerText || '';
                    let rating = '';
                    const ratingElement = document.querySelector('[itemprop="ratingValue"]');
                    if (ratingElement) {
                        rating = ratingElement.getAttribute('content') || ratingElement.textContent || '';
                    }
                    if (!rating) {
                        const elements = Array.from(document.querySelectorAll('[class*="rating"], [aria-label*="Рейтинг"]'));
                        const found = elements
                            .map(element => ((element.getAttribute('aria-label') || '') + ' ' + (element.textContent || '')))
                            .find(value => /\d[.,]\d/.test(value));
                        if (found) {
                            const match = found.match(/\d[.,]\d/);
                            if (match) rating = match[0];
                        }
                    }
                    if (!rating) {
                        const match = text.match(/рейтинг[^\d]{0,20}(\d[.,]\d)/i);
                        if (match) rating = match[1];
                    }

                    let reviews = 0;
                    const reviewElement = document.querySelector('[itemprop="reviewCount"]');
                    if (reviewElement) {
                        const value = reviewElement.getAttribute('content') || reviewElement.textContent || '';
                        const digits = (value.match(/\d[\d\s]*/) || [''])[0].replace(/\s/g, '');
                        if (digits) reviews = parseInt(digits, 10);
                    }
                    if (!reviews) {
                        const matches = Array.from(text.matchAll(/(\d[\d\s]{0,8})\s*(?:отзыв(?:а|ов)?|оцен(?:ка|ки|ок))/gi));
                        if (matches.length) {
                            const digits = matches[0][1].replace(/\s/g, '');
                            reviews = parseInt(digits, 10);
                        }
                    }
                    return { rating, reviews };
                }"""
            )
            rating = _clean_text(str(data.get("rating", ""))).replace(",", ".")
            try:
                reviews = int(data.get("reviews", 0) or 0)
            except (TypeError, ValueError):
                reviews = 0
            return rating, reviews
        except Exception:
            return "", 0
