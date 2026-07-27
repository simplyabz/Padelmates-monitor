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

BASE_URL = "https://nestjs-production-fargate.padelmates.io"

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
    return {"notified": [], "booked": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def get_activities(start_dt, end_dt):
    url = f"{BASE_URL}/webportal/getClubActivityRecordsWithoutAuth/{CLUB_ID}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        all_sessions = r.json()
        start_ms = to_ms(start_dt)
        end_ms = to_ms(end_dt)
        filtered = [s for s in all_sessions if start_ms <= s.get("start_datetime", 0) <= end_ms]
        print(f"Fetched {len(all_sessions)} total, {len(filtered)} in target week")
        return filtered
    except Exception as e:
        print(f"Activities error: {e}")
        return []

def is_target_session(title):
    return any(target in title.lower() for target in TARGET_SESSIONS)

def book_activity(token, session):
    activity_id = session.get("_id")
    title = session.get("title", "Unknown")
    start_ms = session.get("start_datetime")
    end_ms = session.get("stop_datetime")
    price = session.get("payment_per_person", 0)

    print(f"Attempting to book: {title} (£{price})")

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
        r = requests.post(f"{BASE_URL}/payment/createPaymentIntent", json=payload, headers=auth_headers(token), timeout=15)
        r.raise_for_status()
        intent_data = r.json()
        print(f"Payment intent: {str(intent_data)[:300]}")

        payment_intent_id = (
            intent_data.get("paymentIntentId") or
            intent_data.get("id") or
            (intent_data.get("data") or {}).get("paymentIntentId")
        )

        if not payment_intent_id:
            print(f"No payment intent ID in: {intent_data}")
            return False

        r2 = requests.get(f"{BASE_URL}/payment/check-charge-auto-join-v2",
            params={"paymentintentid": payment_intent_id}, headers=auth_headers(token), timeout=15)
        r2.raise_for_status()
        print(f"Booking result: {str(r2.json())[:300]}")
        return True

    except Exception as e:
        print(f"Booking failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Error detail: {e.response.text[:300]}")
        return False

def send_notification(title, message):
    try:
        requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8").decode("latin-1", errors="replace"),
                "Priority": "urgent",
                "Tags": "white_check_mark",
                "Content-Type": "text/plain; charset=utf-8",
            },
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
    notified = set(state.get("notified", []))
    booked = set(state.get("booked", []))
    run_until = time.time() + 55

    while time.time() < run_until:
        print(f"\nChecking at {datetime.now(LONDON_TZ).strftime('%H:%M:%S')}...")
        activities = get_activities(target_start, target_end)

        if activities:
            new_sessions = [a for a in activities if a.get("_id") not in notified]
            if new_sessions:
                names = [a.get("title", "Unknown") for a in new_sessions]
                send_notification(
                    title=f"🎾 {len(new_sessions)} new sessions released!",
                    message=f"Week of {target_start.strftime('%d %b')}:\n" +
                            "\n".join(f"• {n}" for n in names[:10]) +
                            f"\n\npadelmates.se/club/{CLUB_ID}"
                )
                for a in new_sessions:
                    notified.add(a.get("_id"))

            for a in activities:
                act_id = a.get("_id")
                title = a.get("title", "")
                is_full = a.get("current_no_of_players", 0) >= a.get("no_of_players", 999)
                if act_id in booked or not is_target_session(title) or is_full:
                    continue
                print(f"Target session available: {title}")
                success = book_activity(token, a)
                if success:
                    booked.add(act_id)
                    send_notification(
                        title=f"✅ Booked: {title}!",
                        message=f"Booked and paid £{a.get('payment_per_person')} for {title} — week of {target_start.strftime('%d %b')}!"
                    )
                else:
                    send_notification(
                        title=f"⚠️ Booking failed: {title}",
                        message=f"Found {title} but auto-booking failed. Book manually!\npadelmates.se/club/{CLUB_ID}"
                    )
        else:
            print("No sessions found for target week yet")

        state["notified"] = list(notified)
        state["booked"] = list(booked)
        save_state(state)

        remaining = run_until - time.time()
        if remaining > CHECK_INTERVAL:
            time.sleep(CHECK_INTERVAL)
        else:
            break

if __name__ == "__main__":
    main()
