# solar-battery-guard

ESP32 "Cheap Yellow Display"-style board running ESPHome as a battery
monitor + AC charger controller for a solar battery bank:

- Reads live voltage/current from a **TBD-SmartShunt** (FE-Shunt app) over
  its UART-TTL port and derives a coulomb-counted State of Charge.
- Shows SoC%, voltage, current, and charger status on the 2.8" ILI9341
  screen.
- Computes on-device whether the AC charger should be running, using
  configurable SoC thresholds with hysteresis, and exposes that decision to
  Home Assistant, which switches your actual smart plug (Kasa/Tuya/Wemo)
  feeding the AC charger.

## Architecture

```
TBD-SmartShunt --UART(TX/GND, ~3.3V)--> ESP32 CYD --WiFi(ESPHome API)--> Home Assistant --> Kasa/Tuya/Wemo plug --> AC charger
                                          |
                                     ILI9341 display
```

The CYD does **not** talk to the smart plug directly - Kasa/Tuya/Wemo are
cloud-oriented protocols that ESPHome doesn't natively drive. Instead the
ESP32 exposes a `binary_sensor` (`charger_should_run`) with the fully
computed decision (threshold + hysteresis + manual override already
applied), and a Home Assistant automation mirrors that onto the real plug.
This also gives you HA's history, notifications, and manual override for
free.

### Why UART instead of the shunt's Bluetooth

The TBD-SmartShunt has both an onboard BLE radio (used by the FE-Shunt
phone app) and a separate physical UART-TTL debug port. This project taps
the UART port rather than BLE, for two reasons:

1. **No existing library speaks its BLE protocol.** It isn't supported by
   any known Home Assistant/ESPHome BLE integration, so using it would mean
   reverse-engineering the GATT protocol from scratch.
2. **BLE devices like this typically accept only one connected client at a
   time.** If the ESP32 held the BLE connection, your phone's FE-Shunt app
   would likely get locked out whenever both want to read the shunt (and
   vice versa). Tapping the UART port instead means the ESP32 and your
   phone can both read the shunt at the same time, since UART is just
   listening to data the shunt already outputs - no exclusive connection.

The `webbbn/esphome-tbd-smartshunt` component this project uses was
specifically reverse-engineered against this UART port for this exact
device (not a generic/borrowed protocol from another shunt brand).

## Hardware

- **LCDWIKI ESP32-32E, model E32R28T** (2.8", ILI9341, 240x320, resistive
  touch, acrylic case). This is sold under generic "ESP32 CYD" listings but
  is a *different manufacturer* than the more commonly-documented Sunton
  "ESP32-2432S028R" boards most online ESPHome/CYD guides are written for -
  see [Board identification](#board-identification-important) below before
  trusting a generic CYD guide's pin map for this board.
- TBD-SmartShunt / FE-Shunt battery monitor, wired in-line on the battery
  bank's negative terminal per its own instructions
- A Kasa, Tuya, or Wemo wifi smart plug feeding the AC charger, already set
  up in Home Assistant

## Board identification (important)

Despite the generic "ESP32 CYD" branding it's often sold under, this exact
board is manufactured by **LCDWIKI** as model **ESP32-32E / E32R28T**, not
a Sunton ESP32-2432S028R. The two look nearly identical and share most
pins, but differ in ways that matter:

- **GPIO22** is wired to the onboard red LED on this board (not free, as
  most generic CYD guides claim).
- **GPIO4** is the display's reset pin on this board (generic guides
  typically say reset is tied to EN with no controllable pin - that's
  wrong for this board, and omitting `reset_pin: GPIO4` is what caused the
  display to boot to a blank white screen despite otherwise-correct SPI
  wiring).
- The display also needs `color_palette: 8BIT` and explicit
  `dimensions: {width: 320, height: 240}` with `transform: {swap_xy: true}`
  on this board for ESPHome's `ili9xxx` driver to initialize it correctly.

