"""
Ingests TESSA's tax glossary and comparison-table entries into Pinecone.

=====================================================================
IMPORTANT - READ BEFORE RUNNING THIS AGAINST PRODUCTION
=====================================================================
This is a SMALL STARTER SET, not a complete glossary. Each term entry below
is paraphrased from IRD Grenada's own public pages (ird.gd) and tagged with
its source_url for traceability, but it has NOT been reviewed by IRD staff
or checked against the current Income Tax Act / VAT Act text. In particular:

  - Any tax RATE mentioned (e.g. income tax brackets, VAT rate) should be
    re-verified against the current Act and IRD's published rates before
    this is used to answer real taxpayers - published secondary sources
    disagreed slightly on bracket structure, and rates can change.
  - Deadlines are deliberately NOT hardcoded here - TESSA is instructed to
    pull those from the FAQ/rules namespace (already sourced from your live
    site) rather than the glossary, since deadlines change and stale
    hardcoded ones are worse than none.
  - Scenario-based FAQs from the feature spec ("I started working halfway
    through the year", "I have two jobs", etc.) are NOT included here,
    because answering them correctly requires actual IRD procedural rules
    this script doesn't have access to. Add them once you have the
    authoritative answer, following the same record shape as below.

Treat this file as a template to extend under IRD review, not a finished
glossary.
=====================================================================

Run from the project root with the venv active:

    python -m scripts.ingest_tessa_glossary
"""

