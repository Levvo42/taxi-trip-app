import os, datetime
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
KEY_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

print("Worksheets:", [ws.title for ws in sh.worksheets()])

# Skrivtest – skapar 'Health' om den inte finns och lägger till en rad
try:
    ws = sh.worksheet("Health")
except gspread.exceptions.WorksheetNotFound:
    ws = sh.add_worksheet(title="Health", rows=100, cols=10)

ws.append_row(
    ["OK", datetime.datetime.now().isoformat(timespec="seconds"), "Topptaxi connection test"],
    value_input_option="RAW"
)
print("Write test: OK (row appended to 'Health')")
