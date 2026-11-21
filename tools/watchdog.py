#!/usr/bin/env python3
"""Ежедневный сторож для сайтов, healthz и ИИ-консультанта.

Только стандартная библиотека. Запуск по cron на VPS:
    python tools/watchdog.py
    python tools/watchdog.py --once
    python tools/watchdog.py --dry-run

Конфиг: tools/watchdog_targets.json (реальный файл в .gitignore).
Секреты: переменные окружения WATCHDOG_TG_TOKEN и WATCHDOG_TG_CHAT_ID.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_RETRY_WAIT = 15 * 60
DEFAULT_TIMEOUT = 20
SUCCESS_TEXT = "Сторож: всё живо"


def usage():
    print("""Использование:
  python tools/watchdog.py [--config путь] [--once] [--dry-run]

  --config   путь к JSON-конфигу (по умолчанию tools/watchdog_targets.json)
  --once     одна проверка без ожидания и повторной попытки
  --dry-run  проверка без отправки в Telegram и без ожидания
""")


def parse_args(argv):
    options = {
        "config": os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog_targets.json"),
        "once": False,
        "dry_run": False,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            usage()
            sys.exit(0)
        elif arg == "--once":
            options["once"] = True
        elif arg == "--dry-run":
            options["dry_run"] = True
        elif arg == "--config":
            i += 1
            if i >= len(argv):
                print("Ошибка: --config требует путь к файлу", file=sys.stderr)
                sys.exit(2)
            options["config"] = argv[i]
        elif arg.startswith("--config="):
            options["config"] = arg.split("=", 1)[1]
        else:
            print("Ошибка: неизвестный аргумент %s" % arg, file=sys.stderr)
            usage()
            sys.exit(2)
        i += 1
    return options


def load_config(path):
    if not os.path.isfile(path):
        print("Ошибка: конфиг не найден: %s" % path, file=sys.stderr)
        print("Создайте его по примеру tools/watchdog_targets.example.json", file=sys.stderr)
        sys.exit(2)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        print("Ошибка чтения конфига: %s" % exc, file=sys.stderr)
        sys.exit(2)
    if not isinstance(config, dict):
        print("Ошибка: корень конфига должен быть объектом JSON", file=sys.stderr)
        sys.exit(2)
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        print("Ошибка: в конфиге нужен непустой массив targets", file=sys.stderr)
        sys.exit(2)
    return config


def make_ssl_context():
    return ssl.create_default_context()


def check_url(name, url, timeout, expect_status=200, check_noindex=False):
    """Проверяет один URL. Возвращает словарь результата."""
    result = {
        "name": name,
        "url": url,
        "ok": False,
        "status": None,
        "detail": "",
    }
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PVL-Watchdog/1.0 (+cron)",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=make_ssl_context()) as response:
            status = response.getcode()
            headers = response.headers
            result["status"] = status
            if status != expect_status:
                result["detail"] = "HTTP %s вместо %s" % (status, expect_status)
                return result
            if check_noindex:
                values = headers.get_all("X-Robots-Tag") or []
                joined = " ".join(values).lower()
                if "noindex" in joined:
                    result["detail"] = "найден X-Robots-Tag: noindex"
                    return result
            result["ok"] = True
            result["detail"] = "HTTP %s" % status
            return result
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["detail"] = "HTTP %s" % exc.code
        return result
    except urllib.error.URLError as exc:
        result["detail"] = "сетевая ошибка: %s" % exc.reason
        return result
    except ssl.SSLError as exc:
        result["detail"] = "SSL-ошибка: %s" % exc
        return result
    except Exception as exc:  # сторож не должен падать от одного странного ответа
        result["detail"] = "ошибка проверки: %s" % exc
        return result


def run_checks(config):
    timeout = int(config.get("timeout_seconds", DEFAULT_TIMEOUT))
    results = []
    for index, target in enumerate(config.get("targets", []), start=1):
        if not isinstance(target, dict):
            results.append({
                "name": "target-%d" % index,
                "url": "",
                "ok": False,
                "status": None,
                "detail": "элемент targets должен быть объектом",
            })
            continue
        name = str(target.get("name") or target.get("url") or ("target-%d" % index))
        url = target.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            results.append({
                "name": name,
                "url": str(url or ""),
                "ok": False,
                "status": None,
                "detail": "не задан корректный URL",
            })
            continue
        expect_status = int(target.get("expect_status", 200))
        production = bool(target.get("production", True))
        check_noindex = bool(target.get("check_noindex", production))
        results.append(check_url(name, url, timeout, expect_status, check_noindex))

        healthz = target.get("healthz")
        if isinstance(healthz, str) and healthz.strip():
            health_name = "%s / healthz" % name
            health_expect = int(target.get("healthz_expect_status", 200))
            results.append(check_url(health_name, healthz.strip(), timeout, health_expect, False))
    return results


def print_results(results):
    for item in results:
        mark = "OK" if item["ok"] else "FAIL"
        line = "[%s] %s - %s" % (mark, item["name"], item["detail"])
        if item.get("url"):
            line += " - %s" % item["url"]
        print(line)


def failures_of(results):
    return [item for item in results if not item["ok"]]


def send_telegram(text):
    token = os.environ.get("WATCHDOG_TG_TOKEN", "").strip()
    chat_id = os.environ.get("WATCHDOG_TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("WARN: WATCHDOG_TG_TOKEN или WATCHDOG_TG_CHAT_ID не заданы, Telegram не отправлен", file=sys.stderr)
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT, context=make_ssl_context()) as response:
            if response.getcode() == 200:
                print("Telegram: отправлено")
                return True
            print("WARN: Telegram ответил HTTP %s" % response.getcode(), file=sys.stderr)
            return False
    except urllib.error.HTTPError as exc:
        # Тело ответа Telegram может содержать описание ошибки, но не токен.
        print("WARN: Telegram HTTP %s" % exc.code, file=sys.stderr)
        return False
    except Exception as exc:
        print("WARN: Telegram недоступен: %s" % exc, file=sys.stderr)
        return False


def success_message(results):
    return "%s. Проверок: %d. %s" % (
        SUCCESS_TEXT,
        len(results),
        time.strftime("%Y-%m-%d %H:%M"),
    )


def failure_message(results):
    lines = ["Сторож: проблемы (%s)" % time.strftime("%Y-%m-%d %H:%M")]
    for item in results:
        lines.append("- %s: %s" % (item["name"], item["detail"]))
    return "\n".join(lines)


def notify(text, dry_run):
    if dry_run:
        print("Telegram: dry-run, сообщение не отправлено")
        print("Текст для Telegram: %s" % text.replace("\n", " | "))
        return True
    return send_telegram(text)


def main(argv):
    options = parse_args(argv)
    config = load_config(options["config"])
    retry_wait = int(config.get("retry_wait_seconds", DEFAULT_RETRY_WAIT))
    dry_run = options["dry_run"]
    once = options["once"]

    print("=== Сторож: проверка %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Конфиг: %s" % options["config"])
    results = run_checks(config)
    print_results(results)
    failures = failures_of(results)

    if failures and not once and not dry_run:
        print("Есть падения. Повторная проверка через %d сек." % retry_wait)
        time.sleep(retry_wait)
        print("=== Повторная проверка ===")
        results = run_checks(config)
        print_results(results)
        failures = failures_of(results)
    elif failures and (once or dry_run):
        print("Режим без ожидания: повторная проверка не выполняется.")

    if failures:
        message = failure_message(failures)
        print(message)
        notify(message, dry_run)
        return 1

    message = success_message(results)
    print(message)
    notified = notify(message, dry_run)
    return 0 if notified else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
