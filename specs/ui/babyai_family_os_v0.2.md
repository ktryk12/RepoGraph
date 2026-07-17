---
type: spec
project: BabyAI Family OS
version: 0.2
status: proposed
supersedes: "[[babyai_family_os_v0.1]]"
related:
  - "[[babyai_family_os_prototype]]"
  - "[[Approval Center design]]"
  - "[[Roller og privatlivskontrakter]]"
phase: Phase 7
tags:
  - spec/ui
  - babyai
  - policy-first
  - phase-7
created: 2026-04-29
author: Marie
reviewer: Claude
---

# BabyAI Family OS — UI v0.2

**Version:** 0.2 (revideret efter v0.1 review)
**Status:** Forslag til Phase 7 frontend
**Princip:** *Simpelt nok til bedstemor. Stærkt nok til en agent-flåde. Sikkert nok til at lade børn røre ved det.*

---

## 0. Hvad er ændret fra v0.1

Dette er ikke et helt nyt dokument — det er en kirurgisk revision der fjerner det der ikke virker, og strammer det der allerede er godt. De ti vigtigste ændringer:

1. **Onboarding er kortet ned fra 9 til 3 obligatoriske skærme.** Resten flytter til "Smart defaults" som familien justerer senere når de faktisk støder ind i en capability.
2. **Børn-modellen er specificeret.** Ikke længere et flag — det er en hel rolle med eget UI, egne grænser, og et eksplicit privatlivskontrakt mellem barn og forælder.
3. **Approval Center er bygget om så det skalerer.** Auto-godkend-regler, bulk-actions, digest-notifikationer, og en fast-track for kendte sikre kilder.
4. **Live trading er fjernet helt fra Phase 7.** Ikke "disabled" — fjernet. Det kommer i en separat fase med sin egen juridiske discovery.
5. **Voice-revoke er gjort ærligt.** Vi lover ikke at slette lyd hos modtagere — kun at stoppe ny generering og slette internt.
6. **Forklaringslaget har fået en feedback-knap.** "Det her var ikke det jeg ville" → policy-justering eller flag.
7. **Fejl-states er designet, ikke efterrationaliseret.** Hver capability har nu en explicit degraded-mode.
8. **Farvepaletten er forenklet.** Status-farver bærer betydning, capability-farver er kun til ikoner og ikke flader.
9. **Global search er en del af IA fra dag ét.**
10. **Service-arkitekturen er forgrenet, ikke lineær.** Diagrammet afspejler at planneren kan fanout til flere orchestrators parallelt.

---

## 1. Designprincippet (uændret men skarpere)

```
Først regler.
Derefter muligheder.
Altid forklaring.
Tekniske detaljer kun på forespørgsel.
Alt farligt kræver godkendelse.
```

Tre niveauer i UI'et:

| Niveau   | Hvem ser det     | Indhold                                                     |
| -------- | ---------------- | ----------------------------------------------------------- |
| Default  | Alle             | Hvad brugeren kan gøre. Menneskeligt sprog.                 |
| Details  | Den nysgerrige   | Hvorfor BabyAI gjorde det. Hvilke regler den fulgte.        |
| Advanced | Admin/udvikler   | Hvilke services, hvilke events, hvilke latencies, hvilke logs. |

Eksempel — samme handling, tre niveauer:

```
Default:   "Din video er klar til gennemgang."
Details:   "BabyAI fulgte familiens regler, brugte dine billeder, og venter på godkendelse før den publicerer."
Advanced:  "policy-validator → planner → media-renderer → voice-runtime → artifact-writer → publisher (awaiting approval)"
```

---

## 2. Roller, ikke flags

Den største enkeltændring fra v0.1: vi behandler ikke "børn" som en bool. BabyAI har fire roller, og hver rolle har sit eget UI, egne capabilities og egne privatlivsregler.

### 2.1 Roller

