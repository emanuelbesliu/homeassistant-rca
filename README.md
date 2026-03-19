# RCA Insurance Check for Home Assistant

[![HACS Validation](https://github.com/emanuelbesliu/homeassistant-rca/actions/workflows/validate.yml/badge.svg)](https://github.com/emanuelbesliu/homeassistant-rca/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/emanuelbesliu/homeassistant-rca)](https://github.com/emanuelbesliu/homeassistant-rca/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/emanuelbesliu)

Home Assistant custom integration for checking Romanian car insurance (RCA) policy validity via [AIDA](https://www.aida.info.ro/) (Autoritatea de Supraveghere Financiara).

## Features

- Check RCA policy validity for any Romanian-registered vehicle
- Search by registration plate number or VIN/chassis number
- Sensors for policy status, validity dates, insurer name, and days remaining
- Configurable update interval (1 hour to 7 days, default: 24 hours)
- Configurable expiry alert presets (persistent notifications + HA events)
- Support for multiple vehicles via multiple config entries
- Romanian and English translations

## Architecture

This integration requires a companion **rca-browser** microservice that handles the AIDA website interaction (including reCAPTCHA solving via audio challenge and OCR extraction of policy details from anti-scraping images).

```
Home Assistant  --->  rca-browser microservice  --->  aida.info.ro
  (this integration)     (browser-service/)            (AIDA website)
```

The browser microservice is included in this repository under `browser-service/` and is deployed as a Docker container on Kubernetes.

## Prerequisites

- Home Assistant 2024.1.0 or newer
- Running `rca-browser` microservice (see [Browser Service Setup](#browser-service-setup))

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu (top right) > **Custom repositories**
3. Add `https://github.com/emanuelbesliu/homeassistant-rca` with category **Integration**
4. Search for "RCA Insurance Check" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/rca` directory to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration**
2. Search for "RCA Insurance Check"
3. Enter:
   - **Plate Number**: Vehicle registration number (e.g., `B123ABC`)
   - **Search Type**: Registration Number or VIN/Chassis Number
   - **Browser Service URL**: URL of the rca-browser microservice
   - **Update Interval**: How often to check (in seconds, default: 86400 = 24h)

### Options

After setup, go to the integration's **Configure** to adjust:

- **Browser Service URL**: Change the microservice endpoint
- **Update Interval**: How often to check the policy
- **Expiry Alerts**: Choose an alert preset for policy expiry notifications

#### Alert Presets

| Preset | Alerts at | Daily alerts |
|--------|-----------|--------------|
| Conservative | 60, 30, 14, 7 days before expiry | Below 7 days |
| **Standard** (default) | 30, 14, 7 days before expiry | Below 7 days |
| Minimal | 7 days before expiry | Below 7 days |
| Off | No alerts | No alerts |

Alerts are delivered as both **persistent notifications** (visible in the HA notification panel) and **`rca_expiring_soon` events** (for automations).

## Sensors

For each configured vehicle, the following sensors are created:

| Sensor | Description | Example Value |
|--------|-------------|---------------|
| `sensor.rca_{plate}_has_policy` | Whether a valid RCA policy exists | `Yes` / `No` |
| `sensor.rca_{plate}_valid_from` | Policy start date | `2025-07-25` |
| `sensor.rca_{plate}_valid_to` | Policy end date | `2026-07-24` |
| `sensor.rca_{plate}_insurer` | Insurance company name | `ZAD "DallBogg: Zhivot I Zdrave" AD` |
| `sensor.rca_{plate}_days_remaining` | Days until policy expires | `365` |

## Events

The integration fires `rca_expiring_soon` events based on the configured alert preset. Event data includes:

| Field | Description | Example |
|-------|-------------|---------|
| `plate` | Vehicle registration number | `B123ABC` |
| `days_remaining` | Days until policy expires | `12` |
| `valid_to` | Policy end date | `24.07.2026` |
| `insurer` | Insurance company name | `Groupama Asigurari` |

### Automation Example — Push Notification

Create the following automation to receive a push notification on your phone when an RCA policy is about to expire. Replace `notify.mobile_app_your_phone` with your actual mobile app entity (find it under **Settings** > **Devices & Services** > **Mobile App**).

```yaml
alias: "Monitor RCA Expiring Soon"
description: >-
  Trimite notificare push cand polita RCA este pe cale sa expire.
  Se declanseaza de evenimentul rca_expiring_soon emis de integrarea RCA.
mode: single
max_exceeded: silent

triggers:
  - trigger: event
    event_type: rca_expiring_soon

condition: []

actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "RCA expira curand!"
      message: >-
        Polita RCA pentru {{ trigger.event.data.plate }} expira
        in {{ trigger.event.data.days_remaining }} zile
        ({{ trigger.event.data.valid_to }}).
        Asigurator: {{ trigger.event.data.insurer }}.
      data:
        push:
          sound: default
          interruption-level: time-sensitive
```

### Testing the Notification

You can test the automation without waiting for an actual RCA check:

1. Go to **Developer Tools** > **Events**
2. Enter event type: `rca_expiring_soon`
3. Enter event data:
   ```json
   {
     "plate": "B123ABC",
     "days_remaining": 10,
     "valid_to": "24.07.2026",
     "insurer": "Groupama Asigurari"
   }
   ```
4. Click **Fire Event** — you should receive a push notification immediately

## Browser Service Setup

The `rca-browser` microservice is a Docker container that runs Chromium with nodriver to interact with the AIDA website. It solves reCAPTCHA v2 via audio challenge and uses Tesseract OCR to extract policy details from anti-scraping images.

### Kubernetes Deployment

```bash
kubectl apply -f infra/rca-browser.yaml
```

The service runs on port 8194 by default. See `infra/rca-browser.yaml` for the full deployment manifest.

### Docker (Manual)

```bash
docker build -t rca-browser browser-service/
docker run -d -p 8194:8194 --name rca-browser rca-browser
```

### Health Check

```bash
curl http://localhost:8194/health
# {"status": "ok"}
```

## ☕ Support the Developer

If you find this project useful, consider buying me a coffee!

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/emanuelbesliu)

## License

MIT License - see [LICENSE](LICENSE) for details.
