"""Three ready-to-load sample scenarios for the sidebar."""

from __future__ import annotations

from typing import Dict, List

from .models import Stop

SCENARIOS: Dict[str, Dict] = {
    "Urban - NYC (5 stops, 8am-5pm)": {
        "start_time": "08:00",
        "total_budget_minutes": 540,  # 9 hours
        "stops": [
            Stop("Statue of Liberty", 40.6892, -74.0445, "08:00", "10:30", 90),
            Stop("9/11 Memorial", 40.7115, -74.0134, "09:30", "12:00", 60),
            Stop("Times Square", 40.7580, -73.9855, "11:00", "14:00", 45),
            Stop("Central Park (The Mall)", 40.7712, -73.9740, "13:00", "16:00", 75),
            Stop("Top of the Rock", 40.7590, -73.9787, "15:00", "17:00", 60),
        ],
    },
    "Regional - 3 Cities (8 stops, 10am-6pm)": {
        "start_time": "10:00",
        "total_budget_minutes": 480,  # 8 hours
        "stops": [
            Stop("Newark Museum of Art", 40.7423, -74.1719, "10:00", "12:00", 45),
            Stop("Branch Brook Park", 40.7645, -74.1837, "10:30", "13:00", 40),
            Stop("Liberty State Park", 40.7093, -74.0555, "11:00", "14:00", 50),
            Stop("Journal Square", 40.7333, -74.0629, "12:00", "15:00", 30),
            Stop("Hoboken Waterfront", 40.7440, -74.0324, "12:30", "15:30", 40),
            Stop("The High Line (NYC)", 40.7480, -74.0048, "13:30", "16:30", 45),
            Stop("Chelsea Market", 40.7424, -74.0060, "14:30", "17:00", 40),
            Stop("Empire State Building", 40.7484, -73.9857, "15:30", "18:00", 60),
        ],
    },
    "Urgent - Same-Day Errands (3 stops, all by 2pm)": {
        "start_time": "09:00",
        "total_budget_minutes": 300,  # 5 hours
        "stops": [
            Stop("Downtown Bank Branch", 40.7074, -74.0113, "09:00", "11:00", 20),
            Stop("City Passport Office", 40.7484, -73.9967, "09:30", "13:00", 45),
            Stop("Courier Pickup Depot", 40.7306, -73.9866, "10:00", "14:00", 15),
        ],
    },
}


def get_scenario_names() -> List[str]:
    return list(SCENARIOS.keys())


def load_scenario(name: str) -> Dict:
    scenario = SCENARIOS[name]
    return {
        "start_time": scenario["start_time"],
        "total_budget_minutes": scenario["total_budget_minutes"],
        "stops": [Stop(**vars(s)) for s in scenario["stops"]],
    }
