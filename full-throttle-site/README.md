# Full Throttle Screener

En hemsida som screenar tillväxtbolag enligt Full Throttle-strategin (Hello Stocks) med ett förbättrat riskfilter. Nyckeltalen hämtas automatiskt från Yahoo Finance varje vardag natt, så sidan är alltid uppdaterad när du vaknar.

Allt körs gratis på GitHub: sidan ligger på GitHub Pages och uppdateringen sköts av GitHub Actions. Ingen server, ingen API-nyckel.

## Så kommer du igång (ca 10 minuter)

### 1. Skapa ett GitHub-konto
Gå till https://github.com/signup om du inte redan har ett.

### 2. Skapa ett nytt repo
1. Klicka på **+** uppe till höger → **New repository**.
2. Namn: till exempel `full-throttle`.
3. Välj **Public** (krävs för gratis GitHub Pages).
4. Klicka **Create repository**.

### 3. Ladda upp filerna
1. Packa upp zip-filen på din dator.
2. I det nya repot, klicka på länken **uploading an existing file**.
3. Dra in **allt innehåll** i mappen (inte själva mappen): `index.html`, `data.json`, `tickers.json`, `requirements.txt`, `README.md` och mapparna `scripts` och `.github`.
   - Mappen `.github` är dold på Mac. Tryck **Cmd + Shift + .** i Finder för att visa den. På Windows: Visa → Dolda objekt.
4. Klicka **Commit changes**.

Kontrollera att filen `.github/workflows/update.yml` finns i repot. Annars kör inte uppdateringen. Saknas den: **Add file** → **Create new file**, skriv `.github/workflows/update.yml` som filnamn, klistra in innehållet från filen i zip-mappen och klicka **Commit changes**.

### 4. Ge uppdateringen rätt att spara
**Settings** → **Actions** → **General** → under *Workflow permissions* välj **Read and write permissions** → **Save**.

### 5. Slå på hemsidan
**Settings** → **Pages** → *Source*: **Deploy from a branch** → Branch: **main**, mapp **/ (root)** → **Save**.

Efter någon minut står adressen överst på samma sida, till exempel `https://dittnamn.github.io/full-throttle/`.

### 6. Kör första uppdateringen
**Actions** → **Uppdatera nyckeltal** → **Run workflow**. Efter 2–3 minuter har sidan färsk data. Sedan sker det automatiskt varje vardag.

## Vanliga ändringar

**Lägga till eller ta bort bolag:** redigera `tickers.json` direkt på GitHub (pennan uppe till höger). Varje bolag behöver `ticker`, `name`, `sector` och `group`. Gruppen styr regeln om max två bolag per grupp i modellportföljen. Nya bolag dyker upp efter nästa körning, eller direkt om du kör workflowet manuellt.

**Ändra tid för uppdateringen:** raden `cron` i `.github/workflows/update.yml`. Tiden anges i UTC.

**Ändra kriteriernas standardvärden:** objektet `DEF` i `index.html`. Gränserna går också att ändra direkt på sidan, och sparas då i din webbläsare.

## Bra att veta

- Nyckeltalen kommer från Yahoo Finance och kan skilja sig något från andra källor, särskilt PEG och ROIC som räknas olika hos olika leverantörer.
- Om Yahoo inte svarar för ett bolag visas förra dagens siffror med en markering. Om mer än hälften misslyckas sparas ingenting och förra datan ligger kvar.
- GitHub pausar schemalagda körningar i repon som inte haft någon aktivitet på 60 dagar. Du får ett mejl innan, och det räcker att klicka på **Enable workflow** under Actions.
- Detta är ett screeningverktyg, inte investeringsrådgivning.

## Filer

| Fil | Vad den gör |
|---|---|
| `index.html` | Själva hemsidan. Läser `data.json`. |
| `data.json` | Senaste nyckeltalen. Skrivs över av uppdateringen. |
| `tickers.json` | Listan med bolag som bevakas. |
| `scripts/update.py` | Hämtar nyckeltalen och skriver `data.json`. |
| `.github/workflows/update.yml` | Schemat som kör skriptet varje vardag. |
