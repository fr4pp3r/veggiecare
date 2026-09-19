from gpiozero import LED
from time import sleep

# Set this to match the relay modules on your bench, and keep it in sync
# with `relays.active_high` in config/config.yaml:
#   True  -> active-high modules  (relay energizes when the pin is HIGH)
#   False -> active-low modules   (relay energizes when the pin is LOW)
ACTIVE_HIGH = True

# Define your relay pins (change numbers to match your actual GPIO connections)
relay1 = LED(17, active_high=ACTIVE_HIGH)
relay2 = LED(27, active_high=ACTIVE_HIGH)
relay3 = LED(22, active_high=ACTIVE_HIGH)

print("Relays initialized. Should be completely OFF.")

try:
    while True:
        print("Turning Relay 1 ON...")
        relay1.on()
        sleep(2)

        print("Turning Relay 1 OFF...")
        relay1.off()
        sleep(2)

        print("Turning Relay 2 ON...")
        relay2.on()
        sleep(2)

        print("Turning Relay 2 OFF...")
        relay2.off()
        sleep(2)

        print("Turning Relay 3 ON...")
        relay3.on()
        sleep(2)

        print("Turning Relay 3 OFF...")
        relay3.off()
        sleep(2)

except KeyboardInterrupt:
    print("Program stopped.")