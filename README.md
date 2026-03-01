# AlarmFW

OpenShift pod sağlığını izleyen, Zabbix ve SMTP üzerinden alarm gönderen monitoring sistemi.

## Servisler

| Servis | Port | Açıklama |
|---|---|---|
| `alarmfw` | — | Python alarm runner (periyodik check döngüsü) |
| `alarmfw-api` | 8000 | FastAPI yönetim API'si |
| `alarmfw-observe` | 8001 | FastAPI gözlem API'si (Prometheus, OCP events) |
| `alarmfw-ui` | 3000 | Next.js yönetim arayüzü |

---

## Docker Compose ile Kurulum (Linux Host)

### 1. Repoları Klonla

```bash
# Tüm servisler aynı dizin altında olmalı
mkdir -p ~/alarmfw-workspace && cd ~/alarmfw-workspace
git clone git@github.com:canberkgurcan/alarmfw.git
git clone git@github.com:canberkgurcan/alarmfw-api.git
git clone git@github.com:canberkgurcan/alarmfw-observe.git
git clone git@github.com:canberkgurcan/alarmfw-ui.git
```

### 2. Ortam Değişkenlerini Ayarla

```bash
cd alarmfw
cp .env.example .env
```

`.env` dosyasını düzenle:

```env
# Token dosyalarının bulunduğu dizin (oc login tokenları, prometheus tokenları)
SECRETS_DIR=/path/to/alarmfw-secrets

# SMTP
SMTP_HOST=mailrelay.internal
SMTP_PORT=25
SMTP_USER=alarmfw@sirket.com
SMTP_PASS=
SMTP_TO=ops@sirket.com

# Zabbix
ZABBIX_URL=https://zabbix.internal
ZABBIX_TOKEN=
```

### 3. Secrets Dizinini Oluştur

```bash
mkdir -p /path/to/alarmfw-secrets
# Token dosyaları bootstrap sonrası buraya yazılacak
```

### 4. Servisleri Başlat

```bash
docker compose up -d
# veya rebuild ile:
docker compose build && docker compose up -d
```

### 5. İlk Kurulum (Bootstrap)

Servisler ayaktayken UI'ya gir → **Manage → Admin Console** ve bootstrap scriptini çalıştır:

```bash
bash /app/scripts/bootstrap.sh \
  --cluster CLUSTER_ADI \
  --ocp-api https://api.CLUSTER.DOMAIN:6443 \
  --ocp-token TOKEN \
  --prometheus-url https://thanos-querier.apps.CLUSTER.DOMAIN \
  --prometheus-token PROMETHEUS_TOKEN \
  --smtp-host mailrelay.internal --smtp-port 25 --smtp-to ops@sirket.com \
  --zabbix-url https://zabbix.internal --zabbix-token ZABBIX_TOKEN
```

> Birden fazla cluster için scripti her cluster için ayrı çalıştır.

### Servis Durumu

```bash
docker compose ps
docker compose logs -f alarmfw-api
```

---

## OpenShift (OCP) ile Kurulum

### Gereksinimler

- `oc` CLI kurulu ve cluster'a erişim
- Nexus/Harbor registry erişimi
- Jenkins (pipeline için)
- ReadWriteMany destekli StorageClass (NFS veya Ceph)

### 1. Namespace Oluştur

```bash
oc new-project alarmfw-prod
```

### 2. Registry Credentials Ekle

```bash
oc create secret docker-registry nexus-pull-secret \
  --docker-server=REGISTRY_URL \
  --docker-username=KULLANICI \
  --docker-password=SIFRE \
  -n alarmfw-prod

oc secrets link default nexus-pull-secret --for=pull -n alarmfw-prod
```

### 3. PVC'leri Oluştur

```bash
# StorageClass'ı ortamına göre ocp/pvc.yaml'da ayarla
oc apply -f ocp/pvc.yaml -n alarmfw-prod
```

### 4. Jenkins Pipeline'larını Çalıştır

Jenkins'te her repo için pipeline tanımla ve şu değişkenleri ekle:

| Değişken | Repo | Açıklama |
|---|---|---|
| `REGISTRY_URL` | hepsi | Nexus registry adresi (ör: `nexus.internal:5000`) |
| `REGISTRY_CREDS` | hepsi | Jenkins credential ID (Docker kullanıcı/şifre) |
| `OCP_API_URL` | hepsi | OpenShift API endpoint (ör: `https://api.cluster.local:6443`) |
| `OCP_TOKEN_CREDS` | hepsi | Jenkins credential ID (OCP service account token) |
| `DEPLOY_NAMESPACE` | hepsi | Deploy namespace (ör: `alarmfw-prod`) |
| `OCP_APPS_DOMAIN` | yalnızca alarmfw-ui | OCP apps domain — `NEXT_PUBLIC_*` URL'leri türetilir |

Her Jenkinsfile 4 stage içerir: **Checkout SCM → Docker Build → Nexus Push → OCP Deploy**

Pipeline sırası önemli değil, paralel çalıştırılabilir:
- `alarmfw` → `alarmfw/Jenkinsfile`
- `alarmfw-api` → `alarmfw-api/Jenkinsfile`
- `alarmfw-observe` → `alarmfw-observe/Jenkinsfile`
- `alarmfw-ui` → `alarmfw-ui/Jenkinsfile`

> **Not:** `ocp/pvc.yaml` pipeline'a dahil değildir. İlk kurulumda bir kez elle uygulanır:
> ```bash
> oc apply -f ocp/pvc.yaml -n alarmfw-prod
> ```

### 5. UI Route'unu Kontrol Et

```bash
oc get route alarmfw-ui -n alarmfw-prod
# https://alarmfw-ui.apps.CLUSTER.DOMAIN
```

### 6. İlk Kurulum (Bootstrap)

UI'ya gir → **Manage → Admin Console** ve aynı bootstrap scriptini çalıştır:

```bash
bash /app/scripts/bootstrap.sh \
  --cluster CLUSTER_ADI \
  --ocp-api https://api.CLUSTER.DOMAIN:6443 \
  --ocp-token TOKEN \
  --prometheus-url https://thanos-querier.apps.CLUSTER.DOMAIN \
  --prometheus-token PROMETHEUS_TOKEN \
  --smtp-host mailrelay.internal --smtp-port 25 --smtp-to ops@sirket.com \
  --zabbix-url https://zabbix.internal --zabbix-token ZABBIX_TOKEN
```

> Bootstrap scripti container içinde çalışır, tokenları PVC'ye yazar.

---

## Dizin Yapısı

```
alarmfw/
├── config/
│   ├── notifiers/          # SMTP, Zabbix, outbox notifier config
│   ├── policies/           # Dedup politikaları
│   ├── checks/             # Manuel check YAML şablonları
│   ├── generated/          # Otomatik üretilen check YAML'ları (gitignore)
│   ├── observe.yaml        # Cluster Prometheus/Loki URL'leri (gitignore)
│   └── run_server.yaml     # Ana run config
├── ocp/
│   ├── pvc.yaml            # PersistentVolumeClaim tanımları
│   └── deployment.yaml     # OCP Deployment
├── src/alarmfw/            # Python kaynak kodu
├── Jenkinsfile
├── docker-compose.yml
└── .env.example
```

## Güvenlik

- `.env` dosyası git'e gitmez
- `config/observe.yaml` (gerçek URL'ler) git'e gitmez
- `state/` dizini (alarm geçmişi) git'e gitmez
- `setup.sh` ve `scripts/bootstrap.sh` git'e gitmez
- Token dosyaları `SECRETS_DIR` dizininde tutulur, volume ile mount edilir
