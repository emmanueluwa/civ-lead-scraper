"""
hubspot integration
push civil engineering leads as contact and company records
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactInput
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyInput
from hubspot.crm.associations.v4 import AssociationSpec
from hubspot.crm.contacts.exceptions import ApiException as ContactApiException
from hubspot.crm.companies.exceptions import ApiException as CompanyApiException

logger = logging.getLogger(__name__)

# HubSpot API rate limit — 110 requests per 10 seconds
# conservative delay
REQUEST_DELAY = 0.15  # seconds between requests


@dataclass
class LeadRecord:
    """represents fully enriched lead ready to push to hubspot"""

    # company data
    company_name: str
    address: str
    website: Optional[str]
    phone: str
    city: str
    state: str

    # contact data
    contact_first_name: Optional[str] = None
    contact_last_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_title: Optional[str] = None

    # metadata
    source: str = "google_places"
    search_query: str = ""


class HubSpotClient:
    def __init__(self):
        api_key = os.environ.get("HUBSPOT_API_KEY")
        if not api_key:
            raise ValueError("HUBSPOT_API_KEY not set in environment")

        self.client = hubspot.Client.create(access_token=api_key)

    def push_lead(self, lead: LeadRecord) -> bool:
        """
        push a lead to hubspot as a company and contact record
        """
        try:
            company_id = self._create_or_update_company(lead)
            if not company_id:
                return False

            time.sleep(REQUEST_DELAY)

            contact_id = self._create_contact(lead)

            if contact_id and company_id:
                time.sleep(REQUEST_DELAY)
                self._associate_contact_with_company(contact_id, company_id)

            return True

        except Exception as e:
            logger.error(
                f"Failed to push lead {lead.company_name}: {type(e).__name__}: {e}"
            )

            return False

    def _create_or_update_company(self, lead: LeadRecord) -> Optional[str]:
        """create a company record in hubspot"""
        try:
            properties = {
                "name": lead.company_name,
                "phone": lead.phone,
                "address": lead.address,
                "city": lead.city,
                "state": lead.state,
                "industry": "CONSTRUCTION",
                "description": f"Civil engineering firm — sourced via {lead.source}",
            }

            if lead.website:
                properties["website"] = lead.website
                domain = (
                    lead.website.replace("https://", "")
                    .replace("http://", "")
                    .split("/")[0]
                )
                properties["domain"] = domain

            company_input = CompanyInput(properties=properties)
            response = self.client.crm.companies.basic_api.create(
                simple_public_object_input_for_create=company_input
            )

            logger.info(f"created company: {lead.company_name} (ID: {response.id})")

            return response.id

        except CompanyApiException as e:
            logger.error(
                f"hubspot company creation failed for {lead.company_name}: {e}"
            )

            return None

    def _create_contact(self, lead: LeadRecord) -> Optional[str]:
        """create a contact record in hubspot"""
        try:
            properties = {
                "phone": lead.phone,
                "company": lead.company_name,
                "hs_lead_status": "NEW",
                "lifecyclestage": "lead",
            }

            if lead.contact_first_name:
                properties["firstname"] = lead.contact_first_name
            if lead.contact_last_name:
                properties["lastname"] = lead.contact_last_name
            if lead.contact_email:
                properties["email"] = lead.contact_email
            if lead.contact_title:
                properties["jobtitle"] = lead.contact_title

            # if no named contact use company name as placeholder
            if not lead.contact_first_name:
                properties["firstname"] = lead.company_name
                properties["lastname"] = "(Civil Engineering)"

            contact_input = ContactInput(properties=properties)
            response = self.client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=contact_input
            )

            logger.info(f"created contact for: {lead.company_name} (ID: {response.id})")

            return response.id

        except ContactApiException as e:
            # 409 for contact already exists, not a failure
            if e.status == 409:
                logger.info(f"contact already exists for {lead.company_name}")

                return None

            logger.error(f"hubspot contact created failed for {lead.company_name}: {e}")

            return None

    def _associate_contact_with_company(self, contact_id: str, company_id: str) -> None:
        """associate a contact record with a company record"""
        try:
            self.client.crm.associations.v4.basic_api.create(
                object_type="contacts",
                object_id=contact_id,
                to_object_type="companies",
                to_object_id=company_id,
                association_spec=[
                    AssociationSpec(
                        association_category="HUBSPOT_DEFINED",
                        association_type_id=1,
                    )
                ],
            )
        except Exception as e:
            logger.warning(
                f"failed to associate contact {contact_id} "
                f"with company {company_id}: {e}"
            )
