from pydantic import BaseModel

class MAR(BaseModel):
    District: str
    Region: str
    School: str
    
    Residential: str
    Bedrooms: str
    Tenure: str
    
    PriceRangeMin: int
    PriceRangeMax: int
    
    PricePSFMin: int
    PricePSFMax: int
    
    TOPLower: str = "Any"
    TOPUpper: str = "Any"
    
    SizeLower: str = "Any"
    SizeUpper: str = "Any"
    
    MRTNearby: str = "Any"
    MRTSize: str = "Both"
    ShowLinks: bool = True

class IntelligenceReport(BaseModel):
    District: str
    Region: str
    School: str
    
    Residential: str
    Bedrooms: str
    Tenure: str
    
    PriceRangeMin: int
    PriceRangeMax: int
    
    PricePSFMin: int
    PricePSFMax: int
    
    TOPLower: str = "Any"
    TOPUpper: str = "Any"
    
    SizeLower: str = "Any"
    SizeUpper: str = "Any"
    
    MRTNearby: str = "Any"
    MRTSize: str = "Both"
    ShowLinks: bool = True

class DualKeyProjects(BaseModel):
    District: str
    
    Residential: str
    Bedrooms: str
    Tenure: str
    
    PriceRangeMin: int
    PriceRangeMax: int
    
    TOPLower: str = "Any"
    TOPUpper: str = "Any"
    
    TOPSizeLower: str = "Any"
    TOPSizeUpper: str = "Any"
    
    MRTNearby: str = "Any"

class UpcomingProjects(BaseModel):
    Name: str
    District: str
    
class ProjectComparison(BaseModel):
    Name:str

class ResidentialInvestmentIndex(BaseModel):
    Name: str

class ObjectionHandling(BaseModel):
    pass
