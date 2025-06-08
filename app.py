from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_session import Session
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
import backoff
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os
import json
from dotenv import load_dotenv
import logging

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SECRET_KEY'] = os.urandom(24)
Session(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Event configuration
CURRENT_EVENT = {
    'name': 'U.S. Open',
    'location': 'Oakmont'
}

load_dotenv(encoding='utf-8')
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

user_player_mapping = {
    'user1': 'Stephen',
    'user2': 'Jason',
    'user3': 'Josh',
    'user4': 'Jed',
    'user5': 'Alex',
    'user6': 'Brandon',
    'user7': 'Eric',
    'user8': 'Mel',
    'user9': 'Stacie',
    'user10': 'Ryan',
    'user11': 'Liz'
}

USER_CREDENTIALS = {
    'user1': 'Zingg25',
    'user2': 'JDog25',
    'user3': 'Jorkman',
    'user4': 'Jedman',
    'user5': 'Alex1121',
    'user6': 'Brands99',
    'user7': 'Eric2988',
    'user8': 'mellow',
    'user9': '1winner',
    'user10': 'ryry01',
    'user11': 'Liz1962',
    'admin': 'daboss'
}
TURN_DURATION = 180  # 3 minutes in seconds

# Caching variables
cached_picks = None
cached_golfers = None
cached_draft_order = None
cached_draft_start_time = None
last_picks_update = None
last_golfers_update = None
last_draft_order_update = None
last_draft_start_time_update = None
CACHE_DURATION = timedelta(seconds=30)  # Increase cache duration

def ensure_draft_columns():
    """Ensure the 'Pick Time' and 'Draft Start Time' columns exist in the Draft Board worksheet."""
    try:
        headers = draft_worksheet.row_values(1)
        if 'Pick Time' not in headers:
            draft_worksheet.update_cell(1, len(headers) + 1, 'Pick Time')
            logger.info("Added 'Pick Time' column to Draft Board worksheet")
        if 'Draft Start Time' not in headers:
            draft_worksheet.update_cell(1, len(headers) + 1, 'Draft Start Time')
            logger.info("Added 'Draft Start Time' column to Draft Board worksheet")
    except Exception as e:
        logger.error(f"Error ensuring draft columns: {str(e)}")
        raise

def get_draft_start_time():
    """Get or set the draft start time from the Draft Board worksheet with caching, enforcing 8:00 PM EDT start."""
    global cached_draft_start_time, last_draft_start_time_update
    now = datetime.now()
    if cached_draft_start_time and last_draft_start_time_update and (now - last_draft_start_time_update) < CACHE_DURATION:
        logger.info("Returning cached draft start time")
        return cached_draft_start_time

    try:
        headers = draft_worksheet.row_values(1)
        draft_start_col = headers.index('Draft Start Time') + 1 if 'Draft Start Time' in headers else None
        if not draft_start_col:
            logger.error("Draft Start Time column not found")
            return None

        values = draft_worksheet.col_values(draft_start_col)[1:]  # Skip header
        for value in values:
            if value:
                cached_draft_start_time = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                last_draft_start_time_update = now
                return cached_draft_start_time

        # Enforce draft start at 8:00 PM EDT
        scheduled_start = datetime(2025, 6, 8, 20, 0, 0)  # 8:00 PM EDT
        if now < scheduled_start:
            logger.info(f"Draft not started yet, current time {now}, scheduled start {scheduled_start}")
            return None  # Prevent timer/picks until start time

        start_time = now
        player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row.get('Player') == user_player_mapping['user1']), None)
        if player_row:
            draft_worksheet.update_cell(player_row, draft_start_col, start_time.strftime('%Y-%m-%d %H:%M:%S'))
            logger.info(f"Set Draft Start Time to {start_time} for {user_player_mapping['user1']}")
            cached_draft_start_time = start_time
            last_draft_start_time_update = now
            return start_time
        else:
            logger.error(f"{user_player_mapping['user1']} not found in draft board to set Draft Start Time")
            return None
    except gspread.exceptions.APIError as e:
        if e.response and e.response.status_code == 429:
            logger.error(f"APIError 429 in get_draft_start_time: {str(e)}")
            return cached_draft_start_time  # Return cached value on quota exceeded
        raise

