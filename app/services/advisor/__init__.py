"""Farm-advice flows: irrigation and water loss, soil health, fertilizer,
water sources, weed control, seed variety choice and germination checks.

N-ATLaS collects the farmer's facts and phrases the answer in their
language; the numbers come from app/agri.
"""

from app.services.advisor.specs import ADVISOR_FLOWS, FLOWS

__all__ = ["ADVISOR_FLOWS", "FLOWS"]
