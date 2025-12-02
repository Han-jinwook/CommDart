from flask_cors import CORS
import os
import random
import datetime

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from wtforms import Form, StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'

# CORS 설정 추가
CORS(app)

# [수정됨] SocketIO 객체 수정: async_mode를 'eventlet'으로 변경 (서버 환경과 일치)
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

@app.route('/guest')
def guest_view():
    return render_template('index.html',
                           participants=participants,
                           colors=colors,
                           user=None,
                           is_guest=True)
    
# ----- 참가자 로딩 함수 (가나다순 정렬 추가) -----
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
        
        # 가나다순으로 정렬 (이름 기준)
        participants.sort(key=lambda x: x[0])
        
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
    if current_user.is_authenticated:
        # 로그인한 사용자는 바로 게임 화면으로
        return render_template('index.html',
                             participants=participants,
                             colors=colors,
                             user=current_user)
    else:
        # 로그인하지 않은 사용자는 로그인 페이지로
        return render_template('welcome.html')

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
    
    # 기존 게임 정보 초기화
    if user_id not in games:
        games[user_id] = {
            'running': False,
            'target_time': None,
            'current_angle': 0.0,
            'final_winner': None,
            'winner_announced': False  # 당첨자 발표 상태 추가
        }
    else:
        # 기존 게임 객체가 있으면 winner_announced 상태 초기화
        games[user_id]['winner_announced'] = False
    
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

    # 총 지속시간도 저장 (추가)
    game['total_duration'] = duration
    
    # 최종 회전 각도 미리 결정 (720~1440도 사이 랜덤)
    # 정확한 계산을 위해 소수점 자리까지 유지
    final_angle = random.uniform(720, 1440)
    
    # 화살표는 12시 방향(0도)에 고정됨
    # 전체 회전 각도 저장
    game['current_angle'] = final_angle
    
    # 화살표가 가리키는 실제 각도 계산 (0도가 12시 방향)
    # 원판이 시계 방향으로 회전하면, 화살표의 상대적 위치는 반시계 방향으로 이동하는 효과
    # 정확한 모듈로 연산 적용 후 정규화
    relative_angle = (360 - (final_angle % 360)) % 360
    
    print(f"DEBUG: 최종 회전 각도: {final_angle:.2f}°, 상대 각도: {relative_angle:.2f}°")
    
    # 정확한 당첨자 계산 (화살표가 가리키는 섹터의 참가자)
    winner = calculate_winner_at_angle(relative_angle)
    game['final_winner'] = winner
    
    # 게임 상태 업데이트
    game['running'] = True

    # 이 부분 추가: 게임 정보를 'global_game' 키에 복사
    games['global_game'] = game.copy()
    
    # 클라이언트에게 모든 정보를 한 번에 전송
    socketio.emit('start_game', {
        'duration': duration,
        'finalAngle': final_angle,
        'winner': winner
    }, namespace='/')
        
    # 모든 클라이언트에게 게임 상태 정보 브로드캐스트
    socketio.emit('game_status', {
        'target_time': game['target_time'].isoformat(),
        'final_winner': winner
    }, namespace='/')
    
    # 비프음 재생
    socketio.emit('play_beep', namespace='/')
    
    # 종료 시간에 팡파레 및 당첨자 알림을 위한 타이머 설정
    def schedule_end_notification():
        socketio.sleep(duration)
        # 종료 시간에 도달했음을 알리는 로그만 남기고,
        # 당첨자 발표는 클라이언트의 애니메이션 완료 후 confirm_winner에서만 처리
        print(f"DEBUG: 서버에서 예정된 종료 시간에 도달. 예상 당첨자: {winner}")
        game['running'] = False
    
    # 백그라운드 작업으로 타이머 실행
    socketio.start_background_task(schedule_end_notification)

@socketio.on('confirm_winner')
def handle_confirm_winner():
    """
    애니메이션 완료 후 당첨자 확인 이벤트 처리
    클라이언트에서 애니메이션이 완료된 후 호출됨
    """
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    
    # 먼저 user_id로 게임 정보 확인
    if user_id in games and games[user_id]['final_winner']:
        winner = games[user_id]['final_winner']
    # global_game에서도 확인 (추가된 부분)
    elif 'global_game' in games and games['global_game'].get('final_winner'):
        winner = games['global_game']['final_winner']
    # 다른 진행 중인 게임이 있는지 확인 (추가된 부분)
    else:
        winner = None
        # 모든 게임 중 마지막으로 시작된 게임 찾기
        latest_game = None
        latest_time = None
        
        for gid, game in games.items():
            if game.get('target_time') and game.get('final_winner'):
                if latest_time is None or game['target_time'] > latest_time:
                    latest_time = game['target_time']
                    latest_game = game
        
        if latest_game:
            winner = latest_game['final_winner']
    
    # 당첨자를 찾았으면 발표
    if winner:
        print(f"DEBUG: 확정된 당첨자: {winner}")
        
        # 당첨자 정보 전송
        socketio.emit('update_winner', {'winner': winner}, namespace='/')
        socketio.emit('play_fanfare', namespace='/')
    else:
        # 게임 정보가 없거나 당첨자가 설정되지 않은 경우 오류 메시지 전송
        print("ERROR: 당첨자 정보를 찾을 수 없음")
        socketio.emit('error', {'message': '당첨자 정보를 찾을 수 없습니다.'}, namespace='/')

