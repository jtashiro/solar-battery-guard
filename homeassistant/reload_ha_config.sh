#!/usr/bin/env bash
# Reloads all reloadable HA YAML config (packages - automation, input_select,
# input_datetime - plus pyscript modules) via the REST API, without a full HA
# restart. Equivalent to the Developer Tools -> YAML -> "Reload All" button.
# Reuses the ha_base_url / ha_long_lived_token already set up in
# ../esphome/secrets.yaml for solar_battery_monitor.yaml's polling.
#
# Usage: ./reload_ha_config.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
secrets_file="$script_dir/../esphome/secrets.yaml"

if [[ ! -f "$secrets_file" ]]; then
    echo "error: secrets file not found at $secrets_file" >&2
    echo "copy esphome/secrets.yaml.example to esphome/secrets.yaml and fill in ha_base_url / ha_long_lived_token" >&2
    exit 1
fi

extract() {
    sed -nE "s/^$1:[[:space:]]*\"(.*)\"[[:space:]]*\$/\1/p" "$secrets_file"
}

ha_base_url="$(extract ha_base_url)"
ha_token="$(extract ha_long_lived_token)"

if [[ -z "$ha_base_url" || -z "$ha_token" ]]; then
    echo "error: ha_base_url or ha_long_lived_token missing/empty in $secrets_file" >&2
    exit 1
fi

response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT

http_status=$(curl -s -o "$response_file" -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer $ha_token" \
    -H "Content-Type: application/json" \
    -d '{}' \
    "$ha_base_url/api/services/homeassistant/reload_all")

if [[ "$http_status" != "200" ]]; then
    echo "error: reload_all failed (HTTP $http_status)" >&2
    cat "$response_file" >&2
    exit 1
fi

echo "HA config reloaded (packages, automations, input helpers, pyscript, etc.)"
