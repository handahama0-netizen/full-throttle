# Full Throttle Screener

Screener för tre strategier – **Full Throttle**, **Full Throttle+** (lägre risk) och **Low Risk, High Reward** (storbolag, för swingtrading) – kombinerade med **200-veckorszoner** enligt indikatorn Universal Value Zones.

Nyckeltal och veckokurser hämtas från Yahoo Finance varje vardag natt av GitHub Actions. Sidan ligger på GitHub Pages. Allt är gratis och kräver ingen API-nyckel.

Adress: https://handahama0-netizen.github.io/full-throttle/

## Flikar

- **Signaler** – radar (kvalitet mot avstånd till 200W), Swing-läge, Tillväxt på rea, Bevakning, Nära Low Risk High Reward och en händelselogg.
- **Screener** – välj strategi, filtrera på status och 200W-zon, ändra alla gränser. Klicka på en rad för 200W-diagram, köpnivåer och alla krav.
- **Modellportfölj** – tillväxtportfölj (max två per grupp) och swing-kandidater med köpnivåer.
- **Strategier & metod** – kriterierna sida vid sida.

## Mejllarm

När ett bolag får en ny signal (Swing-läge eller Tillväxt på rea) skapar uppdateringen en *issue* i repot. GitHub mejlar dig om nya issues i dina egna repon. Syns inget mejl: kontrollera github.com/settings/notifications att e-post är påslaget för *Watching*.

## Inställningar (en gång)

1. Settings → Actions → General → *Workflow permissions* → **Read and write permissions** → Save.
2. Settings → Pages → *Deploy from a branch* → **main**, **/ (root)** → Save.
3. Actions → **Uppdatera nyckeltal** → **Run workflow**. Tar 4–6 minuter första gången.

## Ändra

- **Bolag:** `full-throttle-site/tickers.json`. Varje bolag behöver `ticker` (Yahoo-symbol, svenska aktier slutar på `.ST`), `name`, `sector` och `group`.
- **Tid för uppdatering:** `cron` i `.github/workflows/update.yml` (UTC).
- **Standardgränser:** objektet `DEF` i `full-throttle-site/index.html`. Gränser går också att ändra på sidan; de sparas i din webbläsare.

## Bra att veta

- Siffrorna kan skilja något mot andra sajter, särskilt PEG och ROIC som räknas olika hos olika leverantörer.
- Misslyckas ett bolag visas förra dagens siffror med en markering. Misslyckas mer än hälften sparas ingenting.
- GitHub pausar schemalagda körningar efter 60 dagar utan aktivitet i repot. Dagliga datauppdateringar räknas som aktivitet, men får du ett mejl om det: klicka **Enable workflow** under Actions.
- Screeningverktyg, inte investeringsrådgivning.
