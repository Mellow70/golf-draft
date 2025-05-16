import os
from flask import Flask, render_template, request, session, redirect, url_for
from flask_session import Session
from google.oauth2 import service_account
from googleapiclient.discovery import build
import gspread
from datetime import datetime
import backoff
from collections import defaultdict
import json

app = Flask(__name__)

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Load Google Sheets credentials from environment variable
creds_dict = json.loads(os.environ.get('GOOGLE_CREDENTIALS', '{}'))
scoped_credentials = service_account.Credentials.from_service_account_info(
    creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets']
)
client = gspread.authorize(scoped_credentials)

# Get the spreadsheet by ID from environment variable
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', 'your_spreadsheet_id_here')
sheet = client.open_by_key(SPREADSHEET_ID)
worksheet = sheet.worksheet("Golfers")
draft_worksheet = sheet.worksheet("Draft Board")

# Global cache using a simple dictionary (not persistent across restarts)
g = {}

# Hardcoded users and passwords
USER_PASSWORDS = {
    'user1': 'Player1-PGA2025',
    'user2': 'Player2-PGA2025',
    'admin': 'admin'
}

# User-to-player mapping
USER_PLAYER_MAPPING = {
    'user1': 'Alex',
    'user2': 'Liz',
    'user3': 'Eric',
    'user4': 'Jed',
    'user5': 'Stacie',
    'user6': 'Jason',
    'user7': 'Stephen',
    'user8': 'Mel',
    'user9': 'Brandon',
    'user10': 'Tony',
}

# Debug mode for testing
DEBUG_MODE = True

# List of players (used as a fallback)
PLAYERS = ['Alex', 'Liz', 'Eric', 'Jed', 'Stacie', 'Jason', 'Stephen', 'Mel', 'Brandon', 'Tony']
TOTAL_ROUNDS = 3
PICKS_PER_PLAYER = TOTAL_ROUNDS
TIMER_SECONDS = 180
TOURNAMENT = "PGA Championship"

def get_draft_order():
    """Generate the draft order based on the Draft Order column in the Draft Board."""
    try:
        records = draft_worksheet.get_all_records()
        if not records:
            print("Draft Board worksheet has no players; using default order")
            return PLAYERS
        
        # Filter out generic players (e.g., 'PlayerXX')
        actual_players = [record for record in records if not record['Player'].startswith('Player')]
        sorted_players = sorted(actual_players, key=lambda x: int(x['Draft Order']) if x['Draft Order'] else 999)
        players = [record['Player'] for record in sorted_players if record['Player']]
        
        order = []
        for round_num in range(TOTAL_ROUNDS):
            if round_num % 2 == 0:
                order.extend(players)
            else:
                order.extend(reversed(players))
        return order
    except Exception as e:
        print(f"Error generating draft order: {e}")
        return PLAYERS

@backoff.on_exception(backoff.expo, Exception, max_tries=3, giveup=lambda e: not str(e).startswith('[429]'))
def load_golfers():
    """Load golfers from the Golfers worksheet with caching."""
    if 'golfers' in g:
        return g['golfers']
    try:
        all_values = worksheet.get_all_values()
        
        if not all_values:
            print("Golfers worksheet is empty; adding headers")
            worksheet.append_row(['Golfer Name', 'Ranking'])
            return []
        
        headers = all_values[0]
        required_columns = ['Golfer Name', 'Ranking']
        missing_columns = [col for col in required_columns if col not in headers]
        
        if missing_columns:
            print(f"Golfers worksheet missing headers: {missing_columns}; resetting headers")
            worksheet.clear()
            worksheet.append_row(['Golfer Name', 'Ranking'])
            return []
        
        records = worksheet.get_all_records()
        if not records:
            print("Warning: Golfers worksheet has no golfers")
            return []
        
        g['golfers'] = sorted(records, key=lambda x: x['Ranking'])
        return g['golfers']
    except Exception as e:
        print(f"Error loading golfers: {e}")
        raise

