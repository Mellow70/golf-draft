from flask import Flask, render_template, request, jsonify, make_response, session, redirect, url_for
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import logging
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a secure key

# Logging setup
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Google Sheets setup
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDS = ServiceAccountCredentials.from_json_keyfile_name('ninth-matter-455721-s1-418ebef17f56.json', SCOPE)
CLIENT = gspread.authorize(CREDS)
SHEET = CLIENT.open_by_key("1lA0HYWd3CaiPXkCPFLltR2BPu6ucIVfWGuUUdlDMSY4")

# Configurable tournament settings
TOURNAMENT = "Major Fantasy Golf"  # e.g., "PGA Championship"
YEAR = "2025"
DRAFT_DATE = "Wednesday, April 9th"  # Update per tournament

# In-memory state
CACHE = {'Draft Board': None, 'Golfer Pool': None, 'last_update': 0, 'turn_start': None}
GOLFERS = {}  # {golfer_name: {'available': bool, 'ranking': int}}
TURN_DURATION = 300  # 5 minutes in seconds
USERS = {}  # {'username': {'password': hashed_pw, 'player': 'Player Name'}}

def initialize_state():
    golfers_data = SHEET.worksheet("Golfer Pool").get_all_values()
    for row in golfers_data[1:]:  # Skip header
        GOLFERS[row[0]] = {'available': True, 'ranking': int(row[2])}
    draft_data = SHEET.worksheet("Draft Board").get_all_values()
    for row in draft_data[1:]:
        if row[0]:  # Player name exists
            for pick in row[1:4]:
                if pick in GOLFERS:
                    GOLFERS[pick]['available'] = False
    players = [row[0] for row in draft_data[1:] if row[0]]
    for i, player in enumerate(players, 1):
        username = f"user{i}"
        password = f"pass{i}"  # Replace with secure passwords
        USERS[username] = {'password': generate_password_hash(password), 'player': player}
    logger.debug(f"Initialized golfers: {list(GOLFERS.keys())[:5]}, users: {list(USERS.keys())}")

def get_sheet_data(sheet_name):
    if time.time() - CACHE['last_update'] > 10 or CACHE[sheet_name] is None:
        CACHE[sheet_name] = SHEET.worksheet(sheet_name).get_all_values()
        CACHE['last_update'] = time.time()
    return CACHE[sheet_name]

def mark_golfer_unavailable(pick):
    if pick not in GOLFERS or not GOLFERS[pick]['available']:
        raise ValueError(f"{pick} is already unavailable")
    GOLFERS[pick]['available'] = False
    golfer_sheet = SHEET.worksheet("Golfer Pool")
    golfer_data = golfer_sheet.get_all_values()
    golfer_row = next(i + 1 for i, row in enumerate(golfer_data) if row[0] == pick)
    golfer_sheet.update_cell(golfer_row, 2, "No")
    logger.debug(f"Marked {pick} unavailable")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USERS and check_password_hash(USERS[username]['password'], password):
            session['username'] = username
            logger.debug(f"User {username} logged in")
            return redirect(url_for('index'))
        return render_template('login.html', error="Invalid credentials")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    draft_board = get_sheet_data("Draft Board")
    total_players = len([row for row in draft_board[1:] if row[0]])
    if total_players == 0:
        return "Error: No players defined in Draft Board", 500
    total_picks = total_players * 3
    current_picks = sum(1 for row in draft_board[1:] for cell in row[1:4] if cell)
    
    if current_picks >= total_picks:
        teams = [[row[0], row[1], row[2], row[3]] for row in draft_board[1:] if row[0]]
        response = make_response(render_template('index.html', tournament=f"{TOURNAMENT} {YEAR}", draft_date=DRAFT_DATE,
                                                message="Draft Complete. Good Luck!", player=None, round_num=None, golfers=[],
                                                teams=teams, current_picks=current_picks, draft_complete=True, username=session['username']))
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    
    round_num = current_picks // total_players + 1
    pick_in_round = current_picks % total_players
    order = [int(row[4]) for row in draft_board[1:] if row[4]]
    players = [row[0] for row in draft_board[1:] if row[0]]
    current_order = order if round_num % 2 == 1 else order[::-1]
    current_player = players[current_order[pick_in_round] - 1]
    
    available_golfers = [{'name': name, 'ranking': info['ranking']} for name, info in GOLFERS.items() if info['available']]
    available_golfers.sort(key=lambda x: x['ranking'])
    logger.debug(f"Available golfers: {available_golfers[:5]}")
    teams = [[row[0], row[1], row[2], row[3]] for row in draft_board[1:] if row[0]]
    
    user_player = USERS[session['username']]['player']
    is_user_turn = (user_player == current_player)
    if CACHE['turn_start'] is None or current_picks == 0:
        CACHE['turn_start'] = time.time()
    
    response = make_response(render_template('index.html', tournament=f"{TOURNAMENT} {YEAR}", draft_date=DRAFT_DATE,
                                            turn=f"{current_player} (Round {round_num}, Pick {current_picks + 1} of {total_picks})",
                                            player=current_player, round_num=round_num, golfers=available_golfers, teams=teams,
                                            current_picks=current_picks, draft_complete=False, is_user_turn=is_user_turn,
                                            username=session['username']))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/submit', methods=['POST'])
