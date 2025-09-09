import os, json, time, math, requests
from flask import Flask, request, render_template, redirect, url_for, flash
from dotenv import load_dotenv
from sheets_repo import (
    load_all as sheets_load_all,
    append_place, append_route_with_prices,
    delete_route as sheets_delete_route,
    delete_place as sheets_delete_place,
    update_route_row,
    update_place_latlng_by_title,
)

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")  # valfritt, om du har en /static
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")
API_KEY = os.getenv("GOOGLE_API_KEY")
SETTINGS_FILE = "settings.json"


@app.after_request
def add_no_store(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------- Settings (tariffer lokalt) ----------
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "tariffs": {
            "Taxa 1 (Småbil)": {"start": 0.0, "km": 0.0, "hour": 0.0},
            "Taxa 2 (Storbils)": {"start": 0.0, "km": 0.0, "hour": 0.0},
        }
    }


def save_settings(data):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_FILE)


user_tariffs = load_settings().get("tariffs", {})


# ---------- Helpers ----------
def make_routes_bidirectional(routes):
    out, seen = [], set()
    for r in routes:
        k1 = (r.get("from"), r.get("to"))
        k2 = (r.get("to"), r.get("from"))
        if k1 not in seen:
            out.append(r)
            seen.add(k1)
        if k2 not in seen:
            out.append({
                **r,
                "route_id": r.get("route_id"),
                "from": r.get("to"),
                "to": r.get("from"),
                "from_address": r.get("to_address"),
                "to_address": r.get("from_address"),
            })
            seen.add(k2)
    return out


SHEETS_CACHE = {"routes": [], "places": [], "loaded_at": 0.0}
SHEETS_TTL = 0  # sek – hämta var 5e sek från Sheets (justera senare om du vill cacha)


def refresh_sheets_cache(force=False):
    now = time.time()
    if force or (now - SHEETS_CACHE["loaded_at"] > SHEETS_TTL) or not SHEETS_CACHE["routes"]:
        try:
            sdata = sheets_load_all()
            SHEETS_CACHE["routes"] = sdata["routes"]
            SHEETS_CACHE["places"] = sdata.get("places", [])
            SHEETS_CACHE["loaded_at"] = now
        except Exception as e:
            print("⚠️ Sheets-läsfel:", e)


def get_predefined_routes():
    refresh_sheets_cache()
    return make_routes_bidirectional(SHEETS_CACHE["routes"])


def get_address_titles_from_sheets():
    refresh_sheets_cache()
    out = []
    for p in SHEETS_CACHE["places"]:
        out.append({
            "id": p.get("PlaceID", ""),
            "title": p.get("Title", ""),
            "address": p.get("Address", ""),
            "lat": p.get("Lat"),
            "lng": p.get("Lng"),
        })
    return out


def calculate_derived_tariffs():
    t1 = user_tariffs.get("Taxa 1 (Småbil)", {"start": 0.0, "km": 0.0, "hour": 0.0})
    t2 = user_tariffs.get("Taxa 2 (Storbils)", {"start": 0.0, "km": 0.0, "hour": 0.0})
    return {
        "Taxa 1 (Småbil)": {"start": float(t1["start"]), "km": float(t1["km"]), "hour": float(t1["hour"])},
        "Taxa 2 (Storbils)": {"start": float(t2["start"]), "km": float(t2["km"]), "hour": float(t2["hour"])},
        "Taxa 4 (Småbil Rabatt)": {
            "start": round(float(t1["start"]) * 0.83, 2),
            "km": round(float(t1["km"]) * 0.86, 2),
            "hour": round(float(t1["hour"]) * 0.85, 2),
        },
        "Taxa 5 (Storbils Rabatt)": {
            "start": round(float(t2["start"]) * 0.86, 2),
            "km": round(float(t2["km"]) * 0.85, 2),
            "hour": round(float(t2["hour"]) * 0.85, 2),
        },
    }


# ---------- Google APIs ----------
def get_travel_details(origin, destination):
    """origin/destination kan vara 'place_id:XXXX', 'lat,lng' eller adress-sträng."""
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {"origin": origin, "destination": destination, "mode": "driving", "key": API_KEY}
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    try:
        if data.get("status") == "OK":
            leg = data["routes"][0]["legs"][0]
            return leg["duration"]["value"] / 60.0, leg["distance"]["value"] / 1000.0
        print("⚠️ Directions status:", data.get("status"))
    except Exception as e:
        print("🚨 Tolkningsfel:", e)
    return None, None


