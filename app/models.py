"""
Database models for Pasture Predictions backend.
All tables use SQLModel so they work as both Pydantic schemas and ORM models.
"""

from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field

# ── Farm ──────────────────────────────────────────────────────────────────────

class Farm(SQLModel, table=True):
    """
    A physical farm location with a street address.
    Animals belong to a farm via farm_id.
    When only one farm exists cows are auto-assigned to it.
    When a second farm is added the importer prompts the user to choose.
    """
    id:           Optional[int] = Field(default=None, primary_key=True)
    name:         str                          # display name, e.g. "Home Farm"
    street:       str                          # "185 Vaughan Dr"
    city:         str                          # "Charlottesville"
    state:        str                          # "VA"
    created_at:   datetime = Field(default_factory=datetime.utcnow)

    @property
    def full_address(self) -> str:
        return f"{self.street}, {self.city}, {self.state}"


class FarmCreate(SQLModel):
    name:   str
    street: str
    city:   str
    state:  str


# ── Animals ───────────────────────────────────────────────────────────────────

class Animal(SQLModel, table=True):
    """Individual animal record matching the Animal Cards tab."""
    id:          Optional[int] = Field(default=None, primary_key=True)
    name:        str
    animal_type: str = "Cattle"
    tag_number:  Optional[str] = None
    status:      str = "Active"   # Active | Sick | Sold | Deceased | Weaning

    # Farm & paddock assignment
    farm_id:     Optional[int] = Field(default=None, foreign_key="farm.id")
    paddock_id:  Optional[int] = Field(default=None, foreign_key="paddock.id")

    # Physical
    sex:         Optional[str]   = None   # Male | Female
    breed:       Optional[str]   = None
    coloring:    Optional[str]   = None
    weight_lb:   Optional[float] = None
    height_in:   Optional[float] = None
    framescore:  Optional[float] = None
    bcs:         Optional[float] = None   # Body Condition Score 1-5
    last_measured: Optional[datetime] = None

    # Birth
    age_yr:      Optional[float]    = None
    birth_date:  Optional[datetime] = None
    dam:         Optional[str]      = None   # mother
    sire:        Optional[str]      = None   # father
    offspring:   Optional[str]      = None
    notes:       Optional[str]      = None

    created_at:  datetime = Field(default_factory=datetime.utcnow)
    updated_at:  datetime = Field(default_factory=datetime.utcnow)


# ── Breeds ────────────────────────────────────────────────────────────────────

class Breed(SQLModel, table=True):
    """USDA breed averages — used for Breed Analysis charts."""
    id:            Optional[int]   = Field(default=None, primary_key=True)
    name:          str             # Angus, Hereford, etc.
    breed_type:    str = "Beef"    # Beef | Dairy | Dual-purpose
    avg_weight_kg: Optional[float] = None
    daily_feed_kg: Optional[float] = None   # dry matter
    milk_l_day:    Optional[float] = None
    adg_kg_day:    Optional[float] = None   # average daily gain (calves)
    stocking_h_ac: Optional[float] = None   # head per acre
    us_prevalence: Optional[float] = None   # % of US herd
    notes:         Optional[str]   = None


# ── Paddocks ──────────────────────────────────────────────────────────────────

class Paddock(SQLModel, table=True):
    """A grazeable paddock / field section."""
    id:             Optional[int]   = Field(default=None, primary_key=True)
    name:           str
    acres:          Optional[float] = None
    lat:            Optional[float] = None   # centroid lat for weather lookup
    lon:            Optional[float] = None   # centroid lon
    status:         str = "ready"            # ready | grazing | resting | hay
    assigned_breed: Optional[str]   = None
    polygon_json:   Optional[str]   = None   # "[[x,y],[x,y],...]"
    notes:          Optional[str]   = None
    created_at:     datetime = Field(default_factory=datetime.utcnow)


# ── Sensor Readings ───────────────────────────────────────────────────────────

class SensorReading(SQLModel, table=True):
    """One timestamped reading from a field sensor."""
    id:              Optional[int]   = Field(default=None, primary_key=True)
    timestamp:       datetime = Field(default_factory=datetime.utcnow)
    paddock_id:      Optional[int]   = Field(default=None, foreign_key="paddock.id")
    paddock_name:    Optional[str]   = None
    grass_height_cm: Optional[float] = None
    soil_moisture:   Optional[float] = None
    soil_temp_c:     Optional[float] = None
    air_temp_c:      Optional[float] = None
    rainfall_mm:     Optional[float] = None
    source:          str = "manual"          # manual | sensor_api | simulated
    sensor_id:       Optional[str]   = None


# ── Weather ───────────────────────────────────────────────────────────────────

class WeatherRecord(SQLModel, table=True):
    """Weather row — either historical (polled hourly) or forecast (7-day)."""
    id:               Optional[int]   = Field(default=None, primary_key=True)
    timestamp:        datetime = Field(default_factory=datetime.utcnow)
    record_date:      str              # YYYY-MM-DD
    is_forecast:      bool = False
    lat:              Optional[float] = None
    lon:              Optional[float] = None
    temperature_c:    float
    precipitation_mm: float = 0.0
    wind_ms:          Optional[float] = None
    humidity_pct:     Optional[float] = None
    description:      Optional[str]   = None
    source:           str = "openweather"


# ── Grass Growth Predictions ──────────────────────────────────────────────────

class GrassPrediction(SQLModel, table=True):
    """Stored output of the grass growth model for a paddock."""
    id:                Optional[int]   = Field(default=None, primary_key=True)
    created_at:        datetime = Field(default_factory=datetime.utcnow)
    paddock_id:        Optional[int]   = Field(default=None, foreign_key="paddock.id")
    paddock_name:      Optional[str]   = None
    current_height_cm: float
    soil_moisture:     float
    temperature_c:     float
    day1_cm:  float
    day2_cm:  float
    day3_cm:  float
    day4_cm:  float
    day5_cm:  float
    day6_cm:  float
    day7_cm:  float
    days_to_ready: Optional[int] = None
    pgr_cm_day:    float = 0.0


# ── Pydantic-only schemas (for API request bodies) ────────────────────────────

class SensorIngestPayload(SQLModel):
    paddock_name:    str
    grass_height_cm: Optional[float] = None
    soil_moisture:   Optional[float] = None
    soil_temp_c:     Optional[float] = None
    air_temp_c:      Optional[float] = None
    rainfall_mm:     Optional[float] = None
    sensor_id:       Optional[str]   = None
    source:          str = "sensor_api"


class PaddockCreate(SQLModel):
    name:           str
    acres:          Optional[float] = None
    lat:            Optional[float] = None
    lon:            Optional[float] = None
    status:         str = "ready"
    assigned_breed: Optional[str] = None
    notes:          Optional[str] = None


class AnimalCreate(SQLModel):
    name:        str
    animal_type: str = "Cattle"
    tag_number:  Optional[str]   = None
    status:      str = "Active"
    farm_id:     Optional[int]   = None
    paddock_id:  Optional[int]   = None
    sex:         Optional[str]   = None
    breed:       Optional[str]   = None
    coloring:    Optional[str]   = None
    weight_lb:   Optional[float] = None
    height_in:   Optional[float] = None
    framescore:  Optional[float] = None
    bcs:         Optional[float] = None
    birth_date:  Optional[datetime] = None
    dam:         Optional[str]   = None
    sire:        Optional[str]   = None
    offspring:   Optional[str]   = None
    notes:       Optional[str]   = None