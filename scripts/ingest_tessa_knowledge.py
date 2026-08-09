"""
One-off / re-runnable script to ingest TESSA's approved IRD Grenada knowledge
into Pinecone.

This uses the same FAQ content already live on TESSA's own frontend (the
FAQ_DATA in the chat widget) - not placeholder data - so what TESSA retrieves
matches what's already been approved to show taxpayers. Extend KNOWLEDGE_RECORDS
below with rulebook sections, procedures, and other approved knowledge as it
becomes available.

Run from the project root with the venv active:

    python -m scripts.ingest_tessa_knowledge
"""

import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from services.pinecone_service import upsert_knowledge, init_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingest")

# Real, already-approved IRD Grenada FAQ content (mirrors TESSA's frontend
# FAQ_DATA). Each record needs a unique "_id" and a "content" string.
KNOWLEDGE_RECORDS = [
    {"_id": "faq1", "content": "Q: How do I register as an individual with the IRD? A: You can register online through the G-TAX portal at tax.gov.gd or in person at an IRD office using a valid government ID and proof of address."},
    {"_id": "faq2", "content": "Q: How do I register a business with the IRD? A: First obtain a Business Registration Certificate from CAIPO, then submit it along with your business details to the IRD online or in person."},
    {"_id": "faq3", "content": "Q: What forms do I need for individual registration? A: You must submit the IRD Individual Registration Form, or the IRD Individual Enterprise Registration Form if you are a sole trader."},
    {"_id": "faq4", "content": "Q: What forms do I need for business registration? A: You must submit the IRD Non-Individual Registration Form or Non-Individual Enterprise Registration Form."},
    {"_id": "faq5", "content": "Q: How do I get a Tax Identification Number (TIN)? A: You can request a TIN by applying online at tax.gov.gd or by handing in a paper registration form at any IRD office."},
    {"_id": "faq6", "content": "Q: Can I register multiple businesses under one TIN? A: Sole proprietors operate multiple trade names under one individual TIN, but incorporated companies must each have their own unique TIN."},
    {"_id": "faq7", "content": "Q: Do I need to register if I earn foreign income? A: Yes, tax residents in Grenada must register and declare foreign income earned overseas or remitted locally."},
    {"_id": "faq8", "content": "Q: How do I apply for an extension to file my tax return? A: Submit a formal written request explaining your reasons to the Comptroller of Inland Revenue before the official filing deadline."},
    {"_id": "faq9", "content": "Q: Do I need a Tax Clearance Certificate? A: You need a Tax Clearance Certificate for government tenders, property transfers, work permits, and certain business loans."},
    {"_id": "faq10", "content": "Q: Are there penalties for late registration or filing? A: Yes, late filings and overdue tax payments result in statutory penalties and monthly interest charges."},
    {"_id": "faq11", "content": "Q: How do I check my IRD account balance or status? A: Log in to your G-TAX account at tax.gov.gd or request an official Statement of Account directly from an IRD office."},
    {"_id": "faq12", "content": "Q: What documents should I keep for my records? A: Keep all financial records, invoices, receipts, bank statements, and tax notices for at least six years."},
    {"_id": "faq13", "content": "Q: How do I correct errors on my registration form? A: Correct your details online through your G-TAX account or submit supporting documents to IRD Customer Support to request an update."},
    {"_id": "faq14", "content": "Q: How do I know if my registration was successful? A: You will receive an official confirmation email or letter containing your new TIN once your account is active."},
    {"_id": "faq15", "content": "Q: Where is the IRD office located in St. George's? A: The main IRD office is located on Young Street, St. George's, Grenada."},
    {"_id": "faq16", "content": "Q: Can I submit my registration forms online? A: Yes, you can register, upload documents, file tax returns, and make payments online using the G-TAX / Tax e-Filing portal at tax.gov.gd."},
    {"_id": "faq17", "content": "Q: How do I contact the IRD by phone or email? A: You can contact the IRD Helpdesk by calling +1 (473) 440-3556 or +1 (473) 435-6945/46, or emailing helpdesk@ird.gov.gd."},
    {"_id": "faq18", "content": "Q: How long does registration processing take? A: Registration processing usually takes between 3 to 10 business days after all required documents are submitted."},
    {"_id": "faq19", "content": "Q: Can I update my mailing address? A: Yes, you can update your address directly in your G-TAX portal settings or by submitting an IRD Change of Mailing Address Form."},
    {"_id": "faq20", "content": "Q: Who can help me if I have trouble completing the forms? A: You can get assistance from IRD customer service officers at the main office, district revenue offices, or by phone and email support."},
    {"_id": "faq21", "content": "Q: What identification is required to apply for a TIN? A: You need a valid government-issued photo ID (such as a passport or driver's license) and proof of address."},
    {"_id": "faq22", "content": "Q: Is there a fee to register or receive a TIN with the IRD? A: No, registering with the Inland Revenue Department and receiving a Tax Identification Number is completely free."},
    {"_id": "faq23", "content": "Q: Can a non-resident or foreign national register with the IRD? A: Yes, non-residents who earn income or conduct business within Grenada can register for a TIN."},
    {"_id": "faq24", "content": "Q: What are the official opening hours for the IRD office? A: The main office is open Monday to Friday, 8:00 AM to 4:00 PM. The Cash Office closes earlier, at 3:00 PM."},
    {"_id": "faq25", "content": "Q: Do I need to schedule an appointment to visit the IRD office in person? A: No appointment is required for general inquiries, though booking ahead is recommended for complex tax consultations."},
    {"_id": "faq26", "content": "Q: Who do I contact if I am locked out of my e-Filing or GTAX account? A: You can contact the IRD Helpdesk by emailing helpdesk@ird.gov.gd or calling +1 (473) 440-3556."},
    {"_id": "faq27", "content": "Q: How do I reset my IRD online portal password? A: Click the \"Forgot Password\" link on the GTAX/e-Tax portal login page to receive a reset link via email."},
    {"_id": "faq28", "content": "Q: Is technical support available on weekends or public holidays? A: No, technical and general support is only available Monday through Friday during regular business hours."},
    {"_id": "faq29", "content": "Q: Does the IRD have official social media channels for updates? A: Yes, official updates and public announcements are posted on the IRD's Facebook page (GrenadaIRD) and Instagram account (@grenadainlandrevenue)."},
    {"_id": "faq30", "content": "Q: Can I request an in-person advisory meeting with a tax officer? A: Yes, you can request an advisory session by contacting the Client Relations Unit, calling the main IRD office, or using the Schedule Meeting tab in the app."},
    {"_id": "faq31", "content": "Q: Does the IRD handle motor vehicle licences and road taxes? A: Yes, motor vehicle license renewals, registration transfers, and road tax payments are processed through the IRD and District Revenue Offices."},
    {"_id": "faq32", "content": "Q: When are annual professional and business licence payments due? A: Annual licence fees must be paid at the beginning of each calendar year prior to conducting business operations."},
    {"_id": "faq33", "content": "Q: How do I claim a refund if I overpaid my taxes? A: You can claim a refund by submitting your annual tax return along with supporting documents showing excess tax payments or deductions."},
    {"_id": "faq34", "content": "Q: What happens if my tax return is selected for an IRD audit? A: The IRD will notify you in writing to request supporting financial records, receipts, and account statements to verify your filed figures."},
    {"_id": "faq35", "content": "Q: Is there a process to appeal an official tax assessment by the IRD? A: Yes, you can file a formal written objection with the Comptroller of Inland Revenue within specified statutory deadlines after receiving an assessment notice."},
    {"_id": "faq36", "content": "Q: What is a Tax Clearance Certificate and why might I need one? A: A Tax Clearance Certificate confirms you have no outstanding tax debts and is often required for government contracts, bank loans, or property transfers."},
    {"_id": "faq37", "content": "Q: How long is a Tax Clearance Certificate valid? A: A Tax Clearance Certificate is typically valid for three to six months from the date of issue."},
    {"_id": "faq38", "content": "Q: Can I obtain a Tax Clearance Certificate if I have unpaid tax arrears? A: You can only receive a certificate if you settle your balance in full or enter into an approved formal payment plan with the IRD."},
    {"_id": "faq39", "content": "Q: What is General Consumption Tax (GCT)? A: GCT is a tax applied to goods and services consumed in Grenada. It is generally collected by registered businesses and paid to the IRD."},
    {"_id": "faq40", "content": "Q: Who needs to register for GCT? A: Businesses that meet the required taxable-supply threshold must register for GCT. Contact the IRD to confirm whether your business qualifies."},
    {"_id": "faq41", "content": "Q: What is Property Tax? A: Property Tax applies to property ownership in Grenada. For account-specific balances or assessments, please contact the IRD directly."},
    {"_id": "faq42", "content": "Q: What is Stamp Tax? A: Stamp Tax applies to certain documents and transactions. Contact the IRD for guidance on which transactions require it."},
    {"_id": "faq43", "content": "Q: What happens if I don't file a tax return at all? A: Failing to file can lead to statutory penalties, accumulating interest, and possible enforcement action. It's always best to file, even late, rather than not at all."},
    {"_id": "faq44", "content": "Q: Can I file my tax return jointly with my spouse? A: Tax filing arrangements can vary by circumstance - contact the IRD or a tax professional to confirm the correct filing approach for your situation."},
    {"_id": "faq45", "content": "Q: Do pensioners need to pay income tax in Grenada? A: Pension income may be treated differently depending on its source and amount. Contact the IRD to confirm how your specific pension income is treated."},
    {"_id": "faq46", "content": "Q: What is the deadline for filing annual income tax returns? A: Annual filing deadlines are set by the IRD each year - check the official IRD website, Facebook page, or the Tax News tab for the current deadline."},
    {"_id": "faq47", "content": "Q: Can I pay my taxes using a debit or credit card? A: Accepted payment methods can vary by office and service - contact the IRD or check the G-TAX portal to confirm which payment methods are currently supported."},
    {"_id": "faq48", "content": "Q: What should I do if I lose my Tax Clearance Certificate? A: Contact the IRD to request a reissue or duplicate copy of your Tax Clearance Certificate."},
    {"_id": "faq49", "content": "Q: How do I deregister a business that has closed? A: Submit a formal notice of business closure to the IRD along with your final tax filings, so your account can be properly closed out."},
    {"_id": "faq50", "content": "Q: Can I authorize someone else to handle my tax matters on my behalf? A: Yes, you can typically appoint an authorized representative (such as an accountant) by submitting the appropriate authorization form to the IRD."},
    {"_id": "faq51", "content": "Q: Are charitable donations tax-deductible in Grenada? A: Deductibility rules can vary - contact the IRD or a tax professional to confirm whether a specific donation qualifies as deductible."},
    {"_id": "faq52", "content": "Q: What is the difference between GCT and Income Tax? A: Income Tax is charged on income you earn, while GCT is a consumption tax charged on goods and services you buy or sell - they are separate tax types with different rules."},
    {"_id": "faq53", "content": "Q: How do I get help filling out an IRD form? A: You can ask TESSA directly - describe the form or upload it in the Chat tab, and TESSA will walk you through it with a worked example."},
]


def main():
    logger.info("Ensuring the TESSA Pinecone index exists...")
    index = init_index()
    if index is None:
        logger.error(
            "Pinecone index isn't available - check PINECONE_API_KEY and your "
            "network connection, then re-run this script."
        )
        sys.exit(1)

    logger.info("Ingesting %d knowledge record(s)...", len(KNOWLEDGE_RECORDS))
    ok = upsert_knowledge(KNOWLEDGE_RECORDS)

    if ok:
        logger.info("Done. TESSA's Pinecone knowledge base is up to date.")
    else:
        logger.error("Ingestion failed - see the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
