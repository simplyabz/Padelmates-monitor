import requests
import os
import json
import time
from datetime import datetime, timedelta
import pytz

CLUB_ID         = "788fa2c66535421aabc60fd27f941c42"
NTFY_TOPIC      = os.environ.get("NTFY_TOPIC", "rocketpadel-ilford-alerts")
NTFY_SERVER     = "https://ntfy.sh"
LONDON_TZ       = pytz.timezone("Europe/London")
STATE_FILE      = "notified_state.json"
FIREBASE_KEY    = "AIzaSyCTllK1OKm-YRcEeKXEc2KNcPtmZFZrqIk"
EMAIL           = os.environ.get("PADELMATES_EMAIL", "")
PASSWORD        = os.environ.get("PADELMATES_PASSWORD", "")
CHECK_INTERVAL  = 10
TEST_DATE       = "2026-07-28"

BASE_URL = "https://fastapi-production-fargate.padelmates.io"

def firebase_login():
    # Try Firebase first
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_KEY}"
    payload = {"email": EMAIL, "password": PASSWORD, "returnSecureToken": True, "clientType": "CLIENT_TYPE_WEB"}
    r = requests.post(url, json=payload, timeout=10)
    if r.status_code == 200:
        token = r.json().get("idToken")
        print("Firebase login successful")
    else:
        print(f"Firebase failed ({r.status_code}), trying PadelMates login...")
        r2 = requests.post(
            "https://nestjs-production-fargate.padelmates.io/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
            timeout=10
        )
        r2.raise_for_status()
        data = r2.json()
        print(f"PadelMates login response: {str(data)[:200]}")
        token = data.get("accessToken") or data.get("token") or data.get("idToken")
        print("PadelMates login successful")
    return token

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

def check_court_slots(token, start_dt, end_dt):
    url = f"{BASE_URL}/player/player_booking/all_courts_slot_prices_v2"
    params = {"club_id": CLUB_ID, "start_datetime": to_ms(start_dt), "end_datetime": to_ms(end_dt)}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        print(f"Court slots response: {str(data)[:300]}")
        return data.get("allSlots", [])
    except Exception as e:
        print(f"Court slots error: {e}")
        return []

def check_activities(token, start_dt, end_dt):
    endpoints = ["/activity/get_activities", "/player/activity/get_activities", "/activity/club_activities", "/player/activities", "/activity/activities"]
    headers = {"Authorization": f"Bearer {token}"}
    for path in endpoints:
        params = {"club_id": CLUB_ID, "start_datetime": to_ms(start_dt), "end_datetime": to_ms(end_dt)}
        try:
            r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                print(f"Activity response from {path}: {str(data)[:300]}")
                activities = data if isinstance(data, list) else data.get("activities", data.get("data", []))
                if activities:
                    return activities
        except Exception as e:
            print(f"{path} error: {e}")
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
            lines.append(f"• {s.get('courtName')} at {s.get('startTime')} - £{s.get('price')}")
    return "\n".join(lines[:10])

def main():
    now = datetime.now(LONDON_TZ)
    print(f"Running at {now.strftime('%A %d %b %Y %H:%M:%S')} London time")

    try:
        token = firebase_login()
    except Exception as e:
        print(f"Login failed: {e}")
        return

    target_start, target_end = get_target_week()
    notified = load_state()
    run_until = time.time() + 55

    while time.time() < run_until:
        check_time = datetime.now(LONDON_TZ)
        print(f"\nChecking at {check_time.strftime('%H:%M:%S')}...")

        slots = check_court_slots(token, target_start, target_end)
        slot_keys = set(f"{s.get('startDatetime')}_{s.get('courtName')}" for s in slots)
        new_slots = slot_keys - notified

        if new_slots:
            send_notification(
                title=f"Rocket Padel {len(slots)} slots dropped!",
                message=f"Week of {target_start.strftime('%d %b')}:\n{format_slots(slots)}\n\nBook: padelmates.se/club/788fa2c66535421aabc60fd27f941c42"
            )
            notified |= slot_keys
            save_state(notified)

        activities = check_activities(token, target_start, target_end)
        act_keys = set(f"act_{a.get('start_datetime', a.get('startDatetime', ''))}" for a in activities)
        new_acts = act_keys - notified

        if new_acts:
            send_notification(
                title=f"Rocket Padel {len(activities)} activities dropped!",
                message=f"New sessions for week of {target_start.strftime('%d %b')} just dropped!\n\nBook: padelmates.se/club/788fa2c66535421aabc60fd27f941c42"
            )
            notified |= act_keys
            save_state(notified)

        if not new_slots and not new_acts:
            print(f"No new sessions ({len(slots)} slots, {len(activities)} activities)")

        remaining = run_until - time.time()
        if remaining > CHECK_INTERVAL:
            time.sleep(CHECK_INTERVAL)
        else:
            break

if __name__ == "__main__":
    main()