def get_draft_order():
    """Get the draft order from the Draft Board worksheet with caching."""
    global cached_draft_order, last_draft_order_update
    now = datetime.now()
    if cached_draft_order and last_draft_order_update and (now - last_draft_order_update) < CACHE_DURATION:
        logger.info("Returning cached draft order")
        return cached_draft_order

    try:
        records = draft_worksheet.get_all_records()
        if not records:
            logger.info("No records found in Draft Board, using default player mapping")
            cached_draft_order = [{'Player': player} for player in user_player_mapping.values()]
        else:
            order = [r for r in records if 'Player' in r and 'Draft Order' in r and r['Draft Order']]
            if not order:
                logger.info("No valid draft order entries, using default player mapping")
                cached_draft_order = [{'Player': player} for player in user_player_mapping.values()]
            else:
                sorted_order = sorted(order, key=lambda x: int(float(str(x['Draft Order']).strip())))
                logger.info(f"Draft order: {sorted_order}")
                cached_draft_order = sorted_order if order else [{'Player': player} for player in user_player_mapping.values()]
        last_draft_order_update = now
        return cached_draft_order
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Error parsing draft order: {str(e)}, falling back to default order")
        return [{'Player': player} for player in user_player_mapping.values()]

@backoff.on_exception(backoff.expo, gspread.exceptions.APIError, max_tries=8, max_time=120)  # Increase max_time to 120s
def load_golfers():
    """Load golfers from the Google Sheet with caching."""
    global cached_golfers, last_golfers_update
    now = datetime.now()
    if cached_golfers and last_golfers_update and (now - last_golfers_update) < CACHE_DURATION:
        logger.info("Returning cached golfers")
        return cached_golfers

    try:
        golfers = worksheet.get_all_records()
        logger.info(f"Loaded golfers: {golfers}")
        cached_golfers = sorted(golfers, key=lambda x: int(x['Ranking']))
        last_golfers_update = now
        return cached_golfers
    except Exception as e:
        logger.error(f"Error loading golfers: {str(e)}")
        if cached_golfers:
            logger.info("Returning cached golfers due to error")
            return cached_golfers
        raise

@backoff.on_exception(backoff.expo, gspread.exceptions.APIError, max_tries=8, max_time=120)  # Increase max_time to 120s
def load_draft_picks():
    """Load draft picks from the Google Sheet with caching."""
    global cached_picks, last_picks_update
    now = datetime.now()
    if cached_picks and last_picks_update and (now - last_picks_update) < CACHE_DURATION:
        logger.info("Returning cached draft picks")
        return cached_picks

    try:
        draft_worksheet = sheet.worksheet('Draft Board')
        records = draft_worksheet.get_all_records()
        picks = []
        for record in records:
            player = record.get('Player')
            pick_time = record.get('Pick Time', '')
            for pick_num in range(1, 4):
                pick_key = f'Pick {pick_num}'
                golfer = record.get(pick_key)
                if golfer:
                    picks.append({
                        'Player': player,
                        'Golfer': golfer,
                        'Pick Number': pick_num,
                        'Pick Time': pick_time
                    })
        logger.info(f"Loaded draft picks: {picks}")
        cached_picks = picks
        last_picks_update = now
        return picks
    except gspread.exceptions.APIError as e:
        if e.response and e.response.status_code == 429:
            logger.error(f"APIError 429 in load_draft_picks: {str(e)}")
            if cached_picks:
                logger.info("Returning cached draft picks due to quota exceeded")
                return cached_picks
        raise

@backoff.on_exception(backoff.expo, gspread.exceptions.APIError, max_tries=8, max_time=60)
def update_draft_cell(player_row, column, value):
    """Update a cell in the Draft Board worksheet with retries."""
    try:
        draft_worksheet.update_cell(player_row, column, value)
        logger.info(f"Updated cell at row {player_row}, column {column} with value {value}")
    except Exception as e:
        logger.error(f"Error updating cell at row {player_row}, column {column}: {str(e)}")
        raise

