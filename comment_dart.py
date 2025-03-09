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
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", engineio_logger=True, logger=True)

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

@socketio.on('start_rotation')
def handle_start_rotation(data):
    """
    data = { time: "HH:MM:SS" } (UTC 기준)
    """
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
        socketio.emit('error', {'message': '시간 형식이 잘못되었습니다.'}, namespace='/')
        return

    print("DEBUG: now =", now, "target_time =", game['target_time'])
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
    
    # 회전 시작 시간 저장
    start_time = datetime.datetime.utcnow()
    total_duration = (game['target_time'] - start_time).total_seconds()
    
    while game['running']:
        now = datetime.datetime.utcnow()
        time_left = (game['target_time'] - now).total_seconds()
        elapsed = total_duration - time_left
        
        if time_left <= 0:
            game['running'] = False
            game['current_angle'] %= 360
            winner = calculate_winner(game['current_angle'])
            game['final_winner'] = winner
            socketio.emit('update_winner', {'winner': winner}, namespace='/')
            socketio.emit('play_fanfare', namespace='/')
            break
        
        # 속도 조정 로직
        acceleration_time = min(5.0, total_duration / 3)  # 초반 가속 시간 (최대 5초)
        deceleration_time = min(5.0, total_duration / 3)  # 후반 감속 시간 (최대 5초)
        
        if elapsed < acceleration_time:
            # 초반 가속 단계 - 천천히 시작해서 빨라짐
            speed_factor = elapsed / acceleration_time  # 0에서 1로 증가
            max_speed = 30
            speed = max(3, max_speed * speed_factor)
        elif time_left < deceleration_time:
            # 후반 감속 단계 - 점점 느려짐
            speed_factor = time_left / deceleration_time  # 1에서 0으로 감소
            max_speed = 30
            speed = max(1, max_speed * speed_factor)
        else:
            # 중간 최대 속도 단계
            speed = 30
        
        angle_step = random.uniform(speed * 0.5, speed)
        game['current_angle'] += angle_step
        game['current_angle'] %= 360
        
        socketio.emit('update_chart',
                     {'angle': game['current_angle'], 'winner': game['final_winner']},
                     namespace='/')
        eventlet.sleep(0.05)

def calculate_winner(final_angle):
    # 화살표는 3시 방향(0도)에 있으므로, 회전한 각도의 반대 방향에 있는 섹터가 화살표와 만나게 됨
    # 따라서 360에서 각도를 빼주어 반대 방향 계산
    pointer_angle = (360 - final_angle) % 360
    print(f"Final angle: {final_angle}, Adjusted pointer angle: {pointer_angle}")
    
    cumulative_angle = 0.0
    for name, cnt in zip(names, counts):
        portion = cnt / total_count
        sector_angle = portion * 360.0
        seg_start = cumulative_angle % 360
        seg_end = (cumulative_angle + sector_angle) % 360
        print(f"Name: {name}, Range: {seg_start}-{seg_end}, Pointer: {pointer_angle}")
        
        if in_arc_range(pointer_angle, seg_start, seg_end):
            print(f"WINNER SELECTED: {name}")
            return name
        cumulative_angle += sector_angle
    
    print(f"No winner found, returning last name: {names[-1]}")
    return names[-1]

def in_arc_range(x, start, end):
    if start <= end:
        return start <= x < end
    else:
        return x >= start or x < end

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
