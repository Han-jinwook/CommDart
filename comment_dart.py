import os
import random
import datetime
import numpy as np
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'

# eventlet 모드로 사용 (pip install eventlet 필요)
socketio = SocketIO(app, async_mode='eventlet')

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
    except:
        pass
    return None

# ----- 로그인 폼 -----
class LoginForm(FlaskForm):
    username = StringField('ID', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

# ----- 참가자 로딩 -----
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
        participants = [(name, int(count)) for name, count in participants_dict.items()]

        # 별명 100개 이상이면, 1~100까지 숫자로 대체
        if len(participants) > 100:
            participants = [(f"{i+1}", count) for i, (name, count) in enumerate(participants)]
        return participants
    except:
        return None

participants = load_participants()
if not participants:
    print("[ERROR] Failed to load participants. Check participants.txt.")
    exit()

names = [p[0] for p in participants]
counts = [p[1] for p in participants]
total_count = sum(counts)
# 색상 리스트
colors = [f"hsl({i * 360 / len(participants)}, 70%, 50%)" for i in range(len(participants))]

# ----- Flask Routes -----
@app.route('/')
def index():
    return render_template('index.html',
                           participants=participants,
                           game_name_kr="썬드림 댓글 다트 로또게임",
                           game_name_en="Sundream Comment Dart Lotto",
                           user=current_user,
                           colors=colors)

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        users_file = os.path.join(BASE_DIR, 'users.txt')
        try:
            with open(users_file, 'r', encoding='utf-8') as f:
                for line in f:
                    user_id, password_hash = line.strip().split(':')
                    if user_id == username and password == password_hash:
                        user = User(user_id)
                        login_user(user)
                        flash('로그인 성공!', 'success')
                        return redirect(url_for('index'))
            flash('잘못된 ID 또는 비밀번호입니다.', 'danger')
        except:
            flash('사용자 파일을 찾을 수 없습니다.', 'danger')

    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    if current_user.is_authenticated:
        logout_user()
    return redirect(url_for('index'))

# ----- 게임 로직 관리 -----
games = {}

@socketio.on('start_rotation')
def handle_start_rotation(data):
    # 현재 로그인 사용자 구분
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    if user_id not in games:
        games[user_id] = {
            'running': False,
            'target_time': None,
            'current_angle': 0.0,
            'final_winner': None
        }
    game = games[user_id]

    # 타겟 시간 파싱
    t_str = data['time']
    now = datetime.datetime.now()
    target_today_str = now.strftime('%Y-%m-%d') + ' ' + t_str

    try:
        game['target_time'] = datetime.datetime.strptime(target_today_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        emit('error', {'message': '시간 형식이 잘못되었습니다. HH:MM:SS 형식으로 입력.'}, broadcast=True)
        return

    if game['target_time'] <= now:
        emit('error', {'message': '미래 시각을 입력해주세요.'}, broadcast=True)
        return

    game['running'] = True
    game['final_winner'] = None
    game['current_angle'] = 0.0

    # 소리 재생 신호 (옵션)
    emit('play_beep', broadcast=True)
    # 회전 스레드 시작
    socketio.start_background_task(rotate, user_id)

def rotate(user_id):
    if user_id not in games:
        return

    game = games[user_id]
    while game['running']:
        now = datetime.datetime.now()
        time_left = (game['target_time'] - now).total_seconds()

        if time_left <= 0:
            # 회전 종료
            game['running'] = False
            game['current_angle'] %= 360
            winner = calculate_winner(game['current_angle'])
            game['final_winner'] = winner
            emit('update_winner', {'winner': winner}, broadcast=True)
            emit('play_fanfare', broadcast=True)
            break

        # time_left에 따라 속도 조절
        # 1 ~ 6 사이로 회전
        speed = max(1, min(6, time_left * 2))
        # 랜덤 각도만큼 누적
        game['current_angle'] += random.uniform(1, speed)
        game['current_angle'] %= 360

        # 클라이언트에게 각도 업데이트
        emit('update_chart',
             {'angle': game['current_angle'], 'winner': game['final_winner']},
             broadcast=True)

        # 0.05초 대기 후 반복
        socketio.sleep(0.05)

# ----- 당첨자 계산 -----
def calculate_winner(final_angle):
    pointer_angle = final_angle % 360
    cumulative_angle = 0.0
    for name, count in zip(names, counts):
        portion = count / total_count
        sector_angle = portion * 360.0
        seg_start = cumulative_angle % 360
        seg_end = (cumulative_angle + sector_angle) % 360
        if in_arc_range(pointer_angle, seg_start, seg_end):
            return name
        cumulative_angle += sector_angle
    # 혹시 못찾으면 마지막
    return names[-1]

def in_arc_range(x, start, end):
    if start <= end:
        return (start <= x < end)
    else:
        return (x >= start) or (x < end)

if __name__ == '__main__':
    # debug=True를 사용하면 코드 변경 시 자동 재시작.
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
