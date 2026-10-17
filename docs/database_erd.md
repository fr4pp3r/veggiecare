# VeggieCare — Database ERD

Entity-relationship diagram of the SQLite database (WAL mode). There are no
foreign-key constraints; relationships shown are logical (e.g. tank doses are
recorded against a prior tank refill for the same tank).

![Database ERD](database_erd.png)

```mermaid
erDiagram
    NPK_READINGS {
        int id PK
        string timestamp
        float nitrogen
        float phosphorus
        float potassium
        int nitrogen_below
        int phosphorus_below
        int potassium_below
    }
    MOISTURE_READINGS {
        int id PK
        string timestamp
        float moisture
        int below_threshold
    }
    RELAY_ACTIVATIONS {
        int id PK
        string timestamp
        int relay_id
        string relay_name
        string trigger_type
        int duration_seconds
        string source
    }
    BLOCKED_ACTIVATIONS {
        int id PK
        string timestamp
        int relay_id
        string reason
    }
    PEST_DETECTIONS {
        int id PK
        string timestamp
        int detected
        string pest_class
        float confidence
        string image_path
        string model
        string camera
        string suggestion
    }
    NPK_RECOMMENDATIONS {
        int id PK
        string timestamp
        float nitrogen
        float phosphorus
        float potassium
        string fertilizer_id
        string fertilizer_name
        float dosage_g_per_10L
        string deficits
        string reasoning
        string priority
        string model
    }
    SYSTEM_EVENTS {
        int id PK
        string timestamp
        string level
        string source
        string message
    }
    CONFIG_CHANGES {
        int id PK
        string timestamp
        string key
        string old_value
        string new_value
        string source
    }
    TANK_REFILLS {
        int id PK
        string timestamp
        string tank
        string tank_type
        float npk_n
        float npk_p
        float npk_k
        float total_ml
        float ml_per_dose_or_cycle
        int max_doses
        string mode
        float pump_lph
    }
    TANK_AUTO_DOSES {
        int id PK
        string timestamp
        string tank
        string tank_type
        float npk_n
        float npk_p
        float npk_k
        float ml_dispensed
        float activation_seconds
        int doses_used_before
        int doses_used_after
        float ml_remaining_est
        int combined_with_watering
        string mode
    }
    TANK_AUTO_DOSE_BLOCKED {
        int id PK
        string timestamp
        string tank
        string reason
        string cooldown_until_at
        string blocked_until_at
        int doses_used
        int max_doses
        float ml_remaining_est
    }
    TANK_AUTO_DOSE_DEFERRED {
        int id PK
        string timestamp
        string tank
        string reason
    }

    TANK_REFILLS ||--o{ TANK_AUTO_DOSES : "records doses for"
    TANK_REFILLS ||--o{ TANK_AUTO_DOSE_BLOCKED : "blocked attempts"
    TANK_REFILLS ||--o{ TANK_AUTO_DOSE_DEFERRED : "deferred doses"
```