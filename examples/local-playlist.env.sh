# Example: local server playlist probe (no Hotmart JWT needed)

export PLAYLIST_URL='http://localhost:8080/video/master.m3u8'
# optional:
# export REFERER_HEADER='http://localhost:3000/'

echo "PLAYLIST_URL=$PLAYLIST_URL"
# SECONDS_CLIP=5 ./bin/fast-hls-security-test.sh clip