| Rolle    | Hvem               | UI                                                  | Privatliv                                                       |
| -------- | ------------------ | --------------------------------------------------- | --------------------------------------------------------------- |
| **Admin**    | Hovedansvarlig    | Fuld adgang, godkendelser, regler, dashboard         | Ser alle samtaler i familien, men kun metadata for andre voksne |
| **Voksen**   | Andre over 18     | Fuld brugerflade, kan anmode om capabilities         | Egne samtaler er private fra admin (kun metadata)               |
| **Teen**     | 13–17             | Som voksen, men med strammere defaults og rate-limits | Forælder ser metadata + flagged content, ikke fuld samtale      |
| **Barn**     | Under 13          | Forenklet UI, færre kort, ingen tekstinput til ukendte capabilities | Forælder ser alt. Barnet får at vide at det gør forælderen.     |

### 2.2 Privatlivskontrakten

Børn skal vide hvad forælderen ser. Det er ikke et juridisk krav alene — det er produktets sjæl. Hvis et barn ikke ved at det bliver overvåget, bygger vi et overvågningsprodukt forklædt som en familieven.

Børneskærmens footer skal stå ærligt:

```
👁️  Mor og far kan se hvad du laver her.
    Det er for at hjælpe dig sikkert.
```

For teens:

```
👁️  Mor og far kan se overskrifter af hvad du laver.
    De ser kun hele samtalen hvis BabyAI markerer noget.
```

For voksne:

```
🔒  Dine samtaler er private. Admin ser kun:
    • At du har brugt BabyAI
    • Hvor mange godkendelser du har anmodet om
```

Det er det. Tre korte linjer. Det er meget mere ærligt end de fleste familieapps og det er en konkurrencefordel.

### 2.3 Aldersgrænser

| Capability         | Barn (<13)              | Teen (13–17)            | Voksen     |
| ------------------ | ----------------------- | ----------------------- | ---------- |
| Chat               | ✅ med simpelt sprog     | ✅                       | ✅         |
| Søgning            | ✅ filtreret             | ✅ filtreret             | ✅         |
| Skrivning          | ✅                       | ✅                       | ✅         |
| Video              | ❌ kun med forælder      | ⚠️ kræver godkendelse    | ✅         |
| Voice (egen stemme) | ❌                       | ⚠️ kræver dobbelt-samtykke (selv + forælder) | ✅ med samtykke |
| Investering         | ❌                       | ⚠️ kun læsning           | ✅ analyse  |
| GitHub-import       | ❌                       | ❌                       | ✅ admin    |

Disse er **defaults**. Admin kan løsne i specifikke tilfælde — men aldrig stramme under "ingenting".

---

## 3. Onboarding — 3 skærme, ikke 9

### 3.1 Princippet: progressive disclosure

Familien skal ikke læse en hel manual før de kan stille det første spørgsmål. De fleste capabilities (video, voice, GitHub, trading) møder de først når de har brug for dem — og det er der vi viser policy-skærmen for *netop den* capability.

**Onboarding er kun det der virkelig skal sættes på forhånd.**

### 3.2 De tre skærme

#### Skærm 1 — Velkomst og admin

```
🤖  BabyAI Family OS
    Din families AI-system

    Vi laver det enkelt: du bestemmer reglerne,
    BabyAI følger dem.

    Hvad er dit navn?
    [ Marie Andersen                              ]

    Du bliver familiens admin. Det betyder du:
    • Bestemmer hvad BabyAI må og ikke må
    • Godkender ting der koster penge eller er
      offentlige
    • Tilføjer andre familiemedlemmer

                                      [ Næste ]
```

#### Skærm 2 — Familien

```
👥  Hvem bor med dig?

    Du behøver ikke tilføje alle nu — de kan
    selv komme på senere.

    [ + Voksen                                    ]
    [ + Teen (13–17)                              ]
    [ + Barn (under 13)                           ]

    [ Spring over — kun mig                       ]
```

For hver tilføjet person:

```
👤  Lars Andersen, voksen
    [ Send invitation til lars@... ]

👧  Emma, 9 år
    Emma logger ind med en pinkode du laver til
    hende.
    [ Lav pinkode ]
```

#### Skærm 3 — Sikker som standard

