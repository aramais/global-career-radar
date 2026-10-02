"""Personal applications, referrals and next-action tracking."""

from job_intake.crm.repository import (
    CRMRepository,
    serialize_application,
    serialize_company,
    serialize_contact,
)
from job_intake.crm.schemas import CRMConflictError, CRMValidationError

__all__ = [
    "CRMRepository",
    "CRMConflictError",
    "CRMValidationError",
    "serialize_application",
    "serialize_company",
    "serialize_contact",
]
