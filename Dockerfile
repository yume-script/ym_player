# YMPlayer 도커 이미지 - 설치 방법은 docs/install-docker.md
FROM python:3.12-slim

# ffmpeg: 텔레그램 mkv·HEVC 영상 서버 변환, 자동 올리기 인코딩·나눠 올리기
# rclone: 자동 올리기의 "rclone 드라이브 · 폴더" 규칙에서 rclone 원격(이름:경로)을 볼 때 (mount 폴더만 쓰면 없어도 됨)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip tzdata ffmpeg fonts-noto-cjk \
    && curl -fsSL https://rclone.org/install.sh | bash \
    && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ym_player ./ym_player

# 설정·사용자·색인·캐시는 /data (볼륨). 컨테이너 밖에서 접속하므로 0.0.0.0
ENV MP_DATA_DIR=/data MP_HOST=0.0.0.0 MP_PORT=8899 PYTHONUNBUFFERED=1 TZ=Asia/Seoul
VOLUME ["/data"]
EXPOSE 8899
STOPSIGNAL SIGTERM
CMD ["python", "-m", "ym_player"]