Confirmed against [LCDWIKI's own E32R28T/E32N28T pin table](https://www.lcdwiki.com/res/E32R28T/2.8inch_E32R28T_E32N28T_ESP32-32E_Demo_Instructions.pdf)
and a working prior ESPHome project on this exact physical unit. The full
confirmed pin map:

| Function | GPIO | Free for other use? |
|---|---|---|
| TFT CS / DC / SCK / MOSI / MISO / Backlight | 15 / 2 / 14 / 13 / 12 / 21 | No |
| TFT Reset | 4 | No |
| Touch SCK / DIN / DOUT / CS / IRQ | 25 / 32 / 39 / 33 / 36 | No |
| RGB LED (red / green / blue) | 22 / 16 / 17 | No |
| SD card CS / MOSI / SCK / MISO | 5 / 23 / 18 / 19 | No |
| Battery ADC | 34 | No |
| Audio enable / DAC | 4 (shared w/ TFT reset) / 26 | No |
| Boot button | 0 | No |
| Dedicated "UART" JST port | shares UART0 w/ USB programming chip | **No - see warning below** |
| **Free** | **27, 35 (input-only)** | **Yes** |

This is why the shunt UART below uses **IO35** (not GPIO22, and not the
board's dedicated "UART" port - see the next section).

### ⚠️ The board's dedicated "UART" port is a trap

This board has a separate 4-pin JST connector silkscreened simply
"UART", clearly labeled 5V/GND/TXD/RXD - it looks exactly like a spare
port for connecting an external serial device like the shunt. **It is
not.** Per LCDWIKI's own schematic documentation, this port is wired
directly to the ESP32's primary UART0, the same lines used by the onboard
USB-to-serial programming/console chip. Connecting an external device
there puts it in electrical contention with the USB chip - it will not
communicate reliably (or at all), and it does **not** correspond to
whatever GPIO your firmware's `uart:` block is configured for.

This cost significant debugging time on this project: the shunt was wired
to that port, while the firmware listened on a separate, genuinely-free
GPIO - so every diagnostic (voltage checks, baud rate sweeps, even an
internal loopback test) came up empty, because the two ends were never
actually on the same wire. Use the small **separate** expansion header
(silkscreened with individual GPIO numbers like "IO35") for any external
device instead - never the labeled "UART" port.

## Wiring

The shunt's 4-pin JST connector was identified by multimeter directly on
the physical unit (no documentation exists for this exact port):

| Pin | Function | Measured |
|---|---|---|
| 1 | Power | Steady 3.48V DC |
| 2 | TX | Fluctuating 3.38-3.39V DC (idles high, dips as it transmits) |
| 3 | RX | (by elimination) |
| 4 | GND | Continuity to shunt negative |

TX idles at ~3.38V, safely within the ESP32 GPIO's 3.3V-logic tolerance -
**no voltage divider is needed**, unlike an earlier draft of this doc which
assumed 5V logic based on a mislabeled reference.

| Shunt pin | ESP32 CYD pin | Notes |
|---|---|---|
| Pin 2 (TX) | **IO35** on the small expansion header - **direct connection** | No divider needed, confirmed ~3.38V. This is NOT the board's dedicated "UART" JST port - see the warning above. |
| Pin 4 (GND) | Any GND pin | Common ground |
| Pin 1 (Power) | **do not connect** | The CYD has its own separate 5V supply (see Power below) - don't cross-connect |
| Pin 3 (RX) | **do not connect** | The ESP32 never needs to send data to the shunt |

The ESP32 never transmits to the shunt, so no `tx_pin` is configured - the
`uart:` block only sets `rx_pin: GPIO35`.

**Baud rate:** confirmed working at 115200 (the config default) once wired
to the correct pin - real data flows immediately. If you ever need to
experiment with other rates, there's a `select.solar_battery_guard_debug_shunt_uart_baud_rate`
entity in Home Assistant that changes the baud live without reflashing
(useful for testing, remove from the config once no longer needed).

IO35 was chosen over the other free pin (GPIO27) purely because it's
directly silkscreen-labeled "IO35" on this board's expansion header,
removing any ambiguity about which physical pin it is - GPIO27 remains
available on the same header as an alternative if you ever need a second
free GPIO (it just requires more careful pin counting to identify, since
it isn't individually labeled to the same degree on this board).

## Firmware setup (ESPHome)

1. `cd esphome`
2. `cp secrets.yaml.example secrets.yaml` and fill in your wifi
   credentials, an OTA password, and an API encryption key (generate one
   with `esphome generate-key` or `openssl rand -base64 32`).
3. Validate: `esphome config solar-battery-guard.yaml`
4. Flash over USB the first time: `esphome run solar-battery-guard.yaml`
   (subsequent updates can go out over OTA/WiFi once connected to your
   network - use `esphome upload solar-battery-guard.yaml --device
   solar-battery-guard.local` or `esphome logs ... --device
   solar-battery-guard.local` to target it by hostname rather than a
   hardcoded IP, since DHCP may reassign its address between reboots).

The device will boot and show the display even with nothing wired to the
shunt yet - it just displays "NO SHUNT DATA" until IO35 is connected and
receiving data. The display also shows the current date/time (synced from
Home Assistant) and the shunt UART's active baud rate in the top-right
corner as a quick on-device status check.

## Calibrating State of Charge

The TBD-SmartShunt only reports instantaneous voltage and current, not
SoC directly, so the ESP32 integrates power over time (coulomb counting)
against a capacity you configure. **Until you calibrate it, SoC will read
0%/unavailable.** After wiring is complete and the shunt is reporting
voltage/current on the display:

1. In Home Assistant (or the ESPHome web UI), set **Solar Battery Guard
   Battery Capacity** to your bank's real usable capacity in kWh
   (Ah x nominal voltage / 1000, e.g. a 280Ah 12V LiFePO4 bank ≈ 3.4 kWh).
2. The next time the bank is genuinely fully charged (solar controller shows
   float/absorption complete), press **Solar Battery Guard Set Charged
   State**.
3. The next time the bank is at your real-world low cutoff, press **Solar
   Battery Guard Set Discharged State**. This seeds the counter's zero
   point.
4. Optionally tune **Charge Efficiency** (default 1.0) if you know your
   battery's round-trip efficiency.

SoC will drift over days/weeks like any coulomb counter - repeat step 2 or 3
occasionally (e.g. whenever the bank naturally hits full) to re-anchor it.

## Charger control logic

Two adjustable thresholds (numbers, live-editable from Home Assistant),
default 30% / 90%:

- SoC drops **to or below** `Charger Turn-On SoC` -> charger turns ON
- SoC rises **to or above** `Charger Turn-Off SoC` -> charger turns OFF
- Between the two thresholds, the last state is held (hysteresis, prevents
  relay/plug chatter right at the boundary)

A `select.solar_battery_guard_charger_mode` entity (Auto / Force ON / Force
OFF) overrides the automatic logic - useful for manual testing or forcing a
charge before bad weather.

## Home Assistant setup

1. Add the ESPHome device in Home Assistant as usual (Settings -> Devices &
   Services -> ESPHome -> it should auto-discover, or add by IP).
2. Edit [homeassistant/solar_battery_guard.yaml](homeassistant/solar_battery_guard.yaml):
   replace `switch.ac_charger_plug` with your actual Kasa/Tuya/Wemo plug's
   entity ID.
3. Include the file as a package (see the comment at the top of that file)
   or paste its `automation:` block into your existing automations.

This gives you three automations: the core charger control mirror, a
desync alert if the plug doesn't respond to a command within 2 minutes, and
a critical-low-SoC notification as a backstop in case the charger itself
has failed.

### Entity ID gotcha

Entity `name:` fields in `solar-battery-guard.yaml` are intentionally
short (e.g. `"AC Charger Should Run"`, not `"Solar Battery Guard AC
Charger Should Run"`) - Home Assistant's ESPHome integration
automatically prefixes the *device's* friendly name onto each entity for
display, so adding it again in the entity name causes a doubled name
(`solar_battery_guard_solar_battery_guard_...`). Don't reintroduce that
prefix if you add new entities.

If you ever see a doubled entity_id like that in practice (e.g. after
re-pairing the device), it's likely a stale Home Assistant entity
registry entry that didn't get regenerated cleanly - deleting and
re-adding the ESPHome integration doesn't always clear it. The reliable
fix is a manual rename: open the entity in Settings -> Devices & Services
-> Entities, click the gear/settings icon, and directly edit its Entity
ID field to the expected clean value (you may need to enable "Advanced
Mode" in your HA profile to see that field).

## Safety notes

- This automates a mains AC charger. Keep normal electrical safety practice
  (correct fusing/breaker sized for the charger, rated wiring, GFCI where
  applicable) independent of this software - don't rely on the wifi plug as
  your only protection.
- If WiFi or Home Assistant goes down, the smart plug holds whatever state
  it was last commanded to - there's no local fallback relay in this
  design. If that's a concern for your setup, consider a plug with a local
  overcurrent/thermal cutoff, or a future revision wiring a relay directly
  to a free CYD GPIO instead of going through a cloud plug.
- Double-check polarity and voltage on the shunt's UART-TTL port before
  connecting - see your specific shunt's manual, since "TX/RX/GND" pinout
  order can vary between production batches even on the same product
  listing.

## Possible next steps

- Touchscreen on-device controls for the Auto/Force ON/Force OFF modes
  (the resistive touch controller is wired but not yet used in the
  config - needs per-unit touch calibration, so it's left for a follow-up
  once the base monitor is confirmed working).
- Push notifications via the ESPHome device itself (e.g. a piezo/speaker
  alert, since the CYD has one wired to GPIO26) for critical-low SoC,
  independent of Home Assistant being reachable.