def geocode_address(address: str = None, place_id: str = None):
    """
    Returnerar (lat, lng, formatted_address).
    - Om place_id finns: använd Places Details (säkrast).
    - Annars: använd Geocoding (adress-sträng).
    """
    try:
        if place_id:
            # Places Details API ger oss geometry direkt från place_id
            url = "https://maps.googleapis.com/maps/api/place/details/json"
            params = {
                "place_id": place_id,
                "fields": "geometry,formatted_address",
                "language": "sv",
                "key": API_KEY,
            }
            resp = requests.get(url, params=params, timeout=20)
            data = resp.json()
            if data.get("status") == "OK" and data.get("result"):
                res = data["result"]
                loc = res["geometry"]["location"]
                return loc["lat"], loc["lng"], res.get("formatted_address", address or "")
            else:
                print("⚠️ Place Details status:", data.get("status"), data)

        # Fallback: Geocoding på adress-sträng (SE/NO-bias)
        if address:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": address,
                "key": API_KEY,
                "language": "sv",
                "components": "country:SE|country:NO",
            }
            resp = requests.get(url, params=params, timeout=20)
            data = resp.json()
            if data.get("status") == "OK" and data["results"]:
                res = data["results"][0]
                loc = res["geometry"]["location"]
                return loc["lat"], loc["lng"], res.get("formatted_address", address)
            else:
                print("⚠️ Geocoding status:", data.get("status"), data)
    except Exception as e:
        print("⚠️ geocode_address fel:", e)

    return None, None, address or ""



def generate_static_map_url(origin, destination):
    base = "https://www.google.com/maps/embed/v1/directions"
    return f"{base}?origin={origin}&destination={destination}&key={API_KEY}&mode=driving", None, None


# ---------- Pris/bilar ----------
def calculate_price(duration_min, distance_km, start_cost, km_cost, hourly_cost):
    total = float(start_cost) + (float(km_cost) * float(distance_km)) + (
                (float(duration_min) / 60.0) * float(hourly_cost))
    return round(total)


def format_duration(minutes):
    if minutes is None: return "–"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h}h {m}min" if h else f"{m}min"


def distribute_cars(passengers: int):
    if passengers <= 0: return 0, 0
    large = passengers // 8
    rem = passengers % 8
    small = math.ceil(rem / 4) if rem > 0 else 0
    return large, small