def perform_autopick(current_player, current_pick_number, draft_order, picks):
    """Perform an autopick for the current player."""
    try:
        golfers = load_golfers()
        available_golfers = [g for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        logger.info(f"Available golfers for autopick: {available_golfers}")
        if not available_golfers:
            logger.warning("No golfers available for autopick")
            return False

        golfer = min(available_golfers, key=lambda x: int(x['Ranking']))['Golfer Name']
        player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == current_player), None)
        if not player_row:
            logger.error("Player not found in draft board for autopick")
            return False

        column = f'Pick {current_pick_number}'
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pick_time_col = draft_worksheet.find('Pick Time')
        if pick_time_col:
            update_draft_cell(player_row, pick_time_col.col, pick_time)
        else:
            logger.error("Pick Time column not found during autopick")
            return False

        update_draft_cell(player_row, draft_worksheet.find(column).col, golfer)
        logger.info(f"Autopick successful: {current_player} picked {golfer}")
        # Invalidate cache after autopick
        global cached_picks, last_picks_update
        cached_picks = None
        last_picks_update = None
        return True
    except Exception as e:
        logger.error(f"Error during autopick: {str(e)}")
        return False

def get_current_turn(picks, draft_order):
    """Determine whose turn it is and the remaining time, performing autopick if necessary."""
    if not draft_order:
        logger.info("Draft order is empty")
        return None, None, TURN_DURATION

    player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}
    for pick in picks:
        player = pick['Player']
        if player in player_picks:
            player_picks[player].append(pick)

    logger.info(f"Player picks: {player_picks}")

    for round_num in range(1, 4):  # Rounds 1 to 3
        # Snake draft: reverse order in even-numbered rounds (Round 2)
        current_order = draft_order if round_num % 2 != 0 else list(reversed(draft_order))
        logger.info(f"Round {round_num} draft order: {current_order}")

        for player in current_order:
            player_name = player['Player'] if isinstance(player, dict) else player
            if len(player_picks[player_name]) < round_num:
                pick_number = round_num
                if not picks:  # First turn of the draft
                    draft_start = get_draft_start_time()
                    if not draft_start:
                        logger.error("Could not determine draft start time")
                        return player_name, pick_number, TURN_DURATION
                    elapsed = (datetime.now() - draft_start).total_seconds()
                    remaining_time = max(0, TURN_DURATION - elapsed)
                else:
                    last_pick = max(picks, key=lambda x: x.get('Pick Time', ''), default=None)
                    if last_pick and last_pick.get('Pick Time'):
                        try:
                            pick_time = datetime.strptime(last_pick['Pick Time'], '%Y-%m-%d %H:%M:%S')
                            elapsed = (datetime.now() - pick_time).total_seconds()
                            remaining_time = max(0, TURN_DURATION - elapsed)
                        except ValueError as e:
                            logger.error(f"Error parsing Pick Time '{last_pick['Pick Time']}': {str(e)}")
                            remaining_time = TURN_DURATION
                    else:
                        remaining_time = TURN_DURATION

                if remaining_time <= 0:
                    logger.info(f"Timer expired for {player_name}'s turn, performing autopick")
                    success = perform_autopick(player_name, pick_number, draft_order, picks)
                    if success:
                        picks = load_draft_picks()
                        player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}
                        for pick in picks:
                            player = pick['Player']
                            if player in player_picks:
                                player_picks[player].append(pick)
                        return get_current_turn(picks, draft_order)

                logger.info(f"Current turn - Player: {player_name}, Pick Number: {pick_number}, Remaining Time: {remaining_time}")
                return player_name, pick_number, int(remaining_time)

    logger.info("No current turn, draft might be complete")
    return None, None, TURN_DURATION

