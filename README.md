# X32 Router

Web app (PWA) for patching an X32 by stage box jack. Runs in Docker on a Raspberry Pi beside Dante. It talks to the X32 over OSC (UDP 10023) only. It does not touch Dante.

## How it works

1. **Devices tab**: add devices from presets: S16, S32, Card, X32 Local (Local In 1-32 + XLR Out 1-16), X32 Aux I/O (Aux In/Out 1-6), Outboard insert, Internal FX insert, or Custom. Set which port range each uses, e.g. S32 inputs = AES50-A 17-48. Open **Port labels** to name each jack (e.g. "Kick In", "Wedge 1").
2. **Blocks tab**: set fader blocks in groups of 8, e.g. Ch 1-8 = User In 9-16. Use **User In** blocks for channels and **User Out** blocks for AES50/Card outputs.
3. **Route tab → Inputs**: click a fader, then a stage box jack (or jack first). Example: Ch 1 → S32 In 2. Ch 1 uses User In 9, S32 In 2 is AES50-A 18, so the app sets User In 9 = AES50-A 18. **Unassign input** sets the fader's User In to Off.
4. **Route tab → Outputs**: click an output, then a source.
   - AES50/Card outputs: Output 1-16, P16, Aux, Monitor, or any input (through User Out).
   - Local XLR Out 1-16 and Aux Out 1-6: Main, Bus, Matrix, direct outs, Monitor (sets `/outputs/main|aux/NN/src`).
   - **Unassign output** sets it to Off.
5. **Route tab → Inserts**: click a fader, then an insert slot. Outboard insert slots use Aux Out N (send) + Aux In N (return). Internal FX slots use FX1L … FX4R. **Remove insert** turns it off.
   - **FX insert** button (Inputs and Inserts modes): puts the next free insert slot on the selected fader.
   - **Outboard wiring** (Devices tab, outboard insert devices): set the stage box jacks the unit is plugged into. On insert, the app routes Aux Out N to the send jack (through User Out) and the return jack to Aux In N (through User In N, which needs the Aux In block set to User In 1-x). The app does not take a User In that a channel already uses; it warns you.
   - **Outboard units with auto Aux** (preset "Outboard unit"): add as many units as you have (e.g. 10). For each, set its send jack (AES50/Card output) and return jack (stage box input). Insert a unit on a fader and the app picks a free Aux from the **Aux insert pool** (Devices tab, Aux 1-6 by default), routes Aux Out N → send jack (User Out), return jack → User In N → Aux In N, and sets the insert. Remove it and the Aux is free again (the unit's send jack is switched off). Up to 6 at a time (one per Aux). The Inserts view shows which unit is on which Aux.
   - Pool needs: the Aux In block set to User In 1-6 (the app sets it), and User In N free for each pool Aux N. If a channel block uses User In 1-8, those Auxes show as blocked.
   - Internal FX slots show the effect loaded in FX1-4 and warn if it is a send-type effect (reverb, delay, mod).
6. **Route tab → Patch bay**: drag cables between jacks like an analog patch bay. Rows: stage box inputs, channel inputs, channel insert points, insert devices, output sources (Main/Bus/Matrix for local outs, Output/P16/Aux/Mon for AES50/Card outs), and stage box outputs. Drag a stage box input onto an AES50/Card output for a direct split. Drag a patched jack to empty space, or tap a cable, to unplug. Tap one jack then another to patch without dragging. Dragging near the screen edge scrolls.
7. **Plan tab**: printable routing plan (channel list, every jack with its label, outputs, inserts). Type a show name and click **Print** (or save as PDF).

Auto-advance moves to the next fader / output after each patch.

Needs X32 firmware 4.0 or newer (User In / User Out routing).

## Run on the Pi (CasaOS)

1. Push this folder to `github.com/daviied/x32-router`. The GitHub Action builds `ghcr.io/daviied/x32-router:latest` (arm64, armv7, amd64).
2. Make the package public (GitHub → Packages → x32-router → Settings), or log in to GHCR on the Pi.
3. In CasaOS: **Custom Install → Import**, paste `docker-compose.yml`, set `APP_PASSWORD`, `SESSION_SECRET`, `X32_IP`.
4. Open `http://<pi>:8080`. Put nginx in front with HTTPS for public access (send `X-Forwarded-Proto` so cookies get `Secure`).

The Pi and X32 must be on the same network. The compose file uses **host networking** so **Settings → Find console** can broadcast `/xinfo` on the LAN, the same way X32-Edit finds the console. The app listens on `PORT` (default 8080) on the Pi. Port 8080 does not conflict with Dante. If you use bridge networking, type the console IP instead; routing still works.

## Relay for mixing apps (optional, off by default)

**Settings → Relay for mixing apps.** The Pi answers on UDP 10023 like an X32. Point X32-Mix, Mixing Station, X32-Edit etc. at the Pi's IP (their console search also finds it, with the name you set). The console has only one client: the Pi.

- Reads: answered from the Pi's cache when the value is known; else sent to the console once, even if several apps ask.
- Writes: sent to the console and copied to the other apps at once.
- Desk changes and Router routing changes: copied to every app.
- Meters and other subscriptions: requested once from the console and copied to every app that asked.
- Scene / snippet / preset loads and copy commands clear the cache.

Limits:
- If the Pi or the container stops, the apps lose the console. Turn the relay off, or point the apps at the console IP, to go direct.
- Rare file-type operations (show files, library transfers, firmware) are passed through but not tested. Use X32-Edit direct to the console for those.
- Needs host networking (the compose file uses it) and free UDP port 10023 on the Pi (`RELAY_PORT` changes it, but most apps expect 10023).

## How it talks to the X32

- `/xinfo` to find the console and read its name, model and firmware.
- `/xremote` every 8 s, so the console pushes every change (from the desk, X32-Mix, X32-Edit) to the app.
- The server pushes each change to all open pages at once (Server-Sent Events on `/api/events`). With nginx, the app sends `X-Accel-Buffering: no`, so no extra nginx setup is needed.
- Each click returns the new state in its reply, so the page updates after one round trip.

## Local test without a mixer

```bash
cd app && APP_PASSWORD=test X32_MOCK=1 DATA_DIR=../data python server.py
```

## Notes

- Config is saved in `/data/config.json`.
- All X32 OSC value tables are in `app/x32map.py`. The **Aux In block** and **output block** value lists are from community docs; check them against X32-Edit on first use. If a label is wrong, fix the table there.
- Aux 7-8 are USB L/R and cannot be routed.
- Local XLR outs follow the main output patch only if Routing → XLR Out is at the default (Out 1-4 … Out 13-16).
- The FX type list (`/fx/N/type`) and which types count as insert types are from community docs.
- Outboard return routing assumes Aux In block "User In 1-x" feeds Aux N from User In N.
- The local out source list and insert values (`/outputs/main/NN/src`, `/insert/sel`) are also from community docs. Check them on first use.