```
🛡️  BabyAI starter sikkert

    Som default har BabyAI tændt for det trygge:

    ✅  Chat, søgning og skrivning
    ✅  Forklarer hvad den gør
    ✅  Spørger om lov til alt der er nyt eller
        følsomt
    ✅  Børnesikre filtre hvis der er børn

    Følgende er slukket indtil du tænder dem:

    🔒  Video og film
    🔒  Stemmesyntese
    🔒  Investeringsanalyse
    🔒  Import af kode fra GitHub

    Når en familie-bruger første gang prøver
    en af dem, spørger BabyAI dig.

    [ Det lyder rigtigt — kom i gang ]
    [ Jeg vil tilpasse selv ]
```

Det er det. **Skærm 4–9 fra v0.1 er flyttet** til just-in-time policy: de dukker op første gang en bruger prøver at bruge den specifikke capability. Det betyder at en bedstemor der bare vil snakke med BabyAI, kommer i gang på 60 sekunder uden at skulle tage stilling til deepfake-politik.

### 3.3 Just-in-time policy

Første gang nogen i familien klikker på fx "🎬 Film og video", møder admin denne flow før noget genereres:

```
🎬  Film og video skal aktiveres først

    Lars vil prøve at lave en video. Du skal
    sætte familiens regler først.

    Som standard foreslår BabyAI:
    ✅  Familievideoer er tilladt
    ✅  Publicering kræver din godkendelse
    ✅  Videoer med børn kræver din godkendelse
    ❌  Deepfake af andre mennesker er blokeret

    [ Brug standardreglerne ]
    [ Tilpas reglerne ]   [ Sig nej tak ]
```

To klik. Lars kan derefter bruge funktionen, og familien har en fungerende politik som de kan finjustere senere.

---

## 4. Hovedskærmen — rolle-tilpasset

### 4.1 Voksen / Admin

```
┌────────────────────────────────────────────────────┐
│  🤖 BabyAI                            👋 Hej Marie  │
│  ──────────────────────────────────────────────── │
│                                                    │
│  Hvad vil du lave?                                 │
│                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ 💬 Spørg  │ │ 🔍 Find   │ │ 📝 Skriv │           │
│  └──────────┘ └──────────┘ └──────────┘           │
│                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ 🎬 Video  │ │ 🎤 Lyd    │ │ 📈 Aktier │           │
│  │ 🔒 Slukket│ │           │ │ Kun analyse│         │
│  └──────────┘ └──────────┘ └──────────┘           │
│                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ ➕ Tilføj │ │ 📊 Mine   │ │ 🛡️ Regler │           │
│  └──────────┘ └──────────┘ └──────────┘           │
│                                                    │
│  ⚠️  3 ting venter din godkendelse  →              │
│  🔍  Søg i alt jeg har lavet                      │
│                                                    │
└────────────────────────────────────────────────────┘
```

### 4.2 Barn (under 13) — radikalt forenklet

```
┌────────────────────────────────────────────────────┐
│  🐣 Hej Emma!                                       │
│  ──────────────────────────────────────────────── │
│                                                    │
│  ┌─────────────────┐  ┌─────────────────┐         │
│  │  💬             │  │  📚             │         │
│  │  Snak med mig    │  │  Hjælp med lektier│       │
│  └─────────────────┘  └─────────────────┘         │
│                                                    │
│  ┌─────────────────┐  ┌─────────────────┐         │
│  │  🎨             │  │  📖             │         │
│  │  Lav en historie │  │  Find ud af noget│        │
│  └─────────────────┘  └─────────────────┘         │
│                                                    │
│  👁️  Mor og far kan se hvad du laver her.         │
│                                                    │
└────────────────────────────────────────────────────┘
```

Bemærk: ingen sidebar, ingen indstillinger, ingen "tilføj kode", ingen aktiekort. Kun det barnet faktisk skal bruge. Privatlivsfooter er altid synlig — ikke skjult i en menu.

---

## 5. Approval Center — bygget til at skalere

### 5.1 Tre tier af godkendelser

I v0.1 var alle godkendelser ens. Det dur ikke når familien får 30 om ugen.