@app.route('/')
@app.route('/index')
def index():
    try:
        if 'username' not in session:
            logger.info("No username in session, redirecting to login")
            return redirect(url_for('login'))

        ensure_draft_columns()

        username = session['username']
        logger.info(f"Loading index for username: {username}")
        # Check if draft has started
        scheduled_start = datetime(2025, 6, 8, 20, 0, 0)  # 8:00 PM EDT
        if datetime.now() < scheduled_start:
            logger.info(f"Access blocked, draft starts at {scheduled_start}, current time {datetime.now()}")
            return render_template('waiting.html', start_time=scheduled_start.strftime('%I:%M %p EDT'))

        golfers = load_golfers()
        picks = load_draft_picks()
        draft_order = get_draft_order()
        player_picks = {player['Player'] if isinstance(player, dict) else player: [] for player in draft_order}

        for pick in picks:
            player = pick['Player']
            if player in player_picks:
                player_picks[player].append(pick)

        current_player, current_pick_number, remaining_time = get_current_turn(picks, draft_order)
        current_player = str(current_player) if current_player else 'N/A'
        logger.info(f"Index - Current player: {current_player}, Pick number: {current_pick_number}, Remaining time: {remaining_time}")

        available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        draft_complete = all(len(player_picks.get(player_name, [])) >= 3 for player_name in player_picks.keys())

        return render_template(
            'index.html',
            username=username,
            golfers=available_golfers,
            picks=picks,
            participants=draft_order,
            player_picks=player_picks,
            draft_complete=draft_complete,
            current_player=current_player,
            current_pick_number=current_pick_number,
            timer_seconds=remaining_time,
            user_player_mapping=user_player_mapping,
            current_event=CURRENT_EVENT
        )
    except Exception as e:
        logger.error(f"Internal Server Error in /index: {str(e)}")
        return "Internal Server Error", 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']
            if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
                session['username'] = username
                logger.info(f"User {username} logged in successfully")
                return redirect(url_for('index'))
            else:
                logger.warning(f"Failed login attempt for username: {username}")
                return render_template('login.html', error="Invalid credentials")
        return render_template('login.html')
    except Exception as e:
        logger.error(f"Internal Server Error in /login: {str(e)}")
        return "Internal Server Error", 500

@app.route('/logout')
def logout():
    try:
        session.pop('username', None)
        logger.info("User logged out")
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Internal Server Error in /logout: {str(e)}")
        return "Internal Server Error", 500

@app.route('/pick', methods=['POST'])
def pick():
    try:
        if 'username' not in session:
            logger.info("No username in session, redirecting to login")
            return redirect(url_for('login'))

        username = session['username']
        golfer = request.form.get('golfer')
        logger.info(f"Pick attempt - Username: {username}, Golfer: {golfer}")
        if not golfer:
            logger.warning("No golfer selected in pick attempt")
            flash('No golfer selected', 'error')
            return redirect(url_for('index'))

        picks = load_draft_picks()
        draft_order = get_draft_order()
        current_player, current_pick_number, _ = get_current_turn(picks, draft_order)

        user_player = user_player_mapping.get(username)
        if user_player != current_player:
            logger.warning(f"Not {user_player}'s turn, current player is {current_player}")
            flash('Not your turn', 'error')
            return redirect(url_for('index'))

        golfers = load_golfers()
        available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        if golfer not in available_golfers:
            logger.warning(f"Golfer {golfer} not available for {user_player}")
            flash('Golfer not available', 'error')
            return redirect(url_for('index'))

        player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == user_player), None)
        if not player_row:
            logger.error(f"Player {user_player} not found in draft board")
            flash('Player not found in draft board', 'error')
            return redirect(url_for('index'))

        column = f'Pick {current_pick_number}'
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pick_time_col = draft_worksheet.find('Pick Time')
        if pick_time_col:
            update_draft_cell(player_row, pick_time_col.col, pick_time)
        else:
            logger.error("Pick Time column not found during pick")
            flash('Pick Time column not found', 'error')
            return redirect(url_for('index'))

        update_draft_cell(player_row, draft_worksheet.find(column).col, golfer)
        logger.info(f"Pick successful: {user_player} picked {golfer}")
        # Invalidate cache after pick
        global cached_picks, last_picks_update
        cached_picks = None
        last_picks_update = None

        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Internal Server Error in /pick: {str(e)}")
        flash('Failed to register pick, please try again', 'error')
        return redirect(url_for('index'))

