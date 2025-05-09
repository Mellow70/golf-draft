import os
import json
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import threading
import time
from datetime import datetime

app = Flask(__name__)
print("Template folder:", app.template_folder)
print("Files in template folder:", os.listdir(app.template_folder) if os.path.exists(app.template_folder) else "Folder not found")
app.secret_key = os.urandom(24)  # Secure session key

# Environment variable for Google Credentials
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
# creds_json = os.environ.get("GOOGLE_CREDENTIALS")
# if not creds_json:
#     raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
# creds_dict = json.loads(creds_json)
# CREDS = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
CREDS = ServiceAccountCredentials.from_json_keyfile_name("key1.json", SCOPE)
CLIENT = gspread.authorize(CREDS)
# Google Sheet setup
print("Available spreadsheets:", CLIENT.list_spreadsheet_files())  # Debug line
spreadsheet = CLIENT.open_by_key("1lA0HYWd3CaiPXkCPFLltR2BPu6ucIVfWGuUUdlDMSY4")
print("Available worksheets:", spreadsheet.worksheets())  # Debug to confirm tabs
SHEET = spreadsheet.worksheet("Draft Board")

# Draft settings
TOURNAMENT = "PGA Championship"
DRAFT_DATE = "Wednesday, May 14th"
USERS = {
    "user1": "Player1-PGA2025",
    "user2": "Player2-PGA2025",
    # Add more users for 20-30 players as needed
}
DRAFT_DURATION = 60  # 60 seconds per pick
PICKED_GOLFERS = set()  # Track selected golfers
CURRENT_USER_INDEX = 0
DRAFT_START_TIME = None
DRAFT_ACTIVE = False

def update_sheet(pick, user):
    row = [datetime.now(), user, pick]
    SHEET.append_row(row)

def next_user():
    global CURRENT_USER_INDEX, DRAFT_ACTIVE
    CURRENT_USER_INDEX = (CURRENT_USER_INDEX + 1) % len(USERS)
    if CURRENT_USER_INDEX == 0 and time.time() > DRAFT_START_TIME + (len(USERS) * DRAFT_DURATION):
        DRAFT_ACTIVE = False

@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html', tournament=TOURNAMENT, draft_date=DRAFT_DATE,
                          users=list(USERS.values()), current_user=list(USERS.values())[CURRENT_USER_INDEX],
                          time_left=max(0, int(DRAFT_START_TIME + (CURRENT_USER_INDEX + 1) * DRAFT_DURATION - time.time()) if DRAFT_ACTIVE else 0))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        return "Invalid credentials", 401
    return render_template('login.html')

@app.route('/start_draft')
def start_draft():
    global DRAFT_START_TIME, DRAFT_ACTIVE, CURRENT_USER_INDEX, PICKED_GOLFERS
    if not DRAFT_ACTIVE and session.get('logged_in'):
        DRAFT_START_TIME = time.time()
        DRAFT_ACTIVE = True
        CURRENT_USER_INDEX = 0
        PICKED_GOLFERS.clear()
        return jsonify({"status": "success", "message": "Draft started"})
    return jsonify({"status": "error", "message": "Draft already active or not logged in"}), 400

@app.route('/make_pick', methods=['POST'])
def make_pick():
    global PICKED_GOLFERS
    if not DRAFT_ACTIVE or not session.get('logged_in') or list(USERS.values())[CURRENT_USER_INDEX] != USERS[session.get('username')]:
        return jsonify({"status": "error", "message": "Not your turn or draft not active"}), 400
    pick = request.json.get('pick')
    if pick and pick not in PICKED_GOLFERS:
        PICKED_GOLFERS.add(pick)
        update_sheet(pick, USERS[session.get('username')])
        next_user()
        return jsonify({"status": "success", "message": "Pick recorded", "next_user": list(USERS.values())[CURRENT_USER_INDEX]})
    return jsonify({"status": "error", "message": "Pick already taken or invalid"}), 400
