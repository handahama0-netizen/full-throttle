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

Allt är gratis och kräver ingen API-nyckel. Källor: Yahoo Finance (kurser, nyckeltal, prognoser), SEC EDGAR (officiella årssiffror för amerikanska bolag), Yahoo Finance och Google News via RSS (nyheter).

## Inställningar (en gång)

1. **Settings → Actions → General → Workflow permissions:** välj *Read and write permissions* → Save.
2. **Actions → Bygg och uppdatera → Run workflow** (läge `full`). Första körningen tar 30–60 minuter.
3. **Settings → Pages:** *Deploy from a branch* → branch **gh-pages**, mapp **/ (root)** → Save.

## Mina innehav

Fliken **Mina innehav** har två listor: **Swingar** och **Portfölj**. Lägg till en aktie med köpkurs och antingen antal eller belopp i kronor, plus valfri stop och mål. Sidan visar värde, resultat i kronor och procent (inklusive valutaeffekt), dagens förändring, 200W-zon, signal och varningar (stop passerad, mål nått, Very Expensive, rapport inom en vecka). Översikten visar summan och dina bästa och sämsta innehav.

Innehaven sparas i webbläsaren. Använd **Flytta till annan enhet** för att föra över dem till mobilen.

## Info-knappar

Alla nyckeltal, kolumner och analyser har en liten **i**-knapp som förklarar vad måttet betyder och hur det ska läsas.

## Analys per aktie

Fliken **Analys** på varje aktie visar:

- **Varför aktien rört sig** (1 vecka, 1 månad, 3 månader), uppdelat i marknad, sektor och bolagsspecifikt, och om rörelsen stöds av ändrade vinstprognoser. Bedömningen blir *Möjlig överreaktion*, *Fundamentalt motiverat*, *Makro/sektor* m.fl.
- **Intrinsic value**: DCF (bear/bas/bull) med WACC, mid-year-konvention, andel terminalvärde och känslighetstabell (WACC × evig tillväxt), omvänd DCF, Graham, analytikernas riktkurser och 200W-köpzon i samma diagram.
- **Officiella siffror** ur årsredovisningen (SEC 10-K) för amerikanska bolag.
- **Prognoser och rapporter**, **nyheter** och **källor**.

Fliken **Bransch** jämför bolaget med branschen: 25:e percentil, median och 75:e percentil per nyckeltal, plats i branschen, värde vid branschens P/E och branschanpassade nyckeltal (t.ex. P/B istället för bruttomarginal för banker, Rule of 40 för teknik).

### Analysbrev från Claude (valfritt)

Ett analysbrev i stil med en bankanalys, skrivet av Claude Sonnet 5.5 med webbsök efter färska nyheter. Kräver en API-nyckel från Anthropic och kostar ungefär 1 kr per brev.

1. Skapa en nyckel på https://console.anthropic.com (API Keys) och lägg in lite krediter.
2. Repot: **Settings → Secrets and variables → Actions → New repository secret**. Namn: `ANTHROPIC_API_KEY`, värde: nyckeln.
3. Skriv aktierna du vill ha brev om i `config/ai.txt`. Aktier med Swing- eller Rea-signal får brev automatiskt. Högst 12 brev per natt, och ett brev skrivs bara om när det är en vecka gammalt eller kursen rört sig mer än 4 % på en dag.
4. Vill du ha ett brev direkt: **Actions → Bygg och uppdatera → Run workflow**, välj läge `ai` och skriv t.ex. `SOFI` i rutan.

Utan nyckel fungerar allt annat som vanligt.

## Lägga till aktier

Skriv Yahoo-symbolen på en egen rad i `config/egna.txt` (svenska aktier slutar på `.ST`, t.ex. `EVO.ST`). S&P 500 och Nasdaq-100 hämtas automatiskt, Stockholmsbörsen finns i `config/stockholm.txt`. Nya aktier kommer med vid nästa nattkörning, eller direkt om du kör workflowet manuellt med läge `full`.

## Filer

| Fil | Vad |
|---|---|
| `site/index.html` | Själva sidan |
| `site/lightweight-charts.js` | Diagrambiblioteket (TradingView Lightweight Charts, Apache 2.0) |
| `scripts/build.py` | Hämtar data och bygger sidan |
| `scripts/analysis.py` | Kursattribution och värdering |
| `scripts/sec.py` | Årssiffror från SEC EDGAR |
| `scripts/ai_notes.py` | Analysbrev från Claude (valfritt) |
| `config/ai.txt` | Aktier som får analysbrev |
| `config/*.txt` | Aktielistor |
| `.github/workflows/site.yml` | Schemat |

## Bra att veta

- Mejllarm: nya signaler (Swing-läge, Tillväxt på rea) skapas som en issue i repot och GitHub mejlar dig.
- Misslyckas en aktie behålls förra dagens data för den.
- Siffror kan skilja något mot andra sajter, särskilt PEG och ROIC.
- Analysverktyg, inte investeringsrådgivning.