@socketio.on('request_game_status')
def handle_request_game_status():
    """
    클라이언트(특히 게스트)가 접속했을 때 현재 게임 상태 정보를 요청
    """
    print("게임 상태 요청 수신")
    user_id = current_user.id if current_user.is_authenticated else 'anonymous'
    
    # 현재 진행 중인 게임이 있는지 확인 (global_game이나 다른 게임)
    active_game = None
    
    if 'global_game' in games and games['global_game'].get('target_time'):
        active_game = games['global_game']
    elif user_id in games and games[user_id].get('target_time'):
        active_game = games[user_id]
    else:
        # 다른 활성 게임 찾기
        for gid, game in games.items():
            if game.get('running') and game.get('target_time'):
                active_game = game
                break
    
    if active_game:
        now = datetime.datetime.utcnow()
        target_time = active_game['target_time']
        
        # 게임이 아직 진행 중인지 확인
        if target_time > now:
            # 남은 시간 계산
            duration_left = (target_time - now).total_seconds()
            
            # 클라이언트에게 게임 상태, 회전 정보, 남은 시간 전송
            socketio.emit('game_status', {
                'target_time': target_time.isoformat(),
                'final_winner': active_game.get('final_winner'),
                'is_running': active_game.get('running', False),
                'finalAngle': active_game.get('current_angle', 0),
                'duration_left': duration_left,
                'total_duration': active_game.get('total_duration', 0)
            }, namespace='/')
            
            # 진행 중인 게임이 있으므로 start_game 이벤트도 전송
            socketio.emit('start_game', {
                'duration': duration_left,
                'finalAngle': active_game.get('current_angle', 0),
                'winner': active_game.get('final_winner')
            }, namespace='/', to=request.sid)  # 요청한 클라이언트에게만 전송
            
            print(f"진행 중인 게임 정보 전송: 남은 시간 {duration_left:.2f}초")
            return
        else:
            # 게임이 이미 종료됨 - 결과만 전송
            socketio.emit('game_status', {
                'target_time': target_time.isoformat(),
                'final_winner': active_game.get('final_winner'),
                'is_running': False
            }, namespace='/')
            print("이미 종료된 게임 정보 전송")
            return
    
    # 진행 중인 게임이 없는 경우
    socketio.emit('game_status', {}, namespace='/')
    print("진행 중인 게임 없음")
        
def calculate_winner_at_angle(angle):
    """
    특정 각도에서의 당첨자를 계산하는 함수
    angle: 0도는 12시 방향, 시계방향으로 증가
    """
    print(f"DEBUG: 당첨자 계산에 사용되는 각도: {angle:.2f}°")
    
    # 각 섹터 배치를 계산하기 위한 설정
    cumulative_angle = 0.0
    
    # 각 참가자의 세그먼트 정보를 먼저 계산하고 저장
    segments = []
    
    for i, (name, cnt) in enumerate(zip(names, counts)):
        portion = cnt / total_count
        sector_size = portion * 360.0
        sector_start = cumulative_angle
        sector_end = cumulative_angle + sector_size
        
        # 세그먼트 정보 저장
        segments.append({
            'name': name,
            'start': sector_start,
            'end': sector_end,
            'size': sector_size
        })
        
        # 디버깅 정보 출력
        print(f"DEBUG: Sector {i}: {name}, {sector_start:.2f}° ~ {sector_end:.2f}°, size: {sector_size:.2f}°")
        
        cumulative_angle += sector_size
    
    # 입력 각도를 0-360 범위로 정규화
    normalized_angle = angle % 360
    
    # 세그먼트별로 검사 - 정확한 범위 검사
    for segment in segments:
        # 화살표가 현재 세그먼트 내에 있는지 확인 (시작 경계도 포함)
        if segment['start'] <= normalized_angle < segment['end']:
            print(f"DEBUG: 당첨자 결정 - {segment['name']} (각도: {normalized_angle:.2f}°)")
            return segment['name']
    
    # 경계 조건 처리 (360도/0도 근처)
    if normalized_angle >= segments[-1]['start'] or normalized_angle < segments[0]['start']:
        print(f"DEBUG: 경계 조건 처리 - 첫 번째 참가자: {names[0]} (각도: {normalized_angle:.2f}°)")
        return names[0]
    
    # 여기까지 오면 오류 상황
    print(f"ERROR: 당첨자를 결정할 수 없음 (각도: {normalized_angle:.2f}°)")
    return names[0]  # 기본값으로 첫 번째 참가자 반환

# [수정됨] 시간 전송 로직 수정 (threading.Thread 제거하고 socketio 백그라운드 태스크 사용)
def send_current_time():
    """현재 시간을 1초마다 클라이언트로 전송하는 함수"""
    while True:
        now = datetime.datetime.now().strftime('%H:%M:%S')
        # print(f"[DEBUG] send_current_time 실행 중 - 현재 시간 전송: {now}") # 로그 너무 많으면 주석 처리
        socketio.emit('update_current_time', {'current_time': now}, namespace='/')
        socketio.sleep(1)  # [중요] time.sleep 대신 socketio.sleep 사용

# 서버 시작 시 현재 시간 업데이트 쓰레드 실행
# [수정됨] threading.Thread 대신 socketio.start_background_task 사용
socketio.start_background_task(send_current_time)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