import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from services.pinecone_service import init_index
from services.glossary_service import (
    build_term_record,
    build_comparison_record,
    ingest_glossary,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingest")

IRD_GTAX_URL = "https://ird.gd/gtax/"
IRD_INCOME_TAX_URL = "https://ird.gd/taxes/income-tax"
IRD_VAT_URL = "https://ird.gd/index.php/taxes/value-added-tax"

TERM_RECORDS = [
    build_term_record(
        entry_id="term_tax_return",
        term="Tax Return",
        plain_definition="A tax return is the form you use to tell the IRD how much you earned and what tax you owe or have already paid, for a given period.",
        technical_definition="Under Grenada's Income Tax Act, a taxpayer declares their chargeable income for a year of assessment on the prescribed return, per IRD Grenada's published guidance.",
        example="A self-employed person files an income tax return each year reporting their business income and allowable expenses.",
        related_terms=["Filing", "Taxpayer", "Chargeable Income", "TIN"],
        common_misunderstanding="Filing a return isn't the same as paying tax - you can file a return showing nothing owed, or owe an amount you still need to pay separately.",
        synonyms=["return", "filing", "tax filing", "file my taxes", "submit my taxes"],
        category="Filing & Returns",
        source_url=IRD_INCOME_TAX_URL,
    ),
    build_term_record(
        entry_id="term_tin",
        term="Tax Identification Number (TIN)",
        plain_definition="Your TIN is a unique ID number the IRD gives you when you register, and you use it every time you deal with them.",
        technical_definition="IRD Grenada assigns a permanent, unique number to every registered individual and business, which must be quoted in all correspondence with the IRD.",
        example="You'll need your TIN on hand when applying for a Tax Clearance Certificate or filing a return.",
        related_terms=["Registration", "Taxpayer", "Tax Clearance Certificate"],
        common_misunderstanding="A TIN isn't the same as a business registration number from CAIPO - a business typically needs both.",
        synonyms=["TIN", "tax ID", "taxpayer number", "tax identification number"],
        category="Registration",
        source_url=IRD_GTAX_URL,
    ),
    build_term_record(
        entry_id="term_income_tax",
        term="Income Tax",
        plain_definition="Income tax is money you pay to the government based on what you earn during the year - your salary, business profits, or other income.",
        technical_definition="IRD Grenada describes Income Tax as levied on the chargeable income of individuals, corporations, or other legal entities under the Income Tax Act. NOTE: bracket/rate figures need re-verification against the current Act before use with real taxpayers.",
        example="An employee's monthly salary has income tax withheld through PAYE before they receive their pay.",
        related_terms=["PAYE", "Chargeable Income", "Tax Resident"],
        common_misunderstanding="Not all income is taxed the same way - employees usually have tax withheld through PAYE, while self-employed people calculate and pay it themselves.",
        synonyms=["income tax", "personal tax"],
        category="Filing & Returns",
        source_url=IRD_INCOME_TAX_URL,
    ),
    build_term_record(
        entry_id="term_paye",
        term="PAYE (Pay As You Earn)",
        plain_definition="PAYE is the system where your employer takes income tax out of your paycheck automatically and sends it to the IRD, before you're paid.",
        technical_definition="Under the PAYE system, an employer withholds tax from an employee's monthly income and remits it to IRD Grenada on the employee's behalf. NOTE: exact remittance timing should be re-verified against current IRD guidance.",
        example="If PAYE applies to you, you don't separately pay income tax on that salary - your employer already sent it in.",
        related_terms=["Income Tax", "Employer", "Withholding Tax"],
        common_misunderstanding="Having PAYE withheld doesn't always mean you're fully done with tax for the year - you may still need to file a return depending on your situation.",
        synonyms=["PAYE", "payroll tax", "pay as you earn", "withholding on salary"],
        category="Filing & Returns",
        source_url=IRD_GTAX_URL,
    ),
    build_term_record(
        entry_id="term_vat",
        term="Value Added Tax (VAT)",
        plain_definition="VAT is a tax added to the price of most goods and services you buy - registered businesses collect it and pass it on to the IRD.",
        technical_definition="A taxable person must lodge a VAT return for each tax period, per IRD Grenada's published VAT guidance. NOTE: current VAT rate should be re-verified against IRD's official published rate before use with real taxpayers.",
        example="A retail shop registered for VAT adds VAT to the sale price and later reports and pays it to the IRD.",
        related_terms=["Taxable Person", "VAT Return", "Filing Deadline"],
        common_misunderstanding="VAT is a tax on transactions, not income - it can apply even to people who don't owe any income tax.",
        synonyms=["VAT", "value added tax", "consumption tax"],
        category="Payments",
        source_url=IRD_VAT_URL,
    ),
    build_term_record(
        entry_id="term_tax_resident",
        term="Tax Resident",
        plain_definition="Whether Grenada counts you as living here for tax purposes, which affects what income you have to pay tax on.",
        technical_definition="Per IRD Grenada, an individual who spends more than 183 days a year in Grenada is a tax resident; a company can be tax resident if registered in Grenada or its head office/management is there.",
        example="Someone who lives and works in Grenada for most of the year would typically be treated as a tax resident.",
        related_terms=["Non-Resident", "Income Tax", "Withholding Tax"],
        common_misunderstanding="Being a Grenadian citizen doesn't automatically make you a tax resident, and tax residency doesn't require citizenship - it's based on days present and other residency tests.",
        synonyms=["tax resident", "tax residency", "resident for tax purposes"],
        category="Getting Started",
        source_url=IRD_GTAX_URL,
    ),
    build_term_record(
        entry_id="term_withholding_tax",
        term="Withholding Tax",
        plain_definition="Tax that's taken out and sent to the IRD before you receive a payment, rather than you paying it yourself afterward.",
        technical_definition="Per IRD Grenada, withholding tax applies when income (such as dividends) is paid by a Grenada resident to a non-resident. NOTE: rate should be re-verified against current IRD guidance, as it can vary by income type.",
        example="If a Grenada company pays dividends to an overseas shareholder, it may withhold tax before sending the payment.",
        related_terms=["Non-Resident", "Income Tax", "PAYE"],
        common_misunderstanding="Withholding tax isn't a separate 'extra' tax - it's typically a prepayment or final settlement of tax that would otherwise be owed.",
        synonyms=["withholding tax", "WHT"],
        category="Payments",
        source_url=IRD_GTAX_URL,
    ),
    build_term_record(
        entry_id="term_tax_clearance_certificate",
        term="Tax Clearance Certificate",
        plain_definition="A document from the IRD confirming you don't owe any outstanding taxes, often needed for loans, government contracts, or property transfers.",
        technical_definition="Issued by IRD Grenada to confirm a taxpayer has no outstanding tax debts, typically required for government tenders, property transfers, work permits, and certain business loans.",
        example="A contractor bidding on a government tender submits a current Tax Clearance Certificate as part of their application.",
        related_terms=["Tax Debt", "TIN", "Registration"],
        common_misunderstanding="A Tax Clearance Certificate isn't permanent - it's typically only valid for a few months and needs to be reissued.",
        synonyms=["tax clearance", "clearance certificate", "TCC"],
        category="Common Problems",
        source_url=IRD_GTAX_URL,
    ),
]

COMPARISON_RECORDS = [
    build_comparison_record(
        entry_id="cmp_individual_vs_business",
        title="Individual vs Business Taxpayer",
        left_label="Individual",
        right_label="Business",
        rows=[
            {"aspect": "Registration", "left": "Registers using the Individual Registration Form with valid ID", "right": "Registers using the Non-Individual Registration Form, usually after getting a Business Registration Certificate from CAIPO"},
            {"aspect": "Main tax type", "left": "Personal income tax on salary/earnings", "right": "Corporate income tax and/or VAT depending on activity"},
            {"aspect": "Filing", "left": "Files an individual income tax return", "right": "Files a business/corporate tax return, often with additional schedules"},
        ],
        category="Getting Started",
        source_url=IRD_GTAX_URL,
    ),
    build_comparison_record(
        entry_id="cmp_filing_vs_payment",
        title="Filing vs Payment",
        left_label="Filing",
        right_label="Payment",
        rows=[
            {"aspect": "What it is", "left": "Submitting a return that reports your income/tax situation", "right": "Actually sending money owed to the IRD"},
            {"aspect": "Can happen without the other?", "left": "Yes - you can file a return showing nothing owed", "right": "Yes - you can make a payment toward a balance separately from filing"},
            {"aspect": "Consequence if missed", "left": "Late filing penalties may apply", "right": "Late payment penalties and interest may apply separately"},
        ],
        category="Filing & Returns",
        source_url=IRD_GTAX_URL,
    ),
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

    all_records = TERM_RECORDS + COMPARISON_RECORDS
    logger.info("Ingesting %d glossary/comparison record(s)...", len(all_records))
    ok = ingest_glossary(all_records)

    if ok:
        logger.info(
            "Done. %d term(s) and %d comparison(s) loaded - remember this is a "
            "starter set that needs IRD review before relying on it for real "
            "taxpayers (see the warning at the top of this file).",
            len(TERM_RECORDS), len(COMPARISON_RECORDS),
        )
    else:
        logger.error("Ingestion failed - see the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()