def submit():
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    
    try:
        player = request.form['player']
        pick = request.form['golfer']
        round_str = request.form.get('round', '')
        if not round_str.strip().isdigit():
            return jsonify({'status': 'error', 'message': f'Invalid round number: "{round_str}"'}), 400
        round_num = int(round_str.strip()) - 1
        
        user_player = USERS[session['username']]['player']
        if user_player != player:
            return jsonify({'status': 'error', 'message': 'Not your turn'}), 403
        
        if pick not in GOLFERS or not GOLFERS[pick]['available']:
            return jsonify({'status': 'error', 'message': f'{pick} is not available'}), 400
        
        draft_sheet = SHEET.worksheet("Draft Board")
        draft_data = draft_sheet.get_all_values()
        player_row = next(i + 2 for i, row in enumerate(draft_data[1:]) if row[0] == player)
        logger.debug(f"Updating Draft Board row {player_row}, col {2 + round_num} with {pick}")
        draft_sheet.update_cell(player_row, 2 + round_num, pick)
        
        mark_golfer_unavailable(pick)
        
        CACHE['Draft Board'] = None
        CACHE['Golfer Pool'] = None
        CACHE['last_update'] = 0
        CACHE['turn_start'] = time.time()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error in submit: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/auto_pick', methods=['POST'])
def auto_pick():
    if 'username' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    
    try:
        player = request.form['player']
        round_str = request.form.get('round', '')
        if not round_str.strip().isdigit():
            return jsonify({'status': 'error', 'message': f'Invalid round number: "{round_str}"'}), 400
        round_num = int(round_str.strip()) - 1
        
        user_player = USERS[session['username']]['player']
        if user_player != player:
            return jsonify({'status': 'error', 'message': 'Not your turn'}), 403
        
        available = [{'name': name, 'ranking': info['ranking']} for name, info in GOLFERS.items() if info['available']]
        if not available:
            return jsonify({'status': 'error', 'message': 'No golfers available'})
        
        pick = min(available, key=lambda x: x['ranking'])['name']
        logger.debug(f"Auto-picking {pick} for {player}")
        
        draft_sheet = SHEET.worksheet("Draft Board")
        draft_data = draft_sheet.get_all_values()
        player_row = next(i + 2 for i, row in enumerate(draft_data[1:]) if row[0] == player)
        draft_sheet.update_cell(player_row, 2 + round_num, pick)
        
        mark_golfer_unavailable(pick)
        
        CACHE['Draft Board'] = None
        CACHE['Golfer Pool'] = None
        CACHE['last_update'] = 0
        CACHE['turn_start'] = time.time()
        
        return jsonify({'status': 'success', 'pick': pick})
    except Exception as e:
        logger.error(f"Error in auto_pick: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/check_picks')
def check_picks():
    draft_board = get_sheet_data("Draft Board")
    pick_count = sum(1 for row in draft_board[1:] for cell in row[1:4] if cell)
    return jsonify({'pick_count': pick_count})

@app.route('/get_timer')
def get_timer():
    if CACHE['turn_start'] is None:
        return jsonify({'time_left': TURN_DURATION})
    time_left = max(0, TURN_DURATION - int(time.time() - CACHE['turn_start']))
    return jsonify({'time_left': time_left})

import os
if __name__ == '__main__':
    initialize_state()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)