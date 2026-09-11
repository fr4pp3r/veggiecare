## Project: VeggieCare — Raspberry Pi 5 Smart Plant Monitoring and Control System

I am developing a system called **VeggieCare**, which will run on a **Raspberry Pi 5**.

I already have individual Python programs that have been tested and are working for:

* NPK sensor
* Soil moisture sensor
* Relay control

These components currently work independently. I want to integrate them into one reliable, modular, and configurable system.

I **do not yet have the Raspberry Pi Camera Module 3 code or the pest image-recognition model**. These will need to be implemented later. Therefore, design the system architecture so that the camera and pest-detection functionality can be added without requiring major changes to the existing system.

---

# 1. Main System Requirements

VeggieCare should monitor and manage a plant-growing environment using:

* NPK sensor
* Soil moisture sensor
* Three relays
* Raspberry Pi 5
* Raspberry Pi Camera Module 3 — **future component**
* Pest image-recognition model — **future component**
* Web-based dashboard
* Local database for logs and historical data

The application should run continuously on the Raspberry Pi 5.

The system should be modular so individual hardware components can be replaced or upgraded without rewriting the entire application.

---

# 2. Current Hardware

The following components already have tested Python code:

### NPK Sensor

The existing NPK Python code should be integrated into the new system rather than unnecessarily rewritten.

The system should periodically read:

* Nitrogen (N)
* Phosphorus (P)
* Potassium (K)

### Soil Moisture Sensor

The existing soil-moisture Python code should also be integrated.

The system should periodically read the current soil moisture level.

### Relay Module

The existing relay-control Python code should be integrated.

There are three relays:

* **Relay 1:** NPK/fertilizer control
* **Relay 2:** Soil moisture/watering control
* **Relay 3:** Pest-response control

---

# 3. Relay 1 — NPK/Fertilizer Control

When one or more NPK values fall below their configured thresholds:

1. Display the current NPK values on the dashboard.
2. Clearly indicate which value(s) are below the configured threshold.
3. **Do not automatically activate Relay 1.**
4. Display a button near the NPK status.
5. The user can press the button to activate Relay 1 manually.
6. Relay 1 should operate for the configured activation duration.
7. Record the activation in the system logs.

The following should be configurable:

* Nitrogen threshold
* Phosphorus threshold
* Potassium threshold
* Relay 1 activation duration

The dashboard should clearly distinguish between:

* Normal NPK values
* NPK values below threshold
* Relay 1 currently active
* User-initiated Relay 1 activation

---

# 4. Relay 2 — Soil Moisture/Watering Control

When soil moisture falls below the configured threshold:

1. Display the current soil moisture value on the dashboard.
2. Indicate that the soil moisture is below the configured threshold.
3. Automatically activate Relay 2.
4. Keep Relay 2 active for the configured watering duration.
5. Turn Relay 2 OFF automatically after the configured duration.
6. Record the activation in the system logs.

### Monthly Activation Limit

Relay 2 may be activated **a maximum of two times per calendar month**.

The system must:

* Track Relay 2 activations.
* Display the number of activations used this month.
* Display the number of remaining activations.
* Prevent automatic activation after the monthly limit has been reached.
* Display an appropriate warning on the dashboard when the limit is reached.
* Record blocked activation attempts in the logs.
* Automatically reset the counter at the beginning of each calendar month.

The maximum number of monthly activations should also be configurable.

---

# 5. Relay 3 — Future Pest Detection System

**Important: I currently do NOT have the PiCamera 3 code or the pest image-recognition model.**

Do not assume that these components already exist.

Instead, design the application with a dedicated interface/module for future pest detection.

The future system should work approximately as follows:

1. Raspberry Pi Camera Module 3 captures an image or video frame.
2. An image-recognition model analyzes the image.
3. The model determines whether a pest is present.
4. If a pest is detected:

   * Record the detection.
   * Store the timestamp.
   * Store the detected pest class, if available.
   * Store the confidence score, if available.
   * Display the detection on the dashboard.
   * Display the captured image if practical.
   * Activate Relay 3.
   * Turn Relay 3 OFF after the configured activation duration.
   * Record the event in the logs.

### For the initial implementation

Since the camera and model are not available yet:

* Create the appropriate software interface/module for pest detection.
* Use a **mock/simulated pest detector** during development and testing.
* Allow the mock detector to simulate:

  * No pest detected
  * Pest detected
  * Pest class
  * Confidence score
* Make it possible to replace the mock detector with the actual PiCamera 3 + AI model later.
* Do not tightly couple the rest of the application to a specific AI model.

