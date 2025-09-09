import json
import os, uuid
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _open_sheet():
    load_dotenv()
    # 1) Inline JSON? (om du hellre vill lägga hela JSON:en som env-variabel)
    info = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
    if info:
        creds = Credentials.from_service_account_info(json.loads(info), scopes=SCOPES)
    else:
        # 2) Filväg via env
        key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        # 2a) Fallback till Render's standard path om inget satt
        if not key_path:
            default_secret = "/etc/secrets/topptaxi-sa.json"
            if os.path.exists(default_secret):
                key_path = default_secret
        if not key_path or not os.path.exists(key_path):
            raise FileNotFoundError(f"Service account JSON not found at: {key_path or '(unset)'}")
        creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    gc = gspread.authorize(creds)
    return gc.open_by_key(spreadsheet_id)


def _ws(sh, name):
    try:
        return sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return None

def _get_all(ws):
    return ws.get_all_records() if ws else []

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    repl = str.maketrans({"å":"a","ä":"a","ö":"o","é":"e","Å":"a","Ä":"a","Ö":"o","É":"e"})
    s = s.translate(repl)
    return " ".join(s.split())

def _route_key(from_title: str, to_title: str) -> str:
    return f"{_norm(from_title)}→{_norm(to_title)}"

def load_all():
    """Läser Sheets och bygger färdiga rutter (inkl. RouteID)."""
    sh = _open_sheet()
    places_name = os.getenv("SHEETS_PLACES", "Places")
    routes_name = os.getenv("SHEETS_ROUTES", "Routes")
    prices_name = os.getenv("SHEETS_PRICES", "RoutePrices")

    places = _get_all(_ws(sh, places_name))
    routes = _get_all(_ws(sh, routes_name))
    prices = _get_all(_ws(sh, prices_name))

    prices_by_route = {}
    for p in prices:
        rid = p.get("RouteID")
        if not rid:
            continue
        prices_by_route.setdefault(rid, []).append({
            "label": p.get("Label", ""),
            "min": int(p["Min"]) if str(p.get("Min","")).strip() != "" else 0,
            "max": (int(p["Max"]) if str(p.get("Max","")).strip() != "" else None),
            **({"total": int(p["Total"])} if str(p.get("Total","")).strip() != "" else {}),
            **({"price_per_person": int(p["PricePerPerson"])} if str(p.get("PricePerPerson","")).strip() != "" else {}),
        })

    built_routes = []
    for r in routes:
        built_routes.append({
            "route_id": r.get("RouteID",""),
            "from": r.get("FromTitle", ""),
            "to": r.get("ToTitle", ""),
            "from_address": r.get("FromAddress", ""),
            "to_address": r.get("ToAddress", ""),
            "from_lat": r.get("FromLat"),
            "from_lng": r.get("FromLng"),
            "to_lat": r.get("ToLat"),
            "to_lng": r.get("ToLng"),
            "prices": prices_by_route.get(r.get("RouteID"), []),
            "key": _route_key(r.get("FromTitle",""), r.get("ToTitle","")),
            "title": r.get("Title", ""),
        })

    return {"places": places, "routes": built_routes}

# --------- Skrivning / dubblettkontroll ----------
def list_route_keys():
    sh = _open_sheet()
    ws = _ws(sh, os.getenv("SHEETS_ROUTES", "Routes"))
    keys = set()
    if not ws:
        return keys
    for r in ws.get_all_records():
        keys.add(_route_key(r.get("FromTitle",""), r.get("ToTitle","")))
    return keys

def append_place(title: str, address: str, lat: float=None, lng: float=None, aliases: str=""):
    sh = _open_sheet()
    ws = _ws(sh, os.getenv("SHEETS_PLACES","Places"))
    if not ws:
        raise RuntimeError("Worksheet 'Places' saknas.")
    place_id = str(uuid.uuid4())
    ws.append_row(
        [place_id, title, address, lat if lat is not None else "", lng if lng is not None else "", aliases],
        value_input_option="RAW"
    )
    return place_id

def append_route_with_prices(
    from_title: str, to_title: str,
    from_address: str, to_address: str,
    title: str = "", group_id: str = None,
    from_lat: float=None, from_lng: float=None,
    to_lat: float=None, to_lng: float=None,
    prices: list = None,
):
    prices = prices or []
    sh = _open_sheet()
    ws_routes = _ws(sh, os.getenv("SHEETS_ROUTES","Routes"))
    ws_prices = _ws(sh, os.getenv("SHEETS_PRICES","RoutePrices"))
    if not ws_routes or not ws_prices:
        raise RuntimeError("Worksheet 'Routes' eller 'RoutePrices' saknas.")

    key = _route_key(from_title, to_title)
    if key in list_route_keys():
        raise ValueError("Denna rutt finns redan i Sheets (dubblett).")

    route_id = str(uuid.uuid4())
    group_id = group_id or str(uuid.uuid4())
    ws_routes.append_row(
        [route_id, from_title, to_title, from_address, to_address, title, group_id,
         from_lat if from_lat is not None else "", from_lng if from_lng is not None else "",
         to_lat if to_lat is not None else "", to_lng if to_lng is not None else "", key],
        value_input_option="RAW"
    )

    for p in prices:
        label = p.get("label","")
        min_v = int(p.get("min",0))
        max_v = p.get("max")
        max_v = (int(max_v) if max_v not in (None,"") else "")
        total = p.get("total")
        ppp   = p.get("price_per_person")
        if (total is None and ppp is None) or (total not in (None,"") and ppp not in (None,"")):
            raise ValueError(f"Prisraden '{label}' måste ha antingen Total ELLER Pris/Person (inte båda).")
        price_id = str(uuid.uuid4())
        ws_prices.append_row(
            [price_id, route_id, label, min_v, max_v if max_v != "" else "",
             (int(total) if total not in (None,"") else ""),
             (int(ppp) if ppp not in (None,"") else "")],
            value_input_option="RAW"
        )

    return {"route_id": route_id, "group_id": group_id}

