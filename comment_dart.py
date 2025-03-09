import os
import random
import datetime

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from wtforms import Form, StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

from flask_socketio import SocketIO
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'

socketio = SocketIO(app, async_mode='eventlet')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    # ... (생략) ...
    return None

class LoginForm(Form):
    username = StringField('ID', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... (생략) ...
    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    # ... (생략) ...
    return redirect(url_for('index'))

def load_participants(filename="participants.txt"):
    # ... (생략) ...
    return participants

participants = load_participants()
# ... (생략)...

@app.route('/')
def index():
    return render_template('index.html', participants=participants, user=current_user)

games = {}

@socketio.on('start_rotation')
def handle_start_rotation(data):
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    if user_id not in games:
        games[user_id] = {
            'running': False,
            'target_time': None,
            'current_angle': 0.0,
            'final_winner': None
        }
    game = games[user_id]

    # UTC 시간 비교
    now = datetime.datetime.utcnow()
    t_str = data['time']
    target_today_str = now.strftime('%Y-%m-%d') + ' ' + t_str
    try:
        game['target_time'] = datetime.datetime.strptime(target_today_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        socketio.emit('error', {'message': '시간 형식이 잘못되었습니다.'}, namespace='/')
        return

    if game['target_time'] <= now:
        socketio.emit('error', {'message': '미래 시각을 입력해주세요.'}, namespace='/')
        return

    game['running'] = True
    game['final_winner'] = None
    game['current_angle'] = 0.0

    socketio.emit('play_beep', namespace='/')
    socketio.start_background_task(rotate, user_id)

def rotate(user_id):
    if user_id not in games:
        return

    game = games[user_id]
    while game['running']:
        now = datetime.datetime.utcnow()
        time_left = (game['target_time'] - now).total_seconds()

        if time_left <= 0:
            game['running'] = False
            game['current_angle'] %= 360
            winner = calculate_winner(game['current_angle'])
            game['final_winner'] = winner
            socketio.emit('update_winner', {'winner': winner}, namespace='/')
            socketio.emit('play_fanfare', namespace='/')
            break

        speed = max(1, min(6, time_left * 2))
        game['current_angle'] += random.uniform(1, speed)
        game['current_angle'] %= 360

        # 여기서 broadcast=True 제거
        socketio.emit('update_chart', {
            'angle': game['current_angle'],
            'winner': game['final_winner']
        }, namespace='/')

        eventlet.sleep(0.05)

def calculate_winner(final_angle):
    # ... (생략) ...
    return winner

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