# ---------- Views ----------
@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    origin = destination = ""
    passenger_count = 0
    tariffs = calculate_derived_tariffs()

    if request.method == "POST":
        origin = request.form.get("origin", "").strip()
        destination = request.form.get("destination", "").strip()
        origin_pid = (request.form.get("origin_place_id") or "").strip()
        dest_pid = (request.form.get("destination_place_id") or "").strip()
        is_fixed = request.form.get("fixed_price") == "1"
        try:
            passenger_count = int(request.form.get("passengers", 0))
        except Exception:
            passenger_count = 0

        # Bygg säkra parametrar till Google: använd place_id om finns
        o_param = f"place_id:{origin_pid}" if origin_pid else origin
        d_param = f"place_id:{dest_pid}" if dest_pid else destination

        # FASTPRIS
        if is_fixed:
            routes = get_predefined_routes()
            matched = next((r for r in routes if r.get("from") == origin and r.get("to") == destination), None)
            if matched:
                from_addr = matched.get("from_address") or matched["from"]
                to_addr = matched.get("to_address") or matched["to"]

                # Om lat/lng finns i rutten, använd dem för 100% träffsäkerhet
                from_param = (f"{matched['from_lat']},{matched['from_lng']}"
                              if matched.get("from_lat") and matched.get("from_lng") else from_addr)
                to_param = (f"{matched['to_lat']},{matched['to_lng']}"
                            if matched.get("to_lat") and matched.get("to_lng") else to_addr)

                duration, distance = get_travel_details(from_param, to_param)
                map_url, _, _ = generate_static_map_url(from_param, to_param)
                rows = []
                for price in matched.get("prices", []):
                    min_p = int(price.get("min", 0))
                    max_p = price.get("max")
                    max_p = int(max_p) if max_p not in (None, "") else 10 ** 9
                    label = price.get("label", "Fastpris")
                    if passenger_count == 0 or (min_p <= passenger_count <= max_p):
                        if "total" in price:
                            cost = int(price["total"])
                        elif "price_per_person" in price:
                            base = passenger_count or min_p
                            cost = round(base * int(price["price_per_person"]))
                        else:
                            continue
                        rows.append({"tariff": label, "total_cost": cost})
                if rows:
                    result = {
                        "origin": matched["from"], "destination": matched["to"],
                        "duration": format_duration(duration),
                        "distance": round(distance, 1) if distance else "–",
                        "calculations": rows, "map_url": map_url,
                    }

        # TARIFF
        else:
            duration, distance = get_travel_details(o_param, d_param)
            if duration and distance:
                rows = []
                if passenger_count <= 0:
                    for name, t in tariffs.items():
                        rows.append({"tariff": name,
                                     "total_cost": calculate_price(duration, distance, t["start"], t["km"], t["hour"])})
                else:
                    n_large, n_small = distribute_cars(passenger_count)

                    def per_tariff(tname, count):
                        t = tariffs[tname]
                        unit = calculate_price(duration, distance, t["start"], t["km"], t["hour"])
                        return unit * count

                    if n_small > 0:
                        rows.append({"tariff": f"Småbil – Taxa 1 ×{n_small}",
                                     "total_cost": per_tariff("Taxa 1 (Småbil)", n_small)})
                        rows.append({"tariff": f"Småbil – Taxa 4 ×{n_small}",
                                     "total_cost": per_tariff("Taxa 4 (Småbil Rabatt)", n_small)})
                    if n_large > 0:
                        rows.append({"tariff": f"Storbils – Taxa 2 ×{n_large}",
                                     "total_cost": per_tariff("Taxa 2 (Storbils)", n_large)})
                        rows.append({"tariff": f"Storbils – Taxa 5 ×{n_large}",
                                     "total_cost": per_tariff("Taxa 5 (Storbils Rabatt)", n_large)})

                map_url, _, _ = generate_static_map_url(o_param, d_param)
                result = {
                    "origin": origin, "destination": destination,
                    "duration": format_duration(duration),
                    "distance": round(distance, 1),
                    "calculations": rows, "map_url": map_url,
                }

    return render_template(
        "index.html",
        result=result,
        origin=origin, destination=destination, passengers=passenger_count,
        api_key=API_KEY,
        predefined_routes=get_predefined_routes(),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    global user_tariffs

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "save_tariffs":
            updated = {}
            for key, vals in user_tariffs.items():
                start = request.form.get(f"{key}_start", vals.get("start", 0))
                km = request.form.get(f"{key}_km", vals.get("km", 0))
                hour = request.form.get(f"{key}_hour", vals.get("hour", 0))
                try:
                    updated[key] = {"start": float(start), "km": float(km), "hour": float(hour)}
                except Exception:
                    updated[key] = vals
            user_tariffs = updated
            data = load_settings()
            data["tariffs"] = user_tariffs
            save_settings(data)
            flash("Tariffer sparade.", "success")
            refresh_sheets_cache(force=True)
            return redirect(url_for("settings"))

        if action == "add_place":
            title = (request.form.get("place_title") or "").strip()
            address = (request.form.get("place_address") or "").strip()
            if not title or not address:
                flash("Titel och adress krävs.", "warning")
                refresh_sheets_cache(force=True)
                return redirect(url_for("settings"))
            pid = (request.form.get("place_place_id") or "").strip()
            lat, lng, fmt_addr = geocode_address(address, place_id=pid if pid else None)
            try:
                append_place(title, fmt_addr, lat, lng, aliases=request.form.get("place_aliases", "").strip())
                flash(f"Plats '{title}' tillagd.", "success")
            except Exception as e:
                flash(f"Kunde inte lägga till plats: {e}", "danger")
            refresh_sheets_cache(force=True)
            return redirect(url_for("settings"))

        if action == "add_route":
            from_title = (request.form.get("route_from_title") or "").strip()
            to_title = (request.form.get("route_to_title") or "").strip()
            if not from_title or not to_title:
                flash("Både 'Från' och 'Till' krävs för rutt.", "warning")
                refresh_sheets_cache(force=True)
                return redirect(url_for("settings"))

            # Hämta address + lat/lng från Places
            p_map = {p["title"]: {"address": p.get("address", ""), "lat": p.get("lat"), "lng": p.get("lng")}
                     for p in get_address_titles_from_sheets()}

            from_place = p_map.get(from_title, {})
            to_place = p_map.get(to_title, {})

            from_address = (request.form.get("route_from_address") or "").strip() or from_place.get("address", "")
            to_address = (request.form.get("route_to_address") or "").strip() or to_place.get("address", "")

            # Förifyll koordinater från platsen (om de redan finns i Places)
            flt = from_place.get("lat")
            fln = from_place.get("lng")
            tlt = to_place.get("lat")
            tln = to_place.get("lng")

            from_pid = (request.form.get("route_from_place_id") or "").strip()
            to_pid = (request.form.get("route_to_place_id") or "").strip()

            # Geokoda ENDAST om lat/lng saknas eller om användaren gav en explicit adress/place_id
            if (not flt or not fln) and (from_address or from_pid):
                flt, fln, faddr = geocode_address(from_address, place_id=from_pid if from_pid else None)
            else:
                # behåll den kända adressen som sparas till arket
                faddr = from_address

            if (not tlt or not tln) and (to_address or to_pid):
                tlt, tln, taddr = geocode_address(to_address, place_id=to_pid if to_pid else None)
            else:
                taddr = to_address

            labels = request.form.getlist("price_label[]")
            mins = request.form.getlist("price_min[]")
            maxs = request.form.getlist("price_max[]")
            totals = request.form.getlist("price_total[]")
            ppps = request.form.getlist("price_ppp[]")
            prices = []
            for i in range(len(labels)):
                label = (labels[i] or "").strip()
                if not label: continue
                min_v = mins[i].strip() if i < len(mins) else ""
                max_v = maxs[i].strip() if i < len(maxs) else ""
                total = totals[i].strip() if i < len(totals) else ""
                ppp = ppps[i].strip() if i < len(ppps) else ""
                if not min_v:
                    flash(f"Pris '{label}' måste ha Min.", "warning")
                    refresh_sheets_cache(force=True)
                    return redirect(url_for("settings"))
                if (total == "" and ppp == "") or (total != "" and ppp != ""):
                    flash(f"Pris '{label}' måste ha antingen Total eller Pris/Person.", "warning")
                    refresh_sheets_cache(force=True)
                    return redirect(url_for("settings"))
                price = {"label": label, "min": int(min_v)}
                if max_v != "": price["max"] = int(max_v)
                if total != "": price["total"] = int(total)
                if ppp != "": price["price_per_person"] = int(ppp)
                prices.append(price)

            create_reverse = (request.form.get("route_create_reverse") == "on")
            title = (request.form.get("route_title") or f"{from_title} → {to_title}").strip()
            try:
                res = append_route_with_prices(
                    from_title, to_title, faddr or from_address, taddr or to_address,
                    title=title, from_lat=flt, from_lng=fln, to_lat=tlt, to_lng=tln,
                    prices=prices
                )
                # Säkra att radens lat/lng/adresser faktiskt skrevs (ibland blir de tomma)
                try:
                    update_route_row(res["route_id"],
                                     from_addr=(faddr or from_address),
                                     to_addr=(taddr or to_address),
                                     from_lat=flt, from_lng=fln, to_lat=tlt, to_lng=tln)
                except Exception as e:
                    print("⚠️ update_route_row (forward) misslyckades:", e)

                # Om Places saknar lat/lng – fyll på nu (bekvämlighet)
                try:
                    if flt and fln:
                        update_place_latlng_by_title(from_title, flt, fln)
                    if tlt and tln:
                        update_place_latlng_by_title(to_title, tlt, tln)
                except Exception as e:
                    print("⚠️ update_place_latlng_by_title misslyckades:", e)

                if create_reverse:
                    append_route_with_prices(
                        to_title, from_title, taddr or to_address, faddr or from_address,
                        title=f"{to_title} → {from_title}",
                        from_lat=tlt, from_lng=tln, to_lat=flt, to_lng=fln,
                        prices=prices, group_id=res["group_id"]
                    )
                # Säkra lat/lng för returvägen också
                try:
                    # Hämta senaste route_id för returvägen genom att läsa om cache
                    refresh_sheets_cache(force=True)
                    # Leta upp rätt rutt via key (to→from) och samma group_id
                    rev = next((r for r in SHEETS_CACHE["routes"]
                                if r.get("from") == to_title and r.get("to") == from_title), None)
                    if rev and rev.get("route_id"):
                        update_route_row(rev["route_id"],
                                         from_addr=(taddr or to_address),
                                         to_addr=(faddr or from_address),
                                         from_lat=tlt, from_lng=tln, to_lat=flt, to_lng=fln)
                except Exception as e:
                    print("⚠️ update_route_row (reverse) misslyckades:", e)

                flash("Rutt(er) tillagda.", "success")
            except Exception as e:
                flash(f"Kunde inte lägga till rutt: {e}", "danger")
            refresh_sheets_cache(force=True)
            return redirect(url_for("settings"))

        if action == "delete_route":
            rid = (request.form.get("route_id") or "").strip()
            try:
                n1, n2 = sheets_delete_route(rid)
                flash(f"Raderade rutt ({n1} route-rad, {n2} prisrader).", "success")
            except Exception as e:
                flash(f"Kunde inte radera rutt: {e}", "danger")
            refresh_sheets_cache(force=True)
            return redirect(url_for("settings"))

        if action == "delete_place":
            pid = (request.form.get("place_id") or "").strip()
            try:
                sheets_delete_place(pid)
                flash("Plats raderad.", "success")
            except Exception as e:
                flash(f"Kunde inte radera plats: {e}", "danger")
            refresh_sheets_cache(force=True)
            return redirect(url_for("settings"))

        flash("Okänd åtgärd.", "warning")
        refresh_sheets_cache(force=True)
        return redirect(url_for("settings"))

    # GET
    address_titles = get_address_titles_from_sheets()
    predefined = get_predefined_routes()
    return render_template("settings.html",
                           tariffs=user_tariffs,
                           address_titles=address_titles,
                           predefined=predefined,
                           api_key=API_KEY)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
