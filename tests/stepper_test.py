import time
from gpiozero import OutputDevice

# Define the GPIO pins connected to ULN2003 IN1-IN4
# Update these numbers if you choose different pins later
IN1 = OutputDevice(23)
IN2 = OutputDevice(24)
IN3 = OutputDevice(25)
IN4 = OutputDevice(26)

# Group pins for easy looping
pins = [IN1, IN2, IN3, IN4]

# Standard 4-step sequence for 28BYJ-48 stepper motor
step_sequence = [
    [1, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 1],
    [1, 0, 0, 1]
]

def step_motor(steps, delay=0.002, clockwise=True):
    """
    Rotates the motor a specified number of steps.
    delay: Time in seconds between steps (lower = faster, but too low will stall)
    clockwise: True for forward, False for reverse
    """
    sequence = step_sequence if clockwise else list(reversed(step_sequence))
    
    for _ in range(steps):
        for step in sequence:
            for pin_index in range(4):
                if step[pin_index] == 1:
                    pins[pin_index].on()
                else:
                    pins[pin_index].off()
            time.sleep(delay)

try:
    print("Rotating clockwise...")
    step_motor(steps=512, delay=0.003, clockwise=True) # 512 steps is roughly 90 degrees
    
    time.sleep(1) # Pause for a second
    
    print("Rotating counter-clockwise...")
    step_motor(steps=512, delay=0.003, clockwise=False)

except KeyboardInterrupt:
    print("\nProgram stopped by user.")

finally:
    # Turn off all pins to prevent the motor from overheating while idle
    for pin in pins:
        pin.off()
    print("Pins cleaned up safely.")
