"""Pörssisähkövahti: hakee huomisen spot-hinnat ja ilmoittaa, jos sähkö on kallista.

Hinnat ovat snt/kWh sis. ALV, ilman siirtomaksua ja sähköyhtiön marginaalia.

Ilmoitustapa:
  - GitHub-issue (oletus Actionsissa): avaa repoon issuen ja mainitsee NOTIFY_USERS-käyttäjät,
    jolloin GitHub lähettää heille sähköpostin. Ei vaadi salasanoja.
  - Gmail (valinnainen): käytössä, jos SMTP_USER ja SMTP_PASSWORD on asetettu.

Asetukset ympäristömuuttujina (GitHub Actionsissa repon Secrets/Variables):
  NOTIFY_USERS    pilkuilla erotetut GitHub-käyttäjänimet, jotka mainitaan issuessa
  GITHUB_TOKEN, GITHUB_REPOSITORY   Actions asettaa nämä
  SMTP_USER       Gmail-osoite, jolla viesti lähetetään
  SMTP_PASSWORD   Googlen sovellussalasana (16 merkkiä)
  MAIL_TO         vastaanottaja (oletus: SMTP_USER)
  ALERT_AVG      hälytysraja vuorokauden keskihinnalle, snt/kWh (oletus 15)
  ALERT_PEAK      hälytysraja kalleimmalle tunnille, snt/kWh (oletus 30)
  ALWAYS_SEND     "true" = lähetä yhteenveto joka päivä, myös halpoina päivinä
  WINDOW_HOURS    kalleimman/halvimman jakson pituus tunteina (oletus 3)

Paikallinen testaus ilman ilmoituksia:
  python vahti.py --dry-run
"""

import json
import os
import smtplib
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
USER_AGENT = "porssisahko-vahti/1.0 (GitHub Actions)"


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_porssisahko():
    """porssisahko.net: price = snt/kWh sis. ALV, startDate UTC."""
    data = _get_json("https://api.porssisahko.net/v2/latest-prices.json")
    return [
        (datetime.fromisoformat(p["startDate"].replace("Z", "+00:00")), float(p["price"]))
        for p in data["prices"]
    ]


def fetch_spot_hinta():
    """spot-hinta.fi: PriceWithTax = €/kWh sis. ALV."""
    data = _get_json("https://api.spot-hinta.fi/TodayAndDayForward")
    return [(datetime.fromisoformat(p["DateTime"]), float(p["PriceWithTax"]) * 100) for p in data]


def prices_for_day(day):
    """Palauttaa listan (alkuaika paikallisena, snt/kWh) annetulle päivälle. Kokeilee lähteet järjestyksessä."""
    errors = []
    for name, fetch in (("porssisahko.net", fetch_porssisahko), ("spot-hinta.fi", fetch_spot_hinta)):
        try:
            rows = [(t.astimezone(TZ), p) for t, p in fetch()]
        except Exception as e:  # noqa: BLE001 - kokeillaan seuraavaa lähdettä
            errors.append(f"{name}: {e}")
            continue
        rows = sorted(r for r in rows if r[0].date() == day)
        # Normaalisti 96 vartista (92/100 kellojen siirtopäivinä). Vajaa päivä = hintoja ei vielä julkaistu.
        if len(rows) >= 92:
            return rows, name
        errors.append(f"{name}: vain {len(rows)} hintaa päivälle {day}")
    raise RuntimeError("Huomisen hintoja ei saatu:\n  " + "\n  ".join(errors))


def hourly(rows):
    """Laskee 15 min hinnat tuntikeskiarvoiksi. Palauttaa [(tunnin alku, snt/kWh)]."""
    buckets = defaultdict(list)
    for t, p in rows:
        buckets[t.replace(minute=0)].append(p)
    return [(h, sum(v) / len(v)) for h, v in sorted(buckets.items())]


def best_window(hours, length, most_expensive):
    """Kallein tai halvin yhtenäinen `length` tunnin jakso. Palauttaa (alkuindeksi, keskihinta)."""
    sums = [
        (sum(p for _, p in hours[i : i + length]) / length, i)
        for i in range(len(hours) - length + 1)
    ]
    avg, i = max(sums) if most_expensive else min(sums)
    return i, avg


def fmt_span(hours, i, length):
    start = hours[i][0]
    end = hours[i + length - 1][0] + timedelta(hours=1)
    return f"{start:%H}–{end:%H}"


