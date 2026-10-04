# Full Throttle

Aktiesida med sök, TradingView-diagram och din 200-veckorsstrategi, plus Full Throttle, Full Throttle+ och Low Risk, High Reward, analytiker och nyckeltal.

Adress: https://handahama0-netizen.github.io/full-throttle/

## Så fungerar det

GitHub Actions hämtar data från Yahoo Finance och publicerar sidan till grenen `gh-pages`:

| När | Vad |
|---|---|
| Varje vardag 00:15 svensk tid | Allt: ~650 aktier, nyckeltal, analytiker, 10 års kurser, 200W |
| Var 30:e minut 09–23 svensk tid | Senaste kurserna (ca 15–30 min fördröjning) |
| När du ändrar något i repot | Sidan byggs om |

Allt är gratis och kräver ingen API-nyckel.

## Inställningar (en gång)

1. **Settings → Actions → General → Workflow permissions:** välj *Read and write permissions* → Save.
2. **Actions → Bygg och uppdatera → Run workflow** (läge `full`). Första körningen tar 30–60 minuter.
3. **Settings → Pages:** *Deploy from a branch* → branch **gh-pages**, mapp **/ (root)** → Save.

## Lägga till aktier

Skriv Yahoo-symbolen på en egen rad i `config/egna.txt` (svenska aktier slutar på `.ST`, t.ex. `EVO.ST`). S&P 500 och Nasdaq-100 hämtas automatiskt, Stockholmsbörsen finns i `config/stockholm.txt`. Nya aktier kommer med vid nästa nattkörning, eller direkt om du kör workflowet manuellt med läge `full`.

## Filer

| Fil | Vad |
|---|---|
| `site/index.html` | Själva sidan |
| `site/lightweight-charts.js` | Diagrambiblioteket (TradingView Lightweight Charts, Apache 2.0) |
| `scripts/build.py` | Hämtar data och bygger sidan |
| `config/*.txt` | Aktielistor |
| `.github/workflows/site.yml` | Schemat |

## Bra att veta

- Mejllarm: nya signaler (Swing-läge, Tillväxt på rea) skapas som en issue i repot och GitHub mejlar dig.
- Misslyckas en aktie behålls förra dagens data för den.
- Siffror kan skilja något mot andra sajter, särskilt PEG och ROIC.
- Analysverktyg, inte investeringsrådgivning.
