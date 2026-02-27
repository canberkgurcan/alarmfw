# alarmfw

## Build
docker build -t alarmfw:latest .

## Run (compose)
cp config/examples/minimal.env .env
mkdir -p state
docker compose up --build --abort-on-container-exit

## Run (plain docker)
docker run --rm \
  -e ZABBIX_URL -e ZABBIX_TOKEN \
  -e SMTP_HOST -e SMTP_PORT -e SMTP_USER -e SMTP_PASS -e SMTP_TO \
  -v "$PWD/config:/config:ro" \
  -v "$PWD/state:/state" \
  alarmfw:latest run --config /config/base.yaml
