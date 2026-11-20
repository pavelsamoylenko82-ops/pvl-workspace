#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автономная SEO-проверка сайта по 5 пунктам (только стандартная библиотека).

Запуск:
    python tools/seo_check.py <главный_url> [url_страницы_услуги_или_товара] [технический_url_хостинга]
"""

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


TIMEOUT = 15
MAX_BODY = 5 * 1024 * 1024
MAX_SITEMAP_FILES = 50
USER_AGENT = "PVL-SEO-Check/1.0 (+tools/seo_check.py)"

BOTS = [
    ("Googlebot", ("googlebot",)),
    ("Yandex", ("yandex", "yandexbot")),
    ("Bingbot", ("bingbot",)),
    ("OAI-SearchBot", ("oai-searchbot",)),
    ("ChatGPT-User", ("chatgpt-user",)),
    ("Claude-SearchBot", ("claude-searchbot",)),
    ("PerplexityBot", ("perplexitybot",)),
    ("YandexAdditionalBot", ("yandexadditionalbot",)),
]

CATALOG_IMAGE_PATHS = [
    "/catalog/",
    "/shop/",
    "/products/",
    "/product/",
    "/uslugi/",
    "/services/",
    "/images/",
    "/image/",
    "/img/",
    "/uploads/",
    "/wp-content/uploads/",
]

TEST_HOSTS = ("localhost", "vercel.app", "netlify.app")


class Response:
    def __init__(self, url, status=None, headers=None, body=b"", final_url="", error=None):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.body = body or b""
        self.final_url = final_url or url
        self.error = error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def fetch(url, follow_redirects=False, timeout=TIMEOUT):
    """GET-запрос без внешних зависимостей. Редиректы по умолчанию не выполняются."""
    if not re.match(r"^https?://", url or "", re.I):
        return Response(url=url, error="URL должен начинаться с http:// или https://")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    # Диагностический инструмент не должен падать из-за отсутствия CA-бандла
    # в локальном Python. Для SEO-проверки важнее получить сам ответ сервера.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=context)]
    if not follow_redirects:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)

    try:
        response = opener.open(request, timeout=timeout)
        body = response.read(MAX_BODY)
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        status = getattr(response, "status", None) or response.getcode()
        return Response(
            url=url,
            status=status,
            headers=headers,
            body=body,
            final_url=response.geturl(),
        )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_BODY)
        except Exception:
            body = b""
        headers = {str(k).lower(): str(v) for k, v in exc.headers.items()}
        return Response(
            url=url,
            status=exc.code,
            headers=headers,
            body=body,
            final_url=exc.geturl() or url,
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return Response(url=url, error="сетевая ошибка: {0}".format(reason))
    except ssl.SSLError as exc:
        return Response(url=url, error="SSL-ошибка: {0}".format(exc))
    except Exception as exc:
        return Response(url=url, error="{0}: {1}".format(type(exc).__name__, exc))


def decode_body(response):
    content_type = response.headers.get("content-type", "")
    encodings = []
    match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", content_type, re.I)
    if match:
        encodings.append(match.group(1))

    head = response.body[:4096].decode("ascii", errors="ignore")
    match = re.search(r"<meta[^>]+charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", head, re.I)
    if match:
        encodings.append(match.group(1))

    for encoding in encodings + ["utf-8", "cp1251"]:
        try:
            return response.body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return response.body.decode("utf-8", errors="replace")


def _safe_chr(number):
    try:
        return chr(number)
    except (ValueError, OverflowError):
        return ""


def unescape_html(value):
    value = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: _safe_chr(int(m.group(1), 16)), value)
    value = re.sub(r"&#(\d+);", lambda m: _safe_chr(int(m.group(1))), value)
    replacements = (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&apos;", "'"),
        ("&nbsp;", " "),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    return value


def parse_attrs(tag):
    if hasattr(tag, "group"):
        tag = tag.group(0)
    attrs = {}
    pattern = r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
    for match in re.finditer(pattern, tag):
        value = next((group for group in match.groups()[1:] if group is not None), "")
        attrs[match.group(1).lower()] = unescape_html(value).strip()
    return attrs


def iter_tags(text, tag_name):
    return re.finditer(r"<{0}\b[^>]*>".format(tag_name), text, re.I | re.S)


def check_html_page(url, label):
    response = fetch(url, follow_redirects=True)
    if response.error:
        return ["{0}: ошибка запроса ({1})".format(label, response.error)], ""

    text = decode_body(response)
    issues = []
    if not text.strip():
        issues.append("{0}: пустой HTML-ответ".format(label))

    title = ""
    title_match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", text, re.I | re.S)
    if title_match:
        title = unescape_html(re.sub(r"<[^>]+>", "", title_match.group(1)))
        title = re.sub(r"\s+", " ", title).strip()
    if not title:
        issues.append("{0}: нет <title> или он пустой".format(label))
    elif len(title) > 65:
        issues.append("{0}: title {1} знаков (лимит 65)".format(label, len(title)))

    description_found = False
    for tag in iter_tags(text, "meta"):
        attrs = parse_attrs(tag)
        if attrs.get("name", "").lower() == "description" and attrs.get("content", "").strip():
            description_found = True
            break
    if not description_found:
        issues.append("{0}: нет <meta name=\"description\"> с content".format(label))

    canonical_found = False
    for tag in iter_tags(text, "link"):
        attrs = parse_attrs(tag)
        rels = attrs.get("rel", "").lower().split()
        if "canonical" in rels and attrs.get("href", "").strip():
            canonical_found = True
            break
    if not canonical_found:
        issues.append("{0}: нет <link rel=\"canonical\">".format(label))

    h1_count = len(re.findall(r"<h1\b[^>]*>", text, re.I))
    if h1_count != 1:
        issues.append("{0}: H1 найдено {1}, нужен ровно 1".format(label, h1_count))

    html_tag = re.search(r"<html\b[^>]*>", text, re.I | re.S)
    lang = ""
    if html_tag:
        lang = parse_attrs(html_tag.group(0)).get("lang", "")
    if lang.lower() != "ru":
        issues.append("{0}: lang=\"{1}\", нужен lang=\"ru\"".format(label, lang))

    og = {}
    for tag in iter_tags(text, "meta"):
        attrs = parse_attrs(tag)
        prop = (attrs.get("property") or attrs.get("name") or "").lower()
        if prop in ("og:title", "og:image") and attrs.get("content", "").strip():
            og[prop] = attrs.get("content", "").strip()
    for prop in ("og:title", "og:image"):
        if prop not in og:
            issues.append("{0}: нет OpenGraph {1}".format(label, prop))

    favicon_found = False
    for tag in iter_tags(text, "link"):
        attrs = parse_attrs(tag)
        rels = attrs.get("rel", "").lower().split()
        if any(rel in ("icon", "shortcut", "apple-touch-icon") for rel in rels) and attrs.get("href", "").strip():
            favicon_found = True
            break
    if not favicon_found:
        issues.append("{0}: нет favicon".format(label))

    noindex_found = False
    for tag in iter_tags(text, "meta"):
        attrs = parse_attrs(tag)
        if attrs.get("name", "").lower() == "robots" and "noindex" in attrs.get("content", "").lower():
            noindex_found = True
            break
    x_robots = response.headers.get("x-robots-tag", "").lower()
    if noindex_found or "noindex" in x_robots:
        issues.append("{0}: найден noindex на боевой странице".format(label))

    ld_json_ok = False
    ld_json_bad = False
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", text, re.I | re.S):
        attrs = parse_attrs(match.group(1))
        if attrs.get("type", "").lower() != "application/ld+json":
            continue
        payload = match.group(2).strip()
        if not payload:
            ld_json_bad = True
            continue
        try:
            json.loads(payload)
            ld_json_ok = True
        except Exception:
            ld_json_bad = True
    if not ld_json_ok:
        if ld_json_bad:
            issues.append("{0}: application/ld+json есть, но JSON невалиден".format(label))
        else:
            issues.append("{0}: нет валидной разметки application/ld+json".format(label))

    images = list(iter_tags(text, "img"))
    images_without_alt = sum(1 for tag in images if "alt" not in parse_attrs(tag))
    if images_without_alt:
        issues.append("{0}: у {1} из {2} <img> нет alt".format(label, images_without_alt, len(images)))

    summary = (
        "{0}: title {1}; description {2}; canonical {3}; H1 {4}; lang {5}; "
        "OG {6}; JSON-LD {7}; img alt {8}/{9}"
    ).format(
        label,
        len(title) if title else "нет",
        "есть" if description_found else "нет",
        "есть" if canonical_found else "нет",
        h1_count,
        lang or "нет",
        "есть" if len(og) == 2 else "нет",
        "есть" if ld_json_ok else "нет",
        len(images) - images_without_alt,
        len(images),
    )
    return issues, summary


def canonical_origin(main_url, main_response):
    """Возвращает боевой корень сайта с базовым путём (важно для GitHub Pages)."""
    final_url = main_url
    if main_response and not main_response.error and main_response.final_url:
        final_url = main_response.final_url
    parsed = urllib.parse.urlsplit(final_url)
    if not parsed.netloc:
        parsed = urllib.parse.urlsplit(main_url)

    path = parsed.path or "/"
    if not path.endswith("/"):
        last_segment = path.rsplit("/", 1)[-1]
        if "." in last_segment:
            path = path.rsplit("/", 1)[0] + "/"
        else:
            path += "/"
    return "https://{0}{1}".format(parsed.netloc.lower(), path)


def toggle_www(netloc):
    host = netloc.lower()
    if host.startswith("www."):
        return host[4:]
    return "www." + host


def redirect_matches(response, expected_scheme, expected_netloc):
    if response.error or response.status != 301:
        return False
    location = response.headers.get("location", "").strip()
    if not location:
        return False
    target = urllib.parse.urlsplit(urllib.parse.urljoin(response.url, location))
    return target.scheme.lower() == expected_scheme and target.netloc.lower() == expected_netloc.lower()


def describe_response(response):
    if response.error:
        return "ошибка: {0}".format(response.error)
    location = response.headers.get("location", "")
    if location:
        return "HTTP {0} -> {1}".format(response.status, location)
    return "HTTP {0}".format(response.status)


def follow_redirect_chain(url, max_hops=10):
    """Возвращает список фактических редиректов до первого не-3xx ответа."""
    chain = []
    current = url
    seen = set()
    for _ in range(max_hops):
        if current in seen:
            break
        seen.add(current)
        response = fetch(current)
        if response.error:
            break
        location = response.headers.get("location", "").strip()
        if response.status not in (301, 302, 303, 307, 308) or not location:
            break
        chain.append((current, response.status, location))
        current = urllib.parse.urljoin(current, location)
    return chain


def check_redirects(main_url, main_response, hosting_url):
    issues = []
    origin = canonical_origin(main_url, main_response)
    parsed = urllib.parse.urlsplit(origin)
    netloc = parsed.netloc.lower()
    alt_netloc = toggle_www(netloc)
    base_path = parsed.path or "/"

    http_url = "http://{0}{1}".format(netloc, base_path)
    http_response = fetch(http_url)
    if not redirect_matches(http_response, "https", netloc):
        issues.append("http://{0}{1}: {2}, ожидается 301 на https://{0}{1}".format(
            netloc, base_path, describe_response(http_response)))

    alt_url = "https://{0}{1}".format(alt_netloc, base_path)
    alt_response = fetch(alt_url)
    if not redirect_matches(alt_response, "https", netloc):
        issues.append("https://{0}{1}: {2}, ожидается 301 на https://{3}{1}".format(
            alt_netloc, base_path, describe_response(alt_response), netloc))

    for chain_url in (main_url, http_url, alt_url):
        chain = follow_redirect_chain(chain_url)
        if len(chain) >= 3:
            issues.append("цепочка из {0} редиректов: {1}".format(
                len(chain), " -> ".join([item[0] for item in chain] + [urllib.parse.urljoin(chain[-1][0], chain[-1][2])])))

    hosting_note = "технический URL не передан"
    if hosting_url:
        hosting_response = fetch(hosting_url)
        x_robots = hosting_response.headers.get("x-robots-tag", "").lower()
        if hosting_response.error:
            issues.append("технический URL: {0}".format(hosting_response.error))
            hosting_note = "технический URL: ошибка"
        elif hosting_response.status == 301:
            location = hosting_response.headers.get("location", "").strip()
            target = urllib.parse.urlsplit(urllib.parse.urljoin(hosting_url, location))
            if target.netloc.lower() != netloc:
                issues.append("технический URL: 301 ведёт на {0}, а не на боевой {1}".format(
                    target.netloc or "пустой Location", netloc))
                hosting_note = "технический URL: 301 не на боевой домен"
            else:
                hosting_note = "хостинг: 301 на боевой домен"
        elif "noindex" in x_robots:
            hosting_note = "хостинг: X-Robots-Tag noindex"
        else:
            issues.append("технический URL: HTTP {0}, нет 301 на боевой домен и нет X-Robots-Tag: noindex".format(
                hosting_response.status))
            hosting_note = "технический URL: нет 301/noindex"

    if issues:
        return issues
    return ["http -> https: 301; www -> без www: 301; цепочек 3+ нет; {0}".format(hosting_note)]


def check_404(origin):
    url = origin.rstrip("/") + "/non-existent-check-404-samola"
    response = fetch(url)
    if response.error:
        return ["{0}: ошибка запроса ({1})".format(url, response.error)]
    if response.status != 404:
        return ["{0}: HTTP {1}, нужен 404 (редирект на главную/200 не подходят)".format(
            url, response.status)]
    return ["HTTP 404 на {0}".format(url)]


def parse_robots(text):
    groups = []
    agents = []
    rules = []
    sitemaps = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents = [value]
                rules = []
            else:
                agents.append(value)
        elif key in ("allow", "disallow"):
            if not agents:
                agents = ["*"]
            rules.append((key, value))
        elif key == "sitemap":
            sitemaps.append(value)

    if agents or rules:
        groups.append((agents, rules))
    return groups, sitemaps


def robots_pattern_matches(pattern, path):
    if not pattern or not pattern.startswith("/"):
        return False
    regex = re.escape(pattern)
    regex = regex.replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    else:
        regex += ".*"
    try:
        return re.match("^" + regex, path) is not None
    except re.error:
        return False


def is_path_blocked(rules, path):
    best_length = -1
    blocked = False
    for directive, value in rules:
        if not value or not value.startswith("/"):
            continue
        if robots_pattern_matches(value, path):
            specificity = len(value)
            if specificity > best_length or (specificity == best_length and directive == "allow"):
                best_length = specificity
                blocked = directive == "disallow"
    return blocked


def rules_for_bot(groups, bot_names):
    specific = []
    found_specific = False
    for agents, rules in groups:
        if any(agent.lower() in bot_names for agent in agents):
            found_specific = True
            specific.extend(rules)
    if found_specific:
        return specific

    wildcard = []
    for agents, rules in groups:
        if any(agent.strip() == "*" for agent in agents):
            wildcard.extend(rules)
    return wildcard


def check_robots(origin):
    robots_url = origin.rstrip("/") + "/robots.txt"
    response = fetch(robots_url, follow_redirects=True)
    if response.error:
        return ["{0}: ошибка запроса ({1})".format(robots_url, response.error)], []

    if response.status != 200:
        return ["{0}: HTTP {1}, файл недоступен".format(robots_url, response.status)], []

    text = decode_body(response)
    groups, sitemaps = parse_robots(text)
    issues = []

    full_sitemaps = [url for url in sitemaps if re.match(r"^https?://", url, re.I)]
    relative_sitemaps = [url for url in sitemaps if url and not re.match(r"^https?://", url, re.I)]
    if not sitemaps:
        issues.append("нет директивы Sitemap")
    elif relative_sitemaps:
        issues.append("Sitemap указан не полным URL: {0}".format(", ".join(relative_sitemaps[:3])))

    wildcard_rules = rules_for_bot(groups, ())
    blocked_sections = []
    for path in CATALOG_IMAGE_PATHS:
        if is_path_blocked(wildcard_rules, path):
            blocked_sections.append(path)
    if blocked_sections:
        issues.append("закрыты разделы каталога/картинок: {0}".format(", ".join(blocked_sections[:6])))

    blocked_bots = []
    for label, names in BOTS:
        rules = rules_for_bot(groups, names)
        if is_path_blocked(rules, "/"):
            blocked_bots.append(label)
    if blocked_bots:
        issues.append("заблокированы боты: {0}".format(", ".join(blocked_bots)))

    if issues:
        return issues, full_sitemaps
    note = "robots.txt доступен; Sitemap: {0}; каталог/картинки и боты открыты".format(
        full_sitemaps[0] if full_sitemaps else "нет")
    return [note], full_sitemaps


def local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def collect_sitemap(url, visited, depth=0):
    errors = []
    page_urls = []
    if depth > 3:
        return errors, page_urls
    if len(visited) >= MAX_SITEMAP_FILES:
        return ["достигнут лимит {0} файлов sitemap".format(MAX_SITEMAP_FILES)], page_urls
    if url in visited:
        return errors, page_urls
    visited.add(url)

    response = fetch(url, follow_redirects=True)
    if response.error:
        return ["{0}: ошибка запроса ({1})".format(url, response.error)], page_urls
    if response.status != 200:
        return ["{0}: HTTP {1}".format(url, response.status)], page_urls

    try:
        root = ET.fromstring(response.body)
    except ET.ParseError as exc:
        return ["{0}: XML невалиден ({1})".format(url, exc)], page_urls

    root_tag = local_name(root.tag)
    if root_tag == "sitemapindex":
        for child in root:
            if local_name(child.tag) != "sitemap":
                continue
            loc = next((item.text.strip() for item in child if local_name(item.tag) == "loc" and item.text), "")
            if not loc:
                errors.append("{0}: пустой <loc> в sitemapindex".format(url))
                continue
            child_errors, child_urls = collect_sitemap(loc, visited, depth + 1)
            errors.extend(child_errors)
            page_urls.extend(child_urls)
    elif root_tag == "urlset":
        for child in root:
            if local_name(child.tag) != "url":
                continue
            loc = next((item.text.strip() for item in child if local_name(item.tag) == "loc" and item.text), "")
            if loc:
                page_urls.append(loc)
            else:
                errors.append("{0}: пустой <loc> в <url>".format(url))
    else:
        errors.append("{0}: корневой тег <{1}>, ожидается urlset или sitemapindex".format(
            url, local_name(root.tag)))
    return errors, page_urls


def check_sitemap(origin, robots_sitemaps):
    sitemap_urls = robots_sitemaps[:]
    if not sitemap_urls:
        sitemap_urls = [origin.rstrip("/") + "/sitemap.xml"]

    visited = set()
    errors = []
    page_urls = []
    for sitemap_url in sitemap_urls:
        part_errors, part_urls = collect_sitemap(sitemap_url, visited)
        errors.extend(part_errors)
        page_urls.extend(part_urls)

    if errors:
        return errors[:5]

    if not page_urls:
        return ["в sitemap нет ни одного <url><loc>"]

    canonical_host = urllib.parse.urlsplit(origin).hostname or ""
    canonical_host = canonical_host.lower()
    bad_domain = []
    test_addresses = []
    empty_urls = 0

    for url in page_urls:
        if not url:
            empty_urls += 1
            continue
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not host:
            bad_domain.append(url)
            continue
        if host == "localhost" or host.endswith(".localhost"):
            test_addresses.append(url)
            continue
        if any(host == test or host.endswith("." + test) for test in TEST_HOSTS):
            test_addresses.append(url)
            continue
        if host != canonical_host:
            bad_domain.append(url)

    issues = []
    if empty_urls:
        issues.append("пустых URL: {0}".format(empty_urls))
    if bad_domain:
        issues.append("не на боевом домене {0}: {1}{2}".format(
            canonical_host,
            ", ".join(bad_domain[:3]),
            " и ещё {0}".format(len(bad_domain) - 3) if len(bad_domain) > 3 else "",
        ))
    if test_addresses:
        issues.append("тестовые адреса: {0}{1}".format(
            ", ".join(test_addresses[:3]),
            " и ещё {0}".format(len(test_addresses) - 3) if len(test_addresses) > 3 else "",
        ))

    if issues:
        return issues
    return ["XML валиден; URL: {0}; все на {1}; тестовых адресов нет".format(
        len(page_urls), canonical_host)]


def wrap_text(text, width):
    if not text:
        return [""]
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def print_table(rows):
    headers = ("№", "Пункт", "Статус", "Пояснение")
    widths = (3, 24, 8, 92)
    wrapped_rows = []
    for row in rows:
        cells = [wrap_text(str(cell), widths[index]) for index, cell in enumerate(row)]
        height = max(len(cell) for cell in cells)
        lines = []
        for line_index in range(height):
            lines.append([cell[line_index] if line_index < len(cell) else "" for cell in cells])
        wrapped_rows.append((row[2], lines))

    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def make_line(cells):
        return "| " + " | ".join(cells[index].ljust(widths[index]) for index in range(len(widths))) + " |"

    print(border)
    print(make_line(list(headers)))
    print(border)
    for _, lines in wrapped_rows:
        for line in lines:
            print(make_line(line))
        print(border)


def main(argv):
    if len(argv) < 2 or len(argv) > 4:
        print("Использование:")
        print("  python tools/seo_check.py <главный_url> [url_страницы_услуги_или_товара] [технический_url_хостинга]")
        return 2

    main_url = normalize_url(argv[1])
    service_url = normalize_url(argv[2]) if len(argv) > 2 and argv[2].strip() else ""
    hosting_url = normalize_url(argv[3]) if len(argv) > 3 and argv[3].strip() else ""

    print("SEO-ПРОВЕРКА САЙТА")
    print("Главная:          {0}".format(main_url))
    print("Страница услуги:  {0}".format(service_url or "не передана"))
    print("Технический URL:  {0}".format(hosting_url or "не передан"))
    print("")

    main_response = fetch(main_url, follow_redirects=True)
    origin = canonical_origin(main_url, main_response)

    html_issues, main_summary = check_html_page(main_url, "Главная")
    html_summaries = [main_summary] if main_summary else []
    if service_url:
        service_issues, service_summary = check_html_page(service_url, "Услуга/товар")
        html_issues.extend(service_issues)
        if service_summary:
            html_summaries.append(service_summary)

    redirect_result = check_redirects(main_url, main_response, hosting_url)
    redirect_issues = redirect_result if any(
        item.startswith(("http://", "https://", "технический URL"))
        for item in redirect_result
    ) else []

    not_found_result = check_404(origin)
    not_found_issues = [] if not_found_result[0].startswith("HTTP 404") else not_found_result

    robots_issues, robots_sitemaps = check_robots(origin)
    sitemap_issues = check_sitemap(origin, robots_sitemaps)

    robots_ok = not _has_issue_prefix(robots_issues)
    sitemap_ok = not _has_issue_prefix(sitemap_issues)
    rows = [
        ("1", "Исходный HTML", "[ДА]" if not html_issues else "[НЕТ]",
         "; ".join(html_issues) if html_issues else "; ".join(html_summaries)),
        ("2", "Переадресации", "[ДА]" if not redirect_issues else "[НЕТ]",
         "; ".join(redirect_issues) if redirect_issues else redirect_result[0]),
        ("3", "Код 404", "[ДА]" if not not_found_issues else "[НЕТ]",
         "; ".join(not_found_issues) if not_found_issues else not_found_result[0]),
        ("4", "robots.txt", "[ДА]" if robots_ok else "[НЕТ]", "; ".join(robots_issues)),
        ("5", "sitemap.xml", "[ДА]" if sitemap_ok else "[НЕТ]", "; ".join(sitemap_issues)),
    ]

    print_table(rows)
    passed = sum(1 for row in rows if row[2] == "[ДА]")
    print("Итог: {0}/5 проверок пройдено.".format(passed))
    return 0 if passed == 5 else 1


def _has_issue_prefix(items):
    """Возвращает True, если в списке есть сообщение об ошибке (а не успешная сводка)."""
    if not items:
        return True
    success_markers = (
        "HTTP 404",
        "XML валиден;",
        "robots.txt доступен;",
        "http -> https: 301;",
    )
    for item in items:
        if item.startswith("Главная:") or item.startswith("Услуга/товар:"):
            continue
        if item.startswith(success_markers):
            continue
        return True
    return False


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)
    except Exception as exc:
        print("Критическая ошибка: {0}: {1}".format(type(exc).__name__, exc))
        sys.exit(1)
