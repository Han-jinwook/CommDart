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
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    if user_id not in games:
        games[user_id] = {
            'running': False,
            'target_time': None,
            'current_angle': 0.0,
            'final_winner': None
        }
    game = games[user_id]

    now = datetime.datetime.utcnow()
    t_str = data['time']
    target_today_str = now.strftime('%Y-%m-%d') + ' ' + t_str
    try:
        game['target_time'] = datetime.datetime.strptime(target_today_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        print("DEBUG: Invalid time format:", t_str)
        socketio.emit('error', {'message': '시간 형식이 잘못되었습니다.'}, namespace='/')
        return

    print("DEBUG: now =", now, "target_time =", game['target_time'])
    if game['target_time'] <= now:
        socketio.emit('error', {'message': '미래 시각을 입력해주세요.'}, namespace='/')
        return

    # 지속 시간 계산
    duration = (game['target_time'] - now).total_seconds()
    
    # 최종 회전 각도 미리 결정 (720~1440도 사이 랜덤)
    final_angle = random.uniform(720, 1440)
    game['current_angle'] = final_angle % 360
    
    # 화살표는 12시 방향(270도)에 고정
    # 원판의 회전 각도에 따라 당첨자 계산
    pointer_angle = (270 - game['current_angle']) % 360
    winner = calculate_winner_at_angle(pointer_angle)
    game['final_winner'] = winner
    
    # 게임 상태 업데이트
    game['running'] = True
    
    # 클라이언트에게 모든 정보를 한 번에 전송
    socketio.emit('start_game', {
        'duration': duration,
        'finalAngle': final_angle,
        'winner': winner
    }, namespace='/')
    
    # 비프음 재생
    socketio.emit('play_beep', namespace='/')
    
    # 종료 시간에 팡파레 및 당첨자 알림을 위한 타이머 설정
    def schedule_end_notification():
        socketio.sleep(duration)
        socketio.emit('update_winner', {'winner': winner}, namespace='/')
        socketio.emit('play_fanfare', namespace='/')
        game['running'] = False
    
    # 백그라운드 작업으로 타이머 실행
    socketio.start_background_task(schedule_end_notification)

def calculate_winner_at_angle(pointer_angle):
    """특정 각도에서의 당첨자를 계산하는 함수"""
    # 각 섹터 배치를 계산하기 위한 설정
    cumulative_angle = 0.0
    for name, cnt in zip(names, counts):
        portion = cnt / total_count
        sector_size = portion * 360.0
        sector_start = cumulative_angle
        sector_end = cumulative_angle + sector_size
        
        if sector_start <= pointer_angle < sector_end:
            return name
        
        cumulative_angle += sector_size
    
    # 경계 조건 처리
    if pointer_angle >= cumulative_angle or pointer_angle < 0:
        return names[0]
    
    return names[-1]

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
