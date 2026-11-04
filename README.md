# X32 Router

Web app (PWA) for patching an X32 by stage box jack. Runs in Docker on a Raspberry Pi beside Dante. It talks to the X32 over OSC (UDP 10023) only. It does not touch Dante.

## How it works

1. **Devices tab**: add stage boxes (S16, S32, Card, X32 Local, Custom). Set which port range each uses, e.g. S32 inputs = AES50-A 17-48.
2. **Blocks tab**: set fader blocks in groups of 8, e.g. Ch 1-8 = User In 9-16. Use **User In** blocks for channels and **User Out** blocks for AES50/Card outputs.
3. **Route tab → Inputs**: click a fader, then a stage box jack (or jack first). Example: Ch 1 → S32 In 2. Ch 1 uses User In 9, S32 In 2 is AES50-A 18, so the app sets User In 9 = AES50-A 18.
4. **Route tab → Outputs**: click a stage box output, then a source (Output 1-16, P16, Aux, Monitor, or any input). The app sets the User Out slot that feeds that jack.

Auto-advance moves to the next fader / output after each patch.

Needs X32 firmware 4.0 or newer (User In / User Out routing).

## Run on the Pi (CasaOS)

1. Push this folder to `github.com/daviied/x32-router`. The GitHub Action builds `ghcr.io/daviied/x32-router:latest` (arm64, armv7, amd64).
2. Make the package public (GitHub → Packages → x32-router → Settings), or log in to GHCR on the Pi.
3. In CasaOS: **Custom Install → Import**, paste `docker-compose.yml`, set `APP_PASSWORD`, `SESSION_SECRET`, `X32_IP`.
4. Open `http://<pi>:8080`. Put nginx in front with HTTPS for public access (send `X-Forwarded-Proto` so cookies get `Secure`).

The Pi and X32 must be on the same network. Bridge networking works (outgoing UDP). Port 8080 does not conflict with Dante.

## Local test without a mixer

```bash
cd app && APP_PASSWORD=test X32_MOCK=1 DATA_DIR=../data python server.py
```

## Notes

- Config is saved in `/data/config.json`.
- All X32 OSC value tables are in `app/x32map.py`. The **Aux In block** and **output block** value lists are from community docs; check them against X32-Edit on first use. If a label is wrong, fix the table there.
- Aux 7-8 are USB L/R and cannot be routed.
