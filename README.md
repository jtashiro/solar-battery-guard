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

### Why there's no on-device Bluetooth proxy

A Renogy solar charge controller (separate from the battery shunt this
project already reads) needs a BLE connection to pull its data into
Home Assistant. Adding ESPHome's `bluetooth_proxy:` directly to
`solar-battery-guard` was tried first, since it would avoid needing
separate Bluetooth hardware - but it crash-loops this exact board: the
base ESP32 (no PSRAM, 320KB SRAM) already runs WiFi + this display +
UART sensor polling, and the BLE controller's own initialization can't
get enough contiguous heap alongside all of that. The bootloader's OTA
rollback protection silently reverts to the last-known-good firmware
every time this happens, which looks like "nothing happened" rather
than an obvious failure - worth knowing if a config change ever
apparently doesn't take effect after a successful-looking OTA upload:
check for an `OTA rollback detected` line in the boot log before
assuming the flash itself failed.

Trimming the proxy's settings (`connection_slots`, `cache_services`)
didn't help, since the crash happens in the BLE controller's own
bring-up, before any of ESPHome's own components even run - not a
tunable-parameter problem. The working solution instead is a normal USB
Bluetooth adapter (confirmed BLE-capable - explicitly rated Bluetooth
4.0+, e.g. TP-Link UB500) plugged into whichever machine runs Home
Assistant, using its own `bluetooth:` integration plus a
Renogy-specific HACS integration - no ESP32 involved in the Bluetooth
path at all.

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

### Auto-calibrating on full charge

Manually pressing **Set Charged State** every time the bank reaches full
is easy to forget, and each missed cycle lets SoC drift further from
reality. The device can also detect this itself: once **Battery
Voltage** has been at/above **Auto-Calibrate Voltage Threshold**
(default 13.6V - tune this to your charge controller's actual
float/absorption-complete voltage, e.g. via its app or LCD) AND
**Battery Current** has tapered to at/below **Auto-Calibrate Tail
Current Threshold** (default 1.0A), continuously for 15 minutes, it
automatically performs the same recalibration as pressing **Set
Charged State** manually.

The 15-minute sustained requirement (not just an instantaneous reading)
exists specifically so a cloud passing over the panels mid-charge - a
brief current dip that momentarily looks "tapered" - can't falsely
trigger a recalibration; the underlying **Battery At Float** diagnostic
binary sensor only turns on after the condition holds continuously, and
resets its timer immediately if voltage/current drop back out of range.

Caveat: **Battery Current** is measured at the shunt on the battery's
negative terminal, so it reads *net* current (charging in minus
whatever DC loads are drawn directly off the battery bus), not the
charge controller's own charge current in isolation. If your loads
aren't routed through the controller's own load terminal, net current
during float may never actually drop below the tail threshold even once
the battery is genuinely full - watch **Battery At Float** during a
real charge cycle and raise the tail current threshold if it never
trips.

Disable entirely via **Auto-Calibrate On Full Charge** (defaults on) if
it ever misbehaves - falls back to purely manual calibration.

### Power, Consumed Ah, and Time Remaining

The TBD-SmartShunt only reports raw voltage and current over UART - it
does not natively report power, consumed Ah, or time remaining as
distinct fields (what the FE-Shunt phone app shows for these is almost
certainly computed client-side from the same raw voltage/current stream,
not sent by the shunt hardware itself). This project computes its own
versions of all three, exposed as regular sensors:

- **Battery Power** (W) - simply voltage x current, negative while
  discharging, positive while charging.
- **Consumed Ah** - a second coulomb counter, independent of the Wh-based
  SoC one, tracking net Ah removed since the battery was last calibrated
  to 100% (always <= 0). Reset by the same **Set Charged State** /
  **Set Discharged State** presses used for SoC calibration - no separate
  calibration step needed.
- **Time Remaining** (hours) - current stored energy divided by the
  present discharge rate. Only meaningful while actively discharging;
  reads unavailable while charging, idle, or before SoC is calibrated,
  since "time until empty" isn't a sensible number in those states.
- **Battery Status** (text) - "Charging" / "Discharging" / "Idle", or
  "Unknown" if the shunt isn't reporting. Exists because Time Remaining
  is intentionally unavailable in the charging/idle cases above, so this
  fills in *why* rather than leaving a bare unknown - both devices'
  screens show it in place of "--h left" whenever Time Remaining itself
  isn't available.

All four are also shown on-screen on both devices (a compact line below
the voltage/current readout, e.g. "-109W  -0.1Ah  Discharging"). The
monitor viewer pulls them the same way it pulls everything else - via
whichever mechanism `select.solar_battery_monitor_data_source` currently
has selected.

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

## Backlight scheduling

Both devices' backlights can be scheduled off overnight and back on in
the morning, via Home Assistant automations (not on-device logic - see
[homeassistant/solar_battery_guard.yaml](homeassistant/solar_battery_guard.yaml)
and [homeassistant/solar_battery_monitor.yaml](homeassistant/solar_battery_monitor.yaml);
each device gets its own independent schedule). Two modes, selectable
live per device:

