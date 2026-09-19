from gpiozero import LED
from time import sleep

# Define your relay pins (change numbers to match your actual GPIO connections)
# active_high=False forces the Pi to handle the inverted active-low behavior
relay1 = LED(17, active_high=True) 
relay2 = LED(27, active_high=True)
relay3 = LED(22, active_high=True)

print("Relays initialized. Dim lights should now be completely OFF.")

try:
    while True:
        print("Turning Relay 1 ON...")
        relay1.on()  # This sends a LOW signal, turning the relay full ON
        sleep(2)
        
        print("Turning Relay 1 OFF...")
        relay1.off() # This sends a HIGH signal, turning it completely OFF
        sleep(2)

        print("Turning Relay 2 ON...")
        relay2.on()  # This sends a LOW signal, turning the relay full ON
        sleep(2)
        
        print("Turning Relay 2 OFF...")
        relay2.off() # This sends a HIGH signal, turning it completely OFF
        sleep(2)

        print("Turning Relay 3 ON...")
        relay3.on()  # This sends a LOW signal, turning the relay full ON
        sleep(2)
        
        print("Turning Relay 3 OFF...")
        relay3.off() # This sends a HIGH signal, turning it completely OFF
        sleep(2)

except KeyboardInterrupt:
    print("Program stopped.")