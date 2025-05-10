import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure key

# Google Sheets credentials from environment variable
GOOGLE_CREDENTIALS = eval(os.environ.get('GOOGLE_CREDENTIALS'))

# Initialize Google Sheets API
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(GOOGLE_CREDENTIALS, scope)
client = gspread.authorize(creds)

# Access Google Sheets worksheets
golfer_sheet = client.open("FantasyGolf2025").worksheet("Golfer Pool")
draft_sheet = client.open("FantasyGolf2025").worksheet("Draft Board")

# Function to fetch available golfers and their rankings
def get_available_golfers():
    # Fetch all picks from "Draft Board" to determine which golfers are taken
    picks_data = draft_sheet.get_all_records()
    taken_golfers = {row["Golfer"] for row in picks_data if row["Golfer"]}

    # Fetch golfers and rankings from "Golfer Pool" (names in A, rankings in B)
    golfer_data = golfer_sheet.get_all_values()[1:]  # Skip header row
    # Create a list of tuples: [(golfer, ranking), ...], sorted by ranking (lower is better)
    available_golfers = [(row[0], int(row[1])) for row in golfer_data if row[0] and row[1] and row[0] not in taken_golfers]
    available_golfers.sort(key=lambda x: x[1])  # Sort by ranking (ascending)
    return available_golfers

# User credentials for login
USERS = {
    "user1": "Player1-PGA2025",
    "user2": "Player2-PGA2025",
    # Add more for 20-30 players, e.g.,
    # "user3": "Player3-PGA2025",
    # "user4": "Player4-PGA2025",
    # ...
    # "user30": "Player30-PGA2025",
}

@app.route('/')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USERS and USERS[username] == password:
            session['username'] = username
            return redirect(url_for('index'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    # Fetch available golfers for the dropdown
    available_golfers = get_available_golfers()
    golfers = [golfer[0] for golfer in available_golfers]
    # Fetch picks from "Draft Board" worksheet
    picks_data = draft_sheet.get_all_records()
    picks = [{"player": row["Player"], "golfer": row["Golfer"], "time": row["Time"]} for row in picks_data]
    return render_template('index.html', golfers=golfers, picks=picks)

@app.route('/draft')
def draft():
    if 'username' not in session:
        return redirect(url_for('login'))
    # Fetch picks from "Draft Board" worksheet
    picks_data = draft_sheet.get_all_records()
    picks = [{"player": row["Player"], "golfer": row["Golfer"], "time": row["Time"]} for row in picks_data]
    return jsonify({"picks": picks})

@app.route('/pick', methods=['POST'])
def pick():
    if 'username' not in session:
        return redirect(url_for('login'))
    golfer = request.form['golfer']
    # Fetch available golfers to verify the pick
    available_golfers = get_available_golfers()
    golfers = [g[0] for g in available_golfers]
    if golfer in golfers:
        # Add pick to "Draft Board" worksheet
        draft_sheet.append_row([session['username'], golfer, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    return redirect(url_for('index'))

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return jsonify({"error": "Not logged in"}), 401
    # Fetch available golfers
    available_golfers = get_available_golfers()
    if not available_golfers:
        return jsonify({"error": "No golfers available"}), 400
    # Select the highest-ranked available golfer (lowest ranking number)
    golfer, ranking = available_golfers[0]  # First entry is highest-ranked
    # Add autopick to "Draft Board" worksheet
    draft_sheet.append_row([session['username'], golfer, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    return jsonify({"success": True, "golfer": golfer})

if __name__ == '__main__':
    app.run(debug=True)