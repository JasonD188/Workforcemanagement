#!/bin/bash
# Setup script para sa fresh na Hetzner Ubuntu server
# Paano gamitin: chmod +x setup-server.sh && ./setup-server.sh

set -e

echo "=== 1. Ina-update ang system ==="
apt-get update && apt-get upgrade -y

echo "=== 2. Ini-install ang Docker ==="
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
rm get-docker.sh

echo "=== 3. Ini-install ang Docker Compose plugin ==="
apt-get install -y docker-compose-plugin

echo "=== 4. Ini-install ang Nginx at Certbot ==="
apt-get install -y nginx certbot python3-certbot-nginx

echo "=== 5. Ini-configure ang UFW firewall ==="
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "=== 6. Gumagawa ng swap file (importante para sa TensorFlow) ==="
if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap file created (4GB)"
else
    echo "Swap file already exists, skipping"
fi

echo "=== Tapos na ang setup! ==="
echo "Susunod na hakbang:"
echo "1. I-clone ang repo mo: git clone <your-repo-url>"
echo "2. Gumawa ng .env file base sa .env.example"
echo "3. I-copy ang nginx.conf sa /etc/nginx/sites-available/"
echo "4. I-run: docker compose up -d --build"
echo "5. I-run ang certbot para sa SSL: certbot --nginx -d yourdomain.com -d www.yourdomain.com"