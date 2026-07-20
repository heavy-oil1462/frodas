"""frodas project declaration.

Single source of truth for the names the shared esphome-skills tools need:
topic root, node names, compositions, sim injections and presets. Injection
keys are the sim/<key> topics of esphome/packages/sim-sensors.yaml
(validate's sim check enforces the match).
"""

from pathlib import Path

from esphome_skills import Project

PROJECT = Project(
    name="frodas",
    device="greenhouse node",
    mqtt_root="frodas",
    sim_node="frodas-sim",
    sim_yaml="sim-greenhouse.yaml",
    compositions=("example-greenhouse.yaml", "sim-greenhouse.yaml"),
    injections={
        "temperature": ("Greenhouse Temperature", "°C", -15.0, 55.0, 0.5, 21.0),
        "humidity": ("Greenhouse Humidity", "%", 0.0, 100.0, 1.0, 65.0),
        "soil": ("Soil Moisture", "%", 0.0, 100.0, 1.0, 50.0),
        "battery": ("Battery Voltage", "V", 10.0, 14.6, 0.05, 13.2),
        "illuminance": ("Greenhouse Illuminance", "lx", 0.0, 100000.0, 500.0,
                        20000.0),
        "solar_current": ("Solar Current", "A", 0.0, 3.2, 0.05, 1.0),
    },
    presets={
        "Sunny noon": {"time": "12:30", "temperature": 32, "humidity": 55,
                       "soil": 45, "battery": 13.4, "illuminance": 65000,
                       "solar_current": 2.4},
        "Dry morning (watering window)": {"time": "06:30", "temperature": 18,
                                          "humidity": 70, "soil": 20,
                                          "battery": 13.1, "illuminance": 8000,
                                          "solar_current": 0.8},
        "Hot & humid (vent opens)": {"time": "14:00", "temperature": 33,
                                     "humidity": 88, "soil": 55,
                                     "battery": 13.3, "illuminance": 70000,
                                     "solar_current": 2.0},
        "Cold night": {"time": "03:00", "temperature": 1, "humidity": 90,
                       "soil": 50, "battery": 12.9, "illuminance": 0,
                       "solar_current": 0},
        "Low battery (tier 2)": {"battery": 12.3, "solar_current": 0.1},
        "Critical battery (tier 3)": {"battery": 11.8, "solar_current": 0},
    },
    mock_node="frodas-greenhouse",
    radio_switch="radio_always_on",
    repo_root=Path(__file__).resolve().parent.parent,
    python_dirs=("tools",),
)