- **Fixed Times** - two `input_datetime` helpers (default 22:00 off /
  07:00 on), editable anytime from Home Assistant without touching YAML.
- **Sunset/Sunrise** - follows Home Assistant's `sun` integration
  instead of fixed clock times.

An `input_select` helper per device (`Guard Backlight Schedule Mode` /
`Backlight Schedule Mode`) picks which mode is active; both pairs of
automations are always present and trigger on schedule regardless, but
each pair's `condition` blocks it from doing anything unless its mode is
the one currently selected.

This lives in Home Assistant rather than on-device deliberately - the
backlight light entity already exists and is directly controllable, so
scheduling it there means changes take effect without a reflash, same
reasoning as the charger control logic above.

## Touch controls

Both devices' resistive touch controllers (wired but unused until now -
see the pin table above) are active, using an XPT2046 touchscreen
component on a separate SPI bus from the display (SCK/DIN/DOUT/CS/IRQ =
GPIO25/32/39/33/36, per the pin table above - not the display's own SPI
pins).

**Deliberately skips per-unit position calibration.** The usual
resistive-touch bring-up involves tapping each corner and recording raw
ADC values to map them to screen pixels - tedious, and per-unit (every
physical panel needs its own numbers). Since neither device needs to
know *where* on the screen was tapped, just *that* a tap happened, both
use the full 0-4095 raw ADC range as a generous default and only react
to the touch event itself. A future feature needing actual tap-zone
detection (e.g. on-screen buttons) would need that position calibration
step then - it's skipped here only because nothing yet requires it.

- **solar-battery-guard**: tap anywhere toggles the backlight on/off
  directly, independent of the backlight schedule above.
- **solar-battery-monitor**: tap anywhere toggles between Classic and
  App Style display modes (see below).

Both are debounced (600ms) in firmware, since a single physical tap can
otherwise register as multiple touch events while contact is held or
bounces.

## Home Assistant setup

1. Add the ESPHome device in Home Assistant as usual (Settings -> Devices &
   Services -> ESPHome -> it should auto-discover, or add by IP).
2. Edit [homeassistant/solar_battery_guard.yaml](homeassistant/solar_battery_guard.yaml):
   replace `switch.ac_charger_plug` with your actual Kasa/Tuya/Wemo plug's
   entity ID.
3. Include the file as a package (see the comment at the top of that file)
   or paste its `automation:` block into your existing automations.

This gives you three core automations (charger control mirror, a desync
alert if the plug doesn't respond within 2 minutes, and a
critical-low-SoC notification as a backstop) plus four backlight-schedule
automations - see [Backlight scheduling](#backlight-scheduling) above for
those. All send their alerts via `notify.notify`, so make sure you have a
notify target configured in Home Assistant (mobile app notifications,
etc.) or redirect that service call to whatever you actually use.

### Packages include-tag gotcha

If you're using `homeassistant: packages:` to load these files (rather
than pasting their contents directly into `automations.yaml`/
`configuration.yaml`), the include tag matters: use
`!include_dir_named packages`, not `!include_dir_merge_named packages`.
The merge variant flattens every file's top-level keys (`automation:`,
`input_select:`, etc.) into one shared dict *across every file in the
folder*, rather than keying each file's content by its own filename -
which breaks package loading in a way that produces genuinely confusing
errors, e.g. `Setup of package 'input_select' failed: Integration
'<your_entity_name>' not found` (Home Assistant ends up trying to
interpret your entity's object_id as if it were a domain/integration
name). If you see errors shaped like that, check this tag before
suspecting the package file's own content - it can also silently break
*other*, unrelated packages already in the same folder once you add a
package file with multiple top-level keys, since it's the folder's
merge behavior that's wrong, not any individual file.

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

A related but different manifestation of this same underlying issue: a
brand-new entity's ID can also come out prefixed with something
unexpected entirely (e.g. an Area name, like
`sensor.si_mining_shed_solar_battery_guard_battery_status`) rather than
doubled - also caused by a stale/orphaned registry entry colliding with
the clean name you'd expect. Same fix applies: check Developer Tools ->
States for the actual entity_id rather than assuming it matches the
device+entity name pattern, especially right after adding a new entity
to an already-established device - don't trust automations/polling URLs
you wrote against the assumed clean name until you've verified it.

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

