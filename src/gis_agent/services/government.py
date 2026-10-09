from typing import Dict

class GovernmentSchemes:
    """Class to provide information about Pakistani government agricultural schemes"""
    
    SCHEMES = {
        "Kissan Package": {
            "description": "Subsidies on fertilizers, seeds, and agricultural machinery",
            "eligibility": "All registered farmers",
            "benefits": "Up to 50% subsidy on inputs",
            "urdu": "کسان پیکیج: کھاد، بیج اور زرعی مشینری پر سبسڈی"
        },
        "Tubewell Subsidy": {
            "description": "Financial assistance for installing tubewells",
            "eligibility": "Farmers with at least 5 acres of land",
            "benefits": "50% subsidy up to Rs. 100,000",
            "urdu": "ٹیوب ویل سبسڈی: نئے ٹیوب ویل لگانے کے لیے مالی معاونت"
        },
        "Solar Pump Scheme": {
            "description": "Subsidy for solar-powered water pumps",
            "eligibility": "Farmers in water-scarce areas",
            "benefits": "60% subsidy on solar pumps",
            "urdu": "سولر پمپ اسکیم: شمسی توانائی سے چلنے والے پمپوں پر سبسڈی"
        }
    }
    
    @staticmethod
    def get_scheme_info(scheme_name: str = None, language: str = "english") -> Dict:
        """Get information about government schemes"""
        if scheme_name:
            scheme = GovernmentSchemes.SCHEMES.get(scheme_name)
            if not scheme:
                return {"error": "Scheme not found"}
            
            if language.lower() == "urdu":
                return {
                    "name": scheme_name,
                    "description": scheme["urdu"],
                    "eligibility": scheme.get("eligibility_urdu", scheme["eligibility"]),
                    "benefits": scheme.get("benefits_urdu", scheme["benefits"])
                }
            return {
                "name": scheme_name,
                "description": scheme["description"],
                "eligibility": scheme["eligibility"],
                "benefits": scheme["benefits"]
            }
        
        # Return all schemes if no specific scheme requested
        if language.lower() == "urdu":
            return {name: {
                "description": scheme["urdu"],
                "eligibility": scheme.get("eligibility_urdu", scheme["eligibility"]),
                "benefits": scheme.get("benefits_urdu", scheme["benefits"])
            } for name, scheme in GovernmentSchemes.SCHEMES.items()}
        
        return GovernmentSchemes.SCHEMES