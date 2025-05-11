from flask import Flask, request, render_template, redirect, url_for, session, jsonify, g
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json
import backoff

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
load_dotenv()

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
credentials_json = os.getenv('GOOGLE_CREDENTIALS')
if not credentials_json:
    raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
credentials_info = json.loads(credentials_json)
creds = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
client = gspread.authorize(creds)
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'your_spreadsheet_id_here')
sheet = client.open_by_key(SPREADSHEET_ID)
worksheet = sheet.worksheet('Golfers')
draft_worksheet = sheet.worksheet('Draft Board')

# Hardcoded users for simplicity
users = {'user1': 'Player1-PGA2025', 'user2': 'Player2-PGA2025', 'admin': 'admin'}

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
        return g.golfers
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
        
        g.golfers = sorted(records, key=lambda x: x['Ranking'])
        return g.golfers
    except Exception as e:
        print(f"Error loading golfers: {e}")
        raise

@backoff.on_exception(backoff.expo, Exception, max_tries=3, giveup=lambda e: not str(e).startswith('[429]'))
def load_draft_picks():
    """Load draft picks from the Draft Board worksheet with caching."""
    if 'draft_picks' in g:
        return g.draft_picks
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
        
        g.draft_picks = picks
        return g.draft_picks
    except Exception as e:
        print(f"Error loading draft picks: {e}")
        raise

def save_draft_pick(player, golfer, pick_time):
    """Save a draft pick to the Draft Board worksheet and clear cache."""
    try:
        records = draft_worksheet.get_all_records()
        player_row = None
        row_index = 2
        
        for i, record in enumerate(records, 2):
            if record['Player'] == player:
                player_row = record
                row_index = i
                break
        
        if not player_row:
            draft_order = get_draft_order()
            draft_position = draft_order.index(player) + 1 if player in draft_order else len(draft_order) + 1
            new_row = [player, '', '', '', str(draft_position)]
            draft_worksheet.append_row(new_row)
            records = draft_worksheet.get_all_records()
            for i, record in enumerate(records, 2):
                if record['Player'] == player:
                    player_row = record
                    row_index = i
                    break
        
        pick_columns = ['Pick 1', 'Pick 2', 'Pick 3']
        pick_index = 0
        for i, col in enumerate(pick_columns):
            if not player_row.get(col, '').strip():
                pick_index = i
                break
            pick_index = i + 1
        
        if pick_index >= len(pick_columns):
            raise ValueError(f"Player {player} has already made all {TOTAL_ROUNDS} picks")
        
        pick_column_letter = chr(ord('B') + pick_index)
        cell_to_update = f"{pick_column_letter}{row_index}"
        draft_worksheet.update(cell_to_update, [[golfer]], value_input_option='RAW')
        print(f"Saved pick: {player} picked {golfer} in {pick_columns[pick_index]}")
        if 'draft_picks' in g:
            del g.draft_picks
    except Exception as e:
        print(f"Error saving draft pick: {e}")
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

@app.route('/')
def home():
    return redirect(url_for('index'))

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

@app.route('/index')
def index():
    golfers = load_golfers()
    print(f"Loaded golfers: {golfers}")
    picks = load_draft_picks()
    print(f"Loaded picks: {picks}")
    current_player, current_pick_number = get_current_turn()
    
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
                          participants=get_draft_order(), player_picks=player_picks)

@app.route('/draft_state')
def draft_state():
    golfers = load_golfers()
    picks = load_draft_picks()
    current_player, current_pick_number = get_current_turn()
    
    picked_golfers = [pick['Golfer'] for pick in picks]
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    
    player_picks = {player: [] for player in PLAYERS}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)
    
    return jsonify({
        'current_player': current_player,
        'current_pick_number': current_pick_number,
        'available_golfers': available_golfers,
        'picks': picks,
        'player_picks': player_picks,
        'draft_complete': current_player is None
    })

@app.route('/pick', methods=['POST'])
def pick():
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    golfer = request.form.get('golfer')
    current_player, _ = get_current_turn()
    logged_in_user = session['username']
    assigned_player = USER_PLAYER_MAPPING.get(logged_in_user)
    
    if not golfer:
        return jsonify({'error': 'No golfer selected'}), 400
    
    if not current_player:
        return jsonify({'error': 'Draft is complete or no current turn'}), 400
    
    if not DEBUG_MODE and assigned_player != current_player:
        return jsonify({'error': 'You can only pick for your assigned player'}), 403
    
    picks = load_draft_picks()
    picked_golfers = [p['Golfer'] for p in picks]
    if golfer in picked_golfers:
        return jsonify({'error': 'Golfer already picked'}), 400
    
    try:
        pick_time = f"Round {sum(1 for p in picks if p['Player'] == current_player) + 1} (No timestamp)"
        save_draft_pick(current_player, golfer, pick_time)
    except Exception as e:
        return jsonify({'error': f'Failed to save pick: {str(e)}'}), 500
    
    return jsonify({'success': True, 'golfer': golfer, 'player': current_player, 'pick_time': pick_time})

@app.route('/autopick', methods=['POST'])
def autopick():
    current_player, _ = get_current_turn()
    
    if not current_player:
        return jsonify({'error': 'Draft is complete or no current turn'}), 400
    
    if 'username' in session:
        logged_in_user = session['username']
        assigned_player = USER_PLAYER_MAPPING.get(logged_in_user)
        if not DEBUG_MODE and assigned_player != current_player:
            return jsonify({'error': 'You can only autopick for your assigned player'}), 403
    
    golfers = load_golfers()
    picks = load_draft_picks()
    picked_golfers = [p['Golfer'] for p in picks]
    
    available_golfers = [g for g in golfers if g['Golfer Name'] not in picked_golfers]
    if not available_golfers:
        return jsonify({'error': 'No golfers available'}), 400
    
    selected_golfer = min(available_golfers, key=lambda x: x['Ranking'])['Golfer Name']
    
    try:
        pick_time = f"Round {sum(1 for p in picks if p['Player'] == current_player) + 1} (No timestamp)"
        save_draft_pick(current_player, selected_golfer, pick_time)
    except Exception as e:
        return jsonify({'error': f'Failed to save autopick: {str(e)}'}), 500
    
    return jsonify({'success': True, 'golfer': selected_golfer, 'player': current_player, 'pick_time': pick_time})

@app.route('/setup', methods=['GET'])
def setup():
    if 'username' not in session or session['username'] != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        draft_worksheet.clear()
        draft_worksheet.append_row(['Player', 'Pick 1', 'Pick 2', 'Pick 3', 'Draft Order'])
        
        for i, player in enumerate(PLAYERS, 1):
            draft_worksheet.append_row([player, '', '', '', str(i)])
        
        return jsonify({'success': True, 'message': 'Draft Board initialized with all players'})
    except Exception as e:
        return jsonify({'error': f'Failed to setup Draft Board: {str(e)}'}), 500

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)