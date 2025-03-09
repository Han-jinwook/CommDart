bind = "0.0.0.0:$PORT"  # Render의 PORT 환경 변수 사용
workers = 1  # WebSocket 지원을 위해 반드시 1로 설정
worker_class = "eventlet"
worker_connections = 1000
timeout = 300
keepalive = 5
preload_app = True
