# Celestron NexStar GT als Zweitmontierung

Eine ältere Celestron NexStar GT (azimutal, mit Handbox) lässt sich über den Astro-Pi ([07](07-astro-pi.md)) wie die iEXOS-100 steuern: GoTo, Zentrieren per Plate-Solving, Serien. Dafür gibt es zwei Wege.

> **Stand:** recherchiert, aber nicht am Gerät getestet. Vor dem ersten Einsatz Fotos von der Buchse an der Montierung, vom Typenschild und von der Versionsanzeige der Handbox machen und den passenden Weg wählen.

## Überblick

| | A: über die Handbox | B: direkt am AUX-Bus (ohne Handbox) |
|---|---|---|
| INDI-Treiber | `indi_celestron_gps` | `indi_celestron_aux` |
| Anschluss | Serielle Buchse unten an der Handbox (4-polig RJ11) | Buchse der Handbox am Montierungsarm (6-polig RJ12) |
| Pegel / Baudrate | RS-232 (±12 V), 9600 Baud | **TTL (3,3–5 V)**, 19200 Baud, halbduplex |
| Adapter | echter RS-232-USB-Adapter (z. B. FTDI/Prolific mit DB9 + Kabel auf RJ11) | USB-TTL-Adapter + Diode, oder ESP32 als WLAN-Brücke |
| Ausrichtung | Sternauswahl der Handbox, danach Sync per Plate-Solving | nur Plate-Solving („GoTo + zentrieren“) |
| Funktionsumfang | abhängig von der Handbox-Firmware; sehr alte Versionen oft nur GoTo + Position | Motorsteuerung direkt, unabhängig von der Handbox-Firmware |
| Risiko | gering | Pegel und Belegung müssen stimmen (siehe Sicherheit) |

Quelle für Anschlüsse und Pegel: [INDI Celestron AUX (StellarMate-Doku)](https://stellarmate.com/devices/mounts/celestron/248-celestron-aux.html).

## Weg A: über die Handbox

1. Prüfen, ob die Handbox unten eine kleine serielle Buchse hat (meist 4-polig RJ11) und welche Firmware sie meldet (Anzeige beim Einschalten bzw. im Menü „Version“).
2. Kabel RJ11 → DB9 (Celestron „PC-Kabel“ oder nachgebaut) plus **echter RS-232-USB-Adapter**.
3. Auf dem Pi in `/etc/default/astro-pi-indi`:
   ```bash
   INDI_DRIVERS="indi_celestron_gps indi_canon_ccd"
   ```
   `sudo systemctl restart indiserver`
4. In der Handbox zuerst das normale Alignment durchführen, dann in der Astro-Pi-App „GoTo + zentrieren“ verwenden.

Sehr alte GT-Handboxen sprechen nur einen Teil des Protokolls. Was fehlt (z. B. Sync, Parken, Nachführmodus), meldet der Treiber im Status-Tab der App bzw. in den Meldungen.

## Weg B: direkt am AUX-Bus

Die Handbox spricht über ihr Kabel zum Montierungsarm den Celestron-AUX-Bus mit den Motorsteuerungen. `indi_celestron_aux` kann das selbst – die Handbox wird abgesteckt (sie kann den Treiber stören).

**Elektrisch** (laut Treiber-Doku): 6-polig RJ12, TTL-Pegel, 19200 Baud, halbduplex. Sende- und Empfangsdaten teilen sich eine Leitung, eine zweite Leitung signalisiert, wer gerade sendet (CTS/RTS-Verfahren mit Echo).

**Variante B1 – Kabel:** USB-TTL-Adapter (mit RTS/CTS herausgeführt) und eine Signaldiode (z. B. 1N4148) zwischen TX und der gemeinsamen Datenleitung. Ein Schaltungsbeispiel für Arduino mit genau diesem Prinzip zeigt [nexstar-arduino](https://github.com/ars-ka0s/nexstar-arduino) (getestet nur mit neueren GT-Montierungen).

**Variante B2 – WLAN:** Ein ESP32 an der Buchse setzt den AUX-Bus auf TCP um (wie Celestrons SkyFi-Adapter). Der Treiber unterstützt das direkt im Netzwerk-Modus (Standard: Port 2000). Vorteil: kein Kabel zwischen Pi und Montierung, die Montierung ist auch vom Tablet bzw. aus Ekos direkt erreichbar. Die ESP32-Firmware müsste noch erstellt werden (Idee, siehe Roadmap).

**Treiber auf dem Pi:** `indi_celestron_aux` gehört seit INDI 1.9.4 zu den Treibern, fehlt aber in manchen Distributionspaketen (z. B. Ubuntu 24.04 mit `indi-bin` 1.9.9 hat nur `indi_celestron_gps`). Der Installer versucht dann `indi-celestronaux`; sonst die Treiber aus dem INDI-PPA bzw. den 3rd-Party-Paketen installieren (StellarMate und AstroArch bringen ihn mit).

**Treiber auf dem Pi:**
```bash
INDI_DRIVERS="indi_celestron_aux indi_canon_ccd"   # /etc/default/astro-pi-indi
```
Im Treiber den Verbindungsmodus wählen: seriell (USB-TTL-Adapter) bzw. Netzwerk (ESP32, IP + Port 2000).

## Sicherheit

- An einem Pin der AUX-Buchse liegen **12 V** an. Belegung vor dem Anschließen mit dem Multimeter prüfen, nicht nach Fotos aus dem Netz verdrahten.
- **Nie** einen RS-232-Adapter (±12 V) an die AUX-Buchse – nur TTL. Umgekehrt braucht die Handbox-Buchse (Weg A) RS-232, ein TTL-Adapter funktioniert dort nicht.
- Erste Tests **nur lesend**: Position bzw. Firmware-Version abfragen, noch nicht fahren. Danach mit niedriger Geschwindigkeit und Hand am Netzschalter.
- Ob eine sehr alte GT denselben AUX-Befehlssatz spricht wie neuere Modelle, ist nicht dokumentiert. Der Treiber ist laut Doku mit NexStar Evolution und CPC getestet und als Beta gekennzeichnet.

## Beobachten mit der GT

- **Azimutale Montierung:** Das Bildfeld dreht sich während der Nacht. Belichtungen kurz halten (etwa 15–30 s) und viele Frames aufnehmen. siril-auto gleicht die Drehung bei der 2-Pass-Registrierung aus (wie beim Seestar).
- **Tragkraft:** Die GT-Montierungen sind für leichte Optiken gebaut. Die 6D mit einem leichten Objektiv (bis etwa 200 mm) passt eher als ein Refraktor mit Kamera.
- **Ausrichtung:** Mit Weg B ersetzt das Plate-Solving die Sternauswahl der Handbox: grob nach Norden ausrichten, waagerecht stellen, „GoTo + zentrieren“.
- **Stromversorgung:** Ältere GTs reagieren empfindlich auf schwache Batterien (Positionsverlust). Ein stabiles 12-V-Netzteil oder eine Powerbox verwenden.

## Checkliste vor dem Basteln

- [ ] Fotos: Buchse am Montierungsarm, Buchse unten an der Handbox, Typenschild der Montierung, Versionsanzeige der Handbox
- [ ] Weg wählen: Handbox hat serielle Buchse und brauchbare Firmware → A; sonst → B
- [ ] Belegung der Buchse mit dem Multimeter prüfen (12 V, Masse)
- [ ] Am Pi zuerst `--mount simulator`, dann den echten Treiber eintragen
- [ ] Lesend testen, dann langsam fahren, dann GoTo + Zentrieren
