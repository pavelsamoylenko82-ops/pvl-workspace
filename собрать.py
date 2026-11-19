import re
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent
CARDS_DIR = ROOT / "Карточки"
DELA_FILE = ROOT / "ДЕЛА.md"
PULT_FILE = ROOT / "ПУЛЬТ.md"

TODAY = date.today()

def parse_frontmatter(text: str) -> dict:
    meta = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta

def extract_steps(text: str, source_name: str) -> list:
    steps = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*\[\s*\]\s*(\d{4}-\d{2}-\d{2})\s+(.*)", line)
        if m:
            try:
                step_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                steps.append((step_date, source_name, m.group(2).strip()))
            except ValueError:
                pass
    return steps

def main():
    money_wait = []
    money_owe = []
    all_steps = []
    no_next_step = []
    silent_leads = []
    phones_map = {}

    if CARDS_DIR.exists():
        for card_file in sorted(CARDS_DIR.glob("*.md")):
            content = card_file.read_text(encoding="utf-8")
            meta = parse_frontmatter(content)
            name = card_file.stem
            status = meta.get("статус", "").lower()

            if meta.get("деньги_ждём") and meta["деньги_ждём"] not in ("0", "нет", "-"):
                money_wait.append(f"- **{name}**: ждём {meta['деньги_ждём']}")
            if meta.get("мы_должны") and meta["мы_должны"] not in ("0", "нет", "-"):
                money_owe.append(f"- **{name}**: должны {meta['мы_должны']}")

            phones_raw = meta.get("телефоны", "")
            for ph in re.findall(r"\+?\d[\d\-\(\)\s]{6,}\d", phones_raw):
                clean_ph = re.sub(r"\D", "", ph)
                if clean_ph:
                    phones_map.setdefault(clean_ph, []).append(name)

            last_contact = meta.get("последний_контакт", "")
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})", last_contact)
            if date_m and status in ("в работе", "ждём"):
                try:
                    lc_date = datetime.strptime(date_m.group(1), "%Y-%m-%d").date()
                    if (TODAY - lc_date).days > 14:
                        silent_leads.append(f"- **{name}** (молчит {(TODAY - lc_date).days} дн. с {lc_date})")
                except ValueError:
                    pass

            steps = extract_steps(content, name)
            all_steps.extend(steps)
            if status in ("в работе", "ждём") and not steps:
                no_next_step.append(f"- **{name}** (статус: {status}, нет шага с датой)")

    if DELA_FILE.exists():
        dela_content = DELA_FILE.read_text(encoding="utf-8")
        all_steps.extend(extract_steps(dela_content, "ДЕЛА"))

    overdue = [s for s in all_steps if s[0] < TODAY]
    today_steps = [s for s in all_steps if s[0] == TODAY]
    upcoming = [s for s in all_steps if s[0] > TODAY]
    duplicates = [f"- Телефон {ph}: {', '.join(names)}" for ph, names in phones_map.items() if len(names) > 1]

    out = [
        f"# ПУЛЬТ УПРАВЛЕНИЯ (обновлено: {TODAY.isoformat()})",
        "_Файл генерируется скриптом собрать.py. Руками не править._\n",
        "## 1. Деньги",
        *(money_wait if money_wait else ["- Ожидаемых оплат пока нет."]),
        *(money_owe if money_owe else []),
        "\n## 2. Просрочено",
        *([f"- [{d}] **{src}**: {txt}" for d, src, txt in sorted(overdue)] if overdue else ["- Просроченных задач нет."]),
        "\n## 3. Сегодня",
        *([f"- **{src}**: {txt}" for d, src, txt in today_steps] if today_steps else ["- На сегодня задач в расписании нет."]),
        "\n## 4. Ближайшие шаги",
        *([f"- [{d}] **{src}**: {txt}" for d, src, txt in sorted(upcoming)[:10]] if upcoming else ["- Пусто."]),
        "\n## 5. Контроль базы (молчуны >14 дн., без шага, дубли)",
        *(silent_leads + no_next_step + duplicates if (silent_leads or no_next_step or duplicates) else ["- Всё чисто."])
    ]

    PULT_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("ПУЛЬТ.md успешно пересобран.")

if __name__ == "__main__":
    main()