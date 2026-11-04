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

The Pi and X32 must be on the same network. Bridge networking works (outgoing UDP). Port 8080 does not conflict with Dante.

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