- Touch-driven charger mode cycling (Auto/Force ON/Force OFF) - the
  resistive touch controller itself is now confirmed working (see
  [Touch controls](#touch-controls) above, currently used only for
  backlight toggling), so this is now just a matter of adding tap-zone
  detection for this specific action, not a hardware bring-up problem
  anymore.
- Push notifications via the ESPHome device itself (e.g. a piezo/speaker
  alert, since the CYD has one wired to GPIO26) for critical-low SoC,
  independent of Home Assistant being reachable - distinct from the
  `notify.notify` Home Assistant alerts already in place (see Home
  Assistant setup above), which depend on HA being reachable.

## Second device: display-only viewer (`solar_battery_monitor.yaml`)

A separate, optional ESP32 CYD board (same LCDWIKI ESP32-32E/E32R28T
hardware) that shows battery status on its own screen elsewhere in the
house/vehicle/vessel, without any shunt wired to it. It has no local
sensing or charger-control logic at all - every value it displays (SoC,
voltage, current, charger status/mode) is read from the main
`solar-battery-guard` device's entities already in Home Assistant.

**Prerequisite**: the main solar-battery-guard device must already be set
up and reporting into Home Assistant, since this device has nothing to
show otherwise.

### Why this polls Home Assistant's REST API instead of using ESPHome's push subscription

The first version of this device used ESPHome's `platform: homeassistant`
sensor/binary_sensor/text_sensor integrations, which subscribe to entity
states and have Home Assistant push updates as they change. In practice
this was unreliable here: it depends on Home Assistant proactively
sending a `SubscribeHomeAssistantStatesRequest` message that, for reasons
never fully pinned down, wasn't reliably happening - even a full
delete-and-rediscover of the device didn't fix it. Separately, ESPHome's
`homeassistant` sensor platform is documented to not forward *unchanged*
values even with `force_update` set on the source
([esphome/esphome#13351](https://github.com/esphome/esphome/issues/13351)),
which would have silently broken the SoC display specifically once it
reached a stable value like 100%.

Instead, `solar_battery_monitor.yaml` polls Home Assistant's REST API
directly (`GET /api/states/<entity_id>`) on a fixed timer (`poll_interval`,
default 10s) using the `http_request:` component, parses the JSON
response, and stores the values in global variables the display reads
from. This is a plain, predictable request/response cycle with no
dependency on HA's push timing or dedup behavior.

**Setup**:
1. `cd esphome` (same folder - both device YAML files share one
   `secrets.yaml`).
2. Fill in `monitor_api_encryption_key` and `monitor_ota_password` in
   `secrets.yaml` if not already done (kept separate from the main
   device's credentials since it's a distinct physical unit).
3. Fill in `ha_base_url` (your Home Assistant instance's local address,
   e.g. `http://192.168.1.X:8123`) and `ha_long_lived_token` (generate one
   from your HA profile: click your user icon (bottom-left) -> scroll to
   bottom -> "Long-Lived Access Tokens" -> Create Token).
4. `esphome run solar_battery_monitor.yaml` to flash over USB the first
   time; OTA (`--device solar-battery-monitor.local`) after that.

If you ever rename entities on the main device, update the URLs in
`solar_battery_monitor.yaml`'s `interval:` polling block to match. **Don't
just assume a rename produced the exact clean name you typed** - if
another (often stale/orphaned) entity already occupies that exact
entity_id, Home Assistant silently appends `_2` rather than erroring, and
the REST API will 404 on the name you expected. Always verify the actual
entity_id in Developer Tools -> States after renaming, for each entity
individually.

### Data Source select: comparing both mechanisms live

The device also runs the original push-subscription mechanism (via
ESPHome's `platform: homeassistant` entities, using the corrected entity
IDs) in parallel with HTTP polling, and exposes a `select.solar_battery_monitor_data_source`
entity ("HTTP Polling" / "Push Subscription") in Home Assistant that
switches which one actually drives the display - no reflash needed to
compare them.

This exists because the push mechanism's original failure was likely
caused mostly by the wrong-entity-name issue described above, rather than
a deeper protocol problem - once the names were fixed, it may well work
correctly. HTTP polling stays the default regardless of how that
comparison turns out, since its failures are loud (an explicit 404 with
the exact URL in the log) where the push mechanism's are silent - given
how much debugging time the silent-failure mode cost on this project, that
observability difference matters more than which one is marginally more
efficient. HTTP requests are automatically skipped while "Push
Subscription" is selected, so there's no wasted API load either way.

### Display modes: Classic / App Style

The monitor supports two on-screen layouts, toggleable live via
`select.solar_battery_monitor_display_mode` in Home Assistant or by
[tapping the screen](#touch-controls):

- **Classic** - the original landscape layout (big SoC%, voltage/current,
  power/Ah/time line, charger status banner).
- **App Style** - approximates the Renogy phone app's home screen (a
  circular SoC gauge with Battery Status shown underneath, stacked
  Voltage/Current/Power/Consumed Ah/Time Left rows with simple
  lettered-circle icons), built entirely from ESPHome's basic drawing
  primitives - no gradients or vector icons available on this display,
  so it's an approximation of the app's look, not a pixel-perfect copy.

App Style rotates the physical panel to portrait
(`it.set_rotation(DISPLAY_ROTATION_90_DEGREES)`) on top of this board's
existing static `swap_xy` transform (see
[Board identification](#board-identification-important) above) -
confirmed working on this hardware, though the exact rotation value
needed (90° vs 180°/270°) was found by testing on the physical unit
rather than derived from the two transforms' theoretical combination.
Treat that value as board-specific-verified rather than portable to a
different panel/wiring without re-checking on real hardware.
