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

## Mina aktier

Fliken **Mina aktier** är din bevakningslista med en rad per aktie:

| Kolumn | Vad |
|---|---|
| Kurs, Idag | Senaste kurs och dagens förändring |
| 52 veckor | Var kursen ligger mellan årets lägsta och högsta, och hur långt från toppen |
| Värdering | **Undervärderad / Rimligt värderad / Övervärderad**: DCF, riktkurs, P/E framåt mot branschen, PEG, 200W och Value-faktorn vägda ihop |
| P/E, P/E fr., PEG | Med branschmedianen under. Grönt = minst 10 % billigare än branschen, rött = minst 10 % dyrare |
| Sentiment | **Bullish / Neutral / Bearish**: analytikerbetyg, prognosändringar, momentum, trend och blankning |
| Bransch | Plats i branschen (sammanvägd rank) |
| Quant | Faktorpoäng 0–100 och profil i Value, Growth, Quality, Momentum, Revisions och Low risk |

Öppna en rad (▶) för att se exakt vad som drar värdering och sentiment åt vilket håll, faktorprofilen och nyckeltalen mot branschmedianen.

Lägg till aktier med sökrutan (flera går med komma: `NVDA, AAPL, VOLV-B`) eller ☆ på en aktiesida. De sparas i webbläsaren. Aktier i `config/mina.txt` syns på alla enheter och får data vid nästa nattkörning, även om de inte ingår i S&P 500, Nasdaq-100 eller Stockholmslistan.

## Guld och backtest

**Guld** är den kombinerade strategin för swingar med hävstång: 200W-köpzonen (under Cheap) + ett stort, stabilt och dominerande kvalitetsbolag (börsvärde ≥ 200 md $ eller branschledare ≥ 50 md $, beta ≤ 1,3, ROIC/ROE ≥ 15 %, FCF-marginal ≥ 10 %, växande, skulden under kontroll). Originalstrategierna finns kvar bredvid.

`scripts/backtest.py` testar 200W-reglerna på all kurshistorik varje natt (originalet, sälj vid Fair Value, stop −20 %, djupare köp) för Guld, LRHR, megabolag och alla aktier, även med 2x hävstång. Resultatet visas under **Strategi & backtest**. Slutsatsen hittills: sälj redan vid Fair Value och ha en stop på −20 %. Det ger högre takt och ungefär halverar de värsta fallen. Urvalet bygger på dagens nyckeltal (facit i hand), vilket står på sidan.

Varje aktie har kortet **Din strategi** med vilka strategier den klarar och en handelsplan (köpnivå, sälj enligt förbättrad regel och original, stop), och **Vem dominerar branschen?** med rank efter omsättning och varför ledaren är störst. Översikten har **Din strategi just nu**: innehav att agera på, köplägen och aktier som snart når köpzonen.

## Startsidan: Dagens läge

Överst berättar sidan vad som hänt: marknaden idag (S&P 500, Nasdaq-100, hur många aktier som steg, starkaste och svagaste tema), Claudes marknadskommentar, hur dina innehav gått, och **Att hålla koll på**: stop som passerats, mål som nåtts, nya signaler, rapporter inom en vecka och stora rörelser i dina aktier. Claudes fokus visar bara rubrikerna, tryck för motiveringen. Teman och övriga listor (radar, bra bolag till fel pris, händelser) ligger i fällbara sektioner.

## Aktiesidan som berättelse

Varje aktie börjar med **Kort sagt**: en mening om vad det är för bolag och hur det är prissatt, fyra kapitel (Affären, Priset, Stämningen, Riskerna) och listor över vad som **talar för** och **talar emot**. Sedan fyra kort:

- **Vad är aktien värd?** Inneboende värde (DCF, annars analytikernas riktkurs) mot kursen, med under- eller övervärdering i procent.
- **Vinst och kurs:** EPS per kvartal som staplar med kursen ovanpå.
- **Vad gör insiders?** Köp och försäljningar senaste 0–3, 3–6 och 6–12 månaderna, med senaste affären. Svenska bolag länkar till Finansinspektionens insynsregister.
- **Hur bra är bolaget?** Quant-poäng, plats i branschen och faktorprofil.

Under dem **Så har bolaget utvecklats**: omsättning, rörelseresultat, nettoresultat och vinst per aktie per år, med analytikernas prognos som randiga staplar och tillväxt över 3 och 5 år. Sist kursgrafen och **Fördjupning** med alla detaljer. Länkarna i korten (t.ex. "Värdering i detalj →") hoppar rätt in i fördjupningen.

## Nyckeltal och EPS på aktiesidan

Fliken **Nyckeltal** visar varje nyckeltal bredvid **branschmedianen**, var bolaget hamnar i branschen och ett omdöme: **Bra** (bästa tredjedelen), **Okej** eller **Svag** (sämsta tredjedelen), vägt mot tumregler så att ett nyckeltal inte blir bra bara för att hela branschen är svag.

Grafen har en **EPS**-ruta: vinst per aktie, rullande 12 månader, som en linje på vänster skala. Prickarna visar varje rapport, grön om bolaget slog förväntningarna och röd om det missade, med överraskningen i procent. Under grafen står hur vinst och kurs har rört sig senaste året, om P/E har gått upp eller ner, och hur tätt kursen har följt vinsten. Kvartalshistoriken (upp till tio år) hämtas i nattkörningen. Tills den finns visas årlig EPS.

## Quant-profil och risk

Varje aktiesida har en **Quant-profil** med faktorpercentiler, värdering och sentiment. Under **Portfölj** och **Live swing** visar **Risk och exponering** vägd beta, volatilitet, effektivt antal aktier, största position, fördelning per tema och valuta och portföljens faktorlutning, med varningar vid hög koncentration. Screenern har en kolumn **Quant** att sortera på.

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
| `config/mina.txt` | Mina aktier (syns på alla enheter) |
| `config/*.txt` | Aktielistor |
| `.github/workflows/site.yml` | Schemat |

## Bra att veta

- Mejllarm: nya signaler (Swing-läge, Tillväxt på rea) skapas som en issue i repot och GitHub mejlar dig.
- Misslyckas en aktie behålls förra dagens data för den.
- Siffror kan skilja något mot andra sajter, särskilt PEG och ROIC.
- Analysverktyg, inte investeringsrådgivning.
