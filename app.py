from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json

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
users = {'user1': 'Player1-PGA2025', 'user2': 'Player2-PGA2025'}  # Add more as needed

# List of players
PLAYERS = ['Alex', 'Liz', 'Eric', 'Jed', 'Stacie', 'Jason', 'Stephen', 'Mel', 'Brandon', 'Tony',
           'Ryan', 'Player12', 'Player13', 'Player14', 'Player15', 'Player16', 'Player17',
           'Player18', 'Player19', 'Player20']
TOTAL_ROUNDS = 3
PICKS_PER_PLAYER = TOTAL_ROUNDS
TIMER_SECONDS = 180

def get_draft_order():
    """Generate the snake draft order."""
    order = []
    for round_num in range(TOTAL_ROUNDS):
        if round_num % 2 == 0:
            order.extend(PLAYERS)
        else:
            order.extend(reversed(PLAYERS))
    return order

def load_golfers():
    """Load golfers from the Golfers worksheet."""
    try:
        records = worksheet.get_all_records()
        if not records:
            print("Warning: Golfers worksheet is empty")
            return []
        required_columns = ['Ranking', 'Golfer Name']
        first_record = records[0]
        missing_columns = [col for col in required_columns if col not in first_record]
        if missing_columns:
            raise ValueError(f"Missing required columns in Golfers worksheet: {missing_columns}")
        return sorted(records, key=lambda x: x['Ranking'])
    except Exception as e:
        print(f"Error loading golfers: {e}")
        return []

def load_draft_picks():
    """Load draft picks from the Draft Board worksheet."""
    try:
        records = draft_worksheet.get_all_records()
        if not records:
            print("Warning: Draft Board worksheet is empty")
            return []
        required_columns = ['Player', 'Golfer', 'Pick Time']
        if records:
            first_record = records[0]
            missing_columns = [col for col in required_columns if col not in first_record]
            if missing_columns:
                raise ValueError(f"Missing required columns in Draft Board worksheet: {missing_columns}")
        return records
    except Exception as e:
        print(f"Error loading draft picks: {e}")
        return []

def save_draft_pick(player, golfer, pick_time):
    """Save a draft pick to the Draft Board worksheet."""
    try:
        draft_worksheet.append_row([player, golfer, pick_time])
        print(f"Saved pick: {player} picked {golfer} at {pick_time}")
    except Exception as e:
        print(f"Error saving draft pick: {e}")
        raise

def get_current_turn():
    """Determine the current player whose turn it is."""
    picks = load_draft_picks()
    total_picks = len(picks)
    if total_picks >= len(PLAYERS) * PICKS_PER_PLAYER:
        return None, None
    
    draft_order = get_draft_order()
    current_position = total_picks % len(draft_order)
    current_player = draft_order[current_position]
    
    player_picks = sum(1 for pick in picks if pick['Player'] == current_player)
    if player_picks >= PICKS_PER_PLAYER:
        return None, None
    
    return current_player, total_picks + 1

@app.route('/')
def home():
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

@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    golfers = load_golfers()
    print(f"Loaded golfers: {golfers}")
    picks = load_draft_picks()
    print(f"Loaded picks: {picks}")
    current_player, current_pick_number = get_current_turn()
    
    # Get available golfers (not yet picked)
    picked_golfers = [pick['Golfer'] for pick in picks]
    print(f"Picked golfers: {picked_golfers}")
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    print(f"Available golfers: {available_golfers}")
    
    # Group picks by player
    player_picks = {player: [] for player in PLAYERS}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)
    
    # Draft status
    draft_complete = current_player is None
    
    return render_template('index.html', golfers=available_golfers, picks=picks,
                          current_player=current_player, current_pick_number=current_pick_number,
                          draft_complete=draft_complete, timer_seconds=TIMER_SECONDS,
                          players=PLAYERS, player_picks=player_picks)

@app.route('/draft_state')
def draft_state():
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    golfers = load_golfers()
    picks = load_draft_picks()
    current_player, current_pick_number = get_current_turn()
    
    picked_golfers = [pick['Golfer'] for pick in picks]
    available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in picked_golfers]
    
    # Group picks by player for the Players and Picks table
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
    
    if not golfer:
        return jsonify({'error': 'No golfer selected'}), 400
    
    if not current_player:
        return jsonify({'error': 'Draft is complete or no current turn'}), 400
    
    # Verify the golfer is still available
    picks = load_draft_picks()
    picked_golfers = [p['Golfer'] for p in picks]
    if golfer in picked_golfers:
        return jsonify({'error': 'Golfer already picked'}), 400
    
    # Save the pick
    try:
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        save_draft_pick(current_player, golfer, pick_time)
    except Exception as e:
        return jsonify({'error': f'Failed to save pick: {str(e)}'}), 500
    
    return jsonify({'success': True, 'golfer': golfer, 'player': current_player, 'pick_time': pick_time})

@app.route('/autopick', methods=['POST'])
def autopick():
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    current_player, _ = get_current_turn()
    if not current_player:
        return jsonify({'error': 'Draft is complete or no current turn'}), 400
    
    # Load golfers and picks
    golfers = load_golfers()
    picks = load_draft_picks()
    picked_golfers = [p['Golfer'] for p in picks]
    
    # Find the highest-ranked available golfer
    available_golfers = [g for g in golfers if g['Golfer Name'] not in picked_golfers]
    if not available_golfers:
        return jsonify({'error': 'No golfers available'}), 400
    
    # Pick the golfer with the lowest ranking (highest rank)
    selected_golfer = min(available_golfers, key=lambda x: x['Ranking'])['Golfer Name']
    
    # Save the autopick
    try:
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        save_draft_pick(current_player, selected_golfer, pick_time)
    except Exception as e:
        return jsonify({'error': f'Failed to save autopick: {str(e)}'}), 500
    
    return jsonify({'success': True, 'golfer': selected_golfer, 'player': current_player, 'pick_time': pick_time})

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)