| Tier         | Hvad         | Hvordan godkendes                                         |
| ------------ | ------------ | --------------------------------------------------------- |
| **Auto**     | Kendt-sikker | Godkendes automatisk hvis en regel matcher. Logges.       |
| **Tap**      | Lavrisiko    | Et enkelt tryk fra notifikation eller hovedskærm.         |
| **Review**   | Højrisiko    | Kræver at admin åbner detaljevisningen og læser.           |

### 5.2 Auto-godkend-regler (det der gør det levedygtigt)

Admin kan oprette regler i et naturligt sprog:

```
✅  Auto-godkend video-eksporter under 30 sekunder
    der ikke indeholder børn

✅  Auto-godkend GitHub-import fra konti jeg har
    godkendt før, hvis sikkerhedsscan er ren

✅  Auto-godkend transskription af mine egne
    optagelser
```

Hver auto-godkend logges som en `approval.auto_granted.v1` event så det stadig er auditbart. Og admin får et ugentligt digest:

```
📬  Denne uge: 23 auto-godkendelser
    14 transskriptioner, 6 korte videoer, 3 GitHub-checks
    Ingenting blev blokeret. [Se detaljer]
```

### 5.3 Bulk og smart-routing

```
┌────────────────────────────────────────────────────┐
│  ✅  Godkendelser (3)                               │
│  ──────────────────────────────────────────────── │
│                                                    │
│  ⚡  Hurtige (kan godkendes med ét tryk)            │
│  ─────────────────────────────────────────         │
│  ☑  🎬 Video "Sommer-klip"     45s, ingen børn     │
│  ☑  🎤 Lydfil til Lars        20s, egen stemme     │
│                                  [ Godkend valgte ]│
│                                                    │
│  🔍  Kræver gennemgang                              │
│  ─────────────────────────────────────────         │
│  💻  Calculator App fra GitHub                     │
│      Sikkerhedscheck: ren                          │
│      Adapter foreslået, ikke registreret endnu     │
│      [ Gennemgå detaljer ]                         │
│                                                    │
└────────────────────────────────────────────────────┘
```

### 5.4 Notifikations-strategi

| Type                   | Hvor                                       |
| ---------------------- | ------------------------------------------ |
| Kritisk (block, fejl)  | Push, badge, email                         |
| Højrisiko (review)     | Push + badge i app                         |
| Lavrisiko (tap)        | Badge i app, ikke push                     |
| Auto-godkendt          | Ugentligt digest                           |
| Børn flagged content   | Push til admin med det samme               |

---

## 6. Capabilities — hvad der ændrede sig

### 6.1 Trading: skåret kraftigt ned

**Phase 7 indeholder kun:**
- Aktieanalyse (læsning af markedsdata, fundamentals, nyhedssammenfatning)
- Paper trading (helt isoleret simulation, ingen rigtige penge)
- Kursalarmer

**Phase 7 indeholder ikke længere:**
- `broker-gateway`
- `order-manager`
- `risk-engine` (som live-component)
- "Live trading slået fra"-toggle

Disse services findes ikke i kodebasen før der er en separat fase med juridisk og regulatorisk discovery. At have dem som "disabled rows" i admin-dashboardet er for risikabelt — det er en på-tryk-her-knap der venter på et uheld.

Trading-svaret beholder sin disclaimer-stil fra v0.1 — den er god.

### 6.2 Voice: ærligere consent-model

v0.1 sagde "consent kan trækkes tilbage". Det er kun halvdelen sandt.

Den ærlige version:

```
🎙️  Når du trækker dit samtykke tilbage:

    ✅  BabyAI sletter din stemmemodel
    ✅  Ingen ny lyd kan laves med din stemme
    ✅  Dine samtykke-events markeres som tilbagetrukne

    ⚠️  Lyd der allerede er lavet og delt med
        andre, kan vi ikke slette hos dem.
        Vi kan kun fjerne det fra dine egne
        gemte filer.
```

Det er den slags ærlighed der bygger tillid. Ikke et tomt løfte.

### 6.3 GitHub: uændret men med fast-track

Sikkerhedsmodellen i v0.1 er solid. Tilføj:

