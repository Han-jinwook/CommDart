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
    
    # 화살표는 12시 방향(0도)에 고정됨
    # 전체 회전 각도 저장
    game['current_angle'] = final_angle
    
    # 화살표가 가리키는 실제 각도 계산 (0도가 12시 방향)
    # 원판이 시계 방향으로 회전하면, 화살표의 상대적 위치는 반시계 방향으로 이동하는 효과
    relative_angle = 360 - (final_angle % 360)
    
    print(f"DEBUG: 최종 회전 각도: {final_angle}°, 상대 각도: {relative_angle}°")
    
    # 정확한 당첨자 계산 (화살표가 가리키는 섹터의 참가자)
    winner = calculate_winner_at_angle(relative_angle)
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
        # 팡파레 소리 재생 (당첨자 표시는 클라이언트 애니메이션 완료 후 confirm_winner에서 처리)
        socketio.emit('play_fanfare', namespace='/')
        game['running'] = False
        print(f"DEBUG: 서버에서 예정된 종료 시간에 도달. 당첨자: {winner}")
    
    # 백그라운드 작업으로 타이머 실행
    socketio.start_background_task(schedule_end_notification)

@socketio.on('confirm_winner')
def handle_confirm_winner():
    """
    애니메이션 완료 후 당첨자 확인 이벤트 처리
    클라이언트에서 애니메이션이 완료된 후 호출됨
    """
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    if user_id in games and games[user_id]['final_winner']:
        winner = games[user_id]['final_winner']
        print(f"DEBUG: 확정된 당첨자: {winner}")
        
        # 당첨자 정보 전송
        socketio.emit('update_winner', {'winner': winner}, namespace='/')
        
        # 팡파레 소리는 이미 schedule_end_notification에서 재생했을 수 있지만,
        # 타이밍 문제로 소리가 재생되지 않았을 경우를 대비해 다시 한번 재생
        socketio.emit('play_fanfare', namespace='/')
    else:
        # 게임 정보가 없거나 당첨자가 설정되지 않은 경우 오류 메시지 전송
        print("ERROR: 당첨자 정보를 찾을 수 없음")
        socketio.emit('error', {'message': '당첨자 정보를 찾을 수 없습니다.'}, namespace='/')

def calculate_winner_at_angle(angle):
    """
    특정 각도에서의 당첨자를 계산하는 함수
    angle: 0도는 12시 방향, 시계방향으로 증가
    """
    print(f"DEBUG: 당첨자 계산에 사용되는 각도: {angle:.2f}°")
    
    # 각 섹터 배치를 계산하기 위한 설정
    cumulative_angle = 0.0
    
    # 각 참가자의 세그먼트 정보를 정확히 계산
    for i, (name, cnt) in enumerate(zip(names, counts)):
        portion = cnt / total_count
        sector_size = portion * 360.0
        sector_start = cumulative_angle
        sector_end = cumulative_angle + sector_size
        
        # 디버깅 정보 출력
        print(f"DEBUG: Sector {i}: {name}, {sector_start:.2f}° ~ {sector_end:.2f}°, size: {sector_size:.2f}°")
        
        # 화살표가 현재 세그먼트 내에 있는지 확인
        if sector_start <= angle < sector_end:
            print(f"DEBUG: 당첨자 결정 - {name} (각도: {angle:.2f}°)")
            return name
        
        cumulative_angle += sector_size
    
    # 경계 조건 처리 (360도/0도 근처)
    if angle >= cumulative_angle or angle < 0:
        print(f"DEBUG: 경계 조건 처리 - 첫 번째 참가자: {names[0]} (각도: {angle:.2f}°)")
        return names[0]
    
    # 여기까지 오면 오류 상황
    print(f"ERROR: 당첨자를 결정할 수 없음 (각도: {angle:.2f}°)")
    return names[0]  # 기본값으로 첫 번째 참가자 반환

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
