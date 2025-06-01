from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_session import Session
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
import backoff
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os
import json
from dotenv import load_dotenv

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SECRET_KEY'] = os.urandom(24)
Session(app)

load_dotenv()
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')

scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
service_account_json = os.getenv('SERVICE_ACCOUNT_JSON')
if not service_account_json:
    raise ValueError("SERVICE_ACCOUNT_JSON environment variable is not set")
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(service_account_json), scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = sheet.worksheet('Golfers')
draft_worksheet = sheet.worksheet('Draft Board')

PLAYERS = ['Alex', 'Liz', 'Mel', 'Eric', 'Jed', 'Stacie', 'Tony', 'Brandon', 'Ryan']

USER_PLAYER_MAPPING = {
    'user1': 'Alex', 'user2': 'Liz', 'user3': 'Mel', 'user4': 'Eric', 'user5': 'Jed',
    'user6': 'Stacie', 'user7': 'Tony', 'user8': 'Brandon', 'user9': 'Ryan',
    'admin': 'Admin'
}
USER_CREDENTIALS = {
    'user1': 'Player1-PGA2025', 'user2': 'Player2-PGA2025', 'user3': 'Player3-PGA2025',
    'user4': 'Player4-PGA2025', 'user5': 'Player5-PGA2025', 'user6': 'Player6-PGA2025',
    'user7': 'Player7-PGA2025', 'user8': 'Player8-PGA2025', 'user9': 'Player9-PGA2025',
    'admin': 'admin'
}
TURN_DURATION = 180  # 3 minutes in seconds

def get_draft_order():
    """Get the draft order from the Draft Board worksheet."""
    try:
        records = draft_worksheet.get_all_records()
        if not records:
            print("No records found in Draft Board, using default PLAYERS")
            return PLAYERS
        order = [r for r in records if 'Player' in r and 'Draft Order' in r and r['Draft Order']]
        if not order:
            print("No valid draft order entries, using default PLAYERS")
            return PLAYERS
        # Ensure 'Draft Order' is an integer or convertible to int
        sorted_order = sorted(order, key=lambda x: int(float(str(x['Draft Order']).strip())))
        print(f"Draft order: {sorted_order}")
        return sorted_order if order else PLAYERS
    except (ValueError, KeyError, TypeError) as e:
        print(f"Error parsing draft order: {str(e)}, falling back to default order")
        return PLAYERS

@backoff.on_exception(backoff.expo, gspread.exceptions.APIError, max_tries=5)
def load_golfers():
    """Load golfers from the Google Sheet."""
    golfers = worksheet.get_all_records()
    print(f"Loaded golfers: {golfers}")
    return sorted(golfers, key=lambda x: int(x['Ranking']))

@backoff.on_exception(backoff.expo, gspread.exceptions.APIError, max_tries=5)
def load_draft_picks():
    """Load draft picks from the Google Sheet."""
    try:
        records = draft_worksheet.get_all_records()
        picks = []
        for record in records:
            player = record.get('Player')
            for pick_num in range(1, 4):
                pick_key = f'Pick {pick_num}'
                golfer = record.get(pick_key)
                if golfer:  # Only include non-empty picks
                    picks.append({
                        'Player': player,
                        'Golfer': golfer,
                        'Pick Number': pick_num
                    })
        print(f"Loaded draft picks: {picks}")
        return picks
    except Exception as e:
        print(f"Error loading draft picks: {str(e)}")
        return []

def get_current_turn(picks, draft_order):
    """Determine whose turn it is and the remaining time."""
    if not draft_order:
        print("Draft order is empty")
        return None, None, TURN_DURATION

    player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)

    print(f"Player picks: {player_picks}")

    for round_num in range(1, 4):  # 3 picks per player
        for player in draft_order:
            player_name = player['Player'] if isinstance(player, dict) else player
            if len(player_picks[player_name]) < round_num:
                pick_number = round_num
                remaining_time = TURN_DURATION  # Default to full duration since we don't store pick times
                print(f"Current turn - Player: {player_name}, Pick Number: {pick_number}, Remaining Time: {remaining_time}")
                return player_name, pick_number, remaining_time

    print("No current turn, draft might be complete")
    return None, None, TURN_DURATION