When I later provide the camera and model requirements, the pest-detection module should be replaceable without requiring major changes to the dashboard, database, relay controller, or automation system.

The following should eventually be configurable:

* Detection confidence threshold
* Camera capture interval
* Relay 3 activation duration
* Pest classes
* Other model-specific settings

---

# 6. Dashboard

Create a clean and responsive web dashboard that can be accessed from a computer, tablet, or phone connected to the Raspberry Pi's network.

The dashboard should display:

### NPK

* Nitrogen (N)
* Phosphorus (P)
* Potassium (K)
* Configured thresholds
* Normal/below-threshold status
* Manual Relay 1 activation button
* Last reading timestamp

### Soil Moisture

* Current moisture value
* Configured threshold
* Normal/below-threshold status
* Relay 2 status
* Relay 2 activation count for the current month
* Remaining Relay 2 activations
* Last watering event

### Relays

Display:

* Relay 1: ON/OFF
* Relay 2: ON/OFF
* Relay 3: ON/OFF
* Activation duration
* Current activation state
* Last activation time

### Pest Detection

For now, clearly indicate:

**Pest Detection: Not configured / Camera not installed**

Once the camera and model are implemented, this section should display:

* Pest detected/not detected
* Pest type/class
* Confidence score
* Last detection timestamp
* Last captured image
* Camera status
* AI model status

### System Status

Display:

* Raspberry Pi/system status
* Sensor connection status
* Camera status
* Pest detection model status
* Database status
* Last successful sensor readings
* Application uptime
* Any current errors

---

# 7. Alerts

The dashboard should clearly display alerts for:

* NPK below threshold
* Soil moisture below threshold
* Relay activation
* Relay 2 monthly limit reached
* Pest detected
* Sensor failure
* Camera failure
* AI model failure
* Database errors
* Other system errors

Pest-related alerts should remain inactive/disabled until the camera and model are configured.

---

# 8. Logging and Database

Use a lightweight local database such as **SQLite**.

Store relevant events including:

* NPK readings
* Soil moisture readings
* Threshold violations
* Relay activations
* Manual Relay 1 activations
* Automatic Relay 2 activations
* Blocked Relay 2 activations
* Pest detections
* System errors
* Sensor errors
* Camera errors
* Configuration changes

For pest detections, prepare the database schema for information such as:

* Timestamp
* Pest class
* Confidence
* Image path
* Model used

The database should be designed now even though the camera/model will be implemented later.

Avoid unnecessarily storing extremely frequent sensor readings. Reading/logging intervals should be configurable.

---

# 9. Configuration

The system must be configurable without requiring source-code modifications.

Use a configuration file and/or dashboard settings page.

At minimum, make the following configurable:

### NPK

* Nitrogen threshold
* Phosphorus threshold
* Potassium threshold
* Sensor reading interval

### Soil Moisture

* Moisture threshold
* Reading interval
* Relay 2 activation duration
* Maximum Relay 2 activations per month

### Relays

* GPIO pin assignments
* Active-high/active-low configuration
* Relay activation durations

### Future Pest Detection

* Camera capture interval
* Pest detection confidence threshold
* Relay 3 activation duration
* Model settings
* Pest classes

Configuration should be validated before being applied.

---

# 10. Modular Architecture

Do **not** create one large Python script containing the entire application.

Use a modular architecture.

A possible structure is:

```text
veggiecare/
├── app.py
├── config/
│   └── config.yaml
├── sensors/
│   ├── npk.py
│   └── soil_moisture.py
├── hardware/
│   └── relay_controller.py
├── pest_detection/
│   ├── detector.py
│   └── mock_detector.py
├── camera/
│   └── camera.py
├── automation/
│   └── controller.py
├── database/
│   └── database.py
├── dashboard/
│   ├── routes.py
│   ├── templates/
│   └── static/
├── logs/
├── tests/
└── requirements.txt
```

This is only a suggested structure. Modify it if you have a better architecture.

The important requirement is that:

* Sensors are independent.
* Relay control is independent.
* Automation logic is independent.
* Dashboard is independent.
* Database/logging is independent.
* Pest detection is independent.
* Camera integration is independent.

---

# 11. Pest Detection Interface

Create a general interface for the future pest detector.

For example, the rest of the application should be able to request something conceptually like:

```text
detect(image) → detection result
```

The result could contain:

```text
detected: true/false
class: "aphid"
confidence: 0.91
image: "path/to/image.jpg"
timestamp: ...
```

The actual implementation can initially be a mock detector.