def build_report(day, rows, cfg):
    hours = hourly(rows)
    prices = [p for _, p in hours]
    avg = sum(p for _, p in rows) / len(rows)
    max_i = prices.index(max(prices))
    min_i = prices.index(min(prices))
    w = min(cfg["window"], len(hours))
    exp_i, exp_avg = best_window(hours, w, most_expensive=True)
    cheap_i, cheap_avg = best_window(hours, w, most_expensive=False)
    peak_15 = max(rows, key=lambda r: r[1])

    reasons = []
    if avg >= cfg["alert_avg"]:
        reasons.append(f"keskihinta {avg:.1f} ≥ {cfg['alert_avg']:g} snt/kWh")
    if prices[max_i] >= cfg["alert_peak"]:
        reasons.append(f"kallein tunti {prices[max_i]:.1f} ≥ {cfg['alert_peak']:g} snt/kWh")
    expensive = bool(reasons)

    weekday = ["ma", "ti", "ke", "to", "pe", "la", "su"][day.weekday()]
    day_str = f"{weekday} {day.day}.{day.month}."
    if expensive:
        subject = (
            f"⚡ Kallista sähköä huomenna {day_str}: ka {avg:.1f} snt, "
            f"kalleinta klo {fmt_span(hours, exp_i, w)}"
        )
    else:
        subject = f"Sähkön hinta huomenna {day_str}: ka {avg:.1f} snt/kWh"

    summary = [
        ("Keskihinta", f"{avg:.2f} snt/kWh"),
        ("Kallein tunti", f"klo {hours[max_i][0]:%H}–{hours[max_i][0] + timedelta(hours=1):%H}: {prices[max_i]:.2f} snt"),
        ("Halvin tunti", f"klo {hours[min_i][0]:%H}–{hours[min_i][0] + timedelta(hours=1):%H}: {prices[min_i]:.2f} snt"),
        (f"Kallein {w} h jakso", f"klo {fmt_span(hours, exp_i, w)}: ka {exp_avg:.2f} snt"),
        (f"Halvin {w} h jakso", f"klo {fmt_span(hours, cheap_i, w)}: ka {cheap_avg:.2f} snt"),
        ("Kallein vartti", f"klo {peak_15[0]:%H:%M}: {peak_15[1]:.2f} snt"),
    ]

    # Tekstiversio
    lines = [f"Pörssisähkö {day_str} (snt/kWh sis. ALV, ilman siirtoa)", ""]
    if expensive:
        lines += ["KALLIS PÄIVÄ: " + ", ".join(reasons), ""]
    lines += [f"{k}: {v}" for k, v in summary]
    lines += ["", "Tunnit:"]
    top = max(max(prices), 1)
    for h, p in hours:
        bar = "█" * max(0, round(p / top * 30))
        lines.append(f"{h:%H}  {p:6.2f}  {bar}")
    text = "\n".join(lines)

    # HTML-versio
    def color(p):
        if p >= cfg["alert_peak"]:
            return "#d32f2f"
        if p >= cfg["alert_avg"]:
            return "#f57c00"
        if p < 5:
            return "#388e3c"
        return "#1976d2"

    rows_html = "".join(
        f"<tr><td style='padding:1px 8px'>{h:%H}–{h + timedelta(hours=1):%H}</td>"
        f"<td style='padding:1px 8px;text-align:right'>{p:.2f}</td>"
        f"<td style='padding:1px 8px'><div style='background:{color(p)};height:12px;"
        f"width:{max(1, round(max(p, 0) / top * 250))}px'></div></td></tr>"
        for h, p in hours
    )
    alert_html = (
        f"<p style='color:#d32f2f;font-weight:bold'>Kallis päivä: {', '.join(reasons)}</p>"
        if expensive
        else ""
    )
    summary_html = "".join(
        f"<tr><td style='padding:2px 12px 2px 0'><b>{k}</b></td><td>{v}</td></tr>" for k, v in summary
    )
    html = f"""<html><body style="font-family:Arial,sans-serif;font-size:14px">
<h2 style="margin-bottom:4px">Pörssisähkö {day_str}</h2>
<div style="color:#666">snt/kWh sis. ALV, ilman siirtomaksua ja marginaalia</div>
{alert_html}
<table style="margin:12px 0">{summary_html}</table>
<table style="border-collapse:collapse;font-family:monospace">{rows_html}</table>
</body></html>"""

    # Markdown-versio GitHub-issueta varten
    md = [f"**Pörssisähkö {day_str}** · snt/kWh sis. ALV, ilman siirtomaksua ja marginaalia", ""]
    if expensive:
        md += [f"> [!WARNING]\n> **Kallis päivä:** {', '.join(reasons)}", ""]
    md += ["| | |", "|---|---|"] + [f"| **{k}** | {v} |" for k, v in summary]
    md += ["", "| Klo | snt/kWh | |", "|---|--:|---|"]
    for h, p in hours:
        icon = "🔴" if p >= cfg["alert_peak"] else "🟠" if p >= cfg["alert_avg"] else "🟢" if p < 5 else "🔵"
        md.append(f"| {h:%H}–{h + timedelta(hours=1):%H} | {p:.2f} | {icon} `{'█' * max(0, round(p / top * 20))}` |")
    markdown = "\n".join(md)

    return expensive, subject, text, html, markdown


