import os
import json
import requests
from flask import Flask, request, render_template, redirect, url_for
from dotenv import load_dotenv
from urllib.parse import quote

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")
API_KEY = os.getenv('GOOGLE_API_KEY')

SETTINGS_FILE = 'settings.json'

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {
            "tariffs": {
                "Taxa 1 (Småbil)": {"start": 59, "km": 22.1, "hour": 660},
                "Taxa 2 (Storbils)": {"start": 79, "km": 28.6, "hour": 720}
            },
            "predefined": []
        }

def save_settings(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

data = load_settings()
user_tariffs = data['tariffs']
PREDEFINED_ROUTES = data['predefined']

def calculate_derived_tariffs():
    t1 = user_tariffs["Taxa 1 (Småbil)"]
    t2 = user_tariffs["Taxa 2 (Storbils)"]
    return {
        **user_tariffs,
        "Taxa 4 (Småbil Rabatt)": {
            "start": round(t1['start'] * 0.83, 2),
            "km": round(t1['km'] * 0.86, 2),
            "hour": round(t1['hour'] * 0.85, 2),
        },
        "Taxa 5 (Storbils Rabatt)": {
            "start": round(t2['start'] * 0.86, 2),
            "km": round(t2['km'] * 0.85, 2),
            "hour": round(t2['hour'] * 0.85, 2),
        }
    }

def get_travel_details(origin, destination):
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": origin,
        "destination": destination,
        "mode": "driving",
        "key": API_KEY
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()

        if data['status'] == "OK":
            route = data['routes'][0]['legs'][0]
            duration_sec = route['duration']['value']  # in seconds
            distance_m = route['distance']['value']    # in meters

            duration_min = duration_sec / 60
            distance_km = distance_m / 1000
            return duration_min, distance_km
        else:
            print("Directions API error:", data['status'])
            return None, None
    except Exception as e:
        print("Error fetching directions:", e)
        return None, None

def generate_static_map_url(origin, destination):
    # OBS! Vi använder en vanlig rutt med embed istället för statisk bild
    base_url = "https://www.google.com/maps/embed/v1/directions"
    params = {
        "origin": origin,
        "destination": destination,
        "key": API_KEY,
        "mode": "driving"
    }
    query = "&".join([f"{k}={quote(str(v))}" for k, v in params.items()])
    return f"{base_url}?{query}", None, None  # Vi returnerar fortfarande 3 värden, för kompatibilitet


def calculate_price(duration_min, distance_km, start_cost, km_cost, hourly_cost):
    per_km_cost = km_cost * distance_km
    per_hour_cost = (duration_min / 60) * hourly_cost
    total_cost = round(start_cost + per_km_cost + per_hour_cost)
    return total_cost

def format_duration(minutes):
    hours = int(minutes) // 60
    mins = int(minutes) % 60
    return f"{hours}h {mins}min" if hours else f"{mins}min"

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    origin = ""
    destination = ""
    calculations = []
    tariffs = calculate_derived_tariffs()

    if request.method == 'POST':
        origin = request.form.get('origin', '')
        destination = request.form.get('destination', '')
        is_fixed = request.form.get('fixed_price') == '1'

        if is_fixed:
            title = request.form.get("title")
            price_small = request.form.get("price_small")
            price_large = request.form.get("price_large")

            if price_small:
                calculations.append({"tariff": "Taxa 1 (Småbil)", "total_cost": int(price_small)})
            if price_large:
                calculations.append({"tariff": "Taxa 2 (Storbils)", "total_cost": int(price_large)})

            duration, distance = get_travel_details(origin, destination)
            map_url, _, _ = generate_static_map_url(origin, destination)

            result = {
                "origin": origin,
                "destination": destination,
                "duration": format_duration(duration) if duration else "–",
                "distance": round(distance, 1) if distance else "–",
                "calculations": calculations,
                "map_url": map_url
            }

        else:
            duration, distance = get_travel_details(origin, destination)
            if duration is not None and distance is not None:
                for name, tariff in tariffs.items():
                    cost = calculate_price(duration, distance, tariff['start'], tariff['km'], tariff['hour'])
                    calculations.append({
                        "tariff": name,
                        "total_cost": cost
                    })
                map_url, _, _ = generate_static_map_url(origin, destination)
                result = {
                    "origin": origin,
                    "destination": destination,
                    "duration": format_duration(duration),
                    "distance": round(distance, 1),
                    "calculations": calculations,
                    "map_url": map_url
                }

    return render_template(
        'index.html',
        result=result,
        origin=origin,
        destination=destination,
        api_key=API_KEY,
        predefined_routes=PREDEFINED_ROUTES
    )

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    global user_tariffs, PREDEFINED_ROUTES

    if request.method == 'POST':
        for key in user_tariffs:
            user_tariffs[key]['start'] = float(request.form.get(f"{key}_start", 0))
            user_tariffs[key]['km'] = float(request.form.get(f"{key}_km", 0))
            user_tariffs[key]['hour'] = float(request.form.get(f"{key}_hour", 0))

        updated_routes = []
        i = 1
        while True:
            if f"pre_title_{i}" in request.form:
                if f"pre_delete_{i}" in request.form:
                    i += 1
                    continue
                route = {
                    "title": request.form.get(f"pre_title_{i}").strip(),
                    "from": request.form.get(f"pre_from_{i}").strip(),
                    "to": request.form.get(f"pre_to_{i}").strip(),
                    "price_small": int(request.form.get(f"pre_small_{i}", 0)),
                    "price_large": int(request.form.get(f"pre_large_{i}", 0))
                }
                updated_routes.append(route)
                i += 1
            else:
                break

        new_title = request.form.get("new_pre_title", "").strip()
        new_from = request.form.get("new_pre_from", "").strip()
        new_to = request.form.get("new_pre_to", "").strip()
        new_small = request.form.get("new_pre_small", "").strip()
        new_large = request.form.get("new_pre_large", "").strip()

        if new_title and new_to:
            route = {
                "title": new_title,
                "from": new_from,
                "to": new_to,
                "price_small": int(new_small) if new_small.isdigit() else 0,
                "price_large": int(new_large) if new_large.isdigit() else 0
            }
            updated_routes.append(route)

        PREDEFINED_ROUTES = updated_routes

        save_settings({
            "tariffs": user_tariffs,
            "predefined": PREDEFINED_ROUTES
        })

        return redirect(url_for('index'))

    return render_template(
        'settings.html',
        tariffs=user_tariffs,
        predefined=PREDEFINED_ROUTES,
        api_key=API_KEY
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)