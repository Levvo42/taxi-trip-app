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

# ---------------------------
# Load & Save Settings (Tariffs + Predefined Locations)
# ---------------------------

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # Default values if file doesn't exist
        return {
            "tariffs": {
                "Taxa 1 (Småbil)": {"start": 59, "km": 22.1, "hour": 660},
                "Taxa 2 (Storbils)": {"start": 79, "km": 28.6, "hour": 720},
            },
            "predefined": {
                "Åre Airport": 995,
                "Staff Housing - Årevägen": 275,
                "Staff Housing - Brattland": 325
            }
        }

def save_settings(data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Load current settings into memory
data = load_settings()
user_tariffs = data['tariffs']
PREDEFINED_LOCATIONS = data['predefined']

# ---------------------------
# Derived Tariffs Calculation
# ---------------------------

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

# ---------------------------
# Google Maps APIs
# ---------------------------

def get_travel_details(origin, destination):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': API_KEY,
        'X-Goog-FieldMask': 'routes.duration,routes.distanceMeters'
    }
    body = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE"
    }

    response = requests.post(url, headers=headers, json=body)
    result = response.json()

    try:
        route = result['routes'][0]
        duration_sec = int(route['duration'].rstrip('s'))
        distance_m = int(route['distanceMeters'])
        duration_min = round(duration_sec / 60, 2)
        distance_km = round(distance_m / 1000, 2)
        return duration_min, distance_km
    except (KeyError, IndexError, ValueError) as e:
        print("Route API parsing error:", e)
        return None, None

def generate_static_map_url(origin, destination):
    base_url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        "size": "600x300",
        "markers": [
            f"color:green|label:A|{origin}",
            f"color:red|label:B|{destination}"
        ],
        "path": f"color:0x0000ff|weight:5|{origin}|{destination}",
        "key": API_KEY
    }
    marker_str = "&".join([f"markers={quote(m)}" for m in params["markers"]])
    path_str = f"path={quote(params['path'])}"
    return f"{base_url}?size={params['size']}&{marker_str}&{path_str}&key={params['key']}"

# ---------------------------
# Pricing & Formatting
# ---------------------------

def calculate_price(duration_min, distance_km, start_cost, km_cost, hourly_cost):
    per_km_cost = km_cost * distance_km
    per_hour_cost = (duration_min / 60) * hourly_cost
    total_cost = round(start_cost + per_km_cost + per_hour_cost)
    return total_cost

def format_duration(minutes):
    hours = int(minutes) // 60
    mins = int(minutes) % 60
    return f"{hours}h {mins}min" if hours else f"{mins}min"

# ---------------------------
# Routes
# ---------------------------

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    origin = ""
    destination = ""
    calculations = []
    tariffs = calculate_derived_tariffs()

    if request.method == 'POST':
        origin = request.form['origin']
        destination = request.form['destination']

        duration, distance = get_travel_details(origin, destination)

        if duration is not None and distance is not None:
            for name, tariff in tariffs.items():
                cost = calculate_price(duration, distance, tariff['start'], tariff['km'], tariff['hour'])
                calculations.append({
                    "tariff": name,
                    "total_cost": cost
                })

            result = {
                "origin": origin,
                "destination": destination,
                "duration": format_duration(duration),
                "distance": round(distance),
                "calculations": calculations,
                "map_url": generate_static_map_url(origin, destination)
            }

    return render_template('index.html', result=result, origin=origin, destination=destination,
                           api_key=API_KEY, predefined=PREDEFINED_LOCATIONS)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    global user_tariffs, PREDEFINED_LOCATIONS

    if request.method == 'POST':
        # Update tariffs
        for key in user_tariffs:
            user_tariffs[key]['start'] = float(request.form.get(f"{key}_start", 0))
            user_tariffs[key]['km'] = float(request.form.get(f"{key}_km", 0))
            user_tariffs[key]['hour'] = float(request.form.get(f"{key}_hour", 0))

        # Update predefined locations
        updated_predefined = {}
        i = 1
        while True:
            name_key = f"pre_name_{i}"
            price_key = f"pre_price_{i}"
            delete_key = f"pre_delete_{i}"
            if name_key in request.form and price_key in request.form:
                if delete_key in request.form:
                    i += 1
                    continue  # skip if user marked this for deletion
                name = request.form.get(name_key).strip()
                price = request.form.get(price_key).strip()
                if name and price:
                    updated_predefined[name] = int(price)
                i += 1
            else:
                break
        PREDEFINED_LOCATIONS = updated_predefined

        # Add new location if provided
        new_name = request.form.get("new_pre_name", "").strip()
        new_price = request.form.get("new_pre_price", "").strip()
        if new_name and new_price.isdigit():
            PREDEFINED_LOCATIONS[new_name] = int(new_price)

        # Save everything
        save_settings({
            "tariffs": user_tariffs,
            "predefined": PREDEFINED_LOCATIONS
        })

        return redirect(url_for('index'))

    return render_template('settings.html', tariffs=user_tariffs, predefined=PREDEFINED_LOCATIONS)

# ---------------------------
# Run the App
# ---------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
