# Deploy AWS VPS

Hướng dẫn này dùng cho mô hình một VPS chạy cả backend FastAPI và frontend Next.js bằng Docker Compose.

## 1. Kiến trúc

```text
Domain / Elastic IP
  -> Nginx trên EC2
      -> /api, /docs, /ready, /health -> backend:8080
      -> /                            -> frontend:3000
```

Database, Auth và Storage vẫn dùng Supabase.

## 2. Chuẩn bị AWS

Tạo một EC2 Ubuntu 22.04 hoặc 24.04.

Security Group nên mở:

- `22`: SSH, tốt nhất chỉ cho IP của bạn.
- `80`: HTTP.
- `443`: HTTPS.

Nên gắn Elastic IP nếu dùng domain.

## 3. Cài phần mềm trên VPS

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx git
sudo systemctl enable --now docker nginx
sudo usermod -aG docker $USER
```

Đăng xuất SSH rồi đăng nhập lại để quyền Docker có hiệu lực.

## 4. Tạo thư mục deploy lần đầu

```bash
sudo mkdir -p /opt/p092
sudo chown -R $USER:$USER /opt/p092
cd /opt/p092
mkdir -p frontend
```

CI/CD mặc định upload source vào `/opt/p092`. Có thể đổi bằng GitHub secret `VPS_DEPLOY_PATH`.
VPS không cần quyền `git clone` repo private; GitHub Actions sẽ checkout code rồi copy gói source qua SSH.

## 5. Tạo env production

Backend/root env:

```bash
cp .env.production.example .env.production
nano .env.production
```

Frontend env:

```bash
cp frontend/.env.production.example frontend/.env.production
nano frontend/.env.production
```

Các biến quan trọng:

```env
APP_ENV=production
ALLOW_LIVE_MIGRATION=false
DEV_OTP_MOCK_ENABLED=false
ENABLE_DEV_PASSWORD_LOGIN=false
CORS_ORIGINS=https://your-domain.com

NEXT_PUBLIC_API_URL=https://your-domain.com
NEXT_PUBLIC_API_BASE_URL=https://your-domain.com/api/v1
NEXT_PUBLIC_DEV_PASSWORD_LOGIN=false
NEXT_PUBLIC_DEV_AUTH_ENABLED=false
```

Không commit `.env.production` hoặc `frontend/.env.production`.

## 6. Chạy thủ công lần đầu

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm backend alembic upgrade head
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Kiểm tra:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:3000
```

## 7. Cấu hình Nginx

Copy file mẫu:

```bash
sudo cp deploy/nginx/p092.conf.example /etc/nginx/sites-available/p092
sudo nano /etc/nginx/sites-available/p092
```

Đổi `your-domain.com` thành domain thật.

Enable site:

```bash
sudo ln -s /etc/nginx/sites-available/p092 /etc/nginx/sites-enabled/p092
sudo nginx -t
sudo systemctl reload nginx
```

Tạo HTTPS:

```bash
sudo certbot --nginx -d your-domain.com
```

## 8. GitHub Actions CI/CD

Workflow: `.github/workflows/deploy-vps.yml`

Workflow sẽ chạy khi:

- Push lên `hungphuc`.
- Push lên `main`.
- Bấm chạy thủ công bằng `workflow_dispatch`.

GitHub repo cần các secrets:

| Secret | Ý nghĩa |
|---|---|
| `VPS_HOST` | IP hoặc domain VPS |
| `VPS_USER` | user SSH, ví dụ `ubuntu` |
| `VPS_SSH_KEY` | private key SSH |
| `VPS_PORT` | port SSH, thường là `22` |
| `VPS_DEPLOY_PATH` | đường dẫn repo trên VPS, ví dụ `/opt/p092` |

Deploy script sẽ:

1. SSH vào VPS.
2. Upload gói source từ GitHub Actions vào VPS.
3. Giữ lại `.env.production` và `frontend/.env.production` đang có trên VPS.
4. Thay source mới vào `VPS_DEPLOY_PATH`.
5. Build lại backend/frontend image.
6. Chạy `alembic upgrade head` bằng container backend tạm.
7. Restart containers.

Không sửa code trực tiếp trong `/opt/p092`; lần deploy tiếp theo sẽ thay toàn bộ source, chỉ giữ lại env production.

## 9. Lệnh vận hành

Xem log:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f frontend
```

Restart:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml restart
```

Rollback nhanh về bản source trước đó:

```bash
cd /opt/p092
cd ..
rm -rf p092.failed
mv p092 p092.failed
mv p092.previous p092
cd p092
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

## 10. Lưu ý bảo mật

- Rotate các Supabase/OpenAI key nếu key thật từng xuất hiện trong repo.
- Không bật `DEV_OTP_MOCK_ENABLED` hoặc `ENABLE_DEV_PASSWORD_LOGIN` ở production.
- Không mở port `3000` và `8080` ra public. Compose đã bind hai port này vào `127.0.0.1`; public chỉ đi qua Nginx `80/443`.
- Sao lưu Supabase DB trước migration production.