```
✨  Fast-track for kendte konti

    Når en GitHub-konto har bestået 3 sikre
    scans i træk, kan du sætte den på fast-track:
    fremtidige imports fra samme konto godkendes
    automatisk hvis de scanner rent.

    [ Se mine fast-track-konti ]
```

Det fjerner halvdelen af approval-trykket fra teknisk-orienterede admins.

### 6.4 Video: tilføjet draft-watermark

Alle videoer i draft-state er watermarked med "BabyAI · Ikke endeligt". Det forsvinder først når admin godkender. Forhindrer at en draft slipper ud og misforstås som færdig.

---

## 7. Forklaringslag — med feedback

```
┌────────────────────────────────────────────────────┐
│  🔍  Sådan arbejdede BabyAI                         │
│  ──────────────────────────────────────────────── │
│                                                    │
│  1. ✅  Tjekkede familiens regler                   │
│  2. ✅  Lavede storyboard                           │
│  3. ✅  Brugte dine billeder                        │
│  4. ✅  Lavede voice-over                           │
│  5. ⏳  Venter på din godkendelse                   │
│                                                    │
│  ──────────────────────────────────────────────── │
│                                                    │
│  Var det her ikke det du ville?                    │
│  [ 👎  Det her var ikke rigtigt ]                  │
│                                                    │
│  [ Se tekniske detaljer ]   [ OK ]                 │
└────────────────────────────────────────────────────┘
```

Når brugeren trykker 👎:

```
Hvad gik galt?
○  Den brugte forkerte billeder
○  Tonen var forkert
○  Den misforstod hvad jeg ville
○  Den gjorde noget jeg ikke gav lov til
○  Andet: [_____________________]

[ Send feedback ]
```

Det logges som `user.flagged.outcome.v1` med policy-snapshot, så vi kan se mønstre og finjustere policy. Det er forskellen på et produkt der lærer og et produkt der bare svarer.

---

## 8. Fejl-states — designet, ikke efterrationaliseret

For hver capability skal vi have:

| Fejltype                    | UX-respons                                              |
| --------------------------- | ------------------------------------------------------- |
| Service nede                | "BabyAI kan ikke lave video lige nu. Prøv igen senere." |
| Policy timeout              | "Sikkerhedstjekket tager længere end normalt. Vent eller annullér." |
| Scanner offline             | "Vi kan ikke scanne kode lige nu. GitHub-import er pauset indtil vi er sikre igen." |
| Render fejl efter X minutter | "Videoen kunne ikke færdiggøres. Vi gemte alt det vi nåede. [Prøv igen] [Slet]" |
| Auth udløbet til Publisher  | "Forbindelsen til YouTube er udløbet. [Forbind igen]"   |

Sprog-regel: vi siger **aldrig** "noget gik galt", "der opstod en fejl", "kontakt support". Vi siger hvad der ikke virker, og hvad brugeren kan gøre.

---

## 9. Farver og visuelt design — strammere

### 9.1 Status-farver (bærer betydning)

```
✅  Success      #20B26B  (kun til OK-states)
⚠️  Warning      #F6B73C  (skal læses, ikke akut)
❌  Danger       #E74C3C  (blokeret eller fejl)
🔒  Locked       #8B95A8  (slukket af policy)
ℹ️  Info         #4F7CFF  (forklaringer)
```

### 9.2 Capability-accent (kun ikoner og badges)

```
💬  Chat       #4F7CFF
🎬  Video      #E85AAD
🎤  Voice      #15AABF
📈  Trading    #0EA66B
💻  Kode       #7C5CFF
🛡️  Policy     #172033
```

Disse farver bruges **aldrig som store flader**. Kun ikoner, små badges, og hover-detaljer. Det forhindrer at appen ser ud som et regnbue-dashboard.

### 9.3 Typografi

- Display/headlines: en varm humanist sans-serif (fx **Söhne**, **Inter Tight**, eller **Public Sans**)
- Body: samme familie, lidt lavere weight
- Mono (kun admin-dashboard): **JetBrains Mono** eller **IBM Plex Mono**

