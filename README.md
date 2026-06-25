# Gurobi MCP Multi-User Backend

A FastAPI backend that lets multiple users each run their own isolated `gurobi/mcp` container against their own Gurobi Intelligence Hub credentials. Users register, sign in for a JWT, and chat with three MCP tools (`gurobot`, `explainer`, `modeler`) over multi-turn conversations.

## What is implemented

- **Auth**: signup (bcrypt passwords, Fernet-encrypted Gurobi secrets), login (JWT), protected endpoints
- **Chat proxy**: per-user `gurobi/mcp` container started on demand, MCP session kept alive across turns, agent binding enforced per conversation
- **Isolation**: one container per user, ports 61100–61200 bound to loopback only, per-user file workspace
- **Idle reaper**: background task stops containers idle past `IDLE_TIMEOUT_MINUTES` (default 15 min)
- **Graceful recovery**: if a container is reaped mid-conversation, the next message transparently restarts it and retries (`recovered: true` in response)
- **Deployment**: systemd unit (`deploy/gurobimcp.service`) + Caddy reverse proxy (`deploy/Caddyfile`)

## Stack

Python 3.11 · FastAPI · SQLite (SQLAlchemy async) · Docker SDK · MCP Python SDK · bcrypt · Fernet · JWT · Caddy

## Setup (AMD64 — Oracle Linux 9)

```bash
# 1. Install uv and Python 3.11
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install cpython-3.11.15

# 2. Clone and create venv
git clone https://github.com/endorgobio/gurobiMCP.git
cd gurobiMCP
uv venv .venv --python cpython-3.11.15
source .venv/bin/activate
uv pip install -e .

# 3. Configure secrets
cp .env.example .env
# Edit .env: set JWT_SECRET_KEY, FERNET_KEY, and optionally DOMAIN

# 4. Add user to docker group (requires re-login)
sudo usermod -aG docker $USER

# 5. Install systemd service
sudo cp /etc/gurobimcp.env .env   # keep secrets at system path
sudo chmod 600 /etc/gurobimcp.env
sudo cp deploy/gurobimcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gurobimcp

# 6. Install Caddy and configure reverse proxy
curl -sL https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz -o /tmp/caddy.tar.gz
tar -xzf /tmp/caddy.tar.gz -C /tmp/ caddy
sudo mv /tmp/caddy /usr/local/bin/caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit with your domain or IP
sudo systemctl enable --now caddy

# 7. Open firewall
sudo firewall-cmd --permanent --add-port=80/tcp --add-port=443/tcp
sudo firewall-cmd --reload
```

### SELinux (Oracle Linux 9)

systemd cannot execute binaries from home directories by default. Apply `bin_t` context:

```bash
# uv-managed Python
sudo semanage fcontext -a -t bin_t '/home/opc/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu(/.*)?'
sudo restorecon -Rv /home/opc/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/

# Caddy (if installed to /usr/local/bin)
sudo semanage fcontext -a -t bin_t '/usr/local/bin/caddy'
sudo restorecon -v /usr/local/bin/caddy
```

## Adapting for ARM64 (Oracle A1.Flex — Always Free)

The `gurobi/mcp:latest` image supports both `amd64` and `arm64`. Only two steps differ:

**1. Caddy binary — use the arm64 build:**
```bash
curl -sL https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_arm64.tar.gz -o /tmp/caddy.tar.gz
```

**2. SELinux path — Python arch suffix changes:**
```bash
sudo semanage fcontext -a -t bin_t '/home/opc/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu(/.*)?'
sudo restorecon -Rv /home/opc/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu/
```

Everything else — `uv`, FastAPI, SQLite, systemd unit, Caddyfile, Docker SDK — is identical.

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/signup` | — | Register (username, password, access_id, gurobi_secret) |
| POST | `/login` | — | Get JWT token |
| GET | `/me` | JWT | Current user info |
| POST | `/chat` | JWT | Send message to gurobot / explainer / modeler |
| DELETE | `/conversations/{id}` | JWT | End conversation |
| GET | `/healthz` | — | Health check |

## Environment variables

| Variable | Description |
|----------|-------------|
| `JWT_SECRET_KEY` | Random 32+ byte string for JWT signing |
| `FERNET_KEY` | Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `IDLE_TIMEOUT_MINUTES` | Container idle timeout (default: 15) |
| `PORT_POOL_START` / `PORT_POOL_END` | Container port range (default: 61100–61200) |
| `DB_PATH` | SQLite path (default: data/app.db) |
| `DOMAIN` | Domain for Caddy HTTPS (leave blank to use IP + HTTP) |