@app.route('/autopick', methods=['POST'])
def autopick():
    try:
        if 'username' not in session:
            logger.info("No username in session, redirecting to login")
            return redirect(url_for('login'))

        username = session['username']
        picks = load_draft_picks()
        draft_order = get_draft_order()
        current_player, current_pick_number, _ = get_current_turn(picks, draft_order)

        user_player = user_player_mapping.get(username)
        logger.info(f"Autopick - Username: {username}, User Player: {user_player}, Current Player: {current_player}")
        if user_player != current_player:
            flash('Not your turn', 'error')
            return redirect(url_for('index'))

        golfers = load_golfers()
        available_golfers = [g for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        logger.info(f"Available golfers for autopick: {available_golfers}")
        if not available_golfers:
            flash('No golfers available', 'error')
            return redirect(url_for('index'))

        golfer = min(available_golfers, key=lambda x: int(x['Ranking']))['Golfer Name']

        player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == user_player), None)
        if not player_row:
            flash('Player not found in draft board', 'error')
            return redirect(url_for('index'))

        column = f'Pick {current_pick_number}'
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pick_time_col = draft_worksheet.find('Pick Time')
        if pick_time_col:
            update_draft_cell(player_row, pick_time_col.col, pick_time)
        else:
            flash('Pick Time column not found', 'error')
            return redirect(url_for('index'))

        update_draft_cell(player_row, draft_worksheet.find(column).col, golfer)
        logger.info(f"Autopick successful: {user_player} picked {golfer}")
        # Invalidate cache after autopick
        global cached_picks, last_picks_update
        cached_picks = None
        last_picks_update = None

        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Internal Server Error in /autopick: {str(e)}")
        return "Internal Server Error", 500

@app.route('/draft_state', methods=['GET'])
def draft_state():
    try:
        picks = load_draft_picks()
        draft_order = get_draft_order()
        current_player, current_pick_number, remaining_time = get_current_turn(picks, draft_order)
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
            'remaining_time': remaining_time if remaining_time is not None else TURN_DURATION,
            'picks': picks,
            'available_golfers': available_golfers,
            'player_picks': player_picks,
            'draft_complete': draft_complete
        })
    except Exception as e:
        logger.error(f"Internal Server Error in /draft_state: {str(e)}")
        return jsonify({
            'current_player': 'Unknown',
            'current_pick_number': None,
            'remaining_time': TURN_DURATION,
            'picks': cached_picks if cached_picks else [],
            'available_golfers': [],
            'player_picks': {},
            'draft_complete': false,
            'error': str(e)
        }), 200

@app.route('/admin_pick', methods=['POST'])
def admin_pick():
    try:
        if 'username' not in session or session['username'] != 'admin':
            logger.info("Admin pick attempted by non-admin user, redirecting to login")
            return redirect(url_for('login'))

        player = request.form.get('player')
        golfer = request.form.get('golfer')
        logger.info(f"Admin pick attempt - Player: {player}, Golfer: {golfer}")
        if not player or not golfer:
            logger.warning("No player or golfer selected in admin pick attempt")
            flash('Please select both a player and a golfer', 'error')
            return redirect(url_for('index'))

        picks = load_draft_picks()
        golfers = load_golfers()
        available_golfers = [g['Golfer Name'] for g in golfers if g['Golfer Name'] not in [p['Golfer'] for p in picks]]
        if golfer not in available_golfers:
            logger.warning(f"Golfer {golfer} not available for {player}")
            flash('Golfer not available', 'error')
            return redirect(url_for('index'))

        if player not in [p['Player'] for p in get_draft_order()]:
            logger.error(f"Player {player} not found in draft order")
            flash('Invalid player', 'error')
            return redirect(url_for('index'))

        player_row = next((i + 2 for i, row in enumerate(draft_worksheet.get_all_records()) if row['Player'] == player), None)
        if not player_row:
            logger.error(f"Player {player} not found in draft board")
            flash('Player not found in draft board', 'error')
            return redirect(url_for('index'))

        player_picks = [p for p in picks if p['Player'] == player]
        current_pick_number = len(player_picks) + 1
        if current_pick_number > 3:
            logger.warning(f"Player {player} has already made 3 picks")
            flash('Player has already made all picks', 'error')
            return redirect(url_for('index'))

        column = f'Pick {current_pick_number}'
        pick_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pick_time_col = draft_worksheet.find('Pick Time')
        if pick_time_col:
            update_draft_cell(player_row, pick_time_col.col, pick_time)
        else:
            logger.error("Pick Time column not found during admin pick")
            flash('Pick Time column not found', 'error')
            return redirect(url_for('index'))

        update_draft_cell(player_row, draft_worksheet.find(column).col, golfer)
        logger.info(f"Admin pick successful: {player} picked {golfer}")
        # Invalidate cache after admin pick
        global cached_picks, last_picks_update
        cached_picks = None
        last_picks_update = None

        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Internal Server Error in /admin_pick: {str(e)}")
        flash('Failed to register admin pick, please try again', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)