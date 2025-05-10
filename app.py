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

# Fetch golfers and rankings from "Golfer Pool" worksheet (names in A, rankings in B)
golfer_data = golfer_sheet.get_all_values()[1:]  # Skip header row
# Create a list of tuples: [(golfer, ranking), ...], sorted by ranking (lower is better)
available_golfers = [(row[0], int(row[1])) for row in golfer_data if row[0] and row[1]]  # Use Column A and B
available_golfers.sort(key=lambda x: x[1])  # Sort by ranking (ascending)

# Extract just the golfer names for the dropdown
golfers = [golfer[0] for golfer in available_golfers]

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
    if golfer in golfers:
        # Add pick to "Draft Board" worksheet
        draft_sheet.append_row([session['username'], golfer, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        # Remove golfer from available_golfers and golfers lists
        global available_golfers, golfers
        available_golfers = [g for g in available_golfers if g[0] != golfer]
        golfers.remove(golfer)
    return redirect(url_for('index'))

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return jsonify({"error": "Not logged in"}), 401
    if not available_golfers:
        return jsonify({"error": "No golfers available"}), 400
    # Select the highest-ranked available golfer (lowest ranking number)
    global available_golfers, golfers
    golfer, ranking = available_golfers[0]  # First entry is highest-ranked (lowest ranking number)
    # Add autopick to "Draft Board" worksheet
    draft_sheet.append_row([session['username'], golfer, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    # Remove golfer from available_golfers and golfers lists
    available_golfers = [g for g in available_golfers if g[0] != golfer]
    golfers.remove(golfer)
    return jsonify({"success": True, "golfer": golfer})

if __name__ == '__main__':
    app.run(debug=True)