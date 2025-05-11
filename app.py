from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime
from dotenv import load_dotenv
import json

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_key_if_not_set')  # Load secret key from .env

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
credentials_json = os.getenv('GOOGLE_CREDENTIALS')
if not credentials_json:
    raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
import json
credentials_info = json.loads(credentials_json)
creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
client = gspread.authorize(creds)
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'your_spreadsheet_id_here')  # Load from .env, or fallback
sheet = client.open_by_key(SPREADSHEET_ID)
worksheet = sheet.worksheet('Golfers')  # Worksheet with golfer rankings
draft_worksheet = sheet.worksheet('Draft Board')  # Worksheet for draft picks

# Sample users for login (replace with your actual users)
users = {
    'user1': 'Player1-PGA2025',
    'user2': 'Player2-PGA2025',
    # Add more users as needed
}

# Load golfers from Google Sheets
def load_golfers():
    records = worksheet.get_all_records()
    return sorted(records, key=lambda x: x['Ranking'])

# Load draft picks from Google Sheets
def load_draft_picks():
    records = draft_worksheet.get_all_records()
    return records

# Save a draft pick to Google Sheets
def save_draft_pick(player, golfer, pick_time):
    draft_worksheet.append_row([player, golfer, pick_time])

@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username] == password:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    golfers = load_golfers()
    picks = load_draft_picks()
    available_golfers = [g['Golfer'] for g in golfers if g['Golfer'] not in [p['Golfer'] for p in picks]]
    
    return render_template('index.html', golfers=available_golfers, picks=picks)

@app.route('/pick', methods=['POST'])
def pick():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    golfer = request.form['golfer']
    player = session['username']
    pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Save the pick to Google Sheets
    save_draft_pick(player, golfer, pick_time)
    
    return redirect(url_for('index'))

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    golfers = load_golfers()
    picks = load_draft_picks()
    available_golfers = [g for g in golfers if g['Golfer'] not in [p['Golfer'] for p in picks]]
    
    if not available_golfers:
        return jsonify({'success': False, 'error': 'No golfers available'})
    
    # Select the highest-ranked available golfer (lowest ranking number)
    selected_golfer = min(available_golfers, key=lambda x: x['Ranking'])
    golfer_name = selected_golfer['Golfer']
    player = session['username']
    pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Save the autopick to Google Sheets
    save_draft_pick(player, golfer_name, pick_time)
    
    return jsonify({'success': True, 'golfer': golfer_name})

@app.route('/draft')
def draft():
    picks = load_draft_picks()
    return jsonify({'picks': picks})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)