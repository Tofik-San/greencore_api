from sqlalchemy import Column, Integer, String
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
    indoor = Column(String)
    outdoor = Column(String)
    beginner_friendly = Column(String)
    toxicity = Column(String)
    ru_regions = Column(String)