def delete_route(route_id: str):
    """Tar bort en route + alla dess prisrader."""
    sh = _open_sheet()
    ws_routes = _ws(sh, os.getenv("SHEETS_ROUTES","Routes"))
    ws_prices = _ws(sh, os.getenv("SHEETS_PRICES","RoutePrices"))
    if not ws_routes or not ws_prices:
        raise RuntimeError("Worksheet saknas.")

    # Routes
    r_values = ws_routes.get_all_values()
    r_header = r_values[0]; rid_col = r_header.index("RouteID")+1
    r_del = []
    for i, row in enumerate(r_values[1:], start=2):
        if len(row) >= rid_col and row[rid_col-1] == route_id:
            r_del.append(i)
    for idx in reversed(r_del):
        ws_routes.delete_rows(idx)

    # RoutePrices
    p_values = ws_prices.get_all_values()
    p_header = p_values[0]; prid_col = p_header.index("RouteID")+1
    p_del = []
    for i, row in enumerate(p_values[1:], start=2):
        if len(row) >= prid_col and row[prid_col-1] == route_id:
            p_del.append(i)
    for idx in reversed(p_del):
        ws_prices.delete_rows(idx)

    return len(r_del), len(p_del)

def delete_place(place_id: str):
    """Tar bort plats om den inte används av någon rutt (via Title)."""
    sh = _open_sheet()
    ws_places = _ws(sh, os.getenv("SHEETS_PLACES","Places"))
    ws_routes = _ws(sh, os.getenv("SHEETS_ROUTES","Routes"))
    if not ws_places or not ws_routes:
        raise RuntimeError("Worksheet saknas.")

    p_values = ws_places.get_all_values()
    p_header = p_values[0]
    pid_col = p_header.index("PlaceID")+1
    title_col = p_header.index("Title")+1
    row_idx, title = None, None
    for i, row in enumerate(p_values[1:], start=2):
        if len(row) >= pid_col and row[pid_col-1] == place_id:
            row_idx = i
            title = row[title_col-1] if len(row) >= title_col else None
            break
    if row_idx is None:
        raise ValueError("PlaceID hittades inte.")

    # finns rutter som använder denna Title?
    if title:
        r_values = ws_routes.get_all_values()
        r_header = r_values[0]
        f_col = r_header.index("FromTitle")+1
        t_col = r_header.index("ToTitle")+1
        for row in r_values[1:]:
            if len(row) >= max(f_col, t_col) and (row[f_col-1] == title or row[t_col-1] == title):
                raise ValueError(f"Platsen '{title}' används av en rutt. Ta bort rutter först.")

    ws_places.delete_rows(row_idx)
    return True
def update_route_row(route_id: str, from_addr=None, to_addr=None,
                     from_lat=None, from_lng=None, to_lat=None, to_lng=None):
    """Uppdatera en redan skapad rutt med lat/lng och/eller adresser."""
    sh = _open_sheet()
    ws = _ws(sh, os.getenv("SHEETS_ROUTES", "Routes"))
    if not ws:
        raise RuntimeError("Worksheet 'Routes' saknas.")

    values = ws.get_all_values()
    header = values[0]
    col = {name: header.index(name) + 1 for name in
           ["RouteID", "FromAddress", "ToAddress", "FromLat", "FromLng", "ToLat", "ToLng"]}

    row_idx = None
    for i, row in enumerate(values[1:], start=2):
        if len(row) >= col["RouteID"] and row[col["RouteID"]-1] == route_id:
            row_idx = i
            break
    if row_idx is None:
        raise ValueError("RouteID hittades inte.")

    updates = []
    if from_addr is not None: updates.append(("FromAddress", from_addr))
    if to_addr   is not None: updates.append(("ToAddress",   to_addr))
    if from_lat  is not None: updates.append(("FromLat",     from_lat))
    if from_lng  is not None: updates.append(("FromLng",     from_lng))
    if to_lat    is not None: updates.append(("ToLat",       to_lat))
    if to_lng    is not None: updates.append(("ToLng",       to_lng))

    for key, val in updates:
        ws.update_cell(row_idx, col[key], val)
    return True


def update_place_latlng_by_title(title: str, lat: float, lng: float):
    """Skriv lat/lng till Places för en given Title om den finns."""
    sh = _open_sheet()
    ws = _ws(sh, os.getenv("SHEETS_PLACES", "Places"))
    if not ws:
        raise RuntimeError("Worksheet 'Places' saknas.")

    values = ws.get_all_values()
    header = values[0]
    c_title = header.index("Title") + 1
    c_lat   = header.index("Lat") + 1
    c_lng   = header.index("Lng") + 1

    for i, row in enumerate(values[1:], start=2):
        if len(row) >= c_title and row[c_title-1] == title:
            ws.update_cell(i, c_lat, lat)
            ws.update_cell(i, c_lng, lng)
            return True
    return False
