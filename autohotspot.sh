#!/usr/bin/bash

ap_ssid="Veggiecare1.0"
ap_password="veggiecare2026"

check_wlan0_connected() {
    local wlan_state=$( nmcli connection show --active | grep -E "wlan0\s ")
    if [ -n "$wlan_state" ]; then
        return 0  # true - connected
    fi

    return 1  # false - not connected
}

is_pi_zero() {
    if [ -f /proc/device-tree/model ]; then
        local model=$(cat /proc/device-tree/model | tr -d '\0')
        if [[ "$model" == *"Raspberry Pi 5 Model B Rev 1.1"* ]]; then
            return 0  # true - is Pi Zero
        fi
    fi

    return 1  # false - not Pi Zero
}

if [ "$(whoami)" != "root" ]; then
    echo "This script must be run as root"
    exit 1
fi

echo "Checking WiFi connection..."

counter=0
# Loop for a maximum of 15 seconds
while [ $counter -lt 15 ]; do
    echo "Check #${counter}"

    # Check if connected to WiFi network
    if check_wlan0_connected; then
        echo "Connected to internet via WiFi"
        exit 0
    fi

    sleep 1
    counter=$((counter + 1))  # Increment the counter by 1 each second
done

echo "No connection, switching to AP mode"
nmcli device wifi hotspot ssid ${ap_ssid} password ${ap_password} ifname wlan0

if is_pi_zero; then
    echo heartbeat | sudo tee /sys/class/leds/ACT/trigger
fi

exit 0