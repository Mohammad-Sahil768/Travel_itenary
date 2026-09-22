"""Three ready-to-load sample scenarios for the sidebar.

Stops are given as real addresses/place names — they're geocoded on demand
(see travel_agent.geocoding) rather than shipping pre-baked coordinates, so
loading a scenario exercises the same real-data path as manually typed stops.
Street addresses are used wherever a landmark's informal name doesn't
reliably resolve on the free OSM geocoder.
"""

from __future__ import annotations

from typing import Dict, List

from .models import Stop

SCENARIOS: Dict[str, Dict] = {
    "Urban Delivery (5 stops)": {
        "start_time": "08:00",
        "total_budget_minutes": 540,  # 9 hours
        "stops": [
            Stop("Statue of Liberty", "Statue of Liberty, New York, NY", earliest="08:00", latest="10:30", duration_minutes=90),
            Stop("9/11 Memorial", "180 Greenwich St, New York, NY", earliest="09:30", latest="12:00", duration_minutes=60),
            Stop("Times Square", "Times Square, New York, NY", earliest="11:00", latest="14:00", duration_minutes=45),
            Stop("Central Park", "Central Park, New York, NY", earliest="13:00", latest="16:00", duration_minutes=75),
            Stop("Top of the Rock", "30 Rockefeller Plaza, New York, NY", earliest="15:00", latest="17:00", duration_minutes=60),
        ],
    },
    "Regional Logistics (8 stops)": {
        "start_time": "10:00",
        "total_budget_minutes": 480,  # 8 hours
        "stops": [
            Stop("Newark Museum", "49 Washington St, Newark, NJ", earliest="10:00", latest="12:00", duration_minutes=45),
            Stop("Branch Brook Park", "Branch Brook Park, Newark, NJ", earliest="10:30", latest="13:00", duration_minutes=40),
            Stop("Liberty State Park", "Liberty State Park, Jersey City, NJ", earliest="11:00", latest="14:00", duration_minutes=50),
            Stop("Journal Square", "Journal Square, Jersey City, NJ", earliest="12:00", latest="15:00", duration_minutes=30),
            Stop("Hoboken Waterfront", "Hoboken, NJ", earliest="12:30", latest="15:30", duration_minutes=40),
            Stop("The High Line", "The High Line, New York, NY", earliest="13:30", latest="16:30", duration_minutes=45),
            Stop("Chelsea Market", "Chelsea Market, New York, NY", earliest="14:30", latest="17:00", duration_minutes=40),
            Stop("Empire State Building", "Empire State Building, New York, NY", earliest="15:30", latest="18:00", duration_minutes=60),
        ],
    },
    "Same-day Service (6 stops)": {
        "start_time": "09:00",
        "total_budget_minutes": 480,  # 8 hours
        "stops": [
            Stop("Downtown Delivery", "233 Broadway, New York, NY", earliest="09:00", latest="11:00", duration_minutes=20),
            Stop("Passport Pickup", "421 8th Ave, New York, NY", earliest="09:30", latest="12:00", duration_minutes=30),
            Stop("Bank Deposit", "1 Wall St, New York, NY", earliest="10:00", latest="13:00", duration_minutes=15),
            Stop("Client Meeting", "350 5th Ave, New York, NY", earliest="11:00", latest="14:00", duration_minutes=45),
            Stop("Warehouse Pickup", "225 Liberty St, New York, NY", earliest="12:00", latest="15:00", duration_minutes=25),
            Stop("Courier Drop-off", "89 Chambers St, New York, NY", earliest="13:00", latest="16:00", duration_minutes=15),
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
