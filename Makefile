.PHONY: mac-server windows-server mac-downloader windows-downloader \
        stop-server stop-downloader logs-server logs-downloader

# ── Media stack (Nextcloud + Immich + Cloudflare) ──────────────────────────────

mac-server:
	docker compose -f docker-compose.yml --env-file .env up -d

windows-server:
	docker compose -f docker-compose.windows.yml --env-file .env up -d

stop-server:
	docker compose -f docker-compose.yml down

logs-server:
	docker compose -f docker-compose.yml logs -f

# ── Photo downloader ───────────────────────────────────────────────────────────

mac-downloader:
	docker compose -f downloader/docker-compose.yml --env-file downloader/.env up -d

windows-downloader:
	docker compose -f downloader/docker-compose.windows.yml --env-file downloader/.env up -d

stop-downloader:
	docker compose -f downloader/docker-compose.yml down

logs-downloader:
	docker compose -f downloader/docker-compose.yml logs -f downloader-api
