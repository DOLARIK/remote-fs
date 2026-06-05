# remote-fss

## Project Overview
Self-hosted media infrastructure stack running on a Mac M1 Pro (16GB RAM, 8-core).
Serves ~7,700 wedding photos/videos (~42GB) from an external SSD to family over the internet.
All services run via a single `docker-compose.yml`. Do not split into multiple compose files.

## ⚠️ Required Reading
Before working on anything in this repo, read these files in order:
1. This file (CLAUDE.md) — infrastructure and stack context
2. `.claude/ai-pipeline.md` — AI photo intelligence pipeline (active development)

The AI pipeline is the primary active workstream. Most new code lives in `pipeline/`.

## Hardware
- **Machine:** MacBook M1 Pro, 16GB unified memory, macOS
- **External SSD:** mounted at `/Volumes/DP SSD`
- **SSD label:** "DP SSD" (exFAT formatted — no Linux file permissions)
- **SSD content:** `/Volumes/DP SSD/Palak & Divyanshu/` (~7,022 photos, 748 videos)

## Domain & Tunnel
- **Domain:** `divyanshu-palak-wedding.space` (Namecheap → transferred to Cloudflare DNS)
- **Tunnel:** Single Cloudflare `cloudflared` container handles all subdomains
- **Nextcloud:** `divyanshu-palak-wedding.space` (root)
- **Immich:** `immich.divyanshu-palak-wedding.space`
- Cloudflare tunnel config lives in `~/.cloudflared/` on host, mounted into container

## Stack Services (single docker-compose.yml)

### Nextcloud
- Container name: `nextcloud_app`
- Image: `nextcloud:latest`
- DB: MariaDB (`db` service, container `nextcloud_db`)
- Cache: Redis (`redis` service, container `nextcloud_redis`)
- Cron: `nextcloud-cron` container (runs `/cron.sh` for background jobs + file scanning)
- SSD mounted at `/mnt/ssd` inside container (read-only)
- External storage mounted at `/dp-photos` via `occ files_external`
- Known issue: exFAT SSD requires `usermod -aG staff www-data` in entrypoint for permissions
- Brute force protection enabled by default — reset with `occ security:bruteforce:reset --all`

### Immich
- Container name: `immich_server`
- Image: `ghcr.io/immich-app/immich-server`
- DB: PostgreSQL with pgvecto-rs (`immich_postgres`)
- Cache: Valkey Redis (`immich_redis`)  
- ML: `immich_machine_learning` container (face recognition, CLIP smart search)
- External library path: `/mnt/ssd/Palak & Divyanshu` → SSD photos indexed but not copied
- ML concurrency tuned for 8-core/16GB: set in env vars
- Admin account created manually on first boot (cannot be automated)

### Cloudflared
- Container name: `cloudflared`
- Single tunnel serves both Nextcloud and Immich via ingress rules
- Token stored in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`

## Network
- All services share single Docker network: `app_net`
- No ports exposed externally except via cloudflared tunnel
- Inter-service communication uses service names as hostnames

## Key Files
```
remote-fss/
├── docker-compose.yml       ← single source of truth for all services
├── .env                     ← secrets (gitignored)
├── CLAUDE.md                ← this file
├── CLAUDE.local.md          ← personal prefs (gitignored)
└── .claude/
    └── ai-pipeline.md       ← AI photo intelligence pipeline spec
```

## Environment Variables (.env)
```
MYSQL_PASSWORD=
MYSQL_ROOT_PASSWORD=
IMMICH_DB_PASSWORD=
CLOUDFLARE_TUNNEL_TOKEN=
DB_PASSWORD=
```
Never hardcode secrets. Always reference via `${VAR_NAME}` in compose file.

## Common Commands
```bash
# Start everything
docker compose up -d

# Stop everything
docker compose down

# View logs for a service
docker compose logs -f [service_name]

# Restart a single service
docker compose restart [service_name]

# Force Nextcloud file rescan (if needed manually)
docker exec -u www-data nextcloud_app php occ files:scan --all

# Reset Nextcloud brute force protection
docker exec -u www-data nextcloud_app php occ security:bruteforce:reset --all

# Check Nextcloud external storage
docker exec nextcloud_app php occ files_external:list

# Immich logs
docker compose logs -f immich_server

# Check all running containers
docker compose ps
```

## Architecture Decisions & Why
- **Single compose file:** Owner's strong preference — easier to manage, single `up/down`
- **Cloudflared over port forwarding:** No home IP exposure, no router config needed
- **Immich external library:** Photos stay on SSD, not copied into Docker volumes (saves space)
- **exFAT SSD:** Cross-platform (Mac/Windows readable) but no Unix permissions — requires staff group workaround
- **No reverse proxy (nginx/traefik):** Cloudflare tunnel handles routing directly to services

## Known Gotchas
1. SSD must be mounted before `docker compose up` — Docker Desktop remounts on restart
2. After SSD remount or Docker restart, may need `occ files:scan --all`
3. `nextcloud-cron` needs same env vars and volumes as `nextcloud` service
4. Immich ML container is memory-hungry — don't run heavy batch jobs while stack is serving traffic
5. Cloudflare tunnel ingress rules are in the Cloudflare dashboard, not in compose file
6. `pgvecto-rs` PostgreSQL image is required for Immich — standard postgres image won't work

## Active Development
Primary workstream is the AI photo intelligence pipeline.
Full spec, data models, architecture, and file structure: **see `.claude/ai-pipeline.md`**
