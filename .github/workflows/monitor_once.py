import requests
import os
import json
from datetime import datetime, timedelta
import pytz

CLUB_ID    = "788fa2c66535421aabc60fd27f941c42"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "rocketpadel-ilford-alerts")
NTFY_SERVER = "https://ntfy.sh"
LONDON_TZ  = pytz.timezone("Europe/London")
STATE_FILE = "notified_state.json"

TEST_DATE = "2026-05-11"

BASE_URL = "https://fastapi-production-fargate.padelmates.io"

def get_target_week():
    if TEST_DATE:
        target_monday = LONDON_TZ.localize(datetime.strptime(TEST_DATE, "%Y-%m-%d"))
        target_monday = target_monday.replace(hour=0, minute=0, second=0, microsecond=0)
        print(f"TEST MODE - checking week of {target_monday.strftime('%d %b %Y')}")
    else:
        now = datetime.now(LONDON_TZ)
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 0
        next_monday = now + timedelta(days=days_until_monday)
        target_monday = next_monday + timedelta(weeks=2)
        target_monday = target_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    target_sunday = target_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return target_monday, target_sunday

def to_ms(dt):
    return int(dt.timestamp() * 1000)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("notified", []))
    return set()

def save_state(notified):
    with open(STATE_FILE, "w") as f:
        json.dump({"notified": list(notified)}, f)

def check_court_slots(start_dt, end_dt):
    url = f"{BASE_URL}/player/player_booking/all_courts_slot_prices_v2"
    params = {"club_id": CLUB_ID, "start_datetime": to_ms(start_dt), "end_datetime": to_ms(end_dt)}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("allSlots", [])
    except Exception as e:
        print(f"Court slots error: {e}")
        return []

def check_activities(start_dt, end_dt):
    endpoints = ["/activity/get_activities", "/player/activity/get_activities", "/activity/club_activities", "/player/activities", "/activity/activities"]
    for path in endpoints:
        params = {"club_id": CLUB_ID, "start_datetime": to_ms(start_dt), "end_datetime": to_ms(end_dt)}
        try:
            r = requests.get(BASE_URL + path, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                activities = data if isinstance(data, list) else data.get("activities", data.get("data", []))
                if activities:
                    return activities
        except Exception:
            continue
    return []

def send_notification(title, message):
    try:
        requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "tennis,bell"}, timeout=10)
        print(f"Notification sent: {title}")
    except Exception as e:
        print(f"Notification failed: {e}")

def format_slots(slots):
    seen, lines = set(), []
    for s in slots:
        key = (s.get("startTime"), s.get("courtName"))
        if key not in seen:
            seen.add(key)
            lines.append(f"• {s.get('courtName')} at {s.get('startTime')} — £{s.get('price')}")
    return "\n".join(lines[:10])

def main():
    now = datetime.now(LONDON_TZ)
    print(f"Running check at {now.strftime('%A %d %b %Y %H:%M')} London time")
    target_start, target_end = get_target_week()
    notified = load_state()
    slots = check_court_slots(target_start, target_end)
    slot_keys = set(f"{s.get('startDatetime')}_{s.get('courtName')}" for s in slots)
    new_slots = slot_keys - notified
    if new_slots:
        send_notification(title=f"🎾 {len(slots)} Rocket Padel slots just dropped!", message=f"Week of {target_start.strftime('%d %b')}:\n{format_slots(slots)}\n\nBook: padelmates.se/club/788fa2c66535421aabc60fd27f941c42")
        notified |= slot_keys
    else:
        print(f"No new court slots found")
    activities = check_activities(target_start, target_end)
    act_keys = set(f"act_{a.get('start_datetime', a.get('startDatetime', ''))}" for a in activities)
    if act_keys - notified:
        send_notification(title=f"🎾 {len(activities)} coaching/activities just released!", message=f"New sessions for week of {target_start.strftime('%d %b')} just dropped!\n\nBook: padelmates.se/club/788fa2c66535421aabc60fd27f941c42")
        notified |= act_keys
    else:
        print("No new activities found")
    save_state(notified)

if __name__ == "__main__":
    main()
