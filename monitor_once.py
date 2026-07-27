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
CHECK_INTERVAL  = 10
TEST_DATE       = None

TARGET_SESSIONS = [
    "monday club night",
    "late night social",
    "padel n pizza",
]

BASE_URL   = "https://nestjs-production-fargate.padelmates.io"
FAST_URL   = "https://fastapi-production-fargate.padelmates.io"

def firebase_login():
    refresh_token = os.environ.get("PADELMATES_REFRESH_TOKEN", "")
    print(f"Refresh token length: {len(refresh_token)}")
    url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_KEY}"
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    token = r.json().get("id_token")
    print("Login successful via refresh token")
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
            return json.load(f)
    return {"notified_slots": [], "booked": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def get_activities(token, start_dt, end_dt):
    endpoints = [
        f"{BASE_URL}/tournament/getClubActivities",
        f"{BASE_URL}/tournament/getActivities",
        f"{BASE_URL}/activity/getActivities",
        f"{FAST_URL}/activity/get_activities",
        f"{FAST_URL}/player/activity/get_activities",
    ]
    params = {
        "clubId": CLUB_ID,
        "club_id": CLUB_ID,
        "startDatetime": to_ms(start_dt),
        "endDatetime": to_ms(end_dt),
        "start_datetime": to_ms(start_dt),
        "end_datetime": to_ms(end_dt),
    }
    for url in endpoints:
        try:
            r = requests.get(url, params=params, headers=auth_headers(token), timeout=10)
            if r.status_code == 200:
                data = r.json()
                activities = data if isinstance(data, list) else data.get("data", data.get("activities", []))
                if activities:
                    print(f"Activities found at: {url} ({len(activities)} sessions)")
                    return activities
        except Exception as e:
            print(f"{url} error: {e}")
    return []

def is_target_session(name):
    name_lower = name.lower()
    for target in TARGET_SESSIONS:
        if target in name_lower:
            return True
    return False

def book_activity(token, activity):
    activity_id = activity.get("_id") or activity.get("id")
    name = activity.get("name", "Unknown")
    start_time = activity.get("startDatetime") or activity.get("start_datetime")
    end_time = activity.get("endDatetime") or activity.get("end_datetime")
    price = activity.get("price", 0)

    if isinstance(start_time, str):
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            start_ms = to_ms(dt)
            dt_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            end_ms = to_ms(dt_end)
        except:
            start_ms = start_time
            end_ms = end_time
    else:
        start_ms = start_time
        end_ms = end_time

    print(f"Attempting to book: {name} (ID: {activity_id})")

    payload = {
        "payFor": "training",
        "clubId": CLUB_ID,
        "amount": price,
        "sourceId": activity_id,
        "startTime": start_ms,
        "endTime": end_ms,
        "gameType": "padel",
        "paymentMethod": "cards",
        "cardDetails": None,
        "discountCode": "",
        "split_players_ids": [],
        "membershipData": {},
        "isSupportAutoJoinFlow": True,
        "autoJoinAdditionalInfo": {
            "discountPercentageCoupon": 0,
            "individualMembership": True,
            "individualCoupon": False,
            "original_price": price
        }
    }

    try:
        r = requests.post(
            f"{BASE_URL}/payment/createPaymentIntent",
            json=payload,
            headers=auth_headers(token),
            timeout=15
        )
        r.raise_for_status()
        intent_data = r.json()
        print(f"Payment intent: {str(intent_data)[:300]}")

        payment_intent_id = (
            intent_data.get("paymentIntentId") or
            intent_data.get("id") or
            intent_data.get("data", {}).get("paymentIntentId")
        )

        if not payment_intent_id:
            print(f"No payment intent ID found: {intent_data}")
            return False

        r2 = requests.get(
            f"{BASE_URL}/payment/check-charge-auto-join-v2",
            params={"paymentintentid": payment_intent_id},
            headers=auth_headers(token),
            timeout=15
        )
        r2.raise_for_status()
        result = r2.json()
        print(f"Booking result: {str(result)[:300]}")
        return True

    except Exception as e:
        print(f"Booking failed for {name}: {e}")
        return False

def send_notification(title, message):
    try:
        requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "tennis,bell"},
            timeout=10
        )
        print(f"Notification sent: {title}")
    except Exception as e:
        print(f"Notification failed: {e}")

def main():
    now = datetime.now(LONDON_TZ)
    print(f"Running at {now.strftime('%A %d %b %Y %H:%M:%S')} London time")

    try:
        token = firebase_login()
    except Exception as e:
        print(f"Login failed: {e}")
        return

    target_start, target_end = get_target_week()
    state = load_state()
    notified_slots = set(state.get("notified_slots", []))
    booked = set(state.get("booked", []))

    run_until = time.time() + 55

    while time.time() < run_until:
        check_time = datetime.now(LONDON_TZ)
        print(f"\nChecking at {check_time.strftime('%H:%M:%S')}...")

        activities = get_activities(token, target_start, target_end)

        if activities:
            new_activities = []
            for a in activities:
                act_id = a.get("_id") or a.get("id", "")
                key = f"act_{act_id}"
                if key not in notified_slots:
                    new_activities.append(a)
                    notified_slots.add(key)

            if new_activities:
                names = [a.get("name", "Unknown") for a in new_activities]
                send_notification(
                    title=f"🎾 {len(new_activities)} new sessions released!",
                    message=f"Week of {target_start.strftime('%d %b')}:\n" +
                            "\n".join(f"• {n}" for n in names[:10]) +
                            "\n\npadelmates.se/club/788fa2c66535421aabc60fd27f941c42"
                )

            for a in activities:
                act_id = a.get("_id") or a.get("id", "")
                name = a.get("name", "")
                if act_id in booked:
                    continue
                if is_target_session(name):
                    print(f"Target session found: {name}")
                    success = book_activity(token, a)
                    if success:
                        booked.add(act_id)
                        send_notification(
                            title=f"✅ Booked: {name}!",
                            message=f"Successfully booked and paid for {name} — week of {target_start.strftime('%d %b')}!"
                        )
                    else:
                        send_notification(
                            title=f"⚠️ Booking failed: {name}",
                            message=f"Found {name} but couldn't auto-book. Book manually now!"
                        )
        else:
            print("No activities found yet")

        state["notified_slots"] = list(notified_slots)
        state["booked"] = list(booked)
        save_state(state)

        remaining = run_until - time.time()
        if remaining > CHECK_INTERVAL:
            time.sleep(CHECK_INTERVAL)
        else:
            break

if __name__ == "__main__":
    main()
