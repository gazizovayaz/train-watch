"""
Train watcher: Bangkok (Krung Thep Aphiwat) -> Chiang Mai, 2nd class sleeper, new CNR coaches only.
Checks the official SRT D-Ticket site and sends a Telegram message
when 2nd-class sleeper berths appear.

Settings come from environment variables (set in the workflow file):
  DATE_FROM, DATE_TO   travel dates, format YYYY-MM-DD
  MIN_SEATS            how many berths you need in one train (for the "enough" mark)
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   from GitHub Secrets
"""

import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

BASE = "https://dticket.railway.co.th/DTicketPublicWeb/"
FROM_STATION = "1679"  # Krung Thep Aphiwat Central Terminal
TO_STATION = "74"      # Chiang Mai
STATE_FILE = "state.json"

DATE_FROM = date.fromisoformat(os.environ.get("DATE_FROM", "2026-11-01"))
DATE_TO = date.fromisoformat(os.environ.get("DATE_TO", "2026-11-30"))
MIN_SEATS = int(os.environ.get("MIN_SEATS", "4"))
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BKK = timezone(timedelta(hours=7))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}


def telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true"},
        timeout=20,
    )
    print("telegram:", r.status_code)


def start_session(first_day):
    """Open the site and run one search, like a person would, to get a session."""
    s = requests.Session()
    s.headers.update({"User-Agent": HEADERS["User-Agent"]})
    s.get(BASE + "home/Home", timeout=30)
    s.post(BASE + "booking/booking", timeout=30, data={
        "tripType": "1",
        "provinceStart": FROM_STATION,
        "provinceEnd": TO_STATION,
        "date-start": first_day.strftime("%d/%m/%Y"),
        "date-end": "",
        "seatperson": "1",
        "lineId": "1",
    })
    return s


def api(s, path, data):
    r = s.post(BASE + path, data=data, headers=HEADERS, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("errorCode") != "S_SUCCESS":
        raise RuntimeError(f"{path}: {j.get('errorCode')} {j.get('errorMessage')}")
    return j["data"]


def check_day(s, day):
    """Return {train_no: (dep_time, free_2nd_class_sleeper_berths)} for one date."""
    trips = api(s, "booking/booking/getTrip", {
        "tripType": "1",
        "provinceStartId": FROM_STATION,
        "provinceEndId": TO_STATION,
        "dateStart": day.strftime("%d/%m/%Y"),
        "dateEnd": "",
    }) or []
    out = {}
    for t in trips:
        time.sleep(0.7)  # be gentle with the site
        coach = api(s, "booking/booking/getTrainCoach", {
            "tripId": t["tripId"],
            "provinceStartId": FROM_STATION,
            "provinceEndId": TO_STATION,
        }) or {}
        free = sum(
            int(c.get("availableSeatCount") or 0)
            for c in coach.get("results", [])
            if c.get("coachClass") == "2" and c.get("coachSeatType") == "1"  # 2nd class, sleeping
            and ".CN" in (c.get("coachDesc") or "")  # only new CNR coaches (red seats)
        )
        out[t["trainNo"]] = (t["departureTime"], free)
    return out


def main():
    today = datetime.now(BKK).date()
    first = max(DATE_FROM, today)
    if first > DATE_TO:
        print("All dates are in the past. Nothing to check.")
        return

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}

    s = start_session(first)
    new_state, found = {}, []
    errors = 0
    day = first
    while day <= DATE_TO:
        try:
            for train, (dep, free) in check_day(s, day).items():
                key = f"{day.isoformat()}#{train}"
                new_state[key] = free
                if free > state.get(key, 0):  # only tell when more berths appear
                    found.append((day, train, dep, free))
        except Exception as e:  # noqa: BLE001
            errors += 1
            print("error on", day, e)
        day += timedelta(days=1)

    if found:
        lines = ["🚆 Появились места: новый поезд CNR, 2-й класс, спальный, Бангкок → Чиангмай", ""]
        for day, train, dep, free in found:
            mark = "✅ хватит всем" if free >= MIN_SEATS else "⚠️ мало"
            lines.append(f"{day:%d.%m (%a)} · поезд {train} · {dep} · мест: {free} {mark}")
        lines += ["", "Бронировать: https://dticket.railway.co.th"]
        telegram("\n".join(lines))

    # Warn once if the whole run failed (site changed or blocked us)
    days_total = (DATE_TO - first).days + 1
    if errors == days_total and not state.get("_error_sent"):
        telegram("⚠️ Бот не смог проверить сайт D-Ticket. Возможно, сайт блокирует GitHub или изменился.")
        new_state["_error_sent"] = True
    elif errors == days_total:
        new_state["_error_sent"] = True

    with open(STATE_FILE, "w") as f:
        json.dump(new_state, f, indent=1, sort_keys=True)
    print(f"checked {days_total} days, errors {errors}, new finds {len(found)}")

    # When you press "Run workflow" by hand, send a short report so you know it works
    if os.environ.get("RUN_KIND") == "workflow_dispatch":
        free_now = {k: v for k, v in new_state.items() if not k.startswith("_") and v > 0}
        telegram(f"🤖 Тестовый запуск. Проверено дней: {days_total}, ошибок: {errors}. "
                 f"Поездов со свободными местами сейчас: {len(free_now)}.")


if __name__ == "__main__":
    main()