Aldrig blandede font-familier i én skærm. Aldrig kursiv som fremhævning (det er svagt). Tykkelse og størrelse bærer hierarki.

### 9.4 Tone i copy

| Skriv                                           | Skriv ikke                                  |
| ----------------------------------------------- | ------------------------------------------- |
| "BabyAI venter på din godkendelse"              | "Pending approval required"                 |
| "Det her kan vi ikke lige nu"                   | "Operation failed"                          |
| "Mor og far kan se hvad du laver her"          | "Parental monitoring is enabled"            |
| "Slukket af familiens regler"                   | "Restricted by policy"                      |

---

## 10. Information architecture (med søgning)

```
/
  onboarding/
    welcome
    family
    safe-defaults

  home

  search                ← global, dækker alt brugeren har lavet

  chat
  search-content        ← web-søgning (omdøbt for klarhed)
  write

  capabilities/
    video               ← inkluderer just-in-time policy
    voice
    investing           ← kun analysis + paper
    code-import         ← github

  approvals/
    pending
    rules               ← auto-godkend-regler
    history
    digest

  my-things/
    conversations
    artifacts
    apps
    investments
    library             ← global søgbar

  family/
    members
    roles
    privacy-contracts
    children-settings

  rules-and-safety/
    overview
    capability-rules    ← per capability
    consent-management  ← voice consent, especially
    security-report
    data-and-export

  admin/                ← kun admin-rolle
    services
    events
    policy-decisions
    artifacts
    metrics
    logs
```

---

## 11. Servicearkitektur — forgrenet, ikke lineær

```
                              web-ui
                                 │
                            ui-gateway
                                 │
                            request-gate
                                 │
                  ┌──────────────┴──────────────┐
                  │                             │
          family-policy-service          policy-validator
                  │                             │
                  └──────────────┬──────────────┘
                                 │
                              planner
                                 │
              ┌──────┬───────────┼───────────┬──────┐
              │      │           │           │      │
          chat    search    media-project trading repo-intake
        orchestr orchestr   service       orch.   service
              │      │           │           │      │
              └──────┴───────────┼───────────┴──────┘
                                 │
                  tool-runtime / skill-runtime
                                 │
                          artifact-writer
                                 │
                  ┌──────────────┼──────────────┐
                  │              │              │
              Kafka events  provenance     memory-plane
```

Pointen er at planneren kan parallel-fan-ud til flere orchestrators i samme intent, og at både `family-policy-service` og `policy-validator` skal være tilfreds før noget når til planneren. Det er ikke en kø — det er en gate-tree.

---

## 12. Hvad vi bygger først (revideret rækkefølge)

```
1.  Roller + privatlivskontrakter
        (fundamentet — uden det er resten utroligt)

2.  3-skærms onboarding + just-in-time policy framework
        (alle capabilities hænger på det her)

3.  Approval Center med auto-regler
        (skal være solid før vi slipper noget farligt løs)

4.  Simpel home + chat + search + write
        (det 80% af brugerne faktisk gør)

5.  Forklaringslag + feedback-loop
        (uden det lærer vi ikke)

6.  Fejl-states for alt det ovenstående
        (parallelt — ikke til sidst)

7.  GitHub Phase 7 flow
8.  Voice TTS + consent
9.  Voice cloning (med dobbelt-samtykke for teens)
10. Video draft + storyboard + approval
11. Trading: kun analyse + paper
12. Admin teknisk dashboard

Senere fase:
13. Live trading (kræver separat juridisk/regulatorisk arbejde)
14. Public sharing / publisher OAuth
```

---

## 13. Den endelige produktregel

```
Bedstemor skal kunne stille det første spørgsmål
inden for 60 sekunder.

Et 9-årigt barn skal vide at forælderen kigger med.

En admin skal kunne forstå hvorfor BabyAI gjorde
hvad den gjorde, uden at læse en kodebase.

En udvikler skal kunne se hver event, hver
beslutning, hver service-grænse — på forespørgsel.

Et farligt skridt skal aldrig kunne tages
ved et uheld.
```

Det er hele produktet. Resten er implementering.