@backoff.on_exception(backoff.expo, Exception, max_tries=3, giveup=lambda e: not str(e).startswith('[429]'))
def load_draft_picks():
    """Load draft picks from the Draft Board worksheet with caching."""
    if 'draft_picks' in g:
        return g['draft_picks']
    try:
        all_values = draft_worksheet.get_all_values()
        
        if not all_values:
            print("Draft Board worksheet is empty; adding headers")
            draft_worksheet.append_row(['Player', 'Pick 1', 'Pick 2', 'Pick 3', 'Draft Order'])
            return []
        
        headers = all_values[0]
        required_columns = ['Player', 'Pick 1', 'Pick 2', 'Pick 3', 'Draft Order']
        missing_columns = [col for col in required_columns if col not in headers]
        
        if missing_columns:
            print(f"Draft Board worksheet has incorrect headers: {headers}. Expected: {required_columns}")
            return []
        
        records = draft_worksheet.get_all_records()
        if not records:
            print("Warning: Draft Board worksheet has no picks")
            return []
        
        picks = []
        for record in records:
            player = record['Player']
            for pick_num, pick_key in enumerate(['Pick 1', 'Pick 2', 'Pick 3'], 1):
                golfer = record.get(pick_key, '').strip()
                if golfer:
                    pick_time = f"Round {pick_num} (No timestamp)"
                    picks.append({'Player': player, 'Golfer': golfer, 'Pick Time': pick_time})
        
        draft_order = get_draft_order()
        picks.sort(key=lambda x: (
            draft_order.index(x['Player']) if x['Player'] in draft_order else len(draft_order),
            int(x['Pick Time'].split()[1])
        ))
        
        g['draft_picks'] = picks
        return g['draft_picks']
    except Exception as e:
        print(f"Error loading draft picks: {e}")
        raise

def get_current_turn():
    """Determine the current player whose turn it is based on existing picks."""
    picks = load_draft_picks()
    total_picks = len(picks)
    if total_picks >= len(PLAYERS) * PICKS_PER_PLAYER:
        return None, None
    
    draft_order = get_draft_order()
    if not draft_order:
        return None, None
    
    current_position = total_picks % len(draft_order)
    current_player = draft_order[current_position]
    
    player_picks = sum(1 for pick in picks if pick['Player'] == current_player)
    if player_picks >= PICKS_PER_PLAYER:
        next_position = (current_position + 1) % len(draft_order)
        attempts = 0
        while attempts < len(draft_order):
            next_player = draft_order[next_position]
            next_player_picks = sum(1 for pick in picks if pick['Player'] == next_player)
            if next_player_picks < PICKS_PER_PLAYER:
                current_player = next_player
                break
            next_position = (next_position + 1) % len(draft_order)
            attempts += 1
        else:
            return None, None
    
    return current_player, total_picks + 1

def save_draft_pick(player, golfer, pick_time):
    """Save a draft pick to the Draft Board worksheet in the player's existing row."""
    try:
        # Get all records from the Draft Board worksheet
        records = draft_worksheet.get_all_records()
        player_row = None
        row_index = None

        # Find the player's existing row (should be in rows 2 to 11)
        for i, record in enumerate(records):
            if record['Player'] == player:
                player_row = record
                row_index = i + 2  # +2 because records start at row 2 (row 1 is headers)
                break

        if not player_row:
            print(f"Player {player} not found in Draft Board worksheet")
            return False

        # Determine the pick number from pick_time (e.g., "Round 1 (No timestamp)" -> 1)
        round_num = int(pick_time.split()[1])
        pick_key = f'Pick {round_num}'

        # Check if the pick already exists for this round
        if player_row.get(pick_key):
            print(f"Pick already exists for {player} in {pick_key}")
            return False

        # Update the existing row with the new pick
        col_index = ['Pick 1', 'Pick 2', 'Pick 3'].index(pick_key) + 2  # Column B=2, C=3, D=4
        draft_worksheet.update_cell(row_index, col_index, golfer)

        # Invalidate cache to ensure draft picks are reloaded
        if 'draft_picks' in g:
            del g['draft_picks']
        return True
    except Exception as e:
        print(f"Error saving draft pick: {e}")
        return False

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        print(f"Login attempt: username={username}, password={password}")
        if username in USER_PASSWORDS and USER_PASSWORDS[username] == password:
            session['username'] = username
            print(f"Login successful for {username}, redirecting to index")
            return redirect(url_for('index'))
        print(f"Login failed for {username}: invalid credentials")
        return render_template('login.html', error="Invalid username or password")
    print("Rendering login page for GET request")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
