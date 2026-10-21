# Bixia – framtida digital teckningsresa (PoC)

Klickbar Proof of Concept för hur en kund kan teckna elavtal hos Bixia digitalt på cirka tre minuter. Fokus är design, kundupplevelse, flöde och konvertering, inte teknisk implementation.

> **Demonstration.** Inga riktiga avtal tecknas, ingen BankID-koppling finns och inga uppgifter skickas någonstans. Priser är tydligt märkta platshållare.

## Öppna

Öppna `index.html` i en webbläsare (dubbelklicka på filen). Det behövs ingen server eller installation.

| Fil | Innehåll |
|---|---|
| `index.html` | Översikt för presentation: mål, kundresa, skärmbilder, UX-inspiration, logik, vad som ska verifieras och förslag till användartest |
| `prototyp.html` | Själva prototypen, anpassad för både mobil och dator |
| `skarmbilder/` | Skärmbilder från mobil och dator som används i översikten |

Adressparametrar till `prototyp.html`:

| Parameter | Vad den gör |
|---|---|
| `?vy=mobil` | Visar flödet i en mobilram, bra på storskärm |
| `?test` | Testläge för användartest: döljer demoverktygen |
| `?nystart` | Börjar om från början |
| `?anteckningar` | Visar UX-anteckningar för varje vy |

Demolisten högst upp har **Dator / Mobil**, **Demopersonor** (tre typkunder), **Hoppa till**, **UX-anteckningar** och **Starta om**. Svaren sparas i webbläsaren så att man kan gå fram och tillbaka.

## Kundresan

1. **Ditt hem** – byta eller flytta, postnummer och adress, boendeform, antal rum (eller egen årsförbrukning), uppvärmning (bara villa och radhus), elbil och flexibel elanvändning, sammanfattning av uppskattad förbrukning med justering.
2. **Välj elavtal** – avtalsassistent (”Vad är viktigast för dig?”) och ”Det här avtalet kan passa dig” med motivering och jämförelse av Rörligt månadspris, Kvartspris och Fast pris.
3. **Granska och bekräfta** – sammanfattning med ”Ändra”, Bixia Nära och realtidsmätare som frivilliga tillägg, kontaktuppgifter, så går bytet till, det här godkänner du, illustrativ BankID-signering och ”Det här händer nu”.

## Fyll i priser

Alla priser finns på ett ställe, `PRISER` högst upp i skriptet i `prototyp.html`. `null` visar en platshållare. När ett verifierat pris och ett prisexempel för spotpriset finns räknas uppskattad månadskostnad ut automatiskt på avtalskorten och i sammanfattningen.

```js
const PRISER = {
  spotExempel: null,                               // öre/kWh, prisexempel för spotpris
  rorligt: { manadsavgift: null, paslag: null },   // kr/mån, öre/kWh
  kvart:   { manadsavgift: null, paslag: null },
  fast:    { manadsavgift: null, elpris: null },   // fast elpris i öre/kWh, påslag ingår
  nara:    { manadsavgift: null },
  matare:  { engang: null, manadsavgift: null },
};
```

Schablonvärden för förbrukning (`HUSHALL`, `VARME`, `ELBIL_KWH`) och avtalstexter (`AVTAL`) ligger alldeles under. Färger och typsnitt är CSS-variabler under `:root`.

## Källor och vad som ska verifieras

Bixias webbplats gick inte att läsa direkt när PoC:n byggdes (oktober 2026). Villkoren nedan kommer från sökresultat som återger Bixias egna sidor och dokument och ska stämmas av mot källan före användartest.

**Används i prototypen (verifieras):**

- Rörligt månadspris och Kvartspris: ingen bindningstid, 14 dagars uppsägningstid. Elpriset består av spotpris, rörliga kostnader, påslag och månadsavgift.
- Kvartspris kräver att elmätaren mäter per kvart (tidigare Timpris).
- Fast pris: bindningstid 12 månader. Om kunden inte väljer nytt avtal går det över till Rörligt pris.
- 100 % förnybar el i avtalen.
- Bixia Nära: tillval där kunden väljer ett område och Bixia köper ursprungsgarantier från lokala producenter där. Ingen bindningstid.
- Realtidsmätare: kopplas till elmätarens HAN-port, visar förbrukningen i appen Mitt Bixia ungefär var tionde sekund. Kräver elavtal hos Bixia, aktiverad HAN-port och wifi (2,4 GHz).

**Prisuppgifter som hittades men inte kunde verifieras (används inte i prototypen):**

- Rörligt månadspris: 39 kr/mån och 5 öre/kWh i påslag enligt flera jämförelsesajter.
- Kvartspris: 59 kr/mån och 2 öre/kWh enligt sökresultat från Bixias produktsida. Jämförelsesajter anger 49 kr/mån vid tecknande online och 0 öre i påslag.
- Bixia Nära: 29 kr/mån.
- Fast pris: uppgifter om 24 och 36 månaders bindning förekommer hos jämförelsesajter. Avgiften vid förtida uppsägning hittades inte.

Källor:

- [Sammanfattning av Bixias avtalsvillkor till konsument (pdf)](https://bixia.se/globalassets/dokument/avtalsvillkor/konsument/sammanfattning-av-bixias-avtalsvillkor-till-konsument.pdf)
- [Bixias särskilda avtalsvillkor el – konsument](https://bixia.se/privat/elavtal/teckna/sarskilda-avtalsvillkor-konsument)
- [Bixia Kvartspris](https://bixia.se/privat/elavtal/kvartspris)
- [Bixia Fast pris](https://bixia.se/privat/elavtal/fast-pris)
- [Säga upp elavtal – Bixia](https://bixia.se/kundservice/villkor-blanketter-och-rattigheter/uppsagning)
- [Bixia Nära](https://www.bixia.se/elavtal/bixia-nara)
- [Manual realtidsmätare (pdf)](https://bixia.se/globalassets/dokument/manual/realtidsmatare/manual_realtidsmatare_swe_web.pdf)

**Övrigt att verifiera eller byta ut:**

- Logotyp, typsnitt och exakta färgkoder. PoC:n använder närliggande ersättare (typsnittet Figtree och en varm orange).
- Schablonvärden för förbrukning. De är kalibrerade mot Energimarknadsinspektionens typkunder (lägenhet 2 000, villa 5 000 och villa med elvärme 20 000 kWh/år).
- Elområde från postnummer är en grov mappning för demon. I en riktig lösning hämtas det via adress och anläggning.
- Att Bixia kan säga upp kundens gamla avtal, och preliminärt startdatum (idag + 14 dagar, bara ett exempel).
- Budskapet ”Klart på cirka 3 minuter” ska mätas i användartest.
- Kundservicens telefonnummer och öppettider.

## Utanför PoC:n

Riktig BankID-integration, databaser, API:er, kundregister, livepriser, koppling till Bixias avtalssystem, betalning och produktionsklar arkitektur ingår inte. Funktionerna är simulerade för att visa kundupplevelsen.