@app.route('/')
@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    golfers = load_golfers()
    picks = load_draft_picks()
    draft_order = get_draft_order()
    # Handle both string and dict types in draft_order
    player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}

    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)

    current_player, current_pick_number, remaining_time = get_current_turn(picks, draft_order)
    # Ensure current_player is a string
    current_player = str(current_player) if current_player else 'N/A'

    # Iterate over player names in player_picks instead of draft_order
    draft_complete = all(len(player_picks.get(player_name, [])) >= 3 for player_name in player_picks.keys())

    return render_template(
        'index.html',
        username=username,
        golfers=[g['Golfer Name'] for g in golfers],
        picks=picks,
        participants=draft_order,
        player_picks=player_picks,
        draft_complete=draft_complete,
        current_player=current_player,
        current_pick_number=current_pick_number,
        timer_seconds=remaining_time,
        user_player_mapping=USER_PLAYER_MAPPING
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/pick', methods=['POST'])
def pick():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    golfer = request.form.get('golfer')
    if not golfer:
        flash('No golfer selected', 'error')
        return redirect(url_for('index'))

    picks = load_draft_picks()
    draft_order = get_draft_order()
    current_player, current_pick_number, _ = get_current_turn(picks, draft_order)

    user_player = USER_PLAYER_MAPPING.get(username)
    if user_player != current_player:
        flash('Not your turn', 'error')
        return redirect(url_for('index'))

    golfers = load_golfers()
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
    if golfer not in available_golfers:
        flash('Golfer not available', 'error')
        return redirect(url_for('index'))

    player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == user_player), None)
    if not player_row:
        flash('Player not found in draft board', 'error')
        return redirect(url_for('index'))

    column = f'Pick {current_pick_number}'
    draft_worksheet.update_cell(player_row, draft_worksheet.find(column).col, golfer)

    return redirect(url_for('index'))

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    picks = load_draft_picks()
    draft_order = get_draft_order()
    current_player, current_pick_number, _ = get_current_turn(picks, draft_order)

    user_player = USER_PLAYER_MAPPING.get(username)
    print(f"Autopick - Username: {username}, User Player: {user_player}, Current Player: {current_player}")
    if user_player != current_player:
        flash('Not your turn', 'error')
        return redirect(url_for('index'))

    golfers = load_golfers()
    available_golfers = [g for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
    print(f"Available golfers for autopick: {available_golfers}")
    if not available_golfers:
        flash('No golfers available', 'error')
        return redirect(url_for('index'))

    golfer = min(available_golfers, key=lambda x: int(x['Ranking']))['Golfer Name']

    player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == user_player), None)
    if not player_row:
        flash('Player not found in draft board', 'error')
        return redirect(url_for('index'))

    column = f'Pick {current_pick_number}'
    draft_worksheet.update_cell(player_row, draft_worksheet.find(column).col, golfer)

    return redirect(url_for('index'))

@app.route('/draft_state', methods=['GET'])
def draft_state():
    try:
        picks = load_draft_picks()
        draft_order = get_draft_order()
        current_player, current_pick_number, remaining_time = get_current_turn(picks, draft_order)
        # Ensure current_player is a string
        current_player = str(current_player) if current_player else 'N/A'

        golfers = load_golfers()
        available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}
        for pick in picks:
            player = pick['Player']
            if player in player_picks:
                player_picks[player].append(pick)

        draft_complete = all(len(player_picks.get(player_name, [])) >= 3 for player_name in player_picks.keys())

        return jsonify({
            'current_player': current_player,
            'current_pick_number': current_pick_number,
            'remaining_time': remaining_time,
            'picks': picks,
            'available_golfers': available_golfers,
            'player_picks': player_picks,
            'draft_complete': draft_complete
        })
    except Exception as e:
        app.logger.error(f"Error in draft_state: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)