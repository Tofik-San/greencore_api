from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Plant(Base):
    __tablename__ = "plants"

    id = Column(Integer, primary_key=True, index=True)
    view = Column(String)
    family = Column(String)
    cultivar = Column(String)
    insights = Column(String)
    light = Column(String)
    watering = Column(String)
    temperature = Column(String)
    soil = Column(String)
    fertilizer = Column(String)
    pruning = Column(String)
    pests_diseases = Column(String)
    indoor = Column(Boolean)
    outdoor = Column(Boolean)
    beginner_friendly = Column(Boolean)
    toxicity = Column(String)
    ru_regions = Column(String)
    cultivar_status = Column(String)
    filter_light = Column(String)
    filter_category = Column(String)
    filter_temperature = Column(String)
    filter_toxicity = Column(String)
    filter_zone_usda = Column(String)