Later, I should be able to replace the mock detector with an actual machine-learning model without changing the relay-control logic or dashboard architecture.

Do not select or implement a specific pest-detection AI model yet unless you recommend suitable options separately.

---

# 12. Safety and Reliability

Because the system controls physical hardware, include appropriate safety mechanisms.

The system must:

* Start all relays in the OFF state.
* Never accidentally activate a relay during startup.
* Automatically turn relays OFF after their configured activation duration.
* Prevent Relay 2 from exceeding its monthly activation limit.
* Handle sensor failures gracefully.
* Handle missing camera hardware gracefully.
* Handle missing AI model gracefully.
* Handle camera errors gracefully.
* Handle database errors gracefully.
* Log errors.
* Prevent the dashboard from freezing while sensor/model operations are running.
* Use background tasks, threads, processes, or asynchronous operations where appropriate.
* Recover from temporary hardware failures where possible.
* Provide a safe way to disable outputs.

The absence of the camera or pest model must **not prevent the NPK, soil-moisture, relay, database, and dashboard components from operating**.

---

# 13. Testing

Because the camera and AI model are not available yet, implement testing in stages.

### Stage 1 — Existing Hardware

Test:

* NPK sensor
* Soil moisture sensor
* Relay control

Use my existing tested Python scripts as the starting point.

### Stage 2 — Integrated System

Test:

* NPK → dashboard
* Low NPK → dashboard warning
* Manual Relay 1 activation
* Soil moisture → dashboard
* Low soil moisture → Relay 2
* Relay 2 monthly activation limit
* Database logging

### Stage 3 — Simulated Pest Detection

Use a mock detector to simulate:

```text
No pest
Pest detected
Pest detected with confidence score
Different pest classes
```

Verify that simulated pest detection correctly updates the dashboard and activates Relay 3.

### Stage 4 — Future Camera/AI Integration

When I obtain the PiCamera 3 and pest-detection model, integrate them into the existing pest-detection interface.

Do not redesign the entire application at that point.

---

# 14. Deployment

The final system should be capable of running automatically on Raspberry Pi OS.

Provide instructions for:

1. Creating the Python environment.
2. Installing dependencies.
3. Configuring the hardware.
4. Configuring GPIO pins.
5. Configuring the application.
6. Creating the SQLite database.
7. Starting the application.
8. Running the application as a systemd service.
9. Automatically starting VeggieCare after Raspberry Pi reboot.
10. Viewing application logs.
11. Troubleshooting common problems.

---

# 15. Development Process

Before writing the final implementation:

### Step 1 — Ask Questions

Ask me for the existing Python code for:

* NPK sensor
* Soil moisture sensor
* Relay control

Also ask for:

* NPK sensor model
* Soil moisture sensor model
* Relay module model
* GPIO pin assignments
* Whether the relay module is active-high or active-low
* Raspberry Pi OS version
* Any existing project files

Do not assume hardware details that I have not provided.

### Step 2 — Analyze Existing Code

Review the existing scripts and explain:

* What each script does.
* What libraries it uses.
* Which GPIO pins it uses.
* How sensor readings are obtained.
* How relays are controlled.
* Any potential conflicts between the scripts.

### Step 3 — Design

Before implementing, propose:

* System architecture
* Directory structure
* Database structure
* Configuration structure
* Hardware abstraction
* Dashboard architecture
* Future pest-detection interface

### Step 4 — Implement

Integrate the existing working code into the modular architecture.

Implement:

* NPK monitoring
* Soil moisture monitoring
* Relay control
* Automation logic
* Relay 2 monthly limit
* Dashboard
* Database
* Logging
* Configuration
* Mock pest detector

### Step 5 — Test

Provide tests for each component and the complete system.

### Step 6 — Future Camera Integration

Once I have the PiCamera 3 and pest-detection model, help me implement and integrate them into the existing pest-detection interface.

---

# Important Constraints

1. **Do not assume that the PiCamera 3 or pest-detection model currently exists.**
2. **Do not make the camera/model a dependency for the initial system.**
3. Use a mock pest detector until the actual model is available.
4. Do not unnecessarily rewrite my already-tested NPK, soil-moisture, and relay Python code.
5. Do not assume GPIO pins or relay polarity.
6. Ask clarifying questions before making hardware-specific assumptions.
7. Keep the system modular so future hardware and AI components can be added easily.
8. The system should remain fully functional even when the future camera/pest-detection components are unavailable.
9. Prioritize reliability and safe relay operation because the software controls physical hardware.
10. Once you have the required information and existing code, provide a complete, runnable implementation rather than only pseudocode.