def root():
    return redirect(url_for('index'))

@app.route('/index')
def index():
    golfers = load_golfers()
    print(f"Loaded golfers: {golfers}")
    picks = load_draft_picks()
    print(f"Loaded picks: {picks}")
    current_player, current_pick_number = get_current_turn()
    print(f"Current player: {current_player}, Current pick number: {current_pick_number}")
    
    picked_golfers = [pick['Golfer'] for pick in picks]
    print(f"Picked golfers: {picked_golfers}")
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    print(f"Available golfers: {available_golfers}")
    
    player_picks = {player: [] for player in PLAYERS}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)
    
    draft_complete = current_player is None
    
    return render_template('index.html', golfers=available_golfers, picks=picks,
                          current_player=current_player, current_pick_number=current_pick_number,
                          draft_complete=draft_complete, timer_seconds=TIMER_SECONDS,
                          participants=get_draft_order(), player_picks=player_picks,
                          username=session.get('username'), USER_PLAYER_MAPPING=USER_PLAYER_MAPPING)

@app.route('/draft_state')
def draft_state():
    picks = load_draft_picks()
    current_player, current_pick_number = get_current_turn()
    golfers = load_golfers()
    picked_golfers = [pick['Golfer'] for pick in picks]
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    player_picks = {player: [] for player in PLAYERS}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)
    return {
        'current_player': current_player,
        'current_pick_number': current_pick_number,
        'picks': picks,
        'available_golfers': available_golfers,
        'player_picks': player_picks,
        'draft_complete': current_player is None
    }

@app.route('/pick', methods=['POST'])
def pick():
    if 'username' not in session:
        return {'success': False, 'error': 'Not logged in'}, 401
    username = session['username']
    player = USER_PLAYER_MAPPING.get(username)
    if not player:
        return {'success': False, 'error': 'Invalid user'}, 400
    
    golfer = request.form.get('golfer')
    if not golfer:
        return {'success': False, 'error': 'No golfer selected'}, 400
    
    picks = load_draft_picks()
    current_player, _ = get_current_turn()
    if current_player != player:
        return {'success': False, 'error': 'Not your turn'}, 403
    
    pick_time = f"Round {sum(1 for p in picks if p['Player'] == player) + 1} (No timestamp)"
    if save_draft_pick(player, golfer, pick_time):
        return {'success': True, 'player': player, 'golfer': golfer, 'pick_time': pick_time}
    return {'success': False, 'error': 'Failed to save pick'}, 500

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return {'success': False, 'error': 'Not logged in'}, 401
    username = session['username']
    player = USER_PLAYER_MAPPING.get(username)
    if not player:
        return {'success': False, 'error': 'Invalid user'}, 400
    
    picks = load_draft_picks()
    current_player, _ = get_current_turn()
    if current_player != player:
        return {'success': False, 'error': 'Not your turn'}, 403
    
    golfers = load_golfers()
    if not golfers:
        return {'success': False, 'error': 'No golfers available'}, 400
    
    picked_golfers = {pick['Golfer'] for pick in picks}
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    if not available_golfers:
        return {'success': False, 'error': 'No golfers left'}, 400
    
    golfer = sorted(available_golfers, key=lambda x: next((g['Ranking'] for g in golfers if g['Golfer Name'] == x), 999))[0]
    pick_time = f"Round {sum(1 for p in picks if p['Player'] == player) + 1} (No timestamp)"
    if save_draft_pick(player, golfer, pick_time):
        return {'success': True, 'player': player, 'golfer': golfer, 'pick_time': pick_time}
    return {'success': False, 'error': 'Failed to save autopick'}, 500

if __name__ == '__main__':
    app.run(debug=DEBUG_MODE)