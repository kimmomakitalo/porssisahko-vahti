# Pörssisähkövahti

Ajaa joka iltapäivä GitHub Actionsissa, hakee huomisen pörssisähkön hinnat ja ilmoittaa, jos huomenna on kallista.
Ilmoituksessa on keskihinta, kalleimmat ja halvimmat tunnit ja jaksot sekä tuntitaulukko.

- Hinnat: [porssisahko.net](https://porssisahko.net/api), varalla [spot-hinta.fi](https://spot-hinta.fi). Kumpikaan ei vaadi rekisteröitymistä.
- Hinnat ovat **snt/kWh sis. ALV**, ilman siirtomaksua ja sähköyhtiön marginaalia.
- 15 minuutin hinnat lasketaan tuntikeskiarvoiksi. Kallein yksittäinen vartti näytetään erikseen.

## Miten ilmoitus tulee perille

Kalliina päivänä työnkulku avaa repoon issuen ja mainitsee siinä käyttäjät (@nimi). GitHub lähettää mainituille
sähköpostin osoitteesta `notifications@github.com`. Salasanoja ei tarvita, koska Actions käyttää omaa,
vain tähän repoon rajattua väliaikaista tunnistettaan. Edellinen ilmoitus-issue suljetaan automaattisesti.

Varmista, että sähköposti-ilmoitukset ovat päällä: GitHub → Settings → Notifications.

## Käyttöönotto

1. Luo GitHubiin **yksityinen** repo, esim. `porssisahko-vahti` (ilman README:tä). Yksityisessä repossa ajastus ei sammu 60 päivän jälkeen.
2. Vie tiedostot repoon:
   ```
   git remote add origin https://github.com/kimmomakitalo/porssisahko-vahti.git
   git push -u origin main
   ```
3. Testaa: **Actions → Pörssisähkövahti → Run workflow**. Halpana päivänä viestiä ei synny, joten aseta testin ajaksi muuttuja `ALWAYS_SEND` = `true` (ks. alla).

### Asetukset (valinnaisia)
**Settings → Secrets and variables → Actions → Variables**:

| Nimi | Oletus | Merkitys |
|---|---|---|
| `NOTIFY_USERS` | repon omistaja | Pilkuilla erotetut GitHub-käyttäjät, jotka mainitaan, esim. `kimmomakitalo,puoliso` |
| `ALERT_AVG` | 15 | Ilmoitus, jos vuorokauden keskihinta on vähintään tämä (snt/kWh) |
| `ALERT_PEAK` | 30 | …tai jos jokin tunti on vähintään tämä (snt/kWh) |
| `ALWAYS_SEND` | false | `true` = yhteenveto joka päivä |
| `WINDOW_HOURS` | 3 | Kalleimman ja halvimman jakson pituus tunteina |

### Puolisolle
- **Oma GitHub-tili:** lisää hänet yhteistyökumppaniksi (Settings → Collaborators) ja hänen käyttäjänimensä muuttujaan `NOTIFY_USERS`.
- **Ilman GitHub-tiliä:** tee omaan sähköpostiisi sääntö, joka lähettää `notifications@github.com`-viestit, joiden otsikossa on `porssisahko-vahti`, edelleen hänelle.

### Gmail (valinnainen)
Jos haluat lisäksi HTML-sähköpostin Gmailin kautta, lisää Secrets-välilehdelle `SMTP_USER`, `SMTP_PASSWORD`
(Googlen sovellussalasana) ja tarvittaessa `MAIL_TO`. Käytä tähän mieluiten erillistä Gmail-tiliä.

## Ajastus
Ajo tehdään klo 13:30 UTC, eli talvella klo 15:30 ja kesällä klo 16:30. Jos huomisen hinnat eivät ole vielä saatavilla,
ajoa yritetään uudelleen 15 minuutin välein enintään neljä kertaa. Jos kaikki yritykset epäonnistuvat, GitHub lähettää
sähköpostiin ilmoituksen epäonnistuneesta ajosta.

## Paikallinen ajo
```
pip install -r requirements.txt
python vahti.py --dry-run              # huominen, tulostaa viestin eikä lähetä mitään
python vahti.py --dry-run 2026-10-29   # tietty päivä (jos hinnat ovat saatavilla)
```