def github_api(method, path, token, body=None):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def create_issue(subject, markdown, cfg):
    """Sulkee vahdin edelliset avoimet issuet ja avaa uuden, jossa mainitaan NOTIFY_USERS."""
    repo, token = cfg["repo"], cfg["github_token"]
    old = github_api("GET", f"/repos/{repo}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100", token)
    for issue in old:
        github_api("PATCH", f"/repos/{repo}/issues/{issue['number']}", token, {"state": "closed"})
    users = " ".join(f"@{u}" for u in cfg["notify_users"])
    body = markdown + (f"\n\ncc {users}" if users else "")
    issue = github_api("POST", f"/repos/{repo}/issues", token, {"title": subject, "body": body})
    print(f"Issue luotu: {issue['html_url']} (suljettu {len(old)} vanhaa)")


def send_mail(subject, text, html, cfg):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["mail_to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(cfg["smtp_user"], cfg["smtp_password"])
        s.send_message(msg)


def env_float(name, default):
    v = os.environ.get(name, "").strip()
    return float(v.replace(",", ".")) if v else default


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = {
        "smtp_user": os.environ.get("SMTP_USER", "").strip(),
        "smtp_password": os.environ.get("SMTP_PASSWORD", "").replace(" ", ""),
        "alert_avg": env_float("ALERT_AVG", 15.0),
        "alert_peak": env_float("ALERT_PEAK", 30.0),
        "always_send": os.environ.get("ALWAYS_SEND", "").strip().lower() in ("1", "true", "yes", "kyllä"),
        "window": int(env_float("WINDOW_HOURS", 3)),
        "github_token": os.environ.get("GITHUB_TOKEN", "").strip(),
        "repo": os.environ.get("GITHUB_REPOSITORY", "").strip(),
        "notify_users": [u.strip().lstrip("@") for u in os.environ.get("NOTIFY_USERS", "").split(",") if u.strip()],
    }
    cfg["mail_to"] = os.environ.get("MAIL_TO", "").strip() or cfg["smtp_user"]

    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    if len(sys.argv) > 1 and sys.argv[-1] not in ("--dry-run",):
        tomorrow = date.fromisoformat(sys.argv[-1])  # esim. python vahti.py --dry-run 2026-10-28

    rows, source = prices_for_day(tomorrow)
    expensive, subject, text, html, markdown = build_report(tomorrow, rows, cfg)
    print(f"Lähde: {source}, {len(rows)} hintaa\n\n{subject}\n\n{text}\n")

    if not (expensive or cfg["always_send"]):
        print("Ei kallis päivä eikä ALWAYS_SEND päällä → ei lähetetä viestiä.")
        return
    if dry_run:
        print("--dry-run: viestiä ei lähetetty.")
        return
    use_issue = bool(cfg["github_token"] and cfg["repo"])
    use_mail = bool(cfg["smtp_user"] and cfg["smtp_password"])
    if not (use_issue or use_mail):
        sys.exit("Ei ilmoitustapaa: tarvitaan GITHUB_TOKEN + GITHUB_REPOSITORY tai SMTP_USER + SMTP_PASSWORD.")
    if use_issue:
        create_issue(subject, markdown, cfg)
    if use_mail:
        send_mail(subject, text, html, cfg)
        print(f"Viesti lähetetty osoitteeseen {cfg['mail_to']}.")


if __name__ == "__main__":
    main()
