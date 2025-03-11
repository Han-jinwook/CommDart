from flask_cors import CORS
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

# CORS 설정 추가
CORS(app)

# SocketIO 객체 수정
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# ----- 로그인 매니저 설정 -----
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    users_file = os.path.join(BASE_DIR, 'users.txt')
    try:
        with open(users_file, 'r', encoding='utf-8') as f:
            for line in f:
                user_id_stored, password_hash = line.strip().split(':')
                if user_id == user_id_stored:
                    return User(user_id)
    except Exception as e:
        print("[ERROR] load_user:", e)
    return None

# ----- 로그인 폼 (WTForms만 사용) -----
class LoginForm(Form):
    username = StringField('ID', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        form = LoginForm(request.form)
        if form.validate():
            username = form.username.data
            password = form.password.data
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            users_file = os.path.join(BASE_DIR, 'users.txt')
            try:
                with open(users_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        user_id, password_hash = line.strip().split(':')
                        if user_id == username and password_hash == password:
                            user = User(user_id)
                            login_user(user)
                            flash('로그인 성공!', 'success')
                            return redirect(url_for('index'))
                flash('잘못된 ID 또는 비밀번호입니다.', 'danger')
            except Exception as e:
                flash('사용자 파일을 찾을 수 없습니다.', 'danger')
        else:
            flash('폼 검증 실패.', 'danger')
        return render_template('login.html', form=form)
    else:
        form = LoginForm()
        return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for('index'))

# ----- 참가자 로딩 함수 (수정된 부분) -----
def load_participants(filename="participants.txt"):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    participants_file = os.path.join(BASE_DIR, filename)
    try:
        with open(participants_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        participants_dict = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            try:
                count = float(parts[1])
            except ValueError:
                count = 1.0
            participants_dict[name] = participants_dict.get(name, 0) + count

        # participants 리스트를 생성하고, 별명 100개 이상이면 숫자로 대체
        participants = [(name, int(count)) for name, count in participants_dict.items()]
        if len(participants) > 100:
            participants = [(f"{i+1}", count) for i, (name, count) in enumerate(participants)]
        return participants

    except Exception as e:
        print("[ERROR] Failed to load participants:", e)
        return None

participants = load_participants()
if not participants:
    print("[ERROR] Failed to load participants. Exiting...")
    exit()

# ----- colors 리스트 생성 -----
colors = []
for i in range(len(participants)):
    h = i * 360 / len(participants)
    colors.append(f"hsl({h}, 70%, 50%)")

# ----- 전체 참가자 이름, 댓글 수, 총합 등 (필요 시) -----
names = [p[0] for p in participants]
counts = [p[1] for p in participants]
total_count = sum(counts)

@app.route('/')
def index():
    return render_template('index.html',
                           participants=participants,
                           colors=colors,
                           user=current_user)

# ----- 회전 게임 로직 -----
games = {}

@socketio.on('connect')
def handle_connect():
    print("Client connected:", request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print("Client disconnected:", request.sid)

@socketio.on('reset_game')
def handle_reset_game():
    print("Game reset request received")
    socketio.emit('game_reset_complete', namespace='/')

@socketio.on('start_rotation')
def handle_start_rotation(data):
    """
    data = { time: "HH:MM:SS" } (UTC 기준)
    """
    print("Received start_rotation with data:", data)
    
    # Parse time and validate
    now = datetime.datetime.utcnow()
    t_str = data['time']
    target_today_str = now.strftime('%Y-%m-%d') + ' ' + t_str
    
    try:
        target_time = datetime.datetime.strptime(target_today_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        print("Invalid time format:", t_str)
        socketio.emit('error', {'message': '시간 형식이 잘못되었습니다.'}, namespace='/')
        return
    
    print("Target time:", target_time)
    if target_time <= now:
        socketio.emit('error', {'message': '미래 시각을 입력해주세요.'}, namespace='/')
        return
    
    # Calculate duration and angles
    duration = (target_time - now).total_seconds()
    final_angle = random.uniform(720, 1440)  # 2-4 full rotations
    
    # Choose a random winner based on weights
    weighted_names = []
    for name, count in zip(names, counts):
        weighted_names.extend([name] * count)
    
    winner = random.choice(weighted_names)
    print(f"Selected winner: {winner}, final angle: {final_angle}")
    
    # Send start_game event with all data
    socketio.emit('start_game', {
        'duration': duration,
        'finalAngle': final_angle,
        'winner': winner
    }, namespace='/')
    
    # Play sound
    socketio.emit('play_beep', namespace='/')
    
    # Schedule end notification
    def notify_end():
        socketio.sleep(duration)
        print(f"Game ended, winner: {winner}")
        socketio.emit('update_winner', {'winner': winner}, namespace='/')
        socketio.emit('play_fanfare', namespace='/')
    
    socketio.start_background_task(notify_end)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
