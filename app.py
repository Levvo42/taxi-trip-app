import os
import requests
from flask import Flask, request, render_template
from dotenv import load_dotenv
import math

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")
API_KEY = os.getenv('GOOGLE_API_KEY')

# Define fixed tariffs
TARIFFS = {
    "Taxa 1 (Småbil)":      {"start": 59, "km": 22.1, "hour": 660},
    "Taxa 2 (Storbils)":    {"start": 79, "km": 28.6, "hour": 720},
    "Taxa 4 (Småbil Rabatt)": {"start": 49, "km": 19, "hour": 561},
    "Taxa 5 (Storbils Rabatt)": {"start": 68, "km": 24.3, "hour": 612},
}

# Fetch travel details from Google Routes API
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
    except:
        return None, None

# Generate a JS map config for frontend to use with Google Maps JS API
def generate_map_data(origin, destination):
    return {
        "origin": origin,
        "destination": destination,
        "api_key": API_KEY
    }

# Calculate price using provided settings
def calculate_price(duration_min, distance_km, start_cost, km_cost, hourly_cost):
    per_km_cost = km_cost * distance_km
    per_hour_cost = (duration_min / 60) * hourly_cost
    total_cost = round(start_cost + per_km_cost + per_hour_cost)  # round to nearest whole kr
    return total_cost

# Format minutes as "Xh Ymin"
def format_duration(minutes):
    hours = int(minutes) // 60
    mins = int(minutes) % 60
    return f"{hours}h {mins}min" if hours else f"{mins}min"

# Web interface route
@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    origin = ""
    destination = ""
    calculations = []
    map_data = None

    if request.method == 'POST':
        origin = request.form['origin']
        destination = request.form['destination']

        duration, distance = get_travel_details(origin, destination)

        if duration is not None and distance is not None:
            for name, tariff in TARIFFS.items():
                cost = calculate_price(duration, distance, tariff['start'], tariff['km'], tariff['hour'])
                calculations.append({
                    "tariff": name,
                    "total_cost": cost
                })

            map_data = generate_map_data(origin, destination)

            result = {
                "origin": origin,
                "destination": destination,
                "duration": format_duration(duration),  # Format to "Hh Mmin"
                "distance": round(distance),  # Round only for display
                "calculations": calculations
            }

    return render_template('index.html', result=result, origin=origin, destination=destination, map_data=map_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
